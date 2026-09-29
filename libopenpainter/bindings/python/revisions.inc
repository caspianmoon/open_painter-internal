/* Host callbacks remain on the graphics context thread with the GIL held. */
typedef struct revisions_owner {
    op_revisions_t *state;
    int publishing;
} revisions_owner_t;
static void revisions_destructor(PyObject *capsule) {
    revisions_owner_t *owner = PyCapsule_GetPointer(capsule, "openpainter.revisions");
    if (owner) {
        op_revisions_destroy(owner->state);
        free(owner);
    } else
        PyErr_Clear();
}
static revisions_owner_t *revisions_owner(PyObject *capsule) {
    revisions_owner_t *owner = PyCapsule_GetPointer(capsule, "openpainter.revisions");
    if (owner && (!owner->state || owner->publishing)) {
        PyErr_SetString(PyExc_RuntimeError, "Native revision owner is closed or submitting");
        return NULL;
    }
    return owner;
}
static PyObject *revisions_create(PyObject *self, PyObject *ignored) {
    (void)self;
    (void)ignored;
    revisions_owner_t *owner = calloc(1, sizeof(*owner));
    if (!owner)
        return PyErr_NoMemory();
    if (fail(op_revisions_create(&owner->state))) {
        free(owner);
        return NULL;
    }
    PyObject *capsule = PyCapsule_New(owner, "openpainter.revisions", revisions_destructor);
    if (!capsule) {
        op_revisions_destroy(owner->state);
        free(owner);
    }
    return capsule;
}
static PyObject *revisions_close(PyObject *self, PyObject *capsule) {
    (void)self;
    revisions_owner_t *owner = PyCapsule_GetPointer(capsule, "openpainter.revisions");
    if (!owner)
        return NULL;
    if (owner->publishing) {
        PyErr_SetString(PyExc_RuntimeError, "Cannot close a submitting native revision");
        return NULL;
    }
    op_revisions_destroy(owner->state);
    owner->state = NULL;
    Py_RETURN_NONE;
}
static PyObject *revisions_references(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule;
    unsigned long long token;
    if (!PyArg_ParseTuple(args, "OK", &capsule, &token))
        return NULL;
    revisions_owner_t *owner = revisions_owner(capsule);
    if (!owner)
        return NULL;
    return PyLong_FromUnsignedLong(op_revisions_references(owner->state, (uint64_t)token));
}
typedef struct publication_callback {
    PyObject *callable, *type, *value, *traceback, *restore_type, *restore_value,
        *restore_traceback;
} publication_callback_t;
static op_status_t publication_submit(void *user, uint32_t action, uint32_t index, uint32_t level) {
    publication_callback_t *ctx = user;
    PyObject *result = PyObject_CallFunction(ctx->callable, "III", action, index, level);
    if (result) {
        Py_DECREF(result);
        return OP_OK;
    }
    if (action == OP_PUBLICATION_RESTORE && !ctx->restore_type)
        PyErr_Fetch(&ctx->restore_type, &ctx->restore_value, &ctx->restore_traceback);
    else if (!ctx->type)
        PyErr_Fetch(&ctx->type, &ctx->value, &ctx->traceback);
    else
        PyErr_Clear();
    return OP_VALIDATION_FAILED;
}
static PyObject *revisions_publish(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *records, *callback;
    int batched = 0;
    if (!PyArg_ParseTuple(args, "OOO|p", &capsule, &records, &callback, &batched))
        return NULL;
    revisions_owner_t *owner = revisions_owner(capsule);
    if (!owner)
        return NULL;
    if (!PyCallable_Check(callback)) {
        PyErr_SetString(PyExc_TypeError, "Expected a host submission callback");
        return NULL;
    }
    uint32_t count;
    op_publication_t *channels = copy_buffer(records, sizeof(*channels), 8, &count);
    if (!channels)
        return NULL;
    publication_callback_t ctx = {0};
    ctx.callable = callback;
    uint64_t revision = 0;
    owner->publishing = 1;
    op_status_t status =
        (batched ? op_revisions_publish_batched : op_revisions_publish)(
            owner->state, channels, count, publication_submit, &ctx, &revision);
    owner->publishing = 0;
    free(channels);
    if (ctx.restore_type) {
        PyErr_NormalizeException(&ctx.restore_type, &ctx.restore_value, &ctx.restore_traceback);
        PyObject *error = PyObject_CallFunction(
            PyExc_RuntimeError, "s",
            "GPU publication and recovery failed; reevaluate after context recovery");
        if (error) {
            PyException_SetCause(error, ctx.restore_value);
            ctx.restore_value = NULL;
            PyErr_SetObject(PyExc_RuntimeError, error);
            Py_DECREF(error);
        }
        Py_XDECREF(ctx.restore_type);
        Py_XDECREF(ctx.restore_value);
        Py_XDECREF(ctx.restore_traceback);
        Py_XDECREF(ctx.type);
        Py_XDECREF(ctx.value);
        Py_XDECREF(ctx.traceback);
        return NULL;
    }
    if (ctx.type) {
        PyErr_Restore(ctx.type, ctx.value, ctx.traceback);
        return NULL;
    }
    if (fail(status))
        return NULL;
    return PyLong_FromUnsignedLongLong(revision);
}
