"""Synthetic PNG decoder checks, not provider output or artistic evidence."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/benchmarks"))
from material_director import png_validation as png, runner


SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
ENCODINGS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8),
             4: (8, 16), 6: (8, 16)}
BASE_FIELDS = ("size", "bit_depth", "color_type", "decoded_bytes", "status")


def chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))


def encoded_png(width, height, depth, color, stream, palette=None, *, compressed=False,
                interlace=0):
    header = struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace)
    return (SIGNATURE + chunk(b"IHDR", header)
            + (chunk(b"PLTE", palette) if palette is not None else b"")
            + chunk(b"IDAT", stream if compressed else zlib.compress(stream))
            + chunk(b"IEND", b""))


def paeth(left, above, upper_left):
    prediction = left + above - upper_left
    distances = [abs(prediction - value) for value in (left, above, upper_left)]
    return (left, above, upper_left)[distances.index(min(distances))]


def filtered_stream(rows, bytes_per_pixel, modes=(0, 1, 2, 3, 4)):
    """Encode known rows; each of the five modes gets a nontrivial row."""
    previous = bytes(len(rows[0]))
    output = bytearray()
    for row, mode in zip(rows, modes):
        output.append(mode)
        for offset, value in enumerate(row):
            left = row[offset - bytes_per_pixel] if offset >= bytes_per_pixel else 0
            above = previous[offset]
            upper_left = previous[offset - bytes_per_pixel] if offset >= bytes_per_pixel else 0
            predictor = (0, left, above, (left + above) // 2,
                         paeth(left, above, upper_left))[mode]
            output.append((value - predictor) & 255)
        previous = row
    return bytes(output)


def packed_samples(samples, depth):
    if depth == 8:
        return bytes(samples)
    if depth == 16:
        return b"".join(struct.pack(">H", value) for value in samples)
    output = bytearray((len(samples) * depth + 7) // 8)
    for index, value in enumerate(samples):
        output[index * depth // 8] |= value << (8 - depth - (index * depth % 8))
    return bytes(output)


def representative_png(color, depth):
    """Return encoded bytes and exact 8-bit RGBA expectation where defined."""
    width, height = 7, 5
    palette_count = min(1 << depth, 7) if color == 3 else 0
    palette = (bytes(value for index in range(palette_count)
                     for value in ((index * 37) % 256, (index * 61) % 256,
                                   (index * 89) % 256)) if color == 3 else None)
    rows, expected_rgba = [], bytearray()
    for y in range(height):
        samples = []
        for x in range(width):
            if color == 3:
                values = [(x * 3 + y * 2) % palette_count]
                offset = values[0] * 3
                expected_rgba.extend(palette[offset:offset + 3] + b"\xff")
            else:
                values = [(x * 31 + y * 53 + channel * 67) % (1 << depth)
                          for channel in range(CHANNELS[color])]
                if depth == 8:
                    if color == 0:
                        expected_rgba.extend(bytes(values * 3 + [255]))
                    elif color == 2:
                        expected_rgba.extend(bytes(values + [255]))
                    elif color == 4:
                        expected_rgba.extend(bytes([values[0]] * 3 + [values[1]]))
                    else:
                        expected_rgba.extend(bytes(values))
            samples.extend(values)
        rows.append(packed_samples(samples, depth))
    bpp = max(1, (CHANNELS[color] * depth + 7) // 8)
    data = encoded_png(width, height, depth, color, filtered_stream(rows, bpp), palette)
    return data, bytes(expected_rgba)


class BenchmarkPNGValidation(unittest.TestCase):
    def setUp(self):
        png._DECODED.clear()

    def backends(self):
        return ("python", "auto", "native") if png._load_native() is not None else ("python", "auto")

    def test_every_supported_encoding_reconstructs_all_five_filters(self):
        for backend in self.backends():
            for color, depths in ENCODINGS.items():
                for depth in depths:
                    with self.subTest(backend=backend, color=color, depth=depth):
                        data, _ = representative_png(color, depth)
                        result = png.decode_png(data, [7, 5], backend=backend)
                        self.assertEqual({key: result[key] for key in BASE_FIELDS}, {
                            "size": [7, 5], "bit_depth": depth, "color_type": color,
                            "decoded_bytes": 5 * ((7 * CHANNELS[color] * depth + 7) // 8),
                            "status": "DECODED"})

    def test_native_pixels_match_known_grayscale_rgb_alpha_and_palette_values(self):
        if png._load_native() is None:
            self.skipTest("libpng is unavailable; fallback is checked separately")
        for color, depth in [(0, 8), (2, 8), (4, 8), (6, 8),
                             (3, 1), (3, 2), (3, 4), (3, 8)]:
            with self.subTest(color=color, depth=depth):
                data, expected = representative_png(color, depth)
                result = png._decode_native(data, 7, 5)
                self.assertEqual(result["decoded_rgba_bytes"], len(expected))
                self.assertEqual(result["pixel_sha256"], hashlib.sha256(expected).hexdigest())

    def test_native_dimensions_must_match_the_validated_header(self):
        if png._load_native() is None:
            self.skipTest("libpng is unavailable")
        data, _ = representative_png(2, 8)
        with self.assertRaises(ValueError):
            png._decode_native(data, 8, 5)

    def test_malformed_pngs_are_rejected_by_every_available_backend(self):
        valid = encoded_png(1, 1, 8, 2, b"\0\x10\x20\x30")
        broken_crc = bytearray(valid)
        broken_crc[29] ^= 1
        corruptions = {
            "signature": b"X" + valid[1:],
            "chunk_crc": bytes(broken_crc),
            "truncated_chunk": valid[:-3],
            "missing_iend": valid[:-12],
            "trailing_after_iend": valid + b"trailing",
            "invalid_filter": encoded_png(1, 1, 8, 2, b"\5\x10\x20\x30"),
            "trailing_zlib_stream": encoded_png(1, 1, 8, 2,
                zlib.compress(b"\0\x10\x20\x30") + zlib.compress(b"\0"), compressed=True),
            "trailing_zlib_bytes": encoded_png(1, 1, 8, 2,
                zlib.compress(b"\0\x10\x20\x30") + b"garbage", compressed=True),
            "incomplete_pixels": encoded_png(1, 1, 8, 2, b"\0\x10\x20"),
            "excess_pixels": encoded_png(1, 1, 8, 2, b"\0\x10\x20\x30\x40"),
            "palette_index_out_of_range": encoded_png(1, 1, 2, 3, b"\0\xc0",
                                                       b"\0\0\0\xff\xff\xff"),
            "missing_palette": encoded_png(1, 1, 1, 3, b"\0\0"),
            "palette_exceeds_bit_depth": encoded_png(1, 1, 1, 3, b"\0\0", bytes(9)),
            "invalid_depth": encoded_png(1, 1, 4, 2, b"\0\0\0"),
            "interlaced": encoded_png(1, 1, 8, 2, b"\0\x10\x20\x30", interlace=1),
            "zero_width": encoded_png(0, 1, 8, 2, b"\0"),
            "oversized_dimensions": encoded_png(100_000_001, 1, 8, 2, b"\0"),
        }
        for backend in self.backends():
            for label, data in corruptions.items():
                with self.subTest(backend=backend, corruption=label):
                    with self.assertRaises(ValueError):
                        png.decode_png(data, backend=backend)

    def test_expected_dimensions_are_checked_even_after_a_cached_decode(self):
        data, _ = representative_png(2, 8)
        for backend in self.backends():
            with self.subTest(backend=backend):
                png.decode_png(data, [7, 5], backend=backend)
                with self.assertRaises(ValueError):
                    png.decode_png(data, [5, 7], backend=backend)

    def test_named_decode_memory_budget_applies_to_both_backends(self):
        data = encoded_png(2, 2, 8, 6, (b"\0" + bytes(8)) * 2)
        for backend in self.backends():
            with self.subTest(backend=backend), patch.object(png, "MAX_DECODE_BYTES", 8):
                with self.assertRaises(ValueError):
                    png.decode_png(data, backend=backend)

    def test_auto_fallback_preserves_public_result_and_explicit_native_fails(self):
        data, _ = representative_png(2, 8)
        expected = png.decode_png(data, backend="python")
        with patch.object(png, "_load_native", return_value=None):
            result = png.decode_png(data, backend="auto")
            self.assertEqual({key: result[key] for key in BASE_FIELDS},
                             {key: expected[key] for key in BASE_FIELDS})
            self.assertEqual(png.decoder_backend()["backend"], "PYTHON")
            with self.assertRaisesRegex(ValueError, "Native PNG decoder unavailable"):
                png.decode_png(data, backend="native")

    def test_auto_does_not_hide_native_decode_failure_with_python_fallback(self):
        data, _ = representative_png(6, 8)
        adapter = (None, {"backend": "LIBPNG", "version": "synthetic-failure"})
        with patch.object(png, "_load_native", return_value=adapter), \
                patch.object(png, "_decode_native", side_effect=ValueError("native pixel decoding failed")):
            with self.assertRaisesRegex(ValueError, "native pixel decoding failed"):
                png.decode_png(data, backend="auto")

    def test_decoder_metadata_identifies_the_actual_native_library(self):
        metadata = png.decoder_backend()
        self.assertTrue({"backend", "version", "library_sha256", "library_path"}.issubset(metadata))
        if metadata["backend"] == "LIBPNG":
            path = Path(metadata["library_path"])
            self.assertTrue(path.is_file())
            self.assertEqual(metadata["library_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertTrue(metadata["version"])
        else:
            self.assertEqual(metadata["backend"], "PYTHON")

    def test_launcher_pins_host_decoder_after_a_different_blender_runtime(self):
        """Synthetic process boundary, no Blender/provider/account invocation."""
        host = dict(backend='LIBPNG',version='synthetic-host',library_path='/synthetic/libpng',library_sha256='a'*64)
        observer = dict(backend='PYTHON',version='strict-png-v2',library_path=None,library_sha256=None)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fixture=root/'fixture.blend';fixture.write_bytes(b'synthetic process fixture')
            out=root/'result';observed=[]
            job=runner.make_job(dict(id='T01',create='Synthetic',refinements=[]),mode='H',repeat=1,
                fixture=fixture,output=out,model='synthetic-model')
            def simulated_blender(*args,**kwargs):
                # The actual observer does not claim a host decoder. Its Python
                # decoder may legitimately differ from the launcher's runtime.
                with patch.object(png,'decoder_backend',return_value=observer):
                    observed.append(png.decoder_backend())
                runner.write_json(out/'result.json',dict(status='FAILED',stages=[],checks={},hard_failures=[],
                    failures=[],environment={'blender_version':'synthetic-observer'}))
                self.assertNotIn('png_decoder',json.loads((out/'result.json').read_text(encoding="utf-8"))['environment'])
                return 0
            def inspect_normalization(result,*args):
                self.assertEqual(result['environment']['png_decoder'],host)
            with patch.object(png,'decoder_backend',return_value=host), \
                    patch.object(runner,'runtime_version',return_value='synthetic'), \
                    patch.object(runner,'source_fingerprint',return_value={'commit':'b'*40,'source_sha256':'c'*64}), \
                    patch.object(runner,'run_blender',side_effect=simulated_blender), \
                    patch.object(runner,'library_fingerprint',return_value='d'*64), \
                    patch.object(runner,'_checks',side_effect=inspect_normalization):
                result=runner._execute_authenticated(job,auth=root/'unused-synthetic-auth',blender='not-invoked',codex=__file__)
            self.assertEqual(observed,[observer])
            self.assertNotEqual(host,observer)
            self.assertEqual(result['environment']['png_decoder'],host)
            self.assertEqual(result['status'],'FAILED')


if __name__ == "__main__":
    unittest.main()
