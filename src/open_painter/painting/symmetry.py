"""Resolve object-local symmetry into ordinary world-space brush samples."""
from itertools import product

import numpy as np


def transforms(frame, axes):
    frame = np.asarray(frame, dtype=np.float64).reshape(4, 4)
    inverse = np.linalg.inv(frame)
    for signs in product(*[([1., -1.] if axis else [1.]) for axis in axes]):
        if signs == (1., 1., 1.):
            continue
        yield frame @ np.diag((*signs, 1.)) @ inverse


def reflect(row, matrices, with_transform=False):
    """Yield unique reflected samples; normal uses the inverse transpose.

    Deduplication is relative to the brush radius, including intersections of
    symmetry planes. It is independent of object scale and scene translation.
    """
    row = np.asarray(row, dtype=np.float64)
    seen = [row[:3]]
    epsilon = max(float(row[7]) * 1e-5, 1e-8)
    for matrix in matrices:
        point = (matrix @ np.r_[row[:3], 1.])[:3]
        if any(np.linalg.norm(point - old) <= epsilon for old in seen):
            continue
        result = row.copy()
        result[:3] = point
        for start, transform in ((3, np.linalg.inv(matrix[:3, :3]).T), (10, matrix[:3, :3])):
            vector = transform @ row[start:start + 3]
            result[start:start + 3] = vector / max(np.linalg.norm(vector), 1e-20)
        seen.append(point)
        yield (result, matrix) if with_transform else result
