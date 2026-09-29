"""Owned C surface snapshots and batched anchor/target resolution."""
import ctypes as C
import numpy as np
from .geometry import F32, F64, U32, I64, check
from .paint import array, pointer, U8


class Pair(C.Structure):
    _fields_ = [('triangle', C.c_uint32), ('sample', C.c_uint32)]


class Reach:
    def __init__(self, lib, target):
        self.lib, self.handle = lib, C.c_void_p()
        vertices = np.asarray(target['all_tri_vertices'], np.int64)
        positions = np.asarray(target['positions'], np.float64)
        if vertices.ndim != 2 or vertices.shape[1:] != (3,) or positions.ndim != 2 or positions.shape[1:] != (3,):
            raise ValueError('Expected triangle indices and three-dimensional mesh positions')
        if np.any(vertices < 0) or np.any(vertices >= len(positions)):
            raise ValueError('Triangle vertex is outside the mesh snapshot')
        corners = np.ascontiguousarray(positions[vertices], np.float64)
        polygons = array(target['all_tri_polygons'], (len(vertices),), np.int64).copy()
        self.count = len(vertices)
        ptr, u32, u64 = C.c_void_p, C.c_uint32, C.c_uint64
        for name, args, result in (
            ('create', [F64, I64, u32, u32, C.POINTER(ptr)], C.c_int),
            ('destroy', [ptr], None), ('bytes', [ptr], u64),
            ('copy', [ptr, F32, F32, F32, F32, I64, I64], C.c_int),
            ('query', [ptr, F64, F64, U32, u32, C.POINTER(u32)], C.c_int),
            ('validate', [ptr, F32, F32, u32, C.POINTER(C.POINTER(Pair)), C.POINTER(u64), U8], C.c_int),
            ('nearest', [ptr, F32, F32, F32, U8, u32, I64, F64], C.c_int),
            ('seeds', [ptr, F32, F32, F32, F32, U8, u32, C.POINTER(C.POINTER(Pair)), C.POINTER(u64), U8], C.c_int),
        ):
            fn = getattr(lib, 'op_reach_' + name)
            fn.argtypes, fn.restype = args, result
        lib.op_geometry_free.argtypes, lib.op_geometry_free.restype = [ptr], None
        check(lib.op_reach_create(pointer(corners), polygons.ctypes.data_as(I64), self.count,
                                  bool(target.get('mirrored')), C.byref(self.handle)))

    @property
    def extra_bytes(self):
        return self.lib.op_reach_bytes(self.handle)

    def close(self):
        if self.handle:
            self.lib.op_reach_destroy(self.handle)
            self.handle = None

    __del__ = close

    def snapshot(self):
        corners = np.empty((self.count, 3, 3), np.float32)
        normals, low, high = (np.empty((self.count, 3), np.float32) for _ in range(3))
        order, polygons = (np.empty(self.count, np.int64) for _ in range(2))
        check(self.lib.op_reach_copy(self.handle, *(pointer(a) for a in (corners, normals, low, high)),
                                      order.ctypes.data_as(I64), polygons.ctypes.data_as(I64)))
        return dict(corners=corners, normals=normals, lo=low, hi=high, order=order,
                    sorted_polygons=polygons, count=self.count, bounds_index=self, native=self)

    def query(self, low, high):
        low, high = array(low, (3,), np.float64), array(high, (3,), np.float64)
        indices, count = np.empty(self.count, np.uint32), C.c_uint32()
        check(self.lib.op_reach_query(self.handle, pointer(low), pointer(high), pointer(indices), len(indices), C.byref(count)))
        return indices[:count.value].astype(np.int64)

    def pairs(self, anchors, radii, axes=None, band=None, fallback=None):
        anchors, radii = array(anchors, (len(anchors), 4)), array(radii, (len(anchors),))
        result, count = C.POINTER(Pair)(), C.c_uint64()
        resolved = np.zeros(len(anchors), np.uint8)
        tail = (len(anchors), C.byref(result), C.byref(count), pointer(resolved))
        if axes is None:
            status = self.lib.op_reach_validate(self.handle, pointer(anchors), pointer(radii), *tail)
        else:
            axes, band = array(axes, (len(anchors), 3)), array(band, (len(anchors),))
            fallback = np.ones(len(anchors), np.uint8) if fallback is None else array(fallback, (len(anchors),), np.uint8)
            status = self.lib.op_reach_seeds(self.handle, pointer(anchors), pointer(axes), pointer(radii),
                                            pointer(band), pointer(fallback), *tail)
        check(status)
        try:
            if count.value:
                data = np.ctypeslib.as_array(result, shape=(count.value,))
                return data['triangle'].astype(np.int64), data['sample'].astype(np.int64), resolved.astype(bool)
            return np.empty(0, np.int64), np.empty(0, np.int64), resolved.astype(bool)
        finally:
            self.lib.op_geometry_free(result)

    def nearest(self, anchors, axes, band, wanted):
        count = len(anchors)
        anchors, axes, band = array(anchors, (count, 4)), array(axes, (count, 3)), array(band, (count,))
        wanted = array(wanted, (count,), np.uint8)
        triangles, distances = np.empty(count, np.int64), np.empty(count, np.float64)
        check(self.lib.op_reach_nearest(self.handle, pointer(anchors), pointer(axes), pointer(band),
                                        pointer(wanted), count, triangles.ctypes.data_as(I64), pointer(distances)))
        return triangles, distances


def choose(lib, resolved, distances):
    resolved = np.ascontiguousarray(resolved, np.uint8)
    if resolved.ndim != 2:
        raise ValueError('Expected target-major sample flags')
    distances = array(distances, resolved.shape, np.float64)
    result = np.empty_like(resolved)
    lib.op_reach_choose.argtypes = [U8, F64, C.c_uint32, C.c_uint32, U8]
    lib.op_reach_choose.restype = C.c_int
    check(lib.op_reach_choose(pointer(resolved), pointer(distances), *resolved.shape, pointer(result)))
    return result.astype(bool)
