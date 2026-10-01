"""Barycentric UV raster strips at texel centers, clamped to the 0-1 tile."""
import numpy as np


def tiles(uv, n, clip=None):
    """Yield bounded raster tiles and barycentric weights at pixel centers; ``clip`` is an
    optional UV box ((u0, v0), (u1, v1)) limiting the texels considered."""
    coords = uv * n - 0.5
    lo = np.maximum(np.ceil(coords.min(axis=0)).astype(int), 0)
    hi = np.minimum(np.floor(coords.max(axis=0)).astype(int) + 1, n)
    if clip is not None:
        window = np.asarray(clip, np.float32) * n - 0.5
        lo = np.maximum(lo, np.ceil(window[0]).astype(int))
        hi = np.minimum(hi, np.floor(window[1]).astype(int) + 1)
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


BLOCKS = (4, 8, 16, 32)
EMPTY = (np.empty(0, np.int64), np.empty(0, np.int64), np.empty(0, np.int64), np.empty((0, 3), np.float32))


def rasterize(uv, n, unique=True, clip=None):
    """Covered texels of many triangles at once: (rows, cols, triangle, weights).

    ``uv`` is (T, 3, 2). Triangles are grouped by texel extent and evaluated on
    shared local grids, so the Python cost is per group rather than per
    triangle; the barycentric arithmetic is the float32 sequence of ``tiles``.
    Where triangles overlap, the later triangle wins, matching sequential
    per-triangle writes; ``unique=False`` keeps every (texel, triangle) pair.
    ``clip`` (T, 2, 2) limits each triangle to a UV box (a brush footprint on a
    large face rasterises the footprint, not the face).
    """
    uv = np.asarray(uv, np.float32)
    if uv.ndim != 3 or len(uv) == 0:
        return EMPTY
    from ..native_bridge.lifecycle import cpu_library
    lib = cpu_library()
    if lib is not None and hasattr(lib, 'op_uv_rasterize'):
        from ..native_bridge.geometry import rasterize as native_rasterize
        return native_rasterize(lib, uv, n, unique, clip)
    coords = uv * n - 0.5
    lo = np.maximum(np.ceil(coords.min(axis=1)).astype(np.int64), 0)
    hi = np.minimum(np.floor(coords.max(axis=1)).astype(np.int64) + 1, n)
    if clip is not None:
        window = np.asarray(clip, np.float32) * n - 0.5
        lo = np.maximum(lo, np.ceil(window[:, 0]).astype(np.int64))
        hi = np.minimum(hi, np.floor(window[:, 1]).astype(np.int64) + 1)
    a, b, c = coords[:, 0], coords[:, 1], coords[:, 2]
    det = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
    valid = (np.abs(det) >= 1e-8) & np.all(hi > lo, axis=1)
    extent = (hi - lo).max(axis=1)
    rows, cols, tris, weights = [], [], [], []
    previous = 0
    for block in BLOCKS:
        chosen = np.flatnonzero(valid & (extent > previous) & (extent <= block))
        previous = block
        if not len(chosen):
            continue
        offsets = np.arange(block, dtype=np.float32)
        chunk = max(256, (1 << 20) // (block * block))
        for start in range(0, len(chosen), chunk):
            sub = chosen[start:start + chunk]
            lo_x, lo_y = lo[sub, 0], lo[sub, 1]
            x = lo_x.astype(np.float32)[:, None, None] + offsets[None, None, :]
            y = lo_y.astype(np.float32)[:, None, None] + offsets[None, :, None]
            a0, a1 = a[sub, 0, None, None], a[sub, 1, None, None]
            b0, b1 = b[sub, 0, None, None], b[sub, 1, None, None]
            c0, c1 = c[sub, 0, None, None], c[sub, 1, None, None]
            d = det[sub, None, None]
            w0 = ((b1 - c1) * (x - c0) + (c0 - b0) * (y - c1)) / d
            w1 = ((c1 - a1) * (x - c0) + (a0 - c0) * (y - c1)) / d
            w2 = 1 - w0 - w1
            inside = ((w0 >= -1e-5) & (w1 >= -1e-5) & (w2 >= -1e-5)
                      & (x < hi[sub, 0, None, None]) & (y < hi[sub, 1, None, None]))
            index, yy, xx = np.nonzero(inside)
            if not len(index):
                continue
            rows.append(lo_y[index] + yy)
            cols.append(lo_x[index] + xx)
            tris.append(sub[index])
            weights.append(np.stack((w0[inside], w1[inside], w2[inside]), -1))
    for triangle in np.flatnonzero(valid & (extent > BLOCKS[-1])):
        for (ys, xs), inside, w, _, _ in tiles(uv[triangle], n, None if clip is None else clip[triangle]):
            yy, xx = np.nonzero(inside)
            rows.append(ys.start + yy)
            cols.append(xs.start + xx)
            tris.append(np.full(len(yy), triangle, np.int64))
            weights.append(w[inside])
    if not rows:
        return EMPTY
    rows, cols = np.concatenate(rows), np.concatenate(cols)
    tris, weights = np.concatenate(tris), np.concatenate(weights)
    flat = rows * n + cols
    if unique and len(np.unique(flat)) != len(flat):
        # Later triangles overwrite earlier ones, as sequential writes would.
        order = np.argsort(tris, kind="stable")[::-1]
        _, first = np.unique(flat[order], return_index=True)
        keep = np.sort(order[first])
        rows, cols, tris, weights = rows[keep], cols[keep], tris[keep], weights[keep]
    return rows, cols, tris, weights


def interpolate(weights, corners):
    """Per-texel interpolation of (K, 3, C) corner attributes by (K, 3) weights."""
    from ..native_bridge.lifecycle import cpu_library
    if weights.dtype == corners.dtype == np.float32 and (lib := cpu_library()) is not None:
        from ..native_bridge.geometry import interpolate as native_interpolate
        return native_interpolate(lib, weights, corners)
    return (weights[:, 0, None] * corners[:, 0] + weights[:, 1, None] * corners[:, 1]
            + weights[:, 2, None] * corners[:, 2])
