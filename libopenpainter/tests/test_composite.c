/* Compositing fixtures: NORMAL blend over fills, hand-computed expectations.
 *
 * Convention: out = src*a + dst*(1-a), a = opacity*src_alpha, backdrop =
 * Python DEFAULTS. All expectations below were computed by hand from that
 * rule; any mismatch is a core bug, not fixture drift.
 */
#include <math.h>
#include <stdio.h>

#include "openpainter/op_api.h"

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                                 \
            return 1;                                                                              \
        }                                                                                          \
    } while (0)

#define CHECK_CLOSE(got, want)                                                                     \
    do {                                                                                           \
        if (fabs((double)(got) - (double)(want)) > 1e-6) {                                         \
            printf("FAIL %s:%d: got %f want %f\n", __FILE__, __LINE__, (double)(got),              \
                   (double)(want));                                                                \
            return 1;                                                                              \
        }                                                                                          \
    } while (0)

static int check_uniform(const float *pixels, size_t texels, const float want[4]) {
    size_t i;
    int c;
    for (i = 0; i < texels; i++) {
        for (c = 0; c < 4; c++) {
            if (fabs((double)pixels[i * 4u + (size_t)c] - (double)want[c]) > 1e-6) {
                printf("FAIL texel %zu comp %d: got %f want %f\n", i, c,
                       (double)pixels[i * 4u + (size_t)c], (double)want[c]);
                return 1;
            }
        }
    }
    return 0;
}

