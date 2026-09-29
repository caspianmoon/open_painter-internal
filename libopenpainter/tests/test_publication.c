#include "openpainter/op_revision.h"
#include <assert.h>
#include <string.h>
#include <stdio.h>
typedef struct host {
    unsigned calls, fail_at, restore_fail, prepared, restores, batched;
    unsigned values[2][3];
} host_t;
static op_status_t submit(void *user, uint32_t action, uint32_t index, uint32_t level) {
    host_t *h = user;
    assert(index < 2 && level < 3);
    if (action == OP_PUBLICATION_RESTORE) {
        h->restores++;
        h->values[index][level] = 10 + index;
        return h->restore_fail && h->restores == 1 ? OP_DEVICE_LOSS : OP_OK;
    }
    if (action == OP_PUBLICATION_WRITE && h->batched) {
        assert(index == 0 && level == 0 && h->prepared == 2);
        for (unsigned i = 0; i < 2; i++)
            for (unsigned m = 0; m < 3; m++) {
                h->values[i][m] = 20 + i;
                if (++h->calls == h->fail_at)
                    return OP_RESOURCE_EXHAUSTED;
            }
        return OP_OK;
    }
    h->calls++;
    if (action == OP_PUBLICATION_PREPARE) {
        assert(level == 0);
        h->prepared++;
    } else {
        assert(h->prepared == 2);
        h->values[index][level] = 20 + index;
    }
    return h->calls == h->fail_at ? OP_RESOURCE_EXHAUSTED : OP_OK;
}
int main(void) {
    for (unsigned batched = 0; batched < 2; batched++)
    for (unsigned fail = 0; fail <= 8; fail++) {
        op_status_t (*publish)(op_revisions_t *, const op_publication_t *, uint32_t,
                               op_publication_submit_t, void *, uint64_t *) =
            batched ? op_revisions_publish_batched : op_revisions_publish;
        op_revisions_t *s;
        assert(op_revisions_create(&s) == OP_OK);
        op_publication_t channels[] = {{10, 0, 4}, {11, 1, 4}};
        host_t host = {0};
        host.batched = batched;
        uint64_t revision = 0;
        assert(publish(s, channels, 2, submit, &host, &revision) == OP_OK &&
               revision == 1);
        for (unsigned i = 0; i < 2; i++)
            for (unsigned m = 0; m < 3; m++)
                host.values[i][m] = 10 + i;
        channels[0].resource = 20;
        channels[1].resource = 21;
        host.calls = host.prepared = 0;
        host.fail_at = fail;
        op_status_t status = publish(s, channels, 2, submit, &host, &revision);
        assert(status == (fail ? OP_RESOURCE_EXHAUSTED : OP_OK));
        op_gpu_frame_t frame;
        assert(op_revisions_acquire(s, &frame) == OP_OK);
        assert(frame.revision == (fail ? 1 : 2));
        assert(frame.resources[0] == (fail ? 10 : 20));
        for (unsigned i = 0; i < 2; i++)
            for (unsigned m = 0; m < 3; m++)
                assert(host.values[i][m] == (fail ? 10 : 20) + i);
        assert(op_revisions_release(s, &frame) == OP_OK);
        if (fail) {
            host.calls = host.prepared = host.fail_at = 0;
            assert(publish(s, channels, 2, submit, &host, &revision) == OP_OK &&
                   revision == 2);
        }
        op_revisions_destroy(s);
    }
    op_revisions_t *s;
    assert(op_revisions_create(&s) == OP_OK);
    host_t host = {0};
    host.fail_at = 8;
    host.restore_fail = 1;
    op_publication_t channels[] = {{10, 0, 4}, {11, 1, 4}};
    uint64_t revision = 99;
    assert(op_revisions_publish(s, channels, 2, submit, &host, &revision) == OP_DEVICE_LOSS);
    assert(host.restores == 6 && revision == 99);
    op_gpu_frame_t frame;
    assert(op_revisions_acquire(s, &frame) == OP_PENDING);
    channels[1].channel = 0;
    host.calls = 0;
    assert(op_revisions_publish(s, channels, 2, submit, &host, &revision) == OP_VALIDATION_FAILED &&
           !host.calls);
    op_revisions_destroy(s);
    puts("PASS test_publication");
    return 0;
}
