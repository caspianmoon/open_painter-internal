#ifndef OP_POOL_H
#define OP_POOL_H
#include "op_api.h"
#ifdef __cplusplus
extern "C" {
#endif
/* Host objects are identified by opaque tokens, never pointers. C owns lease,
 * reuse and eviction decisions. Host callbacks only retain/release API objects.
 * Calls and callbacks are synchronous on the GPU context's owning thread.
 * Events: 0 retire, 1 acquire, 2 return to pool. Callbacks must not reenter. */
typedef struct op_pool op_pool_t;
typedef void (*op_pool_event_fn)(void *user, uint64_t token, uint32_t event);
typedef struct op_pool_memory {
    uint64_t leased, pooled, allocations, reuses, evictions;
} op_pool_memory_t;
op_status_t op_pool_create(op_pool_event_fn event, void *user, op_pool_t **out);
void op_pool_destroy(op_pool_t *pool);
op_status_t op_pool_limits(op_pool_t *pool, uint64_t bytes, uint32_t spares);
/* Global managed-texture cap (leased + pooled), shared by every texture set.
 * Budget reduction cannot invalidate live leases. Admit evicts only free pool
 * entries and must run before host allocation. Register checks again. Neither
 * function reserves bytes across calls; submission is on one context thread. */
op_status_t op_pool_budget(op_pool_t *pool, uint64_t bytes);
op_status_t op_pool_admit(op_pool_t *pool, uint32_t width, uint32_t height,
                          uint32_t bytes_per_pixel);
op_status_t op_pool_register(op_pool_t *pool, uint32_t width, uint32_t height, uint32_t format,
                             uint32_t bytes_per_pixel, uint64_t *token);
op_status_t op_pool_acquire(op_pool_t *pool, uint32_t width, uint32_t height, uint32_t format,
                            uint64_t *token);
op_status_t op_pool_release(op_pool_t *pool, uint64_t token);
op_status_t op_pool_discard(op_pool_t *pool, uint64_t token);
op_status_t op_pool_trim(op_pool_t *pool, uint32_t spares);
op_status_t op_pool_memory(const op_pool_t *pool, op_pool_memory_t *out);
#ifdef __cplusplus
}
#endif
#endif
