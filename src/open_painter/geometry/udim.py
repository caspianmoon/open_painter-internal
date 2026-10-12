"""UDIM tile addressing and strict authoring layout validation.

Adapted from Ucupaint (3.0.0) ``UDIM.py`` ``get_tile_numbers`` /
``is_uvmap_udim`` (GPL-3.0-or-later), with two changes: tiles are decided by
triangle centroids instead of per-loop filtering, so a face lying exactly on
integer boundaries still counts, and ``u`` is bounded to 0-9 so it can never
alias into the next tile row. ``core/udim.py`` creates one texture set per tile;
enumeration also remains available for ordinary single-tile diagnostics.
"""
import numpy as np

MIN_TILE = 1001
MAX_TILE = 2000
UV_EPSILON = 1e-6


def tile_origin(number):
    """UDIM's ten-column addressing, including rows above the first ten."""
    if isinstance(number, bool) or int(number) != number or not MIN_TILE <= number <= MAX_TILE:
        raise ValueError(f"UDIM tile must be between {MIN_TILE} and {MAX_TILE}")
    index = int(number) - MIN_TILE
    return index % 10, index // 10


def face_tile(coordinates):
    """The one tile containing a UV polygon, rejecting ambiguous/cross-tile faces.

    Integer-boundary vertices belong to the tile containing the face interior.
    Silently wrapping a straddling face would lose pixels, so preflight rejects it.
    """
    uv = np.asarray(coordinates, dtype=np.float64)
    if uv.ndim != 2 or uv.shape[1] != 2 or len(uv) < 3 or not np.isfinite(uv).all():
        raise ValueError("Face requires at least three finite UV coordinates")
    area = np.sum(uv[:, 0] * np.roll(uv[:, 1], -1) - uv[:, 1] * np.roll(uv[:, 0], -1))
    if abs(area) < 1e-12:
        raise ValueError("Face has a degenerate UV layout")
    u, v = np.floor(uv.mean(axis=0)).astype(np.int64)
    if not (0 <= u < 10 and 0 <= v < 100):
        raise ValueError(f"UVs must lie in UDIM tiles {MIN_TILE}–{MAX_TILE} (ten columns)")
    local = uv - (u, v)
    if (local < -UV_EPSILON).any() or (local > 1 + UV_EPSILON).any():
        raise ValueError("UV face crosses a UDIM tile boundary; keep each face inside one tile")
    return tile_number(u, v)


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
    valid = (u >= 0) & (u <= 9) & (v >= 0) & (v < 100)
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
