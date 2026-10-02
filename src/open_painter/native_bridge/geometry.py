"""Immutable geometry snapshots and UV raster results owned by the C core."""
import ctypes as C
import numpy as np
from .core import NativeError

F32, F64, U32 = C.POINTER(C.c_float), C.POINTER(C.c_double), C.POINTER(C.c_uint32)
I64 = C.POINTER(C.c_int64)


def check(status):
    if status:
        raise NativeError(status, 'Native geometry rejected the snapshot or exhausted its budget')


def adjacency(lib, vertices):
    vertices = np.ascontiguousarray(vertices, np.int64)
    if vertices.ndim != 2 or vertices.shape[1:] != (3,):
        raise ValueError('Expected N x 3 triangle vertex indices')
    lib.op_mesh_adjacency.argtypes = [I64, C.c_uint32, C.POINTER(I64), C.POINTER(I64), C.POINTER(C.c_uint64)]
    lib.op_mesh_adjacency.restype = C.c_int
    lib.op_geometry_free.argtypes, lib.op_geometry_free.restype = [C.c_void_p], None
    offsets, indices, count = I64(), I64(), C.c_uint64()
    check(lib.op_mesh_adjacency(vertices.ctypes.data_as(I64), len(vertices), C.byref(offsets), C.byref(indices), C.byref(count)))
    try:
        return (np.ctypeslib.as_array(offsets, shape=(len(vertices) + 1,)).copy(),
                np.ctypeslib.as_array(indices, shape=(count.value,)).copy() if count.value else np.empty(0, np.int64))
    finally:
        lib.op_geometry_free(offsets)
        lib.op_geometry_free(indices)


def frames(lib, corners, uv):
    corners, uv = np.ascontiguousarray(corners, np.float64), np.ascontiguousarray(uv, np.float64)
    if corners.ndim != 3 or corners.shape[1:] != (3, 3) or uv.shape != (len(corners), 3, 2):
        raise ValueError('Expected matching triangle positions and UVs')
    result = np.empty((len(corners), 2, 3), np.float64)
    lib.op_mesh_frames.argtypes = [F64, F64, C.c_uint32, F64]
    lib.op_mesh_frames.restype = C.c_int
    check(lib.op_mesh_frames(corners.ctypes.data_as(F64), uv.ctypes.data_as(F64), len(corners), result.ctypes.data_as(F64)))
    return result


def interpolate(lib, weights, corners):
    weights, corners = np.ascontiguousarray(weights, np.float32), np.ascontiguousarray(corners, np.float32)
    if corners.ndim != 3 or corners.shape[1] != 3 or weights.shape != corners.shape[:2]:
        raise ValueError('Expected N x 3 weights and N x 3 x C attributes')
    result = np.empty((len(corners), corners.shape[2]), np.float32)
    lib.op_mesh_interpolate.argtypes = [F32, F32, C.c_uint32, C.c_uint32, F32]
    lib.op_mesh_interpolate.restype = C.c_int
    check(lib.op_mesh_interpolate(weights.ctypes.data_as(F32), corners.ctypes.data_as(F32), len(corners), corners.shape[2], result.ctypes.data_as(F32)))
    return result


def padding_extend(lib, values, donors, out):
    """``op_padding_extend``: ``out`` takes ``values`` with every texel that has a donor replaced
    by its donor's components. ``out`` may be ``values`` (in place)."""
    if (values.dtype != np.float32 or out.dtype != np.float32 or donors.dtype != np.int32
            or not (values.flags.c_contiguous and out.flags.c_contiguous and donors.flags.c_contiguous)
            or values.shape != out.shape or values.ndim != 3 or donors.shape != values.shape[:2]):
        raise ValueError('Expected contiguous float32 (n, n, C) values and int32 (n, n) donors')
    lib.op_padding_extend.argtypes = [F32, C.POINTER(C.c_int32), C.c_uint64, C.c_uint32, F32]
    lib.op_padding_extend.restype = C.c_int
    check(lib.op_padding_extend(values.ctypes.data_as(F32), donors.ctypes.data_as(C.POINTER(C.c_int32)),
                                donors.size, values.shape[2], out.ctypes.data_as(F32)))
    return out


