/* Bounded compositing slice (P3 start): fill layers, NORMAL / MULTIPLY /
 * OVERLAY / DISABLE blend modes ported from evaluation/blend_modes.py.
 *
 * Backdrop per channel is the Python engine's authored default
 * (src/open_painter/constants.py DEFAULTS). Each layer composites
 * bottom-to-top with effective coverage a = opacity * src_alpha:
 *   out = base + a * (blend(base, src) - base)
 * with blend() per mode:
 *   NORMAL:   src
 *   MULTIPLY: base * src
 *   OVERLAY:  base <= 0.5 ? 2*base*src : 1 - 2*(1-base)*(1-src) (separable)
 *   DISABLE:  layer skipped entirely
 * Only OP_LAYER_TYPE_FILL layers with full enabled_mask exist in this
 * slice; anything else is OP_UNSUPPORTED rather than silently rendered.
 *
 * Only source alpha 1 is accepted; transparent coverage is explicitly
 * unsupported until the coverage/alpha separation port. The authored
 * OPACITY channel remains an independent data channel.
 *
 * Fills are spatially constant, so the result is size-independent; `size`
 * only controls how many texels are written. Resolution cap guards
 * against accidental multi-gigabyte fills from a bad caller.
 */
#include <math.h>
#include <string.h>

#include "../core/internal.h"

#define OP_COMPOSITE_MAX_SIZE 8192u

static const float backdrop[OP_CHANNEL_COUNT][4] = {
    {0.5f, 0.5f, 0.5f, 1.0f}, /* BASE_COLOR */
    {0.5f, 0.5f, 0.5f, 1.0f}, /* ROUGHNESS */
    {0.0f, 0.0f, 0.0f, 1.0f}, /* METALLIC */
    {0.5f, 0.5f, 1.0f, 1.0f}, /* NORMAL */
    {0.5f, 0.5f, 0.5f, 1.0f}, /* HEIGHT */
    {1.0f, 1.0f, 1.0f, 1.0f}, /* AO */
    {0.0f, 0.0f, 0.0f, 1.0f}, /* EMISSION */
    {1.0f, 1.0f, 1.0f, 1.0f}, /* OPACITY */
};

op_status_t op_composite_read(op_context_t *ctx, op_document_t *doc, uint64_t revision,
                              uint32_t channel, uint32_t size, float *out, size_t capacity) {
    float pixel[4];
    size_t i, texels;
    if (ctx == NULL || doc == NULL || out == NULL) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "composite_read: null argument");
    }
    if (doc != NULL && !op_document_valid(ctx, doc)) {
        return op_fail(ctx, OP_STALE_HANDLE, "document does not belong to this live context");
    }
    op_clear_error(ctx);
    if (channel >= OP_CHANNEL_COUNT) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "composite_read: bad channel");
    }
    if (size == 0u || size > OP_COMPOSITE_MAX_SIZE) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "composite_read: bad size");
    }
    texels = (size_t)size * (size_t)size;
    if (capacity < texels * 4u) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "composite_read: buffer too small");
    }
    {
        uint32_t f;
        for (f = 0; f < OP_MAX_FRAMES; ++f) {
            if (doc->frames[f].valid && doc->frames[f].revision == revision)
                break;
        }
        if (f == OP_MAX_FRAMES)
            return OP_STALE_HANDLE;
        memcpy(pixel, doc->frames[f].pixels[channel], sizeof(pixel));
    }
    for (i = 0; i < texels; i++) {
        memcpy(out + i * 4u, pixel, sizeof(pixel));
    }
    return OP_OK;
}

op_status_t op_fold(op_context_t *ctx, const op_document_t *doc,
                    float pixels[OP_CHANNEL_COUNT][4]) {
    uint32_t channel;
    size_t i;
    for (channel = 0; channel < OP_CHANNEL_COUNT; ++channel) {
        float *pixel = pixels[channel];
        memcpy(pixel, backdrop[channel], sizeof(float) * 4u);
        for (i = 0; i < doc->layer_count; i++) {
            const op_layer_t *layer = &doc->committed[i];
            const float *src;
            float a;
            int c;
            if (layer->type != OP_LAYER_TYPE_FILL) {
                return op_fail(ctx, OP_UNSUPPORTED,
                               "composite_read: only FILL layers in this slice");
            }
            if (layer->blend == OP_BLEND_DISABLE) {
                continue;
            }
            if (!(layer->enabled_mask & (1u << channel))) {
                continue;
            }
            src = layer->values[channel];
            a = layer->opacity * src[3];
            for (c = 0; c < 4; c++) {
                float mixed;
                switch (layer->blend) {
                case OP_BLEND_NORMAL:
                    mixed = src[c];
                    break;
                case OP_BLEND_MULTIPLY:
                    mixed = pixel[c] * src[c];
                    break;
                case OP_BLEND_OVERLAY:
                    mixed = (pixel[c] <= 0.5f) ? 2.0f * pixel[c] * src[c]
                                               : 1.0f - 2.0f * (1.0f - pixel[c]) * (1.0f - src[c]);
                    break;
                default:
                    return op_fail(ctx, OP_UNSUPPORTED, "composite_read: unported blend mode");
                }
                pixel[c] = pixel[c] + a * (mixed - pixel[c]);
                if (!isfinite(pixel[c])) {
                    return op_fail(ctx, OP_VALIDATION_FAILED, "composite produced non-finite data");
                }
            }
        }
    }
    return OP_OK;
}
