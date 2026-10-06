/* CPython limited ABI 3.11+: owns all submission buffers across GIL release.
 * No Blender headers or private host layouts. C errors become exceptions. */
#define Py_LIMITED_API 0x030b0000
#define PY_SSIZE_T_CLEAN
#include "openpainter/op_channels.h"
#include "openpainter/op_geometry.h"
#include "openpainter/op_graph.h"
#include "openpainter/op_ir.h"
#include "openpainter/op_mask.h"
#include "openpainter/op_paint.h"
#include "openpainter/op_program.h"
#include "openpainter/op_revision.h"
#include "openpainter/op_topology.h"
#include <Python.h>
#include <stdlib.h>
#include <string.h>

static int fail(op_status_t status) {
    if (status == OP_OK)
        return 0;
    PyErr_Format(PyExc_RuntimeError, "Native command failed with status %d", (int)status);
    return -1;
}
/* A pinned buffer may still be mutable on another Python thread. Copy before
 * releasing the GIL; never retain a transient NumPy/RNA submission pointer. */
static void *copy_buffer(PyObject *obj, Py_ssize_t item, uint32_t maximum, uint32_t *count) {
    Py_buffer view;
    if (PyObject_GetBuffer(obj, &view, PyBUF_CONTIG_RO) < 0)
        return NULL;
    if (view.len < 0 || view.len % item || view.len / item > maximum) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "Invalid native buffer length");
        return NULL;
    }
    *count = (uint32_t)(view.len / item);
    void *copy = malloc(view.len ? (size_t)view.len : 1);
    if (!copy) {
        PyBuffer_Release(&view);
        PyErr_NoMemory();
        return NULL;
    }
    if (view.len)
        memcpy(copy, view.buf, (size_t)view.len);
    PyBuffer_Release(&view);
    return copy;
}
static PyObject *abi(PyObject *self, PyObject *ignored) {
    (void)self;
    (void)ignored;
    return PyLong_FromUnsignedLong(op_get_abi_version());
}
static PyObject *compile_ir(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *nodes, *targets;
    uint32_t n, t;
    char *source = NULL;
    if (!PyArg_ParseTuple(args, "OO", &nodes, &targets))
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
    Py_BEGIN_ALLOW_THREADS status = op_ir_compile(data, n, out, t, &source);
    Py_END_ALLOW_THREADS free(data);
    free(out);
    if (fail(status))
        return NULL;
    PyObject *result = PyUnicode_FromString(source);
    op_ir_free(source);
    return result;
}
static PyObject *brush_uniforms(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *samples, *settings;
    uint32_t n, c;
    if (!PyArg_ParseTuple(args, "OO", &samples, &settings))
        return NULL;
    float *data = copy_buffer(samples, 13 * sizeof(float), 33554432, &n);
    if (!data)
        return NULL;
    op_brush_settings_t *config = copy_buffer(settings, sizeof(*config), 1, &c);
    if (!config) {
        free(data);
        return NULL;
    }
    if (c != 1) {
        free(data);
        free(config);
        PyErr_SetString(PyExc_ValueError, "Expected one brush settings record");
        return NULL;
    }
    PyObject *out = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)n * 16 * sizeof(float));
    if (!out) {
        free(data);
        free(config);
        return NULL;
    }
    float *result = (float *)PyBytes_AsString(out);
    op_status_t status;
    Py_BEGIN_ALLOW_THREADS status = op_brush_uniforms(data, n, config, result);
    Py_END_ALLOW_THREADS free(data);
    free(config);
    if (fail(status)) {
        Py_DECREF(out);
        return NULL;
    }
    return out;
}
static PyObject *rasterize(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *uv, *clips = Py_None;
    unsigned int size;
    int unique;
    uint32_t count, clip_count = 0;
    if (!PyArg_ParseTuple(args, "OIp|O", &uv, &size, &unique, &clips))
        return NULL;
    float *data = copy_buffer(uv, 6 * sizeof(float), 8388608, &count);
    if (!data)
        return NULL;
    float *clip = NULL;
    if (clips != Py_None) {
        clip = copy_buffer(clips, 4 * sizeof(float), 8388608, &clip_count);
        if (!clip) {
            free(data);
            return NULL;
        }
    }
    if (clip && clip_count != count) {
        free(clip);
        free(data);
        PyErr_SetString(PyExc_ValueError, "UV clip count mismatch");
        return NULL;
    }
    op_uv_sample_t *samples = NULL;
    uint64_t written = 0;
    op_status_t status;
    Py_BEGIN_ALLOW_THREADS status =
        op_uv_rasterize(data, count, size, unique, clip, &samples, &written);
    Py_END_ALLOW_THREADS free(data);
    free(clip);
    if (fail(status))
        return NULL;
    PyObject *out =
        PyBytes_FromStringAndSize((char *)samples, (Py_ssize_t)(written * sizeof(*samples)));
    op_geometry_free(samples);
    return out;
}
static PyObject *distances(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *points, *corners;
    uint32_t n, t;
    if (!PyArg_ParseTuple(args, "OO", &points, &corners))
        return NULL;
    double *p = copy_buffer(points, 3 * sizeof(double), 33554432, &n);
    if (!p)
        return NULL;
    double *c = copy_buffer(corners, 9 * sizeof(double), 33554432, &t);
    if (!c) {
        free(p);
        return NULL;
    }
    if (n != t) {
        free(p);
        free(c);
        PyErr_SetString(PyExc_ValueError, "Triangle/point count mismatch");
        return NULL;
    }
    PyObject *out = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)n * sizeof(double));
    if (!out) {
        free(p);
        free(c);
        return NULL;
    }
    double *result = (double *)PyBytes_AsString(out);
    op_status_t status;
    Py_BEGIN_ALLOW_THREADS status = op_triangle_distances(p, c, n, result);
    Py_END_ALLOW_THREADS free(p);
    free(c);
    if (fail(status)) {
        Py_DECREF(out);
        return NULL;
    }
    return out;
}
static PyObject *field_source(PyObject *self, PyObject *arg) {
    (void)self;
    const char *kind = PyUnicode_AsUTF8AndSize(arg, NULL);
    if (!kind)
        return NULL;
    char *source = NULL;
    if (fail(op_ir_field(kind, &source)))
        return NULL;
    PyObject *out = PyUnicode_FromString(source);
    op_ir_free(source);
    return out;
}
static PyObject *shader_source(PyObject *self, PyObject *arg) {
    (void)self;
    const char *name = PyUnicode_AsUTF8AndSize(arg, NULL);
    if (!name)
        return NULL;
    const char *source = op_shader_source(name);
    if (!source) {
        PyErr_SetString(PyExc_ValueError, "Unknown native shader");
        return NULL;
    }
    return PyUnicode_FromString(source);
}