def padding_donors(lib, covered, islands, limit):
    covered, islands = np.ascontiguousarray(covered, np.uint8), np.ascontiguousarray(islands, np.int32)
    if covered.ndim != 2 or covered.shape[0] != covered.shape[1] or islands.shape != covered.shape:
        raise ValueError('Expected matching square ownership arrays')
    donors, distance = np.empty(covered.shape, np.int32), np.empty(covered.shape, np.int16)
    u8, i32, i16 = C.POINTER(C.c_uint8), C.POINTER(C.c_int32), C.POINTER(C.c_int16)
    lib.op_padding_donors.argtypes = [u8, i32, C.c_uint32, C.c_uint32, i32, i16]
    lib.op_padding_donors.restype = C.c_int
    check(lib.op_padding_donors(covered.ctypes.data_as(u8), islands.ctypes.data_as(i32), len(covered),
                               limit, donors.ctypes.data_as(i32), distance.ctypes.data_as(i16)))
    return donors, distance


class Bounds:
    def __init__(self, lib, low, high):
        self.lib, self.handle = lib, C.c_void_p()
        low, high = np.ascontiguousarray(low, np.float64), np.ascontiguousarray(high, np.float64)
        if low.ndim != 2 or low.shape[1] != 3 or low.shape != high.shape:
            raise ValueError('Expected matching N x 3 bounds')
        self.count = len(low)
        lib.op_bounds_create.argtypes = [F64, F64, C.c_uint32, C.POINTER(C.c_void_p)]
        lib.op_bounds_create.restype = C.c_int
        lib.op_bounds_destroy.argtypes, lib.op_bounds_destroy.restype = [C.c_void_p], None
        lib.op_bounds_query.argtypes = [C.c_void_p, F64, F64, U32, C.c_uint32, C.POINTER(C.c_uint32)]
        lib.op_bounds_query.restype = C.c_int
        lib.op_bounds_bytes.argtypes, lib.op_bounds_bytes.restype = [C.c_void_p], C.c_uint64
        check(lib.op_bounds_create(low.ctypes.data_as(F64), high.ctypes.data_as(F64), self.count, C.byref(self.handle)))

    @property
    def bytes(self):
        return self.lib.op_bounds_bytes(self.handle)

    def query(self, low, high):
        low, high = np.ascontiguousarray(low, np.float64), np.ascontiguousarray(high, np.float64)
        if low.shape != (3,) or high.shape != (3,):
            raise ValueError('Expected three query coordinates')
        result, written = np.empty(self.count, np.uint32), C.c_uint32()
        check(self.lib.op_bounds_query(self.handle, low.ctypes.data_as(F64), high.ctypes.data_as(F64),
                                      result.ctypes.data_as(U32), len(result), C.byref(written)))
        return result[:written.value].astype(np.intp)

    def close(self):
        if getattr(self, 'handle', None):
            self.lib.op_bounds_destroy(self.handle)
            self.handle = None

    __del__ = close


class Sample(C.Structure):
    _fields_ = [('row', C.c_uint32), ('column', C.c_uint32), ('triangle', C.c_uint32), ('weights', C.c_float * 3)]


def rasterize(lib, uv, n, unique=True, clip=None):
    uv = np.ascontiguousarray(uv, np.float32)
    if uv.ndim != 3 or uv.shape[1:] != (3, 2):
        raise ValueError('Expected N x 3 x 2 triangle UVs')
    if clip is not None:
        clip = np.ascontiguousarray(clip, np.float32)
        if clip.shape != (len(uv), 2, 2):
            raise ValueError('Expected one UV clip box per triangle')
    binding = getattr(lib, '_op_binding', None)
    if binding is not None:
        raw = binding.rasterize(uv, n, bool(unique), clip)
        data = np.frombuffer(raw, dtype=np.dtype([('row', 'u4'), ('column', 'u4'), ('triangle', 'u4'), ('weights', 'f4', (3,))]))
        return (data['row'].astype(np.int64), data['column'].astype(np.int64),
                data['triangle'].astype(np.int64), data['weights'].copy())
    lib.op_uv_rasterize.argtypes = [F32, C.c_uint32, C.c_uint32, C.c_uint32, F32,
                                    C.POINTER(C.POINTER(Sample)), C.POINTER(C.c_uint64)]
    lib.op_uv_rasterize.restype = C.c_int
    lib.op_geometry_free.argtypes, lib.op_geometry_free.restype = [C.c_void_p], None
    pointer, count = C.POINTER(Sample)(), C.c_uint64()
    check(lib.op_uv_rasterize(uv.ctypes.data_as(F32), len(uv), n, bool(unique),
                             clip.ctypes.data_as(F32) if clip is not None else None, C.byref(pointer), C.byref(count)))
    try:
        if not count.value:
            return (np.empty(0, np.int64),) * 3 + (np.empty((0, 3), np.float32),)
        data = np.ctypeslib.as_array(pointer, shape=(count.value,))
        return (data['row'].astype(np.int64), data['column'].astype(np.int64),
                data['triangle'].astype(np.int64), data['weights'].copy())
    finally:
        lib.op_geometry_free(pointer)


