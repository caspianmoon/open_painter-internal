"""Native mask planning vs independent recursive recipe traversal."""
import ctypes as C
import hashlib
import os
from pathlib import Path
import random
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.core.mask_stack import block
from open_painter.native_bridge import load_library
from open_painter.native_bridge.masks import snapshot, plan, Node, NONE, INITIAL, ONE


def stamp(*values):
    return hashlib.sha256(repr(values).encode()).hexdigest()


def reference(masks, start, stop, pre, initial, prefix):
    index, result, sources = start, initial, []
    while index < stop:
        mask = masks[index]
        end = min(block(masks, index)[1], stop)
        if mask.enabled and mask.source != 'ANCHOR' and not (pre and mask.source == 'FILTER'):
            prefix.append(mask.id)
            if mask.source == 'GROUP':
                run = []
                value, inner = reference(masks, index + 1, end, pre, None, run)
                sources.extend(inner)
                prefix.extend(run)
                value = 1.0 if value is None else value
            else:
                sources.append((mask.id, tuple(prefix)))
                value = stamp('source', mask.id, result, prefix)
            result = stamp('fold', mask.id, result, value)
        index = end
    return result, sources


def decode(p, masks, initial, prefix):
    values, sources = [], []
    def ref(i):
        return {NONE: None, INITIAL: initial, ONE: 1.0}[i] if i >= ONE else values[i]
    for index, previous, value, begin, end in p.steps:
        previous = ref(previous)
        if value == NONE:
            selected = (prefix + [masks[i].id for i in p.walk[:end]] if begin == NONE
                        else [masks[i].id for i in p.walk[begin:end]])
            sources.append((masks[index].id, tuple(selected)))
            value = stamp('source', masks[index].id, previous, selected)
        else:
            value = ref(value)
        values.append(stamp('fold', masks[index].id, previous, value))
    prefix.extend(masks[i].id for i in p.walk)
    return ref(p.result), sources


@unittest.skipUnless(os.environ.get('OP_TEST_NATIVE_LIBRARY'), 'native build not supplied')
class MaskTests(unittest.TestCase):
    def test_random_nested_ranges_prefixes_and_disabled_groups(self):
        rng = random.Random(93143)
        for compiled in (True, False):
            lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])
            if not compiled:
                lib._op_binding = None
            for trial in range(160):
                masks, parents = [], ['']
                for i in range(rng.randrange(1, 70)):
                    if len(parents) > 1 and rng.random() < .35:
                        parents.pop()
                    source = rng.choice(('GROUP', 'FILL', 'FILTER', 'ANCHOR', 'PROCEDURAL'))
                    masks.append(SimpleNamespace(id=str(i), parent_id=parents[-1],
                                                 source=source, enabled=rng.random() > .25))
                    if source == 'GROUP': parents.append(str(i))
                data = snapshot(masks)
                start = rng.randrange(len(masks) + 1)
                stop = rng.randrange(start, len(masks) + 1)
                for pre in (False, True):
                    for initial in (None, 'continuation'):
                        p = plan(lib, data, start, stop, pre, initial is not None)
                        a, b = ['earlier'], ['earlier']
                        self.assertEqual(decode(p, masks, initial, a), reference(masks, start, stop, pre, initial, b),
                                         (compiled, trial, start, stop, pre, initial))
                        self.assertEqual(a, b)

    def test_owned_output_and_empty_continuation(self):
        lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])
        data = (Node * 1)(Node(NONE, 1))
        p = plan(lib, data, 0, 1)
        data[0].flags = 0
        self.assertEqual(p.walk, (0,))
        self.assertEqual(p.result, 0)
        self.assertEqual(plan(lib, data, 0, 1, initial=True).result, INITIAL)
        self.assertEqual(plan(lib, (Node * 0)(), 0, 0).result, NONE)

    def test_snapshot_and_binding_reject_invalid_data(self):
        lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])
        b = getattr(lib, '_op_binding', None)
        if b is None:
            self.skipTest('compiled binding not supplied')
        for data in (b'x', b'x' * 7, b'x' * (65537 * 8)):
            with self.assertRaises(ValueError): b.mask_plan(data, 0, 0, 0, 0)
        for fields in ((0, 2, 0, 0), (1, 0, 0, 0), (0, 1, 2, 0), (0, 1, 0, 2), (-1, 1, 0, 0)):
            with self.assertRaises(RuntimeError): b.mask_plan(bytes(Node(NONE, 1)), *fields)
        for data in ((Node * 1)(Node(0, 3)), (Node * 2)(Node(NONE, 1), Node(0, 1)),
                     (Node * 1)(Node(NONE, 7))):
            with self.assertRaises(RuntimeError): plan(lib, data, 0, len(data))
        a = SimpleNamespace(id='a', parent_id='', source='FILL', enabled=True)
        with self.assertRaises(ValueError): snapshot([a, a])
        anonymous = SimpleNamespace(id='', parent_id='', source='FILL', enabled=True)
        self.assertEqual(plan(lib, snapshot([anonymous, anonymous]), 0, 2).walk, (0, 1))
        a.parent_id = 'missing'
        with self.assertRaises(ValueError): snapshot([a])


if __name__ == '__main__': unittest.main()
