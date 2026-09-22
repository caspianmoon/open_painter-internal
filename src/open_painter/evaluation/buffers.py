"""Select scalar working storage only when all three stored components agree.

Authored RGB images and color-producing effects retain the reference RGB path,
even on scalar channels: reducing those would change saved output components.
"""
SCALAR_CHANNELS = {'ROUGHNESS', 'METALLIC', 'HEIGHT', 'AO', 'OPACITY'}
RGB_MODES = {'HUE', 'SATURATION', 'COLOR', 'LUMINOSITY', 'NORMAL_COMBINE',
             'RNM', 'NORMAL_ADD', 'NORMAL_REPLACE'}
SCALAR_EFFECTS = set('''LEVELS HISTOGRAM_SCAN HISTOGRAM_RANGE
BRIGHTNESS_CONTRAST GAMMA INVERT THRESHOLD CLAMP POSTERIZE QUANTIZE
ROUGHNESS_REMAP METALLIC_REMAP HEIGHT_REMAP AO_STRENGTH ROUGHNESS_CONTRAST
BLUR GAUSSIAN_BLUR HIGH_PASS SHARPEN SMOOTH_MASK DILATE ERODE
MORPHOLOGICAL_EXPAND MORPHOLOGICAL_CONTRACT ANCHOR'''.split())


def scalar_compatible(channel, layers, properties):
    from .operation_contracts import stack_footprint
    if channel not in SCALAR_CHANNELS:
        return False
    for layer in layers:
        prop = properties[layer.id]
        if not layer.visible or (prop and not prop.enabled):
            continue
        mode = prop.blend_mode if prop and prop.blend_mode != 'INHERIT' else layer.blend_mode
        if mode in RGB_MODES:
            return False
        if prop and (prop.image or prop.image_name or getattr(prop, 'anchor', '')
                     or getattr(layer, 'stroke_key', '')
                     or getattr(prop, 'projection', 'UV') == 'DECAL'   # a decal covers its footprint only
                     or any(e.enabled and e.strength > 0 and e.kind not in SCALAR_EFFECTS for e in prop.effects)
                     # Surface-space filters need the whole image and the seam map,
                     # which only the reference path provides.
                     or stack_footprint(prop.effects) is None):
            return False
    return True


def scalar_source(prop, kind, n, y0, y1, ts, geometry_cache):
    """Scalar fill plus separate coverage; called only after compatibility proof."""
    import numpy as np
    from .channel_eval import _field_tile, _coordinates, apply_texture_effects
    from .operation_contracts import stack_footprint
    from .tiles import expand
    if kind == 'PAINT':  # An image-bearing paint source fails compatibility.
        return np.float32(0), np.float32(0)
    effects = tuple(e for e in prop.effects if e.enabled and e.strength > 0)
    low, high = expand(y0, y1, stack_footprint(effects), n)
    value = np.float32(prop.value)
    has_field = (prop.procedural or getattr(prop, 'detail_image', None)
                 or getattr(prop, 'detail_image_name', ''))
    if has_field:
        noise = _field_tile(prop, n, low, high, ts, geometry_cache)
        if prop.channel == 'HEIGHT':
            from .material_space import height_amplitude
            value = .5 + (noise - .5) * height_amplitude(prop, ts)
        else:
            value = value + (noise - .5) * prop.procedural_detail * .35
        value = np.clip(value, 0, 1)
    value = np.broadcast_to(value, (high - low, n))
    value = apply_texture_effects(value, effects)[y0 - low:y1 - low, :, None]
    coverage = np.float32(1)
    if has_field and prop.projection == 'UV' and prop.tiling == 'NONE':
        u, v = _coordinates(prop, n, y0, y1)
        coverage = ((u >= 0) & (u <= 1) & (v >= 0) & (v <= 1))[..., None]
    return value, coverage
