#ifndef OP_STACK_H
#define OP_STACK_H
#include "op_api.h"
#ifdef __cplusplus
extern "C" {
#endif
#define OP_STACK_DEFAULT UINT32_MAX
#define OP_STACK_TRANSPARENT (UINT32_MAX - 1u)
#define OP_STACK_NONE (UINT32_MAX - 2u)
#define OP_STACK_FOLDER 1u
#define OP_STACK_PASSTHROUGH 2u
/* Preorder recipe snapshot: parent is an earlier folder or OP_STACK_DEFAULT.
 * Sibling array order is compositing order. Channels remain independent. */
typedef struct op_stack_node {
    uint32_t parent, channel, flags;
} op_stack_node_t;
typedef struct op_stack_position {
    uint32_t backdrop, grouped;
} op_stack_position_t;
op_status_t op_stack_plan(const op_stack_node_t *nodes, uint32_t count,
                          op_stack_position_t *positions, uint32_t *order, uint32_t offsets[9],
                          uint32_t finals[8]);
#ifdef __cplusplus
}
#endif
#endif
