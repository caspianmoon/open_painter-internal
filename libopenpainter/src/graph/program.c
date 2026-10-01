#include "openpainter/op_program.h"
#include <math.h>
#include "../core/serial.h"
#include <stdlib.h>
#include <string.h>
static op_serial_t program_serial = 1;
struct op_program {
    op_ir_node_t *nodes;
    op_ir_target_t *targets;
    char *source;
    float *uniforms;
    uint32_t parameters, objects, padded, total;
    uint64_t revision, identity;
};
op_status_t op_program_create(const op_ir_node_t *nodes, uint32_t count,
                              const op_ir_target_t *targets, uint32_t target_count,
                              uint32_t parameters, uint32_t objects, op_program_t **out) {
    if (!out)
        return OP_INVALID_ARGUMENT;
    *out = NULL;
    if (parameters > 1048576 || objects > 65536 || !nodes || !count || count > 262144 || !targets ||
        !target_count || target_count > 8)
        return OP_INVALID_ARGUMENT;
    for (uint32_t i = 0; i < count; i++)
        if ((nodes[i].opcode == OP_IR_PARAMETER && nodes[i].aux[0] >= parameters) ||
            (nodes[i].opcode == OP_IR_OBJECT && nodes[i].aux[0] >= objects))
            return OP_VALIDATION_FAILED;
    op_program_t *p = calloc(1, sizeof(*p));
    if (!p)
        return OP_RESOURCE_EXHAUSTED;
    op_status_t status = op_ir_compile(nodes, count, targets, target_count, &p->source);
    if (status) {
        op_program_destroy(p);
        return status;
    }
    p->nodes = malloc((size_t)count * sizeof(*nodes));
    p->targets = malloc(target_count * sizeof(*targets));
    p->parameters = parameters;
    p->objects = objects;
    p->padded = parameters ? ((parameters + 3) / 4) * 4 : 4;
    p->total = parameters || objects ? p->padded + objects * 16 : 0;
    p->uniforms = calloc((size_t)p->total + 1, sizeof(float));
    if (!p->nodes || !p->targets || !p->uniforms) {
        op_program_destroy(p);
        return OP_RESOURCE_EXHAUSTED;
    }
    memcpy(p->nodes, nodes, (size_t)count * sizeof(*nodes));
    memcpy(p->targets, targets, target_count * sizeof(*targets));
    p->identity = op_serial_next(&program_serial);
    *out = p;
    return OP_OK;
}
void op_program_destroy(op_program_t *p) {
    if (p) {
        free(p->nodes);
        free(p->targets);
        op_ir_free(p->source);
        free(p->uniforms);
        free(p);
    }
}
const char *op_program_source(const op_program_t *p) { return p ? p->source : NULL; }
op_status_t op_program_update(op_program_t *p, const float *values, uint32_t count,
                              const float *matrices, uint32_t matrix_floats, uint64_t *revision) {
    if (!p || !revision || count != p->parameters || matrix_floats != p->objects * 16 ||
        (count && !values) || (matrix_floats && !matrices))
        return OP_INVALID_ARGUMENT;
    for (uint32_t i = 0; i < count; i++)
        if (!isfinite(values[i]))
            return OP_VALIDATION_FAILED;
    for (uint32_t i = 0; i < matrix_floats; i++)
        if (!isfinite(matrices[i]))
            return OP_VALIDATION_FAILED;
    int changed =
        (count && memcmp(p->uniforms, values, count * sizeof(float))) ||
        (matrix_floats && memcmp(p->uniforms + p->padded, matrices, matrix_floats * sizeof(float)));
    if (changed) {
        if (p->revision == UINT64_MAX)
            return OP_RESOURCE_EXHAUSTED;
        if (count)
            memcpy(p->uniforms, values, count * sizeof(float));
        if (matrix_floats)
            memcpy(p->uniforms + p->padded, matrices, matrix_floats * sizeof(float));
        p->revision++;
    }
    *revision = p->revision;
    return OP_OK;
}
const float *op_program_uniforms(const op_program_t *p, uint32_t *count) {
    if (!p || !count)
        return NULL;
    *count = p->total;
    return p->uniforms;
}

uint64_t op_program_identity(const op_program_t *p) { return p ? p->identity : 0; }