typedef struct graph_owner {
    op_graph_t *graph;
    uint32_t count;
    uint64_t *stamps;
    int executing;
} graph_owner;
static graph_owner *graph_get(PyObject *capsule) {
    graph_owner *g = PyCapsule_GetPointer(capsule, "openpainter.graph.v5");
    if (g && (!g->graph || g->executing)) {
        PyErr_SetString(PyExc_RuntimeError, "Closed or executing native graph");
        return NULL;
    }
    return g;
}
static void graph_destroy(PyObject *capsule) {
    graph_owner *g = PyCapsule_GetPointer(capsule, "openpainter.graph.v5");
    if (g) {
        op_graph_destroy(g->graph);
        free(g->stamps);
        free(g);
    }
}
static PyObject *graph_create(PyObject *self, PyObject *ignored) {
    (void)self;
    (void)ignored;
    graph_owner *g = calloc(1, sizeof(*g));
    if (!g)
        return PyErr_NoMemory();
    if (fail(op_graph_create(&g->graph))) {
        free(g);
        return NULL;
    }
    PyObject *result = PyCapsule_New(g, "openpainter.graph.v5", graph_destroy);
    if (!result) {
        op_graph_destroy(g->graph);
        free(g);
    }
    return result;
}
static PyObject *graph_close(PyObject *self, PyObject *capsule) {
    (void)self;
    graph_owner *g = PyCapsule_GetPointer(capsule, "openpainter.graph.v5");
    if (!g)
        return NULL;
    if (g->executing) {
        PyErr_SetString(PyExc_RuntimeError, "Cannot close an executing native graph");
        return NULL;
    }
    op_graph_destroy(g->graph);
    g->graph = NULL;
    free(g->stamps);
    g->stamps = NULL;
    Py_RETURN_NONE;
}
static PyObject *graph_replace(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *offsets, *edges;
    uint32_t n, e;
    if (!PyArg_ParseTuple(args, "OOO", &capsule, &offsets, &edges))
        return NULL;
    graph_owner *g = graph_get(capsule);
    if (!g)
        return NULL;
    uint32_t *o = copy_buffer(offsets, 4, 65537, &n);
    if (!o)
        return NULL;
    uint32_t *d = copy_buffer(edges, 4, 1048576, &e);
    if (!d) {
        free(o);
        return NULL;
    }
    if (!n) {
        free(o);
        free(d);
        PyErr_SetString(PyExc_ValueError, "Missing graph offsets");
        return NULL;
    }
    uint64_t *stamps = calloc(n, sizeof(uint64_t));
    if (!stamps) {
        free(o);
        free(d);
        return PyErr_NoMemory();
    }
    op_status_t status = op_graph_replace(g->graph, n - 1, o, d, e);
    free(o);
    free(d);
    if (fail(status)) {
        free(stamps);
        return NULL;
    }
    free(g->stamps);
    g->stamps = stamps;
    g->count = n - 1;
    Py_RETURN_NONE;
}
static PyObject *graph_evaluate(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *patches, *resident;
    uint32_t n, r, used = 0;
    if (!PyArg_ParseTuple(args, "OOO", &capsule, &patches, &resident))
        return NULL;
    graph_owner *g = graph_get(capsule);
    if (!g)
        return NULL;
    op_graph_patch_t *p = copy_buffer(patches, sizeof(*p), 65536, &n);
    if (!p)
        return NULL;
    uint64_t *v = copy_buffer(resident, 8, 65536, &r);
    if (!v) {
        free(p);
        return NULL;
    }
    if (r != g->count) {
        free(p);
        free(v);
        PyErr_SetString(PyExc_ValueError, "Graph resident count mismatch");
        return NULL;
    }
    uint32_t *plan = malloc(((size_t)r + 1) * 4);
    if (!plan) {
        free(p);
        free(v);
        return PyErr_NoMemory();
    }
    op_status_t status = op_graph_apply_patch(g->graph, p, n);
    if (!status)
        status = op_graph_evaluate_current(g->graph, v, g->stamps, plan, r, &used);
    free(p);
    free(v);
    if (fail(status)) {
        free(plan);
        return NULL;
    }
    PyObject *a = PyBytes_FromStringAndSize((char *)g->stamps, (Py_ssize_t)r * 8),
             *b = PyBytes_FromStringAndSize((char *)plan, (Py_ssize_t)used * 4);
    free(plan);
    if (!a || !b) {
        Py_XDECREF(a);
        Py_XDECREF(b);
        return NULL;
    }
    PyObject *out = PyTuple_Pack(2, a, b);
    Py_DECREF(a);
    Py_DECREF(b);
    return out;
}
static PyObject *graph_plan(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *resident;
    unsigned int root;
    uint32_t n, used = 0;
    if (!PyArg_ParseTuple(args, "OIO", &capsule, &root, &resident))
        return NULL;
    graph_owner *g = graph_get(capsule);
    if (!g)
        return NULL;
    uint64_t *v = copy_buffer(resident, 8, 65536, &n);
    if (!v)
        return NULL;
    if (n != g->count) {
        free(v);
        PyErr_SetString(PyExc_ValueError, "Graph resident count mismatch");
        return NULL;
    }
    uint32_t *plan = malloc(((size_t)n + 1) * 4);
    if (!plan) {
        free(v);
        return PyErr_NoMemory();
    }
    op_status_t status = op_graph_plan_root(g->graph, root, v, g->stamps, plan, n, &used);
    free(v);
    if (fail(status)) {
        free(plan);
        return NULL;
    }
    PyObject *out = PyBytes_FromStringAndSize((char *)plan, (Py_ssize_t)used * 4);
    free(plan);
    return out;
}

