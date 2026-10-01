#include "openpainter/op_seam.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#define MAX_SIDES 1048576u
#define MAX_SAMPLES 33554432u
typedef struct side {
    float source[2], other[2], tangent[2], normal[2], other_tangent[2], other_normal[2];
    float length, other_length, density;
    int64_t island;
    uint32_t index, pieces;
    int32_t low[2], high[2];
} side_t;
static int order_side(const void *av, const void *bv) {
    const side_t *a = av, *b = bv;
    if (a->island != b->island)
        return (a->island > b->island) - (a->island < b->island);
    return (a->index > b->index) - (a->index < b->index);
}
static void piece_bounds(const side_t *s, uint32_t piece, uint32_t size, float halo, int32_t low[2],
                         int32_t high[2], float *from, float *to) {
    *from = (float)((double)piece * halo - halo);
    *to = fminf(*from + halo, s->length + halo);
    for (unsigned j = 0; j < 2; j++) {
        float a = s->source[j] + s->tangent[j] * (*from), b = s->source[j] + s->tangent[j] * (*to),
              reach = s->normal[j] * halo;
        float lo = fminf(fminf(a, b), fminf(a + reach, b + reach)) - 1;
        float hi = fmaxf(fmaxf(a, b), fmaxf(a + reach, b + reach)) + 1;
        low[j] = (int32_t)fminf(fmaxf(floorf(lo), 0), (float)size);
        high[j] = (int32_t)fmaxf(fminf(ceilf(hi), (float)size - 1), -1);
    }
}
static void offsets(const side_t *s, uint32_t row, uint32_t column, float *along, float *depth) {
    float x = (float)column + .5f - s->source[0], y = (float)row + .5f - s->source[1];
    *along = x * s->tangent[0] + y * s->tangent[1];
    *depth = x * s->normal[0] + y * s->normal[1];
}
op_status_t op_seam_bands(const uint8_t *covered, uint32_t size, float halo, const float *input,
                          const int64_t *islands, uint32_t count, op_seam_sample_t **samples,
                          uint64_t *written) {
    if (!samples || !written)
        return OP_INVALID_ARGUMENT;
    *samples = NULL;
    *written = 0;
    if (!covered || !size || size > 8192 || !isfinite(halo) || halo <= 0 || halo > 1024 ||
        count > MAX_SIDES || (count && (!input || !islands)))
        return OP_INVALID_ARGUMENT;
    side_t *sides = calloc((size_t)count + 1, sizeof(*sides));
    uint32_t used = 0;
    uint64_t total_pieces = 0, capacity = 0;
    op_status_t status = OP_OK;
    if (!sides)
        return OP_RESOURCE_EXHAUSTED;
    for (uint32_t i = 0; i < count; i++) {
        const float *in = input + (uint64_t)i * 12;
        for (unsigned j = 0; j < 12; j++)
            if (!isfinite(in[j]) || fabsf(in[j]) > 1e8f) {
                status = OP_VALIDATION_FAILED;
                goto done;
            }
        if (islands[i] < 0) {
            status = OP_VALIDATION_FAILED;
            goto done;
        }
        side_t *s = sides + used;
        float edge[2] = {in[2] - in[0], in[3] - in[1]}, other[2] = {in[8] - in[6], in[9] - in[7]};
        s->length = hypotf(edge[0], edge[1]);
        s->other_length = hypotf(other[0], other[1]);
        if (s->length < 1e-6f || s->other_length < 1e-6f)
            continue;
        s->island = islands[i];
        s->index = i;
        s->density = s->other_length / s->length;
        for (unsigned j = 0; j < 2; j++) {
            s->source[j] = in[j];
            s->other[j] = in[6 + j];
            s->tangent[j] = edge[j] / s->length;
            s->other_tangent[j] = other[j] / s->other_length;
            s->low[j] = (int32_t)size;
            s->high[j] = -1;
        }
        s->normal[0] = -s->tangent[1];
        s->normal[1] = s->tangent[0];
        s->other_normal[0] = -s->other_tangent[1];
        s->other_normal[1] = s->other_tangent[0];
        if ((in[4] - in[0]) * s->normal[0] + (in[5] - in[1]) * s->normal[1] > 0)
            for (unsigned j = 0; j < 2; j++)
                s->normal[j] = -s->normal[j];
        if ((in[10] - in[6]) * s->other_normal[0] + (in[11] - in[7]) * s->other_normal[1] < 0)
            for (unsigned j = 0; j < 2; j++)
                s->other_normal[j] = -s->other_normal[j];
        double pieces = ceilf((s->length + 2 * halo) / halo);
        if (pieces > MAX_SIDES || total_pieces + (uint64_t)pieces > MAX_SAMPLES) {
            status = OP_RESOURCE_EXHAUSTED;
            goto done;
        }
        s->pieces = (uint32_t)pieces;
        total_pieces += s->pieces;
        for (uint32_t p = 0; p < s->pieces; p++) {
            int32_t lo[2], hi[2];
            float from, to;
            piece_bounds(s, p, size, halo, lo, hi, &from, &to);
            if (hi[0] < lo[0] || hi[1] < lo[1])
                continue;
            for (unsigned j = 0; j < 2; j++) {
                if (lo[j] < s->low[j])
                    s->low[j] = lo[j];
                if (hi[j] > s->high[j])
                    s->high[j] = hi[j];
            }
        }
        if (s->high[0] >= s->low[0] && s->high[1] >= s->low[1])
            used++;
    }
    qsort(sides, used, sizeof(*sides), order_side);
    for (uint32_t first = 0; first < used;) {
        uint32_t last = first + 1;
        int32_t low[2] = {sides[first].low[0], sides[first].low[1]},
                high[2] = {sides[first].high[0], sides[first].high[1]};
        while (last < used && sides[last].island == sides[first].island)
            last++;
        for (uint32_t i = first + 1; i < last; i++)
            for (unsigned j = 0; j < 2; j++) {
                if (sides[i].low[j] < low[j])
                    low[j] = sides[i].low[j];
                if (sides[i].high[j] > high[j])
                    high[j] = sides[i].high[j];
            }
        uint32_t width = (uint32_t)(high[0] - low[0] + 1),
                 height = (uint32_t)(high[1] - low[1] + 1);
        uint64_t area = (uint64_t)width * height;
        uint64_t *arena = malloc((size_t)area * sizeof(*arena));
        if (!arena) {
            status = OP_RESOURCE_EXHAUSTED;
            goto done;
        }
        for (uint64_t j = 0; j < area; j++)
            arena[j] = UINT64_MAX;
        for (uint32_t i = first; i < last; i++) {
            const side_t *s = sides + i;
            for (uint32_t p = 0; p < s->pieces; p++) {
                int32_t lo[2], hi[2];
                float from, to;
                piece_bounds(s, p, size, halo, lo, hi, &from, &to);
                for (int32_t row = lo[1]; row <= hi[1]; row++)
                    for (int32_t col = lo[0]; col <= hi[0]; col++) {
                        if (covered[(uint64_t)row * size + col])
                            continue;
                        float along, depth;
                        offsets(s, (uint32_t)row, (uint32_t)col, &along, &depth);
                        if (!(depth > 0 && depth <= halo && along >= from &&
                              (along < to || (p + 1 == s->pieces && along == to))))
                            continue;
                        float overshoot = fmaxf(fmaxf(-along, along - s->length), 0),
                              distance = hypotf(depth, overshoot);
                        uint32_t bits;
                        memcpy(&bits, &distance, sizeof(bits));
                        /* Sorted side index preserves original ordering within each island. */
                        uint64_t key = ((uint64_t)bits << 32) | i,
                                 where = (uint64_t)(row - low[1]) * width + (col - low[0]);
                        if (key < arena[where])
                            arena[where] = key;
                    }
            }
        }
        for (uint64_t j = 0; j < area; j++)
            if (arena[j] != UINT64_MAX) {
                if (*written == MAX_SAMPLES) {
                    free(arena);
                    status = OP_RESOURCE_EXHAUSTED;
                    goto done;
                }
                if (*written == capacity) {
                    uint64_t next = capacity ? capacity * 2 : 1024;
                    op_seam_sample_t *more = realloc(*samples, (size_t)next * sizeof(*more));
                    if (!more) {
                        free(arena);
                        status = OP_RESOURCE_EXHAUSTED;
                        goto done;
                    }
                    *samples = more;
                    capacity = next;
                }
                const side_t *s = sides + (uint32_t)arena[j];
                op_seam_sample_t *out = *samples + (*written)++;
                out->row = (uint32_t)low[1] + (uint32_t)(j / width);
                out->column = (uint32_t)low[0] + (uint32_t)(j % width);
                out->island = s->island;
                uint32_t bits = (uint32_t)(arena[j] >> 32);
                memcpy(&out->depth, &bits, sizeof(bits));
                float along, depth;
                offsets(s, out->row, out->column, &along, &depth);
                float margin = fminf(.75f, s->other_length / 2),
                      t = fminf(fmaxf(along / s->length, 0), 1) * s->other_length;
                t = fminf(fmaxf(t, margin), s->other_length - margin);
                float d = fmaxf(depth * s->density, .75f);
                out->x = s->other[0] + s->other_tangent[0] * t + s->other_normal[0] * d - .5f;
                out->y = s->other[1] + s->other_tangent[1] * t + s->other_normal[1] * d - .5f;
                out->normal[0] = s->normal[0];
                out->normal[1] = s->normal[1];
            }
        free(arena);
        first = last;
    }
    if (*written) {
        /* One index per texel halves the old Python int64 race arena. The
         * per-island result is already unique, so each texel has one winner. */
        uint32_t *nearest = calloc((size_t)size * size, sizeof(*nearest));
        if (!nearest) {
            status = OP_RESOURCE_EXHAUSTED;
            goto done;
        }
        for (uint32_t i = 0; i < *written; i++) {
            op_seam_sample_t *sample = *samples + i;
            uint64_t pixel = (uint64_t)sample->row * size + sample->column;
            uint32_t previous = nearest[pixel];
            const op_seam_sample_t *other = previous ? *samples + previous - 1 : NULL;
            if (!other || sample->depth < other->depth ||
                (sample->depth == other->depth && sample->island < other->island))
                nearest[pixel] = i + 1;
        }
        for (uint32_t i = 0; i < *written; i++) {
            op_seam_sample_t *sample = *samples + i;
            sample->nearest = nearest[(uint64_t)sample->row * size + sample->column] == i + 1;
        }
        free(nearest);
    }
done:
    free(sides);
    if (status) {
        free(*samples);
        *samples = NULL;
        *written = 0;
    }
    return status;
}
