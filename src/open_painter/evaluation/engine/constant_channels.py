"""Prove when one channel can use the same GPU program at a single texel.

Unlike ``uniforms.uniform_channel``, this proves spatial constancy, not an exact
RGBA value. The ordinary shaders must still evaluate all layer intermediates
and the final publication pass, with their original half/full-float formats.
That preserves authored colors, alpha composition, normal normalization and
rounding without duplicating the compositor on the CPU.

Call this again after every edit. It deliberately accepts only plain root FILL
stacks with NORMAL composition and no spatial source, mask, effect or cache.
"""

from math import isfinite

from ...constants import DEFAULTS
from .formats import SCALAR_CHANNELS


def _bounded(value, low, high):
    return isfinite(value) and low <= value <= high


def spatially_constant_channel(ts, channel):
    """Whether a channel's complete stack is independent of UV and resolution.

    The result authorizes a one-texel GPU evaluation only. It does not authorize
    replacing intermediate quantization, changing image color space, or skipping
    the final publication shader. Other channels can contain spatial content;
    the accepted channel cannot read it through an anchor or other source.

    Disabled target channels and DISABLE blends are absent from the compiler's
    root chain. Hidden layers remain in that chain, so their content is checked.
    Numeric limits match the authored RNA values and keep NORMAL interpolation
    finite in both intermediate precisions, including unclamped scalar values.
    """
    if channel not in DEFAULTS:
        return False
    try:
        for layer in ts.layers:
            if (layer.type != 'FILL' or layer.parent_id or layer.bypass
                    or layer.cache_mode != 'LIVE' or len(layer.cache)
                    or layer.stroke_key or len(layer.masks)):
                return False
            props = [prop for prop in layer.channels if prop.channel == channel]
            if len(props) != 1:
                return False
            prop = props[0]
            if not prop.enabled:
                continue
            mode = layer.blend_mode if prop.blend_mode == 'INHERIT' else prop.blend_mode
            if mode == 'DISABLE':
                continue
            if mode != 'NORMAL':
                return False
            if (prop.projection != 'UV' or prop.tiling != 'REPEAT'
                    or prop.material_mapping_version != 0 or prop.anchor
                    or prop.image is not None or prop.image_name
                    or prop.detail_image is not None or prop.detail_image_name
                    or prop.procedural or len(prop.effects)):
                return False
            if not all(_bounded(value, 0.0, 1.0)
                       for value in (layer.opacity, layer.visible, prop.opacity)):
                return False
            if channel in SCALAR_CHANNELS:
                if not _bounded(prop.value, -1.0, 10.0):
                    return False
            elif len(prop.color) != 4 or not all(_bounded(value, 0.0, 1.0) for value in prop.color):
                return False
    except (AttributeError, ReferenceError, TypeError, ValueError, OverflowError):
        return False
    return True
