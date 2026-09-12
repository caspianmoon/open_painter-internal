"""UV-space geometry rasterization for CPU triplanar texture fills."""

import numpy as np

from ..evaluation.blend_modes import sample_uv


def geometry(ts, n):
    """Rasterize assigned faces to world position and world normal maps."""
    positions = np.zeros((n, n, 3), dtype=np.float32)
    normals = np.zeros_like(positions)
    normals[..., 2] = 1
    for obj in dict.fromkeys(r.object for r in ts.assignments if r.object):
        mesh = obj.data
        if not mesh.uv_layers.active:
            raise ValueError(f"No UV map found on {obj.name}.")
        slots = {r.slot_index for r in ts.assignments if r.object == obj}
        mesh.calc_loop_triangles()
        # Unconnected image texture nodes use the render UV layer, which may
        # differ from the layer currently selected in the UV editor.
        uv_layer = next((layer for layer in mesh.uv_layers if layer.active_render),
                        mesh.uv_layers.active)
        uv = uv_layer.data
        world = obj.matrix_world
        normal_matrix = world.to_3x3().inverted_safe().transposed()
        for triangle in mesh.loop_triangles:
            if triangle.material_index not in slots:
                continue
            coords = np.array([uv[i].uv[:] for i in triangle.loops], dtype=np.float32) * n - 0.5
            points = np.array(
                [(world @ mesh.vertices[i].co)[:] for i in triangle.vertices], dtype=np.float32
            )
            vertex_normals = np.array(
                [
                    (normal_matrix @ mesh.vertices[i].normal).normalized()[:]
                    for i in triangle.vertices
                ],
                dtype=np.float32,
            )
            lo = np.maximum(np.ceil(coords.min(axis=0)).astype(int), 0)
            hi = np.minimum(np.floor(coords.max(axis=0)).astype(int) + 1, n)
            if np.any(hi <= lo):
                continue
            a, b, c = coords
            det = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(det) < 1e-8:
                continue
            for y0 in range(lo[1], hi[1], 128):
                y1 = min(hi[1], y0 + 128)
                x, y = np.meshgrid(
                    np.arange(lo[0], hi[0], dtype=np.float32), np.arange(y0, y1, dtype=np.float32)
                )
                w0 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / det
                w1 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / det
                weights = np.stack((w0, w1, 1 - w0 - w1), axis=-1)
                inside = np.all(weights >= -1e-5, axis=-1)
                p = positions[y0:y1, lo[0] : hi[0]]
                normal = normals[y0:y1, lo[0] : hi[0]]
                p[inside] = (weights @ points)[inside]
                normal[inside] = (weights @ vertex_normals)[inside]
    return positions, normals


def triplanar(texture, positions, normals, prop):
    # Rescale before exponentiation to avoid underflow with high hardness.
    weights = np.abs(normals)
    weights = (
        weights / np.maximum(weights.max(axis=-1, keepdims=True), 1e-8)
    ) ** prop.triplanar_hardness
    weights /= np.maximum(weights.sum(axis=-1, keepdims=True), 1e-8)
    result = np.zeros((*positions.shape[:2], 4), dtype=np.float32)
    cs, sn = np.cos(prop.rotation), np.sin(prop.rotation)
    for axis, (a, b) in enumerate(((1, 2), (0, 2), (0, 1))):
        u, v = positions[..., a] * prop.scale[0], positions[..., b] * prop.scale[1]
        u, v = cs * u - sn * v + prop.offset[0], sn * u + cs * v + prop.offset[1]
        result += (
            sample_uv(texture, u, v, prop.filtering, prop.tiling) * weights[..., axis : axis + 1]
        )
    return result
