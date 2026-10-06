"""Blender-to-portable-DAG adapter; native code owns ordering/invalidation.

Shader translation and GPU submission use the established host GPU adapter.
This is the host Metal execution route, not an external Metal texture import.
"""
from array import array
import ctypes
from itertools import accumulate, chain
import os
import sys

from .core import NativeError

U32, U64 = ctypes.c_uint32, ctypes.c_uint64
MASK = (1 << 64) - 1
NONE = 0xffffffff
# Restamp only units whose inputs can have changed since the previous evaluation.
INCREMENTAL = True
# Diagnostics: compare every incremental evaluation with a complete restamp.
VERIFY = os.environ.get('OP_VERIFY_INCREMENTAL') == '1'
_MISSING = object()
HEADROOM = 16   # free slots kept past the described units: most adds take one without a resize
Submit = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, U32, U32, U64)


class Patch(ctypes.Structure):
    _fields_ = [('node', U32), ('reserved', U32), ('value', U64)]


def _patch_bytes(changes):
    """(index, own stamp) pairs as C's patch records (node, reserved, value)."""
    if sys.byteorder == 'little':
        return array('Q', chain.from_iterable(changes)).tobytes()   # node | reserved << 32, value
    return bytes((Patch * len(changes))(*(Patch(index, 0, value) for index, value in changes)))


def resident_stamp(state):
    # Legacy evaluation uses tuple stamps. Switching executors invalidates them.
    return state.stamp & MASK if state.textures is not None and isinstance(state.stamp, int) else 0


class Slot:
    """A unit state's entry in the graph's native-order stamp arrays, kept current
    by the state itself: no per-call Python walk over every unit. A slot writes
    only the arrays it was created with, so a state left behind by a topology
    change can never write into its successor's arrays."""
    __slots__ = ('current', 'resident', 'index')

    def __init__(self, current, resident, index):
        self.current, self.resident, self.index = current, resident, index

    def sync(self, state):
        stamp = state._stamp
        value = stamp & MASK if isinstance(stamp, int) else 0
        self.current[self.index] = value
        self.resident[self.index] = value if state._textures is not None else 0


