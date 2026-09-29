/* Transactions, layer edits, evaluation, generations, frame leases. */
#include <stdio.h>

#include "openpainter/op_api.h"

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                                 \
            return 1;                                                                              \
        }                                                                                          \
    } while (0)

int main(void) {
    op_context_t *ctx = NULL;
    op_document_t *doc = NULL;
    uint64_t tx = 0u, req = 0u;
    int ready = 0;
    op_frame_lease_t lease, stale;
    op_metrics_t metrics;
    const float red[4] = {0.7f, 0.2f, 0.1f, 1.0f};

    CHECK(op_context_create(&ctx) == OP_OK);
    CHECK(op_document_create(ctx, 42u, &doc) == OP_OK);

    /* Revision 0 completes at create: acquirable before any edit. */
    CHECK(op_frame_acquire(ctx, doc, &lease) == OP_OK);
    CHECK(lease.document_id == 42u && lease.generation == 1u);
    CHECK(lease.revision == 0u && lease.channel_mask == 0xFFu);
    CHECK(op_frame_release(ctx, doc, &lease) == OP_OK);

    /* Two fill layers; second tx must be rejected while one is open. */
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_INVALID_ARGUMENT);
    CHECK(op_layer_add(ctx, doc, tx, 7u, OP_LAYER_TYPE_FILL) == OP_OK);
    CHECK(op_layer_add(ctx, doc, tx, 9u, OP_LAYER_TYPE_FILL) == OP_OK);
    CHECK(op_layer_add(ctx, doc, tx, 7u, OP_LAYER_TYPE_FILL) == OP_INVALID_ARGUMENT);
    CHECK(op_layer_add(ctx, doc, tx, 11u, 999u) == OP_UNSUPPORTED);
    CHECK(op_layer_set_color(ctx, doc, tx, 9u, OP_CHANNEL_BASE_COLOR, red) == OP_OK);
    CHECK(op_layer_set_scalar(ctx, doc, tx, 9u, OP_CHANNEL_ROUGHNESS, 0.3f) == OP_OK);
    CHECK(op_layer_set_scalar(ctx, doc, tx, 9u, 99u, 0.3f) == OP_INVALID_ARGUMENT);
    /* Evaluate must refuse while a transaction is still open. */
    CHECK(op_evaluate_request(ctx, doc, &req) == OP_INVALID_ARGUMENT);
    CHECK(op_transaction_commit(ctx, doc, tx) == OP_OK);
    /* Double-commit of the same id is stale. */
    CHECK(op_transaction_commit(ctx, doc, tx) == OP_STALE_HANDLE);

    CHECK(op_evaluate_request(ctx, doc, &req) == OP_OK);
    CHECK(op_poll_completions(ctx, doc, req, &ready) == OP_OK && ready == 1);
    CHECK(op_poll_completions(ctx, doc, req + 100u, &ready) == OP_INVALID_ARGUMENT);
    CHECK(op_frame_acquire(ctx, doc, &lease) == OP_OK);
    CHECK(lease.revision == 1u);

    /* Cancel discards: opacity edit below never reaches a revision. */
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    CHECK(op_layer_set_scalar(ctx, doc, tx, 9u, OP_CHANNEL_OPACITY, 0.6f) == OP_OK);
    CHECK(op_transaction_cancel(ctx, doc, tx) == OP_OK);
    CHECK(op_evaluate_request(ctx, doc, &req) == OP_OK);
    CHECK(op_frame_acquire(ctx, doc, &stale) == OP_OK);
    CHECK(stale.revision == 1u); /* cancel advanced nothing */

    /* Reorder coat above base. */
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    CHECK(op_layer_reorder(ctx, doc, tx, 9u, 0u) == OP_OK);
    CHECK(op_layer_reorder(ctx, doc, tx, 9u, 50u) == OP_INVALID_ARGUMENT);
    CHECK(op_transaction_commit(ctx, doc, tx) == OP_OK);

    /* Undo/load barrier: old leases go stale, new acquire carries gen 2. */
    CHECK(op_document_new_generation(ctx, doc) == OP_OK);
    CHECK(op_frame_release(ctx, doc, &lease) == OP_STALE_HANDLE);
    CHECK(op_frame_acquire(ctx, doc, &lease) == OP_OK);
    CHECK(lease.generation == 2u);
    CHECK(op_frame_release(ctx, doc, &lease) == OP_OK);

    CHECK(op_get_metrics(ctx, &metrics) == OP_OK);
    CHECK(metrics.commits == 2u);
    CHECK(metrics.cancels == 1u);
    CHECK(metrics.evaluations == 2u);
    CHECK(metrics.frames_acquired == 4u);
    CHECK(metrics.frames_released == 2u);

    CHECK(op_document_destroy(ctx, doc) == OP_OK);
    op_context_destroy(ctx);
    printf("PASS test_document\n");
    return 0;
}
