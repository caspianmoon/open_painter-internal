#include "openpainter/op_api.h"
#include <stdio.h>
#include <string.h>
#define CHECK(x)                                                                                   \
    do {                                                                                           \
        if (!(x)) {                                                                                \
            printf("FAIL line %d: %s\n", __LINE__, #x);                                            \
            return 1;                                                                              \
        }                                                                                          \
    } while (0)
int main(void) {
    op_context_t *ctx, *other;
    op_document_t *doc, *restored;
    op_frame_lease_t held[8], lease;
    uint64_t tx, req, old_req;
    unsigned char a[4096], b[4096];
    size_t size;
    float pixel[4];
    int ready;
    CHECK(op_context_create(&ctx) == OP_OK);
    CHECK(op_context_create(&other) == OP_OK);
    CHECK(op_document_create(ctx, 17, &doc) == OP_OK);
    op_document_t *other_doc;
    op_frame_lease_t foreign, own;
    CHECK(op_document_create(other, 17, &other_doc) == OP_OK);
    CHECK(op_frame_acquire(ctx, doc, &own) == OP_OK);
    CHECK(op_frame_acquire(other, other_doc, &foreign) == OP_OK);
    CHECK(op_frame_release(ctx, doc, &foreign) == OP_STALE_HANDLE);
    CHECK(op_frame_release(ctx, doc, &own) == OP_OK);
    CHECK(op_transaction_begin(other, doc, &tx) == OP_STALE_HANDLE);
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    CHECK(op_layer_add(ctx, doc, tx, 9, OP_LAYER_TYPE_FILL) == OP_OK);
    CHECK(op_transaction_cancel(ctx, doc, tx) == OP_OK);
    CHECK(op_document_snapshot_size(ctx, doc, &size) == OP_OK && size == 44);
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    CHECK(op_layer_add(ctx, doc, tx, 9, OP_LAYER_TYPE_FILL) == OP_OK);
    CHECK(op_layer_set_scalar(ctx, doc, tx, 9, 1, .25f) == OP_OK);
    CHECK(op_transaction_commit(ctx, doc, tx) == OP_OK);
    /* Unevaluated commit must not leak into revision 0. */
    CHECK(op_composite_read(ctx, doc, 0, 1, 1, pixel, 4) == OP_OK && pixel[0] == .5f);
    CHECK(op_evaluate_request(ctx, doc, &old_req) == OP_OK);
    CHECK(op_frame_acquire(ctx, doc, &held[0]) == OP_OK);
    for (int i = 1; i < 8; ++i) {
        CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
        CHECK(op_layer_set_scalar(ctx, doc, tx, 9, 1, .75f) == OP_OK);
        CHECK(op_transaction_commit(ctx, doc, tx) == OP_OK);
        CHECK(op_evaluate_request(ctx, doc, &req) == OP_OK);
        CHECK(op_frame_acquire(ctx, doc, &held[i]) == OP_OK);
    }
    CHECK(op_composite_read(ctx, doc, held[0].revision, 1, 1, pixel, 4) == OP_OK);
    CHECK(pixel[0] == .25f);
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    CHECK(op_transaction_commit(ctx, doc, tx) == OP_OK);
    CHECK(op_evaluate_request(ctx, doc, &req) == OP_RESOURCE_EXHAUSTED);
    CHECK(op_frame_release(ctx, doc, &held[0]) == OP_OK);
    CHECK(op_frame_release(ctx, doc, &held[0]) == OP_STALE_HANDLE);
    CHECK(op_evaluate_request(ctx, doc, &req) == OP_OK);
    CHECK(op_composite_read(ctx, doc, held[0].revision, 1, 1, pixel, 4) == OP_STALE_HANDLE);
    /* Snapshot bytes cannot depend on the caller's buffer contents. */
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    CHECK(op_transaction_commit(ctx, doc, tx) == OP_OK);
    CHECK(op_document_snapshot_size(ctx, doc, &size) == OP_OK && size < sizeof(a));
    memset(a, 0xAA, sizeof(a));
    memset(b, 0xBB, sizeof(b));
    CHECK(op_document_snapshot_write(ctx, doc, a, size) == OP_OK);
    CHECK(op_document_snapshot_write(ctx, doc, b, size) == OP_OK);
    CHECK(memcmp(a, b, size) == 0);
    CHECK(op_document_restore(ctx, a, size, &restored) == OP_OK);
    CHECK(op_frame_acquire(ctx, restored, &lease) == OP_OK && lease.revision == 10);
    CHECK(op_document_new_generation(ctx, doc) == OP_OK);
    CHECK(op_poll_completions(ctx, doc, old_req, &ready) == OP_STALE_HANDLE);
    CHECK(op_frame_release(ctx, doc, &held[1]) == OP_STALE_HANDLE);
    /* Unsupported transparency is explicit; overflow cannot replace a good frame. */
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    const float transparent[4] = {1, 0, 0, .5f};
    CHECK(op_layer_set_color(ctx, doc, tx, 9, 0, transparent) == OP_UNSUPPORTED);
    CHECK(op_layer_set_scalar(ctx, doc, tx, 9, 1, 1e30f) == OP_OK);
    CHECK(op_layer_add(ctx, doc, tx, 10, OP_LAYER_TYPE_FILL) == OP_OK);
    CHECK(op_layer_set_scalar(ctx, doc, tx, 10, 1, 1e30f) == OP_OK);
    CHECK(op_layer_set_blend(ctx, doc, tx, 10, OP_BLEND_MULTIPLY) == OP_OK);
    CHECK(op_transaction_commit(ctx, doc, tx) == OP_OK);
    CHECK(op_evaluate_request(ctx, doc, &req) == OP_VALIDATION_FAILED);
    CHECK(op_frame_acquire(ctx, doc, &lease) == OP_OK && lease.revision == 10);
    CHECK(op_composite_read(ctx, doc, lease.revision, 1, 1, pixel, 4) == OP_OK);
    CHECK(pixel[0] == .75f);
    CHECK(op_document_destroy(ctx, doc) == OP_OK);
    CHECK(op_document_destroy(ctx, doc) == OP_STALE_HANDLE);
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_STALE_HANDLE);
    /* Outstanding restored doc is reclaimed by context destruction. */
    op_context_destroy(ctx);
    op_context_destroy(other);
    puts("PASS test_lifetime");
    return 0;
}
