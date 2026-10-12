"""Signed surface curvature per texel: turning within a radius over area within it.

Edge pairing and the signed dihedral weighting follow Ucupaint (3.0.0)
``bake_common.py`` ``fill_curvature_vcol`` (
GPL-3.0-or-later; see ``docs/research/node-stack-study.md``). The estimate is
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
    # inverted_safe, as every other normal transform: a zero-scaled axis is a
    # singular matrix, which np.linalg.inv refuses.
    normal_matrix = np.asarray(target["object"].matrix_world.to_3x3().inverted_safe().transposed(), np.float64)
    fn = _unit(local_normals.reshape(-1, 3).astype(np.float64) @ normal_matrix.T)
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
    """Point samples over every assigned triangle carrying its share of turning and area.

    Triangles are grouped by their (steps_u, steps_v) lattice so each group is
    sampled in one array operation; the placement rule is unchanged.
    """
    positions = np.asarray(target["positions"], np.float64)
    tri_vertices, tri_polygons = target["tri_vertices"], target["tri_polygons"]
    if not len(tri_vertices):
        return np.empty((0, 3)), np.empty(0), np.empty(0)
    corners = positions[tri_vertices]                                    # (T, 3, 3)
    edges = np.linalg.norm(np.roll(corners, -1, axis=1) - corners, axis=2)  # edge k: corner k -> k+1
    k = np.argmax(edges, axis=1)
    rotated = np.stack([corners[np.arange(len(corners)), (k + shift) % 3] for shift in range(3)], axis=1)
    base = np.maximum(edges[np.arange(len(edges)), k], 1e-12)
    tri_area = .5 * np.linalg.norm(np.cross(rotated[:, 1] - rotated[:, 0], rotated[:, 2] - rotated[:, 0]), axis=1)
    height = 2 * tri_area / base
    steps_u = np.maximum(np.ceil(base / spacing).astype(np.int64), 1)
    steps_v = np.maximum(np.ceil(height / spacing).astype(np.int64), 1)
    share = tri_area / np.maximum(area[tri_polygons], 1e-18)
    turn = turning[tri_polygons] * share
    lattice = steps_u * (steps_v.max() + 1) + steps_v
    points, turn_w, area_w = [], [], []
    for code in np.unique(lattice):
        chosen = np.flatnonzero(lattice == code)
        su, sv = int(steps_u[chosen[0]]), int(steps_v[chosen[0]])
        i, j = np.meshgrid(np.arange(su + 1), np.arange(sv + 1))
        u = (i + 1 / 3) / (su + 1)
        v = (j + 1 / 3) / (sv + 1)
        keep = u + v < 1
        u, v = u[keep], v[keep]
        count = max(len(u), 1)
        c0, c1, c2 = rotated[chosen, 0], rotated[chosen, 1], rotated[chosen, 2]
        sampled = (c0[:, None, :] + (c1 - c0)[:, None, :] * u[None, :, None]
                   + (c2 - c0)[:, None, :] * v[None, :, None])
        points.append(sampled.reshape(-1, 3))
        turn_w.append(np.repeat(turn[chosen] / count, len(u)))
        area_w.append(np.repeat(tri_area[chosen] / count, len(u)))
    return np.concatenate(points), np.concatenate(turn_w), np.concatenate(area_w)


_OFFSETS = np.array([(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)], np.int64)


def _encode(cells, origin, span):
    shifted = cells - origin
    return (shifted[:, 0] * span[1] + shifted[:, 1]) * span[2] + shifted[:, 2]


def prepare_raster(target, n, exclude_angle=None):
    """Read the radius-independent part of a target: face turning and areas plus
    the rasterised texels and their world positions.

    Runs inside the ``targets`` context; the returned object holds no RNA
    references. Every reach of the edge map set shares one of these (the 4k
    raster alone is hundreds of megabytes), only the samples differ.
    """
    from types import SimpleNamespace
    from ...geometry.uv_rasterizer import interpolate, rasterize
    turning, area = face_turning(target, exclude_angle)
    rows, cols, tri, weights = rasterize(target["uv"][target["tri_loops"]], n, unique=False)
    positions = np.asarray(target["positions"], np.float64)
    texels = (interpolate(weights, positions[target["tri_vertices"][tri]]) if len(rows)
              else np.empty((0, 3)))
    return SimpleNamespace(turning=turning, area=area, rows=rows, cols=cols, texels=texels)


def prepare_reach(target, raster, radius):
    """Samples for one radius over a shared raster; the arrays are referenced, not copied."""
    from types import SimpleNamespace
    points, turn_w, area_w = _samples(target, raster.turning, raster.area, radius * .5)
    return SimpleNamespace(rows=raster.rows, cols=raster.cols, texels=raster.texels,
                           points=points, turn_w=turn_w, area_w=area_w)


def prepare(target, n, radius, exclude_angle=None):
    """Read everything the kernel needs from the evaluated mesh into plain arrays.

    Runs inside the ``targets`` context; the returned object holds no RNA
    references, so the accumulation can proceed across cooperative steps after
    the evaluated meshes are released.
    """
    return prepare_reach(target, prepare_raster(target, n, exclude_angle), radius)


# Pair work per kernel evaluation: texels of a cell against nearby samples. Both
# grow with radius^2 (a dense 4k asset at the XWIDE reach holds tens of
# thousands of each per cell), so the pair matrix is sliced to a fixed size.
TEXEL_CHUNK = 1024
SAMPLE_CHUNK = 4096


def accumulate(prepared, n, radius, result, covered):
    """Write turning across ``2 * radius`` per texel of one target into ``result``.

    Texels are grouped by grid cell (size ``radius``); each group evaluates the
    tent kernel against the samples of its 27 surrounding cells in one block,
    so samples beyond the kernel contribute nothing, exactly as the per-block
    search did, while the Python cost is per cell rather than per triangle.
    Yields its progress (0..1) between groups so a modal job can stay responsive.
    """
    rows, cols, texels = prepared.rows, prepared.cols, prepared.texels
    points, turn_w, area_w = prepared.points, prepared.turn_w, prepared.area_w
    if not len(rows):
        return result
    covered[rows, cols] = True
    if not len(points):
        return result
    cells = np.floor(points / radius).astype(np.int64)
    origin = cells.min(axis=0) - 2
    span = cells.max(axis=0) - origin + 3
    order = np.argsort(_encode(cells, origin, span), kind="stable")
    sorted_keys = _encode(cells, origin, span)[order]
    sorted_points, sorted_turn, sorted_area = points[order], turn_w[order], area_w[order]
    unique_keys, starts, counts = np.unique(sorted_keys, return_index=True, return_counts=True)
    texel_cells = np.floor(texels / radius).astype(np.int64)
    inside = np.all((texel_cells >= origin) & (texel_cells < origin + span), axis=1)
    texel_keys = np.where(inside, _encode(np.where(inside[:, None], texel_cells, origin), origin, span), -1)
    texel_order = np.argsort(texel_keys, kind="stable")
    sorted_texels = texels[texel_order]
    group_keys, group_starts, group_counts = np.unique(texel_keys[texel_order], return_index=True, return_counts=True)
    # Neighbour sample ranges of every texel cell, resolved for all groups at once.
    group_cells = np.stack(((group_keys // span[2]) // span[1], (group_keys // span[2]) % span[1], group_keys % span[2]), axis=1) + origin
    neighbour_starts = np.zeros((len(group_keys), len(_OFFSETS)), np.int64)
    neighbour_counts = np.zeros((len(group_keys), len(_OFFSETS)), np.int64)
    for column, offset in enumerate(_OFFSETS):
        neighbour = group_cells + offset
        outside = np.any((neighbour < origin) | (neighbour >= origin + span), axis=1)
        code = _encode(np.where(outside[:, None], origin, neighbour), origin, span)
        slot = np.minimum(np.searchsorted(unique_keys, code), len(unique_keys) - 1)
        hit = (unique_keys[slot] == code) & ~outside & (group_keys >= 0)
        neighbour_starts[hit, column] = starts[slot[hit]]
        neighbour_counts[hit, column] = counts[slot[hit]]
    value = np.zeros(len(texels))
    from time import perf_counter
    last_yield = perf_counter()
    for group in range(len(group_keys)):
        if perf_counter() - last_yield > .03:
            yield group / len(group_keys)
            last_yield = perf_counter()
        if group_keys[group] < 0:
            continue
        pieces = [(int(s), int(c)) for s, c in zip(neighbour_starts[group], neighbour_counts[group]) if c]
        if not pieces:
            continue
        gathered = np.concatenate([sorted_points[s:s + c] for s, c in pieces])
        tw = np.concatenate([sorted_turn[s:s + c] for s, c in pieces])
        aw = np.concatenate([sorted_area[s:s + c] for s, c in pieces])
        first, total = int(group_starts[group]), int(group_counts[group])
        block = sorted_texels[first:first + total]
        # Sub-blocks by octant: samples farther than the radius from a
        # sub-block's bounding box have a zero kernel for all of its texels and
        # are dropped before the pair matrix; the remaining sums are unchanged.
        centre = (block.min(axis=0) + block.max(axis=0)) * .5
        octant = ((block >= centre) * (1, 2, 4)).sum(axis=1)
        for code in range(8):
            member = np.flatnonzero(octant == code)
            if not len(member):
                continue
            sub = block[member]
            lo, hi = sub.min(axis=0), sub.max(axis=0)
            outside = np.maximum(np.maximum(lo - gathered, gathered - hi), 0)
            near = np.flatnonzero((outside * outside).sum(axis=1) <= radius * radius)
            if not len(near):
                continue
            close, close_turn, close_area = gathered[near], tw[near], aw[near]
            numerator, denominator = np.zeros(len(sub)), np.zeros(len(sub))
            # Both sums are linear, so slices of the pair matrix add up to the
            # same values.
            for t0 in range(0, len(sub), TEXEL_CHUNK):
                if perf_counter() - last_yield > .03:
                    yield group / len(group_keys)
                    last_yield = perf_counter()
                chunk = sub[t0:t0 + TEXEL_CHUNK]
                samples, sample_turn, sample_area = close, close_turn, close_area
                if len(chunk) < len(sub):
                    # A slice's own bounding box drops more samples than the octant's.
                    lo, hi = chunk.min(axis=0), chunk.max(axis=0)
                    outside = np.maximum(np.maximum(lo - close, close - hi), 0)
                    within = np.flatnonzero((outside * outside).sum(axis=1) <= radius * radius)
                    samples, sample_turn, sample_area = close[within], close_turn[within], close_area[within]
                for s0 in range(0, len(samples), SAMPLE_CHUNK):
                    piece = slice(s0, s0 + SAMPLE_CHUNK)
                    delta = chunk[:, None, :] - samples[None, piece]
                    squared = np.einsum("ijk,ijk->ij", delta, delta)
                    kernel = np.clip(1 - np.sqrt(squared, out=squared) / radius, 0, None)
                    numerator[t0:t0 + TEXEL_CHUNK] += kernel @ sample_turn[piece]
                    denominator[t0:t0 + TEXEL_CHUNK] += kernel @ sample_area[piece]
            # 4 R H: a bend of radius r reads 2 R / r; a hard edge saturates.
            value[texel_order[first + member]] = np.where(
                denominator > 1e-18, 4 * radius * numerator / np.maximum(denominator, 1e-18), 0.0)
    # Overlapping UVs (from several triangles or objects) keep the stronger feature.
    flat = rows * n + cols
    order = np.lexsort((np.abs(value), flat))
    flat_sorted = flat[order]
    last = np.flatnonzero(np.append(flat_sorted[1:] != flat_sorted[:-1], True))
    chosen = order[last]
    current = result.reshape(-1)[flat[chosen]]
    strongest = value[chosen]
    result.reshape(-1)[flat[chosen]] = np.where(np.abs(strongest) > np.abs(current), strongest, current)
    return result


def estimate(target, n, radius, result, covered, exclude_angle=None):
    """Synchronous prepare + accumulate for one target."""
    for _ in accumulate(prepare(target, n, radius, exclude_angle), n, radius, result, covered):
        pass
    return result


def field_steps(prepared_targets, n, radius):
    """Relative signed turning per texel from prepared targets; yields progress.

    Returns (map, coverage, baseline) through StopIteration.
    """
    result = np.zeros((n, n), np.float64)
    covered = np.zeros((n, n), bool)
    for index, prepared in enumerate(prepared_targets):
        for fraction in accumulate(prepared, n, radius, result, covered):
            yield (index + fraction) / len(prepared_targets)
    if not covered.any():
        return result, covered, 0.0
    baseline = float(np.median(result[covered]))
    return np.where(covered, result - baseline, 0.0), covered, baseline


def field(targets, n, radius, exclude_angle=None):
    """Relative signed turning per texel; returns (map, coverage, baseline)."""
    prepared = [prepare(target, n, radius, exclude_angle) for target in targets]
    steps = field_steps(prepared, n, radius)
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return done.value


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


def convex_map_steps(prepared_targets, n, radius):
    """Convex turning above the dead zone from prepared targets; yields progress."""
    rel, covered, _ = yield from field_steps(prepared_targets, n, radius)
    return np.clip((rel - DEAD_ZONE) / (1.0 - DEAD_ZONE), 0, 1).astype(np.float32)


def convex_map(targets, n, radius, exclude_angle=None):
    """Convex turning above the dead zone, 0..1, for the CURVATURE edge map."""
    rel, covered, _ = field(targets, n, radius, exclude_angle)
    return np.clip((rel - DEAD_ZONE) / (1.0 - DEAD_ZONE), 0, 1).astype(np.float32)