int main(void) {
    op_context_t *ctx = NULL;
    op_document_t *doc = NULL;
    uint64_t tx = 0u, req = 0u;
    float pixels[8 * 8 * 4];
    const float red[4] = {0.7f, 0.2f, 0.1f, 1.0f};
    const float white[4] = {1.0f, 1.0f, 1.0f, 1.0f};
    const float black[4] = {0.0f, 0.0f, 0.0f, 1.0f};
    /* Fixture A: red coat at 0.6 over gray -> (0.62, 0.32, 0.26, 1). */
    const float coat060[4] = {0.62f, 0.32f, 0.26f, 1.0f};
    const float rough060[4] = {0.38f, 0.38f, 0.38f, 1.0f};
    const float opaque[4] = {1.0f, 1.0f, 1.0f, 1.0f};
    /* Fixture B: same coat at 0.3 -> (0.56, 0.41, 0.38, 1). */
    const float coat030[4] = {0.56f, 0.41f, 0.38f, 1.0f};

    CHECK(op_context_create(&ctx) == OP_OK);
    CHECK(op_document_create(ctx, 5u, &doc) == OP_OK);

    /* Revision 0 (empty stack) reads the bare backdrop. */
    CHECK(op_composite_read(ctx, doc, 0u, OP_CHANNEL_BASE_COLOR, 4u, pixels,
                            sizeof(pixels) / sizeof(float)) == OP_OK);
    {
        const float gray[4] = {0.5f, 0.5f, 0.5f, 1.0f};
        CHECK(check_uniform(pixels, 16u, gray) == 0);
    }

    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    CHECK(op_layer_add(ctx, doc, tx, 1u, OP_LAYER_TYPE_FILL) == OP_OK);
    CHECK(op_layer_add(ctx, doc, tx, 2u, OP_LAYER_TYPE_FILL) == OP_OK);
    CHECK(op_layer_set_color(ctx, doc, tx, 2u, OP_CHANNEL_BASE_COLOR, red) == OP_OK);
    CHECK(op_layer_set_scalar(ctx, doc, tx, 2u, OP_CHANNEL_ROUGHNESS, 0.3f) == OP_OK);
    CHECK(op_layer_set_opacity(ctx, doc, tx, 2u, 0.6f) == OP_OK);
    CHECK(op_layer_set_opacity(ctx, doc, tx, 2u, 1.5f) == OP_INVALID_ARGUMENT);
    CHECK(op_layer_set_opacity(ctx, doc, tx, 2u, -0.1f) == OP_INVALID_ARGUMENT);
    CHECK(op_transaction_commit(ctx, doc, tx) == OP_OK);
    CHECK(op_evaluate_request(ctx, doc, &req) == OP_OK);

    CHECK(op_composite_read(ctx, doc, 1u, OP_CHANNEL_BASE_COLOR, 4u, pixels,
                            sizeof(pixels) / sizeof(float)) == OP_OK);
    CHECK(check_uniform(pixels, 16u, coat060) == 0);
    CHECK(op_composite_read(ctx, doc, 1u, OP_CHANNEL_ROUGHNESS, 8u, pixels,
                            sizeof(pixels) / sizeof(float)) == OP_OK);
    CHECK(check_uniform(pixels, 64u, rough060) == 0);
    CHECK(op_composite_read(ctx, doc, 1u, OP_CHANNEL_OPACITY, 4u, pixels,
                            sizeof(pixels) / sizeof(float)) == OP_OK);
    CHECK(check_uniform(pixels, 16u, opaque) == 0);

    /* Fixture B: unleased rev 1 is evicted when its slot is reused. */
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    CHECK(op_layer_set_opacity(ctx, doc, tx, 2u, 0.3f) == OP_OK);
    CHECK(op_transaction_commit(ctx, doc, tx) == OP_OK);
    CHECK(op_evaluate_request(ctx, doc, &req) == OP_OK);
    CHECK(op_composite_read(ctx, doc, 2u, OP_CHANNEL_BASE_COLOR, 4u, pixels,
                            sizeof(pixels) / sizeof(float)) == OP_OK);
    CHECK(check_uniform(pixels, 16u, coat030) == 0);
    CHECK(op_composite_read(ctx, doc, 1u, OP_CHANNEL_BASE_COLOR, 4u, pixels,
                            sizeof(pixels) / sizeof(float)) == OP_STALE_HANDLE);

    /* Fixture C: order dependence with three opaque-ish layers. */
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    CHECK(op_layer_add(ctx, doc, tx, 3u, OP_LAYER_TYPE_FILL) == OP_OK);
    CHECK(op_layer_set_color(ctx, doc, tx, 2u, OP_CHANNEL_BASE_COLOR, white) == OP_OK);
    CHECK(op_layer_set_opacity(ctx, doc, tx, 2u, 1.0f) == OP_OK);
    CHECK(op_layer_set_color(ctx, doc, tx, 3u, OP_CHANNEL_BASE_COLOR, black) == OP_OK);
    CHECK(op_layer_set_opacity(ctx, doc, tx, 3u, 0.5f) == OP_OK);
    CHECK(op_transaction_commit(ctx, doc, tx) == OP_OK);
    CHECK(op_evaluate_request(ctx, doc, &req) == OP_OK);
    CHECK(op_composite_read(ctx, doc, 3u, OP_CHANNEL_BASE_COLOR, 4u, pixels,
                            sizeof(pixels) / sizeof(float)) == OP_OK);
    {
        /* gray -> white (full cover) -> black at 0.5 = mid gray. */
        const float mid[4] = {0.5f, 0.5f, 0.5f, 1.0f};
        CHECK(check_uniform(pixels, 16u, mid) == 0);
    }
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    CHECK(op_layer_reorder(ctx, doc, tx, 3u, 0u) == OP_OK);
    CHECK(op_transaction_commit(ctx, doc, tx) == OP_OK);
    CHECK(op_evaluate_request(ctx, doc, &req) == OP_OK);
    CHECK(op_composite_read(ctx, doc, 4u, OP_CHANNEL_BASE_COLOR, 4u, pixels,
                            sizeof(pixels) / sizeof(float)) == OP_OK);
    {
        /* gray -> black at 0.5 = quarter -> white full cover. */
        CHECK(check_uniform(pixels, 16u, white) == 0);
    }

    /* Fixture D: scalar writes the triple, preserves alpha; HDR unclamped. */
    CHECK(op_transaction_begin(ctx, doc, &tx) == OP_OK);
    CHECK(op_layer_set_scalar(ctx, doc, tx, 3u, OP_CHANNEL_EMISSION, 2.0f) == OP_OK);
    CHECK(op_layer_set_opacity(ctx, doc, tx, 3u, 1.0f) == OP_OK);
    CHECK(op_layer_reorder(ctx, doc, tx, 3u, 2u) == OP_OK); /* back on top */
    CHECK(op_transaction_commit(ctx, doc, tx) == OP_OK);
    CHECK(op_evaluate_request(ctx, doc, &req) == OP_OK);
    CHECK(op_composite_read(ctx, doc, 5u, OP_CHANNEL_EMISSION, 4u, pixels,
                            sizeof(pixels) / sizeof(float)) == OP_OK);
    {
        const float hdr[4] = {2.0f, 2.0f, 2.0f, 1.0f};
        CHECK(check_uniform(pixels, 16u, hdr) == 0);
    }

    /* Fixture E: read-path validation. */
    CHECK(op_composite_read(ctx, doc, 5u, 99u, 4u, pixels, sizeof(pixels) / sizeof(float)) ==
          OP_INVALID_ARGUMENT);
    CHECK(op_composite_read(ctx, doc, 5u, OP_CHANNEL_BASE_COLOR, 0u, pixels,
                            sizeof(pixels) / sizeof(float)) == OP_INVALID_ARGUMENT);
    CHECK(op_composite_read(ctx, doc, 5u, OP_CHANNEL_BASE_COLOR, 4u, pixels, 15u) ==
          OP_INVALID_ARGUMENT);
    CHECK(op_composite_read(ctx, doc, 999u, OP_CHANNEL_BASE_COLOR, 4u, pixels,
                            sizeof(pixels) / sizeof(float)) == OP_STALE_HANDLE);
    CHECK(op_composite_read(NULL, doc, 5u, OP_CHANNEL_BASE_COLOR, 4u, pixels,
                            sizeof(pixels) / sizeof(float)) == OP_INVALID_ARGUMENT);

    CHECK_CLOSE(pixels[0], 2.0f); /* last successful read left HDR in place */
    CHECK(op_document_destroy(ctx, doc) == OP_OK);

    /* Fixture F: MULTIPLY ported from blend_modes.blend (W3C separable).
     * gray * (0.8, 0.6, 0.4) = (0.4, 0.3, 0.2); at 0.5 coverage the
     * midpoint (0.45, 0.4, 0.35). */
    {
        op_document_t *mul = NULL;
        const float src[4] = {0.8f, 0.6f, 0.4f, 1.0f};
        const float full[4] = {0.4f, 0.3f, 0.2f, 1.0f};
        const float half[4] = {0.45f, 0.4f, 0.35f, 1.0f};
        size_t snap_size = 0u;
        float snap[1024];
        CHECK(op_document_create(ctx, 6u, &mul) == OP_OK);
        CHECK(op_transaction_begin(ctx, mul, &tx) == OP_OK);
        CHECK(op_layer_add(ctx, mul, tx, 1u, OP_LAYER_TYPE_FILL) == OP_OK);
        CHECK(op_layer_add(ctx, mul, tx, 2u, OP_LAYER_TYPE_FILL) == OP_OK);
        CHECK(op_layer_set_color(ctx, mul, tx, 2u, OP_CHANNEL_BASE_COLOR, src) == OP_OK);
        CHECK(op_layer_set_blend(ctx, mul, tx, 2u, OP_BLEND_MULTIPLY) == OP_OK);
        CHECK(op_layer_set_blend(ctx, mul, tx, 2u, 99u) == OP_UNSUPPORTED);
        CHECK(op_transaction_commit(ctx, mul, tx) == OP_OK);
        CHECK(op_evaluate_request(ctx, mul, &req) == OP_OK);
        CHECK(op_composite_read(ctx, mul, 1u, OP_CHANNEL_BASE_COLOR, 4u, pixels,
                                sizeof(pixels) / sizeof(float)) == OP_OK);
        CHECK(check_uniform(pixels, 16u, full) == 0);
        CHECK(op_transaction_begin(ctx, mul, &tx) == OP_OK);
        CHECK(op_layer_set_opacity(ctx, mul, tx, 2u, 0.5f) == OP_OK);
        CHECK(op_transaction_commit(ctx, mul, tx) == OP_OK);
        CHECK(op_evaluate_request(ctx, mul, &req) == OP_OK);
        CHECK(op_composite_read(ctx, mul, 2u, OP_CHANNEL_BASE_COLOR, 4u, pixels,
                                sizeof(pixels) / sizeof(float)) == OP_OK);
        CHECK(check_uniform(pixels, 16u, half) == 0);
        /* Blend mode survives a snapshot round-trip. */
        CHECK(op_document_snapshot_size(ctx, mul, &snap_size) == OP_OK);
        CHECK(snap_size <= sizeof(snap));
        CHECK(op_document_snapshot_write(ctx, mul, snap, snap_size) == OP_OK);
        {
            op_document_t *back = NULL;
            CHECK(op_document_restore(ctx, snap, snap_size, &back) == OP_OK);
            CHECK(op_composite_read(ctx, back, 2u, OP_CHANNEL_BASE_COLOR, 4u, pixels,
                                    sizeof(pixels) / sizeof(float)) == OP_OK);
            CHECK(check_uniform(pixels, 16u, half) == 0);
            CHECK(op_document_destroy(ctx, back) == OP_OK);
        }
        CHECK(op_document_destroy(ctx, mul) == OP_OK);
    }

    /* Fixture G: OVERLAY exercises both branches per component.
     * base (0.8, 0.3, 0.5), src 0.6 -> (0.84, 0.36, 0.6, 1). */
    {
        op_document_t *ovl = NULL;
        const float base[4] = {0.8f, 0.3f, 0.5f, 1.0f};
        const float src[4] = {0.6f, 0.6f, 0.6f, 1.0f};
        const float want[4] = {0.84f, 0.36f, 0.6f, 1.0f};
        CHECK(op_document_create(ctx, 7u, &ovl) == OP_OK);
        CHECK(op_transaction_begin(ctx, ovl, &tx) == OP_OK);
        CHECK(op_layer_add(ctx, ovl, tx, 1u, OP_LAYER_TYPE_FILL) == OP_OK);
        CHECK(op_layer_add(ctx, ovl, tx, 2u, OP_LAYER_TYPE_FILL) == OP_OK);
        CHECK(op_layer_set_color(ctx, ovl, tx, 1u, OP_CHANNEL_BASE_COLOR, base) == OP_OK);
        CHECK(op_layer_set_color(ctx, ovl, tx, 2u, OP_CHANNEL_BASE_COLOR, src) == OP_OK);
        CHECK(op_layer_set_blend(ctx, ovl, tx, 2u, OP_BLEND_OVERLAY) == OP_OK);
        CHECK(op_transaction_commit(ctx, ovl, tx) == OP_OK);
        CHECK(op_evaluate_request(ctx, ovl, &req) == OP_OK);
        CHECK(op_composite_read(ctx, ovl, 1u, OP_CHANNEL_BASE_COLOR, 4u, pixels,
                                sizeof(pixels) / sizeof(float)) == OP_OK);
        CHECK(check_uniform(pixels, 16u, want) == 0);
        /* Fixture H: DISABLE skips the layer; the base shows through. */
        CHECK(op_transaction_begin(ctx, ovl, &tx) == OP_OK);
        CHECK(op_layer_set_blend(ctx, ovl, tx, 2u, OP_BLEND_DISABLE) == OP_OK);
        CHECK(op_transaction_commit(ctx, ovl, tx) == OP_OK);
        CHECK(op_evaluate_request(ctx, ovl, &req) == OP_OK);
        CHECK(op_composite_read(ctx, ovl, 2u, OP_CHANNEL_BASE_COLOR, 4u, pixels,
                                sizeof(pixels) / sizeof(float)) == OP_OK);
        CHECK(check_uniform(pixels, 16u, base) == 0);
        /* Fixture I: blend validation. */
        CHECK(op_layer_set_blend(ctx, ovl, 0u, 2u, OP_BLEND_NORMAL) == OP_STALE_HANDLE);
        CHECK(op_transaction_begin(ctx, ovl, &tx) == OP_OK);
        CHECK(op_layer_set_blend(ctx, ovl, tx, 999u, OP_BLEND_NORMAL) == OP_INVALID_ARGUMENT);
        CHECK(op_transaction_cancel(ctx, ovl, tx) == OP_OK);
        CHECK(op_document_destroy(ctx, ovl) == OP_OK);
    }

    op_context_destroy(ctx);
    printf("PASS test_composite\n");
    return 0;
}
