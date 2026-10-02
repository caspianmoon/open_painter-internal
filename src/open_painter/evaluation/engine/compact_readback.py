"""Expand native R/RG transfers without repeatedly streaming a whole RGBA image."""

import numpy as np

_BLOCK_PIXELS = 8192


def expand_compact(transfer, rgba, components, gray=False):
    """Copy native floats into a borrowed RGBA buffer, with missing RGB=0, A=1; ``gray`` (R only)
    repeats R in G and B instead: the (v, v, v, 1) of a scalar channel's image.

    The native kernel (op_expand_compact) makes the same copies with SSE or NEON shuffles.
    Without it, small blocks keep the four column writes near each other in CPU cache. The
    source and destination must be separate, equally sized arrays of pixels. Assignment
    preserves each source float bit, including signed zero and NaNs.
    """
    if components not in (1, 2) or (gray and components != 1):
        raise ValueError('Compact expansion needs R or RG, and gray needs R')
    source = np.frombuffer(transfer, np.float32).reshape(-1, components)
    target = np.frombuffer(rgba, np.float32).reshape(-1, 4)
    if len(source) != len(target):
        raise ValueError('Compact and RGBA buffers must hold the same pixel count')
    if np.shares_memory(source, target):
        raise ValueError('Compact source and RGBA destination must not overlap')
    from ...native_bridge.lifecycle import cpu_library
    lib = cpu_library()
    if lib is not None and hasattr(lib, 'op_expand_compact'):
        from ...native_bridge.pixels import expand_compact as native
        native(lib, source, target, components, gray)
        return rgba
    _expand(source, target, components, gray)
    return rgba


def _expand(source, target, components, gray=False):
    for low in range(0, len(source), _BLOCK_PIXELS):
        src = source[low:low + _BLOCK_PIXELS]
        dst = target[low:low + _BLOCK_PIXELS]
        dst[:, 0] = src[:, 0]
        dst[:, 1] = src[:, 1] if components == 2 else src[:, 0] if gray else 0.0
        dst[:, 2] = src[:, 0] if gray else 0.0
        dst[:, 3] = 1.0
