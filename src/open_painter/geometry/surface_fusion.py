"""Reprojection of observation appearance onto the UV atlas, and its fusion.

Fusion gathers in UV space rather than splatting in screen space: every atlas
texel of the assigned surface resolves to its own world position and normal, is
projected back into each observation, and takes appearance only from observations
that actually saw that point. A texel therefore never receives appearance from a
surface the observation could not see, and no texel is left unwritten merely
because an observation was coarser than the atlas.

All channels of one field share these weights, so generated Base Color, Roughness,
Height and Metallic cannot drift apart. Nothing here reads Blender data.
"""
import numpy as np

from . import surface_observation as observation
from .uv_rasterizer import interpolate, rasterize

GOOD, WEAK, MISSING, CONFLICTED = 'GOOD', 'WEAK', 'MISSING', 'CONFLICTED'
GOOD_WEIGHT = .3
WEAK_WEIGHT = .02
CONFLICT_DEVIATION = .18
SILHOUETTE_RAMP = 3  # pixels from a projected boundary before a sample is fully trusted
CHUNK = 1 << 18


def texels(uv, positions, triangles, corner_normals, resolution, *, chosen=None):
    """Atlas texels owned by the surface: world position, normal and triangle.

    ``chosen`` optionally restricts the reprojection target to a region's triangles;
    texels outside it are simply not produced, so a bounded generation cannot write
    onto geometry the artist did not select.
    """
    uv = np.asarray(uv, np.float32)
    index = np.arange(len(uv)) if chosen is None else np.flatnonzero(np.asarray(chosen, bool))
    if not len(index):
        raise ValueError('The reprojection target has no assigned triangles')
    rows, cols, local, weights = rasterize(uv[index], int(resolution))
    if not len(rows):
        raise ValueError('The reprojection target covers no atlas texels; check its render UVs')
    triangle = index[local]
    corners = np.asarray(positions, np.float64)[np.asarray(triangles, np.int64)[triangle]]
    normals = interpolate(weights, np.asarray(corner_normals, np.float32)[triangle])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    return dict(rows=rows, cols=cols, triangle=triangle, resolution=int(resolution),
                position=interpolate(weights, corners.astype(np.float32)), normal=normals)


def texel_world_size(texels, positions, triangles, uv):
    """Median world size of one atlas texel, used as the fusion sampling reference."""
    triangle = np.unique(texels['triangle'])
    p = np.asarray(positions, np.float64)[np.asarray(triangles, np.int64)[triangle]]
    t = np.asarray(uv, np.float64)[triangle]
    world = np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1) * .5
    first, second = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    atlas = np.abs(first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]) * .5
    usable = atlas > 1e-14
    if not usable.any():
        return 1.
    ratio = np.sqrt(world[usable] / atlas[usable]) / texels['resolution']
    return float(np.median(ratio))


def weights_for(view, position, normal, *, texel_world):
    """Contribution weight and sampling coordinates of one observation per texel.

    Facing quality falls off as the surface turns edge-on, silhouette confidence
    falls off at projected boundaries and self-occlusion cliffs, and an observation
    that did not see the point contributes exactly zero.
    """
    coords, depth = observation.project(view['matrix'], view['projection'], position)
    inside = np.all((coords >= 0) & (coords < 1), axis=1)
    rows, cols = observation.nearest(view['depth'], coords)
    stored = view['depth'][rows, cols]
    tolerance = max(float(view['pixel_world']) * 2, float(view.get('depth_tolerance', 0.)), 1e-7)
    visible = inside & np.isfinite(stored) & (np.abs(depth - stored) <= tolerance) & view['target'][rows, cols]
    facing = np.abs(normal @ np.asarray(view['axis'], np.float32))
    facing = np.where(facing >= observation.EDGE_ON, facing, 0.)
    silhouette = view['silhouette'][rows, cols] / max(float(view.get('silhouette_limit', SILHOUETTE_RAMP)), 1)
    # Foreshortening stretches a pixel along the surface; compare sampling lengths,
    # so an observation half the atlas resolution is half as good, not a quarter.
    sample_length = float(view['pixel_world']) / np.maximum(np.sqrt(facing), 1e-3)
    resolution = np.clip(texel_world / np.maximum(sample_length, 1e-30), 0, 1)
    weight = np.where(visible, facing * facing * np.clip(silhouette, 0, 1) * resolution, 0.)
    return weight.astype(np.float32), coords


