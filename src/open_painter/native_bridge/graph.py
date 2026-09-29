"""Blender-to-portable-DAG adapter; native code owns ordering/invalidation.

Shader translation and GPU submission use the established host GPU adapter.
This is the host Metal execution route, not an external Metal texture import.
"""
import ctypes

from .core import NativeError

U32, U64 = ctypes.c_uint32, ctypes.c_uint64
MASK = (1 << 64) - 1
NONE = 0xffffffff
Submit = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, U32, U32, U64)


class Patch(ctypes.Structure):
    _fields_ = [('node', U32), ('reserved', U32), ('value', U64)]


def resident_stamp(state):
    # Legacy evaluation uses tuple stamps. Switching executors invalidates them.
    return state.stamp & MASK if state.textures is not None and isinstance(state.stamp, int) else 0


class NativeGraph:
    def __init__(self, lib):
        self.lib = lib
        ptr = ctypes.c_void_p
        lib.op_graph_create.argtypes = [ctypes.POINTER(ptr)]
        lib.op_graph_create.restype = ctypes.c_int
        lib.op_graph_destroy.argtypes = [ptr]
        lib.op_graph_destroy.restype = None
        lib.op_graph_replace.argtypes = [ptr, U32, ctypes.POINTER(U32), ctypes.POINTER(U32), U32]
        lib.op_graph_replace.restype = ctypes.c_int
        lib.op_graph_evaluate.argtypes = [ptr, ctypes.POINTER(U64), ctypes.POINTER(U64),
                                          ctypes.POINTER(U64), ctypes.POINTER(U32), U32,
                                          ctypes.POINTER(U32)]
        lib.op_graph_evaluate.restype = ctypes.c_int
        lib.op_graph_plan_root.argtypes = [ptr, U32, ctypes.POINTER(U64), ctypes.POINTER(U64),
                                           ctypes.POINTER(U32), U32, ctypes.POINTER(U32)]
        lib.op_graph_plan_root.restype = ctypes.c_int
        lib.op_graph_apply_patch.argtypes = [ptr, ctypes.POINTER(Patch), U32]
        lib.op_graph_apply_patch.restype = ctypes.c_int
        lib.op_graph_evaluate_current.argtypes = [ptr, ctypes.POINTER(U64), ctypes.POINTER(U64),
                                                 ctypes.POINTER(U32), U32, ctypes.POINTER(U32)]
        lib.op_graph_evaluate_current.restype = ctypes.c_int
        self.description = None
        self.own = {}
        self.dispatching = False
        self.current_key = None
        self.binding = getattr(lib, '_op_binding', None)
        if self.binding is not None:
            self.handle = self.binding.graph_create()
        else:
            self.handle = ptr()
            self.check(lib.op_graph_create(ctypes.byref(self.handle)))
        self.topology = None
        self.composition = None
        self.stamps = None
        self.plan = ()
        self.evaluations = 0
        self.rebuilds = 0

    @staticmethod
    def check(status):
        if status:
            raise NativeError(status, 'native dependency graph rejected the submission')

    def close(self):
        if self.dispatching:
            raise RuntimeError('Cannot close an executing native graph')
        if self.handle:
            if self.binding is not None:
                self.binding.graph_close(self.handle)
            else:
                self.lib.op_graph_destroy(self.handle)
            self.handle = None
        self.topology = self.stamps = self.description = self.composition = None
        self.own.clear()

    def prepare(self, engine, epochs):
        from ..evaluation.engine import glsl
        # Ordinary immutable descriptions only; no RNA or GPU address is given to C.
        cached = self.description
        valid = cached is not None and cached[0] is engine.graph and cached[1] is engine.inline
        if valid:
            keys, indices, constants = cached[2:5]
            states = {key: engine._state(key) for key in keys}
            valid = (cached[1] is engine.inline and
                     all(states[key].program is program for key, program in cached[5]))
        if not valid:
            roots = [ref[1] for ref in engine.finals.values() if ref and ref[0] == 'unit']
            while True:
                inline = engine.inline
                states, deps, constants = {}, {}, {}
                work = list(roots)
                while work:
                    key = work.pop()
                    if key in states:
                        continue
                    state = states[key] = engine._state(key)
                    imports = sorted({s.producer[0] for p in glsl.programs(state.program)
                                      for s in p.samplers if s.kind == 'import'})
                    position = engine.positions.get(key) or ()
                    links = imports + [r[1] for r in position if r and r[0] == 'unit']
                    deps[key] = tuple(links)
                    constants[key] = tuple(r if r is None or r[0] == 'const' else ('unit', r[1])
                                           for r in position)
                    work.extend(links)
                if inline is engine.inline:
                    break  # sampler-limit inlining has stabilized
            keys = tuple(sorted(states))
            indices = {key: i for i, key in enumerate(keys)}
            offsets, edges = [0], []
            for key in keys:
                edges.extend(indices[k] for k in deps[key])
                offsets.append(len(edges))
            topology = keys, tuple(offsets), tuple(edges)
            if topology != self.topology:
                a, b = (U32 * len(offsets))(*offsets), (U32 * len(edges))(*edges)
                if self.binding is not None:
                    self.binding.graph_replace(self.handle, bytes(a), bytes(b))
                else:
                    self.check(self.lib.op_graph_replace(self.handle, len(keys), a, b, len(edges)))
                self.own.clear()
                self.topology = topology
                self.composition = None   # replacing the topology clears it in C
                self.rebuilds += 1
            self._compose(engine, keys, indices, states)
            self.description = (engine.graph, engine.inline, keys, indices, constants,
                                tuple((key, states[key].program) for key in keys))
        count = len(keys)
        changes = []
        for key in keys:
            value = hash((key, engine._own_stamp(states[key], epochs), constants[key])) & MASK
            if self.own.get(key) != value:
                changes.append(Patch(indices[key], 0, value))
        if changes and self.binding is None:
            self.check(self.lib.op_graph_apply_patch(self.handle, (Patch * len(changes))(*changes), len(changes)))
            for patch in changes:
                self.own[keys[patch.node]] = patch.value
        self.last_patch_count = len(changes)
        resident = (U64 * count)(*(resident_stamp(states[key]) for key in keys))
        stamps, plan, written = (U64 * count)(), (U32 * count)(), U32()
        if self.binding is not None:
            stamp_data, plan_data = self.binding.graph_evaluate(self.handle, bytes((Patch * len(changes))(*changes)), bytes(resident))
            stamps = (U64 * count).from_buffer_copy(stamp_data)
            written.value = len(plan_data) // 4
            plan = (U32 * written.value).from_buffer_copy(plan_data)
            for patch in changes:
                self.own[keys[patch.node]] = patch.value
        else:
            self.check(self.lib.op_graph_evaluate_current(self.handle, resident, stamps, plan,
                                                        count, ctypes.byref(written)))
        self.keys, self.indices, self.values = keys, indices, stamps
        self.stamps = dict(zip(keys, stamps))
        self.plan = tuple(keys[plan[i]] for i in range(written.value))
        self.evaluations += 1
        return self.stamps


    def _compose(self, engine, keys, indices, states):
        """Layer composition for C's affine suffix runs: what each unit composes over."""
        if self.binding is None:
            return
        backdrop, grouped, affine = [], [], bytearray()
        for key in keys:
            position = engine.positions.get(key) or (None, None)
            for refs, ref in ((backdrop, position[0]), (grouped, position[1])):
                refs.append(indices[ref[1]] if ref is not None and ref[0] == 'unit' else NONE)
            affine.append(int(key[0] == 'CHANNEL' and states[key].program.affine is not None))
        composition = (tuple(backdrop), tuple(grouped), bytes(affine))
        if composition != self.composition:
            self.binding.graph_compose(self.handle, bytes((U32 * len(keys))(*backdrop)),
                                       bytes((U32 * len(keys))(*grouped)), bytes(affine))
            self.composition = composition

    def runs(self, engine, root):
        """Affine runs under ``root``, bottom-up: (top, input key or None, key, members bottom-up)."""
        if self.binding is None or self.composition is None or root not in self.indices:
            return []
        resident = (U64 * len(self.keys))(*(resident_stamp(engine.states[k]) for k in self.keys))
        keys = self.keys
        rows = self.binding.graph_runs(self.handle, self.indices[root], bytes(resident))
        return [(keys[top], None if source == NONE else keys[source], key, tuple(keys[m] for m in members))
                for top, source, key, members in reversed(rows)]

    def render(self, engine, key, size, memo, epochs):
        """C owns pass order and the lifetime of every dependency in the batch."""
        count = len(self.keys)
        resident = (U64 * count)(*(resident_stamp(engine.states[k]) for k in self.keys))

        def submit(action, index, stamp):
            target = self.keys[index]
            if action == 0:
                engine._in_use[target] += 1
            elif action == 1:
                self.current_key = target
                try:
                    engine._render(target, size, memo, epochs)
                    if resident_stamp(engine.states[target]) != stamp:
                        raise RuntimeError('Native pass did not produce its requested revision')
                finally:
                    self.current_key = None
            else:
                engine._in_use[target] -= 1
                if target != key and target[0] == 'CHANNEL':
                    engine._settle(target, size)

        self.dispatching = True
        try:
            if self.binding is not None:
                self.binding.graph_execute(self.handle, self.indices[key], bytes(resident), submit)
            else:
                errors = []
                @Submit
                def callback(_user, action, index, stamp):
                    try:
                        submit(action, index, stamp)
                        return 0
                    except BaseException as error:
                        errors.append(error)
                        return 7
                lib = self.lib
                lib.op_graph_execute.argtypes = [ctypes.c_void_p, U32, ctypes.POINTER(U64),
                                                 ctypes.POINTER(U64), Submit, ctypes.c_void_p,
                                                 ctypes.POINTER(U32)]
                lib.op_graph_execute.restype = ctypes.c_int
                rendered = U32()
                status = lib.op_graph_execute(self.handle, self.indices[key], resident, self.values,
                                              callback, None, ctypes.byref(rendered))
                if errors:
                    raise errors[0]
                self.check(status)
            return engine.states[key].textures
        finally:
            self.dispatching = False
            self.current_key = None
            # A batch may temporarily exceed the cache budget while all of its
            # inputs are live. Reclaim those holds before returning its root.
            engine._trim_retained(size, (key,))


    def checkpoint(self, engine, channel):
        order = engine.orders.get(channel, ())
        nodes = (U32 * len(order))(*(self.indices[key] for key in order))
        below = []
        for key in order:
            ref = engine.positions[key][0]
            below.append(self.indices[ref[1]] if ref is not None and ref[0] == 'unit' else 0xffffffff)
        backdrops = (U32 * len(below))(*below)
        current = (U64 * len(self.keys))(*(engine.states[key].stamp & MASK
                                          if isinstance(engine.states[key].stamp, int) else 0 for key in self.keys))
        if self.binding is not None:
            keep = self.binding.graph_checkpoint(self.handle, bytes(nodes), bytes(backdrops), bytes(current))
        else:
            self.lib.op_graph_checkpoint.argtypes = [ctypes.c_void_p, ctypes.POINTER(U32), ctypes.POINTER(U32), U32,
                                                     ctypes.POINTER(U64), ctypes.POINTER(U64), ctypes.POINTER(U32)]
            self.lib.op_graph_checkpoint.restype = ctypes.c_int
            output = U32()
            self.check(self.lib.op_graph_checkpoint(self.handle, nodes, backdrops, len(nodes), current, self.values, ctypes.byref(output)))
            keep = output.value
        return self.keys[keep] if keep != 0xffffffff else None
