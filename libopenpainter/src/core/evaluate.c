/* CPU revision infrastructure. GPU submission is deliberately not claimed. */
#include "internal.h"
#include <string.h>

op_status_t op_reset_frames(op_context_t *ctx, op_document_t *doc) {
    op_frame_t frame = {0};
    op_status_t status = op_fold(ctx, doc, frame.pixels);
    if (status != OP_OK)
        return status;
    frame.revision = doc->revision_pending;
    frame.valid = 1;
    memset(doc->frames, 0, sizeof(doc->frames));
    memset(doc->leases, 0, sizeof(doc->leases));
    doc->latest_frame = 0;
    doc->revision_ready = doc->revision_pending;
    doc->frames[0] = frame;
    return OP_OK;
}

op_status_t op_evaluate_request(op_context_t *ctx, op_document_t *doc, uint64_t *out_request_id) {
    uint32_t i;
    op_status_t status;
    if (!ctx || !doc || !out_request_id)
        return OP_INVALID_ARGUMENT;
    if (!op_document_valid(ctx, doc))
        return OP_STALE_HANDLE;
    if (doc->open_tx)
        return op_fail(ctx, OP_INVALID_ARGUMENT, "transaction still open");
    if (doc->req_counter == UINT64_MAX)
        return OP_RESOURCE_EXHAUSTED;
    if (doc->revision_ready != doc->revision_pending) {
        for (i = 0; i < OP_MAX_FRAMES; ++i) {
            if (doc->frames[i].references == 0)
                break;
        }
        if (i == OP_MAX_FRAMES) {
            return op_fail(ctx, OP_RESOURCE_EXHAUSTED, "all revision slots are leased");
        }
        /* Fold before making anything current; failure preserves the old frame. */
        op_frame_t frame = {0};
        status = op_fold(ctx, doc, frame.pixels);
        if (status != OP_OK)
            return status;
        frame.valid = 1;
        frame.revision = doc->revision_pending;
        doc->frames[i] = frame;
        doc->latest_frame = i;
        doc->revision_ready = doc->revision_pending;
    }
    *out_request_id = ++doc->req_counter;
    ctx->metrics.evaluations++;
    return OP_OK;
}

op_status_t op_poll_completions(op_context_t *ctx, op_document_t *doc, uint64_t request_id,
                                int *out_ready) {
    if (!ctx || !doc || !out_ready || !request_id)
        return OP_INVALID_ARGUMENT;
    *out_ready = 0;
    if (!op_document_valid(ctx, doc))
        return OP_STALE_HANDLE;
    if (request_id < doc->first_request)
        return OP_STALE_HANDLE;
    if (request_id > doc->req_counter)
        return OP_INVALID_ARGUMENT;
    *out_ready = 1;
    return OP_OK;
}

op_status_t op_frame_acquire(op_context_t *ctx, op_document_t *doc, op_frame_lease_t *out_lease) {
    uint32_t i;
    if (!ctx || !doc || !out_lease)
        return OP_INVALID_ARGUMENT;
    if (!op_document_valid(ctx, doc))
        return OP_STALE_HANDLE;
    for (i = 0; i < OP_MAX_LEASES; ++i)
        if (!doc->leases[i].token)
            break;
    if (i == OP_MAX_LEASES || ctx->lease_counter == UINT64_MAX) {
        return op_fail(ctx, OP_RESOURCE_EXHAUSTED, "lease limit reached");
    }
    memset(out_lease, 0, sizeof(*out_lease));
    out_lease->document_id = doc->id;
    out_lease->generation = doc->generation;
    out_lease->revision = doc->revision_ready;
    out_lease->channel_mask = OP_ALL_CHANNELS_MASK;
    out_lease->lease_id = ++ctx->lease_counter;
    out_lease->context_id = ctx->identity;
    doc->leases[i].token = out_lease->lease_id;
    doc->leases[i].frame = doc->latest_frame;
    doc->frames[doc->latest_frame].references++;
    ctx->metrics.frames_acquired++;
    return OP_OK;
}

op_status_t op_frame_release(op_context_t *ctx, op_document_t *doc, const op_frame_lease_t *lease) {
    uint32_t i;
    if (!ctx || !doc || !lease)
        return OP_INVALID_ARGUMENT;
    if (!op_document_valid(ctx, doc))
        return OP_STALE_HANDLE;
    if (lease->context_id != ctx->identity || lease->document_id != doc->id ||
        lease->generation != doc->generation)
        return OP_STALE_HANDLE;
    for (i = 0; i < OP_MAX_LEASES; ++i) {
        if (doc->leases[i].token && doc->leases[i].token == lease->lease_id) {
            op_frame_t *frame = &doc->frames[doc->leases[i].frame];
            if (lease->revision != frame->revision || lease->channel_mask != OP_ALL_CHANNELS_MASK)
                return OP_STALE_HANDLE;
            frame->references--;
            doc->leases[i].token = 0;
            ctx->metrics.frames_released++;
            return OP_OK;
        }
    }
    return op_fail(ctx, OP_STALE_HANDLE, "unknown or already released lease");
}
