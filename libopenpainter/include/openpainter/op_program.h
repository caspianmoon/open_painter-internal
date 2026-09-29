#ifndef OP_PROGRAM_H
#define OP_PROGRAM_H
#include "op_ir.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct op_program op_program_t;
/* Immutable owned IR/source plus a revisioned, packed parameter bank. No host
 * object is retained. Parameter batches validate completely before mutation. */
op_status_t op_program_create(const op_ir_node_t *nodes, uint32_t count,
                              const op_ir_target_t *targets, uint32_t target_count,
                              uint32_t parameters, uint32_t objects, op_program_t **out);
void op_program_destroy(op_program_t *program);
uint64_t op_program_identity(const op_program_t *program);
const char *op_program_source(const op_program_t *program);
op_status_t op_program_update(op_program_t *program, const float *parameters, uint32_t count,
                              const float *matrices, uint32_t matrix_floats, uint64_t *revision);
const float *op_program_uniforms(const op_program_t *program, uint32_t *count);
#ifdef __cplusplus
}
#endif
#endif
