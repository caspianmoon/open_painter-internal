import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import unittest
import numpy as np
from open_painter.geometry import udim


class Tiles(unittest.TestCase):
    def test_numbering_and_bounds(self):
        self.assertEqual(udim.tile_number(0, 0), 1001)
        self.assertEqual(udim.tile_number(9, 0), 1010)
        self.assertEqual(udim.tile_number(0, 1), 1011)
        self.assertEqual(udim.tile_number(9, 9), 1100)

    def test_centroid_decides_boundary_faces(self):
        # A quad exactly on the 1001/1002 boundary is split into two triangles;
        # per-loop filtering (Ucupaint) would drop them, centroids keep them.
        left = [[.5, .2], [1., .2], [1., .8]]
        right = [[1., .2], [1.5, .2], [1.5, .8]]
        tiles, invalid = udim.tile_numbers(np.array([left, right]))
        self.assertEqual(tiles, [1001, 1002])
        self.assertEqual(invalid, [])

    def test_column_ten_never_aliases_into_next_row(self):
        far = [[10.2, .2], [10.8, .2], [10.5, .8]]
        tiles, invalid = udim.tile_numbers(np.array([far]))
        self.assertEqual(tiles, [1001])
        self.assertEqual(invalid, [0])

    def test_negative_and_second_row(self):
        tiles, invalid = udim.tile_numbers(np.array([
            [[-.5, .2], [-.1, .2], [-.3, .8]],
            [[.2, 1.2], [.8, 1.2], [.5, 1.8]],
        ]))
        self.assertEqual(tiles, [1001, 1011])
        self.assertEqual(invalid, [0])

    def test_empty_input_is_tile_1001(self):
        self.assertEqual(udim.tile_numbers(np.empty((0, 3, 2))), ([1001], []))


if __name__ == "__main__":
    unittest.main()
