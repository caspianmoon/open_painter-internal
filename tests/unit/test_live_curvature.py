"""Pure parts of the live curvature analysis."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import unittest

import numpy as np

from open_painter.geometry.live_curvature import diffuse, passes_for, signed_to_unit, vertex_shares, weld


class LiveCurvature(unittest.TestCase):
    def test_weld_merges_coincident_vertices(self):
        welded, count = weld(np.array([[0, 0, 0], [1, 0, 0], [0, 0, 0], [1, 0, 1e-9]]))
        self.assertEqual(count, 2)
        self.assertEqual(welded[0], welded[2])
        self.assertEqual(welded[1], welded[3])

    def test_shares_split_polygons_evenly(self):
        turning = np.array([1.0, 3.0])
        area = np.array([2.0, 4.0])
        lp = np.array([0, 0, 0, 1, 1, 1, 1])
        lv = np.array([0, 1, 2, 1, 2, 3, 4])
        lt = np.array([3, 4])
        t, a = vertex_shares(lp, lv, lt, turning, area, 5)
        self.assertAlmostEqual(t.sum(), 4.0)
        self.assertAlmostEqual(a.sum(), 6.0)
        self.assertAlmostEqual(t[1], 1 / 3 + 3 / 4)

    def test_diffusion_conserves_and_spreads(self):
        values = np.array([10.0, 0.0, 0.0, 0.0])
        edges = np.array([[0, 1], [1, 2], [2, 3]])
        smoothed = diffuse(values, edges, 6)
        self.assertAlmostEqual(smoothed.sum(), 10.0)
        self.assertTrue(smoothed[0] < 10.0 and smoothed[3] > 0.0)
        np.testing.assert_array_equal(diffuse(values, edges, 0), values)
        np.testing.assert_array_equal(diffuse(values, np.zeros((0, 2), int), 3), values)

    def test_passes_follow_radius_over_edge_length(self):
        positions = np.array([[0, 0, 0], [.1, 0, 0], [.2, 0, 0]], float)
        edges = np.array([[0, 1], [1, 2]])
        self.assertEqual(passes_for(.36, edges, positions), 4)
        self.assertEqual(passes_for(100, edges, positions), 24)
        self.assertEqual(passes_for(.02, edges, positions), 0)

    def test_signed_to_unit_dead_zone(self):
        rel = np.array([0.0, .1, .55, 1.0, -1.0], np.float32)
        convex = signed_to_unit(rel, 1.0)
        concave = signed_to_unit(rel, -1.0)
        self.assertEqual(convex[0], 0.0)
        self.assertAlmostEqual(float(convex[2]), .5, places=5)
        self.assertEqual(convex[3], 1.0)
        self.assertEqual(concave[4], 1.0)
        self.assertEqual(concave[3], 0.0)


if __name__ == '__main__':
    unittest.main()
