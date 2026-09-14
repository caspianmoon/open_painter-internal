"""Signed surface curvature per texel: turning within a radius over area within it.

Edge pairing and the signed dihedral weighting follow Ucupaint 3.0.0
``bake_common.py`` ``fill_curvature_vcol`` (Yusuf Umar and contributors,
GPL-3.0-or-later; see ``docs/research/ucupaint-study.md``). The estimate is
different: every manifold edge carries ``angle x length`` of turning, a quarter
of which belongs to each adjacent face; faces are sampled over their area, and
each texel takes the tent-weighted sum of turning around its world position
divided by the tent-weighted area around it, which is the integral of mean
curvature over the kernel. That ratio is exact for cylinders, identical on
every face of a sphere, insensitive to tessellation density and to sliver
triangles (which contribute little turning and little area), and gives edge
bands of physical width ``radius``. The value is the turning of the surface
across ``2 * radius``: a hard right angle saturates, a bevel reads in
proportion to ``radius`` over its own radius.

The map is relative: the median turning of the covered surface is subtracted,
so a sphere, or the body of a rounded object, is the flat baseline and only
features that bend more than their surroundings register. Turning of less than
``DEAD_ZONE`` above the baseline counts as flat. Hard creases above the crease
angle are left to the crease-line rasterizer when ``exclude_angle`` is given.
"""
import numpy as np

from ...geometry.snapshots import _unit
from ...geometry.uv_rasterizer import tiles as _tiles

DEAD_ZONE = 0.1  # turning across the diameter, in radians (about six degrees)


def face_turning(target, exclude_angle=None):
    """Signed turning per polygon (sum of quarter angle x length; convex > 0) and areas."""
    mesh = target["mesh"]
    positions = np.asarray(target["positions"], np.float64)
    nv, ne, npf, nl = len(mesh.vertices), len(mesh.edges), len(mesh.polygons), len(mesh.loops)
    if nv == 0 or ne == 0 or npf == 0:
        return np.zeros(npf), np.zeros(npf)
    local_normals = np.empty(npf * 3, np.float32)
    mesh.polygons.foreach_get("normal", local_normals)
    world = np.asarray(target["object"].matrix_world, np.float64)
    fn = _unit(local_normals.reshape(-1, 3).astype(np.float64) @ np.linalg.inv(world[:3, :3]))
    lt = np.empty(npf, np.int64)
    mesh.polygons.foreach_get("loop_total", lt)
    ev = np.empty(ne * 2, np.int64)
    mesh.edges.foreach_get("vertices", ev)
    ev = ev.reshape(-1, 2)
    le = np.empty(nl, np.int64)
    mesh.loops.foreach_get("edge_index", le)
    lv = np.empty(nl, np.int64)
    mesh.loops.foreach_get("vertex_index", lv)
    lp = np.repeat(np.arange(npf), lt)
    starts = np.cumsum(lt) - lt
    fc = np.zeros((npf, 3), np.float64)
    np.add.at(fc, lp, positions[lv])
    fc /= lt[:, None]
    # World-space polygon areas from the loop fan around the centroid.
    nxt = np.where(np.arange(nl) + 1 < np.repeat(starts + lt, lt), np.arange(nl) + 1, np.repeat(starts, lt))
    area = np.zeros(npf, np.float64)
    np.add.at(area, lp, .5 * np.linalg.norm(np.cross(positions[lv] - fc[lp], positions[lv[nxt]] - fc[lp]), axis=1))

    # Weld coincident vertices so stitched panels crease instead of reading open.
    key = np.round(positions * 1e5).astype(np.int64)
    uniq, weld = np.unique(key, axis=0, return_inverse=True)
    weld = weld.reshape(-1)
    first = np.zeros(len(uniq), np.int64)
    first[weld[::-1]] = np.arange(nv - 1, -1, -1)
    cow = positions[first]

    epair = np.sort(weld[ev], axis=1)[le]
    order = np.lexsort((epair[:, 1], epair[:, 0]))
    ep_sorted, lp_sorted, le_sorted = epair[order], lp[order], le[order]
    change = np.empty(nl, bool)
    change[0] = True
    change[1:] = (ep_sorted[1:] != ep_sorted[:-1]).any(axis=1)
    run_starts = np.flatnonzero(change)
    lens = np.diff(np.append(run_starts, nl))
    s2 = run_starts[lens == 2]
    f1, f2 = lp_sorted[s2], lp_sorted[s2 + 1]
    valid = f1 != f2
    f1, f2, s2 = f1[valid], f2[valid], s2[valid]
    turning = np.zeros(npf, np.float64)
    if not len(f1):
        return turning, area
    v1, v2 = ep_sorted[s2, 0], ep_sorted[s2, 1]
    angle = np.arccos((fn[f1] * fn[f2]).sum(-1).clip(-1.0, 1.0))
    convex = ((fc[f2] - fc[f1]) * fn[f1]).sum(-1) < 0.0
    w = np.where(convex, angle, -angle) * np.linalg.norm(cow[v1] - cow[v2], axis=1)
    # Edges joined only through the weld are sewn junctions or shading splits:
    # junctions crease inward, so only their concave side counts.
    same_edge = le_sorted[s2] == le_sorted[s2 + 1]
    w = np.where(same_edge | ~convex, w, 0.0)
    if exclude_angle is not None:
        w = np.where(angle >= exclude_angle, 0.0, w)
    np.add.at(turning, f1, .25 * w)
    np.add.at(turning, f2, .25 * w)
    return turning, area


