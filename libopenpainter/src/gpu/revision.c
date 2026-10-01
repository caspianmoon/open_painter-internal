#include "openpainter/op_revision.h"
#include "../core/serial.h"
#include <stdlib.h>
#include <string.h>
#define FRAMES 8
#define LEASES 64
struct frame {
    uint64_t revision, resources[8];
    uint32_t references, mask;
};
struct op_revisions {
    uint64_t instance, next_revision, next_lease;
    struct frame frames[FRAMES], pending;
    struct {
        uint64_t token;
        uint32_t index;
    } leases[LEASES];
    uint32_t latest, expected, staged, slot, open;
};
static op_serial_t next_instance = 1;
op_status_t op_revisions_create(op_revisions_t **out) {
    if (!out)
        return OP_INVALID_ARGUMENT;
    *out = calloc(1, sizeof(**out));
    if (!*out)
        return OP_RESOURCE_EXHAUSTED;
    (*out)->instance = op_serial_next(&next_instance);
    (*out)->latest = FRAMES;
    return OP_OK;
}
void op_revisions_destroy(op_revisions_t *s) { free(s); }
op_status_t op_revisions_begin(op_revisions_t *s, uint32_t mask) {
    if (!s || !mask || (mask & ~255u) || s->open)
        return OP_INVALID_ARGUMENT;
    uint32_t slot = FRAMES;
    for (uint32_t i = 0; i < FRAMES; i++)
        if (i != s->latest && !s->frames[i].references) {
            slot = i;
            break;
        }
    if (slot == FRAMES)
        return OP_RESOURCE_EXHAUSTED;
    memset(&s->pending, 0, sizeof(s->pending));
    if (s->latest < FRAMES)
        s->pending = s->frames[s->latest];
    s->pending.references = 0;
    s->slot = slot;
    s->expected = mask;
    s->staged = 0;
    s->open = 1;
    return OP_OK;
}
op_status_t op_revisions_stage(op_revisions_t *s, uint32_t channel, uint64_t resource) {
    if (!s || !s->open || channel >= 8 || !(s->expected & (1u << channel)) || !resource)
        return OP_INVALID_ARGUMENT;
    s->pending.resources[channel] = resource;
    s->pending.mask |= 1u << channel;
    s->staged |= 1u << channel;
    return OP_OK;
}
op_status_t op_revisions_commit(op_revisions_t *s, uint64_t *revision) {
    if (!s || !revision || !s->open)
        return OP_INVALID_ARGUMENT;
    if (s->staged != s->expected)
        return OP_PENDING;
    if (s->next_revision == UINT64_MAX)
        return OP_RESOURCE_EXHAUSTED;
    s->pending.revision = ++s->next_revision;
    s->frames[s->slot] = s->pending;
    s->latest = s->slot;
    s->open = 0;
    *revision = s->next_revision;
    return OP_OK;
}
void op_revisions_abort(op_revisions_t *s) {
    if (s) {
        s->open = 0;
        memset(&s->pending, 0, sizeof(s->pending));
    }
}
op_status_t op_revisions_acquire(op_revisions_t *s, op_gpu_frame_t *out) {
    if (!s || !out)
        return OP_INVALID_ARGUMENT;
    memset(out, 0, sizeof(*out));
    if (s->latest >= FRAMES)
        return OP_PENDING;
    uint32_t slot = LEASES;
    for (uint32_t i = 0; i < LEASES; i++)
        if (!s->leases[i].token) {
            slot = i;
            break;
        }
    if (slot == LEASES || s->next_lease == UINT64_MAX)
        return OP_RESOURCE_EXHAUSTED;
    struct frame *f = &s->frames[s->latest];
    s->leases[slot].token = ++s->next_lease;
    s->leases[slot].index = s->latest;
    f->references++;
    out->instance = s->instance;
    out->revision = f->revision;
    out->lease = s->next_lease;
    out->channel_mask = f->mask;
    memcpy(out->resources, f->resources, sizeof(out->resources));
    return OP_OK;
}
op_status_t op_revisions_release(op_revisions_t *s, const op_gpu_frame_t *frame) {
    if (!s || !frame)
        return OP_INVALID_ARGUMENT;
    if (frame->instance != s->instance || !frame->lease)
        return OP_STALE_HANDLE;
    for (uint32_t i = 0; i < LEASES; i++)
        if (s->leases[i].token == frame->lease) {
            struct frame *f = &s->frames[s->leases[i].index];
            if (f->revision != frame->revision || f->mask != frame->channel_mask ||
                memcmp(f->resources, frame->resources, sizeof(f->resources)))
                return OP_STALE_HANDLE;
            f->references--;
            s->leases[i].token = 0;
            return OP_OK;
        }
    return OP_STALE_HANDLE;
}
uint32_t op_revisions_references(const op_revisions_t *s, uint64_t token) {
    if (!s || !token)
        return 0;
    uint32_t count = 0;
    for (uint32_t i = 0; i < FRAMES; i++)
        if (i == s->latest || s->frames[i].references)
            for (uint32_t c = 0; c < 8; c++)
                if ((s->frames[i].mask & (1u << c)) && s->frames[i].resources[c] == token)
                    count++;
    if (s->open)
        for (uint32_t c = 0; c < 8; c++)
            if ((s->pending.mask & (1u << c)) && s->pending.resources[c] == token)
                count++;
    return count;
}

