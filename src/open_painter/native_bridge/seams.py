"""Snapshot seam sides; native code chooses the closest continuation per texel."""
import ctypes as C
import numpy as np
from .geometry import F32, I64, check
from .paint import U8


class Sample(C.Structure):
    _fields_ = [('row', C.c_uint32), ('column', C.c_uint32), ('island', C.c_int64),
                ('x', C.c_float), ('y', C.c_float), ('depth', C.c_float), ('normal', C.c_float * 2), ('nearest', C.c_uint32)]


def bands(lib, covered, n, halo, *sides, with_nearest=False):
    covered = np.ascontiguousarray(covered, np.uint8)
    vectors = [np.asarray(value, np.float32) for value in sides[:6]]
    islands = np.ascontiguousarray(sides[6], np.int64)
    if covered.shape != (n, n) or islands.ndim != 1 or any(value.shape != (len(islands), 2) for value in vectors):
        raise ValueError('Invalid seam geometry snapshot dimensions')
    packed = np.ascontiguousarray(np.stack(vectors, axis=1), np.float32)
    lib.op_seam_bands.argtypes = [U8, C.c_uint32, C.c_float, F32, I64, C.c_uint32,
                                 C.POINTER(C.POINTER(Sample)), C.POINTER(C.c_uint64)]
    lib.op_seam_bands.restype = C.c_int
    lib.op_geometry_free.argtypes, lib.op_geometry_free.restype = [C.c_void_p], None
    result, count = C.POINTER(Sample)(), C.c_uint64()
    check(lib.op_seam_bands(covered.ctypes.data_as(U8), n, halo, packed.ctypes.data_as(F32),
                            islands.ctypes.data_as(I64), len(islands), C.byref(result), C.byref(count)))
    try:
        if not count.value:
            return None
        data = np.ctypeslib.as_array(result, shape=(count.value,))
        values = (data['row'].astype(np.int64), data['column'].astype(np.int64), data['x'].copy(),
                data['y'].copy(), data['depth'].copy(), data['normal'].copy(), data['island'].copy())
        return (*values, data['nearest'].astype(bool)) if with_nearest else values
    finally:
        lib.op_geometry_free(result)
