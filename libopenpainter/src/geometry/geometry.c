#include "openpainter/op_geometry.h"
#include <limits.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "../core/simd.h"
#if defined(OP_SIMD_NEON) && !defined(OP_SCALAR_RASTER)
#define OP_NEON_RASTER 1   /* build with -DOP_SCALAR_RASTER to compare */
#elif defined(OP_SIMD_SSE) && !defined(OP_SCALAR_RASTER)
#define OP_SSE_RASTER 1
#endif
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
        for (int y = lo[1]; y < hi[1]; y++) {
            int x = lo[0];
#ifdef OP_NEON_RASTER
            /* Four texels a step: the scalar operations below, lane by lane and in the same
             * order (no fused multiply-add, IEEE division; a NaN fails the tests as it does
             * there). Covered lanes are appended in texel order. */
            const float32x4_t a = vdupq_n_f32(p[3] - p[5]), b = vdupq_n_f32(p[4] - p[2]),
                              c = vdupq_n_f32(p[5] - p[1]), d = vdupq_n_f32(p[0] - p[4]),
                              origin = vdupq_n_f32(p[4]), denominator = vdupq_n_f32(det),
                              one = vdupq_n_f32(1.0f), floor = vdupq_n_f32(-1e-5f);
            const float32x4_t dy = vdupq_n_f32((float)y - p[5]), steps = {0, 1, 2, 3};
            const float32x4_t bdy = vmulq_f32(b, dy), ddy = vmulq_f32(d, dy);
            for (; x + 4 <= hi[0]; x += 4) {
                float32x4_t dx = vsubq_f32(vaddq_f32(vdupq_n_f32((float)x), steps), origin);
                float32x4_t w0 = vdivq_f32(vaddq_f32(vmulq_f32(a, dx), bdy), denominator);
                float32x4_t w1 = vdivq_f32(vaddq_f32(vmulq_f32(c, dx), ddy), denominator);
                float32x4_t w2 = vsubq_f32(vsubq_f32(one, w0), w1);
                uint32x4_t inside = vandq_u32(vandq_u32(vcgeq_f32(w0, floor), vcgeq_f32(w1, floor)),
                                              vcgeq_f32(w2, floor));
                if (!vmaxvq_u32(inside))
                    continue;
                float u0[4], u1[4], u2[4];
                uint32_t hit[4];
                vst1q_f32(u0, w0);
                vst1q_f32(u1, w1);
                vst1q_f32(u2, w2);
                vst1q_u32(hit, inside);
                for (int lane = 0; lane < 4; lane++) {
                    if (!hit[lane])
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
                    result[used++] =
                        (op_uv_sample_t){(uint32_t)y, (uint32_t)(x + lane), t, {u0[lane], u1[lane], u2[lane]}};
                }
            }
#elif defined(OP_SSE_RASTER)
            /* The NEON loop above on SSE: the same operations lane by lane (cmpge is false for
             * a NaN, as the scalar test is). */
            const __m128 a = _mm_set1_ps(p[3] - p[5]), b = _mm_set1_ps(p[4] - p[2]), c = _mm_set1_ps(p[5] - p[1]),
                         d = _mm_set1_ps(p[0] - p[4]), origin = _mm_set1_ps(p[4]), denominator = _mm_set1_ps(det),
                         one = _mm_set1_ps(1.0f), floor = _mm_set1_ps(-1e-5f);
            const __m128 dy = _mm_set1_ps((float)y - p[5]), steps = _mm_setr_ps(0, 1, 2, 3);
            const __m128 bdy = _mm_mul_ps(b, dy), ddy = _mm_mul_ps(d, dy);
            for (; x + 4 <= hi[0]; x += 4) {
                __m128 dx = _mm_sub_ps(_mm_add_ps(_mm_set1_ps((float)x), steps), origin);
                __m128 w0 = _mm_div_ps(_mm_add_ps(_mm_mul_ps(a, dx), bdy), denominator);
                __m128 w1 = _mm_div_ps(_mm_add_ps(_mm_mul_ps(c, dx), ddy), denominator);
                __m128 w2 = _mm_sub_ps(_mm_sub_ps(one, w0), w1);
                int hit = _mm_movemask_ps(_mm_and_ps(_mm_and_ps(_mm_cmpge_ps(w0, floor), _mm_cmpge_ps(w1, floor)),
                                                     _mm_cmpge_ps(w2, floor)));
                if (!hit)
                    continue;
                float u0[4], u1[4], u2[4];
                _mm_storeu_ps(u0, w0);
                _mm_storeu_ps(u1, w1);
                _mm_storeu_ps(u2, w2);
                for (int lane = 0; lane < 4; lane++) {
                    if (!((hit >> lane) & 1))
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
                    result[used++] =
                        (op_uv_sample_t){(uint32_t)y, (uint32_t)(x + lane), t, {u0[lane], u1[lane], u2[lane]}};
                }
            }
#endif
            for (; x < hi[0]; x++) {
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
    }
    if (unique && used) {
        /* Texel order, the last triangle's sample of each texel, in linear time: samples are
         * bucketed by row (stably, so each row keeps triangle order), and within a row the
         * last sample of a column wins (what sorting by texel then triangle kept). */
        size_t *starts = calloc((size_t)size + 1, sizeof(*starts));
        op_uv_sample_t *rows = malloc(used * sizeof(*rows));
        size_t *winner = malloc((size_t)size * sizeof(*winner));
        uint32_t *mark = calloc(size, sizeof(*mark));
        if (!starts || !rows || !winner || !mark) {
            free(starts);
            free(rows);
            free(winner);
            free(mark);
            status = OP_RESOURCE_EXHAUSTED;
            goto done;
        }
        for (size_t i = 0; i < used; i++)
            starts[result[i].row + 1]++;
        for (uint32_t r = 0; r < size; r++)
            starts[r + 1] += starts[r];
        for (size_t i = 0; i < used; i++)
            rows[starts[result[i].row]++] = result[i];
        /* The scatter advanced each row's start to the next row's: shift them back. */
        for (uint32_t r = size; r > 0; r--)
            starts[r] = starts[r - 1];
        starts[0] = 0;
        size_t out = 0;
        for (uint32_t r = 0; r < size; r++) {
            if (starts[r] == starts[r + 1])
                continue;
            uint32_t lo = size, hi = 0;
            for (size_t k = starts[r]; k < starts[r + 1]; k++) {
                uint32_t c = rows[k].column;
                winner[c] = k;
                mark[c] = r + 1;
                if (c < lo)
                    lo = c;
                if (c > hi)
                    hi = c;
            }
            for (uint32_t c = lo; c <= hi; c++)
                if (mark[c] == r + 1)
                    result[out++] = rows[winner[c]];
        }
        free(starts);
        free(rows);
        free(winner);
        free(mark);
        used = out;
    }
    *samples = result;
    *count = used;
    result = NULL;
done:
    free(result);
    return status;
}
/* What a raster visit does at one covered texel of triangle t, with its weights. */
typedef void (*texel_visit)(void *context, uint64_t texel, uint32_t t, float w0, float w1, float w2);

