/* Document, transaction, layer-edit, generation, and snapshot model. */
#include <stdlib.h>
#include <string.h>

#include "../core/internal.h"

static void layer_default(op_layer_t *layer, uint64_t id, uint32_t type) {
    static const float defaults[OP_CHANNEL_COUNT][4] = {
        {0.5f, 0.5f, 0.5f, 1.0f}, /* BASE_COLOR */
        {0.5f, 0.5f, 0.5f, 1.0f}, /* ROUGHNESS */
        {0.0f, 0.0f, 0.0f, 1.0f}, /* METALLIC */
        {0.5f, 0.5f, 1.0f, 1.0f}, /* NORMAL */
        {0.5f, 0.5f, 0.5f, 1.0f}, /* HEIGHT */
        {1.0f, 1.0f, 1.0f, 1.0f}, /* AO */
        {0.0f, 0.0f, 0.0f, 1.0f}, /* EMISSION */
        {1.0f, 1.0f, 1.0f, 1.0f}, /* OPACITY */
    };
    layer->id = id;
    layer->type = type;
    layer->enabled_mask = OP_ALL_CHANNELS_MASK;
    layer->opacity = 1.0f;
    layer->blend = OP_BLEND_NORMAL;
    memcpy(layer->values, defaults, sizeof(layer->values));
}

op_status_t op_document_create(op_context_t *ctx, uint64_t document_id, op_document_t **out_doc) {
    op_document_t *doc;
    if (ctx == NULL || out_doc == NULL || document_id == 0u) {
        return op_fail(ctx, OP_INVALID_ARGUMENT,
                       "document_create: context, output, and nonzero id required");
    }
    op_clear_error(ctx);
    doc = (op_document_t *)calloc(1, sizeof(*doc));
    if (doc == NULL) {
        return op_fail(ctx, OP_RESOURCE_EXHAUSTED, "document_create: out of memory");
    }
    if (ctx->document_handles >= OP_MAX_DOCUMENT_HANDLES || ctx->generation_counter == UINT64_MAX) {
        free(doc);
        return op_fail(ctx, OP_RESOURCE_EXHAUSTED, "document handle limit reached");
    }
    doc->next = ctx->documents;
    ctx->documents = doc;
    ctx->document_handles++;
    doc->alive = 1;
    doc->id = document_id;
    doc->generation = ++ctx->generation_counter;
    doc->revision_ready = 0u; /* empty document completes revision 0 at create */
    doc->revision_pending = 0u;
    op_reset_frames(ctx, doc);
    *out_doc = doc;
    ctx->metrics.documents_created++;
    return OP_OK;
}

op_status_t op_document_destroy(op_context_t *ctx, op_document_t *doc) {
    if (ctx == NULL || doc == NULL) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "document_destroy: null argument");
    }
    if (!op_document_valid(ctx, doc)) {
        return op_fail(ctx, OP_STALE_HANDLE, "document does not belong to this live context");
    }
    op_clear_error(ctx);
    doc->alive = 0; /* Tombstone prevents stale-pointer reuse until context close. */
    return OP_OK;
}

op_status_t op_document_new_generation(op_context_t *ctx, op_document_t *doc) {
    if (ctx == NULL || doc == NULL) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "new_generation: null argument");
    }
    if (!op_document_valid(ctx, doc)) {
        return op_fail(ctx, OP_STALE_HANDLE, "document does not belong to this live context");
    }
    op_clear_error(ctx);
    if (ctx->generation_counter == UINT64_MAX) {
        return op_fail(ctx, OP_RESOURCE_EXHAUSTED, "generation counter exhausted");
    }
    if (doc->req_counter == UINT64_MAX)
        return OP_RESOURCE_EXHAUSTED;
    {
        op_status_t status = op_reset_frames(ctx, doc);
        if (status != OP_OK)
            return status;
    }
    doc->generation = ++ctx->generation_counter;
    doc->first_request = doc->req_counter + 1u;
    doc->open_tx = 0u; /* discard uncommitted staging across the barrier */
    return OP_OK;
}

op_status_t op_transaction_begin(op_context_t *ctx, op_document_t *doc, uint64_t *out_tx_id) {
    if (ctx == NULL || doc == NULL || out_tx_id == NULL) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "transaction_begin: null argument");
    }
    if (!op_document_valid(ctx, doc)) {
        return op_fail(ctx, OP_STALE_HANDLE, "document does not belong to this live context");
    }
    op_clear_error(ctx);
    if (doc->open_tx != 0u) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "transaction_begin: one already open");
    }
    if (ctx->transaction_counter == UINT64_MAX) {
        return op_fail(ctx, OP_RESOURCE_EXHAUSTED, "transaction counter exhausted");
    }
    doc->staged_count = doc->layer_count;
    memcpy(doc->staged, doc->committed, sizeof(doc->staged));
    doc->open_tx = ++ctx->transaction_counter;
    *out_tx_id = doc->open_tx;
    return OP_OK;
}

