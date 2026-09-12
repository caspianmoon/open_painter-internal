"""Dependency-free PNG/TIFF encoding with independent RGBA data channels.

Blender's float save_render pipeline unpremultiplies alpha, which loses RGB
when a packed data channel happens to be zero. Encode integer data directly.
"""

import struct
import zlib

import numpy as np


def quantize(values: np.ndarray, bits: int, srgb: bool = False) -> np.ndarray:
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
        compressor = zlib.compressobj(6)
        for row in data:
            encoded = row.astype(">u2").tobytes() if bits == 16 else row.tobytes()
            payload = compressor.compress(b"\x00" + encoded)
            if payload:
                chunk(b"IDAT", payload)
        chunk(b"IDAT", compressor.flush())
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