def _samples(target, turning, area, spacing):
    """Point samples over every assigned triangle carrying its share of turning and area."""
    positions = np.asarray(target["positions"], np.float64)
    points, turn_w, area_w = [], [], []
    for tri in target["triangles"]:
        corners = positions[list(tri.vertices)]
        edges = [np.linalg.norm(corners[(k + 1) % 3] - corners[k]) for k in range(3)]
        k = int(np.argmax(edges))
        corners = corners[[k, (k + 1) % 3, (k + 2) % 3]]
        base = max(edges[k], 1e-12)
        tri_area = .5 * np.linalg.norm(np.cross(corners[1] - corners[0], corners[2] - corners[0]))
        height = 2 * tri_area / base
        steps_u = max(int(np.ceil(base / spacing)), 1)
        steps_v = max(int(np.ceil(height / spacing)), 1)
        i, j = np.meshgrid(np.arange(steps_u + 1), np.arange(steps_v + 1))
        u = (i + 1 / 3) / (steps_u + 1)
        v = (j + 1 / 3) / (steps_v + 1)
        keep = u + v < 1
        u, v = u[keep], v[keep]
        count = max(len(u), 1)
        polygon = tri.polygon_index
        share = tri_area / max(area[polygon], 1e-18)  # this triangle's part of its polygon
        points.append(corners[0] + (corners[1] - corners[0]) * u[:, None] + (corners[2] - corners[0]) * v[:, None])
        turn_w.append(np.full(count, turning[polygon] * share / count))
        area_w.append(np.full(count, tri_area / count))
    if not points:
        return np.empty((0, 3)), np.empty(0), np.empty(0)
    return np.concatenate(points), np.concatenate(turn_w), np.concatenate(area_w)


def _grid(points, radius):
    cells = {}
    for index, key in enumerate(map(tuple, np.floor(points / radius).astype(np.int64))):
        cells.setdefault(key, []).append(index)
    return {key: np.asarray(value, np.int64) for key, value in cells.items()}


def _nearby(cells, lo, hi):
    found = [cells[key] for x in range(lo[0], hi[0] + 1) for y in range(lo[1], hi[1] + 1)
             for z in range(lo[2], hi[2] + 1) if (key := (x, y, z)) in cells]
    return np.concatenate(found) if found else np.empty(0, np.int64)


