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
    PyObject *capsule, *backdrop, *grouped, *affine, *previous = NULL;
    uint32_t b, p, a, v = 0;
    if (!PyArg_ParseTuple(args, "OOOO|O", &capsule, &backdrop, &grouped, &affine, &previous))
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
    /* previous[i]: node i's index before the topology changed (op_graph_set_composition_from). */
    uint32_t *before = NULL;
    if (previous && previous != Py_None) {
        before = copy_buffer(previous, 4, 65536, &v);
        if (!before) {
            free(back);
            free(group);
            free(flags);
            return NULL;
        }
    }
    op_status_t status = (b != g->count || p != g->count || a != g->count || (before && v != g->count))
                             ? OP_VALIDATION_FAILED
                         : before ? op_graph_set_composition_from(g->graph, back, group, flags, b, before)
                                  : op_graph_set_composition(g->graph, back, group, flags, b);
    free(back);
    free(group);
    free(flags);
    free(before);
    if (fail(status))
        return NULL;
    Py_RETURN_NONE;
}
static PyObject *run_rows(const op_graph_run_t *runs, uint32_t count, const uint32_t *members) {
    PyObject *list = PyList_New(count);
    for (uint32_t i = 0; list && i < count; i++) {
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
    return list;
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
    PyObject *list = fail(status) ? NULL : run_rows(runs, runs_used, members);
    free(runs);
    free(members);
    return list;
}
/* Nodes the host rendered itself (a fused pass): recorded as rendered with their
 * current local stamps (op_graph_mark_rendered). */
static PyObject *graph_mark_rendered(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *nodes;
    uint32_t n;
    if (!PyArg_ParseTuple(args, "OO", &capsule, &nodes))
        return NULL;
    graph_owner *g = graph_get(capsule);
    if (!g)
        return NULL;
    uint32_t *indices = copy_buffer(nodes, 4, 65536, &n);
    if (!indices)
        return NULL;
    op_status_t status = op_graph_mark_rendered(g->graph, indices, n);
    free(indices);
    if (fail(status))
        return NULL;
    Py_RETURN_NONE;
}
/* The runs an edit of node's own content would produce under root, given the
 * resident stamps and those of the last graph_evaluate. */
static PyObject *graph_edit_runs(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *current;
    unsigned int root, node;
    uint32_t n, runs_used = 0, members_used = 0;
    if (!PyArg_ParseTuple(args, "OIIO", &capsule, &root, &node, &current))
        return NULL;
    graph_owner *g = graph_get(capsule);
    if (!g)
        return NULL;
    uint64_t *resident = NULL;
    if (current != Py_None) {
        resident = copy_buffer(current, 8, 65536, &n);
        if (!resident)
            return NULL;
        if (n != g->count) {
            free(resident);
            PyErr_SetString(PyExc_ValueError, "Graph resident count mismatch");
            return NULL;
        }
    }
    op_graph_run_t *runs = malloc(((size_t)g->count + 1) * sizeof(*runs));
    uint32_t *members = malloc(((size_t)g->count + 1) * sizeof(*members));
    if (!runs || !members) {
        free(resident);
        free(runs);
        free(members);
        return PyErr_NoMemory();
    }
    op_status_t status = op_graph_edit_runs(g->graph, root, node, resident, resident ? g->stamps : NULL,
                                            runs, g->count, &runs_used, members, g->count,
                                            &members_used);
    free(resident);
    PyObject *list = fail(status) ? NULL : run_rows(runs, runs_used, members);
    free(runs);
    free(members);
    return list;
}

static PyObject *index_tuple(const uint32_t *values, uint32_t count) {
    PyObject *out = PyTuple_New(count);
    for (uint32_t i = 0; out && i < count; i++) {
        PyObject *value = PyLong_FromUnsignedLong(values[i]);
        if (!value) {
            Py_CLEAR(out);
            break;
        }
        PyTuple_SetItem(out, i, value);
    }
    return out;
}
/* The spine of one node: what an edit of it composes over without changing. */
static PyObject *graph_spine(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule;
    unsigned int node;
    uint32_t count = 0;
    if (!PyArg_ParseTuple(args, "OI", &capsule, &node))
        return NULL;
    graph_owner *g = graph_get(capsule);
    if (!g)
        return NULL;
    uint32_t *keep = malloc(((size_t)g->count + 1) * sizeof(*keep));
    if (!keep)
        return PyErr_NoMemory();
    op_status_t status = op_graph_spine(g->graph, node, keep, g->count, &count);
    PyObject *out = fail(status) ? NULL : index_tuple(keep, count);
    free(keep);
    return out;
}
/* Checkpoints of one channel: the spine of its lowest changed node against the
 * stamps of the last graph_evaluate. */
static PyObject *graph_checkpoints(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *order, *current;
    uint32_t n, c, count = 0, lowest = UINT32_MAX;
    if (!PyArg_ParseTuple(args, "OOO", &capsule, &order, &current))
        return NULL;
    graph_owner *g = graph_get(capsule);
    if (!g)
        return NULL;
    uint32_t *nodes = copy_buffer(order, 4, 65536, &n);
    if (!nodes)
        return NULL;
    uint64_t *stamps = copy_buffer(current, 8, 65536, &c);
    if (!stamps) {
        free(nodes);
        return NULL;
    }
    uint32_t *keep = malloc(((size_t)g->count + 1) * sizeof(*keep));
    if (!keep || c != g->count) {
        free(nodes);
        free(stamps);
        free(keep);
        if (keep)
            PyErr_SetString(PyExc_ValueError, "Checkpoint buffer size mismatch");
        return keep ? NULL : PyErr_NoMemory();
    }
    op_status_t status = op_graph_lowest_changed(g->graph, nodes, n, stamps, g->stamps, &lowest);
    if (!status && lowest != UINT32_MAX)
        status = op_graph_spine(g->graph, lowest, keep, g->count, &count);
    free(nodes);
    free(stamps);
    PyObject *out = fail(status) ? NULL : index_tuple(keep, count);
    free(keep);
    return out;
}
