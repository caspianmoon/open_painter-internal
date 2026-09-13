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


def _image_identity(image):
    return (image.as_pointer(), tuple(image.size)) if image else None


def _effect_signature(effect):
    fields = ("kind", "enabled", "strength", "radius", "amount", "contrast", "gamma",
              "black", "white", "output_black", "output_white", "hue", "saturation",
              "lightness", "angle", "scale", "seed", "softness", "steps", "samples",
              "mode", "noise")
    result = tuple(getattr(effect, name) for name in fields)
    result += tuple(tuple(getattr(effect, name)) for name in
                    ("color", "color2", "offset", "transform_scale", "matrix", "bias"))
    return result + (_image_identity(effect.image),)


def _stack_signature(ts, layer, geometry_cache):
    """Parameter identities keep mesh-aware masks reusable across color edits.

    Painted/fill pixels and triplanar geometry use the set edit serial. Baked
    sources use image identities; rebaking replaces those images. Explicit force
    evaluation clears shared caches for external in-place pixel modifications.
    """
    memo = getattr(geometry_cache, "signatures", None)
    lookup = (layer.id, ts.edit_serial)
    if memo is not None and lookup in memo:
        return memo[lookup]
    maps = tuple((item.kind, _image_identity(item.image)) for item in ts.baked_maps if item.image)
    result = []
    for mask in layer.masks:
        if not mask.enabled:
            result.append((False,))
            continue
        common = (mask.source, mask.operation, mask.invert, mask.black, mask.white, mask.strength)
        if mask.source in {"GENERATOR", "NOISE"}:
            extra = (mask.generator, mask.amount, mask.contrast, mask.noise_scale,
                     mask.noise_strength, mask.seed, tuple(mask.id_color), maps)
        elif mask.source == "PROCEDURAL":
            extra = (mask.procedural, mask.noise_scale, mask.seed, mask.projection,
                     tuple(mask.scale), mask.rotation, tuple(mask.offset), mask.tiling,
                     mask.triplanar_hardness,
                     ts.edit_serial if mask.projection == "TRIPLANAR" else None)
        elif mask.source == "BAKED":
            extra = (mask.map_type, maps)
        elif mask.source == "FILTER":
            extra = (_effect_signature(mask.effect), ts.edit_serial if mask.effect.image else None)
        else:
            extra = (ts.edit_serial, _image_identity(mask.image), mask.projection,
                     tuple(mask.scale), mask.rotation, tuple(mask.offset), mask.tiling,
                     mask.filtering, mask.triplanar_hardness)
        result.append(common + extra)
    result = tuple(result)
    if memo is not None:
        memo[lookup] = result
    return result


def evaluate_masks(ts, layer, n, y0, y1, cache, geometry_cache=None):
    geometry_cache = geometry_cache if geometry_cache is not None else {}
    from .channel_eval import NEIGHBORHOOD_EFFECTS
    full = any(m.enabled and m.source == "FILTER" and m.effect.kind in NEIGHBORHOOD_EFFECTS for m in layer.masks)
    key = ("mask_stack", ts.id, layer.id, _stack_signature(ts, layer, geometry_cache), n,
           0 if full else y0, n if full else y1)
    if key not in geometry_cache:
        geometry_cache[key] = np.asarray(_evaluate_masks(ts, layer, n, 0 if full else y0,
                                             n if full else y1, cache, geometry_cache), dtype=np.float32)
    return geometry_cache[key][y0:y1] if full else geometry_cache[key]


def _evaluate_masks(ts, layer, n, y0, y1, cache, geometry_cache):
    u, v = np.meshgrid(
        (np.arange(n, dtype=np.float32) + 0.5) / n, (np.arange(y0, y1, dtype=np.float32) + 0.5) / n
    )
    result = None
    for mask in layer.masks:
        if not mask.enabled:
            continue
        if mask.source == "FILTER":
            from .channel_eval import apply_effect
            if result is None:
                result = np.ones((y1-y0, n), dtype=np.float32)
            value = apply_effect(result, mask.effect)
            if value.ndim == 3:
                value = np.sum(value[..., :3] * np.array((.2126,.7152,.0722), dtype=np.float32), axis=-1)
            value = np.clip((value-mask.black)/max(1e-6,mask.white-mask.black),0,1)
            if mask.invert:
                value = 1-value
            result = result * (1-mask.strength) + value * mask.strength
            continue
        if mask.source == "PROCEDURAL":
            from types import SimpleNamespace
            from .channel_eval import _procedural_tile, _coordinates
            prop = SimpleNamespace(procedural=mask.procedural,
                procedural_scale=mask.noise_scale, procedural_seed=mask.seed,
                projection=mask.projection, scale=mask.scale, rotation=mask.rotation,
                offset=mask.offset, triplanar_hardness=mask.triplanar_hardness)
            value = _procedural_tile(prop,n,y0,y1,ts,geometry_cache)
            if mask.projection == "UV" and mask.tiling == "NONE":
                pu,pv = _coordinates(prop,n,y0,y1)
                value = value * ((pu>=0)&(pu<=1)&(pv>=0)&(pv<=1))
        elif mask.source == "FILL":
            if not mask.image:
                raise ValueError(f"Fill mask image is missing on {layer.name}.")
            from .channel_eval import source

            value = source(
                mask,
                "FILL",
                n,
                y0,
                y1,
                cache,
                ts,
                geometry_cache if geometry_cache is not None else {},
            )[..., 0]
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
        elif mask.operation == "NORMAL":
            result = result * (1 - mask.strength) + value * mask.strength
        elif mask.operation == "MULTIPLY":
            result *= 1 - mask.strength + value * mask.strength
        else:
            result = np.clip(result + value * mask.strength, 0, 1)
    return result if result is not None else np.ones((y1 - y0, n), dtype=np.float32)


def bake_mask_texture(ts, layer):
    """Bake the current grayscale effect stack into a packed, editable UV image."""
    from ..core import image_manager as images

    n = int(ts.resolution)
    data = np.ones((n, n, 4), dtype=np.float32)
    cache, geometry_cache = {}, {}
    for y0 in range(0, n, 256):
        y1 = min(n, y0 + 256)
        coverage = evaluate_masks(ts, layer, n, y0, y1, cache, geometry_cache)
        data[y0:y1, :, :3] = coverage[..., None]
    image = images.create(f"{ts.name}_{layer.name}_BakedMask", n)
    images.write(image, data)
    return image
