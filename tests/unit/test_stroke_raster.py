"""The stroke kernel on synthetic geometry: disc falloff, pressure, bands, unions, packing."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import unittest

import numpy as np

from open_painter.painting.stroke_raster import (bounds, dilate, kernel, pack_samples, resample, unpack_samples,
                                                  union_rows)


def sample(centre, normal=(0, 0, 1), pressure=1.0, radius=.5, tangent=(1, 0, 0)):
    return np.array([*centre, *normal, pressure, radius, 0, 0, *tangent], np.float32)


def grid(n=41, extent=2.0, z=0.0):
    axis = np.linspace(-extent, extent, n, dtype=np.float32)
    x, y = np.meshgrid(axis, axis)
    positions = np.stack((x.ravel(), y.ravel(), np.full(x.size, z, np.float32)), axis=1)
    normals = np.tile(np.array([0, 0, 1], np.float32), (len(positions), 1))
    return positions, normals


class Kernel(unittest.TestCase):
    def test_disc_falloff_pressure_and_facing(self):
        positions, normals = grid()
        alpha = kernel(positions, normals, np.array([sample((0, 0, 0), hardness := .5) if False else sample((0, 0, 0))]),
                       hardness=.5)
        distance = np.linalg.norm(positions[:, :2], axis=1)
        self.assertAlmostEqual(float(alpha[distance.argmin()]), 1.0)
        self.assertTrue(np.all(alpha[distance >= .5] == 0))
        near = (distance > .26) & (distance < .49)
        expected = np.clip((1 - distance[near] / .5) / .5, 0, 1)
        np.testing.assert_allclose(alpha[near], expected, atol=1e-5)
        half = kernel(positions, normals, np.array([sample((0, 0, 0), pressure=.5)]), hardness=.5)
        np.testing.assert_allclose(half, alpha * .5, atol=1e-6)
        back = kernel(positions, -normals, np.array([sample((0, 0, 0))]))
        self.assertEqual(back.max(), 0.0)

    def test_depth_band_and_planar(self):
        positions, normals = grid(z=.8)
        self.assertEqual(kernel(positions, normals, np.array([sample((0, 0, 0))])).max(), 0.0)   # beyond one radius
        positions, normals = grid(z=.3)
        self.assertGreater(kernel(positions, normals, np.array([sample((0, 0, 0))])).max(), .99)
        # Planar projection along +Z covers a tilted sample's disc in the plane, not along its normal.
        tilted = np.array([sample((0, 0, 0), normal=(1, 0, 0))])
        positions, normals = grid(z=0.0)
        self.assertEqual(kernel(positions, normals, tilted).max(), 0.0)              # surface faces away from the disc axis
        planar = kernel(positions, normals, tilted, planar=(0, 0, 1))
        self.assertGreater(planar.max(), .99)

    def test_unions_max_and_flow(self):
        positions, normals = grid()
        samples = np.array([sample((0, 0, 0)), sample((.1, 0, 0))])
        maxed = kernel(positions, normals, samples, hardness=0.0)
        singles = [kernel(positions, normals, samples[i:i + 1], hardness=0.0) for i in range(2)]
        np.testing.assert_allclose(maxed, np.maximum(*singles), atol=1e-6)
        flowed = kernel(positions, normals, samples, hardness=0.0, accumulate='FLOW', flow=.5)
        expected = 1 - (1 - singles[0] * .5) * (1 - singles[1] * .5)
        np.testing.assert_allclose(flowed, expected, atol=1e-6)
        np.testing.assert_allclose(union_rows(singles[0], singles[1]), np.maximum(*singles))

    def test_chunking_matches_single_pass(self):
        positions, normals = grid(n=61)
        rng = np.random.default_rng(3)
        centres = np.stack((np.linspace(-1.5, 1.5, 600), rng.normal(0, .1, 600), np.zeros(600)), axis=1)
        samples = np.array([sample(c, radius=.3, pressure=float(p)) for c, p in zip(centres, rng.uniform(.3, 1, 600))])
        whole = kernel(positions, normals, samples, hardness=.6)
        import open_painter.painting.stroke_raster as module
        original = module.CHUNK
        module.CHUNK = 7
        try:
            chunked = kernel(positions, normals, samples, hardness=.6)
        finally:
            module.CHUNK = original
        np.testing.assert_allclose(chunked, whole, atol=1e-6)

    def test_alpha_texture_rotates_with_the_stroke(self):
        positions, normals = grid(n=81, extent=1.0)
        texture = np.zeros((16, 16), np.float32)
        texture[:, 8:] = 1.0     # right half of the disc frame
        along_x = kernel(positions, normals, np.array([sample((0, 0, 0), tangent=(1, 0, 0))]), hardness=1.0,
                         alpha_texture=texture)
        along_y = kernel(positions, normals, np.array([sample((0, 0, 0), tangent=(0, 1, 0))]), hardness=1.0,
                         alpha_texture=texture)
        right = (positions[:, 0] > .2) & (positions[:, 0] < .45) & (np.abs(positions[:, 1]) < .1)
        left = (positions[:, 0] < -.2) & (positions[:, 0] > -.45) & (np.abs(positions[:, 1]) < .1)
        up = (positions[:, 1] > .2) & (positions[:, 1] < .45) & (np.abs(positions[:, 0]) < .1)
        self.assertGreater(along_x[right].mean(), .9)
        self.assertLess(along_x[left].mean(), .1)
        self.assertGreater(along_y[up].mean(), .9)

    def test_rake_first_sample_without_tangent_keeps_the_alpha_shape(self):
        positions, normals = grid(n=81, extent=1.0)
        texture = np.zeros((16, 16), np.float32)
        texture[:, 8:] = 1.0
        # The vector brush records the first sample of a gesture with a zero tangent; the
        # second sample lies outside the grid, so only the first stamp is measured.
        samples = np.array([sample((0, 0, 0), tangent=(0, 0, 0)), sample((3, 0, 0), tangent=(1, 0, 0))])
        alpha = kernel(positions, normals, samples, hardness=1.0, alpha_texture=texture)
        up = (positions[:, 1] > .2) & (positions[:, 1] < .45) & (np.abs(positions[:, 0]) < .1)
        down = (positions[:, 1] < -.2) & (positions[:, 1] > -.45) & (np.abs(positions[:, 0]) < .1)
        self.assertGreater(alpha[up].mean(), .9)      # the helper frame, as a single click uses
        self.assertLess(alpha[down].mean(), .1)       # not a solid disc from the texture's centre texel

    def test_packing_resample_bounds_dilate(self):
        samples = np.array([sample((i * .1, 0, 0), radius=.2) for i in range(5)])
        self.assertTrue(np.array_equal(unpack_samples(pack_samples(samples)), samples))
        self.assertEqual(unpack_samples('').shape, (0, 13))
        np.testing.assert_array_equal(resample(samples[:, :3], .25), [0, 3])
        lo, hi = bounds(samples)
        np.testing.assert_allclose(lo, (-.2, -.2, -.2)); np.testing.assert_allclose(hi, (.6, .2, .2))
        mask = np.zeros((5, 5), np.float32); mask[2, 2] = 1.0
        coverage = np.ones((5, 5), bool); coverage[2, 3] = False
        grown = dilate(mask, coverage)
        self.assertEqual(grown[2, 3], 1.0)
        self.assertEqual(grown[2, 1], 0.0)   # covered neighbours keep their own value


if __name__ == '__main__':
    unittest.main()