/* Every texel triangle t covers, by op_uv_rasterize's rule and with its weights, operation for
 * operation (vector lanes and scalar tails alike; no clip), row by row: the coverage loop of the
 * raster kernels below. The caller has checked that the triangle's UVs are finite. */
static inline void raster_triangle(const float *uv, uint32_t t, uint32_t size, texel_visit visit, void *context) {
    float p[6];
    int lo[2], hi[2];
    for (unsigned j = 0; j < 6; j++)
        p[j] = uv[t * 6 + j] * (float)size - .5f;
    for (unsigned d = 0; d < 2; d++) {
        float min = fminf(p[d], fminf(p[2 + d], p[4 + d])), max = fmaxf(p[d], fmaxf(p[2 + d], p[4 + d]));
        lo[d] = lower(min, size);
        hi[d] = upper(max, size);
    }
    float det = (p[3] - p[5]) * (p[0] - p[4]) + (p[4] - p[2]) * (p[1] - p[5]);
    if (!isfinite(det) || fabsf(det) < 1e-8f)
        return;
    for (int y = lo[1]; y < hi[1]; y++) {
        int x = lo[0];
        uint64_t row = (uint64_t)y * size;
#ifdef OP_NEON_RASTER
        const float32x4_t a = vdupq_n_f32(p[3] - p[5]), b = vdupq_n_f32(p[4] - p[2]), c = vdupq_n_f32(p[5] - p[1]),
                          d = vdupq_n_f32(p[0] - p[4]), origin = vdupq_n_f32(p[4]), denominator = vdupq_n_f32(det),
                          one = vdupq_n_f32(1.0f), floor = vdupq_n_f32(-1e-5f);
        const float32x4_t dy = vdupq_n_f32((float)y - p[5]), steps = {0, 1, 2, 3};
        const float32x4_t bdy = vmulq_f32(b, dy), ddy = vmulq_f32(d, dy);
        for (; x + 4 <= hi[0]; x += 4) {
            float32x4_t dx = vsubq_f32(vaddq_f32(vdupq_n_f32((float)x), steps), origin);
            float32x4_t w0 = vdivq_f32(vaddq_f32(vmulq_f32(a, dx), bdy), denominator);
            float32x4_t w1 = vdivq_f32(vaddq_f32(vmulq_f32(c, dx), ddy), denominator);
            float32x4_t w2 = vsubq_f32(vsubq_f32(one, w0), w1);
            uint32x4_t inside = vandq_u32(vandq_u32(vcgeq_f32(w0, floor), vcgeq_f32(w1, floor)), vcgeq_f32(w2, floor));
            if (!vmaxvq_u32(inside))
                continue;
            float u0[4], u1[4], u2[4];
            uint32_t hit[4];
            vst1q_f32(u0, w0);
            vst1q_f32(u1, w1);
            vst1q_f32(u2, w2);
            vst1q_u32(hit, inside);
            for (int lane = 0; lane < 4; lane++)
                if (hit[lane])
                    visit(context, row + (uint64_t)(x + lane), t, u0[lane], u1[lane], u2[lane]);
        }
#elif defined(OP_SSE_RASTER)
        const __m128 a = _mm_set1_ps(p[3] - p[5]), b = _mm_set1_ps(p[4] - p[2]), c = _mm_set1_ps(p[5] - p[1]),
                     d = _mm_set1_ps(p[0] - p[4]), origin = _mm_set1_ps(p[4]), denominator = _mm_set1_ps(det),
                     one = _mm_set1_ps(1.0f), floor = _mm_set1_ps(-1e-5f);
        const __m128 dy = _mm_set1_ps((float)y - p[5]), steps = _mm_setr_ps(0, 1, 2, 3);
        const __m128 bdy = _mm_mul_ps(b, dy), ddy = _mm_mul_ps(d, dy);
        for (; x + 4 <= hi[0]; x += 4) {
            __m128 dx = _mm_sub_ps(_mm_add_ps(_mm_set1_ps((float)x), steps), origin);
            __m128 w0 = _mm_div_ps(_mm_add_ps(_mm_mul_ps(a, dx), bdy), denominator);
            __m128 w1 = _mm_div_ps(_mm_add_ps(_mm_mul_ps(c, dx), ddy), denominator);
            __m128 w2 = _mm_sub_ps(_mm_sub_ps(one, w0), w1);
            int hit = _mm_movemask_ps(_mm_and_ps(_mm_and_ps(_mm_cmpge_ps(w0, floor), _mm_cmpge_ps(w1, floor)),
                                                 _mm_cmpge_ps(w2, floor)));
            if (!hit)
                continue;
            float u0[4], u1[4], u2[4];
            _mm_storeu_ps(u0, w0);
            _mm_storeu_ps(u1, w1);
            _mm_storeu_ps(u2, w2);
            for (int lane = 0; lane < 4; lane++)
                if ((hit >> lane) & 1)
                    visit(context, row + (uint64_t)(x + lane), t, u0[lane], u1[lane], u2[lane]);
        }
#endif
        for (; x < hi[0]; x++) {
            float w0 = ((p[3] - p[5]) * ((float)x - p[4]) + (p[4] - p[2]) * ((float)y - p[5])) / det;
            float w1 = ((p[5] - p[1]) * ((float)x - p[4]) + (p[0] - p[4]) * ((float)y - p[5])) / det;
            float w2 = 1.0f - w0 - w1;
            if (w0 >= -1e-5f && w1 >= -1e-5f && w2 >= -1e-5f)
                visit(context, row + (uint64_t)x, t, w0, w1, w2);
        }
    }
}

