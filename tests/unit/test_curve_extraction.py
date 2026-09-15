"""Mask thinning, tracing and simplification on synthetic rasters."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import unittest

import numpy as np

from open_painter.geometry_effects.curves import simplify, skeleton, split_jumps, trace


class CurveExtraction(unittest.TestCase):
    def test_thick_line_thins_to_one_path(self):
        mask = np.zeros((40, 40), np.float32)
        mask[18:23, 5:35] = 1.0
        thin = skeleton(mask)
        self.assertTrue(thin.any())
        self.assertLessEqual(thin.sum(), 32)
        paths = trace(thin)
        self.assertEqual(len(paths), 1)
        self.assertGreaterEqual(len(paths[0]), 25)
        cols = [c for _, c in paths[0]]
        self.assertTrue(abs(min(cols) - 5) <= 3 and abs(max(cols) - 34) <= 3)

    def test_ring_traces_to_a_loop(self):
        y, x = np.mgrid[0:60, 0:60]
        radius = np.hypot(y - 30, x - 30)
        ring = ((radius > 15) & (radius < 20)).astype(np.float32)
        thin = skeleton(ring)
        paths = trace(thin)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0][0], paths[0][-1], 'a ring traces as a closed loop')
        self.assertGreater(len(paths[0]), 60)

    def test_junction_splits_paths(self):
        mask = np.zeros((40, 40), np.float32)
        mask[19:22, 5:35] = 1.0     # horizontal bar
        mask[5:20, 19:22] = 1.0     # vertical stem meeting it: a T
        paths = trace(skeleton(mask))
        self.assertGreaterEqual(len(paths), 3)

    def test_simplify_and_split(self):
        line = np.array([[i, 0.0] for i in range(20)])
        self.assertEqual(len(simplify(line, 1.0)), 2)
        bent = np.array([[0, 0], [5, 0], [10, 5], [15, 5]], float)
        self.assertEqual(len(simplify(bent, .5)), 4)
        pieces = split_jumps(np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0], [50, 0, 0], [51, 0, 0]], float), 5.0)
        self.assertEqual([len(p) for p in pieces], [3, 2])


if __name__ == '__main__':
    unittest.main()
