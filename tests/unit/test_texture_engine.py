"""Pure CPU texture math; run with Blender's bundled Python or NumPy Python."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from open_painter.texture_engine import filters, procedurals


class ProceduralTests(unittest.TestCase):
    def setUp(self):
        v, u = np.indices((57, 73), dtype=np.float32)
        self.u, self.v = u / 73 - 0.35, v / 57 - 0.23

    def test_catalog_is_deterministic_bounded_and_partition_invariant(self):
        self.assertEqual(len(procedurals.NOISES), 48)
        for name in procedurals.CATALOG:
            with self.subTest(name=name):
                a = procedurals.evaluate(name, self.u, self.v, seed=17, scale=9.37)
                b = procedurals.evaluate(name, self.u, self.v, seed=17, scale=9.37)
                self.assertEqual(a.dtype, np.float32)
                self.assertTrue(np.isfinite(a).all())
                self.assertGreaterEqual(float(a.min()), 0)
                self.assertLessEqual(float(a.max()), 1)
                np.testing.assert_array_equal(a, b)
                tiled = np.concatenate([procedurals.evaluate(name, u, v, seed=17, scale=9.37)
                                        for u, v in zip(np.split(self.u, 3), np.split(self.v, 3))])
                np.testing.assert_array_equal(a, tiled)
                if name != "SOLID":
                    self.assertGreater(float(a.std()), 0.005)

    def test_seed_and_lattice_gradient_behavior(self):
        a = procedurals.evaluate("PERLIN", self.u, self.v)
        b = procedurals.evaluate("PERLIN", self.u, self.v, seed=12)
        self.assertGreater(float(np.abs(a - b).mean()), 0.03)
        x = np.arange(-7, 8, dtype=np.float32)
        np.testing.assert_array_equal(procedurals.evaluate("PERLIN", x, x * 0, scale=1), np.full_like(x, 0.5))
        eps = np.float32(0.0005)
        positions = np.array([-eps, 0, eps], np.float32)
        noise = procedurals.evaluate("PERLIN", positions, positions * 0 + 0.37, scale=1)
        self.assertLess(abs(float(noise[2] - 2 * noise[1] + noise[0])), 1e-6)

    def test_directionality(self):
        brush = procedurals.evaluate("BRUSHED_METAL_LINES", self.u, self.v, scale=3.12)
        dx, dy = np.abs(np.diff(brush, axis=1)).mean(), np.abs(np.diff(brush, axis=0)).mean()
        self.assertGreater(dx, dy * 3)
        rotated = procedurals.evaluate("BRUSHED_METAL_LINES", self.u, self.v, scale=3.12, angle=90)
        dx, dy = np.abs(np.diff(rotated, axis=1)).mean(), np.abs(np.diff(rotated, axis=0)).mean()
        self.assertGreater(dy, dx * 3)

    def test_distinct_cellular_and_surface_patterns(self):
        names = ["PERLIN", "SIMPLEX", "VALUE", "VORONOI", "WORLEY", "CRACKS",
                 "WOOD_GRAIN", "FABRIC_WEAVE", "SCRATCHES", "FINGERPRINTS", "VEINS"]
        results = [procedurals.evaluate(k, self.u, self.v, scale=3.87) for k in names]
        for i, a in enumerate(results):
            for b in results[i + 1:]:
                self.assertGreater(float(np.abs(a - b).mean()), 0.025)

    def test_broadcasting_and_invalid_source(self):
        self.assertEqual(procedurals.evaluate("FBM", np.arange(3)[None, :], np.arange(5)[:, None]).shape, (5, 3))
        with self.assertRaisesRegex(ValueError, "Unknown"):
            procedurals.evaluate("not-a-source", self.u, self.v)
        with self.assertRaisesRegex(ValueError, "positive"):
            procedurals.evaluate("FBM", self.u, self.v, scale=0)

    def test_lattice_hash_matches_integer_reference(self):
        x = np.array([-1000000000, -123, -1, 0, 1, 893, 1000000000], np.int64)
        y = x[::-1].copy()
        expected = []
        for a, b in zip(x, y):
            h = (int(a) * 374761393 + int(b) * 668265263 + 2147483647 * 2246822519) & 0xFFFFFFFF
            h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
            h ^= h >> 16
            expected.append(h)
        expected = np.array(expected, np.float32) * np.float32(1 / 4294967296)
        np.testing.assert_array_equal(procedurals._hash(x, y, 2147483647), expected)
        rng = np.random.default_rng(831)
        x = rng.integers(-(2 ** 40), 2 ** 40, size=257, dtype=np.int64)
        y = rng.integers(-(2 ** 40), 2 ** 40, size=257, dtype=np.int64)
        for seed in (-2147483647, -1, 0, 1, 2 ** 40 + 19):
            expected = []
            for a, b in zip(x, y):
                h = (int(a) * 374761393 + int(b) * 668265263 + seed * 2246822519) & 0xFFFFFFFF
                h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
                expected.append(h ^ (h >> 16))
            np.testing.assert_array_equal(procedurals._hash(x, y, seed),
                np.array(expected, np.float32) * np.float32(1 / 4294967296))


class FilterTests(unittest.TestCase):
    def test_levels_threshold_gamma(self):
        source = np.array([[0.2, 0.35, 0.8]], np.float32)
        np.testing.assert_allclose(filters.apply(source, "LEVELS", black=0.2, white=0.8, gamma=2, output_black=0.1, output_white=0.9), [[0.1, 0.5, 0.9]], atol=1e-7)
        np.testing.assert_array_equal(filters.apply(source, "THRESHOLD", position=0.3), [[0, 1, 1]])
        np.testing.assert_allclose(filters.apply(source, "GAMMA", gamma=2), np.sqrt(source))
        with self.assertRaisesRegex(ValueError, "White"):
            filters.apply(source, "LEVELS", black=0.5, white=0.2)

    def test_gaussian_impulse_no_wrap_and_halo_equivalence(self):
        impulse = np.zeros((35, 35), np.float32)
        impulse[17, 17] = 1
        blurred = filters.apply(impulse, "GAUSSIAN_BLUR", radius=2)
        self.assertAlmostEqual(float(blurred.sum()), 1, places=6)
        np.testing.assert_array_equal(blurred, blurred[::-1])
        np.testing.assert_array_equal(blurred, blurred.T)
        impulse[:] = 0
        impulse[17, 0] = 1
        blurred = filters.apply(impulse, "GAUSSIAN_BLUR", radius=2)
        self.assertEqual(float(blurred[:, -1].max()), 0)
        image = np.random.default_rng(2).random((32, 24), dtype=np.float32)
        whole = filters.apply(image, "GAUSSIAN_BLUR", radius=2)
        top = filters.apply(image[:22], "GAUSSIAN_BLUR", radius=2)[:16]
        bottom = filters.apply(image[10:], "GAUSSIAN_BLUR", radius=2)[6:]
        np.testing.assert_array_equal(whole, np.concatenate((top, bottom)))

    def test_wide_blurs_match_direct_kernels(self):
        # Kernels wider than FFT_TAPS convolve by FFT and the box blur by running
        # sums; both equal the direct clamped kernel to float32 rounding.
        def direct(data, kernel, axis):
            half = len(kernel) // 2
            pad = [(0, 0)] * data.ndim
            pad[axis] = (half, half)
            padded = np.pad(data.astype(np.float64), pad, mode="edge")
            taps = [padded[(slice(None),) * axis + (slice(offset, offset + data.shape[axis]),)] * weight
                    for offset, weight in enumerate(kernel.astype(np.float64))]
            return np.sum(taps, axis=0)
        rng = np.random.default_rng(7)
        for shape in ((1, 9), (9, 1), (41, 29), (30, 45, 3), (17, 17, 4)):
            data = rng.random(shape, dtype=np.float32)
            for sigma in (6.5, 13., 40., 64.):
                half = min(int(np.ceil(sigma * 3)), 192)
                self.assertGreater(2 * half + 1, filters.FFT_TAPS)
                x = np.arange(-half, half + 1, dtype=np.float32)
                kernel = np.exp(-(x * x) / (2 * sigma * sigma))
                kernel /= kernel.sum()
                expected = direct(direct(data, kernel, 1), kernel, 0)
                np.testing.assert_allclose(filters.gaussian_blur(data, sigma), expected, atol=1e-6, rtol=0,
                                           err_msg=f"{shape}, sigma {sigma}")
            for radius in (0, 1, 17, 100, 192):
                kernel = np.full(2 * radius + 1, 1 / (2 * radius + 1), np.float32)
                expected = np.clip(direct(direct(data, kernel, 0), kernel, 1), 0, 1)
                np.testing.assert_allclose(filters.apply(data, "BLUR", radius=radius), expected, atol=1e-6, rtol=0,
                                           err_msg=f"{shape}, radius {radius}")
        constant = np.full((50, 70, 2), .37, np.float32)
        np.testing.assert_array_equal(filters.apply(constant, "BLUR", radius=9), constant)
        np.testing.assert_array_equal(filters.apply(constant, "GAUSSIAN_BLUR", radius=20), constant)
        impulse = np.zeros((301, 301), np.float32)
        impulse[150, 150] = 1
        blurred = filters.apply(impulse, "GAUSSIAN_BLUR", radius=20)
        self.assertAlmostEqual(float(blurred.sum()), 1, places=5)
        np.testing.assert_allclose(blurred, blurred.T, atol=1e-7)
        impulse[150, 150], impulse[150, 0] = 0, 1
        blurred = filters.apply(impulse, "GAUSSIAN_BLUR", radius=20)
        # FFT rounding leaves ~1e-18 beyond the support rather than exact zeros.
        self.assertLess(float(blurred[:, 100:].max()), 1e-12)

    def test_wide_blur_cost_is_radius_independent(self):
        # A 4k blur at the widest radius took tens of seconds in one scheduler
        # step when every tap was a full pass over the image.
        from time import perf_counter
        data = np.random.default_rng(3).random((1024, 1024, 3), dtype=np.float32)
        def seconds(kind, radius):
            best = np.inf
            for _ in range(2):
                started = perf_counter()
                filters.apply(data, kind, radius=radius)
                best = min(best, perf_counter() - started)
            return best
        narrow, wide = seconds("GAUSSIAN_BLUR", 2), seconds("GAUSSIAN_BLUR", 64)
        self.assertLess(wide, narrow * 8, (narrow, wide))
        narrow, wide = seconds("BLUR", 2), seconds("BLUR", 64)
        self.assertLess(wide, narrow * 8, (narrow, wide))

    def test_morphology_expected_area_and_constant(self):
        image = np.zeros((9, 9), np.float32)
        image[4, 4] = 1
        expanded = filters.apply(image, "DILATE", radius=1)
        self.assertEqual(expanded.sum(), 9)
        np.testing.assert_array_equal(filters.apply(expanded, "ERODE", radius=1), image)
        np.testing.assert_array_equal(filters.apply(np.ones_like(image), "ERODE", radius=3), np.ones_like(image))

    def test_warp_sampling_and_slope_identity(self):
        image = np.arange(25, dtype=np.float32).reshape(5, 5) / 25
        # +X lookup displaces content one pixel left; last column clamps.
        field = np.ones((5, 5), np.float32)
        result = filters.apply(image, "DIRECTIONAL_WARP", field=field, strength=1, angle=0)
        np.testing.assert_array_equal(result[:, :-1], image[:, 1:])
        np.testing.assert_array_equal(result[:, -1], image[:, -1])
        np.testing.assert_array_equal(filters.apply(image, "SLOPE_BLUR", field=field, strength=20), image)
        np.testing.assert_array_equal(filters.apply(image, "NON_UNIFORM_TRANSFORM"), image)
        with self.assertRaisesRegex(ValueError, "two-channel"):
            filters.apply(image, "VECTOR_WARP")

    def test_color_math_and_alpha_preservation(self):
        rgb = np.array([[[1, 0, 0, 0.2], [0, 1, 0, 0.4], [0, 0, 1, 0.8]]], np.float32)
        shifted = filters.apply(rgb, "HSV", hue=1 / 3)
        np.testing.assert_allclose(shifted[..., :3], [[[0, 1, 0], [0, 0, 1], [1, 0, 0]]], atol=1e-6)
        np.testing.assert_array_equal(shifted[..., 3], rgb[..., 3])
        np.testing.assert_allclose(filters.apply(rgb, "HSL"), rgb, atol=1e-7)
        gray = np.array([[0, 0.5, 1]], np.float32)
        gradient = filters.apply(gray, "GRADIENT_MAP", color=(1, 0, 0), color2=(0, 0, 1))
        np.testing.assert_allclose(gradient, [[[1, 0, 0], [0.5, 0, 0.5], [0, 0, 1]]])
        stops = [(0, (0, 0, 0)), (0.5, (0, 1, 0)), (1, (1, 1, 1))]
        np.testing.assert_array_equal(filters.apply(gray, "GRADIENT_MAP", stops=stops)[0, 1], [0, 1, 0])

    def test_normal_math(self):
        flat = np.tile(np.array([0.5, 0.5, 1], np.float32), (4, 5, 1))
        normal = filters.height_to_normal(np.arange(20, dtype=np.float32).reshape(4, 5) / 20, 3)
        np.testing.assert_allclose(filters.height_to_normal(np.ones((4, 5), np.float32)), flat)
        np.testing.assert_allclose(filters.normal_combine(flat, normal), normal, atol=1e-7)
        np.testing.assert_allclose(filters.normal_combine(normal, flat), normal, atol=1e-7)
        np.testing.assert_allclose(np.linalg.norm(filters.normal_combine(normal, normal) * 2 - 1, axis=-1), 1, atol=1e-7)
        np.testing.assert_allclose(filters.apply(normal, "NORMAL_STRENGTH", strength=0), flat)
        self.assertLess(float(normal[2, 2, 0]), 0.5)
        self.assertLess(float(normal[2, 2, 1]), 0.5)
        n1 = np.array([.6, 0, .8], np.float32)
        n2 = np.array([0, .6, .8], np.float32)
        base, detail = (n1 + 1) * .5, (n2 + 1) * .5
        whiteout = np.array([.6, .6, .64], np.float32)
        additive = np.array([.6, .6, .6], np.float32)
        np.testing.assert_allclose(filters.normal_combine(base, detail, 'WHITEOUT'),
                                   (whiteout / np.linalg.norm(whiteout) + 1) * .5, atol=1e-7)
        np.testing.assert_allclose(filters.normal_combine(base, detail, 'ADD'),
                                   (additive / np.linalg.norm(additive) + 1) * .5, atol=1e-7)
        self.assertGreater(float(np.linalg.norm(filters.normal_combine(base, detail, 'WHITEOUT')
                                                - filters.normal_combine(base, detail, 'ADD'))), .01)

    def test_catalog_finite_and_immutable(self):
        original = np.random.default_rng(14).random((13, 17, 4), dtype=np.float32)
        original[..., 2] = 0.8
        for name in filters.CATALOG:
            with self.subTest(name=name):
                source = original.copy()
                result = filters.apply(source, name, field=original[..., :2], radius=2, unused_argument=True)
                self.assertEqual(result.shape[:2], source.shape[:2])
                self.assertEqual(result.dtype, np.float32)
                self.assertTrue(np.isfinite(result).all())
                self.assertGreaterEqual(float(result.min()), 0)
                self.assertLessEqual(float(result.max()), 1)
                np.testing.assert_array_equal(source, original)


if __name__ == "__main__":
    unittest.main()
