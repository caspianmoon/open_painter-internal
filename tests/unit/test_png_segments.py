"""PNG data deflated in parallel segments: one valid zlib stream, the same pixels, about the
same size as a single stream, and the same bytes on every write."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.export import image_writer


def decode(path):
    raw = Path(path).read_bytes()
    assert raw[:8] == b'\x89PNG\r\n\x1a\n'
    at, idat, header = 8, b'', None
    while at < len(raw):
        length, kind = struct.unpack('>I4s', raw[at:at + 8])
        payload = raw[at + 8:at + 8 + length]
        assert struct.unpack('>I', raw[at + 8 + length:at + 12 + length])[0] == zlib.crc32(kind + payload)
        if kind == b'IHDR':
            header = struct.unpack('>IIBBBBB', payload)
        elif kind == b'IDAT':
            idat += payload
        at += 12 + length
    width, height, bits = header[:3]
    lines = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(height, -1)   # checks Adler-32
    assert not lines[:, 0].any()
    pixels = lines[:, 1:]
    return (pixels.view('>u2').astype(np.uint16) if bits == 16 else pixels).reshape(height, width, 4), idat


def serial(data, bits):
    compressor = zlib.compressobj(6)
    stream = b''.join(compressor.compress(b'\0' + (row.astype('>u2') if bits == 16 else row).tobytes()) for row in data)
    return stream + compressor.flush()


class PngSegmentsTest(unittest.TestCase):
    def write(self, data, bits):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'map.png'
            image_writer.write_png(path, data, bits, False)
            return decode(path) + (path.stat().st_size,)

    def test_round_trip(self):
        rng = np.random.default_rng(6)
        smooth = (np.linspace(0, 255, 700)[None, :, None] + rng.normal(0, 3, (500, 700, 4))).clip(0, 255)
        cases = [(smooth.astype(np.uint8), 8), ((smooth * 257).astype(np.uint16), 16),
                 (rng.integers(0, 256, (300, 257, 4), dtype=np.uint8), 8), (np.zeros((1, 1, 4), np.uint8), 8)]
        for data, bits in cases:
            for segment in (image_writer.SEGMENT, 5000, 1):   # many segments, partial dictionaries
                saved, image_writer.SEGMENT = image_writer.SEGMENT, segment
                try:
                    pixels, stream, _ = self.write(data, bits)
                    again = self.write(data, bits)[1]
                finally:
                    image_writer.SEGMENT = saved
                np.testing.assert_array_equal(pixels, data)
                self.assertEqual(stream, again)
                if segment == image_writer.SEGMENT and data.size > 1000:
                    self.assertLess(len(stream), len(serial(data, bits)) * 1.01 + 64)
                if segment == 5000 and data is cases[0][0]:   # a row a segment: the dictionary carries it
                    self.assertLess(len(stream), len(serial(data, bits)) * 1.05)

    def test_flipped_rows(self):
        data = np.random.default_rng(1).integers(0, 256, (90, 60, 4), dtype=np.uint8)
        np.testing.assert_array_equal(self.write(data[::-1], 8)[0], data[::-1])

    def test_adler32_combine(self):
        rng = np.random.default_rng(3)
        data = rng.integers(0, 256, 300000, dtype=np.uint8).tobytes()
        for cut in (0, 1, 5552, 65521, 65522, 150000, len(data)):
            a, b = data[:cut], data[cut:]
            self.assertEqual(image_writer.adler32_combine(zlib.adler32(a), zlib.adler32(b), len(b)), zlib.adler32(data))


if __name__ == '__main__':
    unittest.main()