#include "graph_execution.inc"

static PyObject *graph_checkpoint(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *order, *below, *current;
    uint32_t n, b, c, keep = UINT32_MAX;
    if (!PyArg_ParseTuple(args, "OOOO", &capsule, &order, &below, &current))
        return NULL;
    graph_owner *g = graph_get(capsule);
    if (!g)
        return NULL;
    uint32_t *o = copy_buffer(order, 4, 65536, &n);
    if (!o)
        return NULL;
    uint32_t *v = copy_buffer(below, 4, 65536, &b);
    if (!v) {
        free(o);
        return NULL;
    }
    uint64_t *stamps = copy_buffer(current, 8, 65536, &c);
    if (!stamps) {
        free(o);
        free(v);
        return NULL;
    }
    if (b != n || c != g->count) {
        free(o);
        free(v);
        free(stamps);
        PyErr_SetString(PyExc_ValueError, "Checkpoint buffer size mismatch");
        return NULL;
    }
    op_status_t status = op_graph_checkpoint(g->graph, o, v, n, stamps, g->stamps, &keep);
    free(o);
    free(v);
    free(stamps);
    if (fail(status))
        return NULL;
    return PyLong_FromUnsignedLong(keep);
}

static void program_destroy(PyObject *capsule) {
    op_program_t *p = PyCapsule_GetPointer(capsule, "openpainter.program");
    if (p)
        op_program_destroy(p);
}
static PyObject *program_create(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *nodes, *targets;
    unsigned int params, objects;
    uint32_t n, t;
    op_program_t *p = NULL;
    if (!PyArg_ParseTuple(args, "OOII", &nodes, &targets, &params, &objects))
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
    Py_BEGIN_ALLOW_THREADS status = op_program_create(data, n, out, t, params, objects, &p);
    Py_END_ALLOW_THREADS free(data);
    free(out);
    if (fail(status))
        return NULL;
    PyObject *capsule = PyCapsule_New(p, "openpainter.program", program_destroy);
    if (!capsule) {
        op_program_destroy(p);
        return NULL;
    }
    PyObject *source = PyUnicode_FromString(op_program_source(p));
    if (!source) {
        Py_DECREF(capsule);
        return NULL;
    }
    PyObject *result = PyTuple_Pack(2, source, capsule);
    Py_DECREF(source);
    Py_DECREF(capsule);
    return result;
}
static PyObject *program_update(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *values, *matrices;
    uint32_t n, m;
    uint64_t revision;
    if (!PyArg_ParseTuple(args, "OOO", &capsule, &values, &matrices))
        return NULL;
    op_program_t *p = PyCapsule_GetPointer(capsule, "openpainter.program");
    if (!p)
        return NULL;
    float *v = copy_buffer(values, 4, 1048576, &n);
    if (!v)
        return NULL;
    float *mat = copy_buffer(matrices, 4, 1048576, &m);
    if (!mat) {
        free(v);
        return NULL;
    }
    op_status_t status = op_program_update(p, v, n, mat, m, &revision);
    free(v);
    free(mat);
    if (fail(status))
        return NULL;
    return Py_BuildValue("(KK)", (unsigned long long)op_program_identity(p),
                         (unsigned long long)revision);
}
static PyObject *program_uniforms(PyObject *self, PyObject *capsule) {
    (void)self;
    op_program_t *p = PyCapsule_GetPointer(capsule, "openpainter.program");
    if (!p)
        return NULL;
    uint32_t count = 0;
    const float *v = op_program_uniforms(p, &count);
    return PyBytes_FromStringAndSize((const char *)v, (Py_ssize_t)count * 4);
}
#include "host_gpu.inc"
#include "revisions.inc"
#include "topology.inc"
#include "channels.inc"
#include "masks.inc"
#include "composition.inc"

