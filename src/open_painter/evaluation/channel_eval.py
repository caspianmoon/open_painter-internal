"""Resolve constant and raster layer inputs in bounded row tiles."""

import numpy as np

from ..core.image_manager import pixels
from .blend_modes import sample_uv


def source(prop, kind, n, y0, y1, cache, ts, geometry_cache):
    if prop.image_name and not prop.image:
        raise ValueError(
            f"Texture image is missing: {prop.image_name}. Reload or clear the channel texture."
        )
    if prop.image:
        key = prop.image.as_pointer()
        if key not in cache:
            cache[key] = pixels(prop.image)
            if prop.image.colorspace_settings.name == "sRGB":
                rgb = cache[key][..., :3]
                cache[key][..., :3] = np.where(
                    rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4
                )
        if kind == "PAINT" and cache[key].shape[:2] == (n, n):
            # Paint pixels already occupy the output UV grid. Resampling four
            # neighbours per pixel was the dominant cost of every brush update.
            rgba = cache[key][y0:y1].copy()
            if prop.image.is_float:
                np.divide(
                    rgba[..., :3], rgba[..., 3:4], out=rgba[..., :3], where=rgba[..., 3:4] > 1e-8
                )
                rgba[..., :3][rgba[..., 3] <= 1e-8] = 0
            return rgba
        if kind == "FILL" and prop.projection == "TRIPLANAR":
            from ..painting.projection import geometry, triplanar

            if "geometry" not in geometry_cache:
                geometry_cache["geometry"] = geometry(ts, n)
            positions, normals = geometry_cache["geometry"]
            return triplanar(cache[key], positions[y0:y1], normals[y0:y1], prop)
        u, v = np.meshgrid(
            (np.arange(n, dtype=np.float32) + 0.5) / n,
            (np.arange(y0, y1, dtype=np.float32) + 0.5) / n,
        )
        if kind == "FILL":
            u, v = (u - 0.5) * prop.scale[0], (v - 0.5) * prop.scale[1]
            cs, sn = np.cos(prop.rotation), np.sin(prop.rotation)
            u, v = cs * u - sn * v + 0.5 + prop.offset[0], sn * u + cs * v + 0.5 + prop.offset[1]
        rgba = (
            sample_uv(cache[key], u, v, prop.filtering, prop.tiling)
            if kind == "FILL"
            else sample_uv(cache[key], u, v)
        )
        if kind == "PAINT" and prop.image.is_float:
            # Blender's native float brush buffers are premultiplied.
            rgba[..., :3] = np.divide(
                rgba[..., :3],
                rgba[..., 3:4],
                out=np.zeros_like(rgba[..., :3]),
                where=rgba[..., 3:4] > 1e-8,
            )
        return rgba
    if kind == "PAINT":
        return np.array([0, 0, 0, 0], dtype=np.float32)
    if prop.channel in {"BASE_COLOR", "NORMAL"}:
        return np.array(prop.color, dtype=np.float32)
    return np.array([prop.value, prop.value, prop.value, 1], dtype=np.float32)
