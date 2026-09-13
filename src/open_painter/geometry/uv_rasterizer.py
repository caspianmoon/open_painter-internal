"""Barycentric UV raster strips at texel centers, clamped to the 0-1 tile."""
import numpy as np


def tiles(uv, n):
    """Yield bounded raster tiles and barycentric weights at pixel centers."""
    coords = uv * n - 0.5
    lo = np.maximum(np.ceil(coords.min(axis=0)).astype(int), 0)
    hi = np.minimum(np.floor(coords.max(axis=0)).astype(int) + 1, n)
    a, b, c = coords
    det = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
    if abs(det) < 1e-8 or np.any(hi <= lo):
        return
    for y0 in range(lo[1], hi[1], 64):
        y1 = min(y0 + 64, hi[1])
        x, y = np.meshgrid(
            np.arange(lo[0], hi[0], dtype=np.float32),
            np.arange(y0, y1, dtype=np.float32),
        )
        w0 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / det
        w1 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / det
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-5) & (w1 >= -1e-5) & (w2 >= -1e-5)
        if inside.any():
            yield (slice(y0, y1), slice(lo[0], hi[0])), inside, np.stack((w0, w1, w2), -1), x, y