def estimate(target, n, radius, result, covered, exclude_angle=None):
    """Write turning across ``2 * radius`` per texel of this target into ``result``."""
    turning, area = face_turning(target, exclude_angle)
    points, turn_w, area_w = _samples(target, turning, area, radius * .5)
    cells = _grid(points, radius) if len(points) else {}
    positions = np.asarray(target["positions"], np.float64)
    for tri in target["triangles"]:
        uv = target["uv"][list(tri.loops)]
        corners = positions[list(tri.vertices)]
        # Texel size in world units, to cut each tile into blocks about one
        # radius wide so a block only gathers the samples that can reach it.
        uv_area = .5 * abs((uv[1, 0] - uv[0, 0]) * (uv[2, 1] - uv[0, 1]) - (uv[2, 0] - uv[0, 0]) * (uv[1, 1] - uv[0, 1]))
        world_area = .5 * np.linalg.norm(np.cross(corners[1] - corners[0], corners[2] - corners[0]))
        texel = np.sqrt(world_area / max(uv_area, 1e-18)) / n
        block = max(8, int(radius / max(texel, 1e-12)))
        for region, inside, tri_weights, _, _ in _tiles(uv, n):
            covered[region] |= inside
            if not cells:
                continue
            tile = result[region]
            width = inside.shape[1]
            for x0 in range(0, width, block):
                sub = np.zeros_like(inside)
                sub[:, x0:x0 + block] = inside[:, x0:x0 + block]
                p = (tri_weights @ corners)[sub]
                if not len(p):
                    continue
                lo = np.floor((p.min(axis=0) - radius) / radius).astype(np.int64)
                hi = np.floor((p.max(axis=0) + radius) / radius).astype(np.int64)
                nearby = _nearby(cells, lo, hi)
                if not len(nearby):
                    continue
                sample_points, tw, aw = points[nearby], turn_w[nearby], area_w[nearby]
                value = np.zeros(len(p))
                step = max(1, 2_000_000 // len(nearby))
                for start in range(0, len(p), step):
                    chunk = p[start:start + step]
                    kernel = np.clip(1 - np.linalg.norm(chunk[:, None, :] - sample_points[None], axis=-1) / radius, 0, None)
                    numerator, denominator = kernel @ tw, kernel @ aw
                    # 4 R H: a bend of radius r reads 2 R / r; a hard edge saturates.
                    value[start:start + step] = np.where(denominator > 1e-18, 4 * radius * numerator / np.maximum(denominator, 1e-18), 0.0)
                current = tile[sub]
                # Overlapping UVs from several objects keep the stronger feature.
                tile[sub] = np.where(np.abs(value) > np.abs(current), value, current)
    return result


def field(targets, n, radius, exclude_angle=None):
    """Relative signed turning per texel; returns (map, coverage, baseline)."""
    result = np.zeros((n, n), np.float64)
    covered = np.zeros((n, n), bool)
    for target in targets:
        estimate(target, n, radius, result, covered, exclude_angle)
    if not covered.any():
        return result, covered, 0.0
    baseline = float(np.median(result[covered]))
    return np.where(covered, result - baseline, 0.0), covered, baseline


def signed_map(targets, n, radius, crease_angle=None, crease_width=4.0):
    """Return (values in 0..1 with 0.5 flat, coverage, metadata) for a texture set.

    Hard creases above ``crease_angle`` (degrees) are also drawn as signed lines
    of ``crease_width`` texels so a sharp lip on a coarse mesh stays crisp.
    """
    exclude = np.radians(crease_angle) if crease_angle is not None else None
    rel, covered, baseline = field(targets, n, radius, exclude)
    peak = float(np.abs(rel[covered]).max()) if covered.any() else 0.0
    # Stretch only real features so the strongest one saturates.
    stretch = 1.0 / peak if DEAD_ZONE <= peak < 1.0 else 1.0
    values = .5 + .5 * np.clip(rel * stretch, -1, 1)
    if crease_angle is not None:
        from .curvature import rasterize
        for target in targets:
            mesh, world, slots = target["mesh"], target["object"].matrix_world, target["slots"]
            convex = rasterize(mesh, world, slots, n, crease_angle, crease_width)
            concave = rasterize(mesh, world, slots, n, crease_angle, crease_width, concave=True)
            values = np.where(convex > 0, np.maximum(values, .5 + .5 * convex), values)
            values = np.where(concave > 0, np.minimum(values, .5 - .5 * concave), values)
    metadata = dict(mean_curvature_distance=float(radius), mean_curvature_baseline=baseline,
                    mean_curvature_effective_distance=float(radius * stretch))
    return values.astype(np.float32), covered, metadata


def convex_map(targets, n, radius, exclude_angle=None):
    """Convex turning above the dead zone, 0..1, for the CURVATURE edge map."""
    rel, covered, _ = field(targets, n, radius, exclude_angle)
    return np.clip((rel - DEAD_ZONE) / (1.0 - DEAD_ZONE), 0, 1).astype(np.float32)
