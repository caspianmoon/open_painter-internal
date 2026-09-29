/* Bounded parser mutation harness, including malicious but valid checksums. */
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
static void put32(unsigned char *p, uint32_t v) {
    for (int i = 0; i < 4; ++i)
        p[i] = (unsigned char)(v >> (8 * i));
}
static void checksum(unsigned char *p, size_t n) {
    uint32_t hash = 2166136261u;
    for (size_t i = 0; i < n - 4; ++i) {
        hash ^= p[i];
        hash *= 16777619u;
    }
    put32(p + n - 4, hash);
}
int main(void) {
    op_context_t *ctx;
    op_document_t *doc, *out;
    uint64_t tx;
    size_t n;
    unsigned char good[512], bad[512];
    CHECK(op_context_create(&ctx) == OP_OK);
    CHECK(op_document_create(ctx, 1, &doc) == OP_OK);
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    CHECK(op_layer_add(ctx, doc, tx, 1, OP_LAYER_TYPE_FILL) == OP_OK);
    CHECK(op_transaction_commit(ctx, doc, tx) == OP_OK);
    CHECK(op_document_snapshot_size(ctx, doc, &n) == OP_OK && n == 196);
    CHECK(op_document_snapshot_write(ctx, doc, good, n) == OP_OK);
    for (size_t len = 0; len < n; ++len)
        CHECK(op_document_restore(ctx, good, len, &out) == OP_VALIDATION_FAILED);
    for (size_t i = 0; i < n; ++i) {
        memcpy(bad, good, n);
        bad[i] ^= 0x80;
        CHECK(op_document_restore(ctx, bad, n, &out) == OP_VALIDATION_FAILED);
    }
    const size_t offsets[] = {36, 48, 52, 56, 60, 64};
    const uint32_t invalid[] = {1, 999, 0x100, 0x7fc00000, 999, 0x7f800000};
    for (size_t i = 0; i < sizeof(offsets) / sizeof(offsets[0]); ++i) {
        memcpy(bad, good, n);
        put32(bad + offsets[i], invalid[i]);
        checksum(bad, n);
        CHECK(op_document_restore(ctx, bad, n, &out) == OP_VALIDATION_FAILED && out == NULL);
    }
    CHECK(op_document_restore(ctx, good, n, &out) == OP_OK);
    op_frame_lease_t a, b;
    CHECK(op_frame_acquire(ctx, out, &a) == OP_OK);
    CHECK(op_document_restore(ctx, good, n, &out) == OP_OK);
    CHECK(op_frame_acquire(ctx, out, &b) == OP_OK && b.generation > a.generation);
    CHECK(op_frame_release(ctx, out, &a) == OP_STALE_HANDLE);
    op_context_destroy(ctx);
    puts("PASS test_snapshot_validation");
    return 0;
}
