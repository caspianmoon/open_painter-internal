"""Batched brush data and surface snapshots passed to the portable C core."""
import ctypes as C
import numpy as np
from .geometry import F32, F64, U32, check
from .lifecycle import cpu_library

U64, U8 = C.POINTER(C.c_uint64), C.POINTER(C.c_uint8)


class Settings(C.Structure):
    _fields_ = [(k, C.c_float) for k in ('hardness', 'flow', 'angle', 'depth_limit')] + [
        (k, C.c_uint32) for k in ('accumulate', 'rake', 'planar')] + [('plane', C.c_float * 3)]

    def __init__(self, hardness=.8, flow=1, alpha_angle=0, depth_limit=1,
                 accumulate='MAX', alpha_rotation='RAKE', planar=None, **_):
        super().__init__(hardness, flow, alpha_angle, depth_limit, accumulate == 'FLOW',
                         alpha_rotation == 'RAKE', planar is not None, (C.c_float * 3)(*(planar if planar is not None else (0, 0, 0))))


def array(value, shape, dtype=np.float32):
    value = np.ascontiguousarray(value, dtype)
    if value.shape != shape:
        raise ValueError(f'Expected {shape}, received {value.shape}')
    return value


def pointer(value):
    return value.ctypes.data_as({np.dtype('float32'): F32, np.dtype('float64'): F64,
                                np.dtype('uint32'): U32, np.dtype('uint64'): U64,
                                np.dtype('uint8'): U8, np.dtype('bool'): U8}[value.dtype])


def call(lib, name, types, *args):
    fn = getattr(lib, name)
    fn.argtypes, fn.restype = types, C.c_int
    check(fn(*args))


def uniforms(lib, samples, settings):
    samples = array(samples, (len(samples), 13))
    out = np.empty((len(samples), 4, 4), np.float32)
    settings = Settings(**settings)
    binding = getattr(lib, '_op_binding', None)
    if binding is not None:
        return np.frombuffer(binding.brush_uniforms(samples, bytes(settings)), np.float32).reshape(-1, 4, 4)
    call(lib, 'op_brush_uniforms', [F32, C.c_uint32, C.POINTER(Settings), F32],
         pointer(samples), len(samples), C.byref(settings), pointer(out))
    return out


def bounds(lib, samples, margin):
    samples = array(samples, (len(samples), 13))
    low, high = np.empty(3, np.float32), np.empty(3, np.float32)
    call(lib, 'op_brush_bounds', [F32, C.c_uint32, C.c_float, F32, F32],
         pointer(samples), len(samples), margin, pointer(low), pointer(high))
    return low, high


def resample(lib, points, spacing):
    points = array(points, (len(points), 3))
    kept, count = np.empty(len(points), np.uint32), C.c_uint32()
    call(lib, 'op_brush_resample', [F32, C.c_uint32, C.c_float, U32, C.POINTER(C.c_uint32)],
         pointer(points), len(points), spacing, pointer(kept), C.byref(count))
    return kept[:count.value].astype(np.int64)


def coverage(lib, positions, normals, samples, settings, alpha_texture, eligible):
    positions = array(positions, (len(positions), 3))
    normals = array(normals, positions.shape)
    samples = array(samples, (len(samples), 13))
    result, part = np.zeros(len(positions), np.float32), np.empty(len(positions), np.float32)
    settings = Settings(**settings)
    alpha = None if alpha_texture is None else np.ascontiguousarray(alpha_texture, np.float32)
    if alpha is not None and alpha.ndim != 2:
        raise ValueError('Expected a two-dimensional brush alpha')
    # Native evaluates bounded batches; the adapter resolves mesh IDs to eligibility
    # buffers once per batch, never through per-texel callbacks into Python.
    for start in range(0, len(samples), 256):
        chunk = samples[start:start + 256]
        # Bound eligibility storage to 32 MiB even for large export rasters.
        step = max(1, 33554432 // len(chunk))
        for first in range(0, len(positions), step):
            last = min(first + step, len(positions))
            mask = None if eligible is None else array(eligible(np.arange(first, last), start, start + len(chunk)),
                                                       (last - first, len(chunk)), np.uint8)
            call(lib, 'op_brush_coverage', [F32, F32, C.c_uint32, F32, C.c_uint32, C.POINTER(Settings),
                                          F32, C.c_uint32, C.c_uint32, U8, F32],
                 pointer(positions[first:last]), pointer(normals[first:last]), last - first,
                 pointer(chunk), len(chunk), C.byref(settings), pointer(alpha) if alpha is not None else None,
                 alpha.shape[1] if alpha is not None else 0, alpha.shape[0] if alpha is not None else 0,
                 pointer(mask) if mask is not None else None, pointer(part[first:last]))
        if settings.accumulate:
            result = 1 - (1 - result) * (1 - part)
        else:
            np.maximum(result, part, out=result)
    return result


def walk(lib, indptr, indices, normals, low, high, axes, box_low, box_high, visited):
    count, width = visited.shape
    indptr, indices = array(indptr, (count + 1,), np.uint64), array(indices, (len(indices),), np.uint32)
    geometry = [array(v, (count, 3)) for v in (normals, low, high)]
    boxes = [array(v, (width, 3)) for v in (axes, box_low, box_high)]
    visited = array(visited, (count, width), np.uint8)
    call(lib, 'op_surface_walk', [C.c_uint32, C.c_uint32, U64, U32, C.c_uint64] + [F32] * 6 + [U8],
         count, width, pointer(indptr), pointer(indices), len(indices),
         *(pointer(v) for v in (*geometry, *boxes)), pointer(visited))
    return visited.astype(bool)


def distances(lib, points, corners):
    points = array(points, (len(points), 3), np.float64)
    corners = array(corners, (len(points), 3, 3), np.float64)
    binding = getattr(lib, '_op_binding', None)
    if binding is not None:
        return np.frombuffer(binding.distances(points, corners), np.float64)
    out = np.empty(len(points), np.float64)
    call(lib, 'op_triangle_distances', [F64, F64, C.c_uint32, F64], pointer(points), pointer(corners), len(points), pointer(out))
    return out


def uv_window(lib, corners, uv, low, high, margin):
    corners = array(corners, (len(corners), 3, 3), np.float64)
    uv = array(uv, (len(corners), 3, 2), np.float64)
    low, high = array(low, (3,), np.float64), array(high, (3,), np.float64)
    out = np.empty((len(corners), 2, 2), np.float32)
    call(lib, 'op_uv_window', [F64, F64, C.c_uint32, F64, F64, C.c_double, F32],
         pointer(corners), pointer(uv), len(corners), pointer(low), pointer(high), margin, pointer(out))
    return out
