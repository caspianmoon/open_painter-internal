"""Signed discrete mean curvature per vertex of the evaluated target mesh.

Ported from Ucupaint 3.0.0 ``bake_common.py`` ``fill_curvature_vcol``
(Yusuf Umar and contributors, GPL-3.0-or-later; see ``docs/research/ucupaint-study.md``),
adapted to world-space evaluated snapshots: no temporary color attribute is
written to the user's mesh and the scalar goes straight to the UV rasterizer.

The signal is 0 on flat surfaces, positive for convex edges and negative for
concave ones, saturating where the curvature radius reaches ``distance``.
Vertices are welded by position first so unwelded seams read as creases, and
one neighbour relaxation pass smooths coarse meshes. Because the discrete
estimate spreads a sharp edge over its vertex area share, the baker stretches
the set's signal symmetrically when nothing saturates (Ucupaint normalizes the
baked image instead); flat stays exactly 0.5 in the map. Unlike the existing
CURVATURE crease map this is continuous and signed, but it still describes the
target's own geometry, not detail baked from a high-poly source.
"""
import numpy as np

from ...geometry.snapshots import _unit


def vertex_signal(target, distance):
    """Return one signed float per evaluated vertex in ``[-1, 1]`` (0 = flat)."""
    mesh = target["mesh"]
    positions = np.asarray(target["positions"], np.float64)
    nv, ne, npf, nl = len(mesh.vertices), len(mesh.edges), len(mesh.polygons), len(mesh.loops)
    if nv == 0 or ne == 0 or npf == 0:
        return np.zeros(nv, np.float32)
    local_normals = np.empty(npf * 3, np.float32)
    mesh.polygons.foreach_get("normal", local_normals)
    world = np.asarray(target["object"].matrix_world, np.float64)
    normal_matrix = np.linalg.inv(world[:3, :3]).T
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
    # World-space polygon centres and areas from the loop fan, so non-uniform
    # object scale is honoured like the other utility maps.
    starts = np.cumsum(lt) - lt
    fc = np.zeros((npf, 3), np.float64)
    np.add.at(fc, lp, positions[lv])
    fc /= lt[:, None]
    area = np.zeros(npf, np.float64)
    a = positions[lv]
    b = positions[lv[np.where(np.arange(nl) + 1 < np.repeat(starts + lt, lt), np.arange(nl) + 1, np.repeat(starts, lt))]]
    np.add.at(area, lp, .5 * np.linalg.norm(np.cross(a - fc[lp], b - fc[lp]), axis=1))

    # Weld coincident vertices so stitched panels crease instead of reading flat.
    key = np.round(positions * 1e5).astype(np.int64)
    uniq, weld = np.unique(key, axis=0, return_inverse=True)
    weld = weld.reshape(-1)
    nw = len(uniq)
    first = np.zeros(nw, np.int64)
    first[weld[::-1]] = np.arange(nv - 1, -1, -1)
    cow = positions[first]

    # Pair the two faces meeting at each welded edge.
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
    v1, v2 = ep_sorted[s2, 0], ep_sorted[s2, 1]

    # Signed dihedral angle, convex positive, weighted by edge length.
    d = (fn[f1] * fn[f2]).sum(-1).clip(-1.0, 1.0)
    angle = np.arccos(d)
    convex = ((fc[f2] - fc[f1]) * fn[f1]).sum(-1) < 0.0
    sign = np.where(convex, 1.0, -1.0)
    elen = np.linalg.norm(cow[v1] - cow[v2], axis=1)
    w = sign * angle * elen
    # Edges joined only through the weld are sewn junctions or shading splits:
    # junctions crease inward, so only their concave side counts.
    same_edge = le_sorted[s2] == le_sorted[s2 + 1]
    w = np.where(same_edge | ~convex, w, 0.0)

    acc = np.zeros(nw, np.float64)
    np.add.at(acc, v1, w * .5)
    np.add.at(acc, v2, w * .5)
    varea = np.zeros(nw, np.float64)
    np.add.at(varea, weld[lv], np.repeat(area / lt, lt))
    varea = np.maximum(varea / 3.0, 1e-12)
    k = acc / (4.0 * varea)
    signal = np.clip(k * distance, -1.0, 1.0)

    # One relaxation pass evens out coarse meshes whose vertices are far apart.
    nbr = np.zeros(nw, np.float64)
    cnt = np.zeros(nw, np.float64)
    np.add.at(nbr, v1, signal[v2])
    np.add.at(nbr, v2, signal[v1])
    np.add.at(cnt, v1, 1.0)
    np.add.at(cnt, v2, 1.0)
    has = cnt > 0
    signal[has] = .5 * signal[has] + .5 * (nbr[has] / cnt[has])
    return signal[weld].astype(np.float32)
