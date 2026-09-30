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
from ..shader_graph import Binding, Graph, _sampler_limit
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
        self._stubs = any(unit.flags.get('stub') for unit in self.units.values())
        self.validate_parameters()
        corrections = self.update(check_structure=False) if self._reused else {}
        for binding in self.cache_bindings:
            self._reconcile_cache(binding)
        from ...core.validation import warnings
        unit_nodes = {repr(key): unit.node_count for key, unit in self.units.items()}
        queued = sum(key in self.resources.used for key in self.resources.queue)
        self._last = {'parameter_changes': corrections.get('parameter_changes', []),
                      'invalidated_nodes': corrections.get('invalidated_nodes', []), 'affected_channels': list(CHANNELS),
                      'updated_bindings': corrections.get('updated_bindings', 0), 'raster_boundaries_queued': queued,
                      'texture_bake_triggered': False, 'submission_ms': (perf_counter() - started) * 1000,
                      'build_ms': (perf_counter() - started) * 1000,
                      'shader_nodes': self.node_count, 'units': len(self.units), 'unit_nodes': unit_nodes,
                      'largest_unit': max(unit_nodes.values(), default=0),
                      'reused_units': len(self._reused), 'rebuilt_units': len(self.units) - len(self._reused),
                      'unique_images': len(self.unique_images()), 'max_textures_frag': _sampler_limit(),
                      'pending_resources': self.pending_resources,
                      'unit_build_ms': {repr(key): unit.build_ms for key, unit in self.units.items()},
                      'cache_states': self.cache_states(),
                      'warnings': list(warnings(self.ts)) + self._warnings}
        metrics.count('engine_graph_builds')
        from ..dependencies import remember, geometry_key
        remember(self.scene, self.ts, geometry_key(self.scene, self.ts))
        self._adopted = []

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
        self.consumers = defaultdict(set)
        for key, unit in self.units.items():
            for producer_key, _, _ in unit.imports:
                self.consumers[producer_key].add(key)
        readers = defaultdict(set, {key: set(found) for key, found in self.consumers.items()})

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

        self.reverse = defaultdict(set)
        self.channels = {'Final:' + channel: channel for channel in CHANNELS}
        for key, unit in self.units.items():
            targets = {'Final:' + channel for channel in channels(key)}
            for role in _dependency_roles(unit):
                self.reverse[role] |= targets

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