op_status_t op_transaction_commit(op_context_t *ctx, op_document_t *doc, uint64_t tx_id) {
    if (ctx == NULL || doc == NULL || tx_id == 0u) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "transaction_commit: null argument");
    }
    if (!op_document_valid(ctx, doc)) {
        return op_fail(ctx, OP_STALE_HANDLE, "document does not belong to this live context");
    }
    op_clear_error(ctx);
    if (doc->open_tx == 0u || doc->open_tx != tx_id) {
        return op_fail(ctx, OP_STALE_HANDLE, "transaction_commit: not the open transaction");
    }
    if (doc->revision_pending == UINT64_MAX) {
        return op_fail(ctx, OP_RESOURCE_EXHAUSTED, "revision counter exhausted");
    }
    doc->layer_count = doc->staged_count;
    memcpy(doc->committed, doc->staged, sizeof(doc->committed));
    doc->open_tx = 0u;
    doc->revision_pending++;
    ctx->metrics.commits++;
    return OP_OK;
}

op_status_t op_transaction_cancel(op_context_t *ctx, op_document_t *doc, uint64_t tx_id) {
    if (ctx == NULL || doc == NULL || tx_id == 0u) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "transaction_cancel: null argument");
    }
    if (!op_document_valid(ctx, doc)) {
        return op_fail(ctx, OP_STALE_HANDLE, "document does not belong to this live context");
    }
    op_clear_error(ctx);
    if (doc->open_tx == 0u || doc->open_tx != tx_id) {
        return op_fail(ctx, OP_STALE_HANDLE, "transaction_cancel: not the open transaction");
    }
    doc->open_tx = 0u; /* staged edits discarded */
    ctx->metrics.cancels++;
    return OP_OK;
}

static op_status_t require_open_tx(op_context_t *ctx, op_document_t *doc, uint64_t tx_id) {
    if (doc->open_tx == 0u || doc->open_tx != tx_id) {
        return op_fail(ctx, OP_STALE_HANDLE, "edit: no matching open transaction");
    }
    return OP_OK;
}

op_status_t op_layer_add(op_context_t *ctx, op_document_t *doc, uint64_t tx_id, uint64_t layer_id,
                         uint32_t layer_type) {
    op_status_t ok;
    if (ctx == NULL || doc == NULL || layer_id == 0u) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "layer_add: null argument");
    }
    if (!op_document_valid(ctx, doc)) {
        return op_fail(ctx, OP_STALE_HANDLE, "document does not belong to this live context");
    }
    op_clear_error(ctx);
    ok = require_open_tx(ctx, doc, tx_id);
    if (ok != OP_OK) {
        return ok;
    }
    if (layer_type != OP_LAYER_TYPE_FILL) {
        return op_fail(ctx, OP_UNSUPPORTED, "layer_add: only FILL in this slice");
    }
    if (doc->staged_count >= OP_MAX_LAYERS) {
        return op_fail(ctx, OP_RESOURCE_EXHAUSTED, "layer_add: layer cap reached");
    }
    if (op_find_layer(doc->staged, doc->staged_count, layer_id) >= 0) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "layer_add: duplicate layer id");
    }
    layer_default(&doc->staged[doc->staged_count], layer_id, layer_type);
    doc->staged_count++;
    return OP_OK;
}

op_status_t op_layer_set_scalar(op_context_t *ctx, op_document_t *doc, uint64_t tx_id,
                                uint64_t layer_id, uint32_t channel, float value) {
    op_status_t ok;
    int index;
    if (ctx == NULL || doc == NULL) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "set_scalar: null argument");
    }
    if (!op_document_valid(ctx, doc)) {
        return op_fail(ctx, OP_STALE_HANDLE, "document does not belong to this live context");
    }
    op_clear_error(ctx);
    ok = require_open_tx(ctx, doc, tx_id);
    if (ok != OP_OK) {
        return ok;
    }
    if (channel >= OP_CHANNEL_COUNT) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "set_scalar: bad channel");
    }
    if (value != value || value > 1e30f || value < -1e30f) { /* NaN / wild */
        return op_fail(ctx, OP_INVALID_ARGUMENT, "set_scalar: bad value");
    }
    index = op_find_layer(doc->staged, doc->staged_count, layer_id);
    if (index < 0) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "set_scalar: unknown layer");
    }
    doc->staged[index].values[channel][0] = value;
    doc->staged[index].values[channel][1] = value;
    doc->staged[index].values[channel][2] = value;
    return OP_OK;
}

