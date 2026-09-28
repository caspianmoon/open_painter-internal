"""Expand native R/RG transfers without repeatedly streaming a whole RGBA image."""

import numpy as np

_BLOCK_PIXELS = 8192


def expand_compact(transfer, rgba, components):
    """Copy native floats into a borrowed RGBA buffer, with missing RGB=0, A=1.

    Small blocks keep the four column writes near each other in CPU cache. The
    source and destination must be separate, equally sized arrays of pixels.
    Assignment preserves each source float bit, including signed zero and NaNs.
    """
    if components not in (1, 2):
        raise ValueError('Compact expansion needs R or RG')
    source = np.frombuffer(transfer, np.float32).reshape(-1, components)
    target = np.frombuffer(rgba, np.float32).reshape(-1, 4)
    if len(source) != len(target):
        raise ValueError('Compact and RGBA buffers must hold the same pixel count')
    if np.shares_memory(source, target):
        raise ValueError('Compact source and RGBA destination must not overlap')
    for low in range(0, len(source), _BLOCK_PIXELS):
        src = source[low:low + _BLOCK_PIXELS]
        dst = target[low:low + _BLOCK_PIXELS]
        dst[:, 0] = src[:, 0]
        dst[:, 1] = src[:, 1] if components == 2 else 0.0
        dst[:, 2] = 0.0
        dst[:, 3] = 1.0
    return rgba
