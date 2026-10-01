"""Native seam correspondence compared against the independent banded reference."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.native_bridge import load_library, lifecycle
from open_painter.native_bridge.seams import bands
from open_painter.geometry import seams


@unittest.skipUnless(os.environ.get('OP_TEST_NATIVE_LIBRARY'), 'native build not supplied')
class SeamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])

    def test_random_diagonal_density_corners_overlap_and_island_ties(self):
        rng = np.random.default_rng(3872)
        for n in (16, 64, 128):
            sides = [rng.uniform(-10, n+10, (35, 2)).astype(np.float32) for _ in range(6)]
            islands = rng.integers(0, 6, len(sides[0]), dtype=np.int64)
            # Duplicate seam sides test earlier-side and cross-island ties.
            for value in sides:
                value[2] = value[1]
                value[3] = value[1]
            islands[2], islands[3] = islands[1], 12
            sides[1][0] = sides[0][0]  # degenerate edge has no band
            covered = rng.random((n, n)) < .15
            for halo in (1, 4, 17):
                with patch.object(lifecycle, 'cpu_library', return_value=None):
                    expected = seams._bands(covered, n, halo, *sides, islands)
                actual = bands(self.lib, covered, n, halo, *sides, islands)
                self.assertEqual(actual is None, expected is None)
                if actual is None:
                    continue
                for index in (0, 1, 6):
                    np.testing.assert_array_equal(actual[index], expected[index])
                for index in (2, 3, 4, 5):
                    np.testing.assert_allclose(actual[index], expected[index], atol=3e-5, rtol=0)
                self.assertFalse(covered[actual[0], actual[1]].any())
                native = bands(self.lib, covered, n, halo, *sides, islands, with_nearest=True)
                linear = native[0] * n + native[1]
                key = (native[4].view(np.int32).astype(np.int64) << 32) | native[6]
                race = np.full(n*n, np.iinfo(np.int64).max, np.int64)
                np.minimum.at(race, linear, key)
                np.testing.assert_array_equal(native[7], race[linear] == key)

    def test_empty_covered_and_invalid_geometry(self):
        sides = [np.array([[0, 0], [4, 4]], np.float32) for _ in range(6)]
        covered = np.ones((16, 16), bool)
        self.assertIsNone(bands(self.lib, covered, 16, 4, *sides, [0, 1]))
        self.assertIsNone(bands(self.lib, covered, 16, 4, *(np.empty((0, 2)) for _ in range(6)), []))
        sides[0][0, 0] = np.nan
        with self.assertRaises(RuntimeError):
            bands(self.lib, covered, 16, 4, *sides, [0, 1])
