#include "openpainter/op_mask.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    op_mask_node_t nodes[] = {{OP_MASK_NONE, 1},
                              {OP_MASK_NONE, 3},
                              {1, 1},
                              {1, 3},
                              {3, 5},
                              {1, 1},
                              {OP_MASK_NONE, 9},
                              {OP_MASK_NONE, 2},
                              {7, 1},
                              {OP_MASK_NONE, 5},
                              {OP_MASK_NONE, 3}};
    op_mask_plan_t *p = NULL;
    assert(op_mask_plan_build(nodes, 11, 0, 11, 0, 1, &p) == OP_OK);
    uint32_t walk[] = {0, 1, 2, 3, 4, 5, 9, 10};
    op_mask_step_t steps[] = {{0, OP_MASK_INITIAL, OP_MASK_NONE, OP_MASK_NONE, 1},
                              {2, OP_MASK_NONE, OP_MASK_NONE, 2, 3},
                              {4, OP_MASK_NONE, OP_MASK_NONE, 4, 5},
                              {3, 1, 2, 2, 5},
                              {5, 3, OP_MASK_NONE, 2, 6},
                              {1, 0, 4, OP_MASK_NONE, 6},
                              {9, 5, OP_MASK_NONE, OP_MASK_NONE, 7},
                              {10, 6, OP_MASK_ONE, OP_MASK_NONE, 8}};
    assert(p->result == 7 && p->step_count == 8 && p->walk_count == 8);
    assert(!memcmp(p->steps, steps, sizeof(steps)) && !memcmp(p->walk, walk, sizeof(walk)));
    op_mask_plan_free(p);
    assert(op_mask_plan_build(nodes, 11, 0, 11, 1, 0, &p) == OP_OK);
    assert(p->step_count == 6 && p->walk_count == 6 && p->steps[2].value == OP_MASK_ONE);
    op_mask_plan_free(p);
    /* A published prefix can end inside nested groups; both groups close. */
    assert(op_mask_plan_build(nodes, 11, 1, 5, 0, 0, &p) == OP_OK);
    assert(p->step_count == 4 && p->steps[3].index == 1 && p->steps[3].value == 2);
    op_mask_plan_free(p);
    /* Starting inside a group makes the selected run independent. */
    assert(op_mask_plan_build(nodes, 11, 4, 6, 0, 1, &p) == OP_OK);
    assert(p->step_count == 2 && p->steps[0].previous == OP_MASK_INITIAL);
    assert(p->steps[0].prefix_begin == OP_MASK_NONE);
    op_mask_plan_free(p);
    assert(op_mask_plan_build(NULL, 0, 0, 0, 0, 1, &p) == OP_OK);
    assert(p->step_count == 0 && p->result == OP_MASK_INITIAL);
    op_mask_plan_free(p);
    assert(op_mask_plan_build(NULL, 0, 0, 0, 0, 0, &p) == OP_OK);
    assert(p->result == OP_MASK_NONE);
    op_mask_plan_free(p);
    assert(op_mask_plan_build(nodes, 11, 12, 12, 0, 0, &p) == OP_INVALID_ARGUMENT && !p);
    assert(op_mask_plan_build(nodes, 11, 2, 1, 0, 0, &p) == OP_INVALID_ARGUMENT && !p);
    assert(op_mask_plan_build(nodes, 11, 0, 11, 2, 0, &p) == OP_INVALID_ARGUMENT && !p);
    assert(op_mask_plan_build(nodes, 11, 0, 11, 0, 2, &p) == OP_INVALID_ARGUMENT && !p);
    assert(op_mask_plan_build(NULL, 1, 0, 0, 0, 0, &p) == OP_INVALID_ARGUMENT && !p);
    assert(op_mask_plan_build(nodes, 65537, 0, 0, 0, 0, &p) == OP_INVALID_ARGUMENT && !p);
    assert(op_mask_plan_build(nodes, 11, 0, 11, 0, 0, NULL) == OP_INVALID_ARGUMENT);
    for (uint32_t flag = 0; flag < 32; flag++) {
        op_mask_node_t bad = {OP_MASK_NONE, flag};
        uint32_t kind = flag & ~1u;
        op_status_t status = op_mask_plan_build(&bad, 1, 0, 1, 0, 0, &p);
        assert(status ==
               ((flag < 16 && (!kind || !(kind & (kind - 1)))) ? OP_OK : OP_INVALID_ARGUMENT));
        op_mask_plan_free(p);
    }
    /* Reject self/forward/non-group/reopened parents, including unused tail. */
    uint32_t parents[] = {2, 10, 0, OP_MASK_NONE};
    for (unsigned i = 0; i < 4; i++) {
        op_mask_node_t saved = nodes[2];
        nodes[2].parent = parents[i];
        assert(op_mask_plan_build(nodes, 11, 0, 1, 0, 0, &p) == OP_INVALID_ARGUMENT && !p);
        nodes[2] = saved;
    }
    /* Maximum-depth hierarchy uses heap frames, never the C/Python call stack. */
    uint32_t n = 65536;
    op_mask_node_t *deep = calloc(n, sizeof(*deep));
    assert(deep);
    for (uint32_t i = 0; i < n; i++)
        deep[i] = (op_mask_node_t){i ? i - 1 : OP_MASK_NONE, i == n - 1 ? 1u : 3u};
    assert(op_mask_plan_build(deep, n, 0, n, 0, 0, &p) == OP_OK);
    assert(p->step_count == n && p->walk_count == n && p->result == n - 1);
    assert(p->steps[0].index == n - 1 && p->steps[n - 1].index == 0);
    memset(deep, 0, n * sizeof(*deep));
    assert(p->walk[n - 1] == n - 1);
    free(deep);
    op_mask_plan_free(p);
    op_mask_plan_free(NULL);
    return 0;
}
