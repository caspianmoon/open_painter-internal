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
            identifier = f'Parameter:{prefix}{owner.path_from_id()}.{field}>>{shift}'
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
            with self.role('SourceField:' + owner.path_from_id() + ':' + kind):
                return self._procedural(kind, self.coord_uv(), self.param(owner, 'noise_scale'),
                                        self.seed_param(owner, 'seed'))
        if enabled(owner) or not supported(kind):
            return super()._field(owner, kind, generator, rehydrate)
        with self.role('SourceField:' + owner.path_from_id() + ':' + kind):
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
        self._structure = self.structural_signature()
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
        for layer in self.ts.layers:
            if layer.parent_id != parent or not self._included(layer):
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
        reach = {}

        def channels(key, seen=frozenset()):
            if key in reach:
                return reach[key]
            if key in seen:
                return set()
            found = {key[2]} if key[0] == 'CHANNEL' else set()
            for reader in readers.get(key, ()):
                found |= channels(reader, seen | {key})
            reach[key] = found
            return found

        self.reverse = defaultdict(set)
        self.channels = {'Final:' + channel: channel for channel in CHANNELS}
        for key, unit in self.units.items():
            targets = {'Final:' + channel for channel in channels(key)}
            for node in unit.tree.nodes:
                role = node.get('op_dependency')
                if role:
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
        return [(node, node.image) for node in unit.tree.nodes if node.bl_idname == 'ShaderNodeTexImage']
