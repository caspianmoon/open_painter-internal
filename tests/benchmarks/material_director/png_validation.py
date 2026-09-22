"""Strict PNG validation, with optional system libpng acceleration.

The native adapter follows the installed official png.h simplified-read ABI
(PNG_IMAGE_VERSION=1, libpng 1.6). CRCs, stream length, resource bounds, filter
bytes and palette indices are still checked here. No package is installed.
"""
from collections import OrderedDict
import ctypes
import ctypes.util
from functools import lru_cache
from pathlib import Path
import struct
import zlib
import hashlib

MAX_DECODE_BYTES = 256 * 1024 * 1024
_DECODED = OrderedDict()


class _PNGImage(ctypes.Structure):
    _fields_ = [('opaque',ctypes.c_void_p),('version',ctypes.c_uint32),
        ('width',ctypes.c_uint32),('height',ctypes.c_uint32),('format',ctypes.c_uint32),
        ('flags',ctypes.c_uint32),('colormap_entries',ctypes.c_uint32),
        ('warning_or_error',ctypes.c_uint32),('message',ctypes.c_char*64)]


@lru_cache(maxsize=1)
def _load_native():
    """Use an identifiable installed library; otherwise retain Python decoding."""
    for name in ('png16','png'):
        candidate = ctypes.util.find_library(name)
        if not candidate:
            continue
        path = Path(candidate)
        # The external dependency must have actual bytes we can pin. A bare
        # loader alias cannot establish that provenance, so use the fallback.
        if not path.is_absolute() or not path.is_file():
            continue
        path = path.resolve()
        try:
            library = ctypes.CDLL(str(path))
            library.png_access_version_number.argtypes=[]
            library.png_access_version_number.restype=ctypes.c_uint32
            if not 10600 <= library.png_access_version_number() < 20000:
                continue
            library.png_get_libpng_ver.argtypes=[ctypes.c_void_p]
            library.png_get_libpng_ver.restype=ctypes.c_char_p
            library.png_image_begin_read_from_memory.argtypes=[ctypes.POINTER(_PNGImage),ctypes.c_void_p,ctypes.c_size_t]
            library.png_image_begin_read_from_memory.restype=ctypes.c_int
            library.png_image_finish_read.argtypes=[ctypes.POINTER(_PNGImage),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_int32,ctypes.c_void_p]
            library.png_image_finish_read.restype=ctypes.c_int
            library.png_image_free.argtypes=[ctypes.POINTER(_PNGImage)]
            library.png_image_free.restype=None
            with path.open('rb') as stream:
                checksum=hashlib.file_digest(stream,'sha256').hexdigest()
            return library,dict(backend='LIBPNG',version=library.png_get_libpng_ver(None).decode('ascii'),
                library_path=str(path),library_sha256=checksum)
        except (OSError,AttributeError,ValueError):
            continue
    return None


def decoder_backend():
    native=_load_native()
    return dict(native[1]) if native else dict(backend='PYTHON',version='strict-png-v2',
        library_path=None,library_sha256=None)


def _decode_native(data,width,height):
    native=_load_native()
    if native is None:
        raise ValueError('Native PNG decoder unavailable')
    if width*height*4 > MAX_DECODE_BYTES:
        raise ValueError('PNG native output exceeds evidence decode memory budget')
    library,_=native
    image=_PNGImage()
    image.version=1
    try:
        if not library.png_image_begin_read_from_memory(ctypes.byref(image),data,len(data)):
            raise ValueError('Native PNG header decode failed: '+image.message.decode('utf-8','replace'))
        if (image.width,image.height)!=(width,height):
            raise ValueError('Native PNG dimensions differ from validated header')
        image.format=3  # PNG_FORMAT_RGBA, 8-bit output; all source samples decoded.
        pixels=ctypes.create_string_buffer(width*height*4)
        if not library.png_image_finish_read(ctypes.byref(image),None,pixels,0,None):
            raise ValueError('Native PNG pixel decode failed: '+image.message.decode('utf-8','replace'))
        return dict(pixel_sha256=hashlib.sha256(pixels).hexdigest(),decoded_rgba_bytes=len(pixels))
    finally:
        library.png_image_free(ctypes.byref(image))


