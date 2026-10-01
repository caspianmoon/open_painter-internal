"""libopenpainter's SHA-256 against hashlib: identical digests for every way it is fed."""
import hashlib
import os
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.native_bridge import load_library
from open_painter.native_bridge.digest import Sha256


@unittest.skipUnless(os.environ.get('OP_TEST_NATIVE_LIBRARY'), 'native build not supplied')
class NativeDigestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])

    def test_one_shot_and_chunked(self):
        rng = np.random.default_rng(4)
        data = rng.integers(0, 256, 3 << 20, dtype=np.uint8).tobytes()
        for size in [*range(0, 200), 1 << 16, (1 << 20) + 3, len(data)]:
            self.assertEqual(Sha256(self.lib, data[:size]).hexdigest(), hashlib.sha256(data[:size]).hexdigest(), size)
        mine, theirs, at = Sha256(self.lib), hashlib.sha256(), 0
        while at < len(data):
            step = int(rng.integers(0, 100000))
            mine.update(data[at:at + step])
            theirs.update(data[at:at + step])
            at += step
            self.assertEqual(mine.digest(), theirs.digest())   # digest leaves the state

    def test_buffers_and_copy(self):
        pixels = np.random.default_rng(2).random((64, 64, 4), dtype=np.float32)
        for value in (pixels, memoryview(pixels), bytearray(pixels.tobytes()), pixels.tobytes(),
                      memoryview(pixels.tobytes())[5:1000]):
            self.assertEqual(Sha256(self.lib, value).hexdigest(), hashlib.sha256(value).hexdigest())
        with self.assertRaises(BufferError):
            Sha256(self.lib, memoryview(pixels)[::2])
        first = Sha256(self.lib, b'abc')
        twin = first.copy()
        twin.update(b'def')
        self.assertEqual(first.hexdigest(), hashlib.sha256(b'abc').hexdigest())
        self.assertEqual(twin.hexdigest(), hashlib.sha256(b'abcdef').hexdigest())
        self.assertEqual((first.name, first.digest_size, first.block_size), ('sha256', 32, 64))


if __name__ == '__main__':
    unittest.main()
