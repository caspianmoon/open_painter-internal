"""Deterministic smooth value noise, independent of tile boundaries."""

import numpy as np


def noise(u, v, scale=12, seed=1):
    x, y = u * scale, v * scale
    ix, iy = np.floor(x), np.floor(y)
    fx, fy = x - ix, y - iy
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)

    def hash_value(a, b):
        h = np.sin(a * 127.1 + b * 311.7 + seed * 74.7) * 43758.5453
        return h - np.floor(h)

    a = hash_value(ix, iy) * (1 - fx) + hash_value(ix + 1, iy) * fx
    b = hash_value(ix, iy + 1) * (1 - fx) + hash_value(ix + 1, iy + 1) * fx
    return (a * (1 - fy) + b * fy).astype(np.float32)
