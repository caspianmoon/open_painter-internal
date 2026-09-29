#include "openpainter/op_pool.h"
#include <stdlib.h>
#include <string.h>
#define POOL_MAX 65536u
typedef struct entry {
    uint64_t token, bytes, age;
    uint32_t width, height, format, state;
} entry;
struct op_pool {
    entry *entries;
    uint32_t count, capacity, spares;
    uint64_t clock, serial, limit, budget;
    op_pool_memory_t memory;
    op_pool_event_fn event;
    void *user;
};
static int same(const entry *a, const entry *b) {
    return a->width == b->width && a->height == b->height && a->format == b->format;
}
static entry *find(op_pool_t *p, uint64_t token) {
    for (uint32_t i = 0; i < p->count; i++)
        if (p->entries[i].token == token)
            return &p->entries[i];
    return NULL;
}
static void notify(op_pool_t *p, entry *e, uint32_t event) {
    if (p->event)
        p->event(p->user, e->token, event);
}
static void retire(op_pool_t *p, entry *e) {
    if (e->state == 1)
        p->memory.leased -= e->bytes;
    if (e->state == 2) {
        p->memory.pooled -= e->bytes;
        p->memory.evictions++;
    }
    notify(p, e, 0);
    e->token = 0;
    e->state = 0;
}
static entry *oldest(op_pool_t *p, const entry *key) {
    entry *result = NULL;
    for (uint32_t i = 0; i < p->count; i++) {
        entry *e = &p->entries[i];
        if (e->state == 2 && (!key || same(e, key)) && (!result || e->age < result->age))
            result = e;
    }
    return result;
}
static uint32_t matching(op_pool_t *p, const entry *key) {
    uint32_t n = 0;
    for (uint32_t i = 0; i < p->count; i++)
        n += p->entries[i].state == 2 && same(&p->entries[i], key);
    return n;
}
op_status_t op_pool_create(op_pool_event_fn event, void *user, op_pool_t **out) {
    if (!out)
        return OP_INVALID_ARGUMENT;
    *out = calloc(1, sizeof(op_pool_t));
    if (!*out)
        return OP_RESOURCE_EXHAUSTED;
    (*out)->budget = UINT64_MAX;
    (*out)->event = event;
    (*out)->user = user;
    return OP_OK;
}
void op_pool_destroy(op_pool_t *p) {
    if (!p)
        return;
    for (uint32_t i = 0; i < p->count; i++)
        if (p->entries[i].state)
            retire(p, &p->entries[i]);
    free(p->entries);
    free(p);
}
op_status_t op_pool_limits(op_pool_t *p, uint64_t bytes, uint32_t spares) {
    if (!p)
        return OP_INVALID_ARGUMENT;
    p->limit = bytes;
    p->spares = spares;
    return op_pool_trim(p, spares);
}
op_status_t op_pool_budget(op_pool_t *p, uint64_t bytes) {
    if (!p)
        return OP_INVALID_ARGUMENT;
    p->budget = bytes;
    uint64_t available = bytes > p->memory.leased ? bytes - p->memory.leased : 0;
    while (p->memory.pooled > available)
        retire(p, oldest(p, NULL));
    return OP_OK;
}
op_status_t op_pool_admit(op_pool_t *p, uint32_t w, uint32_t h, uint32_t bpp) {
    if (!p || !w || !h || w > 32768 || h > 32768 || !bpp || bpp > 16)
        return OP_INVALID_ARGUMENT;
    uint64_t bytes = (uint64_t)w * h * bpp;
    if (p->memory.leased > p->budget || bytes > p->budget - p->memory.leased)
        return OP_RESOURCE_EXHAUSTED;
    while (p->memory.pooled > p->budget - p->memory.leased - bytes)
        retire(p, oldest(p, NULL));
    return OP_OK;
}
op_status_t op_pool_register(op_pool_t *p, uint32_t w, uint32_t h, uint32_t format, uint32_t bpp,
                             uint64_t *token) {
    entry *e = NULL;
    if (!p || !token || !w || !h || w > 32768 || h > 32768 || !bpp || bpp > 16)
        return OP_INVALID_ARGUMENT;
    *token = 0;
    if (p->serial == UINT64_MAX)
        return OP_RESOURCE_EXHAUSTED;
    op_status_t status = op_pool_admit(p, w, h, bpp);
    if (status != OP_OK)
        return status;
    for (uint32_t i = 0; i < p->count; i++)
        if (!p->entries[i].state) {
            e = &p->entries[i];
            break;
        }
    if (!e) {
        if (p->count == POOL_MAX)
            return OP_RESOURCE_EXHAUSTED;
        if (p->count == p->capacity) {
            uint32_t capacity = p->capacity ? p->capacity * 2 : 64;
            entry *values = realloc(p->entries, (size_t)capacity * sizeof(entry));
            if (!values)
                return OP_RESOURCE_EXHAUSTED;
            p->entries = values;
            p->capacity = capacity;
        }
        e = &p->entries[p->count++];
    }
    *e = (entry){++p->serial, (uint64_t)w * h * bpp, ++p->clock, w, h, format, 1};
    p->memory.leased += e->bytes;
    p->memory.allocations++;
    *token = e->token;
    return OP_OK;
}
op_status_t op_pool_acquire(op_pool_t *p, uint32_t w, uint32_t h, uint32_t format,
                            uint64_t *token) {
    entry *result = NULL;
    if (!p || !token || !w || !h)
        return OP_INVALID_ARGUMENT;
    *token = 0;
    for (uint32_t i = 0; i < p->count; i++) {
        entry *e = &p->entries[i];
        if (e->state == 2 && e->width == w && e->height == h && e->format == format &&
            (!result || e->age > result->age))
            result = e;
    }
    if (result) {
        result->state = 1;
        p->memory.pooled -= result->bytes;
        p->memory.leased += result->bytes;
        p->memory.reuses++;
        *token = result->token;
        notify(p, result, 1);
    }
    return OP_OK;
}
op_status_t op_pool_release(op_pool_t *p, uint64_t token) {
    entry *e;
    if (!p || !token)
        return OP_INVALID_ARGUMENT;
    e = find(p, token);
    if (!e || !e->state)
        return OP_STALE_HANDLE;
    if (e->state == 2)
        return OP_OK; /* idempotent return never aliases an active lease */
    if (e->bytes > p->limit || !p->spares) {
        retire(p, e);
        return OP_OK;
    }
    e->state = 2;
    e->age = ++p->clock;
    p->memory.leased -= e->bytes;
    p->memory.pooled += e->bytes;
    notify(p, e, 2);
    while (matching(p, e) > p->spares)
        retire(p, oldest(p, e));
    while (p->memory.pooled > p->limit)
        retire(p, oldest(p, NULL));
    uint64_t available = p->budget > p->memory.leased ? p->budget - p->memory.leased : 0;
    while (p->memory.pooled > available)
        retire(p, oldest(p, NULL));
    return OP_OK;
}
op_status_t op_pool_discard(op_pool_t *p, uint64_t token) {
    entry *e;
    if (!p || !token)
        return OP_INVALID_ARGUMENT;
    e = find(p, token);
    if (!e || !e->state)
        return OP_STALE_HANDLE;
    retire(p, e);
    return OP_OK;
}
op_status_t op_pool_trim(op_pool_t *p, uint32_t spares) {
    if (!p)
        return OP_INVALID_ARGUMENT;
    for (uint32_t i = 0; i < p->count; i++) {
        entry *e = &p->entries[i];
        if (e->state == 2)
            while (matching(p, e) > spares)
                retire(p, oldest(p, e));
    }
    while (p->memory.pooled > p->limit)
        retire(p, oldest(p, NULL));
    return OP_OK;
}
op_status_t op_pool_memory(const op_pool_t *p, op_pool_memory_t *out) {
    if (!p || !out)
        return OP_INVALID_ARGUMENT;
    *out = p->memory;
    return OP_OK;
}
