/* libopenpainter public C ABI (P2 revision infrastructure).
 *
 * Versioned, opaque-handle API. No Blender types, no GPU backend headers.
 * Single-threaded in this slice: all calls must come from one thread;
 * the Blender adapter will own the threading contract later (plan 7).
 *
 * Status codes distinguish invalid argument, stale handle (old generation),
 * unsupported capability, pending work, resource exhaustion, device loss,
 * and validation failure. op_copy_last_error retrieves optional context
 * detail; callers must interpret the returned status even without detail.
 */

#ifndef OPENPAINTER_OP_API_H
#define OPENPAINTER_OP_API_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define OP_ABI_VERSION 14u

/* Channel indices follow src/open_painter/constants.py CHANNELS order. */
#define OP_CHANNEL_BASE_COLOR 0u
#define OP_CHANNEL_ROUGHNESS 1u
#define OP_CHANNEL_METALLIC 2u
#define OP_CHANNEL_NORMAL 3u
#define OP_CHANNEL_HEIGHT 4u
#define OP_CHANNEL_AO 5u
#define OP_CHANNEL_EMISSION 6u
#define OP_CHANNEL_OPACITY 7u
#define OP_CHANNEL_COUNT 8u

#define OP_LAYER_TYPE_FILL 1u

/* Modes of the bounded constant-fill reference document API. Production GPU
 * materials use OP_IR_BLEND, which supports all 28 existing blend modes. */
#define OP_BLEND_NORMAL 0u
#define OP_BLEND_MULTIPLY 1u
#define OP_BLEND_OVERLAY 2u
#define OP_BLEND_DISABLE 3u

typedef enum op_status {
    OP_OK = 0,
    OP_INVALID_ARGUMENT = 1,
    OP_STALE_HANDLE = 2,
    OP_UNSUPPORTED = 3,
    OP_PENDING = 4,
    OP_RESOURCE_EXHAUSTED = 5,
    OP_DEVICE_LOSS = 6,
    OP_VALIDATION_FAILED = 7
} op_status_t;

typedef struct op_context op_context_t;
typedef struct op_document op_document_t;

typedef struct op_capabilities {
    uint32_t abi_version;
    uint32_t max_texture_size;
    uint32_t has_gpu; /* 0 in this CPU-core slice; GPU backend lands after Gate 1. */
    uint32_t reserved;
} op_capabilities_t;

/* Lease to one complete immutable material revision. The holder must not
 * interpret it as texture memory; GPU resource handles arrive with the
 * Gate 1 backend. A lease from an older generation is stale. */
typedef struct op_frame_lease {
    uint64_t document_id;
    uint64_t generation;
    uint64_t revision;
    uint32_t channel_mask; /* bit i set => channel i current in this revision */
    uint32_t reserved;
    uint64_t lease_id;   /* unique within this context, release exactly once */
    uint64_t context_id; /* prevents release against a different context */
} op_frame_lease_t;

typedef struct op_metrics {
    uint64_t documents_created;
    uint64_t commits;
    uint64_t cancels;
    uint64_t evaluations;
    uint64_t frames_acquired;
    uint64_t frames_released;
} op_metrics_t;

/* ABI + capabilities */
uint32_t op_get_abi_version(void);
op_status_t op_query_capabilities(const op_context_t *ctx, op_capabilities_t *out);

/* Context lifecycle. Destroy releases every document, including tombstones.
 * Never use a context after destroy. Document pointers are checked without
 * dereferencing unowned memory. Up to 256 document handles per context;
 * destroyed handles remain tombstones until context close (no pointer ABA).
 * CPU frame storage is bounded to 8 revisions and 64 leases per document.
 * These leases cover CPU data only, not host GPU completion. */
op_status_t op_context_create(op_context_t **out_ctx);
void op_context_destroy(op_context_t *ctx);

/* Document lifecycle. Document IDs are caller-supplied stable IDs owned by
 * the Python adapter; the core never invents them. */
op_status_t op_document_create(op_context_t *ctx, uint64_t document_id, op_document_t **out_doc);
op_status_t op_document_destroy(op_context_t *ctx, op_document_t *doc);

