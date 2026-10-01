"""Export pixel kernels: quantization and normal encoding in one native pass each, with the
float32 arithmetic of the NumPy code they replace (see op_pixels.h)."""
import ctypes as C

import numpy as np

from .core import NativeError

F32 = C.POINTER(C.c_float)


def check(status):
    if status:
        raise NativeError(status, 'Native pixel kernel rejected its input')


def quantize(lib, values, bits, srgb):
    """``image_writer.quantize`` of (..., channels) float32 values."""
    values = np.ascontiguousarray(values, np.float32)
    out = np.empty(values.shape, np.uint8 if bits == 8 else np.uint16)
    channels = values.shape[-1]
    lib.op_quantize.argtypes = [F32, C.c_uint64, C.c_uint32, C.c_uint32, C.c_uint32, C.c_void_p]
    lib.op_quantize.restype = C.c_int
    check(lib.op_quantize(values.ctypes.data_as(F32), values.size // channels, channels, bits, int(bool(srgb)),
                          out.ctypes.data))
    return out


def normal_encode(lib, values, decode):
    """Normalize and encode the tangent normals of (..., 3 | 4) float32 values, in place."""
    if values.dtype != np.float32 or not values.flags.c_contiguous or values.shape[-1] not in (3, 4):
        raise ValueError('Expected contiguous float32 normals with 3 or 4 channels')
    lib.op_normal_encode.argtypes = [F32, C.c_uint64, C.c_uint32, C.c_uint32]
    lib.op_normal_encode.restype = C.c_int
    check(lib.op_normal_encode(values.ctypes.data_as(F32), values.size // values.shape[-1], values.shape[-1],
                               int(bool(decode))))
    return values
