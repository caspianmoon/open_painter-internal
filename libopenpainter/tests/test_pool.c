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
    puts("PASS test_pool");
    return 0;
}
