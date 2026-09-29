#ifndef OP_MASK_H
#define OP_MASK_H
#include "op_api.h"
#ifdef __cplusplus
extern "C" {
#endif
#define OP_MASK_NONE UINT32_MAX
#define OP_MASK_INITIAL (UINT32_MAX - 1u)
#define OP_MASK_ONE (UINT32_MAX - 2u)
enum { OP_MASK_ENABLED = 1u, OP_MASK_GROUP = 2u, OP_MASK_FILTER = 4u, OP_MASK_ANCHOR = 8u };
/* Preorder snapshot, at most 65536 entries. Each parent is an earlier GROUP
 * with a contiguous member run, or NONE. Source kinds are mutually exclusive. */
typedef struct op_mask_node {
    uint32_t parent, flags;
} op_mask_node_t;
/* Steps are postorder folds. previous/value reference earlier step results or
 * NONE (empty/source), INITIAL (caller's continuation), ONE (empty group).
 * For source steps, [prefix_begin,prefix_end) indexes walk. NONE prefix_begin
 * means prepend the caller's existing prefix, then walk[0:prefix_end]. Nested
 * groups use their isolated member prefix and never include the outer stack. */
typedef struct op_mask_step {
    uint32_t index, previous, value, prefix_begin, prefix_end;
} op_mask_step_t;
typedef struct op_mask_plan {
    uint32_t step_count, walk_count, result;
    op_mask_step_t *steps;
    uint32_t *walk;
} op_mask_plan_t;
/* Borrow inputs only during the call. Own every output, no partial plan on
 * failure. Evaluate [start,stop), clipping groups at stop. A range beginning
 * inside a group is an isolated run, matching filter-prefix evaluation.
 * pre_filter excludes FILTER entries; disabled groups suppress all members;
 * ANCHOR entries never contribute. initial preserves a continuation result. */
op_status_t op_mask_plan_build(const op_mask_node_t *nodes, uint32_t count, uint32_t start,
                               uint32_t stop, uint32_t pre_filter, uint32_t initial,
                               op_mask_plan_t **out);
void op_mask_plan_free(op_mask_plan_t *plan);
#ifdef __cplusplus
}
#endif
#endif
