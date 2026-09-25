import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import unittest
import numpy as np
from open_painter.geometry import udim


class Tiles(unittest.TestCase):
    def test_authoring_keeps_integer_border_faces_in_their_own_tile(self):
        self.assertEqual(udim.face_tile([[0, 0], [1, 0], [1, 1], [0, 1]]), 1001)
        self.assertEqual(udim.face_tile([[1, 0], [2, 0], [2, 1], [1, 1]]), 1002)
        self.assertEqual(udim.face_tile([[0, 1], [1, 1], [1, 2], [0, 2]]), 1011)
        self.assertEqual(udim.tile_origin(2000), (9, 99))
        self.assertEqual(udim.face_tile([[9, 99], [10, 99], [10, 100], [9, 100]]), 2000)

    def test_authoring_rejects_lossy_or_ambiguous_layouts(self):
        for uv in (
            [[.8, .1], [1.2, .1], [1.2, .8]],
            [[-.5, .1], [-.1, .1], [-.1, .8]],
            [[10, 0], [11, 0], [11, 1]],
            [[0, 100], [1, 100], [1, 101]],
            [[0, 0], [0, 0], [0, 0]],
            [[0, 0], [1, 0], [float('nan'), 1]],
        ):
            with self.subTest(uv=uv), self.assertRaises(ValueError):
                udim.face_tile(uv)
        for number in (1000, 2001, 1001.5, True):
            with self.assertRaises(ValueError):
                udim.tile_origin(number)

    def test_numbering_and_bounds(self):
        self.assertEqual(udim.tile_number(0, 0), 1001)
        self.assertEqual(udim.tile_number(9, 0), 1010)
        self.assertEqual(udim.tile_number(0, 1), 1011)
        self.assertEqual(udim.tile_number(9, 9), 1100)

    def test_centroid_decides_boundary_faces(self):
        # A quad exactly on the 1001/1002 boundary is split into two triangles;
        # per-loop filtering (the node-stack add-on) would drop them, centroids keep them.
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