/* A non-finite UV fails a raster kernel's call before any texel is written, as op_uv_rasterize
 * keeps no sample then. */
static int finite_uvs(const float *uv, uint32_t triangles) {
    for (uint64_t i = 0; i < (uint64_t)triangles * 6; i++)
        if (!isfinite(uv[i]))
            return 0;
    return 1;
}

typedef struct interpolation {
    uint32_t attributes;
    const void *const *corners;
    const uint32_t *components, *bytes;
    float *const *outs;
    uint8_t *covered;
} interpolation;

/* One covered texel of op_uv_raster_interpolate: every attribute interpolated at the weights,
 * summed left to right (op_mesh_interpolate's float arithmetic for float corners; NumPy's float64
 * arithmetic for double corners, rounded to float once). */
static void interpolate_texel(void *context, uint64_t texel, uint32_t t, float w0, float w1, float w2) {
    const interpolation *m = context;
    for (uint32_t a = 0; a < m->attributes; a++) {
        uint32_t n = m->components[a];
        float *out = m->outs[a] + texel * n;
        if (m->bytes[a] == 4) {
            const float *c = (const float *)m->corners[a] + (uint64_t)t * 3 * n;
            for (uint32_t j = 0; j < n; j++)
                out[j] = w0 * c[j] + w1 * c[n + j] + w2 * c[2 * n + j];
        } else {
            const double *c = (const double *)m->corners[a] + (uint64_t)t * 3 * n;
            for (uint32_t j = 0; j < n; j++)
                out[j] = (float)((double)w0 * c[j] + (double)w1 * c[n + j] + (double)w2 * c[2 * n + j]);
        }
    }
    if (m->covered)
        m->covered[texel] = 1;
}

