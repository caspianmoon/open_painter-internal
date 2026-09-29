/* Affine suffix collapse: IR classification and run planning over a graph. */
static PyObject *ir_backdrop_affine(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *nodes, *targets;
    unsigned int backdrop;
    uint32_t n, t, affine = 0;
    uint8_t source[4];
    if (!PyArg_ParseTuple(args, "OOI", &nodes, &targets, &backdrop))
        return NULL;
    op_ir_node_t *data = copy_buffer(nodes, sizeof(*data), 262144, &n);
    if (!data)
        return NULL;
    op_ir_target_t *out = copy_buffer(targets, sizeof(*out), 8, &t);
    if (!out) {
        free(data);
        return NULL;
    }
    op_status_t status;
    Py_BEGIN_ALLOW_THREADS status = op_ir_backdrop_affine(data, n, out, t, backdrop, &affine, source);
    Py_END_ALLOW_THREADS free(data);
    free(out);
    if (fail(status))
        return NULL;
    if (!affine)
        Py_RETURN_NONE;
    return Py_BuildValue("(BBBB)", source[0], source[1], source[2], source[3]);
}
static PyObject *graph_compose(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *backdrop, *grouped, *affine;
    uint32_t b, p, a;
    if (!PyArg_ParseTuple(args, "OOOO", &capsule, &backdrop, &grouped, &affine))
        return NULL;
    graph_owner *g = graph_get(capsule);
    if (!g)
        return NULL;
    uint32_t *back = copy_buffer(backdrop, 4, 65536, &b);
    if (!back)
        return NULL;
    uint32_t *group = copy_buffer(grouped, 4, 65536, &p);
    if (!group) {
        free(back);
        return NULL;
    }
    unsigned char *flags = copy_buffer(affine, 1, 65536, &a);
    if (!flags) {
        free(back);
        free(group);
        return NULL;
    }
    op_status_t status = (b != g->count || p != g->count || a != g->count)
                             ? OP_VALIDATION_FAILED
                             : op_graph_set_composition(g->graph, back, group, flags, b);
    free(back);
    free(group);
    free(flags);
    if (fail(status))
        return NULL;
    Py_RETURN_NONE;
}
/* Runs for one root against the stamps of the last graph_evaluate. */
static PyObject *graph_runs(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *current;
    unsigned int root;
    uint32_t n, runs_used = 0, members_used = 0;
    if (!PyArg_ParseTuple(args, "OIO", &capsule, &root, &current))
        return NULL;
    graph_owner *g = graph_get(capsule);
    if (!g)
        return NULL;
    uint64_t *resident = copy_buffer(current, 8, 65536, &n);
    if (!resident)
        return NULL;
    if (n != g->count) {
        free(resident);
        PyErr_SetString(PyExc_ValueError, "Graph resident count mismatch");
        return NULL;
    }
    op_graph_run_t *runs = malloc(((size_t)n + 1) * sizeof(*runs));
    uint32_t *members = malloc(((size_t)n + 1) * sizeof(*members));
    if (!runs || !members) {
        free(resident);
        free(runs);
        free(members);
        return PyErr_NoMemory();
    }
    op_status_t status = op_graph_suffix_runs(g->graph, root, resident, g->stamps, runs, n,
                                              &runs_used, members, n, &members_used);
    free(resident);
    if (fail(status)) {
        free(runs);
        free(members);
        return NULL;
    }
    PyObject *list = PyList_New(runs_used);
    for (uint32_t i = 0; list && i < runs_used; i++) {
        const op_graph_run_t *r = &runs[i];
        PyObject *ids = PyTuple_New(r->length);
        for (uint32_t j = 0; ids && j < r->length; j++) {
            PyObject *value = PyLong_FromUnsignedLong(members[r->first + j]);
            if (!value) {
                Py_CLEAR(ids);
                break;
            }
            PyTuple_SetItem(ids, j, value);
        }
        PyObject *row = ids ? Py_BuildValue("(IIKN)", r->top, r->input, (unsigned long long)r->key, ids)
                            : NULL;
        if (!row) {
            Py_CLEAR(list);
            break;
        }
        PyList_SetItem(list, i, row);
    }
    free(runs);
    free(members);
    return list;
}
