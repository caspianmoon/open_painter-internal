"""Conservative proofs of spatially uniform final channel values.

This is deliberately not a second compositor. It recognizes only the exact
default backdrop, followed by plain root fills of that same default with NORMAL
blending. All other cases keep the ordinary engine path. In particular, equal
arbitrary authored colours are insufficient: a retained half-float backdrop can
differ from a later float32 source before their masked interpolation.

Root channels start opaque, so shader_math.blend reduces to ``b + (s - b) * a``.
For the supported fills, ``s == b`` exactly in both intermediate precisions and
coverage is finite. Every layer therefore preserves the default. Final clamping,
normal normalization, seam padding and mip reduction also preserve these values.
The only admitted nonconstant mask is the bounded, ordinary UV CELLULAR source;
its values do not need evaluating to prove that the final colour is unchanged.

Call the proof again after any edit. No RNA identities or structural signatures
are cached here: a numeric edit can invalidate a previously uniform result.
"""

from math import isfinite

from ...constants import DEFAULTS
from .formats import SCALAR_CHANNELS


def _bounded(value, low, high):
    return isfinite(value) and low <= value <= high


def _vector_bounded(value, size, low, high):
    return len(value) == size and all(_bounded(v, low, high) for v in value)


def _finite_masks(masks):
    """Prove finite coverage without sampling textures or evaluating a graph.

    Bounds are intentionally narrower than RNA's most permissive mapping fields.
    They keep CELLULAR's transformed UVs well inside its float-to-int lattice
    range and avoid overflow before its levels clamp. Disabled simple entries
    are absent from the compiled mask, but groups/references stay unsupported.
    """
    for mask in masks:
        if mask.parent_id or mask.source in {'GROUP', 'ANCHOR', 'REFERENCE', 'FILTER'}:
            return False
        if not mask.enabled:
            continue
        if (mask.source != 'PROCEDURAL' or mask.procedural != 'CELLULAR'
                or mask.projection != 'UV' or mask.tiling != 'REPEAT'
                or mask.material_mapping_version != 0
                or mask.image is not None or mask.node_group is not None
                or mask.reference_anchor or mask.reference_layer or mask.reference_mask
                or mask.detail_height_anchor or mask.detail_normal_anchor or mask.stroke_key):
            return False
        if mask.operation not in {'NORMAL', 'MULTIPLY', 'ADD', 'MAX', 'MIN'}:
            return False
        if not all(_bounded(value, 0.0, 1.0)
                   for value in (mask.black, mask.white, mask.strength, mask.invert)):
            return False
        if (not _bounded(mask.noise_scale, 1.0, 512.0)
                or not _vector_bounded(mask.scale, 2, 0.001, 1024.0)
                or not _vector_bounded(mask.offset, 2, -1024.0, 1024.0)
                or not _bounded(mask.rotation, -10000.0, 10000.0)
                or not isinstance(mask.seed, int) or not 0 <= mask.seed <= 0x7fffffff):
            return False
    return True


def uniform_channel(ts, channel, precise=False):
    """Return proven final RGBA, or ``None`` to use normal engine evaluation.

    Supported: empty stacks, disabled channels, DISABLE channel blends, and
    default-valued root FILL layers using NORMAL. Sources must have no image,
    detail, procedural field, anchor or effect. Masks may be absent, disabled
    simple entries, or bounded UV CELLULAR masks. Folders, PAINT, cached/frozen
    layers, bypass, decal/mapped sources and unknown/malformed state fall back.

    ``precise`` matches the engine publication API. The accepted values (0, .5,
    1) are exactly representable in both half and full precision, so the proof
    is the same in either mode, including the always-full-precision HEIGHT.
    """
    if channel not in DEFAULTS:
        return None
    rgba = tuple(float(value) for value in DEFAULTS[channel])
    try:
        for layer in ts.layers:
            # Check structural/cache state even for a disabled channel. This
            # avoids reasoning about folder anchors or cached composite outputs.
            if (layer.type != 'FILL' or layer.parent_id or layer.bypass
                    or layer.cache_mode != 'LIVE' or len(layer.cache) or layer.stroke_key):
                return None
            props = [prop for prop in layer.channels if prop.channel == channel]
            if len(props) != 1:
                return None
            prop = props[0]
            if not prop.enabled:
                continue
            mode = layer.blend_mode if prop.blend_mode == 'INHERIT' else prop.blend_mode
            if mode == 'DISABLE':
                continue
            if mode != 'NORMAL':
                return None
            if (prop.projection != 'UV' or prop.tiling != 'REPEAT'
                    or prop.material_mapping_version != 0 or prop.anchor
                    or prop.image is not None or prop.image_name
                    or prop.detail_image is not None or prop.detail_image_name
                    or prop.procedural or len(prop.effects)):
                return None
            # Hidden layers still compile behind a zero visibility uniform;
            # do not skip their sources and accidentally conceal NaNs/errors.
            if not all(_bounded(value, 0.0, 1.0)
                       for value in (layer.opacity, layer.visible, prop.opacity)):
                return None
            if channel in SCALAR_CHANNELS:
                if prop.value != rgba[0]:
                    return None
            elif (len(prop.color) != 4 or tuple(prop.color[:3]) != rgba[:3]
                  or not _bounded(prop.color[3], 0.0, 1.0)):
                return None
            if not _finite_masks(layer.masks):
                return None
    except (AttributeError, ReferenceError, TypeError, ValueError, OverflowError):
        # A proof is optional. A missing field or stale RNA owner must never turn
        # an unknown stack into a guessed uniform result.
        return None
    return rgba