def raster_interpolate(lib, uv, n, attributes, covered=None):
    """The unique raster of (T, 3, 2) ``uv`` at n, each (corners, out) pair of ``attributes``
    interpolated straight into ``out``, as rasterize, interpolate and a scatter would give it:
    corners (T, 3, C) float32 (float32 arithmetic) or float64 (float64, rounded once), out a
    C-contiguous float32 (n, n, C) map. ``covered``, an (n, n) bool or uint8 map, is set where a
    triangle covers. Texels no triangle covers keep their values."""
    uv = np.ascontiguousarray(uv, np.float32)
    if uv.ndim != 3 or uv.shape[1:] != (3, 2):
        raise ValueError('Expected N x 3 x 2 triangle UVs')
    count = len(attributes)
    if not 1 <= count <= 8:
        raise ValueError('Expected one to eight attributes')
    held, corner_data = [], (C.c_void_p * count)()
    outs, components, widths = (F32 * count)(), (C.c_uint32 * count)(), (C.c_uint32 * count)()
    for i, (corners, out) in enumerate(attributes):
        if corners.dtype not in (np.float32, np.float64):
            raise ValueError('Expected float32 or float64 corner attributes')
        corners = np.ascontiguousarray(corners)
        if corners.ndim != 3 or corners.shape[:2] != (len(uv), 3) or not 1 <= corners.shape[2] <= 16:
            raise ValueError('Expected triangles x 3 x C corner attributes')
        if out.dtype != np.float32 or not out.flags.c_contiguous or out.shape != (n, n, corners.shape[2]):
            raise ValueError('Expected a contiguous float32 (n, n, C) map per attribute')
        held.append(corners)
        corner_data[i] = corners.ctypes.data
        outs[i] = out.ctypes.data_as(F32)
        components[i], widths[i] = corners.shape[2], corners.itemsize
    if covered is not None and (covered.shape != (n, n) or covered.itemsize != 1 or not covered.flags.c_contiguous):
        raise ValueError('Expected a contiguous (n, n) coverage map of bytes')
    lib.op_uv_raster_interpolate.argtypes = [F32, C.c_uint32, C.c_uint32, C.c_uint32, C.POINTER(C.c_void_p),
                                             U32, U32, C.POINTER(F32), C.c_void_p]
    lib.op_uv_raster_interpolate.restype = C.c_int
    check(lib.op_uv_raster_interpolate(uv.ctypes.data_as(F32), len(uv), n, count, corner_data, components,
                                       widths, outs, covered.ctypes.data if covered is not None else None))


ASSIGN_KINDS = {(np.dtype(np.float32), np.dtype(np.float32)): 0, (np.dtype(np.float64), np.dtype(np.float32)): 1,
                (np.dtype(np.int32), np.dtype(np.int32)): 2, (np.dtype(np.int64), np.dtype(np.int32)): 3}


def raster_assign(lib, uv, n, attributes, covered=None):
    """The unique raster of (T, 3, 2) ``uv`` at n with each (values, out) pair of ``attributes``
    assigned straight into ``out``, as rasterize and a scatter of values[triangle] would give it:
    values (T, C) or (T,) float32/float64 into a float32 map, int32/int64 into an int32 map, cast as
    NumPy's assignment casts; out a C-contiguous (n, n, C) or (n, n) map. ``covered`` (n, n) bytes is
    set where a triangle covers. Texels no triangle covers keep their values."""
    uv = np.ascontiguousarray(uv, np.float32)
    if uv.ndim != 3 or uv.shape[1:] != (3, 2):
        raise ValueError('Expected N x 3 x 2 triangle UVs')
    count = len(attributes)
    if not 1 <= count <= 8:
        raise ValueError('Expected one to eight attributes')
    held, data, outs = [], (C.c_void_p * count)(), (C.c_void_p * count)()
    components, kinds = (C.c_uint32 * count)(), (C.c_uint32 * count)()
    for i, (values, out) in enumerate(attributes):
        values = np.ascontiguousarray(values)
        kind = ASSIGN_KINDS.get((values.dtype, out.dtype))
        width = values.shape[1] if values.ndim == 2 else 1
        if (kind is None or values.ndim not in (1, 2) or len(values) != len(uv) or not 1 <= width <= 16
                or not out.flags.c_contiguous or out.shape != ((n, n, width) if values.ndim == 2 else (n, n))):
            raise ValueError('Expected (T, C) values and a contiguous (n, n, C) map of a matching dtype')
        held.append(values)
        data[i], outs[i] = values.ctypes.data, out.ctypes.data
        components[i], kinds[i] = width, kind
    if covered is not None and (covered.shape != (n, n) or covered.itemsize != 1 or not covered.flags.c_contiguous):
        raise ValueError('Expected a contiguous (n, n) coverage map of bytes')
    lib.op_uv_raster_assign.argtypes = [F32, C.c_uint32, C.c_uint32, C.c_uint32, C.POINTER(C.c_void_p), U32, U32,
                                        C.POINTER(C.c_void_p), C.c_void_p]
    lib.op_uv_raster_assign.restype = C.c_int
    check(lib.op_uv_raster_assign(uv.ctypes.data_as(F32), len(uv), n, count, data, components, kinds, outs,
                                  covered.ctypes.data if covered is not None else None))


