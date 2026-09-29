"""Native LRU residency policy; the host holds only resource references."""
import ctypes as C
from .core import NativeError


class Cache:
    def __init__(self, lib):
        self.lib, self.handle = lib, C.c_void_p()
        self.keys, self.names, self.serial = {}, {}, 0
        self.output = (C.c_uint64 * 64)()
        for name, args, result in (
            ('create', [C.POINTER(C.c_void_p)], C.c_int), ('destroy', [C.c_void_p], None),
            ('put', [C.c_void_p, C.c_uint64, C.c_uint64], C.c_int),
            ('remove', [C.c_void_p, C.c_uint64], None), ('touch', [C.c_void_p, C.c_uint64], None),
            ('bytes', [C.c_void_p], C.c_uint64),
            ('trim', [C.c_void_p, C.c_uint64, C.POINTER(C.c_uint64), C.c_uint32,
                      C.POINTER(C.c_uint64), C.c_uint32, C.POINTER(C.c_uint32)], C.c_int)):
            fn = getattr(lib, 'op_cache_' + name)
            fn.argtypes, fn.restype = args, result
        self.check(lib.op_cache_create(C.byref(self.handle)))

    @staticmethod
    def check(status):
        if status:
            raise NativeError(status, 'Native cache rejected the residency update')

    def put(self, key, size):
        if key not in self.keys:
            self.serial += 1
            self.keys[key] = self.serial
            self.names[self.serial] = key
            if len(self.keys) > len(self.output):
                self.output = (C.c_uint64 * (len(self.output) * 2))()
        self.check(self.lib.op_cache_put(self.handle, self.keys[key], size))

    def touch(self, key):
        if key in self.keys:
            self.lib.op_cache_touch(self.handle, self.keys[key])

    def remove(self, key):
        if key in self.keys:
            token = self.keys.pop(key)
            self.lib.op_cache_remove(self.handle, token)
            self.names.pop(token)

    def trim(self, budget, protected=()):
        keys = [self.keys[k] for k in protected if k in self.keys]
        out, count = self.output, C.c_uint32()
        self.check(self.lib.op_cache_trim(self.handle, max(0, budget), (C.c_uint64 * len(keys))(*keys),
                                         len(keys), out, len(out), C.byref(count)))
        victims = [self.names[out[i]] for i in range(count.value)]
        for key in victims:
            self.names.pop(self.keys.pop(key))
        return victims

    def close(self):
        if self.handle:
            self.lib.op_cache_destroy(self.handle)
            self.handle = None
        self.keys.clear()
        self.names.clear()

    __del__ = close


def select_victims(entries, budget, size, protected=()):
    """Apply native residency policy to an ordered host-resource snapshot.

    Used by small auxiliary caches; the main evaluator keeps its native cache
    incrementally. The host retains objects, the core chooses retirement.
    """
    from .lifecycle import active_library
    lib = active_library()
    if lib is None:
        total, victims = sum(size(value) for value in entries.values()), []
        for key, value in entries.items():
            if total <= max(0, budget):
                break
            if key not in protected:
                victims.append(key)
                total -= size(value)
        return victims
    cache = Cache(lib)
    try:
        for key, value in entries.items():
            cache.put(key, size(value))
        return cache.trim(budget, protected)
    finally:
        cache.close()
