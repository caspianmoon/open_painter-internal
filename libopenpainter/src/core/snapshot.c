/* Canonical little-endian snapshot v4: 40-byte header, 152-byte layer,
 * four-byte FNV-1a checksum. IEEE-754 binary32 values; reserved bytes zero.
 * Captures committed recipe revision, even before evaluation. */
#include <float.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "../core/internal.h"

_Static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24,
               "snapshot format requires IEEE binary32");

static void write_u32(unsigned char *dst, uint32_t v) {
    dst[0] = (unsigned char)(v & 0xFFu);
    dst[1] = (unsigned char)((v >> 8) & 0xFFu);
    dst[2] = (unsigned char)((v >> 16) & 0xFFu);
    dst[3] = (unsigned char)((v >> 24) & 0xFFu);
}

static void write_u64(unsigned char *dst, uint64_t v) {
    int i;
    for (i = 0; i < 8; i++) {
        dst[i] = (unsigned char)((v >> (8 * i)) & 0xFFu);
    }
}

static uint32_t read_u32(const unsigned char *src) {
    return (uint32_t)src[0] | ((uint32_t)src[1] << 8) | ((uint32_t)src[2] << 16) |
           ((uint32_t)src[3] << 24);
}

static uint64_t read_u64(const unsigned char *src) {
    uint64_t v = 0u;
    int i;
    for (i = 0; i < 8; i++) {
        v |= (uint64_t)src[i] << (8 * i);
    }
    return v;
}

op_status_t op_document_snapshot_size(op_context_t *ctx, const op_document_t *doc,
                                      size_t *out_size) {
    if (ctx == NULL || doc == NULL || out_size == NULL) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "snapshot_size: null argument");
    }
    if (!op_document_valid(ctx, doc)) {
        return op_fail(ctx, OP_STALE_HANDLE, "document does not belong to this live context");
    }
    op_clear_error(ctx);
    *out_size = OP_SNAPSHOT_HEADER_SIZE + doc->layer_count * OP_SNAPSHOT_LAYER_SIZE + 4u;
    return OP_OK;
}

op_status_t op_document_snapshot_write(op_context_t *ctx, const op_document_t *doc, void *buffer,
                                       size_t size) {
    unsigned char *dst;
    size_t need, i, j, k;
    if (ctx == NULL || doc == NULL || buffer == NULL) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "snapshot_write: null argument");
    }
    if (!op_document_valid(ctx, doc)) {
        return op_fail(ctx, OP_STALE_HANDLE, "document does not belong to this live context");
    }
    op_clear_error(ctx);
    need = OP_SNAPSHOT_HEADER_SIZE + doc->layer_count * OP_SNAPSHOT_LAYER_SIZE + 4u;
    if (size < need) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "snapshot_write: buffer too small");
    }
    memset(buffer, 0, need);
    dst = (unsigned char *)buffer;
    write_u32(dst + 0, OP_SNAPSHOT_MAGIC);
    write_u32(dst + 4, OP_SNAPSHOT_VERSION);
    write_u64(dst + 8, doc->id);
    write_u64(dst + 16, doc->generation);
    write_u64(dst + 24, doc->revision_pending);
    write_u32(dst + 32, (uint32_t)doc->layer_count);
    dst += OP_SNAPSHOT_HEADER_SIZE;
    for (i = 0; i < doc->layer_count; i++) {
        const op_layer_t *layer = &doc->committed[i];
        uint32_t opacity_bits;
        write_u64(dst + 0, layer->id);
        write_u32(dst + 8, layer->type);
        write_u32(dst + 12, layer->enabled_mask);
        memcpy(&opacity_bits, &layer->opacity, sizeof(opacity_bits));
        write_u32(dst + 16, opacity_bits);
        write_u32(dst + 20, layer->blend);
        for (j = 0; j < OP_CHANNEL_COUNT; j++) {
            for (k = 0; k < 4u; k++) {
                uint32_t bits;
                memcpy(&bits, &layer->values[j][k], sizeof(bits));
                write_u32(dst + 24 + (j * 4u + k) * 4u, bits);
            }
        }
        dst += OP_SNAPSHOT_LAYER_SIZE;
    }
    write_u32(dst, op_checksum_fnv1a(buffer, need - 4u));
    return OP_OK;
}