def raster_ownership(lib, uv, n, owners, starts, island, covered, conflicts):
    """padding.build_ownership's raster of one target, batch by batch (op_uv_raster_ownership):
    ``owners`` (T,) per triangle, batches starting at the ascending triangle indices ``starts``
    (the first 0). Where triangles cover, the (n, n) int32 ``island`` takes the later writer's
    owner, the bool ``covered`` is set, and the bool ``conflicts`` marks texels where a writer
    differs from its batch's first writer."""
    uv = np.ascontiguousarray(uv, np.float32)
    if uv.ndim != 3 or uv.shape[1:] != (3, 2):
        raise ValueError('Expected N x 3 x 2 triangle UVs')
    owners = np.ascontiguousarray(owners, np.int32)
    starts = np.ascontiguousarray(starts, np.uint32)
    if owners.shape != (len(uv),) or starts.ndim != 1:
        raise ValueError('Expected one owner per triangle and a list of batch starts')
    if (island.dtype != np.int32 or island.shape != (n, n) or not island.flags.c_contiguous
            or any(m.shape != (n, n) or m.itemsize != 1 or not m.flags.c_contiguous for m in (covered, conflicts))):
        raise ValueError('Expected contiguous (n, n) island int32, covered and conflicts byte maps')
    I32 = C.POINTER(C.c_int32)
    lib.op_uv_raster_ownership.argtypes = [F32, C.c_uint32, C.c_uint32, I32, U32, C.c_uint32, I32, C.c_void_p,
                                           C.c_void_p]
    lib.op_uv_raster_ownership.restype = C.c_int
    check(lib.op_uv_raster_ownership(uv.ctypes.data_as(F32), len(uv), n, owners.ctypes.data_as(I32),
                                     starts.ctypes.data_as(U32), len(starts), island.ctypes.data_as(I32),
                                     covered.ctypes.data, conflicts.ctypes.data))


def jacobians(lib, corners, uv, n):
    corners, uv = np.ascontiguousarray(corners, np.float64), np.ascontiguousarray(uv, np.float64)
    if corners.ndim != 3 or corners.shape[1:] != (3, 3) or uv.shape != (len(corners), 3, 2):
        raise ValueError('Expected matching triangle positions and UVs')
    result = np.empty((len(corners), 2, 3), np.float32)
    lib.op_mesh_jacobians.argtypes = [F64, F64, C.c_uint32, C.c_uint32, F32]
    lib.op_mesh_jacobians.restype = C.c_int
    check(lib.op_mesh_jacobians(corners.ctypes.data_as(F64), uv.ctypes.data_as(F64), len(corners), n, result.ctypes.data_as(F32)))
    return result


def directions(lib, triangle, matrices, world):
    triangle, matrices = np.ascontiguousarray(triangle, np.int32), np.ascontiguousarray(matrices, np.float32)
    world = np.ascontiguousarray(world, np.float32)
    if matrices.ndim != 3 or matrices.shape[1:] != (2, 3) or world.shape != (3,):
        raise ValueError('Expected per-triangle 2x3 Jacobians and a world direction')
    result = np.empty((*triangle.shape, 2), np.float32)
    lib.op_mesh_directions.argtypes = [C.POINTER(C.c_int32), C.c_uint32, F32, C.c_uint32, F32, F32]
    lib.op_mesh_directions.restype = C.c_int
    check(lib.op_mesh_directions(triangle.ctypes.data_as(C.POINTER(C.c_int32)), triangle.size,
                                 matrices.ctypes.data_as(F32), len(matrices), world.ctypes.data_as(F32), result.ctypes.data_as(F32)))
    return result