def fuse(texels, views, images, *, texel_world, chunk=CHUNK):
    """Confidence-weighted fusion of every observation's appearance into UV space.

    ``images`` maps a channel to one array per observation (or None where that
    observation generated no such channel). Returns fused values, the accumulated
    weight, the observation count and a weighted deviation that exposes observations
    that disagree about the same surface point.
    """
    count = len(texels['rows'])
    channels = {key: next((np.asarray(item).shape[2] for item in value if item is not None), 0)
                for key, value in images.items()}
    if any(width == 0 for width in channels.values()):
        raise ValueError('Every fused channel needs at least one observation image')
    result = {key: np.zeros((count, width), np.float32) for key, width in channels.items()}
    deviation = {key: np.zeros(count, np.float32) for key in channels}
    total = np.zeros(count, np.float32)
    best = np.zeros(count, np.float32)
    observations = np.zeros(count, np.int32)
    for start in range(0, count, chunk):
        stop = min(start + chunk, count)
        position = texels['position'][start:stop]
        normal = texels['normal'][start:stop]
        block = {key: np.zeros((stop - start, width), np.float32) for key, width in channels.items()}
        squared = {key: np.zeros(stop - start, np.float32) for key in channels}
        accumulated = np.zeros(stop - start, np.float32)
        for index, view in enumerate(views):
            weight, coords = weights_for(view, position, normal, texel_world=texel_world)
            active = weight > 0
            if not active.any():
                continue
            accumulated += weight
            observations[start:stop] += active
            best[start:stop] = np.maximum(best[start:stop], weight)
            for key, sources in images.items():
                source = sources[index]
                if source is None:
                    continue
                sampled = observation.sample(source, coords[active])
                block[key][active] += weight[active, None] * sampled
                squared[key][active] += weight[active] * (sampled * sampled).mean(axis=1)
        total[start:stop] = accumulated
        safe = np.maximum(accumulated, 1e-12)[:, None]
        for key in channels:
            mean = block[key] / safe
            result[key][start:stop] = mean
            variance = squared[key] / safe[:, 0] - (mean * mean).mean(axis=1)
            deviation[key][start:stop] = np.sqrt(np.maximum(variance, 0.))
    return dict(values=result, weight=total, observations=observations, best_weight=best,
                deviation=deviation)


def classify(fused, *, good=GOOD_WEIGHT, weak=WEAK_WEIGHT, conflict=CONFLICT_DEVIATION):
    """Atlas coverage state after fusion, including observations that disagree."""
    weight, best = fused['weight'], fused['best_weight']
    worst = np.zeros(len(weight), np.float32)
    for values in fused['deviation'].values():
        worst = np.maximum(worst, values)
    state = np.where(best >= good, GOOD, np.where(weight > weak, WEAK, MISSING))
    return np.where((state != MISSING) & (worst >= conflict), CONFLICTED, state)


def report(state, *, resolution):
    """Counts and fractions of every coverage class over the assigned surface."""
    total = int(len(state))
    counts = {key: int((state == key).sum()) for key in (GOOD, WEAK, MISSING, CONFLICTED)}
    return dict(resolution=int(resolution), surface_texels=total,
                classes={key: dict(texels=value, fraction=float(value / total) if total else 0.)
                         for key, value in counts.items()},
                meaning='Fused atlas coverage over texels that own assigned surface; '
                        'MISSING means no observation saw that surface, not a dark texture')


def write(atlas, texels, values):
    """Scatter fused per-texel values into a square atlas image."""
    atlas[texels['rows'], texels['cols']] = values
    return atlas


def hole_clusters(texels, state, *, limit=8, minimum=16):
    """Group uncovered texels into world-space clusters, largest first.

    The clusters are the actual geometry that still needs an observation; they are
    reported in world coordinates with an outward direction, never filled in by
    stretching neighbouring pixels.
    """
    chosen = np.flatnonzero((state == MISSING) | (state == WEAK))
    if not len(chosen):
        return []
    position = texels['position'][chosen]
    normal = texels['normal'][chosen]
    lower, upper = position.min(axis=0), position.max(axis=0)
    span = float(np.max(upper - lower))
    if span <= 0:
        cells = np.zeros((len(chosen), 3), np.int64)
    else:
        cells = np.floor((position - lower) / (span / 6 + 1e-12)).astype(np.int64)
    keys, groups = np.unique(cells, axis=0, return_inverse=True)
    groups = np.asarray(groups).reshape(-1)
    clusters = []
    for index in range(len(keys)):
        members = groups == index
        if members.sum() < minimum:
            continue
        points = position[members]
        direction = normal[members].mean(axis=0)
        length = float(np.linalg.norm(direction))
        clusters.append(dict(texels=int(members.sum()),
            bounds_min=points.min(axis=0).astype(float).tolist(),
            bounds_max=points.max(axis=0).astype(float).tolist(),
            center=points.mean(axis=0).astype(float).tolist(),
            outward_direction=(direction / length).astype(float).tolist() if length > 1e-9 else [0., 0., 1.],
            states=dict(MISSING=int((state[chosen][members] == MISSING).sum()),
                        WEAK=int((state[chosen][members] == WEAK).sum()))))
    clusters.sort(key=lambda item: -item['texels'])
    return clusters[:limit]
