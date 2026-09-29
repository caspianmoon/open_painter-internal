/* The graph driver calls host resource bindings on the context thread. */
typedef struct graph_callback {
    PyObject *callable, *type, *value, *traceback;
} graph_callback_t;
static op_status_t graph_submit(void *user, uint32_t action, uint32_t node, uint64_t stamp) {
    graph_callback_t *ctx = user;
    PyObject *result =
        PyObject_CallFunction(ctx->callable, "IIK", action, node, (unsigned long long)stamp);
    if (result) {
        Py_DECREF(result);
        return OP_OK;
    }
    /* Clear the exception while C unwinds all remaining resource holds. */
    if (!ctx->type)
        PyErr_Fetch(&ctx->type, &ctx->value, &ctx->traceback);
    else
        PyErr_Clear();
    return OP_VALIDATION_FAILED;
}
static PyObject *graph_execute(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *residents, *callback;
    unsigned root;
    if (!PyArg_ParseTuple(args, "OIOO", &capsule, &root, &residents, &callback))
        return NULL;
    graph_owner *owner = graph_get(capsule);
    if (!owner)
        return NULL;
    if (!PyCallable_Check(callback)) {
        PyErr_SetString(PyExc_TypeError, "Expected a host graph callback");
        return NULL;
    }
    uint32_t count, rendered = 0;
    uint64_t *resident = copy_buffer(residents, sizeof(*resident), 65536, &count);
    if (!resident)
        return NULL;
    if (count != owner->count) {
        free(resident);
        PyErr_SetString(PyExc_ValueError, "Resident stamp count does not match the graph");
        return NULL;
    }
    graph_callback_t ctx = {0};
    ctx.callable = callback;
    owner->executing = 1;
    op_status_t status = op_graph_execute(owner->graph, root, resident, owner->stamps, graph_submit,
                                          &ctx, &rendered);
    owner->executing = 0;
    free(resident);
    if (ctx.type) {
        PyErr_Restore(ctx.type, ctx.value, ctx.traceback);
        return NULL;
    }
    if (fail(status))
        return NULL;
    return PyLong_FromUnsignedLong(rendered);
}
