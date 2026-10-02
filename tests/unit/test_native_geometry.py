"""Native geometry compared with independent NumPy/reference implementations."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.native_bridge import load_library
from open_painter.native_bridge.geometry import (Bounds, rasterize, adjacency, frames, interpolate, padding_donors, padding_extend,
                                                 raster_assign, raster_interpolate, raster_ownership)
from open_painter.geometry import uv_rasterizer
from open_painter.painting import surface_reach


@unittest.skipUnless(os.environ.get('OP_TEST_NATIVE_LIBRARY'), 'native build not supplied')
class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])

    def test_mesh_nonmanifold_degenerate_and_large_vertex_ids(self):
        rng = np.random.default_rng(9224)
        for vertices in (np.empty((0, 3), np.int64),
                         np.array([[0, 1, 2], [1, 0, 3], [0, 1, 4], [4, 4, 0], [9, 9, 9]]),
                         rng.integers(0, 80, (1800, 3), dtype=np.int64)):
            with patch('open_painter.native_bridge.lifecycle.cpu_library', return_value=None):
                expected = surface_reach.adjacency(vertices)
            for a, b in zip(adjacency(self.lib, vertices), expected):
                np.testing.assert_array_equal(a, b)
            # Pairwise edge keys cannot overflow even when vertex IDs approach int64 max.
            for a, b in zip(adjacency(self.lib, vertices + 2**62), expected):
                np.testing.assert_array_equal(a, b)

    def test_tangent_and_interpolation_parity(self):
        rng = np.random.default_rng(1954)
        p = rng.normal(size=(2000, 3, 3))
        uv = rng.normal(size=(2000, 3, 2))
        uv[:20] = 0
        e, f = p[:, 1:] - p[:, :1], uv[:, 1:] - uv[:, :1]
        det = f[:, 0, 0] * f[:, 1, 1] - f[:, 0, 1] * f[:, 1, 0]
        inverse = np.zeros_like(f)
        ok = np.abs(det) > 1e-14
        inverse[ok, 0, 0] = f[ok, 1, 1] / det[ok]
        inverse[ok, 1, 1] = f[ok, 0, 0] / det[ok]
        inverse[ok, 0, 1] = -f[ok, 0, 1] / det[ok]
        inverse[ok, 1, 0] = -f[ok, 1, 0] / det[ok]
        expected = inverse @ e
        expected /= np.maximum(np.linalg.norm(expected, axis=2, keepdims=True), 1e-12)
        np.testing.assert_allclose(frames(self.lib, p, uv), expected, atol=2e-14, rtol=0)
        w = rng.random((len(p), 3), dtype=np.float32)
        p = p.astype(np.float32)
        with patch('open_painter.native_bridge.lifecycle.cpu_library', return_value=None):
            expected = uv_rasterizer.interpolate(w, p)
        np.testing.assert_array_equal(interpolate(self.lib, w, p), expected)

    def test_padding_matches_bruteforce_ties_and_banded_reference(self):
        from open_painter.export import padding
        rng = np.random.default_rng(4228)
        for n in (1, 7, 43, 257):
            covered = rng.random((n, n)) < .02
            islands = np.where(covered, rng.integers(0, 10, (n, n)), -1).astype(np.int32)
            for limit in (0, 1, 4, 16, 64):
                with patch('open_painter.native_bridge.lifecycle.cpu_library', return_value=None):
                    expected = padding._banded(covered, islands, limit)
                actual = padding_donors(self.lib, covered, islands, limit)
                for a, b in zip(actual, expected):
                    np.testing.assert_array_equal(a, b)
                if n < 50:
                    for a, b in zip(actual, padding.reference_donors(covered, islands, limit)):
                        np.testing.assert_array_equal(a, b)

    def test_padding_extend_is_the_numpy_gather(self):
        from open_painter.export import padding
        from open_painter.native_bridge.core import NativeError
        rng = np.random.default_rng(6141)
        for n, limit, channels in ((1, 3, 4), (37, 4, 1), (96, 16, 3), (128, 'FILL', 4), (257, 64, 2)):
            covered = rng.random((n, n)) < .03
            islands = np.where(covered, rng.integers(0, 6, (n, n)), -1).astype(np.int32)
            donor, _ = padding.nearest_donors(covered, islands, limit)
            values = rng.normal(size=(n, n, channels)).astype(np.float32)
            with patch('open_painter.native_bridge.lifecycle.cpu_library', return_value=None):
                expected = padding.extend(values, donor)
            np.testing.assert_array_equal(padding_extend(self.lib, values, donor, np.empty_like(values)), expected)
            in_place = values.copy()
            padding_extend(self.lib, in_place, donor, in_place)
            np.testing.assert_array_equal(in_place, expected)
            # The exporter's entry point: in place when asked, into a copy otherwise.
            with patch('open_painter.native_bridge.lifecycle.cpu_library', return_value=self.lib):
                copied = padding.extend(values, donor)
                self.assertIsNot(copied, values)
                np.testing.assert_array_equal(copied, expected)
                self.assertIs(padding.extend(values, donor, in_place=True), values)
                np.testing.assert_array_equal(values, expected)
        # A donor with a donor of its own would read a texel already overwritten in place.
        chained = np.array([[-1, 0], [1, -1]], np.int32)
        values = np.zeros((2, 2, 4), np.float32)
        with self.assertRaises(NativeError):
            padding_extend(self.lib, values, chained, values)

    def test_bounds_exact_queries_and_owned_snapshot(self):
        rng = np.random.default_rng(481)
        low = rng.normal(size=(9000, 3))
        high = low + rng.uniform(0, .1, size=low.shape)
        native = Bounds(self.lib, low, high)
        for _ in range(40):
            a = rng.normal(size=3)
            b = a + rng.uniform(0, 2, size=3)
            expected = np.flatnonzero(np.all(low <= b, axis=1) & np.all(high >= a, axis=1))
            np.testing.assert_array_equal(native.query(a, b), expected)
        result = native.query((-1, -1, -1), (1, 1, 1))
        low[:] = high[:] = 1e9
        np.testing.assert_array_equal(native.query((-1, -1, -1), (1, 1, 1)), result)
        native.close()

    def test_raster_interpolate_is_rasterize_interpolate_and_scatter(self):
        rng = np.random.default_rng(4471)
        uv = rng.uniform(-.1, 1.1, size=(300, 3, 2)).astype(np.float32)
        uv[0] = uv[1] = ((0, 0), (1, 0), (0, 1))   # overlapping: the later triangle wins
        uv[2] = 0                                   # degenerate
        positions = rng.normal(size=(300, 3, 3)) * 40   # float64: NumPy's float64 arithmetic
        normals = rng.normal(size=(300, 3, 3)).astype(np.float32)
        for n in (1, 7, 96):
            with self.subTest(n=n):
                rows, cols, tri, weights = rasterize(self.lib, uv, n, True, None)
                expected = [np.full((n, n, 3), -7, np.float32) for _ in range(2)]
                expected[0][rows, cols] = uv_rasterizer.interpolate(weights, positions[tri])
                expected[1][rows, cols] = interpolate(self.lib, weights, normals[tri])
                covered = np.zeros((n, n), bool)
                found = [np.full((n, n, 3), -7, np.float32) for _ in range(2)]
                raster_interpolate(self.lib, uv, n, [(positions, found[0]), (normals, found[1])], covered)
                for a, b in zip(found, expected):
                    np.testing.assert_array_equal(a.view(np.uint32), b.view(np.uint32))
                marked = np.zeros((n, n), bool)
                marked[rows, cols] = True
                np.testing.assert_array_equal(covered, marked)
        with self.assertRaises(ValueError):
            raster_interpolate(self.lib, uv, 8, [(positions.astype(np.float16), np.empty((8, 8, 3), np.float32))])
        with self.assertRaises(ValueError):
            raster_interpolate(self.lib, uv, 8, [(normals, np.empty((8, 8, 2), np.float32))])

    def test_raster_assign_is_rasterize_and_scatter(self):
        rng = np.random.default_rng(812)
        uv = rng.uniform(-.1, 1.1, size=(260, 3, 2)).astype(np.float32)
        uv[3] = uv[4] = ((0, 0), (1, 0), (0, 1))   # overlapping: the later triangle wins
        values = [rng.normal(size=(260, 3)).astype(np.float32), rng.normal(size=(260, 3)) * 1e3,
                  rng.integers(-9, 9, 260).astype(np.int32), rng.integers(-9, 9, 260) + (1 << 33)]
        n = 72
        rows, cols, tri, _ = rasterize(self.lib, uv, n, True, None)
        maps, expected = [], []
        for v in values:
            shape = (n, n, 3) if v.ndim == 2 else (n, n)
            dtype = np.float32 if v.dtype.kind == 'f' else np.int32
            maps.append(np.full(shape, -3, dtype))
            reference = np.full(shape, -3, dtype)
            reference[rows, cols] = v[tri]   # NumPy's assignment casts (float64 rounds, int64 wraps)
            expected.append(reference)
        covered = np.zeros((n, n), bool)
        raster_assign(self.lib, uv, n, list(zip(values, maps)), covered)
        for a, b in zip(maps, expected):
            np.testing.assert_array_equal(a.view(np.uint32), b.view(np.uint32))
        marked = np.zeros((n, n), bool)
        marked[rows, cols] = True
        np.testing.assert_array_equal(covered, marked)
        with self.assertRaises(ValueError):   # a float into an int map is not an assignment cast here
            raster_assign(self.lib, uv, n, [(values[0], np.zeros((n, n, 3), np.int32))])

    def test_raster_ownership_is_build_ownerships_batch_loop(self):
        rng = np.random.default_rng(6203)
        uv = rng.uniform(-.1, 1.1, size=(240, 3, 2)).astype(np.float32)
        uv[5] = uv[9] = uv[100] = ((0, 0), (1, 0), (0, 1))   # overlaps within and across batches
        uv[7] = 0                                             # degenerate
        of_triangle = rng.integers(-1, 6, len(uv)).astype(np.int64)
        n = 80
        for split in ([0], [0, 100], [0, 7, 9, 101, 239]):
            with self.subTest(batches=split):
                batches = np.split(np.arange(len(uv)), split[1:])
                # build_ownership's NumPy loop over the same batches, on the native samples.
                island = np.full((n, n), -1, np.int32)
                covered = np.zeros((n, n), bool)
                conflicts = np.zeros((n, n), bool)
                for batch in batches:
                    rows, cols, tri, _ = rasterize(self.lib, uv[batch], n, False, None)
                    owner = of_triangle[batch[tri]].astype(np.int32)
                    flat = rows * n + cols
                    first = np.full(n * n, -2, np.int32)
                    first_order = np.unique(flat, return_index=True)[1]
                    first[flat[first_order]] = owner[first_order]
                    island.reshape(-1)[flat] = owner
                    covered.reshape(-1)[flat] = True
                    conflicts.reshape(-1)[flat[first[flat] != owner]] = True
                found = (np.full((n, n), -1, np.int32), np.zeros((n, n), bool), np.zeros((n, n), bool))
                raster_ownership(self.lib, uv, n, of_triangle.astype(np.int32), split, *found)
                for a, b in zip(found, (island, covered, conflicts)):
                    np.testing.assert_array_equal(a, b)
                self.assertTrue(conflicts.any())

    def test_uv_overlap_clipping_and_barycentric_parity(self):
        rng = np.random.default_rng(934)
        uv = rng.uniform(-.1, 1.1, size=(80, 3, 2)).astype(np.float32)
        uv[0] = uv[1] = ((0, 0), (1, 0), (0, 1))
        uv[2] = 0
        for unique in (False, True):
            for clip in (None, np.tile(((.2, .3), (.8, .9)), (len(uv), 1, 1)).astype(np.float32)):
                with patch('open_painter.native_bridge.lifecycle.cpu_library', return_value=None):
                    expected = uv_rasterizer.rasterize(uv, 64, unique, clip)
                actual = rasterize(self.lib, uv, 64, unique, clip)
                for result in (actual, expected):
                    order = np.lexsort((result[2], result[1], result[0]))
                    if result is actual:
                        a = tuple(v[order] for v in result)
                    else:
                        b = tuple(v[order] for v in result)
                for lhs, rhs in zip(a[:3], b[:3]):
                    np.testing.assert_array_equal(lhs, rhs)
                np.testing.assert_allclose(a[3], b[3], rtol=0, atol=2e-7)
