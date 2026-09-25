"""Continuous weathering fields; no repeating images or UV-sized random grids.

Placement, grain and runoff are separate signals. All samples depend only on
coordinates and seed, so tiles, render strips and exports see the same surface.
These are art-directed fields, not a fluid or corrosion simulation.
"""
import numpy as np


def front(x, y, seed):
    """Growth potential with large islands and fine, irregular boundaries.

    Threshold this field to grow damage without reshuffling existing patches.
    Broad variation belongs in coverage, never in the surface height channel.
    """
    from .procedurals import _fbm, _perlin
    wx = x + 0.85 * _perlin(x * .63, y * .63, seed + 19)
    wy = y + 0.85 * _perlin(x * .63, y * .63, seed + 71)
    large = _fbm(wx * .47, wy * .47, seed, 3)
    broken = _fbm(wx * 2.7, wy * 2.7, seed + 137, 3)
    fine = _fbm(wx * 13, wy * 13, seed + 269, 2)
    return np.clip((.72 * large + .22 * broken + .06 * fine - .28) / .44, 0, 1)


def grain(x, y, seed):
    """Fine oxide granules and irregular pits, without swollen macro lumps."""
    from .procedurals import _fbm, _pores
    granular = _fbm(x * 3.7, y * 3.7, seed, 3)
    pits = _pores(x * 1.9, y * 1.9, seed + 31, radius=.23, density=.68)
    return np.clip((granular - .28) / .44 - .45 * pits, 0, 1)


def runoff(x, y, seed):
    """Finite gravity-aligned trails: unequal lengths, tapered ends, no sawtooth bands.

    x is horizontal and increasing y is up. Geometry-aware RUNOFF can supply
    world-position coordinates; a standalone mask follows its projection axes.
    """
    from .procedurals import _hash, _perlin, _smooth
    ix, iy = np.floor(x), np.floor(y)
    result = np.zeros_like(x)
    for dy in range(0, 3):
        for dx in range(-1, 2):
            cx, cy = ix + dx, iy + dy
            origin_x = cx + _hash(cx, cy, seed + 11)
            origin_y = cy + _hash(cx, cy, seed + 29)
            length = .25 + 1.45 * _hash(cx, cy, seed + 47) ** 2
            t = (origin_y - y) / length
            width = .012 + .055 * _hash(cx, cy, seed + 59) ** 2
            # Bend gently over the trail's length; never undulate every pixel.
            bend = .065 * _perlin(origin_x * .8, y * .7, seed + 83)
            distance = np.abs(x - origin_x - bend)
            taper = np.maximum(1 - np.clip(t, 0, 1), 0) ** .65
            line = 1 - _smooth(.15, 1., distance / np.maximum(width * taper, .0001))
            ends = _smooth(0, .05, t) * (1 - _smooth(.70, 1, t))
            active = _hash(cx, cy, seed + 101) < .46
            result = np.maximum(result, line * ends * active)
    return result
