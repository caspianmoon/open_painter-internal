#include "openpainter/op_stack.h"
#include <assert.h>
int main(void) {
    op_stack_node_t n[] = {
        {OP_STACK_DEFAULT, 0, 0}, {OP_STACK_DEFAULT, 0, 3}, {1, 0, 0}, {1, 0, 1}, {3, 0, 0},
        {OP_STACK_DEFAULT, 1, 1}};
    op_stack_position_t p[6];
    uint32_t order[6], offsets[9], finals[8];
    assert(op_stack_plan(n, 6, p, order, offsets, finals) == OP_OK);
    assert(p[2].backdrop == 0 && p[4].backdrop == OP_STACK_TRANSPARENT);
    assert(p[3].backdrop == 2 && p[3].grouped == 4 && p[1].grouped == 3);
    assert(p[5].grouped == OP_STACK_TRANSPARENT && finals[0] == 1 && finals[1] == 5 &&
           finals[2] == OP_STACK_DEFAULT);
    assert(order[0] == 0 && order[1] == 2 && order[2] == 4 && order[3] == 3 && order[4] == 1 &&
           order[5] == 5);
    n[1].parent = 1;
    assert(op_stack_plan(n, 6, p, order, offsets, finals) == OP_VALIDATION_FAILED);
    return 0;
}
