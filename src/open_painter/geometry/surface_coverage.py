"""Per-triangle observation quality, coverage classification and camera scoring.

Coverage is measured from actual rasterized observations, not predicted from
normals alone: a triangle that faces a camera but sits behind other geometry is
reported as occluded because its pixels were won by the occluding surface.

Nothing here is object specific. Directions are generic sphere samples plus the
area-weighted directions the under-covered geometry itself points at.
"""
import numpy as np

from . import surface_observation as observation

GOOD, WEAK, MISSING = 'GOOD', 'WEAK', 'MISSING'
GOOD_SCORE = .35
DUPLICATE_COSINE = .86  # a new camera within this angle of an accepted one adds little


def triangle_normals(positions, triangles):
    """Geometric face normals and world areas of every triangle."""
    p = np.asarray(positions, np.float64)[np.asarray(triangles, np.int64)]
    cross = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    length = np.linalg.norm(cross, axis=1)
    normals = cross / np.maximum(length, 1e-20)[:, None]
    return normals, length * .5


def observe(buffers, silhouette, *, count, normals, areas, axis, pixel_world, limit=8):
    """Aggregate one observation's pixels onto the triangles it actually showed.

    ``axis`` is the observation's camera direction; ``pixel_world`` the world size
    of one pixel. Returns per-triangle visibility, view angle, projected pixel area,
    silhouette distance, occlusion state and a combined observation score.
    """
    triangle = np.asarray(buffers['triangle']).reshape(-1)
    seen = triangle >= 0
    index = triangle[seen]
    pixels = np.bincount(index, minlength=count).astype(np.float64)
    edge = np.bincount(index, weights=np.asarray(silhouette, np.float64).reshape(-1)[seen], minlength=count)
    facing = np.abs(np.asarray(normals, np.float64) @ np.asarray(axis, np.float64))
    # The area a camera would see with nothing in the way; the shortfall is occlusion.
    expected = np.asarray(areas, np.float64) * facing / max(pixel_world * pixel_world, 1e-30)
    visible = pixels > 0
    occlusion = np.where(visible, np.clip(1 - pixels / np.maximum(expected, 1e-9), 0, 1), 1.)
    silhouette_confidence = np.where(visible, np.clip(edge / np.maximum(pixels, 1) / max(limit, 1), 0, 1), 0.)
    quality = np.where(facing >= observation.EDGE_ON, facing, 0.)
    score = quality * (1 - occlusion) * silhouette_confidence * visible
    return dict(visible=visible, facing=facing, pixels=pixels, occlusion=occlusion,
                silhouette=silhouette_confidence, score=score,
                surface_per_pixel=pixel_world * pixel_world / np.maximum(quality, 1e-6))


def accumulate(records, identifiers, *, count):
    """Fold per-observation triangle evidence into one coverage map.

    Each covered triangle keeps its observation count, best view angle, best
    projected resolution, best confidence and the observations that produced them.
    """
    best = np.zeros(count)
    best_facing = np.zeros(count)
    best_resolution = np.full(count, np.inf)
    observations = np.zeros(count, np.int32)
    winner = np.full(count, -1, np.int32)
    occluded = np.zeros(count, np.int32)
    for position, record in enumerate(records):
        observations += record['visible'].astype(np.int32)
        occluded += ((record['occlusion'] > .5) & record['visible']).astype(np.int32)
        improved = record['score'] > best
        winner[improved] = position
        best = np.where(improved, record['score'], best)
        best_facing = np.maximum(best_facing, np.where(record['visible'], record['facing'], 0.))
        best_resolution = np.minimum(best_resolution, np.where(record['score'] > 0, record['surface_per_pixel'], np.inf))
    state = np.where(best >= GOOD_SCORE, GOOD, np.where(observations > 0, WEAK, MISSING))
    return dict(best_confidence=best, best_view_angle=best_facing, observation_count=observations,
                best_surface_per_pixel=best_resolution, occluded_observations=occluded,
                source=np.array([identifiers[i] if i >= 0 else '' for i in winner], object),
                state=state)


