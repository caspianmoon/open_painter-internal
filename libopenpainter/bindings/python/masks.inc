static PyObject *mask_plan(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *buffer;
    unsigned start, stop, pre_filter, initial;
    if (!PyArg_ParseTuple(args, "OIIII", &buffer, &start, &stop, &pre_filter, &initial))
        return NULL;
    uint32_t count;
    op_mask_node_t *nodes = copy_buffer(buffer, sizeof(*nodes), 65536, &count);
    if (!nodes)
        return NULL;
    op_mask_plan_t *plan = NULL;
    op_status_t status;
    Py_BEGIN_ALLOW_THREADS status =
        op_mask_plan_build(nodes, count, start, stop, pre_filter, initial, &plan);
    Py_END_ALLOW_THREADS free(nodes);
    if (fail(status))
        return NULL;
    PyObject *result = Py_BuildValue(
        "(y#y#I)", (const char *)plan->steps, (Py_ssize_t)plan->step_count * sizeof(op_mask_step_t),
        (const char *)plan->walk, (Py_ssize_t)plan->walk_count * sizeof(uint32_t), plan->result);
    op_mask_plan_free(plan);
    return result;
}
