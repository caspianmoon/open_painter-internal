"""UDIM tile enumeration from evaluated render UVs; a diagnostic, not a painter.

Adapted from Ucupaint 3.0.0 ``UDIM.py`` ``get_tile_numbers`` /
``is_uvmap_udim`` (GPL-3.0-or-later), with two changes: tiles are decided by
triangle centroids instead of per-loop filtering, so a face lying exactly on
integer boundaries still counts, and ``u`` is bounded to 0-9 so it can never
alias into the next tile row. Open Painter paints, bakes and exports only tile
1001 today (``docs/udim-tile-access.md``); this reports what a layout needs.
"""
import numpy as np


def tile_number(u, v):
    return 1001 + int(u) + int(v) * 10


def tile_numbers(triangles_uv):
    """Sorted UDIM numbers touched by (T, 3, 2) UV triangles, keyed by centroid.

    Rows outside 0-9 columns or below row 0 are reported as ``None`` so the
    caller can flag them as unsupported rather than silently wrapping.
    """
    uv = np.asarray(triangles_uv, np.float64)
    if not len(uv):
        return [1001], []
    centroid = uv.mean(axis=1)
    u = np.floor(centroid[:, 0]).astype(np.int64)
    v = np.floor(centroid[:, 1]).astype(np.int64)
    valid = (u >= 0) & (u <= 9) & (v >= 0) & (v <= 9)
    tiles = sorted({tile_number(a, b) for a, b in zip(u[valid], v[valid])} | {1001})
    invalid = np.flatnonzero(~valid)
    return tiles, invalid.tolist()


def layout(ts):
    """Return per-object tile usage for the set: {name: (tiles, invalid faces, outside faces)}."""
    from .snapshots import targets
    result = {}
    with targets(ts) as meshes:
        for target in meshes:
            loops = np.asarray([tri.loops[:] for tri in target['triangles']], np.int64)
            polygon = np.asarray([tri.polygon_index for tri in target['triangles']], np.int64)
            uv = target['uv'][loops].astype(np.float64) if len(loops) else np.empty((0, 3, 2))
            if len(uv):
                # Zero-area UV triangles claim no tile, matching the bake diagnostics.
                a, b = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
                live = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) >= 1e-12
                uv, polygon = uv[live], polygon[live]
            tiles, invalid = tile_numbers(uv)
            centroid = uv.mean(axis=1) if len(uv) else np.empty((0, 2))
            outside = np.flatnonzero((centroid[:, 0] >= 1) | (centroid[:, 1] >= 1) | (centroid < 0).any(axis=1)) if len(uv) else np.empty(0, np.int64)
            result[target['object'].name] = (tiles, sorted({int(p) for p in polygon[invalid]}),
                                             sorted({int(p) for p in polygon[outside]}))
    return result
