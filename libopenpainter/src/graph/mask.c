#include "openpainter/op_mask.h"
#include <stdlib.h>

typedef struct frame {
    uint32_t end, owner, previous, prefix;
} frame_t;

void op_mask_plan_free(op_mask_plan_t *plan) {
    if (!plan)
        return;
    free(plan->steps);
    free(plan->walk);
    free(plan);
}

op_status_t op_mask_plan_build(const op_mask_node_t *nodes, uint32_t count, uint32_t start,
                               uint32_t stop, uint32_t pre_filter, uint32_t initial,
                               op_mask_plan_t **out) {
    if (!out)
        return OP_INVALID_ARGUMENT;
    *out = NULL;
    if (count > 65536 || (count && !nodes) || start > stop || stop > count || pre_filter > 1 ||
        initial > 1)
        return OP_INVALID_ARGUMENT;
    size_t capacity = (size_t)count + 1;
    uint32_t *ends = calloc(capacity, sizeof(*ends));
    frame_t *frames = calloc(capacity, sizeof(*frames));
    op_mask_plan_t *p = calloc(1, sizeof(*p));
    op_status_t status = OP_RESOURCE_EXHAUSTED;
    if (!ends || !frames || !p)
        goto done;
    p->steps = calloc(capacity, sizeof(*p->steps));
    p->walk = calloc(capacity, sizeof(*p->walk));
    if (!p->steps || !p->walk)
        goto done;
    status = OP_INVALID_ARGUMENT;
    uint32_t depth = 0;
    /* Close contiguous groups once; no recursive descent or ancestor scans. */
    for (uint32_t i = 0; i < count; i++) {
        uint32_t flags = nodes[i].flags, kind = flags & ~OP_MASK_ENABLED;
        if ((flags & ~15u) || (kind && (kind & (kind - 1u))))
            goto done;
        uint32_t parent = nodes[i].parent;
        while (depth && frames[depth - 1].owner != parent)
            ends[frames[--depth].owner] = i;
        if (parent != OP_MASK_NONE && (!depth || parent >= i))
            goto done;
        ends[i] = i + 1;
        if (flags & OP_MASK_GROUP)
            frames[depth++].owner = i;
    }
    while (depth)
        ends[frames[--depth].owner] = count;
    frames[0] =
        (frame_t){stop, OP_MASK_NONE, initial ? OP_MASK_INITIAL : OP_MASK_NONE, OP_MASK_NONE};
    uint32_t i = start;
    for (;;) {
        frame_t *f = frames + depth;
        if (i >= f->end) {
            if (!depth) {
                p->result = f->previous;
                break;
            }
            uint32_t value = f->previous == OP_MASK_NONE ? OP_MASK_ONE : f->previous;
            uint32_t owner = f->owner;
            f = frames + --depth;
            p->steps[p->step_count] =
                (op_mask_step_t){owner, f->previous, value, f->prefix, p->walk_count};
            f->previous = p->step_count++;
            continue;
        }
        uint32_t flags = nodes[i].flags;
        if (!(flags & OP_MASK_ENABLED) || (flags & OP_MASK_ANCHOR) ||
            (pre_filter && (flags & OP_MASK_FILTER))) {
            i = ends[i] < f->end ? ends[i] : f->end;
            continue;
        }
        p->walk[p->walk_count++] = i;
        if (flags & OP_MASK_GROUP) {
            uint32_t end = ends[i] < f->end ? ends[i] : f->end;
            frames[++depth] = (frame_t){end, i++, OP_MASK_NONE, p->walk_count};
        } else {
            p->steps[p->step_count] =
                (op_mask_step_t){i++, f->previous, OP_MASK_NONE, f->prefix, p->walk_count};
            f->previous = p->step_count++;
        }
    }
    *out = p;
    p = NULL;
    status = OP_OK;
done:
    free(ends);
    free(frames);
    op_mask_plan_free(p);
    return status;
}
