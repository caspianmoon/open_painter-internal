"""The stack compiler, running on headless trees for the Open Painter engine.

``EngineGraph`` is ``shader_graph.Graph`` with every Blender datablock taken out:
units compile into ``headless.Tree`` objects, nothing is instanced in a material,
and nothing is persisted for undo (the engine recompiles from the stack, and its
programs are cached by source text, so a recompile of unchanged units is free).
Everything else — planning, unit signatures and adoption, parameter / image /
resource / cache bindings, CPU boundaries — is the compiler's own logic, so the
engine evaluates exactly the semantics the node graph described.

The composition the top-level tree used to wire (``Graph._wire``) becomes
``chains``: per channel, the CHANNEL units bottom to top, folders with their
children, which ``runtime`` renders pass by pass.
"""
from collections import defaultdict
from time import perf_counter

from ...constants import CHANNELS
from .. import metrics
from ..shader_graph import (VERIFY_STRUCTURE, Binding, Graph, _removed_graph_units, _same_literal,
                            _sampler_limit)
from ..shader_math import Signal
from . import headless


class BitsBinding(Binding):
    """A binding of 16 bits of an integer property (see ``EngineGraph.seed_param``)."""

    def __init__(self, owner, field, index, socket, value, identifier, shift):
        super().__init__(owner, field, index, socket, value, identifier)
        self.shift = shift

    def read(self):
        return float((int(getattr(self.owner, self.field)) >> self.shift) & 0xFFFF)


