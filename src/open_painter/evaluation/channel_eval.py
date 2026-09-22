"""Resolve raster/procedural channel inputs and texture-space effect stacks.

Unfiltered sources retain cooperative row tiling. Shared scalar procedurals are
cached across channels. Neighborhood effects with a bounded footprint filter
aligned strips a few halos tall (``band_rows``) and serve tiles from the current
strip; whole-image effects evaluate the actual full raster once, then serve
slices. No effect wraps across row-tile boundaries.
"""

import numpy as np

from .blend_modes import sample_uv
from .material_space import height_amplitude
from .metrics import count, measured
from ..texture_engine import filters, procedurals

TILE_ROWS = 16


def band_rows(halo, n):
    """Rows per strip of a bounded-footprint filter. The halo is filtered again
    for every strip, so strips grow with it (at most doubling the work) while
    each temporary stays a fraction of the image; small halos still get strips
    several tiles tall so the compositor's 16-row sweep does not refilter them."""
    return min(n, max(4 * TILE_ROWS, (2 * int(halo) // TILE_ROWS) * TILE_ROWS))


NEIGHBORHOOD_EFFECTS = frozenset({
    "BLUR", "GAUSSIAN_BLUR", "DIRECTIONAL_BLUR", "RADIAL_BLUR", "SLOPE_BLUR",
    "HIGH_PASS", "SHARPEN", "DILATE", "ERODE", "MORPHOLOGICAL_EXPAND",
    "MORPHOLOGICAL_CONTRACT", "EDGE_DETECT", "SMOOTH_MASK", "WARP",
    "DIRECTIONAL_WARP", "VECTOR_WARP", "UV_DISTORTION", "TWIRL", "RIPPLE", "WAVE",
    "NON_UNIFORM_TRANSFORM", "HEIGHT_TO_NORMAL", "NORMAL_COMBINE",
})


EFFECT_UI_FIELDS = {
    "LEVELS": ("black", "white", "gamma", "output_black", "output_white"),
    "GAMMA": ("gamma",), "CLAMP": ("black", "white"),
    "HISTOGRAM_SCAN": ("amount", "softness", "contrast"),
    "HISTOGRAM_RANGE": ("black", "white", "softness"), "THRESHOLD": ("amount",),
    "BRIGHTNESS_CONTRAST": ("amount", "contrast"),
    "POSTERIZE": ("steps",), "QUANTIZE": ("steps",),
    "SHARPEN": ("radius", "amount"),
    "DIRECTIONAL_BLUR": ("radius", "angle", "samples"),
    "RADIAL_BLUR": ("radius", "samples"),
    "SLOPE_BLUR": ("radius", "amount", "noise", "scale", "seed", "samples", "mode"),
    "WARP": ("radius", "amount", "noise", "scale", "seed"),
    "DIRECTIONAL_WARP": ("radius", "amount", "angle", "noise", "scale", "seed"),
    "VECTOR_WARP": ("radius", "amount", "noise", "scale", "seed"),
    "UV_DISTORTION": ("radius", "amount", "noise", "scale", "seed"),
    "TWIRL": ("radius", "angle"), "RIPPLE": ("radius", "amount"),
    "WAVE": ("radius", "amount", "angle"),
    "NON_UNIFORM_TRANSFORM": ("transform_scale", "angle", "offset"),
    "HSL": ("hue", "saturation", "lightness"),
    "HSV": ("hue", "saturation", "lightness"),
    "HUE_SHIFT": ("hue",), "SATURATION": ("saturation",),
    "GRADIENT_MAP": ("color", "color2"), "COLOR_BALANCE": ("color",),
    "COLORIZE": ("color", "amount"), "TINT": ("color", "amount"),
    "TEMPERATURE": ("amount",), "REPLACE_COLOR": ("color", "color2", "softness"),
    "NORMAL_COMBINE": ("image", "amount"), "NORMAL_STRENGTH": ("amount",),
    "HEIGHT_TO_NORMAL": ("amount",), "AO_STRENGTH": ("amount",),
    "ROUGHNESS_CONTRAST": ("contrast",), "CHANNEL_MIXER": ("matrix", "bias"),
    "EDGE_DETECT": ("amount",),
    "ANCHOR": (),
}
for _kind in ("BLUR", "GAUSSIAN_BLUR", "HIGH_PASS", "DILATE", "ERODE",
              "MORPHOLOGICAL_EXPAND", "MORPHOLOGICAL_CONTRACT", "SMOOTH_MASK"):
    EFFECT_UI_FIELDS[_kind] = ("radius",)
for _kind in ("ROUGHNESS_REMAP", "METALLIC_REMAP", "HEIGHT_REMAP"):
    EFFECT_UI_FIELDS[_kind] = EFFECT_UI_FIELDS["LEVELS"]


def effect_ui_fields(kind):
    """Native control schema, shared with mask/channel properties panels."""
    labels = {"black": "Input Black", "white": "Input White", "noise": "Driving Noise",
              "scale": "Noise Scale", "seed": "Noise Seed", "hue": "Hue Shift",
              "color": "Color", "color2": "Second Color", "image": "Detail Normal"}
    if kind in {"THRESHOLD", "HISTOGRAM_SCAN"}:
        labels["amount"] = "Position"
    elif kind == "BRIGHTNESS_CONTRAST":
        labels["amount"] = "Brightness"
    elif kind in {"WARP", "SLOPE_BLUR", "DIRECTIONAL_WARP", "VECTOR_WARP", "UV_DISTORTION", "WAVE", "RIPPLE"}:
        labels["amount"] = "Displacement Factor"
    elif kind in {"NORMAL_COMBINE", "NORMAL_STRENGTH", "HEIGHT_TO_NORMAL", "AO_STRENGTH", "EDGE_DETECT"}:
        labels["amount"] = "Intensity"
    if kind == "GRADIENT_MAP":
        labels.update(color="Black Color", color2="White Color")
    return tuple((name, labels.get(name, name.replace("_", " ").title()))
                 for name in EFFECT_UI_FIELDS.get(kind, ()))


def effect_parameters(effect):
    """Translate persistent effect controls to the pure texture-filter API.

    Effect strength is stack opacity and is deliberately not passed as filter
    displacement. Displacement uses radius * amount in pixels. RNA angles use
    radians; the independent filter API uses degrees.
    """
    defaults = {"radius": 2, "amount": .5, "contrast": 1, "gamma": 1,
                "black": 0, "white": 1, "output_black": 0, "output_white": 1,
                "hue": 0, "saturation": 1, "lightness": 0, "softness": .05,
                "steps": 4, "samples": 8, "mode": "BLUR", "offset": (0, 0),
                "color": (0, 0, 0), "color2": (1, 1, 1),
                "matrix": (1, 0, 0, 0, 1, 0, 0, 0, 1), "bias": (0, 0, 0)}
    params = {key: getattr(effect, key, value) for key, value in defaults.items()}
    params["angle"] = np.degrees(getattr(effect, "angle", 0))
    params["scale"] = tuple(getattr(effect, "transform_scale", (1, 1)))
    params["tolerance"] = params["softness"]
    params["position"] = params["amount"]
    params["brightness"] = params["amount"]
    params["value"] = params["lightness"]
    params["strength"] = params["amount"]
    if effect.kind in {"WARP", "SLOPE_BLUR", "DIRECTIONAL_WARP", "VECTOR_WARP",
                       "UV_DISTORTION", "RIPPLE", "WAVE"}:
        params["strength"] = params["radius"] * params["amount"]
    return params


def seam_map_for(ts, n, effects):
    """The set's seam map when any effect runs in Surface space, else None."""
    from .operation_contracts import surface
    if ts is None or not any(surface(effect) for effect in effects if effect.enabled and effect.strength > 0):
        return None
    from ..geometry.seams import map_for
    try:
        return map_for(ts, n)
    except ValueError:
        return None


def apply_effect(data, effect, seams=None):
    """Apply one persistent effect to a complete texture; preserve RGBA coverage.

    Grayscale colorization/normal conversion can return RGB. Effect opacity blends
    against the original expanded grayscale. Reusable by layer and mask stacks.
    ``seams`` (a whole-image SeamMap) makes Surface-space neighbourhood effects
    read across UV seams and directional effects follow a world direction.
    """
    if not effect.enabled or effect.strength <= 0 or effect.kind == "ANCHOR":
        return data  # Anchor points publish the stack here and change nothing.
    source = np.asarray(data, np.float32)
    from .operation_contracts import footprint, surface
    seam_aware = seams is not None and surface(effect) and source.shape[:2] == (seams.n, seams.n)
    alpha = source[..., 3:4] if source.ndim == 3 and source.shape[-1] == 4 else None
    original = source[..., :3] if alpha is not None else source
    params = effect_parameters(effect)
    if effect.kind in {"WARP", "SLOPE_BLUR", "DIRECTIONAL_WARP", "VECTOR_WARP", "UV_DISTORTION"} and not seam_aware:
        h, w = source.shape[:2]
        y, x = np.indices((h, w), dtype=np.float32)
        u, v = (x + .5) / w, (y + .5) / h
        noise = getattr(effect, "noise", "FBM")
        scale, seed = getattr(effect, "scale", 8), getattr(effect, "seed", 1)
        field = procedurals.evaluate(noise, u, v, seed=seed, scale=scale)
        if effect.kind in {"VECTOR_WARP", "UV_DISTORTION"}:
            field = np.stack((field, procedurals.evaluate(noise, u, v, seed=seed + 37, scale=scale)), axis=-1)
        params["field"] = field
    if effect.kind == "NORMAL_COMBINE":
        detail = getattr(effect, "image", None)
        if detail is None:
            raise ValueError("Normal Combine needs a detail normal texture.")
        # A detail normal map is data: read as stored whatever the image's tag
        # (an opened PNG arrives sRGB). The decision is per read, so the same
        # image used as a colour fill elsewhere still decodes there; the shader
        # re-encodes its sample to match (shader_graph.Graph.effect_image).
        from .image_snapshots import read
        image = read({}, detail, linear=False)[..., :3]
        if image.shape[:2] != source.shape[:2]:
            h, w = source.shape[:2]
            y, x = np.indices((h, w), dtype=np.float32)
            image = sample_uv(image, (x + .5) / w, (y + .5) / h)
        params["detail"] = filters.apply(image, "NORMAL_STRENGTH", strength=params["amount"])
    if seam_aware:
        # Filter each island on a crop padded from its own seams (0.9.4).
        halo = footprint(effect.kind, effect.radius, 'UV')
        halo = int(seams.halo if halo is None else min(max(halo, 2), seams.halo))
        direction = seams.uv_direction(tuple(effect.direction)) if effect.kind in {"DIRECTIONAL_BLUR", "DIRECTIONAL_WARP"} else None
        filtered = _apply_per_island(seams, original, halo, effect, params, direction)
    else:
        filtered = filters.apply(original, effect.kind, **params)
    if original.ndim == 2 and filtered.ndim == 3:
        original = original[..., None]
    elif original.ndim == 3 and filtered.ndim == 2:
        filtered = filtered[..., None]
    result = original + (filtered - original) * effect.strength
    if alpha is not None:
        if result.shape[-1] == 1:
            result = np.repeat(result, 3, axis=-1)
        result = np.concatenate((result, alpha), axis=-1)
    return np.asarray(result, np.float32)


def _apply_per_island(seams, original, halo, effect, params, direction):
    """Per-island seam-aware filtering; warp fields are evaluated on each crop's own UVs."""
    result = np.array(original, np.float32, copy=True)
    n = seams.n
    if not seams.bounds:
        return filters.apply(original, effect.kind, **params)
    base = seams.pad(original, halo)
    for island in seams.bounds:
        padded, (r0, r1, c0, c1) = seams.island_crop(base, island, halo)
        local = dict(params)
        if direction is not None:
            local["direction_field"] = direction[r0:r1, c0:c1]
        if effect.kind in {"WARP", "SLOPE_BLUR", "DIRECTIONAL_WARP", "VECTOR_WARP", "UV_DISTORTION"}:
            y, x = np.indices((r1 - r0, c1 - c0), dtype=np.float32)
            u, v = (x + c0 + .5) / n, (y + r0 + .5) / n
            noise = getattr(effect, "noise", "FBM")
            scale, seed = getattr(effect, "scale", 8), getattr(effect, "seed", 1)
            field = procedurals.evaluate(noise, u, v, seed=seed, scale=scale)
            if effect.kind in {"VECTOR_WARP", "UV_DISTORTION"}:
                field = np.stack((field, procedurals.evaluate(noise, u, v, seed=seed + 37, scale=scale)), axis=-1)
            local["field"] = field
        crop = filters.apply(padded, effect.kind, **local)
        mine = seams.island_of[r0:r1, c0:c1] == island
        if crop.ndim == 3 and result.ndim == 2:
            result = np.repeat(result[..., None], crop.shape[-1], axis=-1)
        elif crop.ndim == 2 and result.ndim == 3:
            crop = np.repeat(crop[..., None], result.shape[-1], axis=-1)
        result[r0:r1, c0:c1][mine] = crop[mine]
    return result


def apply_texture_effects(data, effects, seams=None):
    for effect in effects:
        data = apply_effect(data, effect, seams)
    return data


def _coordinates(prop, n, y0, y1):
    u, v = np.meshgrid((np.arange(n, dtype=np.float32) + .5) / n,
                       (np.arange(y0, y1, dtype=np.float32) + .5) / n)
    u, v = (u - .5) * prop.scale[0], (v - .5) * prop.scale[1]
    cs, sn = np.cos(prop.rotation), np.sin(prop.rotation)
    return cs * u - sn * v + .5 + prop.offset[0], sn * u + cs * v + .5 + prop.offset[1]


def _procedural_block(prop, n, y0, y1, ts, geometry_cache):
    """Cache source scalar by coordinates, excluding the destination channel."""
    from . import material_space
    key = ("procedural", material_space.identity(prop), getattr(ts, "id", ""),
           getattr(geometry_cache, "geometry_stamp", None) if prop.projection == "TRIPLANAR" or material_space.enabled(prop) else None,
           n, y0, y1, prop.procedural, prop.procedural_scale, prop.procedural_seed,
           prop.projection, tuple(prop.scale), prop.rotation, tuple(prop.offset),
           prop.triplanar_hardness)
    if key not in geometry_cache:
        if material_space.enabled(prop):
            result = material_space.block(prop, n, y0, y1, ts, geometry_cache)
        elif prop.projection == "TRIPLANAR":
            from ..painting.projection import geometry
            if "geometry" in geometry_cache:
                positions, normals = geometry_cache["geometry"]
            else:
                positions, normals = geometry(ts, n)
                geometry_cache["geometry"] = (positions, normals)
            positions, normals = positions[y0:y1], normals[y0:y1]
            weights = np.abs(normals)
            weights = (weights / np.maximum(weights.max(axis=-1, keepdims=True), 1e-8)) ** prop.triplanar_hardness
            weights /= np.maximum(weights.sum(axis=-1, keepdims=True), 1e-8)
            result = np.zeros((y1 - y0, n), np.float32)
            cs, sn = np.cos(prop.rotation), np.sin(prop.rotation)
            for axis, (a, b) in enumerate(((1, 2), (0, 2), (0, 1))):
                if not np.any(weights[..., axis] > 1e-8):
                    continue
                u, v = positions[..., a] * prop.scale[0], positions[..., b] * prop.scale[1]
                u, v = cs * u - sn * v + prop.offset[0], sn * u + cs * v + prop.offset[1]
                result += procedurals.evaluate(prop.procedural, u, v, seed=prop.procedural_seed,
                                                scale=prop.procedural_scale) * weights[..., axis]
        else:
            u, v = _coordinates(prop, n, y0, y1)
            result = procedurals.evaluate(prop.procedural, u, v, seed=prop.procedural_seed,
                                           scale=prop.procedural_scale)
        geometry_cache[key] = result
        return result
    return geometry_cache[key]


def _procedural_tile(prop, n, y0, y1, ts, geometry_cache):
    # Canonical row blocks allow full-raster filters, brush regions and preview
    # jobs to share exactly the same raw source cache instead of duplicating it.
    parts = []
    for low in range(y0 // TILE_ROWS * TILE_ROWS, y1, TILE_ROWS):
        high = min(low + TILE_ROWS, n)
        value = _procedural_block(prop, n, low, high, ts, geometry_cache)
        parts.append(value[max(y0 - low, 0):min(y1 - low, high - low)])
    return parts[0] if len(parts) == 1 else np.concatenate(parts)


def _detail_pixels(image, resources):
    """Keep one float32 luminance plane, rather than four decoded components.

    Linear sampling and luminance commute. Reducing the source once keeps the
    same information needed by detail fills while avoiding an RGBA source copy
    and RGB temporaries for every sampled strip. Authored images stay untouched.
    """
    from .image_snapshots import read
    from .resource_keys import image_key
    key = ("decoded_detail_scalar", image_key(image))
    if key in resources:
        count("detail_decoded_reuses")
        return resources[key]
    # A plain cache requests a transient decoded image, never an RGBA entry in
    # the persistent image-snapshot cache. Only the scalar result is retained.
    rgba = read({}, image, linear=True)
    scalar = rgba[..., 0] * np.float32(.2126)
    scalar += rgba[..., 1] * np.float32(.7152)
    scalar += rgba[..., 2] * np.float32(.0722)
    scalar = scalar[..., None]
    scalar.flags.writeable = False
    resources[key] = scalar
    count("detail_decoded_bytes", scalar.nbytes)
    return scalar


@measured("source_generation")
def _sample_detail(image, prop, n, low, high, ts, geometry_cache):
    """One actual scalar sampling operation; cache hits never enter this phase."""
    if prop.projection == 'DECAL':
        from ..painting import decals
        layer = next((item for item in ts.layers if item.id == getattr(prop, 'layer_id', '')), None)
        if layer is None:
            return np.full((high-low, n), .5, np.float32)
        rgba = decals.sample(prop, layer, n, low, high, ts, geometry_cache, {}, image=image)
        return np.broadcast_to(rgba, (high-low, n, 4))[..., :3] @ np.array([.2126, .7152, .0722], np.float32)
    scalar = _detail_pixels(image, geometry_cache)
    value = np.asarray(_sample_fill(scalar, prop, n, low, high, ts, geometry_cache)[..., 0],
                       dtype=np.float32)
    count("detail_sampled_strips")
    count("detail_sampled_pixels", value.size)
    count("detail_sampled_bytes", value.nbytes)
    return value


def _detail_tile(prop, n, y0, y1, ts, geometry_cache):
    """Sample a shared scalar detail image without baking channel-specific files.

    The image is linear data; RGB inputs are reduced to luminance. Both decoded
    pixels and canonical scalar strips are revisioned independently of color,
    roughness and height controls. Existing UV and triplanar sampling remains the
    reference, including filtering, rotation and repeat/transparent borders.
    """
    from .resource_keys import image_key
    image = getattr(prop, "detail_image", None)
    if image is None:
        raise ValueError(f"Surface detail image is missing: {getattr(prop, 'detail_image_name', '')}. "
                         "Reload or clear the surface detail texture.")
    from .material_space import identity as material_identity
    identity = ("detail_scalar", material_identity(prop), getattr(ts, "id", ""), image_key(image), n,
                prop.projection, prop.filtering, prop.tiling, tuple(prop.scale),
                prop.rotation, tuple(prop.offset), prop.triplanar_hardness,
                getattr(geometry_cache, "geometry_stamp", None)
                if prop.projection in {"TRIPLANAR", "DECAL"} else None)
    if prop.projection == 'DECAL':
        from ..painting import decals
        layer = next((item for item in ts.layers if item.id == getattr(prop, 'layer_id', '')), None)
        identity += (decals.signature(layer) if layer is not None else None,)
    parts = []
    for low in range(y0 // TILE_ROWS * TILE_ROWS, y1, TILE_ROWS):
        high = min(low + TILE_ROWS, n)
        key = (*identity, low, high)
        if key not in geometry_cache:
            value = _sample_detail(image, prop, n, low, high, ts, geometry_cache)
            geometry_cache[key] = value
        else:
            value = geometry_cache[key]
            count("detail_reused_strips")
        parts.append(value[max(y0 - low, 0):min(y1 - low, high - low)])
    return parts[0] if len(parts) == 1 else np.concatenate(parts)


def _field_tile(prop, n, y0, y1, ts, geometry_cache):
    if getattr(prop, "detail_image", None) or getattr(prop, "detail_image_name", ""):
        return _detail_tile(prop, n, y0, y1, ts, geometry_cache)
    return _procedural_tile(prop, n, y0, y1, ts, geometry_cache)


def _raw_source(prop, kind, n, y0, y1, cache, ts, geometry_cache):
    rgba = _base_source(prop, kind, n, y0, y1, cache, ts, geometry_cache)
    # Mask fill properties also have a `procedural` field, but an image fill must
    # remain an image fill. Their dedicated PROCEDURAL source is evaluated by masks.
    metric_height_image = (kind == "FILL" and getattr(prop, "channel", "") == "HEIGHT"
                           and getattr(prop, "material_mapping_version", 0) == 1 and prop.image)
    if kind != "FILL" or not hasattr(prop, "channel") or not (
            getattr(prop, "procedural", "") or getattr(prop, "detail_image", None)
            or getattr(prop, "detail_image_name", "") or metric_height_image):
        return rgba
    scalar = rgba[..., 0] if metric_height_image else _field_tile(prop, n, y0, y1, ts, geometry_cache)
    out = np.broadcast_to(rgba, (y1 - y0, n, 4)).copy()
    if prop.channel == "HEIGHT":
        out[..., :3] = (.5 + (scalar - .5) * height_amplitude(prop, ts))[..., None]
    elif prop.channel in {"BASE_COLOR", "EMISSION"}:
        out[..., :3] *= (1 + (scalar - .5) * prop.procedural_detail)[..., None]
    elif prop.channel == "NORMAL":
        # A one-pixel scalar halo gives the same centered derivatives as a full
        # raster while avoiding a full-resolution RGB normal cache at 8K.
        parts = []
        if y0:
            parts.append(_field_tile(prop, n, y0 - 1, y0, ts, geometry_cache))
        parts.append(scalar)
        if y1 < n:
            parts.append(_field_tile(prop, n, y1, y1 + 1, ts, geometry_cache))
        height = np.concatenate(parts)
        normal = filters.height_to_normal(height, prop.procedural_height * n)
        start = 1 if y0 else 0
        out[..., :3] = filters.normal_combine(out[..., :3], normal[start:start + y1 - y0])
    else:
        out[..., :3] += ((scalar - .5) * prop.procedural_detail * .35)[..., None]
    if prop.projection == "UV" and prop.tiling == "NONE":
        u, v = _coordinates(prop, n, y0, y1)
        out[..., 3] *= (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)
    out[..., :3] = np.clip(out[..., :3], 0, 1)
    return out


def source(prop, kind, n, y0, y1, cache, ts, geometry_cache):
    effects = tuple(effect for effect in getattr(prop, "effects", ()) if effect.enabled and effect.strength > 0)
    if not effects:
        return _raw_source(prop, kind, n, y0, y1, cache, ts, geometry_cache)
    if not any(effect.kind in NEIGHBORHOOD_EFFECTS for effect in effects):
        # Levels, hue and other pointwise operations remain cooperative and need
        # neither a full-resolution temporary nor a second whole-image cache.
        rgba = _raw_source(prop, kind, n, y0, y1, cache, ts, geometry_cache)
        return apply_texture_effects(np.broadcast_to(rgba, (y1 - y0, n, 4)), effects)
    from .operation_contracts import stack_footprint
    from .tiles import expand
    halo = stack_footprint(effects)
    # The pixel cache belongs to the current channel evaluation; keep filtered
    # RGBA here rather than retaining every layer/channel result in geometry cache.
    # Anchor prefixes share the owner's pointer, so the effect subset is part of the key.
    key = ("filtered_channel", prop.as_pointer(), tuple(e.as_pointer() for e in effects), n,
           getattr(ts, "edit_serial", 0))
    if halo is not None:
        # The compositor sweeps 16-row tiles: filtering (16 + 2·halo) rows for
        # each was up to 25x the image. Aligned strips are filtered once and only
        # the current one stays in the cache, so temporaries remain bounded.
        rows = band_rows(halo, n)
        parts = []
        for strip_low in range(y0 // rows * rows, y1, rows):
            strip_high = min(strip_low + rows, n)
            strip = (*key, strip_low, strip_high)
            if strip not in cache:
                for stale in [k for k in cache if isinstance(k, tuple) and k[:len(key)] == key]:
                    del cache[stale]
                low, high = expand(strip_low, strip_high, halo, n)
                raster = np.empty((high - low, n, 4), np.float32)
                for start in range(low, high, TILE_ROWS):
                    stop = min(start + TILE_ROWS, high)
                    raster[start - low:stop - low] = _raw_source(prop, kind, n, start, stop, cache, ts, geometry_cache)
                cache[strip] = apply_texture_effects(raster, effects)[strip_low - low:strip_high - low]
            parts.append(cache[strip][max(y0 - strip_low, 0):min(y1, strip_high) - strip_low])
        return parts[0] if len(parts) == 1 else np.concatenate(parts)
    if key not in cache:
        raster = np.empty((n, n, 4), np.float32)
        for low in range(0, n, TILE_ROWS):
            high = min(low + TILE_ROWS, n)
            raster[low:high] = _raw_source(prop, kind, n, low, high, cache, ts, geometry_cache)
        cache[key] = apply_texture_effects(raster, effects, seam_map_for(ts, n, effects))
    return cache[key][y0:y1]


def _base_source(prop, kind, n, y0, y1, cache, ts, geometry_cache):
    if getattr(prop, "image_name", "") and not prop.image:
        raise ValueError(
            f"Texture image is missing: {prop.image_name}. Reload or clear the channel texture."
        )
    if kind == "FILL" and getattr(prop, "anchor", ""):
        from .references import evaluate_anchor
        value = evaluate_anchor(ts, prop.anchor, n, y0, y1, cache, geometry_cache,
                                channel=getattr(prop, 'anchor_channel', '') or prop.channel)
        if value is None:
            return np.array([0, 0, 0, 0], dtype=np.float32)  # Missing anchor contributes nothing.
        if value.ndim == 2:
            return np.concatenate((np.repeat(value[..., None], 3, axis=-1),
                                   np.ones((y1 - y0, n, 1), np.float32)), axis=-1)
        return value
    if kind == "PAINT":
        layer = next((item for item in ts.layers if item.id == getattr(prop, "layer_id", "")), None)
        key = getattr(layer, "stroke_key", "") if layer is not None else ""
        if key:
            from ..painting.stroke_raster import channel_rows
            base = (_image_source(prop.image, prop, kind, n, y0, y1, cache, ts, geometry_cache) if prop.image
                    else None)
            if base is not None:
                base = np.broadcast_to(base, (y1 - y0, n, 4))
            return channel_rows(ts, layer, prop.channel, n, y0, y1, geometry_cache, base=base, cache=cache)
    if kind == "FILL" and getattr(prop, "projection", "UV") == "DECAL":
        # Decal objects (0.9.6): the layer's Empty is the projection frame.
        from ..painting import decals
        layer = next((item for item in ts.layers if item.id == getattr(prop, "layer_id", "")), None)
        if layer is None or (decals.resolve(layer) is None and not getattr(layer, 'decal_inline', False)) or not (prop.image or prop.detail_image):
            return np.array([0, 0, 0, 0], dtype=np.float32)
        if not prop.image:
            rgba = decals.sample(prop, layer, n, y0, y1, ts, geometry_cache if geometry_cache is not None else {}, cache, image=prop.detail_image)
            rgba[..., :3] = prop.color[:3] if prop.channel in {'BASE_COLOR', 'NORMAL', 'EMISSION'} else prop.value
            return rgba
        return decals.sample(prop, layer, n, y0, y1, ts, geometry_cache if geometry_cache is not None else {}, cache)
    if prop.image:
        return _image_source(prop.image, prop, kind, n, y0, y1, cache, ts, geometry_cache)
    if kind == "PAINT":
        return np.array([0, 0, 0, 0], dtype=np.float32)
    if prop.channel in {"BASE_COLOR", "NORMAL", "EMISSION"}:
        return np.array(prop.color, dtype=np.float32)
    return np.array([prop.value, prop.value, prop.value, 1], dtype=np.float32)


def _image_source(image, prop, kind, n, y0, y1, cache, ts, geometry_cache):
    """Shared image sampling for regular fills, paint and scalar detail inputs."""
    if kind == 'FILL' and getattr(prop, 'projection', 'UV') == 'DECAL':
        from ..painting import decals
        layer = next((item for item in ts.layers if item.id == getattr(prop, 'layer_id', '')), None)
        if layer is not None:
            return decals.sample(prop, layer, n, y0, y1, ts, geometry_cache if geometry_cache is not None else {}, cache, image=image)
    if image:
        from .image_snapshots import associated, read
        key = (image.as_pointer(), "linear")
        if key not in cache:
            cache[key] = read(cache, image, linear=True)
        if kind == "PAINT" and cache[key].shape[:2] == (n, n):
            # Paint pixels already occupy the output UV grid. Resampling four
            # neighbours per pixel was the dominant cost of every brush update.
            rgba = cache[key][y0:y1].copy()
            if image.is_float and not associated(image):
                np.divide(
                    rgba[..., :3], rgba[..., 3:4], out=rgba[..., :3], where=rgba[..., 3:4] > 1e-8
                )
                rgba[..., :3][rgba[..., 3] <= 1e-8] = 0
            return rgba
        if kind == "FILL":
            return _sample_fill(cache[key], prop, n, y0, y1, ts, geometry_cache)
        u, v = np.meshgrid(
            (np.arange(n, dtype=np.float32) + 0.5) / n,
            (np.arange(y0, y1, dtype=np.float32) + 0.5) / n,
        )
        rgba = sample_uv(cache[key], u, v)
        if kind == "PAINT" and image.is_float and not associated(image):
            # Blender's native float brush buffers are premultiplied (a file-backed
            # canvas is already divided once by the reader).
            rgba[..., :3] = np.divide(
                rgba[..., :3],
                rgba[..., 3:4],
                out=np.zeros_like(rgba[..., :3]),
                where=rgba[..., 3:4] > 1e-8,
            )
        return rgba


def _sample_fill(texture, prop, n, y0, y1, ts, geometry_cache):
    """Identical texture projection for RGBA fills and single-plane detail."""
    from . import material_space
    if material_space.enabled(prop):
        return material_space.sample_image(texture, prop, n, y0, y1, ts, geometry_cache)
    if prop.projection == "TRIPLANAR":
        from ..painting.projection import geometry, triplanar
        if "geometry" in geometry_cache:
            positions, normals = geometry_cache["geometry"]
        else:
            positions, normals = geometry(ts, n)
            geometry_cache["geometry"] = (positions, normals)
        return triplanar(texture, positions[y0:y1], normals[y0:y1], prop)
    u, v = _coordinates(prop, n, y0, y1)
    return sample_uv(texture, u, v, prop.filtering, prop.tiling)
