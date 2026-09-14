"""Micro detail encoding: convex rims, concave rings, strength-free rasters."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import unittest
from types import SimpleNamespace

import numpy as np

from open_painter.generators import micro_details
from open_painter.texture_engine import filters


def disc(n=96, radius=24, inside=1., outside=.5):
    y, x = np.indices((n, n), dtype=np.float32)
    r = np.hypot(x + .5 - n / 2, y + .5 - n / 2)
    height = np.full((n, n), outside, np.float32)
    height[r < radius] = inside
    return height, r


class MicroDetails(unittest.TestCase):
    def test_height_encoding_signs(self):
        height, r = disc()
        encoded = micro_details.encode(height, None, 2.)
        red = encoded[..., 0]
        self.assertGreater(red[(r > 20) & (r < 24)].mean(), .75)
        self.assertLess(red[(r > 24.5) & (r < 28)].mean(), .25)
        np.testing.assert_allclose(red[r > 40], .5, atol=1e-6)
        np.testing.assert_allclose(encoded[..., 1], .5)
        convex, cavity = micro_details.combine(encoded, 1., 1.)
        self.assertGreater(convex[(r > 20) & (r < 24)].mean(), .5)
        self.assertGreater(cavity[(r > 24.5) & (r < 28)].mean(), .5)
        self.assertLess(convex[r < 12].max(), 1e-5)
        np.testing.assert_array_equal(micro_details.combine(encoded, 0., 0.)[0], 0)

    def test_normal_encoding_matches_height_sign(self):
        height, r = disc()
        normal = filters.height_to_normal(height, 8)
        rgba = np.concatenate((normal, np.ones((*height.shape, 1), np.float32)), axis=-1)
        encoded = micro_details.encode(None, micro_details.slope_field(rgba), 2.)
        green = encoded[..., 1]
        self.assertGreater(green[(r > 20) & (r < 24)].mean(), .6)
        self.assertLess(green[(r > 24.5) & (r < 28)].mean(), .4)
        np.testing.assert_allclose(green[r > 40], .5, atol=1e-6)

    def test_alpha_composites_over_neutral_relief(self):
        rgba = np.zeros((8, 8, 4), np.float32)
        np.testing.assert_allclose(micro_details.height_field(rgba), .5)
        sx, sy = micro_details.slope_field(rgba)
        np.testing.assert_allclose(sx, 0)
        np.testing.assert_allclose(sy, 0)

    def test_participation(self):
        plain = SimpleNamespace(source='GENERATOR', generator='EDGE_WEAR', detail_height_anchor='', detail_normal_anchor='')
        self.assertEqual(micro_details.anchors(plain), ())
        anchored = SimpleNamespace(source='GENERATOR', generator='DIRT', detail_height_anchor='h', detail_normal_anchor='n')
        self.assertEqual(micro_details.anchors(anchored), (('HEIGHT', 'h'), ('NORMAL', 'n')))
        other = SimpleNamespace(source='GENERATOR', generator='SCRATCHES', detail_height_anchor='h', detail_normal_anchor='')
        self.assertEqual(micro_details.anchors(other), ())
        self.assertEqual(micro_details.halo(SimpleNamespace(detail_radius=2.)), 7)


if __name__ == '__main__':
    unittest.main()
