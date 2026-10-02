"""Export tangent normals from the same height relief used by the preview.

Authored NORMAL rasters stay independent: the preview already applies its
Height Bump node after them. Only a finished exported normal includes height.
The height image remains available separately for displacement workflows.
"""

import numpy as np


def _triangle_charts(tri_vertices, uv):
    """Connected UV charts, without joining disconnected or mirrored edges.

    Labels are the smallest triangle index of each chart. Each round hooks a
    triangle and its current label onto the smallest label around its edges,
    then shortcuts the label chains (Shiloach-Vishkin), so whole partial charts
    merge per round and a dense evaluated mesh settles in a few array passes
    rather than a Python loop per triangle.
    """
    count = len(tri_vertices)
    labels = np.arange(count)
    if not count:
        return labels
    du, dv = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
    orientation = np.where(du[:, 0] * dv[:, 1] - du[:, 1] * dv[:, 0] >= 0, 1, -1)
    corner_a, corner_b = np.array((0, 1, 2)), np.array((1, 2, 0))
    # Shared mesh vertices and matching corner UVs establish actual
    # connectivity. Mere adjacency in the atlas does not.
    vertex_a, vertex_b = tri_vertices[:, corner_a], tri_vertices[:, corner_b]
    uv_a, uv_b = uv[:, corner_a], uv[:, corner_b]
    swap = vertex_a > vertex_b
    keys = np.empty((count * 3, 7), np.float64)
    keys[:, 0] = np.where(swap, vertex_b, vertex_a).ravel()
    keys[:, 1] = np.where(swap, vertex_a, vertex_b).ravel()
    keys[:, 2:4] = np.where(swap[..., None], uv_b, uv_a).reshape(-1, 2)
    keys[:, 4:6] = np.where(swap[..., None], uv_a, uv_b).reshape(-1, 2)
    keys[:, 6] = np.repeat(orientation, 3)
    order = np.lexsort(keys.T[::-1])
    ranked = keys[order]
    starts = np.flatnonzero(np.r_[True, np.any(ranked[1:] != ranked[:-1], axis=1)])
    corner_triangle = order // 3
    edge = np.empty(count * 3, np.int64)
    edge[order] = np.repeat(np.arange(len(starts)), np.diff(np.r_[starts, count * 3]))
    edge = edge.reshape(count, 3)
    while True:
        lowest = np.minimum.reduceat(labels[corner_triangle], starts)
        candidate = np.minimum(labels, lowest[edge].min(axis=1))
        hooked = labels.copy()
        # Hooking the label itself, not only the triangle, is what merges
        # whole partial charts per round instead of one edge hop per round.
        np.minimum.at(hooked, labels, candidate)
        hooked = np.minimum(hooked, candidate)
        while True:
            jumped = hooked[hooked]
            if np.array_equal(jumped, hooked):
                break
            hooked = jumped
        if np.array_equal(hooked, labels):
            return labels
        labels = hooked


def _uv_metric(ts, n):
    """Inverse world distance per UV unit, including scale and UV shear.

Columns convert (dH/dU, dH/dV) into slopes along tangent and bitangent:
sx = U * column 0; sy = U * column 1 + V * column 2. Unassigned
materials use the conventional one-world-unit tile. Empty atlas texels have
    zero slope; the authored normal in those texels is preserved. Chart labels
    prevent derivatives from crossing a UV seam into an unrelated surface.
"""
    metric = np.zeros((n, n, 3), np.float32)
    charts = np.zeros((n, n), np.int32)
    if not ts.assignments:
        metric[..., (0, 2)] = 1
        charts.fill(1)
        return metric, charts
    from ..geometry.snapshots import targets
    from ..geometry.uv_rasterizer import rasterize
    from ..native_bridge.lifecycle import cpu_library
    lib = cpu_library()
    chart_offset = 1
    with targets(ts) as entries:
        for entry in entries:
            uv = entry["uv"][entry["tri_loops"]]
            xyz = entry["positions"][entry["tri_vertices"]]
            chart_ids = _triangle_charts(entry["tri_vertices"], uv)
            duv1, duv2 = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
            determinant = duv1[:, 0] * duv2[:, 1] - duv1[:, 1] * duv2[:, 0]
            edge1, edge2 = xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]
            with np.errstate(divide="ignore", invalid="ignore"):
                dpdu = (edge1 * duv2[:, 1, None] - edge2 * duv1[:, 1, None]) / determinant[:, None]
                dpdv = (edge2 * duv1[:, 0, None] - edge1 * duv2[:, 0, None]) / determinant[:, None]
                length_u = np.linalg.norm(dpdu, axis=1)
                shear = np.einsum("ij,ij->i", dpdu, dpdv) / length_u
                length_v = np.linalg.norm(dpdv - dpdu * (shear / length_u)[:, None], axis=1)
                inverse = np.stack((1 / length_u, -shear / (length_u * length_v), 1 / length_v), axis=1)
            # Degenerate triangles are dropped before rasterizing so they cannot
            # overwrite texels an earlier valid triangle covered.
            kept = np.flatnonzero((np.abs(determinant) >= 1e-12)
                                  & (length_u >= 1e-12) & (length_v >= 1e-12))
            if lib is not None and hasattr(lib, 'op_uv_raster_assign'):
                # One native pass, with no per-texel arrays: the maps the scatter below writes.
                from ..native_bridge.geometry import raster_assign
                raster_assign(lib, uv[kept], n, [(inverse[kept], metric), (chart_offset + chart_ids[kept], charts)])
            else:
                rows, cols, tri, _ = rasterize(uv[kept], n)
                if len(rows):
                    tri = kept[tri]
                    metric[rows, cols] = inverse[tri]
                    charts[rows, cols] = chart_offset + chart_ids[tri]
            chart_offset += len(chart_ids)
    return metric, charts


