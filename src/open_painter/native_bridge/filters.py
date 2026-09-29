"""Copied spatial-filter parameters to a native bounded pass plan."""
import ctypes as C
import numpy as np
from .core import NativeError


class Pass(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in ('kind', 'axis', 'distance', 'maximum')]


class Plan(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in ('weight_count', 'pass_count', 'repeats', 'field')] + [
        ('weights', C.c_float * 385), ('passes', Pass * 32)]


def plan(lib, effect):
    lib.op_filter_plan_build.argtypes = [C.c_char_p, C.c_double, C.c_int32, C.POINTER(Plan)]
    lib.op_filter_plan_build.restype = C.c_int
    result = Plan()
    status = lib.op_filter_plan_build(effect.kind.encode('ascii'), effect.radius, effect.samples, C.byref(result))
    if status:
        raise NativeError(status, 'Native filter plan rejected the parameters')
    weights = np.ctypeslib.as_array(result.weights)[:result.weight_count].copy()
    passes = [(p.kind, p.axis, p.distance, p.maximum) for p in result.passes[:result.pass_count]]
    return weights, passes, result.repeats, bool(result.field)