def decode_png(data, expected_size=None, *, backend='auto'):
    if backend not in {'auto','native','python'}:
        raise ValueError('Unknown PNG decoder backend')
    native = _load_native() if backend != 'python' else None
    if backend == 'native' and native is None:
        raise ValueError('Native PNG decoder unavailable')
    actual_backend=native[1] if native else dict(backend='PYTHON',version='strict-png-v2')
    cache_key = (hashlib.sha256(data).digest(), tuple(expected_size) if expected_size else None,
                 actual_backend['backend'],actual_backend['version'],MAX_DECODE_BYTES)
    if cache_key in _DECODED:
        _DECODED.move_to_end(cache_key)
        return dict(_DECODED[cache_key])
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid PNG signature")
    offset, chunks, header, ended, palette = 8, [], None, False, None
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("Truncated PNG chunk")
        length = struct.unpack(">I", data[offset:offset+4])[0]
        kind = data[offset+4:offset+8]
        end = offset + 8 + length
        if end + 4 > len(data):
            raise ValueError("Truncated PNG payload")
        payload = data[offset+8:end]
        if zlib.crc32(kind + payload) & 0xffffffff != struct.unpack(">I", data[end:end+4])[0]:
            raise ValueError("PNG chunk checksum mismatch")
        if kind == b"IHDR":
            if header is not None or offset != 8 or length != 13:
                raise ValueError("Invalid PNG header")
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            chunks.append(payload)
        elif kind == b"PLTE":
            if palette is not None or not 3 <= length <= 768 or length % 3:
                raise ValueError("Invalid PNG palette")
            palette = length // 3
        elif kind == b"IEND":
            ended = length == 0 and end + 4 == len(data)
        offset = end + 4
    if not header or not ended or not chunks:
        raise ValueError("PNG is incomplete")
    width, height, depth, color, compression, filter_method, interlace = header
    if width < 1 or height < 1 or width * height > 100_000_000:
        raise ValueError("PNG dimensions are invalid or exceed evidence decode budget")
    if expected_size is not None and [width, height] != list(expected_size):
        raise ValueError(f"PNG dimensions {[width, height]} differ from expected {list(expected_size)}")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    valid_depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
    if channels is None or depth not in valid_depths[color] or compression or filter_method or interlace:
        raise ValueError("Unsupported PNG encoding; Blender evidence must be non-interlaced")
    if color == 3 and (palette is None or palette > 2 ** depth):
        raise ValueError("Indexed PNG requires a valid palette")
    stride = (width * channels * depth + 7) // 8
    expected = height * (stride + 1)
    if expected + width*height*4 > MAX_DECODE_BYTES:
        raise ValueError('PNG pixels exceed evidence decode memory budget')
    inflater = zlib.decompressobj()
    raw = inflater.decompress(b"".join(chunks), expected + 1)
    if len(raw) != expected or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        raise ValueError("PNG pixel stream length is invalid")
    if any(raw[y*(stride+1)] > 4 for y in range(height)):
        raise ValueError('Invalid PNG scanline filter')
    native_result = _decode_native(data,width,height) if native else {}
    # Reconstruct all scanlines, not merely their signature/IHDR. Standard PNG
    # filters operate on encoded bytes, including 16-bit and packed samples.
    bpp = max(1, (channels * depth + 7) // 8)
    previous = bytearray(stride)
    for y in range(height) if not native or color == 3 else ():
        start = y * (stride + 1)
        mode, row = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        if mode > 4:
            raise ValueError("Invalid PNG scanline filter")
        if mode:
            for x in range(stride):
                a, b, c = (row[x-bpp] if x >= bpp else 0), previous[x], (previous[x-bpp] if x >= bpp else 0)
                if mode == 1: predictor = a
                elif mode == 2: predictor = b
                elif mode == 3: predictor = (a + b) // 2
                else:
                    p = a + b - c
                    pa, pb, pc = abs(p-a), abs(p-b), abs(p-c)
                    predictor = a if pa <= pb and pa <= pc else b if pb <= pc else c
                row[x] = (row[x] + predictor) & 255
        if color == 3:
            for x in range(width):
                index = (row[x * depth // 8] >> (8-depth-(x*depth % 8))) & ((1 << depth)-1)
                if index >= palette:
                    raise ValueError("PNG pixel indexes outside its palette")
        previous = row
    result = {"size": [width, height], "bit_depth": depth, "color_type": color,
              "decoded_bytes": height * stride, "status": "DECODED",
              "decoder":actual_backend['backend'],"decoder_version":actual_backend['version'],**native_result}
    _DECODED[cache_key] = result
    while len(_DECODED)>256:
        _DECODED.popitem(last=False)
    return dict(result)
