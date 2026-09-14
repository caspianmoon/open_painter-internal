import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import unittest
import numpy as np
from open_painter.geometry import uv_overlap


def pairs(array):
    return sorted(map(tuple, array.T.tolist()))


class UVOverlap(unittest.TestCase):
    A = [[0, 0], [1, 0], [0, 1]]
    B = [[1, 0], [1, 1], [0, 1]]           # shares A's hypotenuse: touching only
    C = [[.25, .25], [.75, .25], [.25, .75]]  # inside A
    D = [[0, 0], [0, 1], [1, 0]]           # A mirrored: same corners, opposite winding
    E = [[2, 2], [3, 2], [2, 3]]           # far away
    F = [[0, 0], [1, 0], [0, 1]]           # identical copy of A
    G = [[0, 0], [.5, 0], [0, .5]]         # shares A's corner and two edges, smaller

    def test_edge_and_corner_contact_is_not_overlap(self):
        partial, stacked, _ = uv_overlap.overlaps(np.array([self.A, self.B, self.E], float))
        self.assertEqual(pairs(partial), [])
        self.assertEqual(pairs(stacked), [])

    def test_partial_stacked_and_mirrored_classification(self):
        uv = np.array([self.A, self.B, self.C, self.D, self.E, self.F, self.G], float)
        partial, stacked, mirrored = uv_overlap.overlaps(uv)
        self.assertEqual(pairs(partial), [(0, 2), (0, 6), (2, 3), (2, 5), (3, 6), (5, 6)])
        self.assertEqual(pairs(stacked), [(0, 3), (0, 5), (3, 5)])
        self.assertEqual(mirrored.tolist(), [True, False, True])

    def test_same_face_fan_triangles_are_excluded(self):
        uv = np.array([self.A, self.C], float)
        partial, stacked, _ = uv_overlap.overlaps(uv, exclude_same=np.array([7, 7]))
        self.assertEqual(pairs(partial), [])
        partial, _, _ = uv_overlap.overlaps(uv, exclude_same=np.array([7, 8]))
        self.assertEqual(pairs(partial), [(0, 1)])

    def test_grid_broadphase_matches_brute_force(self):
        rng = np.random.default_rng(1)
        uv = rng.random((300, 3, 2)) * .2 + rng.random((300, 1, 2)) * .8
        partial, stacked, _ = uv_overlap.overlaps(uv)
        found = set(pairs(partial)) | set(pairs(stacked))
        i, j = np.triu_indices(300, 1)
        hit = ~uv_overlap._separated(uv[i], uv[j], 1e-6)
        brute = set(zip(i[hit].tolist(), j[hit].tolist()))
        self.assertEqual(found, brute)
        self.assertGreater(len(brute), 500)

    def test_bounded_candidate_generation_raises(self):
        saved = uv_overlap.MAX_CANDIDATE_PAIRS
        uv_overlap.MAX_CANDIDATE_PAIRS = 10
        try:
            with self.assertRaises(ValueError):
                uv_overlap.overlaps(np.random.default_rng(2).random((64, 3, 2)))
        finally:
            uv_overlap.MAX_CANDIDATE_PAIRS = saved

    def test_single_triangle_and_empty_input(self):
        for uv in (np.empty((0, 3, 2)), np.array([self.A], float)):
            partial, stacked, mirrored = uv_overlap.overlaps(uv)
            self.assertEqual(partial.shape, (2, 0))
            self.assertEqual(stacked.shape, (2, 0))
            self.assertEqual(len(mirrored), 0)


if __name__ == "__main__":
    unittest.main()
