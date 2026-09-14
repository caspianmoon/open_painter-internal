"""Compile editable authoring data into a persistent Blender material graph.

Numeric edits update existing uniform sockets. Images are immutable inputs, not
the output mechanism. Legacy hashed fields and neighborhood filters are explicit
compatibility boundaries; each has its own transitive input identity and jobs.
"""
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter
from types import SimpleNamespace

import bpy
import numpy as np

from ..constants import CHANNELS, DEFAULTS, OWNER
from ..core.layer_stack import channel_for
from .shader_math import Math, Signal, blend, luminance, normal_combine
from .shader_effects import apply_effect, UnsupportedEffect
from .shader_resources import Resources, RESOURCE, values
from .resource_keys import image_key
from . import metrics


class RebuildRequired(Exception):
    """A structural edit changed links or operation types, not just uniforms."""


def structural_signature(ts):
    def effects(items):
        return tuple((e.kind, bool(e.enabled), bool(e.image)) for e in items)
    return (int(ts.resolution), tuple((m.kind, bool(m.image)) for m in ts.baked_maps),
            tuple((l.id, l.type, l.parent_id, l.visible, l.group_mode, l.blend_mode,
                   tuple((p.channel, p.enabled, p.blend_mode, bool(p.image),
                          bool(p.detail_image), p.procedural, p.projection, p.filtering,
                          p.tiling, effects(p.effects)) for p in l.channels),
                   tuple((m.id, m.enabled, m.source, m.generator, m.procedural,
                          m.map_type, m.operation, m.projection, m.filtering, m.tiling,
                          bool(m.image), m.reference_layer, m.reference_mask,
                          m.reference_channel, m.reference_stage,
                          m.effect.kind, m.effect.enabled, bool(m.effect.image)) for m in l.masks))
                  for l in ts.layers))


@dataclass
class Binding:
    owner: object
    field: str
    index: object
    socket: object
    value: float
    identifier: str

    def read(self):
        value = getattr(self.owner, self.field)
        return float(value if self.index is None else value[self.index])


@dataclass
class ResourceBinding:
    identifier: str
    owners: set
    identity: object
    factory: object
    node: object
    signature: object
    pending_image: object = None