def _derivative(height, charts, axis):
    """Centered UV derivative, one-sided at image and connected-chart borders."""
    before = np.roll(height, 1, axis=axis)
    after = np.roll(height, -1, axis=axis)
    valid_before = (charts > 0) & (np.roll(charts, 1, axis=axis) == charts)
    valid_after = (charts > 0) & (np.roll(charts, -1, axis=axis) == charts)
    edge0 = [slice(None)] * 2
    edge1 = [slice(None)] * 2
    edge0[axis], edge1[axis] = 0, -1
    valid_before[tuple(edge0)] = False
    valid_after[tuple(edge1)] = False
    difference = np.where(valid_after, after - height, 0)
    difference += np.where(valid_before, height - before, 0)
    count = valid_after.astype(np.float32) + valid_before
    return np.divide(difference * height.shape[axis], count,
                     out=np.zeros_like(height), where=count > 0)


def combined(ts, normal, height):
    """Return an OpenGL RGBA normal containing authored normals and height.

Finite differences use the exported texel spacing, so changing export
resolution does not change the physical relief. Geometry metrics use evaluated
assigned faces, render UVs and world transforms. Overlapping UVs resolve in
the same deterministic object/triangle order as other geometry rasterization.
"""
    height = np.asarray(height, np.float32)
    if height.ndim == 3:
        height = height[..., 0]
    normal = np.asarray(normal, np.float32)
    if height.shape != normal.shape[:2] or height.shape[0] != height.shape[1]:
        raise ValueError("Surface normal inputs need matching square dimensions.")
    relief = ts.height_distance > 0 and np.ptp(height) > 1e-8
    from ..native_bridge.lifecycle import cpu_library
    lib = cpu_library()
    if lib is not None and normal.shape[-1] in (3, 4) and hasattr(lib, 'op_normal_relief'):
        from ..native_bridge import pixels
        if not relief:
            return pixels.normal_encode(lib, normal, decode=True)   # the arithmetic below, in one pass
        metric, charts = _uv_metric(ts, height.shape[0])
        return pixels.normal_relief(lib, normal, height, metric, charts, ts.height_distance)
    base = normal[..., :3] * 2 - 1
    length = np.linalg.norm(base, axis=-1, keepdims=True)
    base = np.divide(base, length, out=np.zeros_like(base), where=length > 1e-8)
    base[..., 2] = np.where(length[..., 0] > 1e-8, base[..., 2], 1)
    result = normal.copy()
    perturbed = base
    if relief:
        metric, charts = _uv_metric(ts, height.shape[0])
        dhdu = _derivative(height, charts, 1)
        dhdv = _derivative(height, charts, 0)
        slope_x = dhdu * metric[..., 0] * ts.height_distance
        slope_y = (dhdu * metric[..., 1] + dhdv * metric[..., 2]) * ts.height_distance
        # The preview's Bump uses the supplied authored normal in its surface
        # gradient (Cycles kernel/svm/displace.h). RNM instead rotates a detail
        # normal and disagrees when the authored normal is tilted. In an
        # orthonormal tangent frame Rx=(Nz,0,-Nx), Ry=(0,Nz,-Ny), det=Nz.
        z = base[..., 2]
        gradient = np.stack((slope_x * z, slope_y * z,
                             -slope_x * base[..., 0] - slope_y * base[..., 1]), axis=-1)
        perturbed = np.abs(z)[..., None] * base - np.sign(z)[..., None] * gradient
        length = np.linalg.norm(perturbed, axis=-1, keepdims=True)
        perturbed = np.divide(perturbed, length, out=base.copy(), where=length > 1e-8)
    result[..., :3] = perturbed * .5 + .5
    return result
