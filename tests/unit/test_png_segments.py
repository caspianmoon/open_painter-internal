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
    pixels = unfilter(lines, 4 * bits // 8)
    return (pixels.view('>u2').astype(np.uint16) if bits == 16 else pixels).reshape(height, width, 4), idat


def unfilter(lines, bpp):
    """A PNG decoder's reconstruction of filtered scanlines (ISO 15948, 9.2)."""
    rows = np.zeros((lines.shape[0], lines.shape[1] - 1), np.uint8)
    above = np.zeros(rows.shape[1], np.int64)
    for r, line in enumerate(lines):
        kind, f = int(line[0]), line[1:].astype(np.int64)
        assert kind <= 4, kind
        if kind in (0, 2):
            row = (f + (above if kind == 2 else 0)) & 0xFF
        else:
            row = np.zeros_like(f)
            for i in range(len(f)):
                a = row[i - bpp] if i >= bpp else 0
                b, c = above[i], above[i - bpp] if i >= bpp else 0
                if kind == 1:
                    predicted = a
                elif kind == 3:
                    predicted = (a + b) >> 1
                else:
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    predicted = a if pa <= pb and pa <= pc else b if pb <= pc else c
                row[i] = (f[i] + predicted) & 0xFF
        rows[r] = row
        above = row
    return rows


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
        smooth = (np.linspace(0, 255, 37)[None, :, None] + rng.normal(0, 3, (40, 37, 4))).clip(0, 255)
        cases = [(smooth.astype(np.uint8), 8), ((smooth * 257).astype(np.uint16), 16),
                 (rng.integers(0, 256, (23, 19, 4), dtype=np.uint8), 8), (np.zeros((1, 1, 4), np.uint8), 8)]
        for data, bits in cases:
            for segment in (image_writer.SEGMENT, 700, 1):   # one segment, several, one row each
                saved, image_writer.SEGMENT = image_writer.SEGMENT, segment
                try:
                    pixels, stream, _ = self.write(data, bits)
                    again = self.write(data, bits)[1]
                finally:
                    image_writer.SEGMENT = saved
                np.testing.assert_array_equal(pixels, data)
                self.assertEqual(stream, again)

    def test_modes(self):
        """Smooth rows are filtered, noise stays raw; both decode, also mixed across segments."""
        rng = np.random.default_rng(9)
        smooth = (np.linspace(0, 255, 64)[None, :, None] + rng.normal(0, 2, (30, 64, 4))).clip(0, 255).astype(np.uint8)
        noise = rng.integers(0, 256, (30, 64, 4), dtype=np.uint8)
        mixed = np.concatenate((smooth, noise, smooth))
        saved = image_writer.SEGMENT, image_writer.SAMPLE
        image_writer.SEGMENT, image_writer.SAMPLE = 30 * 257, 1 << 12   # one segment per band
        try:
            with tempfile.TemporaryDirectory() as root:
                path = Path(root) / 'map.png'
                image_writer.write_png(path, mixed, 8, False)
                pixels, stream = decode(path)
            types = np.frombuffer(zlib.decompress(stream), np.uint8).reshape(90, -1)[:, 0]
        finally:
            image_writer.SEGMENT, image_writer.SAMPLE = saved
        np.testing.assert_array_equal(pixels, mixed)
        self.assertTrue(types[:30].any() and not types[30:60].any() and types[60:].any(), types)

    def test_size(self):
        """Filtering makes smooth maps smaller than one unfiltered stream; segments cost little."""
        rng = np.random.default_rng(6)
        smooth = (np.linspace(0, 255, 700)[None, :, None] + rng.normal(0, 3, (500, 700, 4))).clip(0, 255).astype(np.uint8)
        noise = rng.integers(0, 256, (300, 257, 4), dtype=np.uint8)
        for data, limit in ((smooth, .8), (noise, 1.01)):
            stream = self.write(data, 8)[1]
            self.assertLess(len(stream), len(serial(data, 8)) * limit + 64)
            saved, image_writer.SEGMENT = image_writer.SEGMENT, 5000
            try:
                many = self.write(data, 8)[1]   # a row a segment: the dictionary carries it
            finally:
                image_writer.SEGMENT = saved
            self.assertLess(len(many), len(stream) * 1.05 + 64)

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