static PyMethodDef methods[] = {
    {"mask_plan", mask_plan, METH_VARARGS, "Plan nested mask folds and isolated filter prefixes."},
    {"channels_create", channels_create, METH_NOARGS, "Create native channel state."},
    {"channels_close", channels_close, METH_O, "Close native channel state."},
    {"channels_reset", channels_reset, METH_O, "Invalidate accepted channel state."},
    {"channels_execute", channels_execute, METH_VARARGS, "Select and commit changed channels atomically."},
    {"revisions_create", revisions_create, METH_NOARGS,
     "Own an immutable native material revision stream."},
    {"revisions_close", revisions_close, METH_O, "Close a native revision stream."},
    {"revisions_references", revisions_references, METH_VARARGS,
     "Count native resource references."},
    {"revisions_publish", revisions_publish, METH_VARARGS,
     "Preflight, submit and commit or restore a complete material revision."},
    {"program_create", program_create, METH_VARARGS, "Create an owned IR and parameter bank."},
    {"program_update", program_update, METH_VARARGS, "Atomically update program inputs."},
    {"program_uniforms", program_uniforms, METH_O, "Copy the accepted uniform bank."},
    {"gpu_draw", gpu_draw, METH_VARARGS, "Submit a validated host GPU pass on the context thread."},
    {"graph_create", graph_create, METH_NOARGS, "Create an owned native dependency graph."},
    {"graph_close", graph_close, METH_O, "Close a graph generation."},
    {"graph_replace", graph_replace, METH_VARARGS, "Replace copied graph topology atomically."},
    {"graph_evaluate", graph_evaluate, METH_VARARGS,
     "Apply a revision batch and evaluate dependencies."},
    {"graph_checkpoint", graph_checkpoint, METH_VARARGS,
     "Select the lowest changed layer checkpoint."},
    {"graph_plan", graph_plan, METH_VARARGS, "Plan one requested output."},
    {"graph_compose", graph_compose, METH_VARARGS,
     "Copy layer composition edges and affine classes."},
    {"graph_runs", graph_runs, METH_VARARGS, "Plan affine suffix runs under one output."},
    {"graph_spine", graph_spine, METH_VARARGS, "Results an edit of one node composes over."},
    {"graph_checkpoints", graph_checkpoints, METH_VARARGS,
     "Keep the spine of a channel's lowest changed node."},
    {"graph_edit_runs", graph_edit_runs, METH_VARARGS,
     "Predict the affine runs an edit of one node would produce."},
    {"graph_mark_rendered", graph_mark_rendered, METH_VARARGS,
     "Record nodes the host rendered itself with their current local stamps."},
    {"ir_backdrop_affine", ir_backdrop_affine, METH_VARARGS,
     "Classify a program as affine in its backdrop."},
    {"graph_execute", graph_execute, METH_VARARGS,
     "Submit a root batch with dependency lifetime holds."},
    {"abi", abi, METH_NOARGS, "C ABI version."},
    {"compile_ir", compile_ir, METH_VARARGS, "Compile copied typed IR, releasing the GIL."},
    {"brush_uniforms", brush_uniforms, METH_VARARGS, "Build a complete dab batch."},
    {"uv_bounds", uv_bounds, METH_VARARGS, "Compute bounds for compact island IDs."},
    {"uv_topology", uv_topology, METH_VARARGS, "Build owned UV islands, borders and seam sides."},
    {"rasterize", rasterize, METH_VARARGS, "Rasterize immutable UV snapshots."},
    {"distances", distances, METH_VARARGS, "Point/triangle distance batch."},
    {"field_source", field_source, METH_O, "Native procedural displacement field."},
    {"shader_source", shader_source, METH_O, "Native GPU program source."},
    {NULL, NULL, 0, NULL}};
static struct PyModuleDef module = {PyModuleDef_HEAD_INIT,
                                    "_native",
                                    "Open Painter C17 batch binding",
                                    -1,
                                    methods,
                                    NULL,
                                    NULL,
                                    NULL,
                                    NULL};
PyMODINIT_FUNC PyInit__native(void) { return PyModule_Create(&module); }
