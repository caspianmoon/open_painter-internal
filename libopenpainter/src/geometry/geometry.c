#include "openpainter/op_geometry.h"
#include <limits.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#define BLOCK 256u
#define MAX_TRIANGLES 8388608u
#define MAX_SAMPLES 33554432u
typedef struct center {
    double value;
    uint32_t index;
} center;
struct op_bounds {
    double *lo, *hi, *block_lo, *block_hi;
    uint32_t *order, count, blocks;
};
static int center_compare(const void *a, const void *b) {
    const center *x = a, *y = b;
    return x->value < y->value   ? -1
           : x->value > y->value ? 1
           : x->index < y->index ? -1
                                 : x->index > y->index;
}
static int index_compare(const void *a, const void *b) {
    uint32_t x = *(const uint32_t *)a, y = *(const uint32_t *)b;
    return x < y ? -1 : x > y;
}
static int intersects(const double *a, const double *b, const double *lo, const double *hi) {
    return a[0] <= hi[0] && a[1] <= hi[1] && a[2] <= hi[2] && b[0] >= lo[0] && b[1] >= lo[1] &&
           b[2] >= lo[2];
}
op_status_t op_bounds_create(const double *lo, const double *hi, uint32_t count,
                             op_bounds_t **out) {
    op_bounds_t *b;
    center *centers = NULL;
    if (!out || (count && (!lo || !hi)) || count > MAX_TRIANGLES)
        return OP_INVALID_ARGUMENT;
    *out = NULL;
    b = calloc(1, sizeof(*b));
    if (!b)
        return OP_RESOURCE_EXHAUSTED;
    b->count = count;
    b->lo = malloc(((size_t)count * 3 + 1) * sizeof(double));
    b->hi = malloc(((size_t)count * 3 + 1) * sizeof(double));
    if (!b->lo || !b->hi)
        goto failed;
    if (count) {
        memcpy(b->lo, lo, (size_t)count * 3 * sizeof(double));
        memcpy(b->hi, hi, (size_t)count * 3 * sizeof(double));
    }
    if (count < 4096) {
        *out = b;
        return OP_OK;
    }
    double minimum[3] = {INFINITY, INFINITY, INFINITY},
           maximum[3] = {-INFINITY, -INFINITY, -INFINITY};
    for (uint32_t i = 0; i < count * 3; i++) {
        if (!isfinite(lo[i]) || !isfinite(hi[i])) {
            *out = b;
            return OP_OK;
        }
        unsigned d = i % 3;
        if (lo[i] < minimum[d])
            minimum[d] = lo[i];
        if (hi[i] > maximum[d])
            maximum[d] = hi[i];
    }
    unsigned axis = 0;
    double extent = maximum[0] * .5 - minimum[0] * .5;
    for (unsigned d = 1; d < 3; d++)
        if (maximum[d] * .5 - minimum[d] * .5 > extent) {
            axis = d;
            extent = maximum[d] * .5 - minimum[d] * .5;
        }
    centers = malloc((size_t)count * sizeof(*centers));
    b->order = malloc((size_t)count * sizeof(uint32_t));
    b->blocks = (count + BLOCK - 1) / BLOCK;
    b->block_lo = malloc((size_t)b->blocks * 3 * sizeof(double));
    b->block_hi = malloc((size_t)b->blocks * 3 * sizeof(double));
    if (!centers || !b->order || !b->block_lo || !b->block_hi)
        goto failed;
    for (uint32_t i = 0; i < count; i++)
        centers[i] = (center){lo[3 * i + axis] * .5 + hi[3 * i + axis] * .5, i};
    qsort(centers, count, sizeof(*centers), center_compare);
    for (uint32_t k = 0; k < b->blocks; k++) {
        for (unsigned d = 0; d < 3; d++) {
            b->block_lo[3 * k + d] = INFINITY;
            b->block_hi[3 * k + d] = -INFINITY;
        }
        uint32_t end = (k + 1) * BLOCK;
        if (end > count)
            end = count;
        for (uint32_t j = k * BLOCK; j < end; j++) {
            uint32_t i = b->order[j] = centers[j].index;
            for (unsigned d = 0; d < 3; d++) {
                if (lo[3 * i + d] < b->block_lo[3 * k + d])
                    b->block_lo[3 * k + d] = lo[3 * i + d];
                if (hi[3 * i + d] > b->block_hi[3 * k + d])
                    b->block_hi[3 * k + d] = hi[3 * i + d];
            }
        }
    }
    free(centers);
    *out = b;
    return OP_OK;
failed:
    free(centers);
    op_bounds_destroy(b);
    return OP_RESOURCE_EXHAUSTED;
}
void op_bounds_destroy(op_bounds_t *b) {
    if (b) {
        free(b->lo);
        free(b->hi);
        free(b->order);
        free(b->block_lo);
        free(b->block_hi);
        free(b);
    }
}
uint64_t op_bounds_bytes(const op_bounds_t *b) {
    return b ? sizeof(*b) + (uint64_t)b->count * 6 * sizeof(double) +
                   (b->order ? (uint64_t)b->count * 4 + (uint64_t)b->blocks * 6 * sizeof(double)
                             : 0)
             : 0;
}
op_status_t op_bounds_query(const op_bounds_t *b, const double low[3], const double high[3],
                            uint32_t *out, uint32_t capacity, uint32_t *written) {
    if (!b || !low || !high || !written || (b->count && !out))
        return OP_INVALID_ARGUMENT;
    *written = 0;
    if (capacity < b->count)
        return OP_RESOURCE_EXHAUSTED;
    if (!b->order) {
        for (uint32_t i = 0; i < b->count; i++)
            if (intersects(b->lo + 3 * i, b->hi + 3 * i, low, high))
                out[(*written)++] = i;
    } else {
        for (uint32_t k = 0; k < b->blocks; k++)
            if (intersects(b->block_lo + 3 * k, b->block_hi + 3 * k, low, high)) {
                uint32_t end = (k + 1) * BLOCK;
                if (end > b->count)
                    end = b->count;
                for (uint32_t j = k * BLOCK; j < end; j++) {
                    uint32_t i = b->order[j];
                    if (intersects(b->lo + 3 * i, b->hi + 3 * i, low, high))
                        out[(*written)++] = i;
                }
            }
        qsort(out, *written, sizeof(*out), index_compare);
    }
    return OP_OK;
}
static int sample_compare(const void *a, const void *b) {
    const op_uv_sample_t *x = a, *y = b;
    if (x->row != y->row)
        return x->row < y->row ? -1 : 1;
    if (x->column != y->column)
        return x->column < y->column ? -1 : 1;
    return x->triangle < y->triangle ? -1 : x->triangle > y->triangle;
}
static int lower(float x, uint32_t n) {
    return x <= 0 ? 0 : x >= (float)n ? (int)n : (int)ceilf(x);
}
static int upper(float x, uint32_t n) {
    return x < 0 ? 0 : x >= (float)(n - 1) ? (int)n : (int)floorf(x) + 1;
}
op_status_t op_uv_rasterize(const float *uv, uint32_t triangles, uint32_t size, uint32_t unique,
                            const float *clip, op_uv_sample_t **samples, uint64_t *count) {
    op_uv_sample_t *result = NULL;
    size_t capacity = 0, used = 0;
    op_status_t status = OP_OK;
    if (!samples || !count)
        return OP_INVALID_ARGUMENT;
    *samples = NULL;
    *count = 0;
    if ((triangles && !uv) || triangles > MAX_TRIANGLES || !size || size > 32768 || unique > 1)
        return OP_INVALID_ARGUMENT;
    for (uint32_t t = 0; t < triangles; t++) {
        float p[6];
        int lo[2], hi[2];
        for (unsigned j = 0; j < 6; j++) {
            if (!isfinite(uv[t * 6 + j])) {
                status = OP_VALIDATION_FAILED;
                goto done;
            }
            p[j] = uv[t * 6 + j] * (float)size - .5f;
        }
        for (unsigned d = 0; d < 2; d++) {
            float min = fminf(p[d], fminf(p[2 + d], p[4 + d])),
                  max = fmaxf(p[d], fmaxf(p[2 + d], p[4 + d]));
            lo[d] = lower(min, size);
            hi[d] = upper(max, size);
            if (clip) {
                float l = clip[t * 4 + d] * (float)size - .5f,
                      h = clip[t * 4 + 2 + d] * (float)size - .5f;
                if (!isfinite(l) || !isfinite(h)) {
                    status = OP_VALIDATION_FAILED;
                    goto done;
                }
                int start = lower(l, size), end = upper(h, size);
                if (start > lo[d])
                    lo[d] = start;
                if (end < hi[d])
                    hi[d] = end;
            }
        }
        float det = (p[3] - p[5]) * (p[0] - p[4]) + (p[4] - p[2]) * (p[1] - p[5]);
        if (!isfinite(det) || fabsf(det) < 1e-8f)
            continue;
        for (int y = lo[1]; y < hi[1]; y++)
            for (int x = lo[0]; x < hi[0]; x++) {
                float w0 =
                    ((p[3] - p[5]) * ((float)x - p[4]) + (p[4] - p[2]) * ((float)y - p[5])) / det;
                float w1 =
                    ((p[5] - p[1]) * ((float)x - p[4]) + (p[0] - p[4]) * ((float)y - p[5])) / det;
                float w2 = 1.0f - w0 - w1;
                if (!(w0 >= -1e-5f && w1 >= -1e-5f && w2 >= -1e-5f))
                    continue;
                if (used == capacity) {
                    if (capacity == MAX_SAMPLES) {
                        status = OP_RESOURCE_EXHAUSTED;
                        goto done;
                    }
                    size_t next = capacity ? capacity * 2 : 4096;
                    if (next > MAX_SAMPLES)
                        next = MAX_SAMPLES;
                    op_uv_sample_t *grown = realloc(result, next * sizeof(*result));
                    if (!grown) {
                        status = OP_RESOURCE_EXHAUSTED;
                        goto done;
                    }
                    result = grown;
                    capacity = next;
                }
                result[used++] = (op_uv_sample_t){(uint32_t)y, (uint32_t)x, t, {w0, w1, w2}};
            }
    }
    if (unique && used) {
        qsort(result, used, sizeof(*result), sample_compare);
        size_t out = 0;
        for (size_t i = 0; i < used; i++) {
            if (i + 1 < used && result[i].row == result[i + 1].row &&
                result[i].column == result[i + 1].column)
                continue;
            result[out++] = result[i];
        }
        used = out;
    }
    *samples = result;
    *count = used;
    result = NULL;
done:
    free(result);
    return status;
}
void op_geometry_free(void *memory) { free(memory); }
