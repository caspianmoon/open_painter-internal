"""Resolve sequential grayscale mask sources with levels and arithmetic."""

import numpy as np

from ..core.image_manager import pixels
from .blend_modes import sample_uv


def map_pixels(ts, kind, cache):
    prop = next((m for m in ts.baked_maps if m.kind == kind and m.image), None)
    if prop is None:
        raise ValueError(f"This mask requires a baked {kind.title()} map. Bake mesh maps first.")
    key = prop.image.as_pointer()
    if key not in cache:
        cache[key] = pixels(prop.image)
    return cache[key]


def evaluate_masks(ts, layer, n, y0, y1, cache, geometry_cache=None):
    u, v = np.meshgrid(
        (np.arange(n, dtype=np.float32) + 0.5) / n, (np.arange(y0, y1, dtype=np.float32) + 0.5) / n
    )
    result = None
    for mask in layer.masks:
        if not mask.enabled:
            continue
        if mask.source == "FILL":
            if not mask.image:
                raise ValueError(f"Fill mask image is missing on {layer.name}.")
            from .channel_eval import source

            value = source(mask, "FILL", n, y0, y1, cache, ts,
                           geometry_cache if geometry_cache is not None else {})[..., 0]
        elif mask.source == "PAINT":
            if not mask.image:
                raise ValueError(f"Painted mask image is missing on {layer.name}.")
            key = mask.image.as_pointer()
            if key not in cache:
                cache[key] = pixels(mask.image)
            value = sample_uv(cache[key], u, v)[..., 0]
        elif mask.source == "BAKED":
            value = sample_uv(map_pixels(ts, mask.map_type, cache), u, v)[..., 0]
        else:
            from ..generators.base import evaluate

            value = evaluate(ts, mask, u, v, cache)
        value = np.clip((value - mask.black) / max(1e-6, mask.white - mask.black), 0, 1)
        if mask.invert:
            value = 1 - value
        if result is None:
            result = 1 - mask.strength + value * mask.strength
        elif mask.operation == "MULTIPLY":
            result *= 1 - mask.strength + value * mask.strength
        else:
            result = np.clip(result + value * mask.strength, 0, 1)
    return result if result is not None else np.ones((y1 - y0, n), dtype=np.float32)
