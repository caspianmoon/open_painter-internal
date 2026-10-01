"""Dependency-free PNG/TIFF encoding with independent RGBA data channels.

Blender's float save_render pipeline unpremultiplies alpha, which loses RGB
when a packed data channel happens to be zero. Encode integer data directly.
"""

import os
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor

import numpy as np

# Rows are filtered adaptively (as libpng does by default) where a sample says it pays, and the
# image data is deflated in segments of about SEGMENT bytes of scanlines, each on its own thread
# (as pigz does): a segment starts from the previous one's last WINDOW bytes as
# its dictionary and ends with a sync flush, so the segments join into one zlib stream. The
# boundaries depend only on the row size, so a file's bytes never depend on the thread count.
SEGMENT = 1 << 20
SAMPLE = 1 << 16
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
    """Filtered PNG scanlines of rows start..stop: each row's filter type, then its bytes
    (big-endian samples). The native filter and NumPy's choose the same filters."""
    from ..native_bridge.lifecycle import cpu_library
    lib = cpu_library()
    if lib is not None and hasattr(lib, 'op_png_filter'):
        from ..native_bridge.pixels import png_filter
        lines = png_filter(lib, data, bits, start, stop)
        if lines is not None:
            return lines
    return _filter_numpy(data, bits, start, stop)


def _filter_numpy(data, bits, start, stop):
    """Adaptive filtering as libpng does by default: per row, of None, Sub, Up, Average and
    Paeth the one whose bytes have the smallest sum of |signed byte|, the lowest on ties."""
    first = max(start - 1, 0)
    rows = data[first:stop]
    rows = np.ascontiguousarray(rows.astype(">u2") if bits == 16 else rows).view(np.uint8)
    rows = rows.reshape(stop - first, -1).astype(np.int16)
    x = rows[start - first:]
    b = rows[:-1] if start else np.vstack((np.zeros((1, rows.shape[1]), np.int16), rows[:-1]))
    bpp = data.shape[2] * (bits // 8)
    a, c = np.zeros_like(x), np.zeros_like(b)
    a[:, bpp:], c[:, bpp:] = x[:, :-bpp], b[:, :-bpp]
    p = a + b - c
    pa, pb, pc = np.abs(p - a), np.abs(p - b), np.abs(p - c)
    predicted = np.where((pa <= pb) & (pa <= pc), a, np.where(pb <= pc, b, c))
    candidates = np.stack((x, x - a, x - b, x - ((a + b) >> 1), x - predicted)) & 0xFF
    choice = np.minimum(candidates, 256 - candidates).sum(axis=2).argmin(axis=0)
    lines = np.empty((len(x), x.shape[1] + 1), np.uint8)
    lines[:, 0] = choice
    lines[:, 1:] = candidates[choice, np.arange(len(x))]
    return lines


def _raw(data, bits, start, stop):
    """Unfiltered scanlines (filter type 0) of rows start..stop."""
    rows = data[start:stop]
    rows = np.ascontiguousarray(rows.astype(">u2") if bits == 16 else rows).view(np.uint8)
    lines = np.empty((stop - start, rows[0].size + 1), np.uint8)
    lines[:, 0] = 0
    lines[:, 1:] = rows.reshape(stop - start, -1)
    return lines


def _lines(data, bits, start, stop, filtered):
    return _scanlines(data, bits, start, stop) if filtered else _raw(data, bits, start, stop)


def _filtered(data, bits, start, stop):
    """Whether rows start..stop compress better filtered. Filters lose on noise-like masks,
    where deflate finds repeats in the raw rows, so a sample of rows spread through the
    segment is compressed both ways; filtering must win it by 1%."""
    count = min(stop - start, max(1, SAMPLE // (data[0].size * (bits // 8) + 1)))
    rows = np.unique(np.linspace(start, stop - 1, count).astype(np.int64))
    filtered = b"".join(_scanlines(data, bits, int(r), int(r) + 1).tobytes() for r in rows)
    raw = b"".join(_raw(data, bits, int(r), int(r) + 1).tobytes() for r in rows)
    return len(zlib.compress(filtered, LEVEL)) < .99 * len(zlib.compress(raw, LEVEL))


def _deflate(data, bits, segments, index):
    """Segment index of segments ((start, stop, filtered) each): raw deflate of its rows with
    its Adler-32 and length. The dictionary is the stream before it, each earlier segment's
    rows as that segment wrote them."""
    start, stop, filtered = segments[index]
    lines = _lines(data, bits, start, stop, filtered)
    if start:
        first = max(0, start - -(-WINDOW // lines.shape[1]))
        before = b"".join(_lines(data, bits, max(first, a), b, mode).tobytes()
                          for a, b, mode in segments[:index] if b > first)
        compressor = zlib.compressobj(LEVEL, zlib.DEFLATED, -15, zdict=before[-WINDOW:])
    else:
        compressor = zlib.compressobj(LEVEL, zlib.DEFLATED, -15)
    last = index == len(segments) - 1
    body = compressor.compress(lines) + compressor.flush(zlib.Z_FINISH if last else zlib.Z_SYNC_FLUSH)
    return body, zlib.adler32(lines), lines.size


def quantize(values: np.ndarray, bits: int, srgb: bool = False) -> np.ndarray:
    from ..native_bridge.lifecycle import cpu_library
    lib = cpu_library()
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
        pool = _segments()
        modes = [pool.submit(_filtered, data, bits, start, stop) for start, stop in bounds]
        segments = [(start, stop, mode.result()) for (start, stop), mode in zip(bounds, modes)]
        futures = [pool.submit(_deflate, data, bits, segments, index) for index in range(len(segments))]
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
