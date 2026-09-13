"""UV-space geometry rasterization for CPU triplanar texture fills."""

from ..evaluation.metrics import measured

import numpy as np

from ..evaluation.blend_modes import sample_uv


@measured('geometry')
def geometry(ts, n):
    """Rasterize assigned faces to world position and world normal maps."""
    positions = np.zeros((n, n, 3), dtype=np.float32)
    normals = np.zeros_like(positions)
    normals[..., 2] = 1
    if not ts.assignments:
        # Duplicated texture sets intentionally have no object assignments.
        # Preserve their neutral staging preview until the artist assigns one.
        return positions, normals
    from ..geometry.snapshots import targets
    from ..geometry.uv_rasterizer import tiles
    with targets(ts) as objects:
        for target in objects:
            for triangle in target["triangles"]:
                loops = list(triangle.loops)
                points = target["positions"][list(triangle.vertices)]
                corner_normals = target["world_normals"][loops]
                for region, inside, weights, *_ in tiles(target["uv"][loops], n):
                    positions[region][inside] = (weights @ points)[inside]
                    normals[region][inside] = (weights @ corner_normals)[inside]
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
