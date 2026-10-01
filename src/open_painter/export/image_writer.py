"""Dependency-free PNG/TIFF encoding with independent RGBA data channels.

Blender's float save_render pipeline unpremultiplies alpha, which loses RGB
when a packed data channel happens to be zero. Encode integer data directly.
"""

import os
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor

import numpy as np

# The image data is deflated in segments of about SEGMENT bytes of scanlines, each on its
# own thread (as pigz does): a segment starts from the previous one's last WINDOW bytes as
# its dictionary and ends with a sync flush, so the segments join into one zlib stream. The
# boundaries depend only on the row size, so a file's bytes never depend on the thread count.
SEGMENT = 1 << 20
WINDOW = 1 << 15
LEVEL = 6
_ZLIB_HEADER = b"\x78\x9c"   # deflate, 32K window, default level
_pool = None


def _segments():
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=max(1, min(8, (os.cpu_count() or 2) - 1)),
                                   thread_name_prefix="open-painter-deflate")
    return _pool


def adler32_combine(first, second, length):
    """zlib's adler32_combine: the Adler-32 of A + B from those of A and B (B's byte length)."""
    base = 65521
    remainder = length % base
    low = first & 0xFFFF
    high = (remainder * low) % base
    low += (second & 0xFFFF) + base - 1
    high += (first >> 16) + (second >> 16) + base - remainder
    low = low - base if low >= base else low
    low = low - base if low >= base else low
    high = high - 2 * base if high >= 2 * base else high
    high = high - base if high >= base else high
    return low | (high << 16)


def _scanlines(data, bits, start, stop):
    """PNG scanlines (filter byte 0, then the row big-endian) of rows start..stop."""
    rows = data[start:stop]
    rows = np.ascontiguousarray(rows.astype(">u2") if bits == 16 else rows).view(np.uint8)
    lines = np.empty((stop - start, rows[0].size + 1), np.uint8)
    lines[:, 0] = 0
    lines[:, 1:] = rows.reshape(stop - start, -1)
    return lines


def _deflate(data, bits, start, stop, last):
    """One segment: raw deflate of rows start..stop, with its Adler-32 and length."""
    lines = _scanlines(data, bits, start, stop)
    if start:
        before = _scanlines(data, bits, max(0, start - -(-WINDOW // lines.shape[1])), start)
        compressor = zlib.compressobj(LEVEL, zlib.DEFLATED, -15, zdict=before.reshape(-1)[-WINDOW:].tobytes())
    else:
        compressor = zlib.compressobj(LEVEL, zlib.DEFLATED, -15)
    body = compressor.compress(lines) + compressor.flush(zlib.Z_FINISH if last else zlib.Z_SYNC_FLUSH)
    return body, zlib.adler32(lines), lines.size


def quantize(values: np.ndarray, bits: int, srgb: bool = False) -> np.ndarray:
    from ..native_bridge.lifecycle import active_library
    lib = active_library()
    if lib is not None and values.ndim == 3 and values.shape[-1] <= 4 and hasattr(lib, 'op_quantize'):
        from ..native_bridge.pixels import quantize as native
        return native(lib, values, bits, srgb)   # one pass, the arithmetic below
    return _quantize(values, bits, srgb)


def _quantize(values: np.ndarray, bits: int, srgb: bool = False) -> np.ndarray:
    values = np.clip(values, 0, 1)
    if srgb:
        values = values.copy()
        rgb = values[..., :3]
        values[..., :3] = np.where(
            rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1 / 2.4) - 0.055
        )
    maximum = (1 << bits) - 1
    return np.rint(values * maximum).astype(np.uint8 if bits == 8 else np.uint16)


def write(path, values: np.ndarray, format: str = "PNG", bits: int = 8, srgb: bool = False) -> None:
    data = quantize(values, bits, srgb)
    if format == "PNG":
        write_png(path, data, bits, srgb)
    elif format == "TIFF":
        write_tiff(path, data, bits)
    else:
        raise ValueError(f"Unsupported export format: {format}")


def write_png(path, data, bits, srgb):
    height, width = data.shape[:2]
    with open(path, "wb") as file:
        file.write(b"\x89PNG\r\n\x1a\n")

        def chunk(kind, payload):
            file.write(
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload))
            )

        chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, bits, 6, 0, 0, 0))
        if srgb:
            chunk(b"sRGB", b"\x00")
        if not height:
            chunk(b"IDAT", zlib.compress(b"", LEVEL))
        rows = max(1, SEGMENT // (data[0].size * (bits // 8) + 1)) if height else 1
        bounds = [(start, min(start + rows, height)) for start in range(0, height, rows)]
        futures = [_segments().submit(_deflate, data, bits, start, stop, stop == height) for start, stop in bounds]
        adler = 1
        for index, future in enumerate(futures):
            body, checksum, length = future.result()
            adler = adler32_combine(adler, checksum, length)
            chunk(b"IDAT", (_ZLIB_HEADER if index == 0 else b"") + body
                  + (struct.pack(">I", adler) if index == len(futures) - 1 else b""))
        chunk(b"IEND", b"")


def write_tiff(path, data, bits):
    height, width = data.shape[:2]
    # One uncompressed chunky RGBA strip, top-left orientation, unassociated alpha.
    count = 11
    bits_offset = 8 + 2 + count * 12 + 4
    pixel_offset = bits_offset + 8
    byte_count = width * height * 4 * (bits // 8)
    entries = [
        (256, 4, 1, width),
        (257, 4, 1, height),
        (258, 3, 4, bits_offset),
        (259, 3, 1, 1),
        (262, 3, 1, 2),
        (273, 4, 1, pixel_offset),
        (277, 3, 1, 4),
        (278, 4, 1, height),
        (279, 4, 1, byte_count),
        (284, 3, 1, 1),
        (338, 3, 1, 2),
    ]
    with open(path, "wb") as file:
        file.write(b"II" + struct.pack("<HIH", 42, 8, count))
        for tag, kind, n, value in entries:
            file.write(struct.pack("<HHII", tag, kind, n, value))
        file.write(struct.pack("<I", 0))
        file.write(struct.pack("<4H", *([bits] * 4)))
        for row in data:
            file.write(row.astype("<u2").tobytes() if bits == 16 else row.tobytes())
