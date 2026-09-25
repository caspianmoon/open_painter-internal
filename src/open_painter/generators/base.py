"""Mesh-map-driven grayscale texture generators. No material node evaluation."""
from dataclasses import dataclass

import numpy as np

from ..evaluation.blend_modes import sample_uv
from ..evaluation.mask_eval import map_pixels
from ..texture_engine.procedurals import evaluate as procedural
from .dirt import dirt
from .edge_wear import edge_wear
from .noise import noise
from . import micro_details

REQUIRED_MAPS = {
    'NOISE': (), 'EDGE_WEAR': ('CURVATURE',), 'DIRT': ('AO', 'CURVATURE'),
    'DUST': ('WORLD_NORMAL',), 'CAVITY_DIRT': ('AO',), 'RUST': ('AO',),
    'CHIPS': ('CURVATURE',), 'SCRATCHES': (), 'DRIPS': ('POSITION', 'AO'),
    'RUNOFF': ('POSITION', 'AO'),
    'WORLD_NORMAL': ('WORLD_NORMAL',), 'POSITION': ('POSITION',),
    'THICKNESS': ('THICKNESS',), 'OBJECT_NORMAL': ('OBJECT_NORMAL',),
    'MATERIAL_ID': ('MATERIAL_ID',), 'OBJECT_ID': ('OBJECT_ID',),
    'UV_ISLAND': ('UV_ISLAND',), 'UV_BORDER': ('UV_BORDER',),
    'CURVATURE': ('CURVATURE',), 'AO': ('AO',),
}


EDGE_MAP_KINDS = ('CURVATURE_THIN', 'CURVATURE', 'CURVATURE_WIDE', 'CURVATURE_XWIDE')


def edge_curvature(texture_set, parameters, mesh_map):
    """Curvature at the mask's Edge Size: a live blend across the baked edge-map set.

    Edge Size 0..3 interpolates thin -> normal -> wide -> extreme. Without the
    companion maps (older projects) the single CURVATURE map is used as is.
    """
    if not all(any(m.kind == kind and m.image for m in texture_set.baked_maps) for kind in EDGE_MAP_KINDS):
        return mesh_map('CURVATURE')[..., 0]
    size = float(getattr(parameters, 'edge_size', 1.0))
    maps = [mesh_map(kind)[..., 0] for kind in EDGE_MAP_KINDS]
    value = maps[0]
    for index, wider in enumerate(maps[1:]):
        t = np.float32(min(max(size - index, 0.0), 1.0))
        value = value + (wider - value) * t
    return value


def required_maps(kind):
    if kind not in REQUIRED_MAPS:
        raise ValueError(f'Unknown generator: {kind}')
    return set(REQUIRED_MAPS[kind])


def missing_maps(ts, kinds):
    available = {m.kind for m in ts.baked_maps if m.image}
    return set().union(*(required_maps(k) for k in kinds)) - available