class EngineGraph(Graph):
    def blend(self, backdrop, source, coverage, mode, channel):
        # Keep semantic operations compact. Native translation owns the math;
        # the diagnostic legacy translator expands the independent reference.
        from numbers import Real
        node = self.node('OPBlend')
        node.operation = mode
        node.opaque = isinstance(backdrop.alpha, Real) and backdrop.alpha == 1.0
        for socket, value in zip(node.inputs, (backdrop.color, backdrop.alpha,
                                               source.color, source.alpha, coverage)):
            self.link(value, socket)
        return Signal(node.outputs['Color'], node.outputs['Alpha'])

    def pointwise_effect(self, signal, effect):
        from ..shader_effects import effect_key, EFFECT_FIELDS, EFFECT_VECTORS
        key = effect_key(effect.kind)
        node = self.node('OPEffect')
        node.operation = key
        entries = [('source', headless.VECTOR, signal.color)]
        for field in (*EFFECT_FIELDS[key], 'strength', 'enabled'):
            kind = headless.VECTOR if field in EFFECT_VECTORS else headless.VALUE
            if field == 'detail':
                value = self.effect_image(effect) if effect.image else (.5, .5, 1.)
            elif field.startswith('row'):
                row = int(field[-1])
                value = self.combine(*(self.param(effect, 'matrix', row * 3 + j) for j in range(3)))
            elif kind == headless.VECTOR:
                value = self.combine(*(self.param(effect, field, j) for j in range(3)))
            else:
                value = self.param(effect, field)
            entries.append((field, kind, value))
        for field, kind, value in entries:
            node.inputs._add(headless.Socket(node, field, kind, False))
            self.link(value, node.inputs[field])
        return Signal(node.outputs['Color'], signal.alpha)

    def _mask_plan(self, masks, start, stop, pre_filter, initial=False):
        from .runtime import device
        from ...native_bridge.lifecycle import execution_library
        from ...native_bridge.masks import snapshot, plan
        lib = execution_library(device().backend)
        if lib is None:
            return None
        return plan(lib, snapshot(masks), start, stop, pre_filter, initial)

    def _mask_walk(self, masks, start, stop, pre_filter, prefix):
        plan = self._mask_plan(masks, start, stop, pre_filter)
        if plan is None:
            return super()._mask_walk(masks, start, stop, pre_filter, prefix)
        prefix.extend(masks[index] for index in plan.walk)
        return prefix

    def _mask_chain(self, layer, masks, start, stop, pre_filter, result, prefix):
        from itertools import chain
        from ...native_bridge.masks import NONE, INITIAL, ONE
        plan = self._mask_plan(masks, start, stop, pre_filter, result is not None)
        if plan is None:
            return super()._mask_chain(layer, masks, start, stop, pre_filter, result, prefix)
        folded = []
        def reference(index):
            if index == NONE:
                return None
            if index == INITIAL:
                return result
            if index == ONE:
                return 1.0
            return folded[index]
        for index, previous, value, begin, end in plan.steps:
            mask = masks[index]
            previous = reference(previous)
            if value == NONE:
                # Only a CPU filter boundary consumes this prefix. Keep ordinary
                # sources O(1); the boundary freezes the iterator on demand.
                source_prefix = chain(prefix if begin == NONE else (),
                                      (masks[plan.walk[j]] for j in range(0 if begin == NONE else begin, end)))
                with self.role(f'MaskSource:{layer.id}:{mask.id}'):
                    value = self._mask_value(layer, mask, previous, source_prefix, pre_filter)
            else:
                value = reference(value)
            folded.append(self._mask_fold(layer, mask, value, previous))
        prefix.extend(masks[index] for index in plan.walk)
        return reference(plan.result)

    def _mask_fold(self, layer, mask, value, result):
        node = self.node('OPMaskFold')
        node.first = result is None
        node.operation = ('NORMAL' if mask.source == 'FILTER' else
                          mask.operation if mask.operation in {'NORMAL', 'MULTIPLY', 'MAX', 'MIN'} else 'ADD')
        self.link(1.0 if result is None else result, node.inputs['Previous'])
        self.link(value, node.inputs['Value'])
        with self.role(f'MaskLevels:{layer.id}:{mask.id}'):
            for field in ('black', 'white', 'invert'):
                self.link(self.param(mask, field), node.inputs[field.title()])
        with self.role(f'MaskMix:{layer.id}:{mask.id}'):
            self.link(self.param(mask, 'strength'), node.inputs['Strength'])
        return node.outputs['Value']

    def __init__(self, scene, ts):
        super().__init__(scene, ts, None)
        self.chains = {}
        self.consumers = defaultdict(set)   # unit key -> unit keys that import it

    # ---- trees -------------------------------------------------------------------

    def _new_unit_tree(self, key):
        tree = headless.Tree(self._unit_name(key))
        tree['op_unit'] = repr(key)
        return tree

    def _free_tree(self, tree):
        # A discarded unit (a failed compile): the images it bound become releasable; the
        # tree itself is garbage collected.
        if tree is not None:
            for node in tree.nodes:
                node.release_image()

    def _native_graph(self, layer, mask):
        """A Mask Graph compiles into its unit's GPU program (``procedural_graph.gpu``)."""
        from ...procedural_graph import gpu
        return gpu.emit(self, mask)

    def native_seed(self, owner, field, tree=''):
        """A Mask Graph node's seed: two exact 16-bit uniforms, like ``seed_param``, named by
        its graph (node paths repeat across graphs)."""
        return tuple(self._bits_param(owner, field, shift, f'{tree}:') for shift in (16, 0))

    def _custom_graph(self, mask):
        """A legacy Blender shader group is baked on the CPU (a Mask Graph compiles to GPU code)."""
        layer = next((item for item in self.ts.layers if any(m.id == mask.id for m in item.masks)), None)
        if layer is None:
            raise ValueError(f'Mask "{mask.name}" has no layer')
        return self._mask_source_boundary(layer, mask)

    def rehydrate(self):
        return False

    # ---- sources the material graph had to rasterise on the CPU ----------------------

    def _strokes_boundary(self, layer, prop, rehydrate=None):
        """Stroke records are a GPU input; the CPU graph remains the export reference."""
        from ...painting.gpu_sources import Source
        node = self.node('ShaderNodeTexImage')
        node.extension = 'EXTEND'
        node.gpu_resource = Source(self, layer, prop)
        node['op_stroke_layer'], node['op_stroke_channel'] = layer.id, prop.channel
        self.link(self.coord_uv(), node.inputs['Vector'])
        return Signal(node.outputs['Color'], node.outputs['Alpha'])

    def spatial_effect(self, signal, effect):
        from . import filters
        from ..shader_effects import UnsupportedEffect
        if not filters.supported(effect):
            raise UnsupportedEffect(f'{effect.kind} uses the CPU reference')
        node = self.node('OPSpatialFilter')
        node.effect = effect
        self.link(signal.color, node.inputs['Color'])
        self.link(signal.alpha, node.inputs['Alpha'])
        amount = self.math('MULTIPLY', self.param(effect, 'strength'), self.param(effect, 'enabled'))
        return Signal(self.mix(signal.color, node.outputs['Color'], amount), signal.alpha)

    def _procedural(self, kind, coordinate, scale, seed):
        """A catalog source at ``coordinate``, evaluated in the unit's GPU program.

        ``seed`` is the (high, low) pair ``seed_param`` binds.
        """
        node = self.node('OPProcedural')
        node.kind = kind
        self.link(coordinate, node.inputs['Vector'])
        self.link(scale, node.inputs['Scale'])
        self.link(seed[0], node.inputs['Seed High'])
        self.link(seed[1], node.inputs['Seed Low'])
        return node.outputs['Value']

    def seed_param(self, owner, field):
        """An integer seed as two uniforms holding its 16-bit halves (exact in float32,
        where the whole seed — catalog materials use about 1.2e9 — is not)."""
        return tuple(self._bits_param(owner, field, shift) for shift in (16, 0))

    def _bits_param(self, owner, field, shift, prefix=''):
        key = (self.unit.key if self.unit is not None else None, owner.as_pointer(), field, ('bits', shift))
        if key not in self.bindings:
            identifier = f'Parameter:{prefix}{self._path(owner)}.{field}>>{shift}'
            with self.role(identifier):
                node = self.node('ShaderNodeValue')
            binding = BitsBinding(owner, field, None, node.outputs[0], 0.0, identifier, shift)
            binding.value = binding.read()
            node.outputs[0].default_value = binding.value
            self._bind(key, binding)
        return self.bindings[key].socket

    def _field(self, owner, kind=None, generator=False, rehydrate=None):
        """Integer-hash catalog fields run on the GPU; scale and seed are uniforms.

        The legacy value noise of Noise masks (a sine hash no GPU reproduces to the bit)
        and metric material mapping keep the CPU raster.
        """
        from ..material_space import enabled
        from .procedural_glsl import supported
        kind = kind or owner.procedural
        if generator:
            if owner.source == 'NOISE' or not supported(kind):
                return super()._field(owner, kind, generator, rehydrate)
            with self.role('SourceField:' + self._path(owner) + ':' + kind):
                return self._procedural(kind, self.coord_uv(), self.param(owner, 'noise_scale'),
                                        self.seed_param(owner, 'seed'))
        if enabled(owner) or not supported(kind):
            return super()._field(owner, kind, generator, rehydrate)
        with self.role('SourceField:' + self._path(owner) + ':' + kind):
            scale, seed = self.param(owner, 'procedural_scale'), self.seed_param(owner, 'procedural_seed')
            sample = self.projected(owner, lambda uv: Signal(self._procedural(kind, uv, scale, seed)))
            return self.separate(sample.color)[0]

    def _mask_source_boundary(self, layer, mask, rehydrate=None):
        from ..material_space import enabled
        from .procedural_glsl import supported
        if mask.source == 'PROCEDURAL' and supported(mask.procedural) and not enabled(mask):
            kind = mask.procedural
            scale, seed = self.param(mask, 'noise_scale'), self.seed_param(mask, 'seed')
            sample = self.projected(mask, lambda uv: Signal(self._procedural(kind, uv, scale, seed)))
            value = self.separate(sample.color)[0]
            if mask.projection == 'UV' and mask.tiling == 'NONE':
                value = self.math('MULTIPLY', value, self._coverage(self.coordinates(mask)))
            return value
        return super()._mask_source_boundary(layer, mask, rehydrate)

    def generator(self, mask, layer=None):
        # The material graph rasterised baked-edge Edge Wear and Chips on the CPU only to stay
        # under Metal's per-material sampler limit; a unit program has its own samplers.
        kind = 'NOISE' if mask.source == 'NOISE' else mask.generator
        if kind in {'EDGE_WEAR', 'CHIPS'} and getattr(mask, 'edge_source', 'BAKED') == 'BAKED':
            return super().generator(mask, None)
        return super().generator(mask, layer)

    # ---- build -------------------------------------------------------------------

    def _build(self, plan, started):
        self.tree = headless.Tree('OP Engine · ' + self.ts.name)
        for key in plan.order:
            self.compile_unit(key)
        self.resources.used = self._used_resources()
        self.chains = {channel: self._chain('', channel, frozenset()) for channel in CHANNELS}
        self._reverse_map()
        self._structure = self.structural_signature(plan)
        self._settled(plan)
        self._stubs = any(unit.flags.get('stub') for unit in self.units.values())
        self._layout = self._stack_layout()
        self.validate_parameters()
        corrections = self.update(check_structure=False) if self._reused else {}
        for binding in self.cache_bindings:
            self._reconcile_cache(binding)
        unit_nodes = {repr(key): unit.node_count for key, unit in self.units.items()}
        unit_build_ms = {repr(key): unit.build_ms for key, unit in self.units.items()}
        self._last = self._report(started, corrections, len(self.units) - len(self._reused), unit_nodes, unit_build_ms)
        metrics.count('engine_graph_builds')
        from ..dependencies import remember, geometry_key
        remember(self.scene, self.ts, geometry_key(self.scene, self.ts))
        self._adopted = []

    def _report(self, started, corrections, rebuilt, unit_nodes, unit_build_ms, patched=False, nodes=None,
                references=None):
        """``references``: the reference warnings, when no reference changed since they were read."""
        from ..references import warnings as reference_warnings
        from ..mesh_masks import warnings as mesh_warnings
        queued = sum(key in self.resources.used for key in self.resources.queue)
        if references is None:
            references = reference_warnings(self.ts)
        elif VERIFY_STRUCTURE and list(references) != list(reference_warnings(self.ts)):
            raise AssertionError('A patch reused reference warnings that changed')
        self._reference_warnings = references
        nodes = self.node_count if nodes is None else nodes
        self._tally = (nodes, unit_nodes, unit_build_ms)
        return {'parameter_changes': corrections.get('parameter_changes', []),
                'invalidated_nodes': corrections.get('invalidated_nodes', []), 'affected_channels': list(CHANNELS),
                'updated_bindings': corrections.get('updated_bindings', 0), 'raster_boundaries_queued': queued,
                'texture_bake_triggered': False, 'submission_ms': (perf_counter() - started) * 1000,
                'build_ms': (perf_counter() - started) * 1000,
                'shader_nodes': nodes, 'units': len(self.units),
                'unit_nodes': unit_nodes,
                'largest_unit': max(unit_nodes.values(), default=0),
                'reused_units': len(self.units) - rebuilt, 'rebuilt_units': rebuilt, 'patched': patched,
                # Counted when explicit diagnostics ask (a walk over every node of every unit).
                'unique_images': None, 'max_textures_frag': _sampler_limit(),
                'pending_resources': self.pending_resources,
                'unit_build_ms': unit_build_ms,
                'cache_states': self.cache_states(),
                'warnings': list(references) + mesh_warnings(self.ts) + self._build_warnings()}

    def structural_signature(self, plan=None):
        """``Graph.structural_signature`` with the units as a dict: nothing persists its text
        (``rehydrate`` is the material graph's), so no sort of every unit per structural edit."""
        from ..shader_graph import global_signature
        plan = plan if plan is not None else self.plan()
        stubs = {key: unit.flags['stub'] for key, unit in self.units.items() if unit.flags.get('stub')}
        signature = self._unit_signature
        reached, structure = plan.affected, self._structure
        settled = self._structure_plan
        if (reached is not None and structure is not None and not stubs and not self._stubs
                and settled is not None and plan.carried is getattr(settled, 'token', None)):
            # Only a layer the plan re-planned can sign otherwise: the rest is the structure's.
            units = {key: value for key, value in structure[2].items() if key[1] not in reached}
            units.update((key, signature(key)) for key in plan.keys if key[1] in reached)
            if VERIFY_STRUCTURE:
                full = {key: signature(key) for key in plan.keys}
                if full != units:
                    differ = sorted(str(key) for key in full.keys() | units.keys() if full.get(key) != units.get(key))
                    raise AssertionError(f'Incremental signature differs from a full one: {differ[:6]}')
        else:
            units = {key: signature(key, stub=stubs.get(key)) for key in plan.keys}
        return (global_signature(self.ts), tuple(plan.wiring), units)

    def _stack_layout(self):
        """What a patch needs where the build left it: the set and its baked maps, which every
        unit captures. Layers that moved in their array are followed (``_patch``)."""
        ts = self.ts
        return tuple(item.as_pointer() for item in (ts, *ts.baked_maps))

    # ---- structural edits in place ---------------------------------------------------

    def patch(self, reconcile=False):
        """Apply a structural edit to this graph: recompile only the units whose structure changed.

        A rebuild compiles a new graph and adopts every unchanged unit into it, one by one: at 200
        layers, some 1,750 units for the 4 to 8 a blend mode or a mask changes. Here the other
        units keep their trees, bindings and engine states. Stale are the units whose signature
        changed, whose captured owners moved (a collection of their layer was reallocated), whose
        Mask Graph lost a bound item, and the importers of a recompiled unit whose exports no
        longer match. Returns the build report; None, having changed nothing, when only a rebuild
        can apply the edit: the layers moved in their array, the set's globals, resolution or
        baked maps changed, or the stack does not validate (the rebuild reports why).
        ``reconcile``: edits were dropped while the rebuild was pending, so bindings are read
        again: every one (True, as a rebuild does) or those of these owner addresses (a set).
        A failed compile restores the graph and raises.
        """
        from ...core.validation import hierarchy_issues
        from ...core.udim import validate as validate_udim
        from ..references import scope
        ts = self.ts
        if (not self.units or self._structure is None or self._previous is not None
                or int(ts.resolution) != self.n or getattr(self, '_layout', None) != self._stack_layout()):
            return None
        with scope(ts):   # the checks below and the report's warnings read one reference graph
            try:
                validate_udim(ts)
            except ValueError:
                return None
            return self._patch(reconcile, hierarchy_issues)

    def _patch(self, reconcile, hierarchy_issues):
        started = perf_counter()
        taken = self.scope_plan(self.ts, signature=True)
        if taken is None:
            plan = self.plan(self._structure_plan, self._dirty_layers)
            current = self.structural_signature(plan)
        else:
            (plan, current), self._anchors = taken, None
            self._plan = plan
        # A reference cycle, a folder's mask referenced, a parent gone: only an edit of a layer
        # taking part in references, or of a layer's parent or kind, can make one.
        references = self._references_reached(plan)
        if references or VERIFY_STRUCTURE:
            problems = hierarchy_issues(self.ts)
            if problems and not references:
                raise AssertionError(f'A patch that reached no reference found issues: {problems}')
            if problems:
                return None
        before = self._structure
        if current[0] != before[0]:
            return None
        signatures = dict(current[2])
        previous = dict(before[2])
        stale = {key for key in self.units
                 if key not in signatures or previous.get(key) != signatures[key]}
        # Only a layer an edit reached (``plan.affected``) can have had its collections moved;
        # a layer whose own struct moved (a layer added, removed or moved below it, or the
        # array grown) is followed where it went.
        reached = plan.affected
        before_plan = self._structure_plan
        moved = ({identifier for identifier, pointer in plan.pointers.items()
                  if before_plan.pointers.get(identifier, pointer) != pointer}
                 if before_plan is not None and getattr(before_plan, 'pointers', None) is not None else None)
        follow, at = {}, len(self._base_owners())
        for key, unit in self.units.items():
            if key in stale:
                continue
            if unit.tree is None:
                stale.add(key)
            elif reached is not None and moved is not None and key[1] not in reached:
                if key[1] in moved:
                    # Only its layer's struct moved: every other owner it captured is where it was.
                    before_owners = unit.owner_pointers
                    follow[key] = before_owners[:at] + (plan.pointers[key[1]],) + before_owners[at + 1:]
            else:
                owners = self._unit_owners(key)
                if unit.owner_pointers != owners:
                    follow[key] = owners
        entries = {}
        for key, owners in follow.items():
            unit = self.units[key]
            if not self._follow_moved_layer(unit, key, owners, tables=True):
                stale.add(key)
            for entry in unit.image_bindings:
                entries[id(entry[3])] = entry
        if entries:
            # The graph's entries carry the identities ``update`` last saw; keep those.
            self.image_bindings = [(*entries[id(entry[3])][:4], entry[4]) if id(entry[3]) in entries else entry
                                   for entry in self.image_bindings]
        removed = _removed_graph_units(self.bindings.values() if reached is None else
                                       [binding for key, unit in self.units.items() if key[1] in reached
                                        for binding in unit.bindings.values()])
        if removed == 'global':
            return None
        stale.update(key for key in removed if key in self.units)
        stubs = {key: self._stub(key) for key in stale if key in signatures}   # what ``current`` signed
        retired = {key: self.units.pop(key) for key in stale}
        self._unbind_units(retired.values())
        kept = set(self.units)
        # Collections of the edited layers may have moved, and moved layers sit at another
        # index; every other layer's structs stay where they were.
        self._refresh_paths(plan, None if reached is None or moved is None else reached | moved)
        self._reused = []
        try:
            for key in plan.order:
                self.compile_unit(key)
            for key in list(retired):
                if key in signatures:
                    self.compile_unit(key)   # a producer only kept units import (a layer's masks)
            self._recompile_importers(kept, retired)
        except Exception:
            self._building_units = set()
            for key in [key for key in self.units if key not in kept]:
                self._discard_partial(self.units.pop(key))
            self._rebind_units(retired.values())
            self.units.update(retired)
            self._compiled_effects = {}
            for unit in self.units.values():
                self._compiled_effects.update(unit.compiled_effects)
            self.resources.used = self._used_resources()   # what the discarded units queued is dropped
            raise
        fresh = [key for key in self.units if key not in kept]
        self._units_token = object()
        nodes, unit_nodes, unit_build_ms = self._tally   # what the last build or patch counted
        nodes -= sum(unit.node_count for unit in retired.values())
        for unit in retired.values():
            self._free_tree(unit.tree)
        self.resources.used = self._used_resources()
        if current[1] != before[1]:
            self.chains = {channel: self._chain('', channel, frozenset()) for channel in CHANNELS}
        self._compiled_effects = {}
        for unit in self.units.values():
            self._compiled_effects.update(unit.compiled_effects)
        if current[1] != before[1] or not self._reverse_units(retired, fresh):
            if not self._reverse_changed(retired):
                self._reverse_map()
        if VERIFY_STRUCTURE:
            self._verify_reverse()
        self._stubs = any(unit.flags.get('stub') for unit in self.units.values())
        if all(self._stub(key) == stubs.get(key) for key in fresh):
            self._structure = current   # each recompiled unit signs as ``current`` assumed
        else:
            self._structure = self.structural_signature(plan)
        self._settled(plan)
        if self.touched is not None:
            self.touched.update(fresh)
        for binding in (*self.resource_bindings, *self.cache_bindings):
            # As adoption does: an input a kept closure resolves through the live graph (an
            # anchor another layer publishes) may have moved or gained owners.
            if binding.owner_factory is not None:
                binding.owners = {o.as_pointer() for o in binding.owner_factory()}
        self.validate_parameters()
        # Inputs a structural edit re-identifies (an anchor's prefix, a cached subtree) are
        # reconciled as a rebuild does, those a layer the edit reached owns (one that only moved
        # reads what it read); uniforms only when edits were dropped meanwhile.
        owned = None
        path_owners = getattr(self, '_path_owners', None) if self._paths is not None else None
        if not reconcile and reached is not None and moved is not None and path_owners is not None:
            owned = set()
            for identifier in reached:
                owned.update(path_owners.get(identifier, ()))
            for key in fresh:
                owned.update(self.units[key].owner_pointers)
        if reconcile is True:
            corrections = self.update(check_structure=False)
        else:
            corrections = self.update(check_structure=False, values=False, owners=owned)
            if reconcile:
                # Edits of these owners were dropped while the patch was pending: their values are
                # read again, a layer's at its current address.
                pointers = {plan.pointers.get(handle[1]) if isinstance(handle, tuple) else handle
                            for handle in reconcile}
                for pointer in pointers - {None}:
                    found = self.update(_Address(pointer), check_structure=False)
                    for name in ('parameter_changes', 'invalidated_nodes'):
                        corrections[name] = list(corrections.get(name, [])) + list(found.get(name, []))
                    corrections['updated_bindings'] = (corrections.get('updated_bindings', 0)
                                                       + found.get('updated_bindings', 0))
        for key in (self.units if reconcile is True else fresh):
            for binding in self.units[key].cache_bindings:
                self._reconcile_cache(binding)
        unit_nodes, unit_build_ms = dict(unit_nodes), dict(unit_build_ms)
        for key in retired:
            unit_nodes.pop(repr(key), None)
            unit_build_ms.pop(repr(key), None)
        for key in fresh:
            unit_nodes[repr(key)] = self.units[key].node_count
            unit_build_ms[repr(key)] = self.units[key].build_ms
        self._last = self._report(started, corrections, len(fresh), unit_nodes, unit_build_ms, patched=True,
                                  nodes=nodes + sum(self.units[key].node_count for key in fresh),
                                  references=None if references else self._reference_warnings)
        self._last['patched_units'] = sorted(fresh)
        self._last['retired_units'] = sorted(retired)
        metrics.count('engine_graph_patches')
        from ..dependencies import remember, geometry_key
        remember(self.scene, self.ts, geometry_key(self.scene, self.ts))
        return self._last

    def _references_reached(self, plan):
        """Whether an issue ``hierarchy_issues`` reports can have appeared: a re-planned layer
        reads or publishes an anchor or a layer, now or before, or is read; a layer is left
        unreachable, has a parent that is no folder, or a folder became another kind; or ids
        repeat. A layer added, removed or moved without references causes none of these."""
        previous = self._structure_plan
        if plan.affected is None or previous is None or getattr(self, '_reference_warnings', None) is None:
            return True
        if len(plan.layers) != len(plan.ids) or '' in plan.layers:
            return True
        named = self._links(previous)[3]
        for identifier in plan.affected:
            now, before = plan.facts.get(identifier), previous.facts.get(identifier)
            for facts in (now, before):
                if facts is not None and (facts[0] or len(facts[1]) > 2):
                    return True   # reads something, or publishes an anchor beyond its two composites
            if any(named.get(handle) for handle in ((now or before or ((), ()))[1]) or ()) \
                    or named.get(('layer', identifier)):
                return True   # something reads it
            if identifier in plan.layers and identifier not in plan.wired:
                return True   # left unreachable
            entry, old = plan.wired.get(identifier), previous.wired.get(identifier)
            if entry is not None and (old is None or entry[1:3] != old[1:3]):
                parent = entry[2]
                if parent and (plan.wired.get(parent) or (None, None))[1] != 'FOLDER':
                    return True   # a parent that is no folder
                if old is not None and old[1] == 'FOLDER' and entry[1] != 'FOLDER':
                    return True   # a folder became another kind: its children lose their parent
        return False

    def _recompile_importers(self, kept, retired):
        """Kept units that import a recompiled one: each is recompiled too unless the
        exports it was linked against (a socket by name, a baked literal) are unchanged."""
        importers = getattr(self, '_importers', None)
        if importers is None:
            importers = defaultdict(set)
            for key in kept:
                unit = self.units[key]
                for producer_key, _, _ in (*unit.imports, *unit.const_imports):
                    importers[producer_key].add(key)
        work, seen = [key for key in self.units if key not in kept], set()
        while work:
            producer = work.pop()
            if producer in seen:
                continue
            seen.add(producer)
            for consumer in list(importers.get(producer, ())):
                unit = self.units.get(consumer)
                if consumer not in kept or unit is None or self._imports_match(unit):
                    continue
                kept.discard(consumer)
                retired[consumer] = self.units.pop(consumer)
                self._unbind_units((retired[consumer],))
                self.compile_unit(consumer)
                work.append(consumer)

    def _imports_match(self, unit):
        for producer_key, export_name, _ in unit.imports:
            producer = self.units.get(producer_key)
            if producer is None or producer.exports.get(export_name) != ('socket', export_name):
                return False
        for producer_key, export_name, payload in unit.const_imports:
            producer = self.units.get(producer_key)
            export = producer.exports.get(export_name) if producer is not None else None
            if export is None or export[0] != 'const' or not _same_literal(export[1], payload):
                return False
        return True

    def _chain(self, parent, channel, ancestors):
        """CHANNEL units of ``parent``'s children, bottom to top, as ``Graph._wire`` links them."""
        steps = []
        # The plan indexes each parent's children in stack order: a walk over the whole
        # stack per folder and channel made this quadratic in folders.
        children = getattr(getattr(self, '_plan', None), 'children', None)
        layers = (children.get(parent, ()) if children is not None
                  else [layer for layer in self.ts.layers if layer.parent_id == parent])
        for layer in layers:
            if not self._included(layer):
                continue
            if layer.id in ancestors:
                raise ValueError('Layer hierarchy contains a cycle')
            if not self._wired(layer, channel):
                continue
            key = ('CHANNEL', layer.id, channel)
            step = {'key': key}
            if layer.type == 'FOLDER':
                step['children'] = self._chain(layer.id, channel, ancestors | {layer.id})
                step['passthrough'] = layer.group_mode == 'PASSTHROUGH'
            steps.append(step)
        return steps

    def _reverse_map(self):
        """Which output channels each dependency role reaches, at unit granularity.

        ``Graph.update`` reports ``affected_channels`` by walking ``reverse`` from the
        roles an edit touched to ``Final:<channel>`` (export invalidation narrows to
        them). A unit's roles reach the channel of every unit that reads its result:
        a consumer importing it, the chain step composed over it, the folder composing
        its children — transitively, so a layer under a published folder reaches the
        channels of that folder's anchor consumers.
        """
        self.consumers, targets = self._reach_targets()
        self.reverse = defaultdict(set)
        self.channels = {'Final:' + channel: channel for channel in CHANNELS}
        self._targets, self._role_units = targets, defaultdict(set)
        self._reach_token = object()         # what reads what changed (``_live_effects``)
        for key, unit in self.units.items():
            for role in _dependency_roles(unit):
                self.reverse[role] |= targets[key]
                self._role_units[role].add(key)
        self._index_importers()

    def _index_importers(self):
        self._importers = defaultdict(set)   # producer -> units importing it (a socket or a literal)
        for key, unit in self.units.items():
            for producer_key, _, _ in (*unit.imports, *unit.const_imports):
                self._importers[producer_key].add(key)

    def _reach_targets(self):
        """(consumers, targets): the units importing each unit, and the ``Final:<channel>``
        roles each unit's result reaches."""
        consumers = defaultdict(set)
        for key, unit in self.units.items():
            for producer_key, _, _ in unit.imports:
                consumers[producer_key].add(key)
        readers = defaultdict(set, {key: set(found) for key, found in consumers.items()})

        def chain(steps, below):
            for step in steps:
                if below is not None:
                    readers[below].add(step['key'])
                if 'children' in step:
                    last = chain(step['children'], below if step['passthrough'] else None)
                    if last is not None:
                        readers[last].add(step['key'])
                below = step['key']
            return below

        for steps in self.chains.values():
            chain(steps, None)
        reach, visiting = {}, set()

        def channels(key):
            # Memoised depth-first walk. Chains are as deep as the stack: copying the
            # path at every step made this quadratic in it.
            found = reach.get(key)
            if found is not None:
                return found
            if key in visiting:
                return set()   # a cycle (reported by the compiler): contributes nothing more
            visiting.add(key)
            found = {key[2]} if key[0] == 'CHANNEL' else set()
            for reader in readers.get(key, ()):
                found |= channels(reader)
            visiting.discard(key)
            reach[key] = found
            return found

        return consumers, {key: frozenset('Final:' + channel for channel in channels(key)) for key in self.units}

    def _verify_reverse(self):
        """OP_VERIFY_STRUCTURE: the patched dependency map must equal one mapped from scratch."""
        def plain(table):
            return {key: set(value) for key, value in table.items() if value}
        found = (plain(self.reverse), plain(self.consumers), dict(self._targets), plain(self._importers))
        self._reverse_map()
        expected = (plain(self.reverse), plain(self.consumers), dict(self._targets), plain(self._importers))
        for name, mine, full in zip(('reverse', 'consumers', 'targets', 'importers'), found, expected):
            if mine != full:
                differ = sorted(str(key) for key in mine.keys() | full.keys() if mine.get(key) != full.get(key))
                raise AssertionError(f'Patched {name} differ from a full map: {differ[:6]}')

    def _reverse_changed(self, retired):
        """``_reverse_map`` after a patch that changed which units exist or what reads what (a
        layer added, removed or moved): reach is walked again for every unit (memoised), roles
        are assembled again only for the units that came, went, were replaced or reach other
        channels now. False when there is no map to update."""
        before = getattr(self, '_targets', None)
        if before is None:
            return False
        self.consumers, targets = self._reach_targets()
        roles = set()
        for key, unit in retired.items():
            for role in _dependency_roles(unit):
                self._role_units[role].discard(key)
                roles.add(role)
        changed = False
        for key, unit in self.units.items():
            replaced = key in retired
            if replaced or before.get(key) != targets[key]:
                changed = changed or not replaced or before.get(key) != targets[key]
                for role in _dependency_roles(unit):
                    self._role_units[role].add(key)
                    roles.add(role)
        changed = changed or any(key not in self.units for key in retired)
        self._targets = targets
        for role in roles:
            found = set()
            for key in self._role_units.get(role, ()):
                found |= targets[key]
            if found:
                self.reverse[role] = found
            else:
                self.reverse.pop(role, None)
                self._role_units.pop(role, None)
        if changed:
            self._reach_token = object()
        self._index_importers()
        return True

    def _reverse_units(self, retired, fresh):
        """``_reverse_map`` after a patch that replaced units without changing what reads them:
        the same keys, each importing the same producers, and the same chains. Only the roles of
        the replaced units change; what each unit reaches does not. False: map everything."""
        targets = getattr(self, '_targets', None)
        if targets is None or set(fresh) != set(retired):
            return False
        for key in fresh:
            if ({producer for producer, _, _ in self.units[key].imports}
                    != {producer for producer, _, _ in retired[key].imports}):
                return False
        roles = set()
        for key, unit in retired.items():
            for role in _dependency_roles(unit):
                self._role_units[role].discard(key)
                roles.add(role)
            for producer_key, _, _ in unit.const_imports:
                self._importers[producer_key].discard(key)
        for key in fresh:
            unit = self.units[key]
            for role in _dependency_roles(unit):
                self._role_units[role].add(key)
                roles.add(role)
            for producer_key, _, _ in (*unit.imports, *unit.const_imports):
                self._importers[producer_key].add(key)
        for role in roles:
            found = set()
            for key in self._role_units[role]:
                found |= targets[key]
            if found:
                self.reverse[role] = found
            else:
                self.reverse.pop(role, None)
                self._role_units.pop(role, None)
        return True

    # ---- lifetime ------------------------------------------------------------------

    def unbind(self):
        """Stop counting the images these units bind (``shader_resources.bound``). No image is
        read: after an undo or a file load the references may be stale."""
        for tree in self.trees():
            for node in tree.nodes:
                node.release_image()

    def close(self):
        # Images only these units bind become releasable.
        self.unbind()
        self.units = {}
        self.resources.close()

    def detach(self):
        for entry in self.resources.entries.values():
            if entry.job:
                entry.job.close()
                entry.job = None
        self.resources.queue.clear()

    # ---- queries for the runtime ---------------------------------------------------

    def bound_sampler_count(self):
        """Image inputs of the largest unit: each unit is a GPU pass with its own samplers
        (the material samples the set's eight channel images only)."""
        return max((len({(node.image.as_pointer(), node.interpolation, node.extension)
                         for node in unit.tree.nodes if node.bl_idname == 'ShaderNodeTexImage' and node.image})
                    for unit in self.units.values() if unit.tree is not None), default=0)

    def unit_images(self, key):
        """(node, image) of every image node of a unit, in node order."""
        unit = self.units[key]
        # Only node membership is cached. Resource replacement, image identity
        # and color/alpha metadata must be read afresh by the runtime.
        return [(node, node.image) for node in unit.tree.nodes.image_nodes]


class _Address:
    """An owner by address alone, for ``Graph.update``'s narrowing (its struct may have moved)."""
    __slots__ = ('pointer',)

    def __init__(self, pointer):
        self.pointer = pointer

    def as_pointer(self):
        return self.pointer


def _dependency_roles(unit):
    """The dependency roles of a unit's nodes. An adopted unit keeps its tree, so the roles
    are read once per tree (and again should its node count change), not on every build."""
    tree = unit.tree
    count = len(tree.nodes)
    cached = getattr(unit, '_roles', None)
    if cached is None or cached[0] is not tree or cached[1] != count:
        roles = tuple(role for role in (node.get('op_dependency') for node in tree.nodes) if role)
        cached = unit._roles = (tree, count, roles)
    return cached[2]
