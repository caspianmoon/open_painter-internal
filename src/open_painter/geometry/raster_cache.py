"""Bounded, content-addressed UV sampling shared by geometry masks.

Only detached arrays are retained. Object identity, transforms and mask membership
do not affect barycentric UV sampling; evaluated UVs, triangle order and resolution
do. Overlaps still use the rasterizer's last-triangle-wins rule.
"""
from collections import OrderedDict
import hashlib

import numpy as np

LIMIT = 128 * 1024 * 1024
_entries = OrderedDict()
_bytes = 0


def clear():
    global _bytes
    _entries.clear()
    _bytes = 0


def sample(uv, resolution):
    global _bytes
    from .uv_rasterizer import rasterize
    uv = np.ascontiguousarray(uv, dtype=np.float32)
    key = (int(resolution), uv.shape, hashlib.sha256(uv.tobytes()).digest())
    found = _entries.get(key)
    if found is not None:
        _entries.move_to_end(key)
        return found
    result = rasterize(uv, resolution)
    size = sum(value.nbytes for value in result)
    if size <= LIMIT:
        while _entries and _bytes + size > LIMIT:
            _, old = _entries.popitem(last=False)
            _bytes -= sum(value.nbytes for value in old)
        for value in result:
            value.flags.writeable = False
        _entries[key] = result
        _bytes += size
    return result