op_status_t op_layer_set_color(op_context_t *ctx, op_document_t *doc, uint64_t tx_id,
                               uint64_t layer_id, uint32_t channel, const float rgba[4]) {
    op_status_t ok;
    int index, c;
    if (ctx == NULL || doc == NULL || rgba == NULL) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "set_color: null argument");
    }
    if (!op_document_valid(ctx, doc)) {
        return op_fail(ctx, OP_STALE_HANDLE, "document does not belong to this live context");
    }
    op_clear_error(ctx);
    ok = require_open_tx(ctx, doc, tx_id);
    if (ok != OP_OK) {
        return ok;
    }
    if (channel >= OP_CHANNEL_COUNT) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "set_color: bad channel");
    }
    for (c = 0; c < 4; c++) {
        if (rgba[c] != rgba[c] || rgba[c] > 1e30f || rgba[c] < -1e30f) {
            return op_fail(ctx, OP_INVALID_ARGUMENT, "set_color: bad component");
        }
    }
    if (rgba[3] != 1.0f) {
        return op_fail(ctx, OP_UNSUPPORTED,
                       "transparent coverage is not implemented in this slice");
    }
    index = op_find_layer(doc->staged, doc->staged_count, layer_id);
    if (index < 0) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "set_color: unknown layer");
    }
    memcpy(doc->staged[index].values[channel], rgba, sizeof(float) * 4u);
    return OP_OK;
}

op_status_t op_layer_set_opacity(op_context_t *ctx, op_document_t *doc, uint64_t tx_id,
                                 uint64_t layer_id, float opacity) {
    op_status_t ok;
    int index;
    if (ctx == NULL || doc == NULL) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "set_opacity: null argument");
    }
    if (!op_document_valid(ctx, doc)) {
        return op_fail(ctx, OP_STALE_HANDLE, "document does not belong to this live context");
    }
    op_clear_error(ctx);
    ok = require_open_tx(ctx, doc, tx_id);
    if (ok != OP_OK) {
        return ok;
    }
    if (opacity != opacity || opacity < 0.0f || opacity > 1.0f) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "set_opacity: out of range");
    }
    index = op_find_layer(doc->staged, doc->staged_count, layer_id);
    if (index < 0) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "set_opacity: unknown layer");
    }
    doc->staged[index].opacity = opacity;
    return OP_OK;
}

op_status_t op_layer_set_blend(op_context_t *ctx, op_document_t *doc, uint64_t tx_id,
                               uint64_t layer_id, uint32_t blend_mode) {
    op_status_t ok;
    int index;
    if (ctx == NULL || doc == NULL) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "set_blend: null argument");
    }
    if (!op_document_valid(ctx, doc)) {
        return op_fail(ctx, OP_STALE_HANDLE, "document does not belong to this live context");
    }
    op_clear_error(ctx);
    ok = require_open_tx(ctx, doc, tx_id);
    if (ok != OP_OK) {
        return ok;
    }
    if (blend_mode != OP_BLEND_NORMAL && blend_mode != OP_BLEND_MULTIPLY &&
        blend_mode != OP_BLEND_OVERLAY && blend_mode != OP_BLEND_DISABLE) {
        return op_fail(ctx, OP_UNSUPPORTED, "set_blend: mode not ported yet");
    }
    index = op_find_layer(doc->staged, doc->staged_count, layer_id);
    if (index < 0) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "set_blend: unknown layer");
    }
    doc->staged[index].blend = blend_mode;
    return OP_OK;
}

op_status_t op_layer_reorder(op_context_t *ctx, op_document_t *doc, uint64_t tx_id,
                             uint64_t layer_id, uint32_t new_index) {
    op_status_t ok;
    int from;
    op_layer_t moved;
    if (ctx == NULL || doc == NULL) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "reorder: null argument");
    }
    if (!op_document_valid(ctx, doc)) {
        return op_fail(ctx, OP_STALE_HANDLE, "document does not belong to this live context");
    }
    op_clear_error(ctx);
    ok = require_open_tx(ctx, doc, tx_id);
    if (ok != OP_OK) {
        return ok;
    }
    from = op_find_layer(doc->staged, doc->staged_count, layer_id);
    if (from < 0) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "reorder: unknown layer");
    }
    if ((size_t)new_index >= doc->staged_count) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "reorder: index out of range");
    }
    if ((size_t)from == (size_t)new_index) {
        return OP_OK;
    }
    moved = doc->staged[from];
    if ((size_t)from < (size_t)new_index) {
        memmove(&doc->staged[from], &doc->staged[from + 1],
                ((size_t)new_index - (size_t)from) * sizeof(op_layer_t));
    } else {
        memmove(&doc->staged[new_index + 1], &doc->staged[new_index],
                ((size_t)from - (size_t)new_index) * sizeof(op_layer_t));
    }
    doc->staged[new_index] = moved;
    return OP_OK;
}
