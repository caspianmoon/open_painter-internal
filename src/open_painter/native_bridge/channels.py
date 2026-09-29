"""Host image registry and callbacks for native atomic channel batches."""
import ctypes as C
from .core import NativeError


class Request(C.Structure):
    _fields_ = [('revision', C.c_uint64), ('target', C.c_uint64),
                ('constant', C.c_double * 4), ('channel', C.c_uint32),
                ('kind', C.c_uint32), ('size', C.c_uint32), ('flags', C.c_uint32)]


Submit = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint32, C.c_uint32)


class NativeChannels:
    def __init__(self, lib):
        self.lib = lib
        self.binding = getattr(lib, '_op_binding', None)
        self.dispatching = False
        self.targets = {}
        self.serial = 0
        if self.binding is not None:
            self.handle = self.binding.channels_create()
        else:
            self.handle = C.c_void_p()
            lib.op_channels_create.argtypes = [C.POINTER(C.c_void_p)]
            lib.op_channels_create.restype = C.c_int
            lib.op_channels_destroy.argtypes = [C.c_void_p]
            lib.op_channels_destroy.restype = C.c_int
            lib.op_channels_execute.argtypes = [C.c_void_p, C.POINTER(Request), C.c_uint32,
                                                Submit, C.c_void_p, C.POINTER(C.c_uint32)]
            lib.op_channels_execute.restype = C.c_int
            self.check(lib.op_channels_create(C.byref(self.handle)))

    @staticmethod
    def check(status):
        if status:
            raise NativeError(status, 'Native channel batch rejected the operation')

    def close(self):
        if self.dispatching:
            raise RuntimeError('Cannot close an executing native channel batch')
        if self.handle:
            if self.binding is not None:
                self.binding.channels_close(self.handle)
            else:
                self.check(self.lib.op_channels_destroy(self.handle))
            self.handle = None
        self.targets.clear()

    def request(self, channel, image, ref, stamp, size, precise, pad, force):
        # RNA addresses are inspected only here; C receives monotonically
        # assigned registry tokens, never a host pointer.
        pointer = (image.as_pointer(), getattr(image, 'session_uid', None))
        previous = self.targets.get(channel)
        if previous is None or previous[0] != pointer:
            self.serial += 1
            self.targets[channel] = pointer, self.serial
        result = Request(channel=channel, target=self.targets[channel][1], size=size,
                         flags=int(force) | (int(precise) << 1) | (int(pad) << 2))
        if ref[0] == 'const':
            result.constant[:] = ref[1]
        else:
            result.kind, result.revision = 1, stamp
        return result

    def execute(self, records, submit):
        if self.dispatching:
            raise RuntimeError('Cannot reenter a native channel batch')
        self.dispatching = True
        try:
            if self.binding is not None:
                return self.binding.channels_execute(self.handle, bytes(records), submit)
            errors = []
            @Submit
            def callback(_user, action, channel):
                try:
                    submit(action, channel)
                    return 0
                except BaseException as error:
                    errors.append(error)
                    return 7
            written = C.c_uint32()
            status = self.lib.op_channels_execute(self.handle, records, len(records), callback,
                                                  None, C.byref(written))
            if errors:
                raise errors[0]
            self.check(status)
            return written.value
        finally:
            self.dispatching = False
