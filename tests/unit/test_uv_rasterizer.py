"""The batched rasterizer reproduces the per-triangle reference exactly."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from open_painter.geometry.uv_rasterizer import interpolate, rasterize, tiles


def reference(uv, n):
    rows = np.full((n, n), -1, np.int64)
    weights = np.zeros((n, n, 3), np.float32)
    for index, triangle in enumerate(uv):
        for (ys, xs), inside, w, _, _ in tiles(triangle, n):
            block = rows[ys, xs]
            block[inside] = index
            weights[ys, xs][inside] = w[inside]
    return rows, weights


class RasterizeTest(unittest.TestCase):
    def check(self, uv, n):
        rows_ref, weights_ref = reference(uv, n)
        rows, cols, tris, weights = rasterize(uv, n)
        covered = np.full((n, n), -1, np.int64)
        covered[rows, cols] = tris
        np.testing.assert_array_equal(covered, rows_ref)
        got = np.zeros((n, n, 3), np.float32)
        got[rows, cols] = weights
        np.testing.assert_array_equal(got, weights_ref)
        self.assertEqual(len(np.unique(rows * n + cols)), len(rows))

    def test_random_mixed_sizes(self):
        rng = np.random.default_rng(3)
        n = 96
        centers = rng.random((400, 1, 2), np.float32)
        sizes = rng.choice([.01, .04, .1, .3, .8], 400)[:, None, None].astype(np.float32)
        uv = (centers + (rng.random((400, 3, 2), np.float32) - .5) * sizes).astype(np.float32)
        self.check(uv, n)

    def test_overlap_last_wins_and_degenerate(self):
        n = 32
        uv = np.array([[[.1, .1], [.9, .1], [.5, .9]],
                       [[.1, .1], [.9, .1], [.5, .9]],       # identical: later wins
                       [[.2, .2], [.2, .2], [.2, .2]],       # degenerate: skipped
                       [[.3, .3], [.7, .3], [.5, .6]]], np.float32)
        self.check(uv, n)

    def test_outside_and_seam(self):
        n = 16
        uv = np.array([[[-.2, -.2], [.4, -.1], [.1, .5]], [[.7, .7], [1.3, .8], [.9, 1.4]]], np.float32)
        self.check(uv, n)

    def test_interpolate_matches_matmul(self):
        rng = np.random.default_rng(5)
        w = rng.random((50, 3), np.float32)
        corners = rng.random((50, 3, 4), np.float32)
        expected = np.einsum("kj,kjc->kc", w, corners)
        np.testing.assert_allclose(interpolate(w, corners), expected, rtol=1e-6, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