op_status_t op_uv_raster_interpolate(const float *uv, uint32_t triangles, uint32_t size, uint32_t attributes,
                                     const void *const *corners, const uint32_t *components,
                                     const uint32_t *bytes, float *const *outs, uint8_t *covered) {
    if (!attributes || attributes > 8 || !corners || !components || !bytes || !outs || (triangles && !uv) ||
        triangles > MAX_TRIANGLES || !size || size > 32768)
        return OP_INVALID_ARGUMENT;
    for (uint32_t a = 0; a < attributes; a++)
        if (!components[a] || components[a] > 16 || (bytes[a] != 4 && bytes[a] != 8) || !outs[a] ||
            (triangles && !corners[a]))
            return OP_INVALID_ARGUMENT;
    if (!finite_uvs(uv, triangles))
        return OP_VALIDATION_FAILED;
    /* Triangles in order, each overwriting the texels it covers: the later triangle wins, as the
     * unique raster keeps, with no sample list to build, grow and deduplicate. */
    interpolation m = {attributes, corners, components, bytes, outs, covered};
    for (uint32_t t = 0; t < triangles; t++)
        raster_triangle(uv, t, size, interpolate_texel, &m);
    return OP_OK;
}

typedef struct assignment {
    uint32_t attributes;
    const void *const *values;
    const uint32_t *components, *kinds;
    void *const *outs;
    uint8_t *covered;
} assignment;

/* One covered texel of op_uv_raster_assign: each attribute's values of triangle t, cast as NumPy's
 * assignment into the map's dtype casts them. */
static void assign_texel(void *context, uint64_t texel, uint32_t t, float w0, float w1, float w2) {
    (void)w0;
    (void)w1;
    (void)w2;
    const assignment *m = context;
    for (uint32_t a = 0; a < m->attributes; a++) {
        uint32_t n = m->components[a];
        uint64_t from = (uint64_t)t * n, to = texel * n;
        switch (m->kinds[a]) {
        case OP_ASSIGN_F32:
            for (uint32_t j = 0; j < n; j++)
                ((float *)m->outs[a])[to + j] = ((const float *)m->values[a])[from + j];
            break;
        case OP_ASSIGN_F64:
            for (uint32_t j = 0; j < n; j++)
                ((float *)m->outs[a])[to + j] = (float)((const double *)m->values[a])[from + j];
            break;
        case OP_ASSIGN_I32:
            for (uint32_t j = 0; j < n; j++)
                ((int32_t *)m->outs[a])[to + j] = ((const int32_t *)m->values[a])[from + j];
            break;
        default:
            for (uint32_t j = 0; j < n; j++)
                ((int32_t *)m->outs[a])[to + j] = (int32_t)(uint32_t)(uint64_t)((const int64_t *)m->values[a])[from + j];
        }
    }
    if (m->covered)
        m->covered[texel] = 1;
}