/* Undo/load barrier: starts a new runtime generation. Outstanding leases
 * become stale; uncommitted transaction state is discarded. */
op_status_t op_document_new_generation(op_context_t *ctx, op_document_t *doc);

/* Transactions: exactly one open at a time per document. Edits are staged
 * until commit; cancel discards them. Commit advances the pending revision. */
op_status_t op_transaction_begin(op_context_t *ctx, op_document_t *doc, uint64_t *out_tx_id);
op_status_t op_transaction_commit(op_context_t *ctx, op_document_t *doc, uint64_t tx_id);
op_status_t op_transaction_cancel(op_context_t *ctx, op_document_t *doc, uint64_t tx_id);

/* Structure + numeric edits. Valid only inside the open transaction.
 * This bounded slice accepts opaque source colors only (rgba[3] == 1). */
op_status_t op_layer_add(op_context_t *ctx, op_document_t *doc, uint64_t tx_id, uint64_t layer_id,
                         uint32_t layer_type);
op_status_t op_layer_set_scalar(op_context_t *ctx, op_document_t *doc, uint64_t tx_id,
                                uint64_t layer_id, uint32_t channel, float value);
op_status_t op_layer_set_color(op_context_t *ctx, op_document_t *doc, uint64_t tx_id,
                               uint64_t layer_id, uint32_t channel, const float rgba[4]);
op_status_t op_layer_set_opacity(op_context_t *ctx, op_document_t *doc, uint64_t tx_id,
                                 uint64_t layer_id, float opacity);
op_status_t op_layer_set_blend(op_context_t *ctx, op_document_t *doc, uint64_t tx_id,
                               uint64_t layer_id, uint32_t blend_mode);
op_status_t op_layer_reorder(op_context_t *ctx, op_document_t *doc, uint64_t tx_id,
                             uint64_t layer_id, uint32_t new_index);

/* Evaluation (CPU core: folds committed state synchronously; the GPU
 * scheduler queues here in the backend slice). */
op_status_t op_evaluate_request(op_context_t *ctx, op_document_t *doc, uint64_t *out_request_id);
op_status_t op_poll_completions(op_context_t *ctx, op_document_t *doc, uint64_t request_id,
                                int *out_ready);

/* Frame leases to the latest completed revision. No completed revision yet
 * (impossible after create, which completes revision 0) would be OP_PENDING.
 * Releasing a lease from an older generation is OP_STALE_HANDLE. */
op_status_t op_frame_acquire(op_context_t *ctx, op_document_t *doc, op_frame_lease_t *out_lease);
op_status_t op_frame_release(op_context_t *ctx, op_document_t *doc, const op_frame_lease_t *lease);

/* CPU read path for tests, diagnostics, and explicit export (plan 12).
 * Composites the committed stack at `size` x `size` into `out` (must hold
 * size*size*4 floats) for one channel of the latest completed revision.
 * Leased revisions remain immutable; an evicted revision is OP_STALE_HANDLE. This is the read path,
 * never the display path. */
op_status_t op_composite_read(op_context_t *ctx, op_document_t *doc, uint64_t revision,
                              uint32_t channel, uint32_t size, float *out, size_t capacity);

/* Versioned snapshot for save/load. Two-step size protocol: call with
 * NULL/0 to get the required size. Restore starts a new generation. */
op_status_t op_document_snapshot_size(op_context_t *ctx, const op_document_t *doc,
                                      size_t *out_size);
op_status_t op_document_snapshot_write(op_context_t *ctx, const op_document_t *doc, void *buffer,
                                       size_t size);
op_status_t op_document_restore(op_context_t *ctx, const void *buffer, size_t size,
                                op_document_t **out_doc);

/* Diagnostics */
op_status_t op_get_metrics(const op_context_t *ctx, op_metrics_t *out);
op_status_t op_copy_last_error(op_context_t *ctx, char *buffer, size_t size);

#ifdef __cplusplus
}
#endif

#endif /* OPENPAINTER_OP_API_H */
