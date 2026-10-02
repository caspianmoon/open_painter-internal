"""Native export pixel kernels against the NumPy code they replace: identical bits."""
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.native_bridge import load_library
from open_painter.native_bridge import pixels


def edge_values(rng, size):
    """Uniform values around [0, 1] with the values the arithmetic treats specially mixed in."""
    values = rng.uniform(-.2, 1.2, size).astype(np.float32)
    t = np.float32(0.0031308)
    special = np.array([0, -0.0, 1, -1, 2, np.nan, np.inf, -np.inf, t, np.nextafter(t, 0), np.nextafter(t, 1),
                        1e-9, 1e-45, 0.5 / 255, np.nextafter(np.float32(0.5 / 255), 0), 254.5 / 255,
                        0.5 / 65535, 32767.5 / 65535, np.nextafter(np.float32(1), 0)], np.float32)
    flat = values.reshape(-1)
    k = min(len(special), (flat.size + 6) // 7)
    flat[:7 * k:7] = special[:k]
    return values


@unittest.skipUnless(os.environ.get('OP_TEST_NATIVE_LIBRARY'), 'native build not supplied')
class NativePixelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])

    def test_quantize_is_numpys(self):
        from open_painter.export.image_writer import _quantize
        rng = np.random.default_rng(5)
        for shape in ((64, 97, 4), (33, 1, 4), (17, 19, 3), (8, 8, 2), (5, 7, 1), (512, 512, 4)):
            values = edge_values(rng, shape)
            for bits in (8, 16):
                for srgb in (False, True):
                    with np.errstate(invalid='ignore'):
                        expected = _quantize(values, bits, srgb)
                    found = pixels.quantize(self.lib, values, bits, srgb)
                    self.assertEqual(found.dtype, expected.dtype)
                    np.testing.assert_array_equal(found, expected, err_msg=f'{shape} {bits} {srgb}')

    def test_quantize_takes_views(self):
        from open_painter.export.image_writer import _quantize
        base = np.random.default_rng(1).random((40, 40, 4), dtype=np.float32)
        for values in (base[::-1], base[::2], base[:, ::-1], base[::-1, :, :3]):   # export flips rows
            np.testing.assert_array_equal(pixels.quantize(self.lib, values, 8, True), _quantize(values, 8, True))
            np.testing.assert_array_equal(pixels.quantize(self.lib, values, 16, False), _quantize(values, 16, False))

    def test_normal_encode_is_numpys(self):
        from open_painter.export.padding import _encode_numpy
        from open_painter.export import surface_normal
        rng = np.random.default_rng(9)
        for shape in ((64, 61, 4), (9, 9, 3), (256, 256, 4)):
            values = rng.uniform(-1.5, 1.5, shape).astype(np.float32)
            values[0, 0, :3] = 0                      # no length: (0, 0, 1)
            values[0, 1, :3] = 1e-9
            values[0, 2, 0] = np.nan
            values[1, 0, :3] = (3e-9, -4e-9, 0)       # just under the threshold
            with np.errstate(invalid='ignore'):
                expected = _encode_numpy(values)
            original = values.copy()
            found = pixels.normal_encode(self.lib, values, decode=False)
            np.testing.assert_array_equal(found, expected, err_msg=f'encode {shape}')
            np.testing.assert_array_equal(values, original)   # a new array unless told otherwise
            in_place = values.copy()
            self.assertIs(pixels.normal_encode(self.lib, in_place, decode=False, out=in_place), in_place)
            np.testing.assert_array_equal(in_place, expected, err_msg=f'in place {shape}')
            # resample at the same resolution: decode, then padding._encode.
            stored = (values * .5 + .5).astype(np.float32)
            decoded = stored.copy()
            decoded[..., :3] = decoded[..., :3] * 2 - 1
            with np.errstate(invalid='ignore'):
                expected = _encode_numpy(decoded)
            np.testing.assert_array_equal(pixels.normal_encode(self.lib, stored, decode=True), expected,
                                          err_msg=f'decode {shape}')
            # surface_normal.combined without relief decodes from [0, 1] first (NumPy here: no
            # library is active in a unit test).
            height = np.zeros(shape[:2], np.float32)
            if shape[0] == shape[1]:
                with np.errstate(invalid='ignore'):
                    expected = surface_normal.combined(SimpleNamespace(height_distance=0), stored, height)
                found = pixels.normal_encode(self.lib, stored, decode=True)
                np.testing.assert_array_equal(found, expected, err_msg=f'combined {shape}')

    def test_normal_relief_is_numpys(self):
        from open_painter.export import surface_normal
        rng = np.random.default_rng(12)
        saved = surface_normal._uv_metric
        try:
            for n, channels in ((96, 4), (33, 3), (2, 4), (3, 4)):
                normal = rng.uniform(-.1, 1.1, (n, n, channels)).astype(np.float32)
                normal[0, 0, :3] = .5                                  # decodes to no length
                height = rng.normal(0, 1, (n, n)).astype(np.float32)
                height[n // 2, :] *= np.where(rng.random(n) < .1, 1e6, 1).astype(np.float32)   # cliffs (a NaN would turn relief off)
                metric = rng.normal(0, 3, (n, n, 3)).astype(np.float32)
                charts = rng.integers(0, 4, (n, n), dtype=np.int32)    # 0 is empty
                charts[n // 3:, n // 3:] = 7                           # one large chart
                surface_normal._uv_metric = lambda ts, size: (metric, charts)
                for distance in (.37, 1e-3, 25.0):
                    ts = SimpleNamespace(height_distance=distance, assignments=[1])
                    with np.errstate(invalid='ignore', divide='ignore'):
                        expected = surface_normal.combined(ts, normal, height)   # NumPy: no active library
                    found = pixels.normal_relief(self.lib, normal, height, metric, charts, distance)
                    np.testing.assert_array_equal(found, expected, err_msg=f'{n} {channels} {distance}')
        finally:
            surface_normal._uv_metric = saved

    def test_compact_expansion_is_numpys(self):
        from open_painter.evaluation.engine.compact_readback import _expand
        rng = np.random.default_rng(11)
        for components, gray in ((1, False), (2, False), (1, True)):
            for count in (0, 1, 2, 3, 5, 8191, 8192, 8193, 1 << 20):
                with self.subTest(components=components, gray=gray, count=count):
                    # Any 32-bit pattern: NaN payloads, infinities, subnormals and signed zeros.
                    source = rng.integers(0, 1 << 32, (count, components), dtype=np.uint64).astype(np.uint32)
                    expected = np.full((count, 4), 0xa5a5a5a5, np.uint32)
                    _expand(source.view(np.float32), expected.view(np.float32), components, gray)
                    found = np.full((count, 4), 0x5a5a5a5a, np.uint32)
                    self.assertIs(pixels.expand_compact(self.lib, source.view(np.float32), found.view(np.float32),
                                                        components, gray).base, found)
                    np.testing.assert_array_equal(found, expected)
        with self.assertRaises(ValueError):
            pixels.expand_compact(self.lib, np.ones((4, 2), np.float32)[:, :1], np.empty((4, 4), np.float32), 1)

    def test_png_filter_is_numpys(self):
        from open_painter.export.image_writer import _filter_numpy
        rng = np.random.default_rng(8)
        smooth = (np.linspace(0, 255, 300)[None, :, None] + rng.normal(0, 4, (90, 300, 4))).clip(0, 255)
        for data, bits in ((smooth.astype(np.uint8), 8), ((smooth * 257).astype(np.uint16), 16),
                           (rng.integers(0, 256, (40, 33, 4), dtype=np.uint8), 8),
                           (rng.integers(0, 65536, (17, 5, 4), dtype=np.uint16), 16),
                           (rng.integers(0, 256, (12, 9, 3), dtype=np.uint8), 8),
                           (np.full((6, 2, 4), 7, np.uint8), 8)):
            for view in (data, data[::-1]):   # the export writes a row-flipped view
                for start, stop in ((0, len(view)), (1, len(view)), (len(view) // 2, len(view)), (0, 1)):
                    found = pixels.png_filter(self.lib, view, bits, start, stop)
                    np.testing.assert_array_equal(found, _filter_numpy(view, bits, start, stop),
                                                  err_msg=f'{data.shape} {bits} {start}:{stop}')
        self.assertIsNone(pixels.png_filter(self.lib, smooth.astype(np.uint8)[:, ::2], 8, 0, 4))


if __name__ == '__main__':
    unittest.main()
