#ifndef OP_IR_H
#define OP_IR_H
#include "op_api.h"
#ifdef __cplusplus
extern "C" {
#endif
/* Typed portable shader IR. Inputs always refer to earlier nodes. No Python,
 * Blender, shader-language expressions or GPU pointers enter this interface. */
enum { OP_IR_FLOAT = 1, OP_IR_VECTOR = 2, OP_IR_COLOR = 3 };
enum {
    OP_IR_CONSTANT = 1,
    OP_IR_PARAMETER,
    OP_IR_CONVERT,
    OP_IR_MATH,
    OP_IR_VECTOR_MATH,
    OP_IR_COMBINE,
    OP_IR_COMPONENT,
    OP_IR_IMAGE,
    OP_IR_IMAGE_COLOR,
    OP_IR_GEOMETRY,
    OP_IR_OBJECT,
    OP_IR_NORMAL_MAP,
    OP_IR_PROCEDURAL,
    OP_IR_FETCH,
    OP_IR_UV,
    /* COLOR result (premultiplied RGB, coverage). Inputs: backdrop VECTOR,
     * backdrop alpha FLOAT, straight source VECTOR, source alpha FLOAT,
     * coverage FLOAT. operation names the blend mode; aux[0] selects the
     * opaque-backdrop specialization (backdrop alpha must then be 1). */
    OP_IR_BLEND,
    /* VECTOR result; input 0 is source RGB, followed by operation-specific
     * typed parameters and FLOAT strength/enabled. Unused slots reference any
     * earlier node. Alpha remains the caller's unchanged input. Parameter contracts are listed in
     * effect_specs in effects.inc. */
    OP_IR_EFFECT,
    /* FLOAT previous, value, black, white, invert, strength; operation is
     * NORMAL/MULTIPLY/MAX/MIN/ADD. aux[0] identifies the first mask entry. */
    OP_IR_MASK_FOLD
};
typedef struct op_ir_node {
    uint32_t opcode, type, inputs[8], aux[4];
    double value[4];
    char operation[64];
} op_ir_node_t;
typedef struct op_ir_target {
    uint32_t color, alpha, layout;
} op_ir_target_t;
/* layout: 0 RGBA, 1 packed scalar+coverage RG, 2 scalar replicated to RGB.
 * Returned source is owned by the caller; free with op_ir_free. Failure never
 * returns a partially compiled program. Bounded to 262144 nodes and 64 MiB. */
op_status_t op_ir_compile(const op_ir_node_t *nodes, uint32_t count, const op_ir_target_t *targets,
                          uint32_t target_count, char **source);
/* Whether a single-target program's output is a per-texel, per-component affine
 * function out[c] = a[c] * backdrop[source[c]] + b[c] of the FETCH sampler
 * `backdrop`, with a and b independent of it. Only linear conversions, component
 * access and opaque-backdrop blends in affine modes may lie on a backdrop path.
 * source[c] is OP_IR_NO_COMPONENT when out[c] does not read the backdrop.
 * *affine = 0 is a normal answer; failure statuses mean malformed IR. */
#define OP_IR_NO_COMPONENT 0xffu
op_status_t op_ir_backdrop_affine(const op_ir_node_t *nodes, uint32_t count,
                                  const op_ir_target_t *targets, uint32_t target_count,
                                  uint32_t backdrop, uint32_t *affine, uint8_t source[4]);
const char *op_shader_source(const char *name); /* static immutable UTF-8, NULL if unknown */
op_status_t op_ir_field(const char *kind, char **source);
void op_ir_free(void *source);
#ifdef __cplusplus
}
#endif
#endif
