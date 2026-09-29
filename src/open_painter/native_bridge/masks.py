"""Copy mask hierarchy descriptors; decode C-owned traversal and fold references."""
import ctypes as C
from dataclasses import dataclass
import struct

from .core import NativeError

NONE, INITIAL, ONE = 0xffffffff, 0xfffffffe, 0xfffffffd
ENABLED, GROUP, FILTER, ANCHOR = 1, 2, 4, 8


class Node(C.Structure):
    _fields_ = [('parent', C.c_uint32), ('flags', C.c_uint32)]


class Step(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in ('index', 'previous', 'value', 'prefix_begin', 'prefix_end')]


class _Plan(C.Structure):
    _fields_ = [('step_count', C.c_uint32), ('walk_count', C.c_uint32), ('result', C.c_uint32),
                ('steps', C.POINTER(Step)), ('walk', C.POINTER(C.c_uint32))]


@dataclass(frozen=True)
class Plan:
    steps: tuple
    walk: tuple
    result: int


def snapshot(masks):
    if len(masks) > 65536:
        raise ValueError('Too many native mask entries')
    # Direct recipe construction can contain anonymous root entries. Empty
    # IDs cannot be parent targets; preserve these entries by array index.
    ids = {mask.id: i for i, mask in enumerate(masks) if mask.id}
    if len(ids) != sum(bool(mask.id) for mask in masks):
        raise ValueError('Nonempty mask IDs must be unique')
    result = (Node * len(masks))()
    for i, mask in enumerate(masks):
        parent = getattr(mask, 'parent_id', '')
        if parent and parent not in ids:
            raise ValueError('Missing mask parent')
        result[i] = Node(ids[parent] if parent else NONE,
                         int(bool(mask.enabled)) | {'GROUP': GROUP, 'FILTER': FILTER,
                                                   'ANCHOR': ANCHOR}.get(mask.source, 0))
    return result


def plan(lib, nodes, start, stop, pre_filter=False, initial=False):
    if not 0 <= start <= stop <= len(nodes) <= 65536:
        raise ValueError('Invalid mask plan range')
    binding = getattr(lib, '_op_binding', None)
    if binding is not None:
        steps, walk, result = binding.mask_plan(bytes(nodes), start, stop, bool(pre_filter), bool(initial))
    else:
        ptr = C.POINTER(_Plan)()
        lib.op_mask_plan_build.argtypes = [C.POINTER(Node), C.c_uint32, C.c_uint32, C.c_uint32,
                                          C.c_uint32, C.c_uint32, C.POINTER(C.POINTER(_Plan))]
        lib.op_mask_plan_build.restype = C.c_int
        lib.op_mask_plan_free.argtypes, lib.op_mask_plan_free.restype = [C.POINTER(_Plan)], None
        status = lib.op_mask_plan_build(nodes, len(nodes), start, stop, bool(pre_filter), bool(initial), C.byref(ptr))
        if status:
            raise NativeError(status, 'Invalid native mask hierarchy')
        try:
            value = ptr.contents
            steps = C.string_at(value.steps, value.step_count * C.sizeof(Step))
            walk = C.string_at(value.walk, value.walk_count * 4)
            result = value.result
        finally:
            lib.op_mask_plan_free(ptr)
    return Plan(tuple(struct.iter_unpack('=5I', steps)), tuple(v[0] for v in struct.iter_unpack('=I', walk)), result)
