"""Live convexity/concavity from the evaluated mesh, without a texel-domain bake.

Face turning (``baking.maps.mean_curvature.face_turning``: signed quarter angle x
length per polygon, convex > 0) and face area are shared out to the polygon's
vertices, welded across seams, diffused over the mesh graph for about
``radius`` worth of edges, and read as the same density the baked map uses
(``4 * radius * turning / area``: a hard edge saturates, a bevel of radius r
reads 2 * radius / r). The per-vertex values rasterize through the UV map like
any attribute, so the whole analysis is O(vertices + texels) and reruns every
time the geometry changes, in cooperative strips.
"""
import numpy as np

from .uv_rasterizer import interpolate, rasterize

MAX_PASSES = 24
CHUNK = 50000
DEAD_ZONE = 0.1   # as baking.maps.mean_curvature.DEAD_ZONE: turning below this reads flat


def weld(positions, tolerance=1e-5):
    """(welded index per vertex, welded count) for coincident vertices across seams."""
    key = np.round(np.asarray(positions, np.float64) / tolerance).astype(np.int64)
    _, inverse = np.unique(key, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    return inverse, int(inverse.max()) + 1 if len(inverse) else 0


def vertex_shares(loop_polygon, loop_vertex, loop_total, turning, area, count):
    """Per-vertex turning and area: every polygon splits its values evenly over its corners."""
    shares_t = np.zeros(count, np.float64)
    shares_a = np.zeros(count, np.float64)
    weights = 1.0 / np.maximum(loop_total[loop_polygon], 1)
    np.add.at(shares_t, loop_vertex, turning[loop_polygon] * weights)
    np.add.at(shares_a, loop_vertex, area[loop_polygon] * weights)
    return shares_t, shares_a


def diffuse(values, edges, passes, rate=.5):
    """Mass-conserving graph diffusion: each pass every vertex hands ``rate`` of
    its value to its neighbours in equal shares. Total is preserved exactly."""
    values = np.array(values, np.float64)
    if passes <= 0 or not len(edges):
        return values
    a, b = edges[:, 0], edges[:, 1]
    degree = np.bincount(np.concatenate((a, b)), minlength=len(values)).astype(np.float64)
    safe = np.maximum(degree, 1.0)
    for _ in range(passes):
        outgoing = values / safe
        received = np.zeros_like(values)
        np.add.at(received, a, outgoing[b])
        np.add.at(received, b, outgoing[a])
        values = np.where(degree > 0, values * (1 - rate) + rate * received, values)
    return values


def passes_for(radius, edges, positions):
    """How many diffusion passes cover ``radius``: the radius over the median edge length."""
    if not len(edges):
        return 0
    lengths = np.linalg.norm(positions[edges[:, 0]] - positions[edges[:, 1]], axis=1)
    median = float(np.median(lengths)) if len(lengths) else 0.0
    if median <= 1e-9:
        return 0
    return int(np.clip(round(radius / median), 0, MAX_PASSES))


def edge_turning(target, exclude_angle=None):
    """Manifold edges as (welded v1, welded v2, signed angle, length) plus per-face
    areas and the welded loop vertices; the pairing rules follow face_turning."""
    from ..baking.maps.mean_curvature import _unit
    mesh = target['mesh']
    positions = np.asarray(target['positions'], np.float64)
    nv, ne, npf, nl = len(mesh.vertices), len(mesh.edges), len(mesh.polygons), len(mesh.loops)
    empty = (np.zeros((0, 2), np.int64), np.zeros(0), np.zeros(0))
    if nv == 0 or ne == 0 or npf == 0:
        return empty, np.zeros(npf), np.zeros(nl, np.int64), np.zeros(0, np.int64), 0
    local_normals = np.empty(npf * 3, np.float32)
    mesh.polygons.foreach_get('normal', local_normals)
    # inverted_safe, as face_turning: a zero-scaled axis is a singular matrix.
    normal_matrix = np.asarray(target['object'].matrix_world.to_3x3().inverted_safe().transposed(), np.float64)
    fn = _unit(local_normals.reshape(-1, 3).astype(np.float64) @ normal_matrix.T)
    lt = np.empty(npf, np.int64); mesh.polygons.foreach_get('loop_total', lt)
    ev = np.empty(ne * 2, np.int64); mesh.edges.foreach_get('vertices', ev); ev = ev.reshape(-1, 2)
    le = np.empty(nl, np.int64); mesh.loops.foreach_get('edge_index', le)
    lv = np.empty(nl, np.int64); mesh.loops.foreach_get('vertex_index', lv)
    lp = np.repeat(np.arange(npf), lt)
    starts = np.cumsum(lt) - lt
    fc = np.zeros((npf, 3), np.float64)
    np.add.at(fc, lp, positions[lv])
    fc /= lt[:, None]
    nxt = np.where(np.arange(nl) + 1 < np.repeat(starts + lt, lt), np.arange(nl) + 1, np.repeat(starts, lt))
    area = np.zeros(npf, np.float64)
    np.add.at(area, lp, .5 * np.linalg.norm(np.cross(positions[lv] - fc[lp], positions[lv[nxt]] - fc[lp]), axis=1))
    welded, count = weld(positions)
    first = np.zeros(count, np.int64)
    first[welded[::-1]] = np.arange(nv - 1, -1, -1)
    cow = positions[first]
    epair = np.sort(welded[ev], axis=1)[le]
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
    if not len(f1):
        return empty, area, welded[lv], welded, count
    v1, v2 = ep_sorted[s2, 0], ep_sorted[s2, 1]
    angle = np.arccos((fn[f1] * fn[f2]).sum(-1).clip(-1.0, 1.0))
    convex = ((fc[f2] - fc[f1]) * fn[f1]).sum(-1) < 0.0
    signed = np.where(convex, angle, -angle)
    same_edge = le_sorted[s2] == le_sorted[s2 + 1]
    signed = np.where(same_edge | ~convex, signed, 0.0)
    if exclude_angle is not None:
        signed = np.where(angle >= exclude_angle, 0.0, signed)
    length = np.linalg.norm(cow[v1] - cow[v2], axis=1)
    return (np.stack((v1, v2), axis=1), signed, length), area, welded[lv], welded, count


def vertex_density(target, radius, exclude_angle=None):
    """Signed turning density per (unwelded) vertex of one target, and a used-vertex mask.

    Each manifold edge hands half of ``angle x min(length, 2 radius)`` to each of
    its vertices; each face corner hands ``min(area / corners, pi radius^2 / 2)``.
    The clamps bound every contribution to what a kernel of ``radius`` would see,
    so long thin faces (a cylinder's side quads) do not dilute the rim turning.
    """
    mesh = target['mesh']
    nv = len(mesh.vertices)
    (edges, signed, length), area, loop_welded, welded, count = edge_turning(target, exclude_angle)
    if count == 0:
        return np.zeros(nv, np.float32), np.zeros(nv, bool)
    lt = np.empty(len(mesh.polygons), np.int64); mesh.polygons.foreach_get('loop_total', lt)
    lp = np.repeat(np.arange(len(mesh.polygons)), lt)
    shares_t = np.zeros(count, np.float64)
    if len(edges):
        w = .5 * signed * np.minimum(length, 2 * radius)
        np.add.at(shares_t, edges[:, 0], w)
        np.add.at(shares_t, edges[:, 1], w)
    shares_a = np.zeros(count, np.float64)
    corner_area = np.minimum(area[lp] / np.maximum(lt[lp], 1), np.pi * radius * radius / 2)
    np.add.at(shares_a, loop_welded, corner_area)
    unique_edges = np.unique(edges, axis=0) if len(edges) else np.zeros((0, 2), np.int64)
    positions = np.asarray(target['positions'], np.float64)
    first = np.zeros(count, np.int64)
    first[welded[::-1]] = np.arange(nv - 1, -1, -1)
    passes = passes_for(radius, unique_edges, positions[first])
    shares_t, shares_a = diffuse(shares_t, unique_edges, passes), diffuse(shares_a, unique_edges, passes)
    used = shares_a > 1e-18
    density = np.where(used, 4 * radius * shares_t / np.maximum(shares_a, 1e-18), 0.0)
    return density[welded].astype(np.float32), used[welded]


def raster_steps(targets, n, radius, exclude_angle=None):
    """Yield progress while rasterizing per-vertex density; returns (values, covered)."""
    result = np.zeros((n, n), np.float32)
    covered = np.zeros((n, n), bool)
    total = max(1, sum(len(target['tri_vertices']) for target in targets))
    done = 0
    for target in targets:
        density, used = vertex_density(target, radius, exclude_angle)
        tri_vertices, tri_loops, uv = target['tri_vertices'], target['tri_loops'], target['uv']
        for start in range(0, len(tri_vertices), CHUNK):
            stop = min(start + CHUNK, len(tri_vertices))
            rows, cols, tri, weights = rasterize(uv[tri_loops[start:stop]], n)
            if len(rows):
                corners = density[tri_vertices[start:stop][tri]]
                result[rows, cols] = interpolate(weights, corners[..., None])[:, 0]
                covered[rows, cols] = True
            done += stop - start
            yield done / total
    return result, covered


BASELINE_PERCENTILE = 25


def relative(values, covered):
    """Subtract the surface's baseline density so only features that bend more
    than their surroundings register (a sphere reads flat). The baked map uses
    the median; per-vertex values spread each edge over its neighbouring texels,
    so a low percentile keeps a coarse mesh's flat faces at zero."""
    if not covered.any():
        return values
    baseline = float(np.percentile(values[covered], BASELINE_PERCENTILE))
    return np.where(covered, values - baseline, 0.0).astype(np.float32)


def signed_to_unit(rel, sign=1.0):
    """Convexity (sign 1) or concavity (sign -1) above the dead zone, 0..1."""
    return np.clip((sign * rel - DEAD_ZONE) / (1.0 - DEAD_ZONE), 0, 1).astype(np.float32)
