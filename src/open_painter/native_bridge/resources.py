"""Host texture registry for C-owned allocation, lease and eviction policy.

The registry holds Blender API objects; C sees only monotonically allocated IDs.
The legacy accounting fields are mirrored for existing diagnostics, never used
to choose a victim, decide reuse, or enforce a memory budget on this path.
"""
import ctypes as C

from .core import NativeError

EVENT = C.CFUNCTYPE(None, C.c_void_p, C.c_uint64, C.c_uint32)


class Memory(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in ('leased', 'pooled', 'allocations', 'reuses', 'evictions')]


class NativePool:
    def __init__(self, lib, device):
        from ..evaluation.engine.formats import FORMAT_BYTES
        self.lib, self.device = lib, device
        self.formats = {fmt: i for i, fmt in enumerate(FORMAT_BYTES)}
        self.objects, self.tokens = {}, {}
        self.callback = EVENT(self._event)
        self.error = None
        self._registering = False
        self.limits = None
        self.handle = C.c_void_p()
        signatures = {
            'create': [EVENT, C.c_void_p, C.POINTER(C.c_void_p)],
            'destroy': [C.c_void_p],
            'limits': [C.c_void_p, C.c_uint64, C.c_uint32],
            'budget': [C.c_void_p, C.c_uint64],
            'admit': [C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32],
            'register': [C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32, C.c_uint32, C.POINTER(C.c_uint64)],
            'acquire': [C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32, C.POINTER(C.c_uint64)],
            'release': [C.c_void_p, C.c_uint64], 'discard': [C.c_void_p, C.c_uint64],
            'trim': [C.c_void_p, C.c_uint32], 'memory': [C.c_void_p, C.POINTER(Memory)]}
        for name, args in signatures.items():
            fn = getattr(lib, 'op_pool_' + name)
            fn.argtypes, fn.restype = args, None if name == 'destroy' else C.c_int
        self.check(lib.op_pool_create(self.callback, None, C.byref(self.handle)))

    def check(self, status):
        if self.error:
            error, self.error = self.error, None
            raise RuntimeError('Native resource registry callback failed') from error
        if status:
            raise NativeError(status, 'Native resource operation rejected')

    def configure(self, budget, spares, total=None):
        limits = max(0, budget), max(0, spares), total
        if limits != self.limits:
            self.check(self.lib.op_pool_limits(self.handle, *limits[:2]))
            if total is not None:
                self.check(self.lib.op_pool_budget(self.handle, max(0, total)))
            self.limits = limits

    def admit(self, width, height, fmt):
        from ..evaluation.engine.formats import FORMAT_BYTES
        status = self.lib.op_pool_admit(self.handle, width, height, FORMAT_BYTES[fmt])
        if status == 5:
            return False
        self.check(status)
        return True

    def _event(self, _user, token, action):
        try:
            from ..evaluation import metrics
            dev = self.device
            data = self.objects.get(token)
            if data is None and action == 0 and self._registering:
                return  # rollback before the first host metadata insertion
            texture, width, height, fmt, size = data
            identity = id(texture)
            if identity in dev._pooled_ids:
                free = dev.pool[(width, fmt)]
                free.remove(texture)
                if not free:
                    del dev.pool[(width, fmt)]
                dev._pooled_ids.remove(identity)
                dev._pooled_bytes -= size
            dev._leases.pop(identity, None)
            if action == 0:
                del self.objects[token]
                self.tokens.pop(identity, None)
                metrics.count('engine_pool_evictions')
            elif action == 1:
                dev._leases[identity] = size
            elif action == 2:
                dev.pool.setdefault((width, fmt), []).append(texture)
                dev.pool.move_to_end((width, fmt))
                dev._pooled_ids.add(identity)
                dev._pooled_bytes += size
        except BaseException as error:
            # Never allow ctypes to swallow an exception across the C callback.
            self.error = error

    def register(self, texture, width, height, fmt):
        from ..evaluation.engine.formats import FORMAT_BYTES, texture_bytes
        token = C.c_uint64()
        size = texture_bytes(width, height, fmt)
        record = texture, width, height, fmt, size
        self._registering = True
        try:
            self.check(self.lib.op_pool_register(self.handle, width, height, self.formats[fmt],
                                                FORMAT_BYTES[fmt], C.byref(token)))
            try:
                self.objects[token.value] = record
                self.tokens[id(texture)] = token.value
                self.device._leases[id(texture)] = size
            except BaseException:
                self.check(self.lib.op_pool_discard(self.handle, token))
                raise
        finally:
            self._registering = False

    def acquire(self, n, fmt):
        token = C.c_uint64()
        self.check(self.lib.op_pool_acquire(self.handle, n, n, self.formats[fmt], C.byref(token)))
        return self.objects[token.value][0] if token.value else None

    def release(self, texture, n, fmt):
        if texture is None:
            return
        token = self.tokens.get(id(texture))
        if token is None:
            # Host-created inputs may be returned without an earlier allocation.
            self.register(texture, texture.width, texture.height, texture.format)
            token = self.tokens[id(texture)]
        actual = self.objects[token]
        if actual[1:4] != (n, n, fmt):
            self.check(self.lib.op_pool_discard(self.handle, token))
        else:
            self.check(self.lib.op_pool_release(self.handle, token))

    def discard(self, texture):
        token = self.tokens.get(id(texture))
        if token is not None:
            self.check(self.lib.op_pool_discard(self.handle, token))

    def trim(self, spares):
        self.check(self.lib.op_pool_trim(self.handle, max(0, spares)))

    def memory(self):
        result = Memory()
        self.check(self.lib.op_pool_memory(self.handle, C.byref(result)))
        return {'leased_bytes': result.leased, 'pooled_bytes': result.pooled}

    def close(self):
        if self.handle:
            self.lib.op_pool_destroy(self.handle)
            self.handle = None
            self.check(0)
        self.objects.clear()
        self.tokens.clear()
