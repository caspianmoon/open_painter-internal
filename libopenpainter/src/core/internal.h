/* Internal document/context model (not part of the public ABI). */
#ifndef OPENPAINTER_OP_INTERNAL_H
#define OPENPAINTER_OP_INTERNAL_H

#include "openpainter/op_api.h"

#define OP_MAX_LAYERS 256u
#define OP_LAST_ERROR_SIZE 256u
#define OP_ALL_CHANNELS_MASK 0xFFu
#define OP_MAX_DOCUMENT_HANDLES 256u
#define OP_MAX_FRAMES 8u
#define OP_MAX_LEASES 64u

typedef struct op_frame {
    uint64_t revision;
    uint32_t references;
    int valid;
    float pixels[OP_CHANNEL_COUNT][4];
} op_frame_t;

typedef struct op_lease_record {
    uint64_t token;
    uint32_t frame;
} op_lease_record_t;

typedef struct op_layer {
    uint64_t id;
    uint32_t type;
    uint32_t enabled_mask;
    float opacity;  /* per-layer master opacity, 0..1; multiplies coverage */
    uint32_t blend; /* OP_BLEND_*; per-layer mode in this slice */
    /* Per-channel RGBA working values. Scalar edits write the data triple
     * and preserve alpha; color edits write all four. GPU compositing
     * replaces these placeholder semantics in the backend slice. */
    float values[OP_CHANNEL_COUNT][4];
} op_layer_t;

struct op_document {
    struct op_document *next;
    int alive;
    uint64_t id;
    uint64_t generation;
    uint64_t revision_ready;   /* latest completed (acquirable) revision */
    uint64_t revision_pending; /* latest committed (awaiting evaluation) */
    uint64_t open_tx;          /* 0 => no transaction open; staged[] invalid */
    uint64_t req_counter;
    uint64_t first_request;
    size_t layer_count;
    size_t staged_count;
    uint32_t latest_frame;
    op_frame_t frames[OP_MAX_FRAMES];
    op_lease_record_t leases[OP_MAX_LEASES];
    op_layer_t committed[OP_MAX_LAYERS];
    op_layer_t staged[OP_MAX_LAYERS];
};

struct op_context {
    uint64_t identity;
    op_document_t *documents;
    uint32_t document_handles;
    uint64_t generation_counter;
    uint64_t transaction_counter;
    uint64_t lease_counter;
    op_metrics_t metrics;
    char last_error[OP_LAST_ERROR_SIZE];
};

/* Largest possible snapshot for OP_MAX_LAYERS layers; also the hard cap
 * accepted by restore (bounds untrusted input before parsing). */
#define OP_SNAPSHOT_MAGIC 0x4F50534Eu /* 'OPSN' */
#define OP_SNAPSHOT_VERSION 4u        /* canonical little-endian, no unwritten padding */
#define OP_SNAPSHOT_HEADER_SIZE 40u
#define OP_SNAPSHOT_LAYER_SIZE 152u
#define OP_SNAPSHOT_MAX_SIZE                                                                       \
    (OP_SNAPSHOT_HEADER_SIZE + (size_t)OP_MAX_LAYERS * OP_SNAPSHOT_LAYER_SIZE + 4u)

op_status_t op_fail(op_context_t *ctx, op_status_t code, const char *message);
void op_clear_error(op_context_t *ctx);
int op_find_layer(const op_layer_t *layers, size_t count, uint64_t layer_id);
uint32_t op_checksum_fnv1a(const void *data, size_t size);
int op_document_valid(const op_context_t *ctx, const op_document_t *doc);
op_status_t op_fold(op_context_t *ctx, const op_document_t *doc, float pixels[OP_CHANNEL_COUNT][4]);
op_status_t op_reset_frames(op_context_t *ctx, op_document_t *doc);

#endif /* OPENPAINTER_OP_INTERNAL_H */
