#include "openpainter/op_cache.h"
#include <stdlib.h>
#define MAX_ENTRIES 65536u
typedef struct entry {
    uint64_t key, bytes, age;
} entry;
struct op_cache {
    entry *entries;
    uint32_t count, capacity;
    uint64_t bytes, clock;
};
op_status_t op_cache_create(op_cache_t **out) {
    if (!out)
        return OP_INVALID_ARGUMENT;
    *out = calloc(1, sizeof(**out));
    return *out ? OP_OK : OP_RESOURCE_EXHAUSTED;
}
void op_cache_destroy(op_cache_t *c) {
    if (c) {
        free(c->entries);
        free(c);
    }
}
static entry *find(op_cache_t *c, uint64_t key) {
    for (uint32_t i = 0; i < c->count; i++)
        if (c->entries[i].key == key)
            return &c->entries[i];
    return NULL;
}
op_status_t op_cache_put(op_cache_t *c, uint64_t key, uint64_t bytes) {
    if (!c || !key || bytes > UINT64_MAX / MAX_ENTRIES)
        return OP_INVALID_ARGUMENT;
    entry *e = find(c, key);
    if (!e) {
        for (uint32_t i = 0; i < c->count; i++)
            if (!c->entries[i].key) {
                e = &c->entries[i];
                break;
            }
        if (!e) {
            if (c->count == MAX_ENTRIES)
                return OP_RESOURCE_EXHAUSTED;
            if (c->count == c->capacity) {
                uint32_t n = c->capacity ? c->capacity * 2 : 64;
                entry *p = realloc(c->entries, n * sizeof(*p));
                if (!p)
                    return OP_RESOURCE_EXHAUSTED;
                c->entries = p;
                c->capacity = n;
            }
            e = &c->entries[c->count++];
            *e = (entry){0};
        }
    }
    c->bytes -= e->bytes;
    *e = (entry){key, bytes, ++c->clock};
    c->bytes += bytes;
    return OP_OK;
}
void op_cache_remove(op_cache_t *c, uint64_t key) {
    if (!c || !key)
        return;
    entry *e = find(c, key);
    if (e) {
        c->bytes -= e->bytes;
        *e = (entry){0};
    }
}
void op_cache_touch(op_cache_t *c, uint64_t key) {
    if (!c || !key)
        return;
    entry *e = find(c, key);
    if (e)
        e->age = ++c->clock;
}
uint64_t op_cache_bytes(const op_cache_t *c) { return c ? c->bytes : 0; }
op_status_t op_cache_trim(op_cache_t *c, uint64_t budget, const uint64_t *keys, uint32_t count,
                          uint64_t *out, uint32_t capacity, uint32_t *written) {
    if (!c || !written || count > MAX_ENTRIES || (count && !keys) || (c->count && !out))
        return OP_INVALID_ARGUMENT;
    *written = 0;
    if (capacity < c->count)
        return OP_RESOURCE_EXHAUSTED;
    while (c->bytes > budget) {
        entry *victim = NULL;
        for (uint32_t i = 0; i < c->count; i++) {
            entry *e = &c->entries[i];
            if (!e->key)
                continue;
            int protect = 0;
            for (uint32_t k = 0; k < count; k++)
                if (keys[k] == e->key) {
                    protect = 1;
                    break;
                }
            if (!protect && (!victim || e->age < victim->age))
                victim = e;
        }
        if (!victim)
            break;
        out[(*written)++] = victim->key;
        c->bytes -= victim->bytes;
        *victim = (entry){0};
    }
    return OP_OK;
}
