"""Copy layer/folder hierarchy to C; decode its compositing plan for host bindings."""
from array import array
import ctypes as C
from .core import NativeError
from ..constants import CHANNELS, DEFAULTS

DEFAULT, TRANSPARENT, NONE = 0xffffffff, 0xfffffffe, 0xfffffffd


class Node(C.Structure):
    _fields_ = [('parent', C.c_uint32), ('channel', C.c_uint32), ('flags', C.c_uint32)]


class Position(C.Structure):
    _fields_ = [('backdrop', C.c_uint32), ('grouped', C.c_uint32)]


def positions(lib, graph):
    keys, flat = [], array('I')   # flat: the Node fields (parent, channel, flags) of each step
    for channel, steps in graph.chains.items():
        code = CHANNELS.index(channel)
        pending = [(step, DEFAULT) for step in reversed(steps)]
        while pending:
            step, parent = pending.pop()
            index = len(keys)
            keys.append(step['key'])
            children = step.get('children')
            if children is None:
                flat.extend((parent, code, 0))
            else:
                flat.extend((parent, code, 3 if step['passthrough'] else 1))
                pending.extend((child, index) for child in reversed(children))
    count = len(keys)
    lib.op_stack_plan.argtypes = [C.POINTER(Node), C.c_uint32, C.POINTER(Position), C.POINTER(C.c_uint32),
                                  C.POINTER(C.c_uint32), C.POINTER(C.c_uint32)]
    lib.op_stack_plan.restype = C.c_int
    data = (Node * count).from_buffer(flat) if count else (Node * 0)()
    plan = (Position * count)()
    order, offsets, finals = (C.c_uint32 * count)(), (C.c_uint32 * 9)(), (C.c_uint32 * 8)()
    status = lib.op_stack_plan(data, count, plan, order, offsets, finals)
    if status:
        raise NativeError(status, 'Native layer/folder plan is invalid')
    defaults = {channel: ('const', (*DEFAULTS[channel][:3], 1.)) for channel in CHANNELS}
    transparent = 'const', (0., 0., 0., 0.)
    units = [('unit', key) for key in keys]

    def ref(index, channel):
        if index < count:
            return units[index]
        if index == DEFAULT:
            return defaults[channel]
        if index == TRANSPARENT:
            return transparent
        if index == NONE:
            return None
        return 'unit', keys[index]   # out of range: raises as indexing did

    fields = memoryview(plan).cast('B').cast('I') if count else ()   # (backdrop, grouped) per step
    positions = {key: (ref(fields[2 * i], key[2]), ref(fields[2 * i + 1], key[2])) for i, key in enumerate(keys)}
    order = memoryview(order).cast('B').cast('I') if count else ()
    return (positions, {c: ref(finals[i], c) for i, c in enumerate(CHANNELS)},
            {c: [keys[order[j]] for j in range(offsets[i], offsets[i + 1])] for i, c in enumerate(CHANNELS)})
