"""NativeGraph descriptions under structural edits: the incremental one (stable indices, only
what an edit reached described again) against one described from scratch, compared by unit key.

Needs NumPy (Blender's bundled Python) and OP_TEST_NATIVE_LIBRARY."""
import ctypes
import os
from pathlib import Path
import random
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
try:
    from open_painter.constants import CHANNELS
    from open_painter.evaluation.engine import glsl, runtime
    from open_painter.native_bridge import graph as native_graph, load_library
except ImportError as error:   # no NumPy: runtime needs it
    native_graph = None
    missing = str(error)

CHOSEN = CHANNELS[:3]


class Stack:
    """Layers in stack order within each parent: (id, parent, folder, channels, masks, affine, passthrough)."""

    def __init__(self, rng):
        self.rng, self.layers, self.next = rng, [], 0
        for _ in range(12):
            self.add()

    def add(self):
        folders = [layer['id'] for layer in self.layers if layer['folder']]
        self.next += 1
        layer = {'id': f'L{self.next}', 'parent': self.rng.choice([''] + folders) if folders else '',
                 'folder': self.rng.random() < .2, 'masks': self.rng.randint(0, 2),
                 'channels': {c for c in CHOSEN if self.rng.random() < .7} or {CHOSEN[0]},
                 'affine': self.rng.random() < .6, 'passthrough': self.rng.random() < .5}
        self.layers.insert(self.rng.randint(0, len(self.layers)), layer)

    def remove(self):
        if len(self.layers) < 3:
            return
        doomed = {self.rng.choice(self.layers)['id']}
        grown = True
        while grown:
            grown = False
            for layer in self.layers:
                if layer['parent'] in doomed and layer['id'] not in doomed:
                    doomed.add(layer['id'])
                    grown = True
        self.layers = [layer for layer in self.layers if layer['id'] not in doomed]

    def move(self):
        if not self.layers:
            return self.add()
        layer = self.layers.pop(self.rng.randrange(len(self.layers)))
        self.layers.insert(self.rng.randint(0, len(self.layers)), layer)

    def edit(self):
        if not self.layers:
            return self.add()
        layer = self.rng.choice(self.layers)
        field = self.rng.choice(('channels', 'masks', 'affine', 'passthrough'))
        if field == 'channels':
            layer['channels'] ^= {self.rng.choice(CHOSEN)}
        elif field == 'masks':
            layer['masks'] = self.rng.randint(0, 2)
        else:
            layer[field] = not layer[field]

    def chains(self):
        def chain(parent, channel):
            steps = []
            for layer in self.layers:
                if layer['parent'] != parent or channel not in layer['channels']:
                    continue
                step = {'key': ('CHANNEL', layer['id'], channel)}
                if layer['folder']:
                    step['children'] = chain(layer['id'], channel)
                    step['passthrough'] = layer['passthrough']
                steps.append(step)
            return steps
        return {channel: chain('', channel) for channel in CHOSEN}

    def units(self):
        """key -> definition (what it imports, whether it is affine): a changed definition is a new program."""
        found = {}
        for layer in self.layers:
            masks = tuple(('MASK', layer['id'], m) for m in range(layer['masks']))
            for mask in masks:
                found[mask] = ((), False)
            for channel in layer['channels']:
                imports = masks
                if not layer['folder']:
                    source = ('SRC', layer['id'], channel, 0)
                    found[source] = ((), False)
                    imports = (source, *masks)
                found[('CHANNEL', layer['id'], channel)] = (imports, layer['affine'])
        return found


PROGRAMS = {}   # (definition, inlined producers) -> Program: engines translating alike share programs