def summary(coverage, areas, *, labels=None):
    """Area-weighted coverage classification, optionally split by region label."""
    areas = np.asarray(areas, np.float64)
    total = float(areas.sum())

    def share(selected):
        chosen = areas[selected]
        return dict(triangles=int(selected.sum()), area_fraction=float(chosen.sum() / total) if total else 0.)

    sources = {}
    for identifier in sorted({str(value) for value in coverage['source'] if value}):
        chosen = coverage['source'] == identifier
        sources[identifier] = dict(share(chosen),
            mean_view_angle=float(np.average(coverage['best_view_angle'][chosen], weights=areas[chosen]))
            if chosen.any() else 0.)
    result = dict(total_triangles=int(len(areas)), total_area=total,
                  classes={key: share(coverage['state'] == key) for key in (GOOD, WEAK, MISSING)},
                  mean_observations=float(np.average(coverage['observation_count'], weights=areas)) if total else 0.,
                  best_source_observations=sources,
                  occluded_observations=int(coverage['occluded_observations'].sum()),
                  meaning='Area-weighted observation quality of the evaluated surface; not an appearance score. '
                          'best_source_observations names the observation that currently supplies each share of '
                          'the surface, with its view angle')
    if labels is not None:
        groups = {}
        for label in sorted({str(value) for value in labels}):
            chosen = np.asarray(labels) == label
            groups[label] = {key: share(chosen & (coverage['state'] == key)) for key in (GOOD, WEAK, MISSING)}
        result['regions'] = groups
    return result


def sphere_directions(count=42):
    """Deterministic near-uniform directions; a generic search set, not a pose guess."""
    count = max(6, int(count))
    index = np.arange(count) + .5
    z = 1 - 2 * index / count
    radius = np.sqrt(np.maximum(0., 1 - z * z))
    angle = index * np.pi * (3 - np.sqrt(5.))
    return np.column_stack((np.cos(angle) * radius, np.sin(angle) * radius, z))


def candidate_directions(normals, areas, needed, *, count=42):
    """Generic sphere samples plus the directions the under-covered surface faces."""
    directions = [sphere_directions(count)]
    if needed.any():
        weighted = (np.asarray(normals, np.float64)[needed] * np.asarray(areas, np.float64)[needed, None])
        total = weighted.sum(axis=0)
        if np.linalg.norm(total) > 1e-9:
            directions.append((total / np.linalg.norm(total))[None])
        # The strongest individual contributors, so a small critical part is not averaged away.
        order = np.argsort(-np.asarray(areas)[needed])[:6]
        directions.append(np.asarray(normals, np.float64)[needed][order])
    stacked = np.concatenate(directions)
    lengths = np.linalg.norm(stacked, axis=1)
    return stacked[lengths > 1e-9] / lengths[lengths > 1e-9, None]


def rank_directions(normals, areas, needed, existing, *, count=42, limit=12):
    """Order candidate directions by the under-covered area each would face.

    This is the cheap pre-pass: the caller rasterizes only the best few, so the
    accepted camera is chosen on measured occlusion rather than on normals alone.
    """
    normals = np.asarray(normals, np.float64)
    areas = np.asarray(areas, np.float64)
    needed = np.asarray(needed, bool)
    if not needed.any():
        return []
    existing = [np.asarray(item, np.float64) for item in existing]
    ranked = []
    for direction in candidate_directions(normals, areas, needed, count=count):
        facing = normals[needed] @ direction
        gain = float((areas[needed] * np.clip(facing, 0, 1) ** 1.5).sum())
        duplicate = max((float(direction @ other) for other in existing), default=-1.)
        if duplicate > DUPLICATE_COSINE:
            gain *= max(0., (1 - duplicate) / (1 - DUPLICATE_COSINE))
        if gain > 0:
            ranked.append((gain, direction))
    ranked.sort(key=lambda item: -item[0])
    return ranked[:limit]


def up_for(direction):
    """A stable up vector for a viewing direction; world Z unless that is parallel."""
    direction = np.asarray(direction, np.float64)
    up = np.array([0., 0., 1.])
    if abs(float(direction @ up)) > .97:
        up = np.array([0., 1., 0.])
    return up
