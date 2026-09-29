#include "openpainter/op_channels.h"
#include <assert.h>
#include <math.h>
#include <string.h>
typedef struct fixture {
    op_channels_t *state;
    op_channel_request_t *source;
    unsigned calls, fail_at, events[10][2], mutate;
} fixture_t;
static op_status_t submit(void *user, uint32_t action, uint32_t channel) {
    fixture_t *f = user;
    assert(f->calls < 10);
    f->events[f->calls][0] = action;
    f->events[f->calls++][1] = channel;
    uint32_t written = 99;
    assert(op_channels_execute(f->state, NULL, 0, submit, user, &written) == OP_PENDING);
    assert(written == 0);
    assert(op_channels_reset(f->state) == OP_PENDING);
    assert(op_channels_destroy(f->state) == OP_PENDING);
    if (f->mutate)
        memset(f->source, 0xff, 8 * sizeof(*f->source));
    return f->fail_at == f->calls ? OP_RESOURCE_EXHAUSTED : OP_OK;
}
int main(void) {
    _Static_assert(sizeof(op_channel_request_t) == 64, "channel record ABI");
    op_channels_t *state = NULL;
    assert(op_channels_create(NULL) == OP_INVALID_ARGUMENT);
    assert(op_channels_create(&state) == OP_OK);
    fixture_t f = {.state = state};
    op_channel_request_t batch[8] = {0}, original[8];
    for (unsigned i = 0; i < 8; i++) {
        batch[i].channel = 7 - i;
        batch[i].target = 10 + i;
        batch[i].size = 2048;
        batch[i].kind = OP_CHANNEL_PROGRAM;
        batch[i].revision = 20 + i;
    }
    memcpy(original, batch, sizeof(batch));
    uint32_t mask = 123;
    /* Fail every render and the atomic commit. Retry must still include all 8. */
    for (unsigned fault = 1; fault <= 9; fault++) {
        f.calls = 0;
        f.fail_at = fault;
        assert(op_channels_execute(state, batch, 8, submit, &f, &mask) == OP_RESOURCE_EXHAUSTED);
        assert(mask == 0 && f.calls == fault);
    }
    f.calls = f.fail_at = 0;
    f.source = batch;
    f.mutate = 1;
    assert(op_channels_execute(state, batch, 8, submit, &f, &mask) == OP_OK);
    assert(mask == 255 && f.calls == 9 && f.events[8][0] == OP_CHANNEL_COMMIT &&
           f.events[8][1] == 255);
    for (unsigned i = 0; i < 8; i++)
        assert(f.events[i][1] == 7 - i);
    memcpy(batch, original, sizeof(batch));
    f.mutate = f.calls = 0;
    assert(op_channels_execute(state, batch, 8, submit, &f, &mask) == OP_OK && !mask && !f.calls);
    /* Native selection detects each relevant descriptor change. */
    for (unsigned change = 0; change < 6; change++) {
        switch (change) {
        case 0:
            batch[3].revision++;
            break;
        case 1:
            batch[3].target++;
            break;
        case 2:
            batch[3].size = 1024;
            break;
        case 3:
            batch[3].flags |= OP_CHANNEL_PRECISE;
            break;
        case 4:
            batch[3].flags |= OP_CHANNEL_PAD;
            break;
        case 5:
            batch[3].kind = OP_CHANNEL_CONSTANT;
            break;
        }
        f.calls = 0;
        assert(op_channels_execute(state, batch, 8, submit, &f, &mask) == OP_OK);
        assert(mask == 16 && f.calls == 2);
    }
    batch[3].constant[2] = .5;
    f.calls = 0;
    assert(op_channels_execute(state, batch + 3, 1, submit, &f, &mask) == OP_OK && mask == 16);
    batch[3].revision++;
    f.calls = 0; /* irrelevant for constants */
    assert(op_channels_execute(state, batch, 8, submit, &f, &mask) == OP_OK && !mask && !f.calls);
    batch[1].flags |= OP_CHANNEL_FORCE;
    assert(op_channels_execute(state, batch, 8, submit, &f, &mask) == OP_OK && mask == 64);
    batch[1].flags &= ~OP_CHANNEL_FORCE;
    assert(op_channels_execute(state, batch, 8, submit, &f, &mask) == OP_OK && !mask);
    f.calls = 0;
    op_channel_request_t bad = batch[0];
    for (unsigned invalid = 0; invalid < 7; invalid++) {
        batch[0] = bad;
        switch (invalid) {
        case 0:
            batch[0].target = 0;
            break;
        case 1:
            batch[0].channel = 8;
            break;
        case 2:
            batch[0].channel = batch[1].channel;
            break;
        case 3:
            batch[0].kind = 2;
            break;
        case 4:
            batch[0].size = 8193;
            break;
        case 5:
            batch[0].flags = 8;
            break;
        case 6:
            batch[0].constant[0] = NAN;
            break;
        }
        assert(op_channels_execute(state, batch, 8, submit, &f, &mask) == OP_VALIDATION_FAILED);
        assert(!mask && !f.calls);
    }
    batch[0] = bad;
    assert(op_channels_execute(state, batch, 9, submit, &f, &mask) == OP_INVALID_ARGUMENT);
    assert(op_channels_execute(state, NULL, 1, submit, &f, &mask) == OP_INVALID_ARGUMENT);
    assert(op_channels_execute(state, NULL, 0, submit, &f, &mask) == OP_OK && !mask);
    assert(op_channels_execute(state, batch, 8, submit, &f, &mask) == OP_OK && !mask);
    assert(op_channels_reset(state) == OP_OK);
    f.calls = 0;
    assert(op_channels_execute(state, batch, 8, submit, &f, &mask) == OP_OK && mask == 255);
    assert(op_channels_destroy(state) == OP_OK);
    assert(op_channels_destroy(NULL) == OP_OK);
    return 0;
}
