#ifndef OP_CHANNELS_H
#define OP_CHANNELS_H
#include "op_api.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct op_channels op_channels_t;
enum { OP_CHANNEL_CONSTANT = 0, OP_CHANNEL_PROGRAM = 1 };
enum { OP_CHANNEL_FORCE = 1, OP_CHANNEL_PRECISE = 2, OP_CHANNEL_PAD = 4 };
typedef struct op_channel_request {
    uint64_t revision, target; /* native graph stamp and opaque host registry token */
    double constant[4];
    uint32_t channel, kind, size, flags;
} op_channel_request_t;
enum { OP_CHANNEL_RENDER = 0, OP_CHANNEL_COMMIT = 1 };
/* RENDER stages one changed channel without touching visible images. COMMIT
 * receives the complete changed-channel mask and must publish atomically, or
 * restore the old visible revision before returning an error. Both callbacks
 * run synchronously on the host context thread. On failure the host discards
 * staged resources; C retains every previously accepted channel description.
 * Requests are copied before callbacks. Duplicate channels are rejected.
 * Unrequested channels retain their accepted state. Zero requests is a no-op.
 * Reentry, reset and destruction during submission return OP_PENDING. */
typedef op_status_t (*op_channels_submit_fn)(void *user, uint32_t action, uint32_t channel_or_mask);
op_status_t op_channels_create(op_channels_t **out);
op_status_t op_channels_destroy(op_channels_t *state);
op_status_t op_channels_reset(op_channels_t *state);
op_status_t op_channels_execute(op_channels_t *state, const op_channel_request_t *requests,
                                uint32_t count, op_channels_submit_fn submit, void *user,
                                uint32_t *written_mask);
#ifdef __cplusplus
}
#endif
#endif
