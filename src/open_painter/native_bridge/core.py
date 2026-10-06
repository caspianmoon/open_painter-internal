"""ctypes bindings for the libopenpainter P2 ABI (spike).

Declares C signatures once (bind), then offers a small object wrapper
(NativeCore) that turns status codes into NativeError. No per-texel calls,
no retained pointers: buffers are copied by the core on snapshot write.
"""

import ctypes

OP_ABI_VERSION = 14


class Status:
    OK = 0
    INVALID_ARGUMENT = 1
    STALE_HANDLE = 2
    UNSUPPORTED = 3
    PENDING = 4
    RESOURCE_EXHAUSTED = 5
    DEVICE_LOSS = 6
    VALIDATION_FAILED = 7


class Channel:
    BASE_COLOR = 0
    ROUGHNESS = 1
    METALLIC = 2
    NORMAL = 3
    HEIGHT = 4
    AO = 5
    EMISSION = 6
    OPACITY = 7


LAYER_FILL = 1


class Blend:
    NORMAL = 0
    MULTIPLY = 1
    OVERLAY = 2
    DISABLE = 3


class Capabilities(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("max_texture_size", ctypes.c_uint32),
        ("has_gpu", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


class FrameLease(ctypes.Structure):
    _fields_ = [
        ("document_id", ctypes.c_uint64),
        ("generation", ctypes.c_uint64),
        ("revision", ctypes.c_uint64),
        ("channel_mask", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("lease_id", ctypes.c_uint64),
        ("context_id", ctypes.c_uint64),
    ]


class Metrics(ctypes.Structure):
    _fields_ = [
        ("documents_created", ctypes.c_uint64),
        ("commits", ctypes.c_uint64),
        ("cancels", ctypes.c_uint64),
        ("evaluations", ctypes.c_uint64),
        ("frames_acquired", ctypes.c_uint64),
        ("frames_released", ctypes.c_uint64),
    ]


class NativeError(RuntimeError):
    def __init__(self, status, detail):
        super().__init__(f"libopenpainter status {status}: {detail}")
        self.status = status


def bind(lib):
    """Declare argument/return types on a CDLL instance (idempotent)."""
    u32, u64 = ctypes.c_uint32, ctypes.c_uint64
    void_p = ctypes.c_void_p
    lib.op_get_abi_version.restype = u32
    lib.op_query_capabilities.argtypes = [void_p, ctypes.POINTER(Capabilities)]
    lib.op_query_capabilities.restype = ctypes.c_int
    lib.op_context_create.argtypes = [ctypes.POINTER(void_p)]
    lib.op_context_create.restype = ctypes.c_int
    lib.op_context_destroy.argtypes = [void_p]
    lib.op_context_destroy.restype = None
    lib.op_document_create.argtypes = [void_p, u64, ctypes.POINTER(void_p)]
    lib.op_document_create.restype = ctypes.c_int
    lib.op_document_destroy.argtypes = [void_p, void_p]
    lib.op_document_destroy.restype = ctypes.c_int
    lib.op_document_new_generation.argtypes = [void_p, void_p]
    lib.op_document_new_generation.restype = ctypes.c_int
    lib.op_transaction_begin.argtypes = [void_p, void_p, ctypes.POINTER(u64)]
    lib.op_transaction_begin.restype = ctypes.c_int
    lib.op_transaction_commit.argtypes = [void_p, void_p, u64]
    lib.op_transaction_commit.restype = ctypes.c_int
    lib.op_transaction_cancel.argtypes = [void_p, void_p, u64]
    lib.op_transaction_cancel.restype = ctypes.c_int
    lib.op_layer_add.argtypes = [void_p, void_p, u64, u64, u32]
    lib.op_layer_add.restype = ctypes.c_int
    lib.op_layer_set_scalar.argtypes = [void_p, void_p, u64, u64, u32, ctypes.c_float]
    lib.op_layer_set_scalar.restype = ctypes.c_int
    lib.op_layer_set_color.argtypes = [void_p, void_p, u64, u64, u32,
                                       ctypes.POINTER(ctypes.c_float)]
    lib.op_layer_set_color.restype = ctypes.c_int
    lib.op_layer_set_opacity.argtypes = [void_p, void_p, u64, u64, ctypes.c_float]
    lib.op_layer_set_opacity.restype = ctypes.c_int
    lib.op_layer_set_blend.argtypes = [void_p, void_p, u64, u64, u32]
    lib.op_layer_set_blend.restype = ctypes.c_int
    lib.op_layer_reorder.argtypes = [void_p, void_p, u64, u64, u32]
    lib.op_layer_reorder.restype = ctypes.c_int
    lib.op_evaluate_request.argtypes = [void_p, void_p, ctypes.POINTER(u64)]
    lib.op_evaluate_request.restype = ctypes.c_int
    lib.op_poll_completions.argtypes = [void_p, void_p, u64, ctypes.POINTER(ctypes.c_int)]
    lib.op_poll_completions.restype = ctypes.c_int
    lib.op_frame_acquire.argtypes = [void_p, void_p, ctypes.POINTER(FrameLease)]
    lib.op_frame_acquire.restype = ctypes.c_int
    lib.op_frame_release.argtypes = [void_p, void_p, ctypes.POINTER(FrameLease)]
    lib.op_frame_release.restype = ctypes.c_int
    lib.op_composite_read.argtypes = [void_p, void_p, u64, u32, u32,
                                      ctypes.POINTER(ctypes.c_float), ctypes.c_size_t]
    lib.op_composite_read.restype = ctypes.c_int
    lib.op_document_snapshot_size.argtypes = [void_p, void_p, ctypes.POINTER(ctypes.c_size_t)]
    lib.op_document_snapshot_size.restype = ctypes.c_int
    lib.op_document_snapshot_write.argtypes = [void_p, void_p, void_p, ctypes.c_size_t]
    lib.op_document_snapshot_write.restype = ctypes.c_int
    lib.op_document_restore.argtypes = [void_p, void_p, ctypes.c_size_t,
                                        ctypes.POINTER(void_p)]
    lib.op_document_restore.restype = ctypes.c_int
    lib.op_get_metrics.argtypes = [void_p, ctypes.POINTER(Metrics)]
    lib.op_get_metrics.restype = ctypes.c_int
    lib.op_copy_last_error.argtypes = [void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.op_copy_last_error.restype = ctypes.c_int
    return lib


class NativeCore:
    """Owns one op_context_t; documents are raw handles by design (spike)."""

    def __init__(self, lib):
        self._ctx = None
        self._lib = bind(lib)
        ctx = ctypes.c_void_p()
        self._check(self._lib.op_context_create(ctypes.byref(ctx)))
        self._ctx = ctx

    def close(self):
        if self._ctx is not None:
            self._lib.op_context_destroy(self._ctx)
            self._ctx = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _check(self, status):
        if status != Status.OK:
            detail = ""
            if self._ctx is not None:
                buffer = ctypes.create_string_buffer(256)
                if self._lib.op_copy_last_error(self._ctx, buffer, len(buffer)) == 0:
                    detail = buffer.value.decode("utf-8", "replace")
            raise NativeError(status, detail)
        return status

    def capabilities(self):
        caps = Capabilities()
        self._check(self._lib.op_query_capabilities(self._ctx, ctypes.byref(caps)))
        return caps

    def document_create(self, document_id):
        doc = ctypes.c_void_p()
        self._check(self._lib.op_document_create(self._ctx, document_id,
                                                 ctypes.byref(doc)))
        return doc

    def document_destroy(self, doc):
        self._check(self._lib.op_document_destroy(self._ctx, doc))

    def new_generation(self, doc):
        self._check(self._lib.op_document_new_generation(self._ctx, doc))

    def begin(self, doc):
        tx = ctypes.c_uint64()
        self._check(self._lib.op_transaction_begin(self._ctx, doc, ctypes.byref(tx)))
        return tx.value

    def commit(self, doc, tx):
        self._check(self._lib.op_transaction_commit(self._ctx, doc, tx))

    def cancel(self, doc, tx):
        self._check(self._lib.op_transaction_cancel(self._ctx, doc, tx))

    def layer_add(self, doc, tx, layer_id, layer_type=LAYER_FILL):
        self._check(self._lib.op_layer_add(self._ctx, doc, tx, layer_id, layer_type))

    def set_scalar(self, doc, tx, layer_id, channel, value):
        self._check(self._lib.op_layer_set_scalar(self._ctx, doc, tx, layer_id,
                                                  channel, value))

    def set_color(self, doc, tx, layer_id, channel, rgba):
        array = (ctypes.c_float * 4)(*rgba)
        self._check(self._lib.op_layer_set_color(self._ctx, doc, tx, layer_id,
                                                 channel, array))

    def reorder(self, doc, tx, layer_id, new_index):
        self._check(self._lib.op_layer_reorder(self._ctx, doc, tx, layer_id,
                                               new_index))

    def set_opacity(self, doc, tx, layer_id, opacity):
        self._check(self._lib.op_layer_set_opacity(self._ctx, doc, tx, layer_id,
                                                   opacity))

    def set_blend(self, doc, tx, layer_id, blend_mode):
        self._check(self._lib.op_layer_set_blend(self._ctx, doc, tx, layer_id,
                                                 blend_mode))

    def evaluate(self, doc):
        req = ctypes.c_uint64()
        self._check(self._lib.op_evaluate_request(self._ctx, doc, ctypes.byref(req)))
        ready = ctypes.c_int()
        self._check(self._lib.op_poll_completions(self._ctx, doc, req.value,
                                                  ctypes.byref(ready)))
        assert ready.value == 1
        return req.value

    def acquire(self, doc):
        lease = FrameLease()
        self._check(self._lib.op_frame_acquire(self._ctx, doc, ctypes.byref(lease)))
        return lease

    def release(self, doc, lease):
        self._check(self._lib.op_frame_release(self._ctx, doc, ctypes.byref(lease)))

    def composite_read(self, doc, revision, channel, size):
        """Read one channel of the ready revision as a size*size*4 float list."""
        if not isinstance(size, int) or not 1 <= size <= 8192:
            raise ValueError("size must be between 1 and 8192")
        count = size * size * 4
        array = (ctypes.c_float * count)()
        self._check(self._lib.op_composite_read(self._ctx, doc, revision, channel,
                                                size, array, count))
        return list(array)

    def snapshot(self, doc):
        size = ctypes.c_size_t()
        self._check(self._lib.op_document_snapshot_size(self._ctx, doc,
                                                        ctypes.byref(size)))
        buffer = ctypes.create_string_buffer(size.value)
        self._check(self._lib.op_document_snapshot_write(self._ctx, doc, buffer,
                                                         size.value))
        return bytes(buffer.raw)

    def restore(self, data):
        buffer = ctypes.create_string_buffer(bytes(data), len(data))
        doc = ctypes.c_void_p()
        self._check(self._lib.op_document_restore(self._ctx, buffer, len(data),
                                                  ctypes.byref(doc)))
        return doc

    def metrics(self):
        metrics = Metrics()
        self._check(self._lib.op_get_metrics(self._ctx, ctypes.byref(metrics)))
        return metrics
