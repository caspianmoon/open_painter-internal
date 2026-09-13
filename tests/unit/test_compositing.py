"""Independent reference values for blend branches, endpoints and UV borders."""

import sys
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from open_painter.constants import BLEND_ITEMS
from open_painter.evaluation.blend_modes import blend, sample_uv


class CompositionTests(unittest.TestCase):
    def test_reference_pixels(self):
        # Low/high branches, plus a neutral backdrop. Values calculated independently.
        base = np.array([0.2, 0.8, 0.5])
        source = np.array([0.3, 0.7, 0.5])
        expected = {
            "DIVIDE": [2 / 3, 1, 1],
            "DARKEN": [0.2, 0.7, 0.5],
            "LIGHTEN": [0.3, 0.8, 0.5],
            "DIFFERENCE": [0.1, 0.1, 0],
            "EXCLUSION": [0.38, 0.38, 0.5],
            "SCREEN": [0.44, 0.94, 0.75],
            "LINEAR_BURN": [0, 0.5, 0],
            "COLOR_BURN": [0, 5 / 7, 0],
            "COLOR_DODGE": [2 / 7, 1, 1],
            "SOFT_LIGHT": [0.136, 0.8 + 0.4 * (np.sqrt(0.8) - 0.8), 0.5],
            "HARD_LIGHT": [0.12, 0.88, 0.5],
            "VIVID_LIGHT": [0, 1, 0.5],
            "LINEAR_LIGHT": [0, 1, 0.5],
            "PIN_LIGHT": [0.2, 0.8, 0.5],
            "DISABLE": [0.2, 0.8, 0.5],
        }
        for mode, values in expected.items():
            with self.subTest(mode=mode):
                np.testing.assert_allclose(blend(base, source, 1, mode), values, atol=1e-7)
                np.testing.assert_allclose(
                    blend(base, source, 0.25, mode),
                    base * 0.75 + np.array(values) * 0.25,
                    atol=1e-7,
                )

    def test_all_modes_finite_and_zero_opacity(self):
        base, source = np.meshgrid(
            np.array([0, 0.001, 0.25, 0.5, 0.75, 0.999, 1]), np.array([0, 0.2, 0.5, 0.8, 1])
        )
        with np.errstate(divide="raise", invalid="raise", over="raise"):
            for mode, _, _ in BLEND_ITEMS:
                b, s = base, source
                if mode in {"HUE","SATURATION","COLOR","LUMINOSITY","NORMAL_COMBINE","RNM","NORMAL_ADD","NORMAL_REPLACE"}:
                    b, s = np.repeat(base[...,None],3,axis=-1), np.repeat(source[...,None],3,axis=-1)
                self.assertTrue(np.isfinite(blend(b, s, 1, mode)).all(), mode)
                np.testing.assert_allclose(blend(b, s, 0, mode), b)
        np.testing.assert_equal(
            blend(np.array([0.0, 1.0]), np.array([1.0, 0.0]), 1, "COLOR_DODGE"), [0, 1]
        )
        np.testing.assert_equal(
            blend(np.array([0.0, 1.0]), np.array([1.0, 0.0]), 1, "COLOR_BURN"), [0, 1]
        )

    def test_shared_raster_cache_budget(self):
        from open_painter.evaluation.raster_cache import RasterCache
        cache=RasterCache(budget=128)
        value=np.ones(16,dtype=np.float32)
        cache["a"] = value
        cache["b"] = value[:2]
        self.assertEqual(cache.used, 64)  # same owning allocation
        cache["c"] = value.copy()
        _ = cache["a"]
        cache["d"] = value.copy()
        self.assertNotIn("b", cache)
        self.assertNotIn("c", cache)
        self.assertEqual(cache.used, 128)
        cache["oversized"] = np.ones(64, dtype=np.float32)
        self.assertNotIn("oversized", cache)
        cache.clear()
        self.assertEqual(cache.used, 0)

    def test_nonseparable_and_normal_invariants(self):
        base = np.array([.2,.45,.7],np.float32)
        source = np.array([.8,.3,.1],np.float32)
        lum = lambda rgb: np.dot(rgb,[.3,.59,.11])
        for kind in ("HUE","SATURATION","COLOR"):
            np.testing.assert_allclose(lum(blend(base,source,1,kind)),lum(base),atol=1e-6)
        np.testing.assert_allclose(lum(blend(base,source,1,"LUMINOSITY")),lum(source),atol=1e-6)
        flat = np.array([.5,.5,1],np.float32)
        tilted = np.array([.8,.5,.9],np.float32)
        for mode in ("RNM","NORMAL_COMBINE","NORMAL_ADD"):
            np.testing.assert_allclose(blend(tilted,flat,1,mode),tilted,atol=1e-6)
        np.testing.assert_allclose(blend(flat,tilted,1,"NORMAL_REPLACE"),tilted,atol=1e-6)

    def test_nearest_repeat_and_transparent_border(self):
        data = np.array([[[1, 0, 0, 1], [0, 1, 0, 1]]], dtype=np.float32)
        u, v = np.array([0.49, 0.51, 1.25, -0.25]), np.full(4, 0.5)
        result = sample_uv(data, u, v, "NEAREST")
        np.testing.assert_equal(result, data[0, [0, 1, 0, 1]])
        clipped = sample_uv(data, u, v, "NEAREST", "NONE")
        np.testing.assert_equal(clipped[2:], 0)
        np.testing.assert_allclose(sample_uv(data, np.array(0.5), np.array(0.5)), [0.5, 0.5, 0, 1])
        # None clamps the inside edge rather than leaking color from the opposite side.
        np.testing.assert_equal(
            sample_uv(data, np.array(0.0), np.array(0.5), tiling="NONE"), data[0, 0]
        )
