typedef struct channels_owner {
    op_channels_t *state;
} channels_owner_t;
static void channels_destructor(PyObject *capsule) {
    channels_owner_t *owner = PyCapsule_GetPointer(capsule, "openpainter.channels.v1");
    if (owner) {
        op_channels_destroy(owner->state);
        free(owner);
    } else
        PyErr_Clear();
}
static channels_owner_t *channels_owner(PyObject *capsule) {
    channels_owner_t *owner = PyCapsule_GetPointer(capsule, "openpainter.channels.v1");
    if (owner && !owner->state) {
        PyErr_SetString(PyExc_RuntimeError, "Native channel owner is closed");
        return NULL;
    }
    return owner;
}
static PyObject *channels_create(PyObject *self, PyObject *ignored) {
    (void)self;
    (void)ignored;
    channels_owner_t *owner = calloc(1, sizeof(*owner));
    if (!owner)
        return PyErr_NoMemory();
    if (fail(op_channels_create(&owner->state))) {
        free(owner);
        return NULL;
    }
    PyObject *capsule = PyCapsule_New(owner, "openpainter.channels.v1", channels_destructor);
    if (!capsule) {
        op_channels_destroy(owner->state);
        free(owner);
    }
    return capsule;
}
static PyObject *channels_close(PyObject *self, PyObject *capsule) {
    (void)self;
    channels_owner_t *owner = PyCapsule_GetPointer(capsule, "openpainter.channels.v1");
    if (!owner || fail(op_channels_destroy(owner->state)))
        return NULL;
    owner->state = NULL;
    Py_RETURN_NONE;
}
static PyObject *channels_reset(PyObject *self, PyObject *capsule) {
    (void)self;
    channels_owner_t *owner = channels_owner(capsule);
    if (!owner || fail(op_channels_reset(owner->state)))
        return NULL;
    Py_RETURN_NONE;
}
typedef struct channels_callback {
    PyObject *callable;
} channels_callback_t;
static op_status_t channels_submit(void *user, uint32_t action, uint32_t channel) {
    channels_callback_t *ctx = user;
    PyObject *result = PyObject_CallFunction(ctx->callable, "II", action, channel);
    if (!result)
        return OP_VALIDATION_FAILED;
    Py_DECREF(result);
    return OP_OK;
}
static PyObject *channels_execute(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *records, *callback;
    if (!PyArg_ParseTuple(args, "OOO", &capsule, &records, &callback))
        return NULL;
    channels_owner_t *owner = channels_owner(capsule);
    if (!owner)
        return NULL;
    if (!PyCallable_Check(callback)) {
        PyErr_SetString(PyExc_TypeError, "Expected a host channel callback");
        return NULL;
    }
    uint32_t count, written = 0;
    op_channel_request_t *batch = copy_buffer(records, sizeof(*batch), OP_CHANNEL_COUNT, &count);
    if (!batch)
        return NULL;
    channels_callback_t ctx = {callback};
    op_status_t status =
        op_channels_execute(owner->state, batch, count, channels_submit, &ctx, &written);
    free(batch);
    if (PyErr_Occurred() || fail(status))
        return NULL;
    return PyLong_FromUnsignedLong(written);
}