op_status_t op_uv_raster_assign(const float *uv, uint32_t triangles, uint32_t size, uint32_t attributes,
                                const void *const *values, const uint32_t *components, const uint32_t *kinds,
                                void *const *outs, uint8_t *covered) {
    if (!attributes || attributes > 8 || !values || !components || !kinds || !outs || (triangles && !uv) ||
        triangles > MAX_TRIANGLES || !size || size > 32768)
        return OP_INVALID_ARGUMENT;
    for (uint32_t a = 0; a < attributes; a++)
        if (!components[a] || components[a] > 16 || kinds[a] > OP_ASSIGN_I64 || !outs[a] || (triangles && !values[a]))
            return OP_INVALID_ARGUMENT;
    if (!finite_uvs(uv, triangles))
        return OP_VALIDATION_FAILED;
    assignment m = {attributes, values, components, kinds, outs, covered};
    for (uint32_t t = 0; t < triangles; t++)
        raster_triangle(uv, t, size, assign_texel, &m);
    return OP_OK;
}

typedef struct ownership {
    const int32_t *owners;
    int32_t *island;
    uint32_t *batch_of, batch;
    uint8_t *covered, *conflicts;
} ownership;

/* One covered texel of op_uv_raster_ownership. build_ownership flags a texel whose writers in a
 * batch differ from the batch's first writer; that holds exactly when two of them in a row differ,
 * so each writer after the batch's first is compared with the one before it, the island so far
 * (batch_of says which batch wrote the texel last). */
static void own_texel(void *context, uint64_t texel, uint32_t t, float w0, float w1, float w2) {
    (void)w0;
    (void)w1;
    (void)w2;
    ownership *m = context;
    int32_t owner = m->owners[t];
    if (m->batch_of[texel] != m->batch)
        m->batch_of[texel] = m->batch;
    else if (m->island[texel] != owner)
        m->conflicts[texel] = 1;
    m->island[texel] = owner;
    m->covered[texel] = 1;
}

op_status_t op_uv_raster_ownership(const float *uv, uint32_t triangles, uint32_t size, const int32_t *owners,
                                   const uint32_t *starts, uint32_t batches, int32_t *island, uint8_t *covered,
                                   uint8_t *conflicts) {
    if ((triangles && (!uv || !owners || !starts || !batches)) || triangles > MAX_TRIANGLES || !size ||
        size > 32768 || !island || !covered || !conflicts)
        return OP_INVALID_ARGUMENT;
    for (uint32_t b = 0; b < batches; b++)
        if ((b == 0 && starts[b] != 0) || (b > 0 && starts[b] <= starts[b - 1]) || starts[b] >= triangles)
            return OP_INVALID_ARGUMENT;
    if (!finite_uvs(uv, triangles))
        return OP_VALIDATION_FAILED;
    if (!triangles)
        return OP_OK;
    ownership m = {owners, island, calloc((uint64_t)size * size, sizeof(uint32_t)), 0, covered, conflicts};
    if (!m.batch_of)
        return OP_RESOURCE_EXHAUSTED;
    /* Batch b is stamped b + 1: the zeroed stamps mean no writer in this batch yet. */
    for (uint32_t b = 0; b < batches; b++) {
        m.batch = b + 1;
        uint32_t end = b + 1 < batches ? starts[b + 1] : triangles;
        for (uint32_t t = starts[b]; t < end; t++)
            raster_triangle(uv, t, size, own_texel, &m);
    }
    free(m.batch_of);
    return OP_OK;
}

void op_geometry_free(void *memory) { free(memory); }
