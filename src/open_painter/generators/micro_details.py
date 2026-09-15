"""Micro details: anchored height or normal relief that generators wear and soil.

Substance generators accept "Micro Height" and "Micro Normal" inputs so painted
rivets, panel lines and scratches receive edge wear and dirt like baked geometry.
Convexity is measured over a small texel radius: the encoded rasters are strength
independent, so intensity sliders stay live uniforms in the shader path.
"""
from math import ceil

import numpy as np

from ..texture_engine.filters import gaussian_blur, _gradient

KINDS = frozenset({'EDGE_WEAR', 'CHIPS', 'DIRT', 'CAVITY_DIRT', 'RUST', 'DRIPS'})
HEIGHT_GAIN, NORMAL_GAIN = 8.0, 2.5


def anchors(mask):
    """((slot, anchor id), ...) for filled slots of a participating generator mask."""
    if getattr(mask, 'source', '') != 'GENERATOR' or getattr(mask, 'generator', '') not in KINDS:
        return ()
    return tuple((slot, identifier) for slot, identifier in (
        ('HEIGHT', getattr(mask, 'detail_height_anchor', '')),
        ('NORMAL', getattr(mask, 'detail_normal_anchor', ''))) if identifier)


def halo(mask):
    return int(ceil(3 * float(getattr(mask, 'detail_radius', 2.0)))) + 1


def height_field(rgba):
    """Composite over the neutral height so unpainted texels read as flat."""
    return .5 + (rgba[..., 0] - .5) * rgba[..., 3]


def slope_field(rgba):
    alpha = rgba[..., 3]
    nz = np.maximum((rgba[..., 2] * 2 - 1) * alpha + (1 - alpha), .05)
    return (rgba[..., 0] * 2 - 1) * alpha / nz, (rgba[..., 1] * 2 - 1) * alpha / nz


def encode(height, slopes, sigma):
    """Signed convexity per slot, 0.5-centred: red from height, green from normal slopes."""
    shape = (height if height is not None else slopes[0]).shape
    out = np.full((*shape, 3), .5, np.float32)
    if height is not None:
        height = np.asarray(height, np.float32)
        out[..., 0] = np.clip((height - gaussian_blur(height, sigma)) * HEIGHT_GAIN, -1, 1) * .5 + .5
    if slopes is not None:
        # Divergence of the tangent slopes is the negative Laplacian of the
        # relief under height_to_normal's convention, matching the height sign.
        divergence = _gradient(np.asarray(slopes[0], np.float32))[0] + _gradient(np.asarray(slopes[1], np.float32))[1]
        out[..., 1] = np.clip(gaussian_blur(divergence, sigma) * (NORMAL_GAIN * sigma), -1, 1) * .5 + .5
    return out


def combine(encoded, height_strength, normal_strength):
    """(convex, cavity) coverage from an encoded raster and live intensities."""
    signed = ((encoded[..., 0] - .5) * (2 * height_strength)
              + (encoded[..., 1] - .5) * (2 * normal_strength))
    return np.clip(signed, 0, 1), np.clip(-signed, 0, 1)


def fields(ts, mask, n, low, high, cache, resources):
    """Height rows and slope rows of the anchored inputs; missing anchors are skipped."""
    from ..evaluation.references import evaluate_anchor
    height = slopes = None
    for slot, identifier in anchors(mask):
        field = None
        for start in range(low, high, 64):
            stop = min(start + 64, high)
            value = evaluate_anchor(ts, identifier, n, start, stop, cache, resources, channel=slot)
            if value is None or (slot == 'NORMAL' and value.ndim == 2):
                field = None
                break
            if field is None:
                field = np.empty((high - low, n) if slot == 'HEIGHT' else (2, high - low, n), np.float32)
            if slot == 'HEIGHT':
                field[start - low:stop - low] = value if value.ndim == 2 else height_field(value)
            else:
                field[0, start - low:stop - low], field[1, start - low:stop - low] = slope_field(value)
        if field is None:
            continue
        if slot == 'HEIGHT':
            height = field
        else:
            slopes = (field[0], field[1])
    return height, slopes


def evaluate(ts, mask, rows, cache, resources):
    """(convex, cavity) for the requested rows, or None when no anchor resolves."""
    from ..evaluation.tiles import expand
    n, y0, y1 = rows
    low, high = expand(y0, y1, halo(mask), n)
    height, slopes = fields(ts, mask, n, low, high, cache, resources)
    if height is None and slopes is None:
        return None
    encoded = encode(height, slopes, float(getattr(mask, 'detail_radius', 2.0)))[y0 - low:y1 - low]
    return combine(encoded, float(getattr(mask, 'detail_height_strength', 1.0)),
                   float(getattr(mask, 'detail_normal_strength', 1.0)))
