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
        values = np.random.default_rng(1).random((40, 40, 4), dtype=np.float32)[::-1]   # export flips rows
        np.testing.assert_array_equal(pixels.quantize(self.lib, values, 8, True), _quantize(values, 8, True))

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
            found = pixels.normal_encode(self.lib, values.copy(), decode=False)
            np.testing.assert_array_equal(found, expected, err_msg=f'encode {shape}')
            # surface_normal.combined without relief decodes from [0, 1] first (NumPy here: no
            # library is active in a unit test).
            stored = (values * .5 + .5).astype(np.float32)
            height = np.zeros(shape[:2], np.float32)
            if shape[0] == shape[1]:
                with np.errstate(invalid='ignore'):
                    expected = surface_normal.combined(SimpleNamespace(height_distance=0), stored, height)
                found = pixels.normal_encode(self.lib, stored.copy(), decode=True)
                np.testing.assert_array_equal(found, expected, err_msg=f'combined {shape}')


if __name__ == '__main__':
    unittest.main()
