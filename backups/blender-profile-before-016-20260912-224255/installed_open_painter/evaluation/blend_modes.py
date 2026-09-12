"""Pure NumPy blend operations in linear space."""

import numpy as np


def _divide(a, b):
    return np.divide(
        a,
        b,
        out=np.ones(np.broadcast_shapes(np.shape(a), np.shape(b)), dtype=np.float32),
        where=np.abs(b) > 1e-8,
    )


def _burn(base, source):
    return np.where(
        base >= 1, 1, np.where(source <= 0, 0, 1 - np.minimum(1, _divide(1 - base, source)))
    )


def _dodge(base, source):
    return np.where(
        base <= 0, 0, np.where(source >= 1, 1, np.minimum(1, _divide(base, 1 - source)))
    )


def blend(base, source, alpha, mode="NORMAL"):
    """Linear RGB. Standard separable modes follow W3C Compositing Level 1.

    Additional arithmetic modes are bounded except legacy ADD/SUBTRACT.
    No claim of pixel-identical Substance engine behavior is made.
    """
    if mode == "NORMAL":
        result = source
    elif mode == "MULTIPLY":
        result = base * source
    elif mode == "ADD":
        result = base + source
    elif mode == "SUBTRACT":
        result = base - source
    elif mode == "OVERLAY":
        result = np.where(base <= 0.5, 2 * base * source, 1 - 2 * (1 - base) * (1 - source))
    elif mode == "DISABLE":
        return base
    elif mode == "DIVIDE":
        result = np.clip(_divide(base, source), 0, 1)
    elif mode == "DARKEN":
        result = np.minimum(base, source)
    elif mode == "LIGHTEN":
        result = np.maximum(base, source)
    elif mode == "DIFFERENCE":
        result = np.abs(base - source)
    elif mode == "EXCLUSION":
        result = base + source - 2 * base * source
    elif mode == "SCREEN":
        result = 1 - (1 - base) * (1 - source)
    elif mode == "LINEAR_BURN":
        result = np.maximum(0, base + source - 1)
    elif mode == "COLOR_BURN":
        result = _burn(base, source)
    elif mode == "COLOR_DODGE":
        result = _dodge(base, source)
    elif mode == "SOFT_LIGHT":
        curve = np.where(
            base <= 0.25, ((16 * base - 12) * base + 4) * base, np.sqrt(np.maximum(base, 0))
        )
        result = np.where(
            source <= 0.5,
            base - (1 - 2 * source) * base * (1 - base),
            base + (2 * source - 1) * (curve - base),
        )
    elif mode == "HARD_LIGHT":
        result = np.where(source <= 0.5, 2 * base * source, 1 - 2 * (1 - base) * (1 - source))
    elif mode == "VIVID_LIGHT":
        result = np.where(source <= 0.5, _burn(base, 2 * source), _dodge(base, 2 * source - 1))
    elif mode == "LINEAR_LIGHT":
        result = np.clip(base + 2 * source - 1, 0, 1)
    elif mode == "PIN_LIGHT":
        result = np.where(
            source <= 0.5, np.minimum(base, 2 * source), np.maximum(base, 2 * source - 1)
        )
    else:
        raise ValueError(f"Unsupported blend mode: {mode}")
    return base + alpha * (result - base)


def sample_uv(texture, u, v, filtering="BILINEAR", tiling="REPEAT"):
    """Bottom-left UV origin, with optional transparent borders or nearest filtering."""
    h, w = texture.shape[:2]
    if tiling not in {"REPEAT", "NONE"} or filtering not in {"BILINEAR", "NEAREST"}:
        raise ValueError("Unsupported texture sampling settings")
    repeat = tiling == "REPEAT"
    su, sv = (u % 1, v % 1) if repeat else (np.clip(u, 0, 1), np.clip(v, 0, 1))
    x, y = su * w - 0.5, sv * h - 0.5

    def fetch(ix, iy):
        return (
            texture[iy % h, ix % w]
            if repeat
            else texture[np.clip(iy, 0, h - 1), np.clip(ix, 0, w - 1)]
        )

    if filtering == "NEAREST":
        result = fetch(
            np.floor(x + 0.5).astype(np.int32), np.floor(y + 0.5).astype(np.int32)
        ).copy()
    else:
        ix, iy = np.floor(x).astype(np.int32), np.floor(y).astype(np.int32)
        fx, fy = (x - ix)[..., None], (y - iy)[..., None]
        a = fetch(ix, iy) * (1 - fx) + fetch(ix + 1, iy) * fx
        b = fetch(ix, iy + 1) * (1 - fx) + fetch(ix + 1, iy + 1) * fx
        result = a * (1 - fy) + b * fy
    if not repeat:
        result = result * ((u >= 0) & (u <= 1) & (v >= 0) & (v <= 1))[..., None]
    return result
