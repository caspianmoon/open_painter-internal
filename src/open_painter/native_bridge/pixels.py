"""Pixel kernels: quantization, normal encoding, PNG filtering and compact readback expansion in
one native pass each, with the float32 arithmetic of the NumPy code they replace (see op_pixels.h)."""
import ctypes as C

import numpy as np

from .core import NativeError

F32 = C.POINTER(C.c_float)


def check(status):
    if status:
        raise NativeError(status, 'Native pixel kernel rejected its input')


def quantize(lib, values, bits, srgb):
    """``image_writer.quantize`` of (..., channels) float32 values. Rows flipped by a view
    (the export's ``values[::-1]``) are read in place and come back flipped the same way."""
    flipped = (values.ndim >= 2 and values.dtype == np.float32 and values.strides[0] < 0
               and values[::-1].flags.c_contiguous)
    values = values[::-1] if flipped else np.ascontiguousarray(values, np.float32)
    out = np.empty(values.shape, np.uint8 if bits == 8 else np.uint16)
    channels = values.shape[-1]
    lib.op_quantize.argtypes = [F32, C.c_uint64, C.c_uint32, C.c_uint32, C.c_uint32, C.c_void_p]
    lib.op_quantize.restype = C.c_int
    check(lib.op_quantize(values.ctypes.data_as(F32), values.size // channels, channels, bits, int(bool(srgb)),
                          out.ctypes.data))
    return out[::-1] if flipped else out


def normal_encode(lib, values, decode, out=None):
    """Tangent normals of (..., 3 | 4) float32 values normalized and encoded, decoded from
    [0, 1] first when ``decode``: into ``out`` (which may be ``values``), else a new array."""
    values = np.ascontiguousarray(values, np.float32)
    if values.shape[-1] not in (3, 4):
        raise ValueError('Expected normals with 3 or 4 channels')
    if out is None:
        out = np.empty_like(values)
    elif out.dtype != np.float32 or not out.flags.c_contiguous or out.shape != values.shape:
        raise ValueError('Expected a contiguous float32 output of the normals\' shape')
    lib.op_normal_encode.argtypes = [F32, F32, C.c_uint64, C.c_uint32, C.c_uint32]
    lib.op_normal_encode.restype = C.c_int
    check(lib.op_normal_encode(values.ctypes.data_as(F32), out.ctypes.data_as(F32), values.size // values.shape[-1],
                               values.shape[-1], int(bool(decode))))
    return out


def normal_relief(lib, normal, height, metric, charts, distance):
    """``surface_normal.combined``'s relief path: (n, n, 3 | 4) normals tilted by an (n, n)
    height through the (n, n, 3) UV metric within (n, n) charts, into a new array."""
    normal = np.ascontiguousarray(normal, np.float32)
    height = np.ascontiguousarray(height, np.float32)
    metric = np.ascontiguousarray(metric, np.float32)
    charts = np.ascontiguousarray(charts, np.int32)
    n = height.shape[0]
    if (height.shape != (n, n) or normal.shape[:2] != (n, n) or normal.shape[-1] not in (3, 4)
            or metric.shape != (n, n, 3) or charts.shape != (n, n)):
        raise ValueError('Expected square normal, height, metric and chart maps of one size')
    out = np.empty_like(normal)
    I32 = C.POINTER(C.c_int32)
    lib.op_normal_relief.argtypes = [F32, F32, F32, I32, C.c_uint32, C.c_uint32, C.c_float, F32]
    lib.op_normal_relief.restype = C.c_int
    check(lib.op_normal_relief(normal.ctypes.data_as(F32), height.ctypes.data_as(F32), metric.ctypes.data_as(F32),
                               charts.ctypes.data_as(I32), n, normal.shape[-1], float(distance),
                               out.ctypes.data_as(F32)))
    return out


def expand_compact(lib, source, target, components, gray=False):
    """``compact_readback.expand_compact`` of C-contiguous float32 (pixels, components) ``source``
    into C-contiguous float32 (pixels, 4) ``target``; the caller has checked their sizes."""
    if not (source.flags.c_contiguous and target.flags.c_contiguous and source.dtype == target.dtype == np.float32):
        raise ValueError('Expected contiguous float32 compact and RGBA pixels')
    lib.op_expand_compact.argtypes = [F32, C.c_uint64, C.c_uint32, C.c_uint32, F32]
    lib.op_expand_compact.restype = C.c_int
    check(lib.op_expand_compact(source.ctypes.data_as(F32), len(target), components, int(bool(gray)),
                                target.ctypes.data_as(F32)))
    return target


def png_filter(lib, data, bits, start, stop):
    """``image_writer._filter_numpy`` of rows start..stop of (rows, columns, channels) uint8
    or native uint16 samples whose rows are each contiguous (any row stride), else None."""
    dtype = np.dtype(np.uint8 if bits == 8 else np.uint16)
    if (data.ndim != 3 or data.dtype != dtype or not data.dtype.isnative or stop <= start
            or data.strides[2] != dtype.itemsize or data.strides[1] != data.shape[2] * dtype.itemsize):
        return None
    row_bytes = data.shape[1] * data.shape[2] * dtype.itemsize
    out = np.empty((stop - start, row_bytes + 1), np.uint8)
    lib.op_png_filter.argtypes = [C.c_void_p, C.c_int64, C.c_uint32, C.c_uint32, C.c_uint32, C.c_uint32,
                                  C.c_uint32, C.c_void_p]
    lib.op_png_filter.restype = C.c_int
    check(lib.op_png_filter(data[start].ctypes.data, data.strides[0], stop - start, row_bytes,
                            data.shape[2] * dtype.itemsize, int(bits == 16), int(start > 0), out.ctypes.data))
    return out
