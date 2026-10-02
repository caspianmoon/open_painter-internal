"""UV-space geometry rasterization for CPU triplanar texture fills."""

from contextlib import nullcontext

from ..evaluation.metrics import measured

import numpy as np

from ..evaluation.blend_modes import sample_uv


@measured('geometry')
def geometry(ts, n, coverage=False):
    """Rasterize assigned faces to world position and world normal maps."""
    positions = np.zeros((n, n, 3), dtype=np.float32)
    normals = np.zeros_like(positions)
    normals[..., 2] = 1
    valid = np.zeros((n,n), dtype=np.bool_) if coverage else None
    if not ts.assignments:
        # Duplicated texture sets intentionally have no object assignments.
        # Preserve their neutral staging preview until the artist assigns one.
        return (positions, normals, valid) if coverage else (positions, normals)
    from ..geometry.snapshots import targets
    from ..geometry.uv_rasterizer import interpolate, rasterize
    from ..native_bridge.lifecycle import cpu_library
    lib = cpu_library()
    with targets(ts) as objects:
        for target in objects:
            # All triangles of a target at once; later targets overwrite earlier ones.
            if lib is not None and hasattr(lib, 'op_uv_raster_interpolate'):
                # One native pass, with no per-texel arrays: the maps the code below writes.
                from ..native_bridge.geometry import raster_interpolate
                raster_interpolate(lib, target["uv"][target["tri_loops"]], n, [
                    (target["positions"][target["tri_vertices"]], positions),
                    (target["world_normals"][target["tri_loops"]], normals)], valid)
                continue
            rows, cols, tri, weights = rasterize(target["uv"][target["tri_loops"]], n)
            if not len(rows):
                continue
            positions[rows, cols] = interpolate(weights, target["positions"][target["tri_vertices"][tri]])
            normals[rows, cols] = interpolate(weights, target["world_normals"][target["tri_loops"][tri]])
            if coverage:
                valid[rows, cols] = True
    return (positions, normals, valid) if coverage else (positions, normals)


def tangents(ts, n, geometry_cache=None):
    """Per-texel world tangent (dP/du) and bitangent (dP/dv) rasters of the assigned faces.

    The frame decal normals are expressed in; cached with the geometry rasters, which the
    shared cache drops together when the geometry stamp (resolution, transforms, mesh) moves.
    """
    if geometry_cache is not None and "tangents" in geometry_cache:
        return geometry_cache["tangents"]
    tangent = np.zeros((n, n, 3), np.float32)
    bitangent = np.zeros((n, n, 3), np.float32)
    tangent[..., 0] = 1
    bitangent[..., 1] = 1
    # Same neutral frame as geometry(): an unassigned (duplicated) set is not an error.
    from ..geometry.snapshots import targets
    from ..geometry.uv_rasterizer import rasterize
    with targets(ts) if ts.assignments else nullcontext(()) as objects:
        for target in objects:
            uv = target["uv"][target["tri_loops"]].astype(np.float64)
            p = target["positions"][target["tri_vertices"]].astype(np.float64)
            from ..native_bridge.lifecycle import cpu_library
            if (lib := cpu_library()) is not None:
                from ..native_bridge.geometry import frames
                frame = frames(lib, p, uv)
            else:
                e = np.stack((p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)          # (T, 2, 3) world edges
                f = np.stack((uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]), axis=1)       # (T, 2, 2) uv edges
                det = f[:, 0, 0] * f[:, 1, 1] - f[:, 0, 1] * f[:, 1, 0]
                inverse = np.zeros_like(f)
                ok = np.abs(det) > 1e-14
                inverse[ok, 0, 0] = f[ok, 1, 1] / det[ok]
                inverse[ok, 1, 1] = f[ok, 0, 0] / det[ok]
                inverse[ok, 0, 1] = -f[ok, 0, 1] / det[ok]
                inverse[ok, 1, 0] = -f[ok, 1, 0] / det[ok]
                frame = inverse @ e                                                     # (T, 2, 3): dP/du, dP/dv
                frame /= np.maximum(np.linalg.norm(frame, axis=2, keepdims=True), 1e-12)
            if lib is not None and hasattr(lib, 'op_uv_raster_assign'):
                from ..native_bridge.geometry import raster_assign
                raster_assign(lib, target["uv"][target["tri_loops"]], n, [(frame[:, 0], tangent), (frame[:, 1], bitangent)])
                continue
            rows, cols, tri, _ = rasterize(target["uv"][target["tri_loops"]], n)
            if not len(rows):
                continue
            tangent[rows, cols] = frame[tri, 0]
            bitangent[rows, cols] = frame[tri, 1]
    result = (tangent, bitangent)
    if geometry_cache is not None:
        geometry_cache["tangents"] = result
    return result


def triplanar(texture, positions, normals, prop):
    # Rescale before exponentiation to avoid underflow with high hardness.
    weights = np.abs(normals)
    weights = (
        weights / np.maximum(weights.max(axis=-1, keepdims=True), 1e-8)
    ) ** prop.triplanar_hardness
    weights /= np.maximum(weights.sum(axis=-1, keepdims=True), 1e-8)
    result = np.zeros((*positions.shape[:2], texture.shape[-1]), dtype=np.float32)
    cs, sn = np.cos(prop.rotation), np.sin(prop.rotation)
    for axis, (a, b) in enumerate(((1, 2), (0, 2), (0, 1))):
        u, v = positions[..., a] * prop.scale[0], positions[..., b] * prop.scale[1]
        u, v = cs * u - sn * v + prop.offset[0], sn * u + cs * v + prop.offset[1]
        result += (
            sample_uv(texture, u, v, prop.filtering, prop.tiling) * weights[..., axis : axis + 1]
        )
    return result
