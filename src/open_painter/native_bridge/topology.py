"""Copy host polygon loops; C owns UV connectivity, borders and seam pairing."""
import ctypes as C
from dataclasses import dataclass
import numpy as np

from .geometry import I64, F32, check


class Face(C.Structure):
    _fields_ = [('start', C.c_uint32), ('count', C.c_uint32), ('flags', C.c_uint32)]


class Border(C.Structure):
    _fields_ = [('island', C.c_int64), ('endpoints', C.c_float * 4)]


class Side(C.Structure):
    _fields_ = [('island', C.c_int64), ('vectors', C.c_float * 12)]


class Topology(C.Structure):
    _fields_ = [('face_count', C.c_uint32), ('island_count', C.c_uint32),
                ('border_count', C.c_uint32), ('side_count', C.c_uint32),
                ('labels', I64), ('islands', I64),
                ('borders', C.POINTER(Border)), ('sides', C.POINTER(Side))]


@dataclass
class Snapshot:
    labels: object
    islands: object
    borders: object
    sides: object
    island_count: int

    def grouped_borders(self):
        order = np.argsort(self.borders['island'], kind='stable')
        data = self.borders[order]
        ids, starts, counts = np.unique(data['island'], return_index=True, return_counts=True)
        return {int(i): data['endpoints'][start:start+count].reshape(-1, 2, 2).copy()
                for i, start, count in zip(ids, starts, counts)}

    def seam_sides(self, island_base=0):
        if not len(self.sides):
            return None
        values = self.sides['vectors'].reshape(-1, 6, 2)
        return (*[values[:, i].copy() for i in range(6)], self.sides['island'] + island_base)


def build(lib, faces, vertices, uv, size=1):
    faces = np.asarray(faces)
    vertices = np.asarray(vertices)
    uv = np.asarray(uv)
    if faces.ndim != 2 or faces.shape[1:] != (3,) or vertices.ndim != 1 or uv.shape != (len(vertices), 2):
        raise ValueError('Invalid polygon loop snapshot dimensions')
    if (faces.dtype.kind not in 'iu' or vertices.dtype.kind not in 'iu'
            or np.any(faces < 0) or np.any(faces > np.iinfo(np.uint32).max)
            or np.any(vertices < 0) or np.any(vertices > np.iinfo(np.int64).max)):
        raise ValueError('Invalid polygon loop snapshot indices')
    faces = np.ascontiguousarray(faces, np.uint32)
    vertices = np.ascontiguousarray(vertices, np.int64)
    uv = np.ascontiguousarray(uv, np.float32)
    binding = getattr(lib, '_op_binding', None)
    if binding is not None:
        labels, islands, borders, sides, count = binding.uv_topology(faces, vertices, uv, size)
        return Snapshot(np.frombuffer(labels, np.int64).copy(), np.frombuffer(islands, np.int64).copy(),
                        np.frombuffer(borders, np.dtype(Border)).copy(), np.frombuffer(sides, np.dtype(Side)).copy(), count)
    ptr = C.POINTER(Topology)()
    lib.op_uv_topology_build.argtypes = [C.POINTER(Face), C.c_uint32, I64, F32, C.c_uint32,
                                       C.c_uint32, C.POINTER(C.POINTER(Topology))]
    lib.op_uv_topology_build.restype = C.c_int
    lib.op_uv_topology_free.argtypes, lib.op_uv_topology_free.restype = [C.POINTER(Topology)], None
    check(lib.op_uv_topology_build(faces.ctypes.data_as(C.POINTER(Face)), len(faces),
          vertices.ctypes.data_as(I64), uv.ctypes.data_as(F32), len(vertices), size, C.byref(ptr)))
    try:
        t = ptr.contents
        return Snapshot(np.ctypeslib.as_array(t.labels, shape=(t.face_count,)).copy(),
                        np.ctypeslib.as_array(t.islands, shape=(t.face_count,)).copy(),
                        np.ctypeslib.as_array(t.borders, shape=(t.border_count,)).copy(),
                        np.ctypeslib.as_array(t.sides, shape=(t.side_count,)).copy(), t.island_count)
    finally:
        lib.op_uv_topology_free(ptr)


def from_target(lib, target, size=1, *, seams=False):
    mesh = target['mesh']
    count = len(mesh.polygons)
    starts, totals, material = (np.empty(count, np.int32) for _ in range(3))
    mesh.polygons.foreach_get('loop_start', starts)
    mesh.polygons.foreach_get('loop_total', totals)
    mesh.polygons.foreach_get('material_index', material)
    vertices = np.empty(len(mesh.loops), np.int32)
    mesh.loops.foreach_get('vertex_index', vertices)
    flags = np.isin(material, np.asarray(tuple(target['slots']), np.int32)).astype(np.int32)
    if seams:
        selected = np.asarray(target['tri_polygons'])
        if selected.ndim != 1 or selected.dtype.kind not in 'iu' or np.any(selected < 0) or np.any(selected >= count):
            raise ValueError('Invalid triangle polygon binding')
        flags[selected] |= 2
    return build(lib, np.column_stack((starts, totals, flags)), vertices, target['uv'], size)


def island_bounds(lib, raster, count):
    raster = np.asarray(raster)
    if (raster.ndim != 2 or raster.shape[0] != raster.shape[1] or raster.dtype.kind not in 'iu'
            or np.any(raster < -1) or np.any(raster >= count) or count < 0 or count > 1048576):
        raise ValueError('Invalid compact island raster')
    raster = np.ascontiguousarray(raster, np.int32)
    binding = getattr(lib, '_op_binding', None)
    if binding is not None:
        data = np.frombuffer(binding.uv_bounds(raster, len(raster), count), np.int32).reshape(count, 4)
    else:
        data = np.empty((count, 4), np.int32)
        ptr = C.POINTER(C.c_int32)
        lib.op_uv_island_bounds.argtypes = [ptr, C.c_uint32, C.c_uint32, ptr]
        lib.op_uv_island_bounds.restype = C.c_int
        check(lib.op_uv_island_bounds(raster.ctypes.data_as(ptr), len(raster), count, data.ctypes.data_as(ptr)))
    return {int(i): tuple(int(v) for v in data[i]) for i in np.flatnonzero(data[:, 1] >= 0)}
