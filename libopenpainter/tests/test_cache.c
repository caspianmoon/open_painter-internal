#include "openpainter/op_cache.h"
#include <assert.h>
int main(void) {
    op_cache_t *c;
    uint64_t out[4], protect = 1;
    uint32_t count;
    assert(op_cache_create(&c) == OP_OK);
    for (unsigned i = 1; i <= 4; i++)
        assert(op_cache_put(c, i, 100) == OP_OK);
    assert(op_cache_bytes(c) == 400);
    op_cache_touch(c, 2);
    assert(op_cache_trim(c, 200, &protect, 1, out, 4, &count) == OP_OK && count == 2 &&
           out[0] == 3 && out[1] == 4);
    assert(op_cache_bytes(c) == 200);
    op_cache_remove(c, 2);
    assert(op_cache_trim(c, 0, &protect, 1, out, 4, &count) == OP_OK && count == 0);
    assert(op_cache_trim(c, 0, NULL, 0, out, 4, &count) == OP_OK && count == 1 && out[0] == 1);
    assert(op_cache_bytes(c) == 0);
    op_cache_destroy(c);
    return 0;
}
