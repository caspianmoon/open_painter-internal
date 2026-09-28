"""Conservative acceleration must exactly match an inclusive full box scan."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.geometry.triangle_bounds import BLOCK_SIZE, TriangleBounds


def direct(index, low, high):
    return np.flatnonzero(np.all(index.minimum <= high, axis=1) & np.all(index.maximum >= low, axis=1))


class BoundsQuery(unittest.TestCase):
    def compare(self, index, low, high):
        got = index.query(low, high)
        np.testing.assert_array_equal(got, direct(index, low, high))
        self.assertEqual(got.dtype, np.dtype(np.intp))

    def test_random_scrambled_triangles_and_inclusive_edges(self):
        rng = np.random.default_rng(495)
        for dtype in (np.float32, np.float64):
            centres = rng.uniform(-30, 30, (8193, 3)).astype(dtype)
            radii = rng.uniform(0, .1, (len(centres), 3)).astype(dtype)
            minimum, maximum = centres - radii, centres + radii
            index = TriangleBounds(minimum, maximum)
            self.assertIs(index.minimum, minimum)
            self.assertIs(index.maximum, maximum)
            self.assertIsNotNone(index.order)
            for centre in centres[::137]:
                self.compare(index, centre - .4, centre + .4)
            for corner in (minimum[17], maximum[519], np.array([100, 100, 100], dtype)):
                self.compare(index, corner, corner)
            self.compare(index, np.full(3, -100), np.full(3, 100))
            blocks = (len(centres) + BLOCK_SIZE - 1) // BLOCK_SIZE
            self.assertEqual(index.extra_bytes, len(centres) * 4 + blocks * 6 * np.dtype(dtype).itemsize)

    def test_degenerate_huge_long_and_overlapping_bounds(self):
        count = 9001
        rng = np.random.default_rng(510)
        for scale in (0., 1., 1e300):
            point = rng.uniform(-1, 1, (count, 3)) * scale
            low, high = point.copy(), point.copy()
            # Long triangles and duplicate/point geometry can occupy many
            # spatial blocks; broad queries still retain exact original order.
            low[::3], high[::3] = -scale, scale
            index = TriangleBounds(low, high)
            for radius in (0., .001 * scale, .2 * scale, scale):
                self.compare(index, np.full(3, -radius), np.full(3, radius))
            self.compare(index, np.full(3, -np.inf), np.full(3, np.inf))
            self.compare(index, np.array([np.nan, 0, 0]), np.full(3, scale))

    def test_non_finite_mesh_falls_back_without_hiding_valid_triangles(self):
        low = np.zeros((5000, 3), np.float32)
        high = np.ones_like(low)
        low[4, 0], high[23, 1] = np.nan, np.inf
        index = TriangleBounds(low, high)
        self.assertEqual(index.extra_bytes, 0)
        self.compare(index, (.5, .5, .5), (.5, .5, .5))
        self.compare(index, (-np.inf, -np.inf, -np.inf), (np.inf, np.inf, np.inf))

    def test_small_empty_and_allocation_failure_use_existing_bounds(self):
        for count in (0, 1, 4095):
            index = TriangleBounds(np.zeros((count, 3)), np.ones((count, 3)))
            self.assertEqual(index.extra_bytes, 0)
            self.compare(index, (0, 0, 0), (0, 0, 0))
        low = np.zeros((5000, 3))
        for allocation in ('numpy.argsort', 'numpy.isfinite'):
            with patch(allocation, side_effect=MemoryError('optional acceleration')):
                index = TriangleBounds(low, low + 1)
            self.assertEqual(index.extra_bytes, 0)
            self.compare(index, (0, 0, 0), (1, 1, 1))

    def test_replacement_snapshot_builds_new_index(self):
        x = np.arange(8192, dtype=np.float64)
        low = np.column_stack((x, np.zeros_like(x), np.zeros_like(x)))
        old = TriangleBounds(low, low + .1)
        replacement = TriangleBounds(low + 50, low + 50.1)
        self.assertGreater(len(old.query((5, 0, 0), (7, 1, 1))), 0)
        self.assertEqual(len(replacement.query((5, 0, 0), (7, 1, 1))), 0)


if __name__ == '__main__':
    unittest.main()
