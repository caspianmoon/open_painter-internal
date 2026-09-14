"""Export tangent normals from the same height relief used by the preview.

Authored NORMAL rasters stay independent: the preview already applies its
Height Bump node after them. Only a finished exported normal includes height.
The height image remains available separately for displacement workflows.
"""

import numpy as np


def _triangle_charts(entry):
    """Connected UV charts, without joining disconnected or mirrored edges."""
    triangles = entry["triangles"]
    parents = list(range(len(triangles)))
    edges = {}

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, triangle in enumerate(triangles):
        vertices = tuple(triangle.vertices)
        uv = entry["uv"][list(triangle.loops)]
        du, dv = uv[1] - uv[0], uv[2] - uv[0]
        orientation = 1 if du[0] * dv[1] - du[1] * dv[0] >= 0 else -1
        for a, b in ((0, 1), (1, 2), (2, 0)):
            if vertices[a] > vertices[b]:
                a, b = b, a
            # Shared mesh vertices and matching corner UVs establish actual
            # connectivity. Mere adjacency in the atlas does not.
            key = (vertices[a], vertices[b], tuple(uv[a]), tuple(uv[b]), orientation)
            previous = edges.setdefault(key, index)
            parents[root(index)] = root(previous)
    return [root(index) for index in range(len(triangles))]


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
    from ..geometry.uv_rasterizer import tiles
    chart_offset = 1
    with targets(ts) as entries:
        for entry in entries:
            chart_ids = _triangle_charts(entry)
            for tri, chart in zip(entry["triangles"], chart_ids):
                xyz = entry["positions"][list(tri.vertices)]
                uv = entry["uv"][list(tri.loops)]
                duv1, duv2 = uv[1] - uv[0], uv[2] - uv[0]
                determinant = duv1[0] * duv2[1] - duv1[1] * duv2[0]
                if abs(determinant) < 1e-12:
                    continue
                edge1, edge2 = xyz[1] - xyz[0], xyz[2] - xyz[0]
                dpdu = (edge1 * duv2[1] - edge2 * duv1[1]) / determinant
                dpdv = (edge2 * duv1[0] - edge1 * duv2[0]) / determinant
                length_u = np.linalg.norm(dpdu)
                if length_u < 1e-12:
                    continue
                shear = np.dot(dpdu, dpdv) / length_u
                length_v = np.linalg.norm(dpdv - dpdu * (shear / length_u))
                if length_v < 1e-12:
                    continue
                inverse = (1 / length_u, -shear / (length_u * length_v), 1 / length_v)
                for region, inside, *_ in tiles(uv, n):
                    metric[region][inside] = inverse
                    charts[region][inside] = chart_offset + chart
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
    base = normal[..., :3] * 2 - 1
    length = np.linalg.norm(base, axis=-1, keepdims=True)
    base = np.divide(base, length, out=np.zeros_like(base), where=length > 1e-8)
    base[..., 2] = np.where(length[..., 0] > 1e-8, base[..., 2], 1)
    result = normal.copy()
    perturbed = base
    if ts.height_distance > 0 and np.ptp(height) > 1e-8:
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
