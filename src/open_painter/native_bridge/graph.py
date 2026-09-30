"""Blender-to-portable-DAG adapter; native code owns ordering/invalidation.

Shader translation and GPU submission use the established host GPU adapter.
This is the host Metal execution route, not an external Metal texture import.
"""
import ctypes
import os

from .core import NativeError

U32, U64 = ctypes.c_uint32, ctypes.c_uint64
MASK = (1 << 64) - 1
NONE = 0xffffffff
# Restamp only units whose inputs can have changed since the previous evaluation.
INCREMENTAL = True
# Diagnostics: compare every incremental evaluation with a complete restamp.
VERIFY = os.environ.get('OP_VERIFY_INCREMENTAL') == '1'
_MISSING = object()
Submit = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, U32, U32, U64)


class Patch(ctypes.Structure):
    _fields_ = [('node', U32), ('reserved', U32), ('value', U64)]


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
        self.reprogrammed = 0   # evaluations that described only what a patch replaced

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
        from .lifecycle import active_library
        # Ordinary immutable descriptions only; no RNA or GPU address is given to C.
        cached = self.description
        graph = engine.graph
        take = getattr(graph, 'take_touched', None)
        touched = take() if take is not None else None
        take = getattr(engine, 'take_replaced', None)
        patched = take() if take is not None else None
        context = (engine.precise, bool(active_library()))
        valid = (cached is not None and cached[0] is graph and cached[1] is engine.inline
                 and cached[6] == context)
        dirty = states = reprogrammed = None
        self.path = 'valid'
        resend = ()       # unchanged units whose own stamps a new topology cleared in C
        earlier = set()   # units an earlier step of this evaluation marked dirty
        if valid and patched is not None:
            # A patch replaced these units in the same graph (or only moved the wiring): while
            # the topology holds, only they are described again.
            reprogrammed = self._reprogram(engine, epochs, patched) if INCREMENTAL else None
            valid = reprogrammed is not None
            self.path = 'reprogram' if valid else 'full'
        if valid:
            keys, indices, constants = cached[2:5]
            dirty = self._dirty(engine, epochs, touched) if INCREMENTAL else None
            if dirty is None:
                dirty = keys
            elif reprogrammed:
                dirty |= reprogrammed
            earlier = set(dirty)
            states = {key: engine._state(key) for key in dirty}
            # A program recompiled on a signature change may read other inputs.
            valid = cached[1] is engine.inline and all(states[key].program is self.programs[key] for key in dirty)
        if not valid:
            # A rebuilt graph that kept the topology restamps only what can have changed.
            self.described += 1
            self.path = 'full' if self.path == 'valid' else self.path + '>full'
            previous_programs, previous, previous_surface = self.programs, cached, self.surface
            identities = {id(entry[0]): entry[2] for entry in self.image_nodes}
            roots = [ref[1] for ref in engine.finals.values() if ref and ref[0] == 'unit']
            # After a patch of the same graph, a unit it did not replace keeps its program and
            # what it imports, unless a producer compiled into it changed or moved in or out.
            reusable = (patched is not None and cached is not None and cached[0] is graph
                        and cached[6] == context and self.imports)
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
                        states[key], imports = state, list(self.imports[key])
                    else:
                        state = states[key] = engine._state(key)
                        imports = sorted({s.producer[0] for p in glsl.programs(state.program)
                                          for s in p.samplers if s.kind == 'import'})
                    imports_of[key] = tuple(imports)
                    position = engine.positions.get(key) or ()
                    links = imports + [r[1] for r in position if r and r[0] == 'unit']
                    deps[key] = tuple(links)
                    constants[key] = tuple(r if r is None or r[0] == 'const' else ('unit', r[1])
                                           for r in position)
                    work.extend(links)
                if inline is engine.inline:
                    break  # sampler-limit inlining has stabilized
            if VERIFY and reusable:
                self._verify_description(engine, states, imports_of)
            self.imports = imports_of
            keys = tuple(sorted(states))
            indices = {key: i for i, key in enumerate(keys)}
            self.deps, self.roots = deps, tuple(roots)
            offsets, edges = [0], []
            for key in keys:
                edges.extend(indices[k] for k in deps[key])
                offsets.append(len(edges))
            topology = keys, tuple(offsets), tuple(edges)
            replaced = topology != self.topology
            kept_own = dict(self.own)   # a new topology clears C's own stamps; unchanged units resend theirs
            if replaced:
                a, b = (U32 * len(offsets))(*offsets), (U32 * len(edges))(*edges)
                if self.binding is not None:
                    self.binding.graph_replace(self.handle, bytes(a), bytes(b))
                else:
                    self.check(self.lib.op_graph_replace(self.handle, len(keys), a, b, len(edges)))
                self.own.clear()
                self.topology = topology
                self.composition = None   # replacing the topology clears it in C
                self.rebuilds += 1
            # After a new topology, each unit's previous index: C keeps what it knew about a unit
            # whose composition is unchanged (its armed suffix runs keep working).
            before = previous[3] if replaced and previous is not None else None
            self._compose(engine, keys, indices, {key: states[key].program for key in keys},
                          None if before is None else [before.get(key, NONE) for key in keys])
            self.description = (graph, engine.inline, keys, indices, constants,
                                tuple((key, states[key].program) for key in keys), context)
            scanned = self._index(engine, keys, indices, states, epochs, identities)
            dirty = keys
            if INCREMENTAL and previous is not None and kept_own and touched is not None:
                constants_before = previous[4]
                changed = {key for key in keys if states[key].program is not previous_programs.get(key)
                           or constants_before.get(key) != constants[key]}
                changed |= scanned | self.volatile
                if self.surface_units and self.surface != previous_surface:
                    changed |= self.surface_units
                for unit in touched:
                    owner = self.inline_owner.get(unit, unit)
                    if owner in indices:
                        changed.add(owner)
                # What an earlier step of this evaluation found (its scan recorded the new image
                # identities, so this description's scan no longer sees them change).
                changed |= earlier
                dirty = changed
                if replaced:
                    # C lost every own stamp with the old topology: an unchanged unit's is the
                    # one it had (a new unit has none, and counts as changed above).
                    resend = [(key, kept_own[key]) for key in keys if key not in changed and key in kept_own]
                    dirty |= {key for key in keys if key not in changed and key not in kept_own}
        count = len(keys)
        changes = []
        for key, value in resend:
            changes.append(Patch(indices[key], 0, value))
            self.own[key] = value
        for key in dirty:
            value = hash((key, engine._own_stamp(states[key], epochs), constants[key])) & MASK
            if self.own.get(key) != value:
                changes.append(Patch(indices[key], 0, value))
        if VERIFY and dirty is not keys:
            self._verify(engine, epochs, keys, constants, changes)
        if changes and self.binding is None:
            self.check(self.lib.op_graph_apply_patch(self.handle, (Patch * len(changes))(*changes), len(changes)))
            for patch in changes:
                self.own[keys[patch.node]] = patch.value
        self.last_patch_count = len(changes)
        self.last_dirty_count = len(dirty)
        stamps, plan, written = (U64 * count)(), (U32 * count)(), U32()
        if self.binding is not None:
            stamp_data, plan_data = self.binding.graph_evaluate(self.handle, bytes((Patch * len(changes))(*changes)),
                                                                bytes(self.resident))
            stamps = (U64 * count).from_buffer_copy(stamp_data)
            written.value = len(plan_data) // 4
            plan = (U32 * written.value).from_buffer_copy(plan_data)
            for patch in changes:
                self.own[keys[patch.node]] = patch.value
        else:
            self.check(self.lib.op_graph_evaluate_current(self.handle, self.resident, stamps, plan,
                                                        count, ctypes.byref(written)))
        self.keys, self.indices, self.values = keys, indices, stamps
        self.stamps = dict(zip(keys, stamps))
        self.plan = tuple(keys[plan[i]] for i in range(written.value))
        self.evaluations += 1
        return self.stamps

    def _index(self, engine, keys, indices, states, epochs, identities=None):
        """What can change a unit's own stamp without a new description: its uniforms
        (reported by update()), the images it samples, object matrices, filters and
        the surface. Also mirrors every unit's stamp into native-order arrays."""
        nodes, volatile, surface = {}, set(), set()
        self.programs = {key: states[key].program for key in keys}
        self.inline_owner = {}
        before, facts_of = self.key_facts, {}
        for key in keys:
            program = states[key].program
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
        count = len(keys)
        self.current, self.resident = (U64 * count)(), (U64 * count)()
        live = set(keys)
        for key, state in engine.states.items():
            if key not in live:
                state.slot = None
        for key in keys:
            state = states[key]
            state.slot = Slot(self.current, self.resident, indices[key])
            state.slot.sync(state)
        self.orders = {}
        for channel, order in engine.orders.items():
            below = []
            for key in order:
                ref = engine.positions[key][0]
                below.append(indices[ref[1]] if ref is not None and ref[0] == 'unit' else NONE)
            self.orders[channel] = (bytes((U32 * len(order))(*(indices[key] for key in order))),
                                    bytes((U32 * len(below))(*below)))
        return scanned

    def _scan_images(self, epochs):
        """Units whose sampled images changed identity; each image is inspected once. While no
        headless node was assigned an image or a GPU resource since the last full scan, the
        nodes still sample what they did: only the distinct images and resources are read."""
        from ..evaluation.engine import headless
        from ..evaluation.engine.runtime import _image_identity
        changed = set()
        if self.image_groups is not None and self.assigned == headless.assignments[0]:
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

    def _reprogram(self, engine, epochs, replaced):
        """Describe again only what a patch replaced: the replaced units the channels reach and
        the units their inlined producers compile into. Returns the units to restamp; None,
        having changed nothing, when the topology or what any unit composes over changed."""
        from ..evaluation.engine import glsl
        graph, inline, keys, indices, constants, _, context = self.description
        roots = tuple(ref[1] for ref in engine.finals.values() if ref and ref[0] == 'unit')
        if roots != self.roots:
            return None
        positions = engine.positions
        for key in keys:
            position = positions.get(key) or ()
            if constants[key] != tuple(r if r is None or r[0] == 'const' else ('unit', r[1]) for r in position):
                return None
        targets = {key for key in replaced if key in indices}
        targets.update(self.inline_owner[key] for key in replaced if key in self.inline_owner)
        states = {}
        for key in targets:
            if key not in engine.states:
                return None   # a unit the channels read is gone
            state = states[key] = engine._state(key)
            if engine.inline is not inline:
                return None   # the sampler limit moved a producer out of its consumer
            imports = sorted({s.producer[0] for p in glsl.programs(state.program)
                              for s in p.samplers if s.kind == 'import'})
            position = positions.get(key) or ()
            if tuple(imports + [r[1] for r in position if r and r[0] == 'unit']) != self.deps.get(key):
                return None   # it reads other units now
        for key, state in states.items():
            self.programs[key] = state.program
            self.imports[key] = tuple(sorted({s.producer[0] for p in glsl.programs(state.program)
                                              for s in p.samplers if s.kind == 'import'}))
        self._compose(engine, keys, indices, self.programs)
        self.description = (graph, inline, keys, indices, constants,
                            tuple((key, self.programs[key]) for key in keys), context)
        self.reprogrammed += 1
        return self._reindex(engine, epochs, states)

    def _verify_description(self, engine, states, imports_of):
        """OP_VERIFY_INCREMENTAL: a reused program and its imports must be what ``_state`` gives."""
        from ..evaluation.engine import glsl
        for key, state in states.items():
            checked = engine._state(key)
            imports = tuple(sorted({s.producer[0] for p in glsl.programs(checked.program)
                                    for s in p.samplers if s.kind == 'import'}))
            if checked is not state or checked.program is not state.program or imports != imports_of[key]:
                raise AssertionError(f'Reused description of {key} is stale')

    def _reindex(self, engine, epochs, states):
        """``_index`` for the units of ``states`` only."""
        changed = set(states)
        for producer in [p for p, owner in self.inline_owner.items() if owner in changed]:
            del self.inline_owner[producer]
        volatile, surface = set(self.volatile) - changed, set(self.surface_units) - changed
        entries = {}
        for entry in self.image_nodes:
            if not changed.isdisjoint(entry[1]):
                entry[1] = tuple(key for key in entry[1] if key not in changed)
            entries[id(entry[0])] = entry
        for key, state in states.items():
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
        self.image_nodes = [entry for entry in entries.values() if entry[1]]
        self.image_groups = None
        scanned = self._scan_images(epochs)
        self.volatile, self.surface_units = frozenset(volatile), frozenset(surface)
        if surface and self.surface is None:
            self.surface = engine._surface()
        indices = self.description[3]
        for key, state in states.items():
            state.slot = Slot(self.current, self.resident, indices[key])
            state.slot.sync(state)
        return changed | scanned

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
        indices = self.description[3]
        for unit in touched:
            owner = self.inline_owner.get(unit, unit)
            if owner in indices:
                dirty.add(owner)
        return dirty

    def _verify(self, engine, epochs, keys, constants, changes):
        own = dict(self.own)
        for patch in changes:
            own[keys[patch.node]] = patch.value
        missed = [key for key in keys
                  if own.get(key) != hash((key, engine._own_stamp(engine._state(key), epochs), constants[key])) & MASK]
        if missed:
            detail = [(key, engine._state(key).program is self.programs.get(key), key in self.own)
                      for key in missed[:4]]
            raise AssertionError(f'Incremental restamp missed {len(missed)} units ({self.path}): {missed[:8]} '
                                 f'(program described, own sent before): {detail}')

    def _compose(self, engine, keys, indices, programs, previous=None):
        """Layer composition for C's affine suffix runs: what each unit composes over.
        ``previous``: each unit's index before a new topology (NONE for a new unit)."""
        if self.binding is None:
            return
        backdrop, grouped, affine = [], [], bytearray()
        for key in keys:
            position = engine.positions.get(key) or (None, None)
            for refs, ref in ((backdrop, position[0]), (grouped, position[1])):
                refs.append(indices[ref[1]] if ref is not None and ref[0] == 'unit' else NONE)
            affine.append(int(key[0] == 'CHANNEL' and programs[key].affine is not None))
        composition = (tuple(backdrop), tuple(grouped), bytes(affine))
        if composition != self.composition:
            if previous is None:
                self.binding.graph_compose(self.handle, bytes((U32 * len(keys))(*backdrop)),
                                           bytes((U32 * len(keys))(*grouped)), bytes(affine))
            else:
                self.binding.graph_compose(self.handle, bytes((U32 * len(keys))(*backdrop)),
                                           bytes((U32 * len(keys))(*grouped)), bytes(affine),
                                           bytes((U32 * len(keys))(*previous)))
            self.composition = composition

    def runs(self, engine, root):
        """Affine runs under ``root``, bottom-up: (top, input key or None, key, members bottom-up)."""
        if self.binding is None or self.composition is None or root not in self.indices:
            return []
        keys = self.keys
        rows = self.binding.graph_runs(self.handle, self.indices[root], bytes(self.resident))
        return [(keys[top], None if source == NONE else keys[source], key, tuple(keys[m] for m in members))
                for top, source, key, members in reversed(rows)]

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
