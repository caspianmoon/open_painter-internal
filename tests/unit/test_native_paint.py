"""Seeded differential tests for the C brush and geometry kernels."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.native_bridge import load_library, paint
from open_painter.painting import stroke_raster as reference, surface_reach


@unittest.skipUnless(os.environ.get('OP_TEST_NATIVE_LIBRARY'), 'native build not supplied')
class PaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])

    def test_coverage_pressure_flow_alpha_planar_and_eligibility(self):
        rng = np.random.default_rng(9071)
        positions = rng.uniform(-1, 1, (300, 3)).astype(np.float32)
        normals = rng.normal(size=(300, 3)).astype(np.float32)
        normals /= np.linalg.norm(normals, axis=1, keepdims=True)
        samples = rng.uniform(-1, 1, (279, 13)).astype(np.float32)
        samples[:, 3:6] /= np.linalg.norm(samples[:, 3:6], axis=1, keepdims=True)
        samples[:, 6:8] = rng.uniform(.1, 1, (len(samples), 2))
        samples[0, 10:] = 0
        alpha = rng.random((23, 19), dtype=np.float32)
        eligible = lambda texels, start, stop: (texels[:, None] + np.arange(start, stop)[None, :]) % 3 != 0
        for accumulate in ('MAX', 'FLOW'):
            for planar in (None, (0, 0, 1)):
                for texture in (None, alpha):
                    settings = dict(hardness=.35, flow=.47, accumulate=accumulate, planar=planar,
                                    alpha_rotation='RAKE', alpha_angle=.4, depth_limit=.8)
                    with patch.object(paint, 'cpu_library', return_value=None):
                        expected = reference.kernel(positions, normals, samples, **settings,
                                                    alpha_texture=texture, eligible=eligible)
                    actual = paint.coverage(self.lib, positions, normals, samples, settings, texture, eligible)
                    np.testing.assert_allclose(actual, expected, rtol=0, atol=3e-6)

    def test_distance_and_uv_window(self):
        rng = np.random.default_rng(17)
        corners = rng.normal(size=(2048, 3, 3))
        corners[:4] = 0
        points = rng.normal(size=(2048, 3))
        uv = rng.uniform(0, 1, (2048, 3, 2))
        with patch.object(paint, 'cpu_library', return_value=None):
            expected = surface_reach.point_triangle_distance(points, corners)
            windows = reference.uv_window(corners, uv, (-.5, -.2, -.1), (.5, .3, .8), .01)
        np.testing.assert_allclose(paint.distances(self.lib, points, corners), expected, rtol=0, atol=2e-14)
        np.testing.assert_allclose(paint.uv_window(self.lib, corners, uv, (-.5, -.2, -.1), (.5, .3, .8), .01), windows, rtol=0, atol=1e-6)

    def test_walk_does_not_cross_back_facing_or_disconnected_sheet(self):
        offsets = np.array([0, 1, 3, 4, 4])
        edges = np.array([1, 0, 2, 1])
        normals = np.array([(0, 0, 1), (0, 0, 1), (0, 0, -1), (0, 0, 1)])
        visited = np.zeros((4, 2), bool)
        visited[0, 0] = visited[3, 1] = True
        result = paint.walk(self.lib, offsets, edges, normals, np.zeros((4, 3)), np.ones((4, 3)),
                            np.array([(0, 0, 1), (0, 0, 1)]), np.zeros((2, 3)), np.ones((2, 3)), visited)
        np.testing.assert_array_equal(result, [[1, 0], [1, 0], [0, 0], [0, 1]])

    def test_resample_and_bounds(self):
        samples = np.zeros((5, 13), np.float32)
        samples[:, 0] = [0, .1, .3, .6, 1]
        samples[:, 7] = .2
        with patch.object(paint, 'cpu_library', return_value=None):
            kept = reference.resample(samples[:, :3], .25)
            low, high = reference.bounds(samples, .02)
        np.testing.assert_array_equal(paint.resample(self.lib, samples[:, :3], .25), kept)
        a, b = paint.bounds(self.lib, samples, .02)
        np.testing.assert_array_equal(a, low)
        np.testing.assert_array_equal(b, high)