@dataclass(frozen=True)
class Generator:
    id: str
    name: str

    def evaluate(self, texture_set, parameters, u, v, cache, resources=None, rows=None):
        n = None
        amount, contrast, strength = parameters.amount, parameters.contrast, parameters.noise_strength
        # Anchored micro details need row coordinates; legacy callers stay bit-identical.
        micro = (micro_details.evaluate(texture_set, parameters, rows, cache, resources)
                 if rows is not None and self.id in micro_details.KINDS and micro_details.anchors(parameters)
                 else None)

        def breakup_noise():
            nonlocal n
            if n is None:
                n = (noise(u, v, parameters.noise_scale, parameters.seed)
                     if parameters.source == 'NOISE' else
                     procedural('FBM', u, v, scale=parameters.noise_scale, seed=parameters.seed))
            return n

        def mesh_map(kind):
            return sample_uv(map_pixels(texture_set, kind, cache), u, v)

        def threshold(value):
            return np.clip((value - (1 - amount)) * contrast + .5, 0, 1)

        def breakup(value):
            # Breakup can reduce eligible coverage, never invent new geometric edges.
            return value * ((1 - strength) + strength * breakup_noise())

        if self.id == 'NOISE':
            n = breakup_noise()
            return n if parameters.source == 'NOISE' else threshold(n)
        if self.id in {'EDGE_WEAR', 'CHIPS'}:
            ao = mesh_map('AO')[..., 0] if any(m.kind == 'AO' and m.image for m in texture_set.baked_maps) else None
            if getattr(parameters, 'edge_source', 'BAKED') == 'LIVE':
                from .scene_fields import evaluate as live_field
                curvature = live_field(texture_set, parameters, u, v, cache)
            else:
                curvature = edge_curvature(texture_set, parameters, mesh_map)
            edges = edge_wear(curvature, ao, breakup_noise(), amount,
                              contrast, strength, getattr(parameters, 'edge_width', 1.0),
                              None if micro is None else micro[0])
            if self.id == 'CHIPS':
                chips = procedural('VORONOI', u, v, seed=parameters.seed, scale=parameters.noise_scale)
                edges *= .15 + .85 * threshold(chips)
            return edges
        if self.id == 'DIRT':
            ao, curvature = mesh_map('AO')[..., 0], mesh_map('CURVATURE')[..., 0]
            if micro is not None:
                ao, curvature = np.minimum(ao, 1 - micro[1]), np.maximum(curvature, micro[0])
            return dirt(ao, curvature, breakup_noise(), amount, contrast, strength)
        if self.id in {'CAVITY_DIRT', 'RUST'}:
            cavity = np.clip(1 - mesh_map('AO')[..., 0], 0, 1)
            if micro is not None:
                cavity = np.maximum(cavity, micro[1])
            if self.id == 'CAVITY_DIRT':
                return breakup(np.clip(cavity * (1 + amount * 8) * contrast, 0, 1))
            # Corrosion starts in occluded regions and grows onto exposed surfaces.
            growth = threshold(breakup_noise() + cavity * .55)
            return breakup(growth)
        if self.id in {'DUST', 'WORLD_NORMAL', 'OBJECT_NORMAL'}:
            kind = 'OBJECT_NORMAL' if self.id == 'OBJECT_NORMAL' else 'WORLD_NORMAL'
            up = np.clip(mesh_map(kind)[..., 2] * 2 - 1, 0, 1)
            return breakup(np.clip(up * (amount * 2) * contrast, 0, 1))
        if self.id == 'SCRATCHES':
            scratches = procedural('SCRATCHES', u, v, seed=parameters.seed, scale=parameters.noise_scale)
            return np.clip(scratches * amount * 2 * contrast, 0, 1)
        if self.id == 'RUNOFF':
            pos = mesh_map('POSITION')
            cavity = np.clip(1 - mesh_map('AO')[..., 0], 0, 1)
            trails = procedural('RUNOFF_TRAILS', pos[..., 0] + pos[..., 1] * .731,
                                pos[..., 2], seed=parameters.seed, scale=parameters.noise_scale)
            # Geometry biases deposition without creating stripes on clean faces.
            return np.clip(trails * (amount * 2) * contrast * (.3 + .7 * cavity), 0, 1)
        if self.id == 'DRIPS':
            pos = mesh_map('POSITION')
            cavity = 1 - mesh_map('AO')[..., 0]
            if micro is not None:
                cavity = np.maximum(cavity, micro[1])
            streaks = procedural('STREAKS', pos[..., 0] + pos[..., 1] * .731, pos[..., 2], seed=parameters.seed, scale=parameters.noise_scale)
            return threshold(streaks) * np.clip(cavity * 3 + amount * (1 - pos[..., 2]), 0, 1)
        if self.id in {'MATERIAL_ID', 'OBJECT_ID', 'UV_ISLAND'}:
            colors = mesh_map(self.id)[..., :3]
            target = np.asarray(getattr(parameters, 'id_color', (1, 1, 1)))
            return (np.linalg.norm(colors - target, axis=-1) <= max(.001, amount * .1)).astype(np.float32)
        if self.id == 'POSITION':
            return threshold(mesh_map('POSITION')[..., 2])
        if self.id in {'THICKNESS', 'UV_BORDER', 'CURVATURE', 'AO'}:
            values = mesh_map(self.id)
            result = values[..., 0]
            if self.id == 'AO':
                result = 1 - result
            return np.clip(result * amount * 2 * contrast, 0, 1) * values[..., 3]
        raise ValueError(f'Unknown generator: {self.id}')


GENERATORS = {key: Generator(key, key.replace('_', ' ').title()) for key in REQUIRED_MAPS}


def evaluate(ts, mask, u, v, cache, resources=None, rows=None):
    return GENERATORS['NOISE' if mask.source == 'NOISE' else mask.generator].evaluate(
        ts, mask, u, v, cache, resources=resources, rows=rows)
