"""Copy layer/folder hierarchy to C; decode its compositing plan for host bindings."""
import ctypes as C
from .core import NativeError
from ..constants import CHANNELS, DEFAULTS

DEFAULT, TRANSPARENT, NONE = 0xffffffff, 0xfffffffe, 0xfffffffd


class Node(C.Structure):
    _fields_ = [('parent', C.c_uint32), ('channel', C.c_uint32), ('flags', C.c_uint32)]


class Position(C.Structure):
    _fields_ = [('backdrop', C.c_uint32), ('grouped', C.c_uint32)]


def positions(lib, graph):
    keys, nodes = [], []
    for channel, steps in graph.chains.items():
        pending = [(step, DEFAULT) for step in reversed(steps)]
        while pending:
            step, parent = pending.pop()
            index = len(keys)
            keys.append(step['key'])
            folder = 'children' in step
            nodes.append(Node(parent, CHANNELS.index(channel), int(folder) | (2 if folder and step['passthrough'] else 0)))
            pending.extend((child, index) for child in reversed(step.get('children', ())))
    lib.op_stack_plan.argtypes = [C.POINTER(Node), C.c_uint32, C.POINTER(Position), C.POINTER(C.c_uint32),
                                  C.POINTER(C.c_uint32), C.POINTER(C.c_uint32)]
    lib.op_stack_plan.restype = C.c_int
    data, plan = (Node * len(nodes))(*nodes), (Position * len(nodes))()
    order, offsets, finals = (C.c_uint32 * len(nodes))(), (C.c_uint32 * 9)(), (C.c_uint32 * 8)()
    status = lib.op_stack_plan(data, len(data), plan, order, offsets, finals)
    if status:
        raise NativeError(status, 'Native layer/folder plan is invalid')

    def ref(index, channel):
        if index == DEFAULT:
            return 'const', (*DEFAULTS[channel][:3], 1.)
        if index == TRANSPARENT:
            return 'const', (0., 0., 0., 0.)
        if index == NONE:
            return None
        return 'unit', keys[index]

    positions = {key: (ref(p.backdrop, key[2]), ref(p.grouped, key[2])) for key, p in zip(keys, plan)}
    return (positions, {c: ref(finals[i], c) for i, c in enumerate(CHANNELS)},
            {c: [keys[order[j]] for j in range(offsets[i], offsets[i + 1])] for i, c in enumerate(CHANNELS)})
