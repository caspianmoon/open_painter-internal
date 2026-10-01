/* op_uv_rasterize before linear-time deduplication and vectorized coverage: the reference
 * test_rasterize compares the library with. */
#include "openpainter/op_geometry.h"
#include <math.h>
#include <stdlib.h>
#ifndef MAX_TRIANGLES
#define MAX_TRIANGLES 8388608u
#endif
#ifndef MAX_SAMPLES
#define MAX_SAMPLES 33554432u
#endif
static int reference_sample_compare(const void *a, const void *b) {
    const op_uv_sample_t *x = a, *y = b;
    if (x->row != y->row)
        return x->row < y->row ? -1 : 1;
    if (x->column != y->column)
        return x->column < y->column ? -1 : 1;
    return x->triangle < y->triangle ? -1 : x->triangle > y->triangle;
}
static int reference_lower(float x, uint32_t n) {
    return x <= 0 ? 0 : x >= (float)n ? (int)n : (int)ceilf(x);
}
static int reference_upper(float x, uint32_t n) {
    return x < 0 ? 0 : x >= (float)(n - 1) ? (int)n : (int)floorf(x) + 1;
}
static op_status_t reference_rasterize(const float *uv, uint32_t triangles, uint32_t size, uint32_t unique,
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
            lo[d] = reference_lower(min, size);
            hi[d] = reference_upper(max, size);
            if (clip) {
                float l = clip[t * 4 + d] * (float)size - .5f,
                      h = clip[t * 4 + 2 + d] * (float)size - .5f;
                if (!isfinite(l) || !isfinite(h)) {
                    status = OP_VALIDATION_FAILED;
                    goto done;
                }
                int start = reference_lower(l, size), end = reference_upper(h, size);
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
        qsort(result, used, sizeof(*result), reference_sample_compare);
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
