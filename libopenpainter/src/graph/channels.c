#include "openpainter/op_channels.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
struct op_channels {
    op_channel_request_t accepted[OP_CHANNEL_COUNT];
    uint32_t valid, executing;
};
op_status_t op_channels_create(op_channels_t **out) {
    if (!out)
        return OP_INVALID_ARGUMENT;
    *out = calloc(1, sizeof(**out));
    return *out ? OP_OK : OP_RESOURCE_EXHAUSTED;
}
op_status_t op_channels_destroy(op_channels_t *s) {
    if (s && s->executing)
        return OP_PENDING;
    free(s);
    return OP_OK;
}
op_status_t op_channels_reset(op_channels_t *s) {
    if (!s)
        return OP_INVALID_ARGUMENT;
    if (s->executing)
        return OP_PENDING;
    s->valid = 0;
    return OP_OK;
}
static int same(const op_channel_request_t *a, const op_channel_request_t *b) {
    if (a->target != b->target || a->kind != b->kind || a->size != b->size ||
        (a->flags & ~OP_CHANNEL_FORCE) != (b->flags & ~OP_CHANNEL_FORCE))
        return 0;
    if (a->kind == OP_CHANNEL_PROGRAM)
        return a->revision == b->revision;
    for (unsigned i = 0; i < 4; i++)
        if (a->constant[i] != b->constant[i])
            return 0;
    return 1;
}
op_status_t op_channels_execute(op_channels_t *s, const op_channel_request_t *requests,
                                uint32_t count, op_channels_submit_fn submit, void *user,
                                uint32_t *written_mask) {
    if (!written_mask)
        return OP_INVALID_ARGUMENT;
    *written_mask = 0;
    if (!s || count > OP_CHANNEL_COUNT || (count && !requests) || !submit)
        return OP_INVALID_ARGUMENT;
    if (s->executing)
        return OP_PENDING;
    op_channel_request_t batch[OP_CHANNEL_COUNT];
    if (count)
        memcpy(batch, requests, count * sizeof(*batch));
    uint32_t seen = 0, changed = 0;
    for (uint32_t i = 0; i < count; i++) {
        const op_channel_request_t *r = batch + i;
        if (r->channel >= OP_CHANNEL_COUNT || r->kind > OP_CHANNEL_PROGRAM || !r->target ||
            !r->size || r->size > 8192 || (r->flags & ~7u))
            return OP_VALIDATION_FAILED;
        uint32_t bit = 1u << r->channel;
        if (seen & bit)
            return OP_VALIDATION_FAILED;
        seen |= bit;
        for (unsigned j = 0; j < 4; j++)
            if (!isfinite(r->constant[j]))
                return OP_VALIDATION_FAILED;
        if (!(s->valid & bit) || (r->flags & OP_CHANNEL_FORCE) ||
            !same(r, s->accepted + r->channel))
            changed |= bit;
    }
    if (!changed)
        return OP_OK;
    s->executing = 1;
    op_status_t status = OP_OK;
    for (uint32_t i = 0; i < count; i++)
        if (changed & (1u << batch[i].channel)) {
            status = submit(user, OP_CHANNEL_RENDER, batch[i].channel);
            if (status != OP_OK)
                goto done;
        }
    status = submit(user, OP_CHANNEL_COMMIT, changed);
    if (status != OP_OK)
        goto done;
    for (uint32_t i = 0; i < count; i++)
        if (changed & (1u << batch[i].channel))
            s->accepted[batch[i].channel] = batch[i];
    s->valid |= changed;
    *written_mask = changed;
done:
    s->executing = 0;
    return status;
}
