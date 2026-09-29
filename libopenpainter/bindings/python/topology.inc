static PyObject *uv_topology(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *face_buffer, *vertex_buffer, *uv_buffer;
    unsigned size;
    if (!PyArg_ParseTuple(args, "OOOI", &face_buffer, &vertex_buffer, &uv_buffer, &size))
        return NULL;
    uint32_t faces_count, vertices_count, uv_count;
    op_uv_face_t *faces = copy_buffer(face_buffer, sizeof(*faces), 1048576, &faces_count);
    if (!faces)
        return NULL;
    int64_t *vertices = copy_buffer(vertex_buffer, sizeof(*vertices), 4194304, &vertices_count);
    if (!vertices) {
        free(faces);
        return NULL;
    }
    float *uv = copy_buffer(uv_buffer, 2 * sizeof(*uv), 4194304, &uv_count);
    if (!uv) {
        free(faces);
        free(vertices);
        return NULL;
    }
    if (vertices_count != uv_count) {
        free(faces);
        free(vertices);
        free(uv);
        PyErr_SetString(PyExc_ValueError, "UV and vertex loop counts must match");
        return NULL;
    }
    op_uv_topology_t *topology = NULL;
    op_status_t status;
    Py_BEGIN_ALLOW_THREADS status =
        op_uv_topology_build(faces, faces_count, vertices, uv, uv_count, size, &topology);
    Py_END_ALLOW_THREADS free(faces);
    free(vertices);
    free(uv);
    if (fail(status))
        return NULL;
    PyObject *result = Py_BuildValue(
        "(y#y#y#y#I)", (const char *)topology->labels,
        (Py_ssize_t)topology->face_count * sizeof(int64_t), (const char *)topology->islands,
        (Py_ssize_t)topology->face_count * sizeof(int64_t), (const char *)topology->borders,
        (Py_ssize_t)topology->border_count * sizeof(op_uv_border_t), (const char *)topology->sides,
        (Py_ssize_t)topology->side_count * sizeof(op_uv_side_t), topology->island_count);
    op_uv_topology_free(topology);
    return result;
}

static PyObject *uv_bounds(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *raster;
    unsigned size, islands;
    if (!PyArg_ParseTuple(args, "OII", &raster, &size, &islands))
        return NULL;
    if (!size || size > 8192 || islands > 1048576) {
        PyErr_SetString(PyExc_ValueError, "Invalid island raster dimensions");
        return NULL;
    }
    uint32_t count;
    int32_t *data = copy_buffer(raster, sizeof(*data), 67108864, &count);
    if (!data)
        return NULL;
    if (count != size * size) {
        free(data);
        PyErr_SetString(PyExc_ValueError, "Island raster size mismatch");
        return NULL;
    }
    op_uv_bounds_t *bounds = calloc((size_t)islands + 1, sizeof(*bounds));
    if (!bounds) {
        free(data);
        return PyErr_NoMemory();
    }
    op_status_t status;
    Py_BEGIN_ALLOW_THREADS status = op_uv_island_bounds(data, size, islands, bounds);
    Py_END_ALLOW_THREADS free(data);
    PyObject *result = NULL;
    if (!fail(status))
        result =
            PyBytes_FromStringAndSize((const char *)bounds, (Py_ssize_t)islands * sizeof(*bounds));
    free(bounds);
    return result;
}
