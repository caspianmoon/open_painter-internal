/* Stock Blender host-device backend. Only documented Python GPU methods are
 * called; no bpy/RNA access, foreign device, private memory or external handle.
 * The caller owns the graphics thread. GPU API calls retain submitted resources
 * under Blender's queue contract; all borrowed Python references outlive submit. */
static int invoke(PyObject *object, const char *name, PyObject *args) {
    PyObject *method = PyObject_GetAttrString(object, name);
    if (!method)
        return -1;
    PyObject *result = PyObject_CallObject(method, args);
    Py_DECREF(method);
    if (!result)
        return -1;
    Py_DECREF(result);
    return 0;
}
static int bind_value(PyObject *shader, const char *method, PyObject *name, PyObject *value) {
    PyObject *args = PyTuple_Pack(2, name, value);
    if (!args)
        return -1;
    int result = invoke(shader, method, args);
    Py_DECREF(args);
    return result;
}
static PyObject *context_method(PyObject *context, const char *name, PyObject *args) {
    /* Blender's stack context omits instance tp_getattro. Generic lookup
     * performs CPython's normal type preparation and descriptor binding. */
    PyObject *key = PyUnicode_FromString(name);
    if (!key)
        return NULL;
    PyObject *method = PyObject_GenericGetAttr(context, key);
    Py_DECREF(key);
    if (!method)
        return NULL;
    PyObject *result = PyObject_CallObject(method, args);
    Py_DECREF(method);
    return result;
}
static PyObject *gpu_draw(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *shader, *targets, *samplers, *ubo, *ints, *uniforms, *batch, *region;
    unsigned int size;
    int block;
    if (!PyArg_ParseTuple(args, "OOIOOOOpOO", &shader, &targets, &size, &samplers, &ubo, &ints,
                          &uniforms, &block, &batch, &region))
        return NULL;
    if (!size || size > 32768) {
        PyErr_SetString(PyExc_ValueError, "Invalid GPU pass dimensions");
        return NULL;
    }
    Py_ssize_t target_count = PySequence_Size(targets), sampler_count = PySequence_Size(samplers);
    if (target_count < 0 || sampler_count < 0)
        return NULL;
    if (!target_count || target_count > 8 || sampler_count > 256) {
        PyErr_SetString(PyExc_ValueError, "Invalid GPU pass binding count");
        return NULL;
    }
    /* Reject feedback loops before creating a framebuffer or changing GPU state. */
    for (Py_ssize_t i = 0; i < target_count; i++) {
        PyObject *slot = PySequence_GetItem(targets, i);
        if (!slot)
            return NULL;
        PyObject *texture = PyDict_Check(slot) ? PyDict_GetItemString(slot, "texture") : slot;
        if (!texture) {
            Py_DECREF(slot);
            PyErr_SetString(PyExc_ValueError, "Missing target texture");
            return NULL;
        }
        for (Py_ssize_t j = 0; j < sampler_count; j++) {
            PyObject *pair = PySequence_GetItem(samplers, j);
            if (!pair) {
                Py_DECREF(slot);
                return NULL;
            }
            PyObject *source = PySequence_GetItem(pair, 1);
            Py_DECREF(pair);
            if (!source) {
                Py_DECREF(slot);
                return NULL;
            }
            int same = source == texture;
            Py_DECREF(source);
            if (same) {
                Py_DECREF(slot);
                PyErr_SetString(PyExc_ValueError, "A GPU pass cannot sample its render target");
                return NULL;
            }
        }
        Py_DECREF(slot);
    }
    PyObject *gpu = NULL, *types = NULL, *state = NULL, *constructor = NULL, *empty = NULL,
             *keywords = NULL;
    PyObject *framebuffer = NULL, *context = NULL, *viewport = NULL, *name = NULL, *value = NULL,
             *entered = NULL;
    int failure = 1, bound = 0;
    gpu = PyImport_ImportModule("gpu");
    if (!gpu)
        goto done;
    types = PyObject_GetAttrString(gpu, "types");
    state = PyObject_GetAttrString(gpu, "state");
    if (!types || !state)
        goto done;
    constructor = PyObject_GetAttrString(types, "GPUFrameBuffer");
    empty = PyTuple_New(0);
    keywords = PyDict_New();
    if (!constructor || !empty || !keywords ||
        PyDict_SetItemString(keywords, "color_slots", targets) < 0)
        goto done;
    framebuffer = PyObject_Call(constructor, empty, keywords);
    if (!framebuffer)
        goto done;
    context = PyObject_CallMethod(framebuffer, "bind", NULL);
    if (!context)
        goto done;
    entered = context_method(context, "__enter__", empty);
    if (!entered)
        goto done;
    bound = 1;
    viewport =
        region == Py_None ? Py_BuildValue("(IIII)", 0, 0, size, size) : PySequence_Tuple(region);
    if (!viewport)
        goto done;
    if (PyTuple_Size(viewport) != 4) {
        PyErr_SetString(PyExc_ValueError, "Invalid GPU viewport");
        goto done;
    }
    if (invoke(state, "viewport_set", viewport) < 0 || invoke(state, "scissor_set", viewport) < 0 ||
        invoke(shader, "bind", empty) < 0)
        goto done;
    name = PyUnicode_FromString("op_inv_size");
    if (!name)
        goto done;
    int has = PySequence_Contains(uniforms, name);
    if (has < 0)
        goto done;
    if (has) {
        value = Py_BuildValue("(dd)", 1.0 / size, 1.0 / size);
        if (!value || bind_value(shader, "uniform_float", name, value) < 0)
            goto done;
        Py_CLEAR(value);
    }
    Py_CLEAR(name);
    if (block && ubo != Py_None) {
        name = PyUnicode_FromString("op_params");
        if (!name || bind_value(shader, "uniform_block", name, ubo) < 0)
            goto done;
        Py_CLEAR(name);
    }
    if (ints != Py_None) {
        if (!PyDict_Check(ints)) {
            PyErr_SetString(PyExc_TypeError, "GPU integer uniforms must be a dictionary");
            goto done;
        }
        Py_ssize_t pos = 0;
        PyObject *key, *item;
        while (PyDict_Next(ints, &pos, &key, &item)) {
            has = PySequence_Contains(uniforms, key);
            if (has < 0)
                goto done;
            if (has && bind_value(shader, "uniform_int", key, item) < 0)
                goto done;
        }
    }
    for (Py_ssize_t j = 0; j < sampler_count; j++) {
        PyObject *pair = PySequence_GetItem(samplers, j);
        if (!pair)
            goto done;
        name = PySequence_GetItem(pair, 0);
        value = PySequence_GetItem(pair, 1);
        Py_DECREF(pair);
        if (!name || !value)
            goto done;
        has = PySequence_Contains(uniforms, name);
        if (has < 0)
            goto done;
        if (has && bind_value(shader, "uniform_sampler", name, value) < 0)
            goto done;
        Py_CLEAR(name);
        Py_CLEAR(value);
    }
    value = PyTuple_Pack(1, shader);
    if (!value || invoke(batch, "draw", value) < 0)
        goto done;
    failure = 0;
done:
    if (bound) {
        PyObject *error_type = NULL, *error_value = NULL, *traceback = NULL;
        if (failure)
            PyErr_Fetch(&error_type, &error_value, &traceback);
        PyObject *exit_args = PyTuple_Pack(3, Py_None, Py_None, Py_None);
        PyObject *result = exit_args ? context_method(context, "__exit__", exit_args) : NULL;
        Py_XDECREF(exit_args);
        if (!result) {
            if (failure)
                PyErr_Clear();
            else
                failure = 1;
        }
        Py_XDECREF(result);
        if (error_type)
            PyErr_Restore(error_type, error_value, traceback);
    }
    Py_XDECREF(name);
    Py_XDECREF(value);
    Py_XDECREF(viewport);
    Py_XDECREF(entered);
    Py_XDECREF(context);
    Py_XDECREF(framebuffer);
    Py_XDECREF(keywords);
    Py_XDECREF(empty);
    Py_XDECREF(constructor);
    Py_XDECREF(state);
    Py_XDECREF(types);
    Py_XDECREF(gpu);
    if (failure)
        return NULL;
    Py_RETURN_NONE;
}
