#include "openpainter/op_graph.h"
#include <stdio.h>
#define CHECK(x)                                                                                   \
    do {                                                                                           \
        if (!(x)) {                                                                                \
            printf("FAIL %d %s\n", __LINE__, #x);                                                  \
            return 1;                                                                              \
        }                                                                                          \
    } while (0)
int main(void) {
    op_graph_t *g;
    uint32_t offsets[] = {0, 0, 1, 2, 4}, edges[] = {0, 0, 1, 2}, plan[4], n;
    uint64_t own[] = {11, 22, 33, 44}, resident[4] = {0}, stamps[4], previous[4];
    CHECK(op_graph_create(&g) == OP_OK);
    CHECK(op_graph_replace(g, 4, offsets, edges, 4) == OP_OK);
    CHECK(op_graph_evaluate(g, own, resident, stamps, plan, 4, &n) == OP_OK && n == 4);
    for (int i = 0; i < 4; i++)
        resident[i] = previous[i] = stamps[i];
    CHECK(op_graph_evaluate(g, own, resident, stamps, plan, 4, &n) == OP_OK && n == 0);
    own[1]++;
    CHECK(op_graph_evaluate(g, own, resident, stamps, plan, 4, &n) == OP_OK && n == 2);
    CHECK(plan[0] == 1 && plan[1] == 3 && stamps[0] == previous[0] && stamps[2] == previous[2]);
    uint32_t bad[] = {1, 0, 1, 2};
    CHECK(op_graph_replace(g, 4, offsets, bad, 4) == OP_VALIDATION_FAILED);
    CHECK(op_graph_evaluate(g, own, resident, stamps, plan, 4, &n) == OP_OK && n == 2);
    CHECK(op_graph_evaluate(g, own, resident, stamps, plan, 3, &n) == OP_RESOURCE_EXHAUSTED);
    resident[0] = 0;
    CHECK(op_graph_evaluate(g, own, resident, stamps, plan, 4, &n) == OP_OK && n == 3);
    CHECK(op_graph_plan_root(g, 2, resident, stamps, plan, 4, &n) == OP_OK && n == 0);
    CHECK(op_graph_plan_root(g, 3, resident, stamps, plan, 4, &n) == OP_OK && n == 3);
    CHECK(plan[0] == 0 && plan[1] == 1 && plan[2] == 3);
    op_graph_patch_t patch[4];
    for (unsigned i = 0; i < 4; i++)
        patch[i] = (op_graph_patch_t){i, 0, own[i]};
    CHECK(op_graph_apply_patch(g, patch, 4) == OP_OK);
    CHECK(op_graph_evaluate_current(g, resident, previous, plan, 4, &n) == OP_OK);
    for (unsigned i = 0; i < 4; i++)
        CHECK(previous[i] == stamps[i]);
    patch[0].value++;
    patch[1].node = 4;
    CHECK(op_graph_apply_patch(g, patch, 2) == OP_VALIDATION_FAILED);
    CHECK(op_graph_evaluate_current(g, resident, previous, plan, 4, &n) == OP_OK);
    for (unsigned i = 0; i < 4; i++)
        CHECK(previous[i] == stamps[i]);
    uint32_t order[] = {0, 1, 3}, below[] = {UINT32_MAX, 0, 1}, keep;
    CHECK(op_graph_checkpoint(g, order, below, 3, previous, stamps, &keep) == OP_OK &&
          keep == UINT32_MAX);
    previous[3]++;
    CHECK(op_graph_checkpoint(g, order, below, 3, previous, stamps, &keep) == OP_OK && keep == 1);
    previous[1]++;
    CHECK(op_graph_checkpoint(g, order, below, 3, previous, stamps, &keep) == OP_OK && keep == 0);
    previous[0]++;
    CHECK(op_graph_checkpoint(g, order, below, 3, previous, stamps, &keep) == OP_OK &&
          keep == UINT32_MAX);
    below[2] = 4;
    CHECK(op_graph_checkpoint(g, order, below, 3, previous, stamps, &keep) == OP_VALIDATION_FAILED);
    op_graph_destroy(g);
    puts("PASS test_graph");
    return 0;
}
