"""Exact UV overlap analysis over evaluated triangles; no ray sampling.

Two triangles overlap only when their intersection has positive area, so
islands that merely share an edge or corner are not reported. Stacked islands
(identical corner sets, optionally mirrored) are separated from partial
overlaps because symmetric assets stack islands deliberately.
"""
import numpy as np

# Bound broad-phase work; larger inputs raise instead of returning a partial
# or sampled answer that could be mistaken for an exact one. Pairs are tested
# in chunks so peak temporaries stay near CHUNK × 6 corners, not the total.
MAX_CANDIDATE_PAIRS = 16_000_000
CHUNK = 1 << 20


def _pairs_in_cells(order, counts):
    """All index pairs inside each sorted cell run, without a per-cell loop."""
    total = int(counts.sum())
    starts = np.cumsum(counts) - counts
    segment = np.repeat(np.arange(len(counts)), counts)
    rank = np.arange(total) - starts[segment]
    reps = counts[segment] - 1 - rank
    pairs = int(reps.sum())
    if pairs > MAX_CANDIDATE_PAIRS:
        raise ValueError(f"UV overlap analysis skipped: {pairs} candidate pairs exceed the bounded limit")
    if not pairs:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    first = np.repeat(np.arange(total), reps)
    second = first + 1 + (np.arange(pairs) - np.repeat(np.cumsum(reps) - reps, reps))
    a, b = order[first], order[second]
    count = int(order.max()) + 1
    packed = np.unique(np.minimum(a, b) * count + np.maximum(a, b))
    return packed // count, packed % count


def candidate_pairs(uv, cell=None):
    """Triangle index pairs whose UV bounding boxes share a uniform grid cell."""
    lo, hi = uv.min(axis=1), uv.max(axis=1)
    if cell is None:
        cell = max(float(np.median((hi - lo).max(axis=1))), 1 / 4096)
    start = np.floor(lo / cell).astype(np.int64)
    span = np.floor(hi / cell).astype(np.int64) - start + 1
    per_triangle = span[:, 0] * span[:, 1]
    total = int(per_triangle.sum())
    if total > MAX_CANDIDATE_PAIRS:
        raise ValueError(f"UV overlap analysis skipped: {total} grid entries exceed the bounded limit")
    tri = np.repeat(np.arange(len(uv)), per_triangle)
    offsets = np.arange(total) - np.repeat(np.cumsum(per_triangle) - per_triangle, per_triangle)
    cx = start[tri, 0] + offsets % span[tri, 0]
    cy = start[tri, 1] + offsets // span[tri, 0]
    key = (cx - cx.min()) * (cy.max() - cy.min() + 1) + (cy - cy.min())
    order = np.argsort(key, kind="stable")
    _, counts = np.unique(key[order], return_counts=True)
    return _pairs_in_cells(tri[order], counts)


def _separated(a, b, eps):
    """Separating-axis test on all six edge normals; touching counts as separated."""
    separated = np.zeros(len(a), bool)
    for tri in (a, b):
        for k in range(3):
            edge = tri[:, (k + 1) % 3] - tri[:, k]
            axis = np.stack((-edge[:, 1], edge[:, 0]), 1)
            axis /= np.maximum(np.linalg.norm(axis, axis=1, keepdims=True), 1e-30)
            pa = np.einsum("nij,nj->ni", a, axis)
            pb = np.einsum("nij,nj->ni", b, axis)
            separated |= (pa.max(1) <= pb.min(1) + eps) | (pb.max(1) <= pa.min(1) + eps)
    return separated


def _same_corners(a, b, eps):
    matched = np.linalg.norm(a[:, :, None, :] - b[:, None, :, :], axis=-1) < eps
    return matched.any(2).all(1) & matched.any(1).all(1)


def _winding(t):
    return np.sign((t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1])
                   - (t[:, 2, 0] - t[:, 0, 0]) * (t[:, 1, 1] - t[:, 0, 1]))


def overlaps(uv, eps=1e-6, exclude_same=None):
    """Return ``(partial, stacked, mirrored)`` for (T, 3, 2) UV triangles.

    ``partial`` and ``stacked`` are (2, N) index arrays; ``mirrored`` flags each
    stacked pair whose windings differ. ``exclude_same`` gives a label per
    triangle so one face's own fan triangles are never paired with each other.
    """
    uv = np.asarray(uv, np.float64)
    empty = np.empty((2, 0), np.int64)
    if len(uv) < 2:
        return empty, empty, np.empty(0, bool)
    i, j = candidate_pairs(uv)
    if exclude_same is not None and len(i):
        keep = np.asarray(exclude_same)[i] != np.asarray(exclude_same)[j]
        i, j = i[keep], j[keep]
    if not len(i):
        return empty, empty, np.empty(0, bool)
    # Cheap bounding-box rejection before the twelve-axis test.
    lo, hi = uv.min(axis=1), uv.max(axis=1)
    near = np.all((hi[i] > lo[j] + eps) & (hi[j] > lo[i] + eps), axis=1)
    i, j = i[near], j[near]
    hit = np.concatenate([~_separated(uv[i[k:k + CHUNK]], uv[j[k:k + CHUNK]], eps)
                          for k in range(0, len(i), CHUNK)]) if len(i) else np.empty(0, bool)
    i, j = i[hit], j[hit]
    a, b = uv[i], uv[j]
    stacked = _same_corners(a, b, max(eps * 10, 1e-5))
    mirrored = (_winding(a) != _winding(b))[stacked]
    return np.stack((i[~stacked], j[~stacked])), np.stack((i[stacked], j[stacked])), mirrored