class Graph(Math):
    def __init__(self, scene, ts, material):
        self.scene, self.ts, self.material = scene, ts, material
        self.n = int(ts.resolution)
        self.tree = None
        self.instance = None
        self.outputs = {}
        self.bindings = {}
        self.owner_bindings = defaultdict(list)
        self.resource_bindings = []
        self.image_bindings = []
        self.resources = Resources(self.n)
        self.reverse = defaultdict(set)
        self.channels = {}
        self._role = 'coordinates'
        self._source_cache, self._mask_cache = {}, {}
        self._compiled_effects = {}
        self._building = set()
        self._structure = None
        self._last = {}

    @contextmanager
    def role(self, identifier):
        previous, self._role = self._role, identifier
        try:
            yield
        finally:
            self._role = previous

    def node(self, kind):
        node = super().node(kind)
        node['op_dependency'] = self._role
        node.label = self._role
        return node

    def param(self, owner, field, index=None):
        key = (owner.as_pointer(), field, index)
        if key not in self.bindings:
            identifier = 'Parameter:' + owner.path_from_id() + '.' + field
            if index is not None:
                identifier += f'[{index}]'
            with self.role(identifier):
                node = self.node('ShaderNodeValue')
            raw = getattr(owner, field)
            value = float(raw if index is None else raw[index])
            node.outputs[0].default_value = value
            binding = Binding(owner, field, index, node.outputs[0], value, identifier)
            self.bindings[key] = binding
            self.owner_bindings[owner.as_pointer()].append(binding)
        return self.bindings[key].socket

    def color_param(self, owner, field):
        return self.combine(*(self.param(owner, field, i) for i in range(3)))

    def scalar_mix(self, a, b, t):
        return self.math('ADD', a, self.math('MULTIPLY', self.math('SUBTRACT', b, a), t))

    def smooth(self, low, high, value):
        x = self.clamp(self.math('DIVIDE', self.math('SUBTRACT', value, low),
                               self.math('MAXIMUM', self.math('SUBTRACT', high, low), 1e-6)))
        return self.math('MULTIPLY', self.math('MULTIPLY', x, x),
                         self.math('SUBTRACT', 3, self.math('MULTIPLY', 2, x)))

    def _coverage(self, coordinate):
        x, y, _ = self.separate(coordinate)
        value = 1.0
        for c in (x, y):
            value = self.math('MULTIPLY', value, self.math('SUBTRACT', 1, self.math('LESS_THAN', c, 0)))
            value = self.math('MULTIPLY', value, self.math('SUBTRACT', 1, self.math('GREATER_THAN', c, 1)))
        return value

    def coordinates(self, owner, pair=None):
        x, y = pair if pair is not None else self.separate(self.uv)[:2]
        center = .5 if pair is None else 0.0
        x = self.math('MULTIPLY', self.math('SUBTRACT', x, center), self.param(owner, 'scale', 0))
        y = self.math('MULTIPLY', self.math('SUBTRACT', y, center), self.param(owner, 'scale', 1))
        angle = self.param(owner, 'rotation')
        c, s = self.math('COSINE', angle), self.math('SINE', angle)
        u = self.math('SUBTRACT', self.math('MULTIPLY', c, x), self.math('MULTIPLY', s, y))
        v = self.math('ADD', self.math('MULTIPLY', s, x), self.math('MULTIPLY', c, y))
        return self.combine(self.math('ADD', self.math('ADD', u, center), self.param(owner, 'offset', 0)),
                            self.math('ADD', self.math('ADD', v, center), self.param(owner, 'offset', 1)), 0)

    def image(self, image, coordinate=None, filtering='BILINEAR', tiling='REPEAT', getter=None, owner=None):
        if image is None:
            raise ValueError('A shader source image is missing')
        node = self.node('ShaderNodeTexImage')
        node.image = image
        node.interpolation = 'Closest' if filtering == 'NEAREST' else 'Linear'
        node.extension = 'REPEAT' if tiling == 'REPEAT' else 'EXTEND'
        coord = coordinate if coordinate is not None else self.uv
        self.link(coord, node.inputs['Vector'])
        if getter:
            self.image_bindings.append((owner.as_pointer(), self._role, getter, node, image_key(image)))
        result = Signal(node.outputs['Color'], node.outputs['Alpha'])
        if tiling == 'NONE':
            coverage = self._coverage(coord)
            result = Signal(self.vec('SCALE', result.color, coverage),
                            self.math('MULTIPLY', result.alpha, coverage))
        return result

    def projected(self, owner, sampler):
        if owner.projection == 'UV':
            return sampler(self.coordinates(owner))
        position = self.separate(self.geometry.outputs['Position'])
        normal = self.separate(self.geometry.outputs['Normal'])
        weights = [self.math('ABSOLUTE', v) for v in normal]
        maximum = self.math('MAXIMUM', self.math('MAXIMUM', weights[0], weights[1]), weights[2])
        hardness = self.param(owner, 'triplanar_hardness')
        weights = [self.math('POWER', self.math('DIVIDE', w, self.math('MAXIMUM', maximum, 1e-8)), hardness)
                   for w in weights]
        total = self.math('MAXIMUM', self.math('ADD', self.math('ADD', weights[0], weights[1]), weights[2]), 1e-8)
        color, alpha = (0., 0., 0.), 0.0
        for axis, (a, b) in enumerate(((1, 2), (0, 2), (0, 1))):
            value = sampler(self.coordinates(owner, (position[a], position[b])))
            weight = self.math('DIVIDE', weights[axis], total)
            color = self.vec('ADD', color, self.vec('SCALE', value.color, weight))
            alpha = self.math('ADD', alpha, self.math('MULTIPLY', value.alpha, weight))
        return Signal(color, alpha)

    def effect_image(self, effect):
        return self.image(effect.image, getter=lambda: effect.image, owner=effect).color

    def mesh_map(self, kind):
        prop = next((m for m in self.ts.baked_maps if m.kind == kind and m.image), None)
        if prop is None:
            raise ValueError(f'This mask requires a baked {kind.title()} map. Bake mesh maps first.')
        return self.image(prop.image, getter=lambda: prop.image, owner=prop)

    def boundary(self, identifier, owners, identity, factory):
        """Expose one unsupported source/prefix as a stable shader image input."""
        signature = identity()
        image = self.resources.request(signature, lambda: factory(signature))
        node = self.node('ShaderNodeTexImage')
        node.image = image
        node.interpolation, node.extension = 'Linear', 'EXTEND'
        self.link(self.uv, node.inputs['Vector'])
        self.resource_bindings.append(ResourceBinding(identifier,
            {o.as_pointer() for o in owners}, identity, factory, node, signature))
        return Signal(node.outputs['Color'], node.outputs['Alpha'])

    @staticmethod
    def _parts(data, rows=16):
        for low in range(0, data.shape[0], rows):
            yield low, data[low:low + rows]

    def _geometry_stamp(self, needed):
        if not needed:
            return None
        from .dependencies import geometry_key
        stamp = geometry_key(self.scene, self.ts)
        # Triplanar sources depend on assigned mesh geometry. Scene-field
        # influencers have their own identities and must not evict those leaves.
        return stamp[:4] if stamp is not None else None

    def _field(self, owner, kind=None, generator=False):
        """Preserve custom integer-hash noise as a separately cached scalar."""
        kind = kind or owner.procedural
        if generator:
            identity = lambda: ('legacy-generator-field', kind, owner.source == 'NOISE', owner.noise_scale, owner.seed)
        else:
            identity = lambda: ('legacy-channel-field', kind, owner.procedural_scale, owner.procedural_seed,
                owner.projection, tuple(owner.scale), owner.rotation, tuple(owner.offset), owner.triplanar_hardness,
                self._geometry_stamp(owner.projection == 'TRIPLANAR'))

        def factory(signature):
            if generator:
                _, name, old_noise, scale, seed = signature
                def parts():
                    from ..texture_engine.procedurals import evaluate
                    from ..generators.noise import noise
                    u = (np.arange(self.n, dtype=np.float32)[None, :] + .5) / self.n
                    for low in range(0, self.n, 16):
                        v = (np.arange(low, min(low + 16, self.n), dtype=np.float32)[:, None] + .5) / self.n
                        yield low, (noise(u, v, scale, seed) if old_noise else evaluate(name, u, v, scale=scale, seed=seed))
                return parts()
            from .snapshots import capture
            snapshot = capture(owner)
            def parts():
                from .channel_eval import _procedural_tile
                resources = {}
                for low in range(0, self.n, 16):
                    yield low, _procedural_tile(snapshot, self.n, low, min(low + 16, self.n), self.ts, resources)
            return parts()
        with self.role('SourceField:' + owner.path_from_id() + ':' + kind):
            return self.separate(self.boundary(self._role, [owner], identity, factory).color)[0]

    ANALYTIC = {'SOLID', 'GRADIENT', 'LINEAR_GRADIENT', 'RADIAL_GRADIENT',
                'CHECKER', 'STRIPES', 'DOTS', 'PATTERN', 'GRID', 'TILE_PATTERN', 'BRICK_PATTERN'}

    def analytic(self, kind, coordinate, scale):
        u, v, _ = self.separate(coordinate)
        x, y = self.math('MULTIPLY', u, scale), self.math('MULTIPLY', v, scale)
        fx, fy = self.math('FRACT', x), self.math('FRACT', y)
        if kind == 'SOLID':
            return 1.0
        if kind in {'GRADIENT', 'LINEAR_GRADIENT'}:
            return fx
        if kind == 'CHECKER':
            return self.math('PINGPONG', self.math('ADD', self.math('FLOOR', x), self.math('FLOOR', y)), 1)
        if kind == 'STRIPES':
            return self.math('LESS_THAN', fx, .5)
        if kind == 'RADIAL_GRADIENT':
            length = self.vec('DISTANCE', coordinate, (.5, .5, 0))
            return self.clamp(self.math('SUBTRACT', 1, self.math('MULTIPLY', length, scale)))
        if kind in {'DOTS', 'PATTERN'}:
            distance = self.vec('DISTANCE', self.combine(fx, fy, 0), (.5, .5, 0))
            return self.math('SUBTRACT', 1, self.smooth(.26, .3, distance))
        if kind == 'BRICK_PATTERN':
            fx = self.math('FRACT', self.math('ADD', x,
                self.math('MULTIPLY', self.math('PINGPONG', self.math('FLOOR', y), 1), .5)))
        bx = self.math('MINIMUM', fx, self.math('SUBTRACT', 1, fx))
        by = self.math('MINIMUM', fy, self.math('SUBTRACT', 1, fy))
        value = self.smooth(.02, .04, self.math('MINIMUM', bx, by))
        return self.math('SUBTRACT', 1, value) if kind == 'GRID' else value

    def detail(self, prop):
        if prop.detail_image:
            result = self.projected(prop, lambda uv: self.image(prop.detail_image, uv,
                prop.filtering, prop.tiling, getter=lambda: prop.detail_image, owner=prop))
            return luminance(self, result.color)
        from ..texture_engine.procedurals import ALIASES
        kind = ALIASES.get(prop.procedural, prop.procedural)
        if kind in self.ANALYTIC:
            scale = self.param(prop, 'procedural_scale')
            result = self.projected(prop, lambda uv: Signal(self.analytic(kind, uv, scale)))
            return self.separate(result.color)[0]
        return self._field(prop, kind)

    def _channel_prefix(self, layer, prop, count):
        owners = [prop, *list(prop.effects)[:count]]
        def identity():
            return ('channel-prefix', layer.type, prop.channel,
                    values(prop, excluded={'effects', 'enabled', 'opacity', 'blend_mode'}),
                    tuple(values(e, excluded={'strength'} if i == count - 1 else ())
                          for i, e in enumerate(list(prop.effects)[:count])),
                    self._geometry_stamp(prop.projection == 'TRIPLANAR'))
        def factory(signature):
            from .snapshots import capture
            effects = tuple(capture(e).replaced(strength=1.) if i == count - 1 else capture(e)
                            for i, e in enumerate(list(prop.effects)[:count]))
            snapshot = capture(prop).replaced(effects=effects)
            def parts():
                from .channel_eval import _raw_source, apply_texture_effects
                from .operation_contracts import stack_footprint
                from .tiles import expand
                halo = stack_footprint(snapshot.effects)
                cache, geometry = {}, {}
                if halo is not None:
                    for low in range(0, self.n, 16):
                        high = min(low + 16, self.n)
                        start, stop = expand(low, high, halo, self.n)
                        data = np.empty((stop - start, self.n, 4), np.float32)
                        for row in range(start, stop, 16):
                            end = min(row + 16, stop)
                            data[row - start:end - start] = _raw_source(snapshot, layer.type, self.n,
                                                                        row, end, cache, self.ts, geometry)
                        yield low, apply_texture_effects(data, snapshot.effects)[low-start:high-start]
                    return
                data = np.empty((self.n, self.n, 4), np.float32)
                for low in range(0, self.n, 16):
                    high = min(low + 16, self.n)
                    data[low:high] = _raw_source(snapshot, layer.type, self.n, low, high, cache, self.ts, geometry)
                    # Source generation yields before the bounded unsupported filter.
                    yield low, data[low:high]
                filtered = apply_texture_effects(data, snapshot.effects)
                yield from self._parts(filtered)
            return parts()
        with self.role(f'RasterChannelPrefix:{layer.id}:{prop.channel}:{count}'):
            return self.boundary(self._role, owners, identity, factory)

    def source(self, layer, channel, pre_filter=False):
        key = (layer.id, channel, pre_filter)
        if key in self._source_cache:
            return self._source_cache[key]
        prop = channel_for(layer, channel)
        if prop is None or not prop.enabled or layer.type == 'FOLDER':
            return Signal((0., 0., 0.), 0)
        if prop.image_name and not prop.image:
            raise ValueError('Texture image is missing: ' + prop.image_name + '. Reload or clear the channel texture.')
        if prop.detail_image_name and not prop.detail_image:
            raise ValueError('Surface detail image is missing: ' + prop.detail_image_name + '. Reload or clear the detail texture.')
        with self.role(f'LayerSource:{layer.id}:{channel}'):
            if prop.image:
                value = (self.image(prop.image, getter=lambda: prop.image, owner=prop) if layer.type == 'PAINT'
                    else self.projected(prop, lambda uv: self.image(prop.image, uv, prop.filtering, prop.tiling,
                                                                  getter=lambda: prop.image, owner=prop)))
                if layer.type == 'PAINT' and prop.image.is_float:
                    # Native float paint canvases contain premultiplied pixels;
                    # the compositor's source-over inputs are straight color.
                    factor = self.math('DIVIDE', self.math('GREATER_THAN', value.alpha, 1e-8),
                                       self.math('MAXIMUM', value.alpha, 1e-8))
                    value = Signal(self.vec('SCALE', value.color, factor), value.alpha)
            elif layer.type == 'PAINT':
                value = Signal((0., 0., 0.), 0)
            elif channel in {'BASE_COLOR', 'NORMAL', 'EMISSION'}:
                value = Signal(self.color_param(prop, 'color'), self.param(prop, 'color', 3))
            else:
                value = Signal(self.param(prop, 'value'))
            field = layer.type == 'FILL' and bool(prop.detail_image or prop.procedural)
            if field and channel == 'NORMAL':
                # UV derivatives are a compatibility source boundary. Normal
                # strength/combine effects remain shader-native downstream.
                value = self._channel_prefix(layer, prop, 0)
            elif field:
                scalar = self.math('SUBTRACT', self.detail(prop), .5)
                if channel == 'HEIGHT':
                    color = self.math('ADD', .5, self.math('MULTIPLY', scalar, self.param(prop, 'procedural_height')))
                elif channel in {'BASE_COLOR', 'EMISSION'}:
                    color = self.vec('SCALE', value.color, self.math('ADD', 1,
                        self.math('MULTIPLY', scalar, self.param(prop, 'procedural_detail'))))
                else:
                    variation = self.math('MULTIPLY', self.math('MULTIPLY', scalar, self.param(prop, 'procedural_detail')), .35)
                    color = self.vec('ADD', value.color, self.combine(variation, variation, variation))
                value = Signal(self.clamp(color), value.alpha)
                if prop.projection == 'UV' and prop.tiling == 'NONE':
                    value = Signal(value.color, self.math('MULTIPLY', value.alpha, self._coverage(self.coordinates(prop))))
            if not pre_filter:
                for index, effect in enumerate(prop.effects):
                    if not effect.enabled:
                        continue
                    self._compiled_effects[effect.as_pointer()] = effect
                    with self.role(f'ChannelEffect:{layer.id}:{channel}:{index}'):
                        try:
                            value = apply_effect(self, value, effect)
                        except UnsupportedEffect:
                            filtered = self._channel_prefix(layer, prop, index + 1)
                            value = Signal(self.mix(value.color, filtered.color, self.param(effect, 'strength')),
                                           value.alpha)
        self._source_cache[key] = value
        return value

    def generator(self, mask):
        kind = 'NOISE' if mask.source == 'NOISE' else mask.generator
        amount, contrast = self.param(mask, 'amount'), self.param(mask, 'contrast')
        strength = self.param(mask, 'noise_strength')
        field = None
        def noise():
            nonlocal field
            if field is None:
                field = self._field(mask, 'FBM', generator=True)
            return field
        def threshold(value):
            return self.clamp(self.math('ADD', self.math('MULTIPLY',
                self.math('SUBTRACT', value, self.math('SUBTRACT', 1, amount)), contrast), .5))
        def breakup(value):
            return self.math('MULTIPLY', value, self.scalar_mix(1, noise(), strength))
        def red(name):
            return self.separate(self.mesh_map(name).color)[0]
        if kind == 'NOISE':
            return noise() if mask.source == 'NOISE' else threshold(noise())
        if kind in {'EDGE_WEAR', 'CHIPS'}:
            value = self.clamp(self.math('MULTIPLY', self.math('MULTIPLY', self.clamp(red('CURVATURE')),
                self.math('MULTIPLY', 2, amount)), contrast))
            value = breakup(value)
            if any(m.kind == 'AO' and m.image for m in self.ts.baked_maps):
                value = self.math('MULTIPLY', value, self.clamp(red('AO')))
            if kind == 'CHIPS':
                chips = self._field(mask, 'VORONOI', generator=True)
                value = self.math('MULTIPLY', value, self.math('ADD', .15,
                    self.math('MULTIPLY', .85, threshold(chips))))
            return self.clamp(value)
        if kind == 'DIRT':
            cavity = self.math('SUBTRACT', 1, red('AO'))
            value = self.math('MULTIPLY', cavity, self.math('ADD', .8,
                self.math('MULTIPLY', .2, self.math('SUBTRACT', 1, red('CURVATURE')))))
            value = self.math('ADD', value, self.math('SUBTRACT', amount, .5))
            value = self.math('ADD', value, self.math('MULTIPLY', self.math('SUBTRACT', noise(), .5), strength))
            return self.clamp(self.math('MULTIPLY', value, contrast))
        if kind in {'CAVITY_DIRT', 'RUST'}:
            cavity = self.clamp(self.math('SUBTRACT', 1, red('AO')))
            if kind == 'CAVITY_DIRT':
                return breakup(self.clamp(self.math('MULTIPLY', self.math('MULTIPLY', cavity,
                    self.math('ADD', 1, self.math('MULTIPLY', amount, 8))), contrast)))
            return breakup(threshold(self.math('ADD', noise(), self.math('MULTIPLY', cavity, .55))))
        if kind in {'DUST', 'WORLD_NORMAL', 'OBJECT_NORMAL'}:
            up = self.separate(self.mesh_map('OBJECT_NORMAL' if kind == 'OBJECT_NORMAL' else 'WORLD_NORMAL').color)[2]
            up = self.clamp(self.math('SUBTRACT', self.math('MULTIPLY', up, 2), 1))
            return breakup(self.clamp(self.math('MULTIPLY', self.math('MULTIPLY', up,
                self.math('MULTIPLY', amount, 2)), contrast)))
        if kind == 'SCRATCHES':
            return self.clamp(self.math('MULTIPLY', self.math('MULTIPLY',
                self._field(mask, 'SCRATCHES', generator=True), self.math('MULTIPLY', amount, 2)), contrast))
        if kind in {'MATERIAL_ID', 'OBJECT_ID', 'UV_ISLAND'}:
            distance = self.vec('DISTANCE', self.mesh_map(kind).color, self.color_param(mask, 'id_color'))
            tolerance = self.math('MAXIMUM', .001, self.math('MULTIPLY', amount, .1))
            return self.math('SUBTRACT', 1, self.math('GREATER_THAN', distance, tolerance))
        if kind == 'POSITION':
            return threshold(self.separate(self.mesh_map('POSITION').color)[2])
        if kind in {'THICKNESS', 'UV_BORDER', 'CURVATURE', 'AO'}:
            source = self.mesh_map(kind)
            value = self.separate(source.color)[0]
            if kind == 'AO':
                value = self.math('SUBTRACT', 1, value)
            return self.math('MULTIPLY', self.clamp(self.math('MULTIPLY', self.math('MULTIPLY', value,
                self.math('MULTIPLY', amount, 2)), contrast)), source.alpha)
        if kind == 'DRIPS':
            # Domain-warped legacy streak hash needs just one cached scalar field;
            # its threshold, amount and cavity mix remain ordinary uniforms.
            def identity():
                return ('drip-field', mask.noise_scale, mask.seed,
                    tuple((m.kind, image_key(m.image)) for m in self.ts.baked_maps if m.kind == 'POSITION'))
            def factory(signature):
                _, scale, seed, _ = signature
                def parts():
                    from ..texture_engine.procedurals import evaluate
                    from .mask_eval import map_pixels
                    from .blend_modes import sample_uv
                    cache = {}
                    u = (np.arange(self.n, dtype=np.float32)[None, :] + .5) / self.n
                    for low in range(0, self.n, 16):
                        v = (np.arange(low, min(low + 16, self.n), dtype=np.float32)[:, None] + .5) / self.n
                        pos = sample_uv(map_pixels(self.ts, 'POSITION', cache), u, v)
                        yield low, evaluate('STREAKS', pos[..., 0] + pos[..., 1] * .731, pos[..., 2], scale=scale, seed=seed)
                return parts()
            with self.role('SourceField:' + mask.path_from_id() + ':DRIPS'):
                streaks = self.separate(self.boundary(self._role, [mask], identity, factory).color)[0]
            pos = self.separate(self.mesh_map('POSITION').color)[2]
            cavity = self.math('SUBTRACT', 1, red('AO'))
            return self.math('MULTIPLY', threshold(streaks), self.clamp(self.math('ADD',
                self.math('MULTIPLY', cavity, 3), self.math('MULTIPLY', amount, self.math('SUBTRACT', 1, pos)))))
        raise ValueError('Unknown generator: ' + kind)

    def _mask_source_boundary(self, layer, mask):
        def identity():
            if mask.source == 'SCENE':
                return ('scene-field', self._scene_identity(mask.influence))
            if mask.source == 'PROCEDURAL':
                return ('legacy-mask-field', mask.procedural, mask.noise_scale, mask.seed,
                        mask.projection, tuple(mask.scale), mask.rotation, tuple(mask.offset),
                        mask.tiling, mask.triplanar_hardness,
                        self._geometry_stamp(mask.projection == 'TRIPLANAR'))
            return ('mask-source', values(mask, excluded={'black', 'white', 'invert', 'strength', 'operation', 'enabled'}),
                    self._geometry_stamp(mask.projection == 'TRIPLANAR' or mask.source == 'SCENE'),
                    tuple((m.kind, image_key(m.image)) for m in self.ts.baked_maps))
        def factory(signature):
            from .snapshots import capture
            snapshot = capture(mask).replaced(black=0., white=1., invert=False, strength=1., enabled=True)
            source_layer = capture(layer).replaced(masks=(snapshot,))
            def parts():
                from .mask_eval import _evaluate_masks
                cache, geometry = {}, {}
                for low in range(0, self.n, 16):
                    yield low, _evaluate_masks(self.ts, source_layer, self.n, low, min(low + 16, self.n), cache, geometry)
            return parts()
        owners = [mask, mask.influence]
        return self.separate(self.boundary(self._role, owners, identity, factory).color)[0]

    def _scene_identity(self, field):
        if field.mode == 'MANUAL':
            from ..generators.scene_fields import signature
            # First analysis publishes this same provenance token. Its derived
            # image allocation must not invalidate a later Levels-only edit.
            return (field.mode, field.update_serial,
                    field.provenance or repr(signature(self.ts, field)))
        if field.mode == 'LIVE':
            from ..generators.scene_fields import signature
            return (field.mode, signature(self.ts, field))
        return (field.mode, image_key(field.image), field.update_serial)

    def _mask_dependencies(self, masks, seen=frozenset()):
        """Exact published source stages used by an isolated mask prefix.

        Traverse authoring inputs rather than the texture-set serial: unrelated
        channels and masks beyond a published stage cannot invalidate this leaf.
        """
        from .dependencies import map_dependencies
        owners, keys = [], []
        for mask in masks:
            if not mask.enabled:
                continue
            owners.extend((mask, mask.effect, mask.influence))
            stamp = values(mask, excluded={'influence'})
            if mask.source == 'SCENE':
                stamp += (('scene', self._scene_identity(mask.influence)),)
            required = map_dependencies(mask)
            stamp += (('maps', tuple((m.kind, image_key(m.image)) for m in self.ts.baked_maps
                                    if required is None or m.kind in required)),)
            if mask.projection == 'TRIPLANAR':
                stamp += (('geometry', self._geometry_stamp(True)),)
            if mask.source == 'REFERENCE':
                target = next((l for l in self.ts.layers if l.id == mask.reference_layer), None)
                if target is None or target.id in seen:
                    raise ValueError('Missing or cyclic published source')
                if mask.reference_channel == 'MASK':
                    prefix = []
                    for item in target.masks:
                        if mask.reference_stage != 'PRE_FILTER' or item.source != 'FILTER':
                            prefix.append(item)
                        if item.id == mask.reference_mask:
                            break
                    nested_owners, nested_keys = self._mask_dependencies(prefix, seen | {target.id})
                    owners.extend(nested_owners)
                    stamp += (('published-mask', nested_keys),)
                else:
                    prop = channel_for(target, mask.reference_channel)
                    if prop:
                        effects = tuple(prop.effects) if mask.reference_stage != 'PRE_FILTER' else ()
                        owners.extend((prop, *effects))
                        stamp += (('published-channel', target.type,
                                   values(prop, excluded={'effects', 'opacity', 'blend_mode'}),
                                   tuple(values(e) for e in effects),
                                   self._geometry_stamp(prop.projection == 'TRIPLANAR')),)
            keys.append(stamp)
        return owners, tuple(keys)

    def _mask_prefix(self, layer, masks):
        # Freeze the selected prefix before the caller continues appending masks.
        masks = tuple(masks)
        owners, _ = self._mask_dependencies(masks[:-1])
        owners.extend((masks[-1], masks[-1].effect))
        def identity():
            return ('mask-filter-prefix', self._mask_dependencies(masks[:-1])[1],
                    values(masks[-1].effect, excluded={'strength'}))
        def factory(signature):
            from .snapshots import capture
            last = capture(masks[-1]).replaced(black=0., white=1., invert=False, strength=1.,
                                              effect=capture(masks[-1].effect).replaced(strength=1.))
            snapshot = capture(layer).replaced(masks=tuple(capture(m) for m in masks[:-1]) + (last,))
            def parts():
                from .mask_eval import evaluate_masks
                cache, geometry = {}, {}
                # Neighborhood operators consume exactly this prefix, never the
                # final material or unrelated layer/channel sources.
                for low in range(0, self.n, 16):
                    yield low, evaluate_masks(self.ts, snapshot, self.n, low,
                                              min(low + 16, self.n), cache, geometry)
            return parts()
        with self.role(f'RasterMaskPrefix:{layer.id}:{masks[-1].id}'):
            return self.separate(self.boundary(self._role, owners, identity, factory).color)[0]

    def mask(self, layer, through='', pre_filter=False):
        key = (layer.id, through, pre_filter)
        if key in self._mask_cache:
            return self._mask_cache[key]
        if key in self._building:
            raise ValueError('Published inputs contain a reference cycle')
        self._building.add(key)
        result = None
        prefix = []
        for mask in layer.masks:
            if mask.enabled and not (pre_filter and mask.source == 'FILTER'):
                prefix.append(mask)
                with self.role(f'MaskSource:{layer.id}:{mask.id}'):
                    if mask.source == 'FILTER':
                        if result is None:
                            result = 1.0
                        if not mask.effect.enabled:
                            value = result
                        else:
                            self._compiled_effects[mask.effect.as_pointer()] = mask.effect
                            try:
                                filtered = apply_effect(self, Signal(result), mask.effect)
                                value = luminance(self, filtered.color)
                            except UnsupportedEffect:
                                filtered = self._mask_prefix(layer, prefix)
                                value = self.scalar_mix(result, filtered, self.param(mask.effect, 'strength'))
                    elif mask.source == 'REFERENCE':
                        target = next((l for l in self.ts.layers if l.id == mask.reference_layer), None)
                        if target is None:
                            raise ValueError('Published source layer is missing')
                        if mask.reference_channel == 'MASK':
                            value = self.mask(target, mask.reference_mask, mask.reference_stage == 'PRE_FILTER')
                        else:
                            sample = self.source(target, mask.reference_channel, mask.reference_stage == 'PRE_FILTER')
                            gray = (luminance(self, sample.color) if mask.reference_channel in {'BASE_COLOR', 'EMISSION', 'NORMAL'}
                                    else self.separate(sample.color)[0])
                            value = self.math('MULTIPLY', gray, sample.alpha)
                    elif mask.source in {'FILL', 'PAINT'}:
                        sample = (self.projected(mask, lambda uv: self.image(mask.image, uv, mask.filtering, mask.tiling,
                                                            getter=lambda mask=mask: mask.image, owner=mask))
                                  if mask.source == 'FILL' else self.image(mask.image, getter=lambda mask=mask: mask.image, owner=mask))
                        value = self.separate(sample.color)[0]
                    elif mask.source == 'BAKED':
                        value = self.separate(self.mesh_map(mask.map_type).color)[0]
                    elif mask.source in {'GENERATOR', 'NOISE'}:
                        value = self.generator(mask)
                    elif mask.source == 'PROCEDURAL':
                        from ..texture_engine.procedurals import ALIASES
                        kind = ALIASES.get(mask.procedural, mask.procedural)
                        if kind in self.ANALYTIC:
                            scale = self.param(mask, 'noise_scale')
                            sample = self.projected(mask, lambda uv: Signal(self.analytic(kind, uv, scale)))
                            value = self.separate(sample.color)[0]
                            if mask.projection == 'UV' and mask.tiling == 'NONE':
                                value = self.math('MULTIPLY', value, self._coverage(self.coordinates(mask)))
                        else:
                            value = self._mask_source_boundary(layer, mask)
                    else:
                        value = self._mask_source_boundary(layer, mask)
                with self.role(f'MaskLevels:{layer.id}:{mask.id}'):
                    black, white = self.param(mask, 'black'), self.param(mask, 'white')
                    value = self.clamp(self.math('DIVIDE', self.math('SUBTRACT', value, black),
                        self.math('MAXIMUM', self.math('SUBTRACT', white, black), 1e-6)))
                    value = self.scalar_mix(value, self.math('SUBTRACT', 1, value), self.param(mask, 'invert'))
                with self.role(f'MaskMix:{layer.id}:{mask.id}'):
                    strength = self.param(mask, 'strength')
                    if result is None:
                        result = self.scalar_mix(1, value, strength)
                    elif mask.source == 'FILTER' or mask.operation == 'NORMAL':
                        result = self.scalar_mix(result, value, strength)
                    elif mask.operation == 'MULTIPLY':
                        result = self.math('MULTIPLY', result, self.scalar_mix(1, value, strength))
                    else:
                        result = self.clamp(self.math('ADD', result, self.math('MULTIPLY', value, strength)))
            if mask.id == through:
                break
        self._building.remove(key)
        result = 1.0 if result is None else result
        self._mask_cache[key] = result
        return result

    def compose(self, parent, channel, initial, ancestors=frozenset()):
        result = initial
        for layer in self.ts.layers:
            if layer.parent_id != parent or not layer.visible:
                continue
            if layer.id in ancestors:
                raise ValueError('Layer hierarchy contains a cycle')
            prop = channel_for(layer, channel)
            if prop and not prop.enabled:
                continue
            mode = prop.blend_mode if prop and prop.blend_mode != 'INHERIT' else layer.blend_mode
            if mode == 'DISABLE':
                continue
            with self.role(f'LayerMix:{layer.id}:{channel}'):
                amount = self.param(layer, 'opacity')
                if prop:
                    amount = self.math('MULTIPLY', amount, self.param(prop, 'opacity'))
                amount = self.math('MULTIPLY', amount, self.mask(layer))
                if layer.type == 'FOLDER' and layer.group_mode == 'PASSTHROUGH':
                    grouped = self.compose(layer.id, channel, result, ancestors | {layer.id})
                    result = Signal(self.mix(result.color, grouped.color, amount),
                                    self.scalar_mix(result.alpha, grouped.alpha, amount))
                    continue
                if layer.type == 'FOLDER':
                    child = self.compose(layer.id, channel, Signal((0., 0., 0.), 0), ancestors | {layer.id})
                    source = Signal(self.vec('SCALE', child.color,
                        self.math('DIVIDE', self.math('GREATER_THAN', child.alpha, 1e-8),
                                  self.math('MAXIMUM', child.alpha, 1e-8))), child.alpha)
                else:
                    source = self.source(layer, channel)
                result = blend(self, result, source, amount, mode, channel)
        return result

    def build(self):
        from ..core.validation import hierarchy_issues
        problems = hierarchy_issues(self.ts)
        if problems:
            raise ValueError(' | '.join(problems))
        self.validate_parameters()
        started = perf_counter()
        self.tree = bpy.data.node_groups.new('OP Procedural · ' + self.ts.name, 'ShaderNodeTree')
        self.tree[OWNER] = True
        self.tree['op_shader_graph'] = self.ts.id
        self.geometry = self.node('ShaderNodeNewGeometry')
        self.uv = self.node('ShaderNodeTexCoord').outputs['UV']
        for channel in CHANNELS:
            self.tree.interface.new_socket(name=channel, in_out='OUTPUT', socket_type='NodeSocketColor')
        output = self.node('NodeGroupOutput')
        output['op_dependency'] = 'OutputInterface'
        for channel in CHANNELS:
            signal = self.compose('', channel, Signal(DEFAULTS[channel][:3]))
            with self.role('Final:' + channel):
                color = signal.color
                if channel == 'NORMAL':
                    vector = self.vec('SUBTRACT', self.vec('SCALE', color, 2), (1., 1., 1.))
                    color = self.vec('SCALE', self.vec('ADD', self.vec('NORMALIZE', vector), (1., 1., 1.)), .5)
                final = self.clamp(color)
                if not hasattr(final, 'node'):
                    final = self.rgb((final,) * 3)
                self.link(final, output.inputs[channel])
                self.channels['Final:' + channel] = channel
        self.instance = self.material.node_tree.nodes.new('ShaderNodeGroup')
        self.instance[OWNER] = True
        self.instance['role'] = 'Procedural Graph'
        self.instance.node_tree = self.tree
        self.instance.label = 'Open Painter · Editable Material'
        self.instance.location = (-400, 200)
        self.outputs = {channel: self.instance.outputs[channel] for channel in CHANNELS}
        for link in self.tree.links:
            a, b = link.from_node.get('op_dependency'), link.to_node.get('op_dependency')
            if a and b and a != b and b != 'OutputInterface':
                self.reverse[a].add(b)
        self._structure = structural_signature(self.ts)
        self._last = {'parameter_changes': [], 'invalidated_nodes': [], 'affected_channels': list(CHANNELS),
                      'updated_bindings': 0, 'raster_boundaries_queued': len(self.resources.queue),
                      'texture_bake_triggered': False, 'submission_ms': (perf_counter() - started) * 1000,
                      'shader_nodes': len(self.tree.nodes), 'pending_resources': self.pending_resources}
        metrics.count('shader_graph_builds')
        from .dependencies import remember, geometry_key
        remember(self.scene, self.ts, geometry_key(self.scene, self.ts))
        return self

    def matches_structure(self):
        return self._structure == structural_signature(self.ts)

    def structural_signature(self):
        return structural_signature(self.ts)

    def update(self, owner=None):
        started = perf_counter()
        if not self.matches_structure():
            raise RebuildRequired('Shader graph topology changed')
        self.validate_parameters()
        pointer = owner.as_pointer() if owner is not None and owner != self.ts else None
        bindings = self.owner_bindings.get(pointer, ()) if pointer is not None else self.bindings.values()
        changed = []
        for binding in bindings:
            value = binding.read()
            if value != binding.value:
                binding.socket.default_value = value
                binding.value = value
                changed.append(binding.identifier)
        invalidated = set(changed)
        queued = 0
        for binding in self.resource_bindings:
            if pointer is not None and pointer not in binding.owners:
                continue
            signature = binding.identity()
            if signature != binding.signature:
                before = len(self.resources.queue)
                image = self.resources.request(signature, lambda b=binding, s=signature: b.factory(s))
                if image.get('op_shader_resource_ready'):
                    binding.node.image, binding.pending_image = image, None
                else:
                    binding.pending_image = image
                queued += len(self.resources.queue) - before
                binding.signature = signature
                invalidated.add(binding.identifier)
        for index, (p, identifier, getter, node, previous) in enumerate(self.image_bindings):
            if pointer is not None and p != pointer:
                continue
            image = getter()
            identity = image_key(image)
            if identity != previous:
                node.image = image
                self.image_bindings[index] = (p, identifier, getter, node, identity)
                invalidated.add(identifier)
        self.resources.used = {(b.pending_image or b.node.image).get(RESOURCE)
                               for b in self.resource_bindings if b.pending_image or b.node.image}
        pending = list(invalidated)
        while pending:
            for downstream in self.reverse.get(pending.pop(), ()):
                if downstream not in invalidated:
                    invalidated.add(downstream)
                    pending.append(downstream)
        channels = sorted({self.channels[node] for node in invalidated if node in self.channels})
        report = {'parameter_changes': changed, 'invalidated_nodes': sorted(invalidated),
                  'affected_channels': channels, 'updated_bindings': len(changed),
                  'raster_boundaries_queued': queued, 'texture_bake_triggered': False,
                  'submission_ms': (perf_counter() - started) * 1000,
                  'pending_resources': self.pending_resources,
                  'gpu_execution_scope': 'Blender evaluates the material shader; per-node execution is not exposed'}
        self._last = report
        metrics.count('shader_parameter_bindings_updated', len(changed))
        metrics.count('shader_parameter_updates', bool(changed))
        metrics.sample('shader_submission_ms', report['submission_ms'])
        return report

    @property
    def pending_resources(self):
        return self.resources.pending

    def step_resources(self, budget=.006):
        result = self.resources.step(budget)
        for binding in self.resource_bindings:
            if binding.pending_image and binding.pending_image.get('op_shader_resource_ready'):
                binding.node.image, binding.pending_image = binding.pending_image, None
        return result

    def close(self):
        try:
            if self.instance:
                self.material.node_tree.nodes.remove(self.instance)
            if self.tree and not self.tree.users:
                bpy.data.node_groups.remove(self.tree)
        except (ReferenceError, RuntimeError):
            pass
        self.resources.close()

    def detach(self):
        """Freeze a usable material on disable/undo without destroying its graph."""
        for entry in self.resources.entries.values():
            if entry.job:
                entry.job.close()
                entry.job = None
        self.resources.queue.clear()

    def validate_parameters(self):
        # Validate before the first socket write, so an invalid slider range
        # cannot partly update a material while reporting a failed transaction.
        # A hidden layer may still publish a source to a visible mask. Validate
        # effects reached by compilation, rather than approximating dependency
        # scope using layer visibility. First-build checks run in apply_effect.
        for effect in self._compiled_effects.values():
            if (effect.enabled and effect.strength > 0
                    and effect.kind == 'NORMAL_COMBINE' and not effect.image):
                raise ValueError('Normal Combine needs a detail normal texture.')
            if (effect.enabled and effect.strength > 0
                    and effect.kind in {'LEVELS', 'ROUGHNESS_REMAP', 'METALLIC_REMAP', 'HEIGHT_REMAP'}
                    and effect.white <= effect.black):
                raise ValueError('White level must be greater than black level')
