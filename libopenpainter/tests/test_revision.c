#include "openpainter/op_revision.h"
#include <assert.h>
int main(void) {
    op_revisions_t *s, *other;
    op_gpu_frame_t a, b;
    uint64_t r;
    assert(op_revisions_create(&s) == OP_OK);
    assert(op_revisions_create(&other) == OP_OK);
    assert(op_revisions_acquire(s, &a) == OP_PENDING);
    assert(op_revisions_begin(s, 255) == OP_OK);
    assert(op_revisions_stage(s, 0, 100) == OP_OK);
    assert(op_revisions_commit(s, &r) == OP_PENDING);
    for (unsigned c = 1; c < 8; c++)
        assert(op_revisions_stage(s, c, 100 + c) == OP_OK);
    assert(op_revisions_commit(s, &r) == OP_OK && r == 1);
    assert(op_revisions_acquire(s, &a) == OP_OK);
    assert(op_revisions_release(other, &a) == OP_STALE_HANDLE);
    assert(op_revisions_begin(s, 1) == OP_OK);
    assert(op_revisions_stage(s, 0, 200) == OP_OK);
    op_revisions_abort(s);
    assert(op_revisions_references(s, 200) == 0);
    assert(op_revisions_references(s, 100) > 0);
    assert(op_revisions_begin(s, 1) == OP_OK);
    assert(op_revisions_stage(s, 0, 300) == OP_OK);
    assert(op_revisions_commit(s, &r) == OP_OK && r == 2);
    assert(op_revisions_acquire(s, &b) == OP_OK && b.resources[0] == 300 && b.resources[1] == 101);
    assert(a.resources[0] == 100 && op_revisions_references(s, 100) > 0);
    assert(op_revisions_release(s, &a) == OP_OK);
    assert(op_revisions_release(s, &a) == OP_STALE_HANDLE);
    assert(op_revisions_references(s, 100) == 0);
    assert(op_revisions_release(s, &b) == OP_OK);
    op_revisions_destroy(s);
    op_revisions_destroy(other);
    return 0;
}
