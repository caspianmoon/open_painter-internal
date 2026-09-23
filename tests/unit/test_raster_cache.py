import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.geometry import raster_cache, uv_rasterizer


class RasterCacheTests(unittest.TestCase):
    def setUp(self):
        raster_cache.clear()
        self.uv = np.array([[[0., 0.], [1., 0.], [0., 1.]],
                            [[0., 0.], [1., 0.], [0., 1.]]], np.float32)
        self.addCleanup(raster_cache.clear)

    def test_shared_sampling_preserves_overlap_winner(self):
        with patch.object(uv_rasterizer, 'rasterize', wraps=uv_rasterizer.rasterize) as raster:
            result = raster_cache.sample(self.uv, 32)
            self.assertIs(result, raster_cache.sample(self.uv.copy(), 32))
            self.assertEqual(raster.call_count, 1)
        self.assertTrue(np.all(result[2] == 1))
        for actual, expected in zip(result, uv_rasterizer.rasterize(self.uv, 32)):
            np.testing.assert_array_equal(actual, expected)
            self.assertFalse(actual.flags.writeable)

    def test_uv_and_resolution_changes_invalidate(self):
        original = raster_cache.sample(self.uv, 32)
        changed = self.uv.copy(); changed[0, 0, 0] = .2
        self.assertIsNot(original, raster_cache.sample(changed, 32))
        self.assertIsNot(original, raster_cache.sample(self.uv, 64))

    def test_memory_is_bounded(self):
        with patch.object(raster_cache, 'LIMIT', 20000):
            for n in range(8, 65, 4):
                raster_cache.sample(self.uv, n)
                self.assertLessEqual(raster_cache._bytes, 20000)


if __name__ == '__main__':
    unittest.main()
