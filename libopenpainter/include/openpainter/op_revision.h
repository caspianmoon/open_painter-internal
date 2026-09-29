#ifndef OP_REVISION_H
#define OP_REVISION_H
#include "op_api.h"
#ifdef __cplusplus
extern "C" {
#endif
/* GPU-independent immutable material revisions. Resource IDs are opaque host
 * registry tokens, never addresses. The host submits ordered GPU work on its
 * context thread before commit. Abort preserves the previous revision. */
typedef struct op_revisions op_revisions_t;
typedef struct op_gpu_frame {
    uint64_t instance, revision, lease, resources[8];
    uint32_t channel_mask, reserved;
} op_gpu_frame_t;
op_status_t op_revisions_create(op_revisions_t **out);
void op_revisions_destroy(op_revisions_t *state);
op_status_t op_revisions_begin(op_revisions_t *state, uint32_t channel_mask);
op_status_t op_revisions_stage(op_revisions_t *state, uint32_t channel, uint64_t resource);
op_status_t op_revisions_commit(op_revisions_t *state, uint64_t *revision);
void op_revisions_abort(op_revisions_t *state);
op_status_t op_revisions_acquire(op_revisions_t *state, op_gpu_frame_t *frame);
op_status_t op_revisions_release(op_revisions_t *state, const op_gpu_frame_t *frame);
uint32_t op_revisions_references(const op_revisions_t *state, uint64_t resource);
typedef struct op_publication {
    uint64_t resource;
    uint32_t channel, size;
} op_publication_t;
enum { OP_PUBLICATION_PREPARE = 0, OP_PUBLICATION_WRITE = 1, OP_PUBLICATION_RESTORE = 2 };
/* Synchronous host-context callbacks. PREPARE allocates/validates both mip
 * chains without visible writes. WRITE/RESTORE copy a single mip. The native
 * transaction preflights all channels, publishes, commits or restores every
 * touched channel. A failed restore returns OP_DEVICE_LOSS. Callbacks must not
 * reenter or destroy state. No GPU/host pointers are stored in frame records. */
typedef op_status_t (*op_publication_submit_t)(void *user, uint32_t action, uint32_t index,
                                               uint32_t level);
op_status_t op_revisions_publish(op_revisions_t *state, const op_publication_t *channels,
                                 uint32_t count, op_publication_submit_t submit, void *user,
                                 uint64_t *revision);
/* Same preflight/acceptance contract, but WRITE is called once (index/level 0)
 * for the entire prepared batch. The host may fuse compatible channel draws.
 * A failed WRITE restores every mip of every prepared channel. */
op_status_t op_revisions_publish_batched(op_revisions_t *state, const op_publication_t *channels,
                                         uint32_t count, op_publication_submit_t submit, void *user,
                                         uint64_t *revision);
#ifdef __cplusplus
}
#endif
#endif
