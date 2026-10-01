#include "openpainter/op_stack.h"
#include <stdlib.h>
typedef struct frame {
    uint32_t next, backdrop, owner;
} frame;
op_status_t op_stack_plan(const op_stack_node_t *n, uint32_t count, op_stack_position_t *p,
                          uint32_t *order, uint32_t offsets[9], uint32_t finals[8]) {
    if (count > 65536 || !offsets || !finals || (count && (!n || !p || !order)))
        return OP_INVALID_ARGUMENT;
    for (uint32_t i = 0; i < count; i++) {
        if (n[i].channel >= 8 || (n[i].flags & ~3u) || ((n[i].flags & 2u) && !(n[i].flags & 1u)))
            return OP_VALIDATION_FAILED;
        if (n[i].parent != OP_STACK_DEFAULT &&
            (n[i].parent >= i || n[n[i].parent].channel != n[i].channel ||
             !(n[n[i].parent].flags & OP_STACK_FOLDER)))
            return OP_VALIDATION_FAILED;
    }
    uint32_t *first = malloc(((size_t)count + 8) * sizeof(uint32_t)),
             *next = malloc(((size_t)count + 1) * sizeof(uint32_t));
    frame *stack = malloc(((size_t)count + 1) * sizeof(frame));
    if (!first || !next || !stack) {
        free(first);
        free(next);
        free(stack);
        return OP_RESOURCE_EXHAUSTED;
    }
    for (uint32_t i = 0; i < count + 8; i++)
        first[i] = OP_STACK_NONE;
    for (uint32_t i = count; i-- > 0;) {
        uint32_t parent = n[i].parent == OP_STACK_DEFAULT ? count + n[i].channel : n[i].parent;
        next[i] = first[parent];
        first[parent] = i;
    }
    uint32_t used = 0;
    for (uint32_t channel = 0; channel < 8; channel++) {
        offsets[channel] = used;
        uint32_t depth = 1;
        stack[0] = (frame){first[count + channel], OP_STACK_DEFAULT, OP_STACK_NONE};
        while (depth) {
            frame *f = &stack[depth - 1];
            if (f->next != OP_STACK_NONE) {
                uint32_t i = f->next;
                f->next = next[i];
                if (n[i].flags & OP_STACK_FOLDER) {
                    stack[depth++] = (frame){
                        first[i],
                        n[i].flags & OP_STACK_PASSTHROUGH ? f->backdrop : OP_STACK_TRANSPARENT, i};
                } else {
                    p[i] = (op_stack_position_t){f->backdrop, OP_STACK_NONE};
                    order[used++] = i;
                    f->backdrop = i;
                }
            } else {
                uint32_t result = f->backdrop, owner = f->owner;
                depth--;
                if (!depth)
                    finals[channel] = result;
                else {
                    frame *parent = &stack[depth - 1];
                    p[owner] = (op_stack_position_t){parent->backdrop, result};
                    order[used++] = owner;
                    parent->backdrop = owner;
                }
            }
        }
    }
    offsets[8] = used;
    free(first);
    free(next);
    free(stack);
    return OP_OK;
}