class Engine:
    """What NativeGraph.prepare reads of runtime.Engine. As there, a producer read by one unit
    only is compiled into it (``inline``), and a consumer's program follows that lazily
    (``_state``) without the consumer being replaced."""

    def __init__(self):
        self.graph = SimpleNamespace(consumers={}, take_touched=self.take_touched)
        self.states, self.definitions, self.versions = {}, {}, {}
        self.replaced, self.touched = None, set()
        self.precise, self.inline = False, frozenset()
        self.positions = self.finals = self.orders = None

    def adopt(self, stack):
        units = stack.units()
        replaced = set()
        for key, definition in units.items():
            if self.definitions.get(key) != definition:
                old = self.states.get(key)
                if old is not None:
                    old.slot = None
                self.states[key], self.definitions[key] = runtime.UnitState(key), definition
                replaced.add(key)
        for key in [key for key in self.states if key not in units]:
            self.states.pop(key).slot = None
            del self.definitions[key]
            replaced.add(key)
        self.replaced = replaced if self.replaced is None else self.replaced | replaced
        consumers = {}
        for key, (imports, _) in self.definitions.items():
            for producer in imports:
                consumers.setdefault(producer, set()).add(key)
        self.graph.consumers = consumers
        inline = frozenset(key for key, found in consumers.items() if key[0] in {'SRC', 'MASK'} and len(found) == 1)
        if inline != self.inline:
            self.inline = inline
        self.positions, self.finals, self.orders = runtime._positions(SimpleNamespace(chains=stack.chains()))

    def value_edit(self, rng):
        for key in rng.sample(sorted(self.states), min(3, len(self.states))):
            self.versions[key] = self.versions.get(key, 0) + 1
            self.touched.add(key)

    def take_replaced(self):
        replaced, self.replaced = self.replaced, None
        return replaced

    def take_touched(self):
        touched, self.touched = self.touched, set()
        return touched

    def _state(self, key):
        state = self.states[key]
        definition = self.definitions[key]
        signature = (definition, self._inlined(key))
        if state.signature != signature:
            program = PROGRAMS.get(signature)
            if program is None:
                imports, affine = definition
                program = PROGRAMS[signature] = glsl.Program(
                    source='', samplers=[glsl.Sampler(f's{i}', 'import', producer=(k, 0))
                                         for i, k in enumerate(imports) if k not in self.inline],
                    affine=(0, 1, 2, 3) if affine else None)
            state.program, state.signature = program, signature
        return state

    def _own_stamp(self, state, epochs):
        return hash((id(state.program), self.versions.get(state.unit, 0)))

    def _surface(self):
        return None

    def _inlined(self, key):
        return tuple(k for k in self.definitions[key][0] if k in self.inline)

    def copy(self):
        """The same units and definitions in states of its own (slots are per graph)."""
        other = Engine()
        other.states = {key: runtime.UnitState(key) for key in self.states}
        other.definitions, other.versions = dict(self.definitions), dict(self.versions)
        other.graph.consumers, other.inline = self.graph.consumers, self.inline
        other.positions, other.finals, other.orders = self.positions, self.finals, self.orders
        return other


def by_key(graph):
    """What C holds, by unit key: rows, composition and the evaluated stamps."""
    keys = graph.keys
    def key(index):
        return None if index == native_graph.NONE else keys[index]
    rows = {k: tuple(keys[i] for i in graph.rows[index]) for k, index in graph.indices.items()}
    composition = ({k: (key(graph.backdrop[i]), key(graph.grouped[i]), graph.affine[i])
                    for k, i in graph.indices.items()} if graph.binding is not None else None)
    return rows, composition, dict(graph.stamps), set(graph.plan)


@unittest.skipUnless(os.environ.get('OP_TEST_NATIVE_LIBRARY'), 'native build not supplied')
@unittest.skipIf(native_graph is None, 'NumPy needed (Blender\'s bundled Python)')
class NativeGraphDescriptionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])
        cls.verify = native_graph.VERIFY
        native_graph.VERIFY = True   # every incremental description is checked against a complete one

    @classmethod
    def tearDownClass(cls):
        native_graph.VERIFY = cls.verify

    def run_edits(self, lib, seed, steps=150):
        rng = random.Random(seed)
        stack, engine = Stack(rng), Engine()
        engine.adopt(stack)
        graph = native_graph.NativeGraph(lib)
        try:
            graph.prepare(engine, {})
            paths = set()
            for step in range(steps):
                for _ in range(rng.randint(1, 2)):
                    rng.choice((stack.add, stack.remove, stack.move, stack.edit, stack.edit))()
                engine.adopt(stack)
                if rng.random() < .3:
                    engine.value_edit(rng)
                graph.prepare(engine, {})
                paths.add(graph.path)
                fresh = native_graph.NativeGraph(lib)
                try:
                    fresh.prepare(engine.copy(), {})
                    self.assertEqual(by_key(graph), by_key(fresh), (seed, step, graph.path))
                finally:
                    fresh.close()
                self.assertEqual(sorted(graph.free), [i for i, key in enumerate(graph.keys) if key is None])
            return paths, graph
        finally:
            graph.close()

    def test_binding_edits(self):
        if getattr(self.lib, '_op_binding', None) is None:
            self.skipTest('compiled binding not supplied')
        for seed in range(4):
            paths, graph = self.run_edits(self.lib, seed)
            self.assertIn('retopology', paths)
            self.assertEqual(graph.described, 1, paths)   # only the first description was complete

    def test_ctypes_edits(self):
        lib = ctypes.CDLL(self.lib._name)   # no compiled binding: the ctypes route
        paths, graph = self.run_edits(lib, 11, steps=60)
        self.assertIn('retopology', paths)


if __name__ == '__main__':
    unittest.main()