def _key_facts(engine, key, program):
    """(program, inlined producers, reads object matrices or filters, reads the surface, image
    nodes it samples): what a unit's program tells the index, kept while the program is."""
    from ..evaluation.engine import glsl
    from ..evaluation.engine.runtime import GEOMETRY
    volatile = surface = False
    images = {}
    for part in glsl.programs(program):
        if part.objects:
            volatile = True
        for sampler in part.samplers:
            if sampler.kind == 'image':
                images.setdefault(id(sampler.node), sampler.node)
            elif sampler.kind == 'filter':
                volatile = True
                if getattr(sampler.node.effect, 'space', 'UV') == 'SURFACE':
                    surface = True
            elif sampler.kind in GEOMETRY:
                surface = True
    return program, tuple(engine._inlined(key)), volatile, surface, tuple(images.values())


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
        # Plain descriptions, no RNA: the engine an undo carries keeps it (``lifecycle.close``).
        self.survives_undo = True
        self.current_key = None
        self.binding = getattr(lib, '_op_binding', None)
        if self.binding is not None:
            self.handle = self.binding.graph_create()
        else:
            self.handle = ptr()
            self.check(lib.op_graph_create(ctypes.byref(self.handle)))
        self.topology = None                  # (offsets, edges) as C last took them
        self.composition = None
        self.stamps = None
        self.values = None
        self.plan = ()
        self.keys = []                        # index -> unit key; None: a free slot
        self.indices = {}                     # unit key -> index, kept while a channel reaches the unit
        self.free = []                        # free indices, the next one last
        self.rows = []                        # index -> the indices it reads (C's topology)
        self.refs = {}                        # key -> described units reading it, one more for a channel result
        self.constants = {}                   # key -> what it composes over (its position)
        self.backdrop, self.grouped = array('I'), array('I')   # index -> composition (NONE: nothing)
        self.affine = bytearray()
        self._reassigned = set()              # slots given to or taken from a unit since C took the topology
        self._positions = None                # engine.positions the composition and orders were taken from
        self.current = self.resident = None   # native-order mirrors of unit stamps (Slot)
        self.programs = {}                    # key -> the program its own stamp was taken from
        self.image_nodes = []                 # [node, unit keys, last identity]
        self.image_groups = None              # [image, last identity, entries] per distinct image, or None
        self.resource_entries = []            # entries whose node samples a GPU resource
        self.assigned = None                  # headless.assignments when the groups were taken
        self.volatile = frozenset()           # units whose object matrices or filters are read every time
        self.surface_units = frozenset()
        self.surface = None
        self.inline_owner = {}                # inlined producer -> the graph node compiling it
        self.deps = {}                        # key -> the keys it reads (imports, then composition)
        self.key_facts = {}                   # key -> (program, inlined, volatile, surface, image nodes)
        self.imports = {}                     # key -> the units its programs import
        self.roots = ()                       # the units the channels' results come from
        self.orders = {}                      # channel -> (checkpoint order, backdrops) as native bytes
        self.evaluations = 0
        self.rebuilds = 0
        self.described = 0      # evaluations that described every unit
        self.reprogrammed = 0   # evaluations that described only what a patch changed
        self.retopologized = 0  # ... of which changed what units read or which units are described

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
        self.topology = self.stamps = self.description = self.composition = self.values = None
        self.own.clear()
        self.keys, self.indices, self.free, self.rows, self.refs = [], {}, [], [], {}
        self.deps, self.constants, self.imports, self.programs = {}, {}, {}, {}
        self.backdrop, self.grouped, self.affine = array('I'), array('I'), bytearray()
        self.current = self.resident = self._positions = None
        self._reassigned = set()

    def prepare(self, engine, epochs):
        from ..evaluation.engine import glsl
        from .lifecycle import active_library
        # Ordinary immutable descriptions only; no RNA or GPU address is given to C.
        cached = self.description
        graph = engine.graph
        take = getattr(graph, 'take_touched', None)
        touched = take() if take is not None else None
        take = getattr(engine, 'take_replaced', None)
        patched = take() if take is not None else None
        context = (engine.precise, bool(active_library()))
        valid = cached is not None and cached[0] is graph and cached[2] == context
        dirty = states = reprogrammed = None
        self.path = 'valid'
        resend = None     # the own stamps C held before a new topology cleared them
        earlier = set()   # units an earlier step of this evaluation marked dirty
        if valid and patched is not None:
            # A patch replaced these units in the same graph, moved what units compose over or
            # which producers are compiled into their consumer: only they, and what they read
            # anew, are described again.
            found = self._redescribe(engine, epochs, patched) if INCREMENTAL else None
            valid = found is not None
            if valid:
                reprogrammed, resend = found
                self.path = 'reprogram' if resend is None else 'retopology'
            else:
                self.path = 'full'
        elif valid and cached[1] is not engine.inline:
            valid = False
        if valid:
            dirty = self._dirty(engine, epochs, touched) if INCREMENTAL else None
            if dirty is None:
                dirty = set(self.indices)
            elif reprogrammed:
                dirty |= reprogrammed
            earlier = set(dirty)
            states = {key: engine._state(key) for key in dirty}
            # A program recompiled on a signature change may read other inputs.
            valid = (self.description[1] is engine.inline
                     and all(states[key].program is self.programs[key] for key in dirty))
        if not valid:
            # A rebuilt graph that kept the topology restamps only what can have changed.
            self.described += 1
            self.path = 'full' if self.path == 'valid' else self.path + '>full'
            previous_programs, previous, previous_surface = self.programs, cached, self.surface
            constants_before = self.constants
            identities = {id(entry[0]): entry[2] for entry in self.image_nodes}
            roots = [ref[1] for ref in engine.finals.values() if ref and ref[0] == 'unit']
            # After a patch of the same graph, a unit it did not replace keeps its program and
            # what it imports, unless a producer compiled into it changed or moved in or out.
            reusable = (patched is not None and cached is not None and cached[0] is graph
                        and cached[2] == context and self.imports)
            # A unit whose images changed identity is translated again (alpha and layout are in
            # its code); the scan below still sees the change against ``identities``.
            early = self._scan_images(epochs) if reusable else set()
            while True:
                inline = engine.inline
                states, deps, constants, imports_of = {}, {}, {}, {}
                fresh = set()
                if reusable:
                    fresh = set(patched) | early
                    fresh.update(self.inline_owner[key] for key in patched if key in self.inline_owner)
                    for key in inline ^ cached[1]:
                        fresh.update(graph.consumers.get(key, ()))
                        fresh.add(self.inline_owner.get(key, key))
                work = list(roots)
                while work:
                    key = work.pop()
                    if key in states:
                        continue
                    state = engine.states.get(key)
                    if (reusable and key not in fresh and key in self.imports and state is not None
                            and state.program is previous_programs.get(key)):
                        states[key], imports = state, self.imports[key]
                    else:
                        state = states[key] = engine._state(key)
                        imports = tuple(sorted({s.producer[0] for p in glsl.programs(state.program)
                                                for s in p.samplers if s.kind == 'import'}))
                    imports_of[key] = imports
                    position = engine.positions.get(key) or ()
                    links = imports + tuple(r[1] for r in position if r and r[0] == 'unit')
                    deps[key] = links
                    constants[key] = tuple(r if r is None or r[0] == 'const' else ('unit', r[1])
                                           for r in position)
                    work.extend(links)
                if inline is engine.inline:
                    break  # sampler-limit inlining has stabilized
            if VERIFY and reusable:
                self._verify_description(engine, states, imports_of)
            self.imports, self.roots = imports_of, tuple(roots)
            kept_own = self.own
            self._assign(engine, deps, constants, {key: state.program for key, state in states.items()})
            replaced = self._send_topology()
            if replaced:
                self.own = {}   # a new topology clears C's own stamps; unchanged units resend theirs
            self._compose(engine, self.indices, replaced)
            self.description = (graph, engine.inline, context)
            scanned = self._index(engine, states, epochs, identities)
            dirty = set(self.indices)
            if INCREMENTAL and previous is not None and kept_own and touched is not None:
                changed = {key for key in self.indices if states[key].program is not previous_programs.get(key)
                           or constants_before.get(key) != constants[key]}
                changed |= scanned | self.volatile
                if self.surface_units and self.surface != previous_surface:
                    changed |= self.surface_units
                for unit in touched:
                    owner = self.inline_owner.get(unit, unit)
                    if owner in self.indices:
                        changed.add(owner)
                # What an earlier step of this evaluation found (its scan recorded the new image
                # identities, so this description's scan no longer sees them change).
                changed |= earlier
                dirty = changed & self.indices.keys()
                if replaced:
                    resend = kept_own
        indices, keys = self.indices, self.keys
        changes = []   # (index, own stamp)
        if resend is not None:
            # C lost every own stamp with the old topology: an unchanged unit's is the one it had
            # (a unit without one is restamped).
            for key, index in indices.items():
                if key not in dirty:
                    value = resend.get(key)
                    if value is None:
                        dirty.add(key)
                    else:
                        changes.append((index, value))
        for key in dirty:
            state = states.get(key)
            if state is None:
                state = states[key] = engine._state(key)
            value = hash((key, engine._own_stamp(state, epochs), self.constants[key])) & MASK
            if self.own.get(key) != value:
                changes.append((indices[key], value))
        if VERIFY and len(dirty) < len(indices):
            self._verify(engine, epochs, changes)
        count = len(keys)
        if self.binding is not None:
            stamp_data, plan_data = self.binding.graph_evaluate(self.handle, _patch_bytes(changes),
                                                                bytes(self.resident))
            stamps = (U64 * count).from_buffer_copy(stamp_data)
            plan, written = memoryview(plan_data).cast('I'), len(plan_data) // 4
        else:
            if changes:
                self.check(self.lib.op_graph_apply_patch(
                    self.handle, (Patch * len(changes))(*(Patch(index, 0, value) for index, value in changes)),
                    len(changes)))
            stamps, plan, found = (U64 * count)(), (U32 * count)(), U32()
            self.check(self.lib.op_graph_evaluate_current(self.handle, self.resident, stamps, plan,
                                                        count, ctypes.byref(found)))
            written = found.value
        for index, value in changes:
            self.own[keys[index]] = value
        self.last_patch_count = len(changes)
        self.last_dirty_count = len(dirty)
        # The units whose own content changed: with the composition, what fusion plans from.
        self.last_changed = frozenset(keys[index] for index, _ in changes)
        self.values = stamps
        found = dict(zip(keys, stamps))
        found.pop(None, None)   # free slots
        self.stamps = found
        self.plan = tuple(key for key in (keys[plan[i]] for i in range(written)) if key is not None)
        self.evaluations += 1
        return self.stamps

    # ---- stable indices ------------------------------------------------------------------
    # A unit keeps its index while any channel reaches it; a unit added takes a free slot. A
    # layer added, removed or moved then changes the rows of the units it touched, not the
    # index of every unit after it in key order.

    def _allocate(self, key):
        index = self.free.pop()
        self.keys[index] = key
        self.indices[key] = index
        self._reassigned.add(index)
        return index

    def _release(self, engine, key):
        index = self.indices.pop(key)
        self.keys[index] = None
        self.rows[index] = ()
        self.backdrop[index] = self.grouped[index] = NONE
        self.affine[index] = 0
        if self.current is not None:
            self.current[index] = self.resident[index] = 0
        self.free.append(index)
        self._reassigned.add(index)
        self.own.pop(key, None)
        state = engine.states.get(key)
        if state is not None and state.slot is not None and state.slot.current is self.current:
            state.slot = None   # the slot may go to another unit

    def _reserve(self, engine, needed):
        """At least ``needed`` free slots; the arrays grow with headroom, so that most adds take
        a free slot without resizing them."""
        if len(self.free) >= needed:
            return
        count = len(self.keys)
        extra = needed - len(self.free) + max(HEADROOM, count // 8)
        size = count + extra
        self.keys.extend([None] * extra)
        self.rows.extend([()] * extra)
        self.backdrop.extend([NONE] * extra)
        self.grouped.extend([NONE] * extra)
        self.affine.extend(bytes(extra))
        self.free[:0] = range(size - 1, count - 1, -1)   # taken lowest first, after released ones
        current, resident = (U64 * size)(), (U64 * size)()
        if self.current is not None:
            ctypes.memmove(current, self.current, ctypes.sizeof(self.current))
            ctypes.memmove(resident, self.resident, ctypes.sizeof(self.resident))
            for key, index in self.indices.items():
                state = engine.states.get(key)
                if state is not None and state.slot is not None and state.slot.current is self.current:
                    state.slot = Slot(current, resident, index)
        self.current, self.resident = current, resident

    def _assign(self, engine, deps, constants, programs):
        """Index every unit of a complete description: one described before keeps its index."""
        indices = self.indices
        for key in [key for key in indices if key not in deps]:
            self._release(engine, key)
        new = sorted(key for key in deps if key not in indices)
        self._reserve(engine, len(new))
        for key in new:
            self._allocate(key)
        rows = self.rows
        for key, links in deps.items():
            rows[indices[key]] = tuple(indices[k] for k in links)
        refs = dict.fromkeys(deps, 0)
        for links in deps.values():
            for key in links:
                refs[key] += 1
        for key in set(self.roots):
            refs[key] += 1
        self.refs, self.deps, self.constants, self.programs = refs, deps, constants, programs

    def _send_topology(self):
        """Hand C the rows when they or the units at their indices changed. Returns whether C
        replaced its topology (which clears its own stamps and keeps the composition it knew
        for ``_compose``)."""
        rows = self.rows
        topology = (array('I', accumulate(map(len, rows), initial=0)).tobytes(),
                    array('I', chain.from_iterable(rows)).tobytes())
        if topology == self.topology and not self._reassigned:
            return False
        offsets, edges = topology
        if self.binding is not None:
            self.binding.graph_replace(self.handle, offsets, edges)
        else:
            count, edge_count = len(offsets) // 4 - 1, len(edges) // 4
            self.check(self.lib.op_graph_replace(self.handle, count, (U32 * (count + 1)).from_buffer_copy(offsets),
                                                 (U32 * edge_count).from_buffer_copy(edges), edge_count))
        self.topology = topology
        self.composition = None   # replacing the topology clears it in C
        self.rebuilds += 1
        return True

    def _redescribe(self, engine, epochs, replaced):
        """Describe again only what a patch changed: the units it replaced (and those their
        inlined producers compile into), the units whose composition moved and the units they
        read for the first time; a unit no channel reaches any more is released. Every other unit
        keeps its index, program and row. Returns (units to restamp, the own stamps C held when
        a new topology cleared them, else None); None, having changed nothing, when every unit
        must be described (the sampler limit moved a producer in or out of its consumer)."""
        from ..evaluation.engine import glsl
        inline = engine.inline
        positions = engine.positions
        roots = tuple(ref[1] for ref in engine.finals.values() if ref and ref[0] == 'unit')
        indices, old_deps = self.indices, self.deps
        candidates = {key for key in replaced if key in indices}
        candidates.update(self.inline_owner[key] for key in replaced if key in self.inline_owner)
        # A producer compiled into its consumer now, or no longer: the consumer's program
        # changes (the consumers the patch left are the ones to look at).
        consumers = engine.graph.consumers
        for key in inline ^ self.description[1]:
            candidates.update(consumer for consumer in consumers.get(key, ()) if consumer in indices)
            owner = self.inline_owner.get(key)
            if owner in indices:
                candidates.add(owner)
        if positions is not self._positions:
            constants = self.constants
            candidates.update(key for key in indices if constants[key] != (positions.get(key) or ()))
        described = {}   # key -> (state, imports, links, constants); None: gone from the graph

        def describe(key):
            state = engine._state(key)
            imports = tuple(sorted({s.producer[0] for p in glsl.programs(state.program)
                                    for s in p.samplers if s.kind == 'import'}))
            position = positions.get(key) or ()
            links = imports + tuple(r[1] for r in position if r and r[0] == 'unit')
            described[key] = (state, imports, links,
                              tuple(r if r is None or r[0] == 'const' else ('unit', r[1]) for r in position))
            return links

        increments, decrements, rows_changed = [], [], False
        for key in candidates:
            if key in engine.states:
                links = describe(key)
            else:
                described[key], links = None, ()   # what read it was replaced too
            if links != old_deps[key]:
                increments.extend(links)
                decrements.extend(old_deps[key])
                rows_changed = True
        now, before = set(roots), set(self.roots)
        increments.extend(now - before)
        decrements.extend(before - now)
        # Reads counted before they are dropped: a unit read anew is described, and what it reads
        # counted, before a dropped read could release something it reads too.
        refs, added = dict(self.refs), []
        while increments:
            key = increments.pop()
            count = refs[key] = refs.get(key, 0) + 1
            if count == 1 and key not in indices:
                if key not in engine.states:
                    return None   # read, yet not in the graph: described from scratch (which raises)
                added.append(key)
                increments.extend(describe(key))
        if engine.inline is not inline:
            return None
        released = []
        while decrements:
            key = decrements.pop()
            count = refs.get(key, 0) - 1
            if count < 0:
                raise AssertionError(f'Native description released {key} more often than it was read')
            if count:
                refs[key] = count
                continue
            del refs[key]
            released.append(key)
            entry = described.get(key, _MISSING)
            decrements.extend(old_deps.get(key, ()) if entry is _MISSING else entry[2] if entry else ())
        if any(entry is None and key in refs for key, entry in described.items()):
            return None   # a unit gone from the graph is still read
        gone = [key for key in released if key in indices]
        new = sorted(set(added) - set(released))
        for key in gone:
            self._release(engine, key)
        self._reserve(engine, len(new))
        for key in new:
            self._allocate(key)
        states = {}
        for key, entry in described.items():
            if entry is None or key not in refs:
                continue
            state, imports, links, constants = entry
            self.rows[indices[key]] = tuple(indices[k] for k in links)
            self.deps[key], self.imports[key], self.constants[key] = links, imports, constants
            self.programs[key] = state.program
            states[key] = state
        for key in gone:
            for table in (self.deps, self.imports, self.constants, self.programs):
                table.pop(key, None)
        self.refs, self.roots = refs, roots
        self.description = (self.description[0], inline, self.description[2])
        topology = bool(gone or new or rows_changed)
        replaced_topology = self._send_topology() if topology else False
        resend = None
        if replaced_topology:
            resend, self.own = self.own, {}
        self._compose(engine, states, replaced_topology)
        if positions is not self._positions or topology:
            self._orders(engine)
        restamp = self._reindex(engine, epochs, states, gone)
        if VERIFY:
            self._verify_topology(engine)
        self.reprogrammed += 1
        if topology:
            self.retopologized += 1
        return restamp, resend

    def _verify_topology(self, engine):
        """OP_VERIFY_INCREMENTAL: an incremental description must be the complete one."""
        from ..evaluation.engine import glsl
        roots = [ref[1] for ref in engine.finals.values() if ref and ref[0] == 'unit']
        deps, constants, programs, work = {}, {}, {}, list(roots)
        while work:
            key = work.pop()
            if key in deps:
                continue
            state = engine._state(key)
            imports = tuple(sorted({s.producer[0] for p in glsl.programs(state.program)
                                    for s in p.samplers if s.kind == 'import'}))
            position = engine.positions.get(key) or ()
            deps[key] = imports + tuple(r[1] for r in position if r and r[0] == 'unit')
            constants[key] = tuple(r if r is None or r[0] == 'const' else ('unit', r[1]) for r in position)
            programs[key] = state.program
            work.extend(deps[key])
        indices = self.indices
        problems = sorted(str(key) for key in deps.keys() ^ indices.keys())[:4]
        problems += [f'{key}: {name}' for key in deps if key in indices
                     for name, same in (('reads', deps[key] == self.deps.get(key)),
                                        ('composes', constants[key] == self.constants.get(key)),
                                        ('program', programs[key] is self.programs.get(key)),
                                        ('row', self.rows[indices[key]] == tuple(indices.get(k) for k in deps[key])),
                                        ('index', self.keys[indices[key]] == key),
                                        ('composition', self.binding is None or (self.backdrop[indices[key]],
                                                         self.grouped[indices[key]]) == tuple(
                                            indices[ref[1]] if ref is not None and ref[0] == 'unit' else NONE
                                            for ref in (engine.positions.get(key) or (None, None)))))
                     if not same][:4]
        refs = dict.fromkeys(deps, 0)
        for links in deps.values():
            for key in links:
                refs[key] += 1
        for key in set(roots):
            refs[key] += 1
        if refs != self.refs:
            problems.append('reference counts')
        if sorted(self.free) != [i for i, key in enumerate(self.keys) if key is None]:
            problems.append('free slots')
        if problems:
            raise AssertionError(f'Incremental native description differs ({self.path}): {problems}')

    def _index(self, engine, states, epochs, identities=None):
        """What can change a unit's own stamp without a new description: its uniforms
        (reported by update()), the images it samples, object matrices, filters and
        the surface. Also mirrors every unit's stamp into native-order arrays."""
        nodes, volatile, surface = {}, set(), set()
        self.inline_owner = {}
        before, facts_of = self.key_facts, {}
        for key in self.indices:
            program = self.programs[key]
            facts = before.get(key)
            if facts is None or facts[0] is not program:
                facts = _key_facts(engine, key, program)
            facts_of[key] = facts
            for producer in facts[1]:
                self.inline_owner[producer] = key
            if facts[2]:
                volatile.add(key)
            if facts[3]:
                surface.add(key)
            for node in facts[4]:
                nodes.setdefault(id(node), (node, set()))[1].add(key)
        self.key_facts = facts_of
        # A node the previous index held keeps its last identity: the scan then reports only
        # the units whose images really changed (a rebuild adopts most nodes unchanged).
        identities = identities or {}
        self.image_nodes = [[node, tuple(units), identities.get(id(node), _MISSING)] for node, units in nodes.values()]
        self.image_groups = None
        scanned = self._scan_images(epochs)
        self.volatile, self.surface_units = frozenset(volatile), frozenset(surface)
        self.surface = engine._surface() if surface else None
        count = len(self.keys)
        self.current, self.resident = (U64 * count)(), (U64 * count)()
        live = self.indices
        for key, state in engine.states.items():
            if key not in live:
                state.slot = None
        for key, index in live.items():
            state = states[key]
            state.slot = Slot(self.current, self.resident, index)
            state.slot.sync(state)
        self._orders(engine)
        return scanned

    def _orders(self, engine):
        indices, positions = self.indices, engine.positions
        self.orders = {}
        for channel, order in engine.orders.items():
            below = []
            for key in order:
                ref = positions[key][0]
                below.append(indices[ref[1]] if ref is not None and ref[0] == 'unit' else NONE)
            self.orders[channel] = (array('I', [indices[key] for key in order]).tobytes(), array('I', below).tobytes())
        self._positions = positions

    def _scan_images(self, epochs):
        """Units whose sampled images changed identity; each image is inspected once. While no
        headless node was assigned an image or a GPU resource since the last full scan, the
        nodes still sample what they did: only the distinct images and resources are read."""
        from ..evaluation.engine import headless
        from ..evaluation.engine.runtime import _image_identity
        changed = set()
        if self._groups_hold(self.image_nodes):
            for entry in self.resource_entries:
                identity = entry[0].gpu_resource.stamp
                if identity != entry[2]:
                    entry[2] = identity
                    changed.update(entry[1])
            for group in self.image_groups:
                identity = _image_identity(group[0], epochs)
                if identity != group[1]:
                    group[1] = identity
                    for entry in group[2]:
                        entry[2] = identity
                        changed.update(entry[1])
            return changed
        memo, groups, resources, grouped = {}, {}, [], True
        for entry in self.image_nodes:
            node = entry[0]
            grouped = grouped and isinstance(node, headless.Node)
            resource = getattr(node, 'gpu_resource', None)
            if resource is not None:
                identity = resource.stamp
                resources.append(entry)
            else:
                image = node.image
                identity = None
                if image is not None:
                    try:
                        pointer = image.as_pointer()
                    except ReferenceError:
                        pointer = None
                    if pointer is not None:
                        identity = memo.get(pointer, _MISSING)
                        if identity is _MISSING:
                            identity = memo[pointer] = _image_identity(image, epochs)
                            groups[pointer] = [image, identity, []]
                        groups[pointer][2].append(entry)
            if identity != entry[2]:
                entry[2] = identity
                changed.update(entry[1])
        # Only headless nodes report their assignments; a real node is scanned every time.
        self.image_groups = list(groups.values()) if grouped else None
        self.resource_entries = resources
        self.assigned = headless.assignments[0]
        return changed

    def _groups_hold(self, entries):
        """Whether the image groups still describe what ``entries`` sample: none of their nodes
        was assigned an image or a GPU resource since the groups were taken (assignments to
        nodes the index does not hold, a unit compiled meanwhile, do not count)."""
        from ..evaluation.engine import headless
        if self.image_groups is None:
            return False
        now = headless.assignments[0]
        if self.assigned != now:
            taken = self.assigned
            if any(getattr(entry[0], '_image_assigned', 0) > taken for entry in entries):
                return False
            self.assigned = now
        return True

    def _regroup(self, epochs, before):
        """``_scan_images`` after the index changed (``before``: the entries it held). While the
        groups still hold for the entries that stayed, only entries new to the index are read,
        and an entry no unit samples any more leaves its group."""
        from ..evaluation.engine import headless
        from ..evaluation.engine.runtime import _image_identity
        known = {id(entry) for entry in before}
        if not self._groups_hold([entry for entry in self.image_nodes if id(entry) in known]):
            self.image_groups = None
            return self._scan_images(epochs)
        fresh = [entry for entry in self.image_nodes if id(entry) not in known]
        if any(not isinstance(entry[0], headless.Node) for entry in fresh):
            self.image_groups = None
            return self._scan_images(epochs)   # a real node is scanned every time
        live = {id(entry) for entry in self.image_nodes}
        groups = []
        for group in self.image_groups:
            group[2] = [entry for entry in group[2] if id(entry) in live]
            if group[2]:
                groups.append(group)
        self.resource_entries = [entry for entry in self.resource_entries if id(entry) in live]
        by_pointer, changed = {}, set()
        if fresh:
            for group in groups:
                try:
                    by_pointer[group[0].as_pointer()] = group
                except ReferenceError:
                    pass
        for entry in fresh:
            node = entry[0]
            resource = getattr(node, 'gpu_resource', None)
            identity = None
            if resource is not None:
                identity = resource.stamp
                self.resource_entries.append(entry)
            elif node.image is not None:
                image = node.image
                try:
                    pointer = image.as_pointer()
                except ReferenceError:
                    pointer = None
                if pointer is not None:
                    group = by_pointer.get(pointer)
                    if group is None:
                        group = by_pointer[pointer] = [image, _image_identity(image, epochs), []]
                        groups.append(group)
                    identity = group[1]   # the next scan compares it with the image's current one
                    group[2].append(entry)
            if identity != entry[2]:
                entry[2] = identity
                changed.update(entry[1])
        self.image_groups = groups
        self.assigned = headless.assignments[0]
        if VERIFY:
            self._verify_groups()
        return changed

    def _verify_groups(self):
        """OP_VERIFY_INCREMENTAL: regrouped images are what a complete scan groups."""
        expected = {}
        for entry in self.image_nodes:
            node = entry[0]
            if getattr(node, 'gpu_resource', None) is None and node.image is not None:
                expected.setdefault(node.image.as_pointer(), set()).add(id(entry))
        found = {group[0].as_pointer(): {id(entry) for entry in group[2]} for group in self.image_groups}
        resources = {id(entry) for entry in self.image_nodes if getattr(entry[0], 'gpu_resource', None) is not None}
        if found != expected or {id(entry) for entry in self.resource_entries} != resources:
            raise AssertionError('Regrouped image entries differ from a complete scan')

    def _verify_description(self, engine, states, imports_of):
        """OP_VERIFY_INCREMENTAL: a reused program and its imports must be what ``_state`` gives."""
        from ..evaluation.engine import glsl
        for key, state in states.items():
            checked = engine._state(key)
            imports = tuple(sorted({s.producer[0] for p in glsl.programs(checked.program)
                                    for s in p.samplers if s.kind == 'import'}))
            if checked is not state or checked.program is not state.program or imports != imports_of[key]:
                raise AssertionError(f'Reused description of {key} is stale')

    def _reindex(self, engine, epochs, states, gone=()):
        """``_index`` for the units of ``states`` (described again) and ``gone`` (released) only."""
        changed = set(states) | set(gone)
        for producer in [p for p, owner in self.inline_owner.items() if owner in changed]:
            del self.inline_owner[producer]
        volatile, surface = set(self.volatile) - changed, set(self.surface_units) - changed
        entries = {}
        for entry in self.image_nodes:
            if not changed.isdisjoint(entry[1]):
                entry[1] = tuple(key for key in entry[1] if key not in changed)
            entries[id(entry[0])] = entry
        for key in gone:
            self.key_facts.pop(key, None)
        for key, state in states.items():
            facts = self.key_facts.get(key)
            if facts is None or facts[0] is not state.program:
                facts = self.key_facts[key] = _key_facts(engine, key, state.program)
            for producer in facts[1]:
                self.inline_owner[producer] = key
            if facts[2]:
                volatile.add(key)
            if facts[3]:
                surface.add(key)
            for node in facts[4]:
                entry = entries.get(id(node))
                if entry is None:
                    entry = entries[id(node)] = [node, (), _MISSING]
                if key not in entry[1]:
                    entry[1] = (*entry[1], key)
        before, self.image_nodes = self.image_nodes, [entry for entry in entries.values() if entry[1]]
        scanned = self._regroup(epochs, before)
        self.volatile, self.surface_units = frozenset(volatile), frozenset(surface)
        if surface and self.surface is None:
            self.surface = engine._surface()
        indices = self.indices
        for key, state in states.items():
            state.slot = Slot(self.current, self.resident, indices[key])
            state.slot.sync(state)
        return (set(states) | scanned) & indices.keys()

    def _dirty(self, engine, epochs, touched):
        """Units to restamp this evaluation; None when every unit must be."""
        dirty = self._scan_images(epochs)
        dirty |= self.volatile
        if self.surface_units:
            surface = engine._surface()
            if surface != self.surface:
                self.surface = surface
                dirty |= self.surface_units
        if touched is None:
            return None
        indices = self.indices
        for unit in touched:
            owner = self.inline_owner.get(unit, unit)
            if owner in indices:
                dirty.add(owner)
        return dirty

    def _verify(self, engine, epochs, changes):
        own = dict(self.own)
        for index, value in changes:
            own[self.keys[index]] = value
        missed = [key for key in self.indices
                  if own.get(key) != hash((key, engine._own_stamp(engine._state(key), epochs),
                                           self.constants[key])) & MASK]
        if missed:
            detail = [(key, engine._state(key).program is self.programs.get(key), key in self.own)
                      for key in missed[:4]]
            raise AssertionError(f'Incremental restamp missed {len(missed)} units ({self.path}): {missed[:8]} '
                                 f'(program described, own sent before): {detail}')

    def _compose(self, engine, keys, replaced):
        """Layer composition for C's affine suffix runs: what each unit of ``keys`` composes over
        (every other unit's is unchanged). After a new topology C keeps what it knew of a unit at
        the index it had; a slot given to another unit since is new to it."""
        if self.binding is None:
            self._reassigned = set()
            return
        indices, programs, positions = self.indices, self.programs, engine.positions
        backdrop, grouped, affine = self.backdrop, self.grouped, self.affine
        for key in keys:
            index = indices[key]
            below, children = positions.get(key) or (None, None)
            backdrop[index] = indices[below[1]] if below is not None and below[0] == 'unit' else NONE
            grouped[index] = indices[children[1]] if children is not None and children[0] == 'unit' else NONE
            affine[index] = key[0] == 'CHANNEL' and programs[key].affine is not None
        composition = (backdrop.tobytes(), grouped.tobytes(), bytes(affine))
        if composition != self.composition:
            if replaced:
                previous = array('I', range(len(self.keys)))
                for index in self._reassigned:
                    previous[index] = NONE
                self.binding.graph_compose(self.handle, *composition, previous.tobytes())
            else:
                self.binding.graph_compose(self.handle, *composition)
            self.composition = composition
        self._reassigned = set()

    def runs(self, engine, root):
        """Affine runs under ``root``, bottom-up: (top, input key or None, key, members bottom-up)."""
        if self.binding is None or self.composition is None or root not in self.indices:
            return []
        keys = self.keys
        rows = self.binding.graph_runs(self.handle, self.indices[root], bytes(self.resident))
        return [(keys[top], None if source == NONE else keys[source], key, tuple(keys[m] for m in members))
                for top, source, key, members in reversed(rows)]

    def mark_rendered(self, keys):
        """Units the host rendered itself in one pass (a fused chain): they were rendered with
        their current local stamps, as op_graph_execute records for the units it renders, so
        they stay members of the affine runs suffix collapse plans (op_graph_mark_rendered).
        Runs are planned through the binding only; without it there is nothing to record."""
        if self.binding is None or self.composition is None:
            return
        indices = array('I', (self.indices[key] for key in keys))
        self.binding.graph_mark_rendered(self.handle, indices.tobytes())

    def edit_runs(self, root, node):
        """The runs an edit of ``node``'s own content would produce under ``root``, bottom-up."""
        if (self.binding is None or self.composition is None or root not in self.indices
                or node not in self.indices):
            return []
        keys = self.keys
        rows = self.binding.graph_edit_runs(self.handle, self.indices[root], self.indices[node], None)
        return [(keys[top], None if source == NONE else keys[source], key, tuple(keys[m] for m in members))
                for top, source, key, members in reversed(rows)]

    def render(self, engine, key, size, memo, epochs):
        """C owns pass order and the lifetime of every dependency in the batch."""
        resident = self.resident

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
                elif target != key and target[0] == 'FIELD':
                    engine._drop_copy(target, size)

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


    def checkpoints(self, engine, channel):
        """Results to keep for a channel: the spine of its lowest changed unit (op_graph_spine)."""
        order = self.orders.get(channel)
        if order is None:
            return ()
        if self.binding is None or self.composition is None:
            keep = self.checkpoint(engine, channel)
            return (keep,) if keep is not None else ()
        keys = self.keys
        return tuple(keys[i] for i in self.binding.graph_checkpoints(self.handle, order[0], bytes(self.current)))

    def spine(self, node):
        """What an edit of ``node`` composes over without changing it, innermost first."""
        if self.binding is None or self.composition is None or node not in self.indices:
            return ()
        keys = self.keys
        return tuple(keys[i] for i in self.binding.graph_spine(self.handle, self.indices[node]))

    def checkpoint(self, engine, channel):
        order = self.orders.get(channel)
        if order is None:
            return None
        nodes, backdrops = order
        if self.binding is not None:
            keep = self.binding.graph_checkpoint(self.handle, nodes, backdrops, bytes(self.current))
        else:
            count = len(nodes) // 4
            self.lib.op_graph_checkpoint.argtypes = [ctypes.c_void_p, ctypes.POINTER(U32), ctypes.POINTER(U32), U32,
                                                     ctypes.POINTER(U64), ctypes.POINTER(U64), ctypes.POINTER(U32)]
            self.lib.op_graph_checkpoint.restype = ctypes.c_int
            output = U32()
            self.check(self.lib.op_graph_checkpoint(self.handle, (U32 * count).from_buffer_copy(nodes),
                                                    (U32 * count).from_buffer_copy(backdrops), count, self.current,
                                                    self.values, ctypes.byref(output)))
            keep = output.value
        return self.keys[keep] if keep != 0xffffffff else None
