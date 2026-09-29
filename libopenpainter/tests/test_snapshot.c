/* Snapshot round-trip, tamper rejection, generation bump on restore. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
    op_document_t *doc = NULL, *restored = NULL;
    uint64_t tx = 0u, req = 0u;
    size_t size = 0u, small = 0u;
    unsigned char *buffer = NULL;
    op_frame_lease_t before, after;
    const float red[4] = {0.7f, 0.2f, 0.1f, 1.0f};

    CHECK(op_context_create(&ctx) == OP_OK);
    CHECK(op_document_create(ctx, 7u, &doc) == OP_OK);
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    CHECK(op_layer_add(ctx, doc, tx, 1u, OP_LAYER_TYPE_FILL) == OP_OK);
    CHECK(op_layer_set_color(ctx, doc, tx, 1u, OP_CHANNEL_BASE_COLOR, red) == OP_OK);
    CHECK(op_transaction_commit(ctx, doc, tx) == OP_OK);
    CHECK(op_evaluate_request(ctx, doc, &req) == OP_OK);
    CHECK(op_frame_acquire(ctx, doc, &before) == OP_OK);

    CHECK(op_document_snapshot_size(ctx, doc, &size) == OP_OK);
    CHECK(size > 0u);
    buffer = (unsigned char *)malloc(size);
    CHECK(buffer != NULL);
    /* Too-small buffer is rejected. */
    small = size - 1u;
    CHECK(op_document_snapshot_write(ctx, doc, buffer, small) == OP_INVALID_ARGUMENT);
    CHECK(op_document_snapshot_write(ctx, doc, buffer, size) == OP_OK);

    /* Corrupt one payload byte: checksum must fail. */
    buffer[20] ^= 0xFFu;
    CHECK(op_document_restore(ctx, buffer, size, &restored) == OP_VALIDATION_FAILED);
    buffer[20] ^= 0xFFu;
    /* Truncated input is rejected. */
    CHECK(op_document_restore(ctx, buffer, size - 8u, &restored) == OP_VALIDATION_FAILED);

    CHECK(op_document_restore(ctx, buffer, size, &restored) == OP_OK);
    CHECK(op_frame_acquire(ctx, restored, &after) == OP_OK);
    CHECK(after.document_id == before.document_id);
    CHECK(after.revision == before.revision);          /* state preserved */
    CHECK(after.generation == before.generation + 1u); /* load = new generation */
    /* Pre-save lease is stale against the restored document. */
    CHECK(op_frame_release(ctx, restored, &before) == OP_STALE_HANDLE);
    CHECK(op_frame_release(ctx, restored, &after) == OP_OK);

    free(buffer);
    CHECK(op_document_destroy(ctx, doc) == OP_OK);
    CHECK(op_document_destroy(ctx, restored) == OP_OK);
    op_context_destroy(ctx);
    printf("PASS test_snapshot\n");
    return 0;
}
