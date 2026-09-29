#include "openpainter/op_pool.h"
#include <assert.h>
#include <stdio.h>
static unsigned events[3];
static void event(void *unused, uint64_t token, uint32_t action) {
    (void)unused;
    assert(token && action < 3);
    events[action]++;
}
int main(void) {
    op_pool_t *p;
    uint64_t a, b, c, got;
    op_pool_memory_t m;
    assert(op_pool_create(event, NULL, &p) == OP_OK);
    assert(op_pool_limits(p, 2048, 2) == OP_OK);
    assert(op_pool_register(p, 8, 8, 1, 16, &a) == OP_OK);
    assert(op_pool_register(p, 8, 8, 1, 16, &b) == OP_OK);
    assert(op_pool_register(p, 8, 8, 1, 16, &c) == OP_OK);
    assert(op_pool_release(p, a) == OP_OK && op_pool_release(p, a) == OP_OK);
    assert(op_pool_release(p, b) == OP_OK && op_pool_release(p, c) == OP_OK);
    assert(events[0] == 1 && events[2] == 3);
    assert(op_pool_discard(p, a) == OP_STALE_HANDLE);
    assert(op_pool_acquire(p, 8, 8, 1, &got) == OP_OK && got == c);
    assert(op_pool_trim(p, 0) == OP_OK);
    assert(op_pool_memory(p, &m) == OP_OK && m.leased == 1024 && m.pooled == 0);
    assert(op_pool_acquire(p, 8, 8, 1, &got) == OP_OK && !got);
    assert(op_pool_limits(p, 0, 0) == OP_OK && op_pool_release(p, c) == OP_OK);
    assert(op_pool_memory(p, &m) == OP_OK && !m.leased && !m.pooled);
    assert(op_pool_register(p, 0, 8, 1, 16, &a) == OP_INVALID_ARGUMENT);
    assert(op_pool_register(p, 8, 8, 1, 16, &a) == OP_OK && a != b && a != c);
    op_pool_destroy(p);
    assert(events[0] == 4);
    /* Admission caps leased + pooled globally, never evicts a live lease. */
    assert(op_pool_create(event, NULL, &p) == OP_OK);
    assert(op_pool_limits(p, 8192, 4) == OP_OK);
    assert(op_pool_budget(p, 3072) == OP_OK);
    assert(op_pool_register(p, 8, 8, 1, 16, &a) == OP_OK);
    assert(op_pool_register(p, 8, 8, 1, 16, &b) == OP_OK);
    assert(op_pool_register(p, 8, 8, 1, 16, &c) == OP_OK);
    assert(op_pool_admit(p, 8, 8, 16) == OP_RESOURCE_EXHAUSTED);
    assert(op_pool_release(p, b) == OP_OK);
    assert(op_pool_admit(p, 8, 8, 16) == OP_OK);
    assert(op_pool_discard(p, b) == OP_STALE_HANDLE);
    assert(op_pool_memory(p, &m) == OP_OK && m.leased == 2048 && !m.pooled);
    assert(op_pool_register(p, 16, 16, 1, 16, &got) == OP_RESOURCE_EXHAUSTED && !got);
    assert(op_pool_budget(p, 1024) == OP_OK); /* existing live work is preserved */
    assert(op_pool_admit(p, 1, 1, 4) == OP_RESOURCE_EXHAUSTED);
    assert(op_pool_release(p, c) == OP_OK);
    assert(op_pool_memory(p, &m) == OP_OK && m.leased == 1024 && !m.pooled);
    assert(op_pool_discard(p, c) == OP_STALE_HANDLE && op_pool_discard(p, a) == OP_OK);
    assert(op_pool_admit(p, 8, 8, 16) == OP_OK);
    assert(op_pool_admit(p, 0, 8, 16) == OP_INVALID_ARGUMENT);
    assert(op_pool_admit(p, 32769, 8, 16) == OP_INVALID_ARGUMENT);
    assert(op_pool_admit(p, 8, 8, 17) == OP_INVALID_ARGUMENT);
    op_pool_destroy(p);
    puts("PASS test_pool");
    return 0;
}
