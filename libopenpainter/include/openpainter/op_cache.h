#ifndef OP_CACHE_H
#define OP_CACHE_H
#include "op_api.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct op_cache op_cache_t;
op_status_t op_cache_create(op_cache_t **out);
void op_cache_destroy(op_cache_t *cache);
op_status_t op_cache_put(op_cache_t *cache, uint64_t key, uint64_t bytes);
void op_cache_remove(op_cache_t *cache, uint64_t key);
void op_cache_touch(op_cache_t *cache, uint64_t key);
uint64_t op_cache_bytes(const op_cache_t *cache);
op_status_t op_cache_trim(op_cache_t *cache, uint64_t budget, const uint64_t *protected_keys,
                          uint32_t protected_count, uint64_t *evicted, uint32_t capacity,
                          uint32_t *written);
#ifdef __cplusplus
}
#endif
#endif
