"""Compact transfers preserve component order and every source float bit."""

from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.evaluation.engine.compact_readback import expand_compact


class CompactTransferTests(unittest.TestCase):
    def test_native_r_and_rg_across_block_boundaries(self):
        for components in (1, 2):
            for count in (0, 1, 8191, 8192, 8193, 17003):
                with self.subTest(components=components, count=count):
                    source = np.arange(count * components, dtype=np.float32).reshape(-1, components)
                    actual = np.full((count, 4), -99., np.float32)
                    expected = np.zeros((count, 4), np.float32)
                    expected[:, 3] = 1.
                    expected[:, :components] = source
                    self.assertIs(expand_compact(source, actual, components), actual)
                    np.testing.assert_array_equal(actual, expected)

    def test_native_components_preserve_special_float_bits(self):
        bits = np.array([0, 0x80000000, 1, 0x007fffff, 0x00800000, 0x3f000001,
                         0x7f800000, 0xff800000, 0x7fc01234, 0x7fa00123], np.uint32)
        for components in (1, 2):
            source = np.tile(bits, 2000).view(np.float32).reshape(-1, components)
            actual = np.empty((len(source), 4), np.float32)
            expand_compact(source, actual, components)
            expected = np.zeros((len(source), 4), np.uint32)
            expected[:, :components] = source.view(np.uint32)
            expected[:, 3] = 0x3f800000
            np.testing.assert_array_equal(actual.view(np.uint32), expected)

    def test_native_rg_preserves_source_alpha_as_green(self):
        source = np.array([[.3, .4], [.5, .6]], np.float32)
        actual = np.empty((2, 4), np.float32)
        expand_compact(source, actual, 2)
        np.testing.assert_array_equal(actual, np.array([[.3, .4, 0., 1.], [.5, .6, 0., 1.]], np.float32))

    def test_gray_repeats_red_in_green_and_blue(self):
        bits = np.array([0, 0x80000000, 1, 0x7f800000, 0x7fc01234, 0x7fa00123, 0x3f000001], np.uint32)
        for count in (0, 1, 7, 8191, 8192, 8193, 17003):
            with self.subTest(count=count):
                source = np.resize(bits, count).view(np.float32).reshape(-1, 1)
                actual = np.full((count, 4), -99., np.float32)
                self.assertIs(expand_compact(source, actual, 1, gray=True), actual)
                expected = np.repeat(source.view(np.uint32), 4, axis=1)
                expected[:, 3] = 0x3f800000
                np.testing.assert_array_equal(actual.view(np.uint32), expected)
        with self.assertRaises(ValueError):
            expand_compact(np.ones(8, np.float32), np.empty(16, np.float32), 2, gray=True)

    def test_invalid_layout_or_alias_is_rejected(self):
        source = np.ones(4, np.float32)
        target = np.empty(16, np.float32)
        for components in (0, 3, 4):
            with self.assertRaises(ValueError):
                expand_compact(source, target, components)
        with self.assertRaises(ValueError):
            expand_compact(source, target[:8], 1)
        with self.assertRaises(ValueError):
            expand_compact(target[:4], target, 1)


if __name__ == '__main__':
    unittest.main()