static op_status_t publish(op_revisions_t *s, const op_publication_t *channels,
                                 uint32_t count, op_publication_submit_t submit, void *user,
                                 uint64_t *revision, int batched) {
    if (!s || !channels || !count || count > 8 || !submit || !revision)
        return OP_INVALID_ARGUMENT;
    op_publication_t snapshot[8];
    memcpy(snapshot, channels, count * sizeof(*channels));
    channels = snapshot;
    uint32_t mask = 0, levels[8], touched = 0;
    for (uint32_t i = 0; i < count; i++) {
        const op_publication_t *c = channels + i;
        if (!c->resource || c->channel >= 8 || !c->size || c->size > 32768 ||
            (mask & (1u << c->channel)))
            return OP_VALIDATION_FAILED;
        mask |= 1u << c->channel;
        levels[i] = 1;
        for (uint32_t size = c->size; size > 1; size /= 2)
            levels[i]++;
    }
    op_status_t status = op_revisions_begin(s, mask);
    if (status)
        return status;
    for (uint32_t i = 0; i < count; i++) {
        status = op_revisions_stage(s, channels[i].channel, channels[i].resource);
        if (status)
            goto failed;
        status = submit(user, OP_PUBLICATION_PREPARE, i, 0);
        if (status)
            goto failed;
    }
    if (batched) {
        /* One host submission may touch every prepared channel. Mark all of
         * them before calling it so even a partially submitted batch restores
         * every mip of every output on failure. */
        touched = count;
        status = submit(user, OP_PUBLICATION_WRITE, 0, 0);
        if (status)
            goto failed;
    } else for (uint32_t i = 0; i < count; i++) {
        touched = i + 1; /* Failed submissions can already have changed storage. */
        for (uint32_t level = 0; level < levels[i]; level++) {
            status = submit(user, OP_PUBLICATION_WRITE, i, level);
            if (status)
                goto failed;
        }
    }
    status = op_revisions_commit(s, revision);
    if (!status)
        return OP_OK;
failed:
    /* Attempt every restore even after one fails; never accept a partial frame. */
    for (uint32_t i = 0; i < touched; i++)
        for (uint32_t level = 0; level < levels[i]; level++)
            if (submit(user, OP_PUBLICATION_RESTORE, i, level) != OP_OK)
                status = OP_DEVICE_LOSS;
    op_revisions_abort(s);
    return status;
}

op_status_t op_revisions_publish(op_revisions_t *s, const op_publication_t *channels,
                                uint32_t count, op_publication_submit_t submit, void *user,
                                uint64_t *revision) {
    return publish(s, channels, count, submit, user, revision, 0);
}

op_status_t op_revisions_publish_batched(op_revisions_t *s, const op_publication_t *channels,
                                        uint32_t count, op_publication_submit_t submit, void *user,
                                        uint64_t *revision) {
    return publish(s, channels, count, submit, user, revision, 1);
}