op_status_t op_document_restore(op_context_t *ctx, const void *buffer, size_t size,
                                op_document_t **out_doc) {
    const unsigned char *src;
    uint64_t doc_id, generation, revision;
    uint32_t layers;
    size_t need, i, j, k;
    op_document_t *doc;
    if (ctx == NULL || buffer == NULL || out_doc == NULL) {
        return op_fail(ctx, OP_INVALID_ARGUMENT, "restore: null argument");
    }
    *out_doc = NULL;
    op_clear_error(ctx);
    if (size < OP_SNAPSHOT_HEADER_SIZE + 4u || size > OP_SNAPSHOT_MAX_SIZE) {
        return op_fail(ctx, OP_VALIDATION_FAILED, "restore: bad size");
    }
    src = (const unsigned char *)buffer;
    if (read_u32(src) != OP_SNAPSHOT_MAGIC) {
        return op_fail(ctx, OP_VALIDATION_FAILED, "restore: bad magic");
    }
    if (read_u32(src + 4) != OP_SNAPSHOT_VERSION) {
        return op_fail(ctx, OP_VALIDATION_FAILED, "restore: bad version");
    }
    if (read_u32(src + (size - 4u)) != op_checksum_fnv1a(buffer, size - 4u)) {
        return op_fail(ctx, OP_VALIDATION_FAILED, "restore: bad checksum");
    }
    doc_id = read_u64(src + 8);
    generation = read_u64(src + 16);
    revision = read_u64(src + 24);
    layers = read_u32(src + 32);
    if (doc_id == 0u || generation == 0u || generation == UINT64_MAX || revision == UINT64_MAX ||
        read_u32(src + 36) != 0u || layers > OP_MAX_LAYERS) {
        return op_fail(ctx, OP_VALIDATION_FAILED, "restore: bad header");
    }
    need = OP_SNAPSHOT_HEADER_SIZE + (size_t)layers * OP_SNAPSHOT_LAYER_SIZE + 4u;
    if (need != size) {
        return op_fail(ctx, OP_VALIDATION_FAILED, "restore: size mismatch");
    }
    doc = (op_document_t *)calloc(1, sizeof(*doc));
    if (doc == NULL) {
        return op_fail(ctx, OP_RESOURCE_EXHAUSTED, "restore: out of memory");
    }
    doc->id = doc_id;
    doc->generation = generation + 1u; /* load starts a new generation */
    if (doc->generation == 0u) {
        doc->generation = 1u;
    }
    doc->revision_ready = revision;
    doc->revision_pending = revision;
    doc->layer_count = layers;
    src += OP_SNAPSHOT_HEADER_SIZE;
    for (i = 0; i < layers; i++) {
        op_layer_t *layer = &doc->committed[i];
        layer->id = read_u64(src + 0);
        layer->type = read_u32(src + 8);
        layer->enabled_mask = read_u32(src + 12);
        {
            uint32_t opacity_bits = read_u32(src + 16);
            memcpy(&layer->opacity, &opacity_bits, sizeof(layer->opacity));
        }
        if (layer->id == 0u || layer->type != OP_LAYER_TYPE_FILL ||
            (layer->enabled_mask & ~OP_ALL_CHANNELS_MASK) != 0u) {
            free(doc);
            return op_fail(ctx, OP_VALIDATION_FAILED, "restore: bad layer");
        }
        if (layer->opacity != layer->opacity || layer->opacity < 0.0f || layer->opacity > 1.0f) {
            free(doc);
            return op_fail(ctx, OP_VALIDATION_FAILED, "restore: bad opacity");
        }
        layer->blend = read_u32(src + 20);
        if (layer->blend != OP_BLEND_NORMAL && layer->blend != OP_BLEND_MULTIPLY &&
            layer->blend != OP_BLEND_OVERLAY && layer->blend != OP_BLEND_DISABLE) {
            free(doc);
            return op_fail(ctx, OP_VALIDATION_FAILED, "restore: bad blend");
        }
        for (j = 0; j < OP_CHANNEL_COUNT; j++) {
            for (k = 0; k < 4u; k++) {
                uint32_t bits = read_u32(src + 24 + (j * 4u + k) * 4u);
                memcpy(&layer->values[j][k], &bits, sizeof(bits));
                if (!isfinite(layer->values[j][k]) || layer->values[j][k] > 1e30f ||
                    layer->values[j][k] < -1e30f) {
                    free(doc);
                    return op_fail(ctx, OP_VALIDATION_FAILED, "restore: invalid channel value");
                }
            }
        }
        for (j = 0; j < OP_CHANNEL_COUNT; ++j) {
            if (layer->values[j][3] != 1.0f) {
                free(doc);
                return op_fail(ctx, OP_VALIDATION_FAILED,
                               "restore: unsupported transparent coverage");
            }
        }
        if (op_find_layer(doc->committed, i, layer->id) >= 0) {
            free(doc);
            return op_fail(ctx, OP_VALIDATION_FAILED, "restore: duplicate layer");
        }
        src += OP_SNAPSHOT_LAYER_SIZE;
    }
    if (op_reset_frames(ctx, doc) != OP_OK) {
        free(doc);
        return op_fail(ctx, OP_VALIDATION_FAILED, "restore: invalid composite");
    }
    if (ctx->document_handles >= OP_MAX_DOCUMENT_HANDLES || ctx->generation_counter == UINT64_MAX) {
        free(doc);
        return OP_RESOURCE_EXHAUSTED;
    }
    if (ctx->generation_counter < generation)
        ctx->generation_counter = generation;
    doc->generation = ++ctx->generation_counter;
    doc->next = ctx->documents;
    ctx->documents = doc;
    ctx->document_handles++;
    doc->alive = 1;
    *out_doc = doc;
    ctx->metrics.documents_created++;
    return OP_OK;
}
