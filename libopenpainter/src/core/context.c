/* Context lifecycle, capabilities, metrics, diagnostics. */
#include <stdlib.h>
#include <string.h>

#include "../core/internal.h"

/* Library-wide single-threaded ABI; never reset while the library is loaded. */
static uint64_t context_counter = 0;

uint32_t op_get_abi_version(void) { return OP_ABI_VERSION; }

op_status_t op_query_capabilities(const op_context_t *ctx, op_capabilities_t *out) {
    if (ctx == NULL || out == NULL) {
        return OP_INVALID_ARGUMENT;
    }
    out->abi_version = OP_ABI_VERSION;
    out->max_texture_size = 8192u;
    out->has_gpu = 0u; /* CPU core slice; GPU backend lands after Gate 1. */
    out->reserved = 0u;
    return OP_OK;
}

op_status_t op_context_create(op_context_t **out_ctx) {
    op_context_t *ctx;
    if (out_ctx == NULL) {
        return OP_INVALID_ARGUMENT;
    }
    *out_ctx = NULL;
    if (context_counter == UINT64_MAX)
        return OP_RESOURCE_EXHAUSTED;
    ctx = (op_context_t *)calloc(1, sizeof(*ctx));
    if (ctx == NULL) {
        return OP_RESOURCE_EXHAUSTED;
    }
    ctx->last_error[0] = '\0';
    ctx->identity = ++context_counter;
    *out_ctx = ctx;
    return OP_OK;
}

void op_context_destroy(op_context_t *ctx) {
    if (ctx != NULL) {
        op_document_t *doc = ctx->documents;
        while (doc != NULL) {
            op_document_t *next = doc->next;
            free(doc);
            doc = next;
        }
    }
    free(ctx);
}

int op_document_valid(const op_context_t *ctx, const op_document_t *doc) {
    const op_document_t *entry;
    if (ctx == NULL || doc == NULL)
        return 0;
    for (entry = ctx->documents; entry != NULL; entry = entry->next) {
        if (entry == doc)
            return entry->alive;
    }
    return 0;
}

op_status_t op_get_metrics(const op_context_t *ctx, op_metrics_t *out) {
    if (ctx == NULL || out == NULL) {
        return OP_INVALID_ARGUMENT;
    }
    *out = ctx->metrics;
    return OP_OK;
}

/* At most size - 1 bytes of text, always terminated (MSVC deprecates strncpy). */
static void copy_text(char *buffer, size_t size, const char *text) {
    size_t length = strlen(text);
    if (length > size - 1u)
        length = size - 1u;
    memcpy(buffer, text, length);
    buffer[length] = '\0';
}

op_status_t op_copy_last_error(op_context_t *ctx, char *buffer, size_t size) {
    if (ctx == NULL || buffer == NULL || size == 0u) {
        return OP_INVALID_ARGUMENT;
    }
    copy_text(buffer, size, ctx->last_error);
    return OP_OK;
}

/* --- shared helpers --- */

op_status_t op_fail(op_context_t *ctx, op_status_t code, const char *message) {
    if (ctx != NULL && message != NULL) {
        copy_text(ctx->last_error, OP_LAST_ERROR_SIZE, message);
    }
    return code;
}

void op_clear_error(op_context_t *ctx) {
    if (ctx != NULL) {
        ctx->last_error[0] = '\0';
    }
}

int op_find_layer(const op_layer_t *layers, size_t count, uint64_t layer_id) {
    size_t i;
    for (i = 0; i < count; i++) {
        if (layers[i].id == layer_id) {
            return (int)i;
        }
    }
    return -1;
}

uint32_t op_checksum_fnv1a(const void *data, size_t size) {
    const unsigned char *bytes = (const unsigned char *)data;
    uint32_t hash = 2166136261u;
    size_t i;
    for (i = 0; i < size; i++) {
        hash ^= bytes[i];
        hash *= 16777619u;
    }
    return hash;
}
