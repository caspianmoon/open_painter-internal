"""Proven CPU filter support in pixels; None requires whole-image evaluation.

These bounds describe the current clamped filter implementation, not texture
projection wrapping. Coordinate-dependent warps keep a conservative fallback.
"""
from math import ceil

POINTWISE = frozenset('''LEVELS HISTOGRAM_SCAN HISTOGRAM_RANGE
BRIGHTNESS_CONTRAST GAMMA INVERT THRESHOLD CLAMP POSTERIZE QUANTIZE HSL HSV
HUE_SHIFT SATURATION COLOR_BALANCE GRADIENT_MAP COLORIZE TINT TEMPERATURE
DESATURATE REPLACE_COLOR ROUGHNESS_REMAP METALLIC_REMAP HEIGHT_REMAP
NORMAL_STRENGTH NORMAL_INVERT_Y AO_STRENGTH ROUGHNESS_CONTRAST CHANNEL_MIXER ANCHOR'''.split())


SURFACE_KINDS = frozenset("""GAUSSIAN_BLUR BLUR HIGH_PASS SHARPEN SMOOTH_MASK DILATE ERODE MORPHOLOGICAL_EXPAND MORPHOLOGICAL_CONTRACT EDGE_DETECT HEIGHT_TO_NORMAL DIRECTIONAL_BLUR SLOPE_BLUR WARP DIRECTIONAL_WARP VECTOR_WARP UV_DISTORTION""".split())


def surface(effect):
    """True when the effect runs in Surface space (seam-aware, whole-image evaluation)."""
    return getattr(effect, 'space', 'UV') == 'SURFACE' and effect.kind in SURFACE_KINDS


def footprint(kind, radius=2, space='UV'):
    if kind in POINTWISE:
        return 0
    if space == 'SURFACE' and kind in SURFACE_KINDS:
        return None   # cross-seam padding needs the whole image
    if kind in {'GAUSSIAN_BLUR', 'HIGH_PASS', 'SHARPEN', 'SMOOTH_MASK'}:
        return min(ceil(max(0, radius) * 3), 192)
    if kind in {'BLUR', 'DILATE', 'ERODE', 'MORPHOLOGICAL_EXPAND', 'MORPHOLOGICAL_CONTRACT'}:
        return min(ceil(max(0, radius)), 192)
    if kind in {'EDGE_DETECT', 'HEIGHT_TO_NORMAL'}:
        return 1
    return None


def stack_footprint(effects):
    total = 0
    for effect in effects:
        if not effect.enabled or effect.strength <= 0:
            continue
        radius = footprint(effect.kind, effect.radius, getattr(effect, 'space', 'UV'))
        if radius is None:
            return None
        total += radius
    return total


def mask_footprint(masks):
    from .anchors import consumed
    if any(m.enabled and (m.source == 'REFERENCE' or consumed(m)) for m in masks):
        return None  # Source-stage filters may widen the dependency transitively.
    return stack_footprint(m.effect for m in masks
                           if m.enabled and m.strength > 0 and m.source == 'FILTER')
