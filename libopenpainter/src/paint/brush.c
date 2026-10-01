#include "openpainter/op_paint.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "../core/simd.h"
#if defined(OP_SIMD_NEON) && !defined(OP_SCALAR_COVERAGE)
#define OP_NEON_COVERAGE 1   /* build with -DOP_SCALAR_COVERAGE to compare */
#elif defined(OP_SIMD_SSE) && !defined(OP_SCALAR_COVERAGE)
#define OP_SSE_COVERAGE 1
#endif
#define MAX_COUNT 33554432u
static float dot(const float *a, const float *b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
static void cross(const float *a, const float *b, float *c) {
    c[0] = a[1] * b[2] - a[2] * b[1];
    c[1] = a[2] * b[0] - a[0] * b[2];
    c[2] = a[0] * b[1] - a[1] * b[0];
}
static void normalize(float *v) {
    float n = fmaxf(sqrtf(dot(v, v)), 1e-8f);
    for (int j = 0; j < 3; j++)
        v[j] /= n;
}
static int valid(const float *v, uint64_t n) {
    for (uint64_t j = 0; j < n; j++)
        if (!isfinite(v[j]))
            return 0;
    return 1;
}
static int settings_ok(const op_brush_settings_t *s) {
    return s && isfinite(s->hardness) && isfinite(s->flow) && isfinite(s->angle) &&
           isfinite(s->depth_limit) && valid(s->plane, 3) && s->accumulate <= 1 && s->rake <= 1 &&
           s->planar <= 1;
}
static void frame(const float *s, const op_brush_settings_t *b, float *t, float *bt) {
    memcpy(t, s + 10, 12);
    if (!b->rake || sqrtf(dot(t, t)) < 1e-8f) {
        float helper[3] = {0, 0, 1};
        if (fabsf(s[5]) >= .9f) {
            helper[0] = 1;
            helper[2] = 0;
        }
        cross(s + 3, helper, t);
    }
    normalize(t);
    cross(s + 3, t, bt);
    /* Match the host's double-precision trig followed by float32 UBO storage. */
    double c = cos(b->angle), sn = sin(b->angle);
    for (int j = 0; j < 3; j++)
        t[j] = (float)(t[j] * c + bt[j] * sn);
    cross(s + 3, t, bt);
}
op_status_t op_brush_uniforms(const float *s, uint32_t count, const op_brush_settings_t *b,
                              float *out) {
    if (count > MAX_COUNT || !settings_ok(b) || (count && (!s || !out)) ||
        !valid(s, (uint64_t)count * 13))
        return OP_INVALID_ARGUMENT;
    for (uint32_t i = 0; i < count; i++) {
        const float *p = s + 13 * i;
        float *v = out + 16 * i;
        memcpy(v, p, 12);
        v[3] = p[7];
        memcpy(v + 4, b->planar ? b->plane : p + 3, 12);
        normalize(v + 4);
        v[7] = p[6] * b->flow;
        frame(p, b, v + 8, v + 12);
        v[11] = b->hardness;
        v[15] = b->depth_limit;
    }
    return OP_OK;
}
op_status_t op_brush_bounds(const float *s, uint32_t count, float margin, float lo[3], float hi[3]) {
    if (!lo || !hi || !isfinite(margin) || count > MAX_COUNT || (count && !s) ||
        !valid(s, (uint64_t)count * 13))
        return OP_INVALID_ARGUMENT;
    for (int j = 0; j < 3; j++)
        lo[j] = hi[j] = 0;
    for (uint32_t i = 0; i < count; i++)
        for (int j = 0; j < 3; j++) {
            float a = s[i * 13 + j] - s[i * 13 + 7], b = s[i * 13 + j] + s[i * 13 + 7];
            if (!i || a < lo[j])
                lo[j] = a;
            if (!i || b > hi[j])
                hi[j] = b;
        }
    if (count)
        for (int j = 0; j < 3; j++) {
            lo[j] -= margin;
            hi[j] += margin;
        }
    return OP_OK;
}
op_status_t op_brush_resample(const float *p, uint32_t count, float spacing, uint32_t *out,
                              uint32_t *written) {
    if (!written || count > MAX_COUNT || !isfinite(spacing) || (count && (!p || !out)) ||
        !valid(p, (uint64_t)count * 3))
        return OP_INVALID_ARGUMENT;
    *written = 0;
    if (!count)
        return OP_OK;
    out[(*written)++] = 0;
    uint32_t last = 0;
    for (uint32_t i = 1; i < count; i++) {
        float d[3];
        for (int j = 0; j < 3; j++)
            d[j] = p[i * 3 + j] - p[last * 3 + j];
        if (sqrtf(dot(d, d)) >= spacing) {
            out[(*written)++] = i;
            last = i;
        }
    }
    return OP_OK;
}
/* One stamp's contribution to one texel (the loop body of op_brush_coverage). Returns 0, or -1
 * for a brush alpha lookup that is not finite. */
static int stamp_alpha(const float *p, const float *n, uint32_t i, const float *q, uint32_t k,
                       uint32_t count, const float *axis, const float *t, const float *bt,
                       const op_brush_settings_t *b, float soft, float limit, const float *alpha,
                       uint32_t width, uint32_t height, const uint8_t *eligible, float *out) {
    float rel[3], lat[3];
    for (int j = 0; j < 3; j++)
        rel[j] = p[i * 3 + j] - q[j];
    float depth = dot(rel, axis), radius = fmaxf(q[7], 1e-8f);
    for (int j = 0; j < 3; j++)
        lat[j] = rel[j] - depth * axis[j];
    float radial = sqrtf(dot(lat, lat)) / radius, a = 0;
    if (dot(n + 3 * i, axis) > .05f && radial < 1 && fabsf(depth) < radius * limit &&
        (!eligible || eligible[(uint64_t)i * count + k])) {
        a = fminf(fmaxf((1 - radial) / soft, 0), 1) * q[6] * b->flow;
        if (alpha) {
            float u = dot(lat, t) / radius * .5f + .5f, v = dot(lat, bt) / radius * .5f + .5f;
            if (!isfinite(u) || !isfinite(v))
                return -1;
            uint32_t x = (uint32_t)fminf(fmaxf(u * width, 0), (float)(width - 1)),
                     y = (uint32_t)fminf(fmaxf(v * height, 0), (float)(height - 1));
            a *= alpha[(uint64_t)y * width + x];
        }
    }
    *out = a;
    return 0;
}

/* The stamps that can reach each texel: a uniform grid over the stroke's bounds, each stamp
 * listed, in stamp order, in the cells its reach overlaps. A stamp contributes only where
 * |lat| < radius and |depth| < radius * limit, so nowhere farther than radius * (1 + limit *
 * |axis|) from its centre; the reach adds a relative and an absolute margin for rounding, and
 * cells are found by a monotonic map, so a pair the grid skips is one that contributes exactly
 * nothing. FLOW multiplies in the same order as a loop over every stamp. */
#define GRID_AXIS 64u
typedef struct {
    float lo[3], cell[3];
    uint32_t dims[3];
    uint32_t *start;       /* per cell, into stamps; cells + 1 entries */
    unsigned char *stamps; /* stamp indices (count <= 256) */
} stamp_grid_t;

static uint32_t grid_cell(const stamp_grid_t *g, int j, float x) {
    float f = (x - g->lo[j]) / g->cell[j];
    if (!(f > 0))
        return 0;
    return f >= (float)g->dims[j] ? g->dims[j] - 1 : (uint32_t)f;
}

static int grid_build(stamp_grid_t *g, const float *s, uint32_t count, float axis[][3], float limit,
                      const float lo[3], const float hi[3]) {
    float reach[256], widest = 0, scale = 0;
    for (int j = 0; j < 3; j++)
        scale = fmaxf(scale, fmaxf(fabsf(lo[j]), fabsf(hi[j])));
    for (uint32_t k = 0; k < count; k++) {
        float radius = fmaxf(s[13 * k + 7], 1e-8f), length = sqrtf(dot(axis[k], axis[k]));
        reach[k] = radius * (1 + limit * length) * 1.001f + scale * 1e-6f;
        widest = fmaxf(widest, reach[k]);
    }
    uint64_t cells = 1;
    for (int j = 0; j < 3; j++) {
        float extent = hi[j] - lo[j];
        g->lo[j] = lo[j];
        g->cell[j] = fmaxf(fmaxf(widest, extent / GRID_AXIS), 1e-30f);
        float d = floorf(extent / g->cell[j]) + 1;
        g->dims[j] = d >= GRID_AXIS ? GRID_AXIS : d < 1 ? 1 : (uint32_t)d;
        cells *= g->dims[j];
    }
    uint32_t range[256][6];
    uint64_t entries = 0;
    for (uint32_t k = 0; k < count; k++) {
        uint64_t n = 1;
        for (int j = 0; j < 3; j++) {
            range[k][2 * j] = grid_cell(g, j, s[13 * k + j] - reach[k]);
            range[k][2 * j + 1] = grid_cell(g, j, s[13 * k + j] + reach[k]);
            n *= range[k][2 * j + 1] - range[k][2 * j] + 1;
        }
        entries += n;
    }
    g->start = calloc(cells + 1, sizeof(uint32_t));
    g->stamps = malloc(entries ? entries : 1);
    if (!g->start || !g->stamps) {
        free(g->start);
        free(g->stamps);
        return 0;
    }
    for (int pass = 0; pass < 2; pass++) {
        for (uint32_t k = 0; k < count; k++)
            for (uint32_t z = range[k][4]; z <= range[k][5]; z++)
                for (uint32_t y = range[k][2]; y <= range[k][3]; y++)
                    for (uint32_t x = range[k][0]; x <= range[k][1]; x++) {
                        uint64_t c = ((uint64_t)z * g->dims[1] + y) * g->dims[0] + x;
                        if (pass)
                            g->stamps[g->start[c]++] = (unsigned char)k;
                        else
                            g->start[c + 1]++;
                    }
        if (!pass)
            for (uint64_t c = 0; c < cells; c++)
                g->start[c + 1] += g->start[c];
    }
    /* The fill advanced each cell's start to the next cell's: shift them back. */
    for (uint64_t c = cells; c > 0; c--)
        g->start[c] = g->start[c - 1];
    g->start[0] = 0;
    return 1;
}

#ifdef OP_NEON_COVERAGE
/* stamp_alpha for four texels (i .. i + 3) that share a grid cell, without a brush alpha: the
 * scalar operations lane by lane, in the same order (no fused multiply-add: the library builds
 * with -ffp-contract=off), IEEE division and square root, and minNum/maxNum as fminf/fmaxf do.
 * A lane that fails a test contributes exactly 0, as in the scalar loop. */
static void coverage4(const float *p, const float *n, uint32_t i, const float *s, const unsigned char *list,
                      uint32_t first, uint32_t last, uint32_t count, float axis[][3],
                      const op_brush_settings_t *b, float soft, float limit, const uint8_t *eligible,
                      float *out) {
    float32x4x3_t pos = vld3q_f32(p + 3 * (uint64_t)i), nor = vld3q_f32(n + 3 * (uint64_t)i);
    const float32x4_t one = vdupq_n_f32(1), zero = vdupq_n_f32(0), threshold = vdupq_n_f32(.05f);
    const float32x4_t softness = vdupq_n_f32(soft), flow = vdupq_n_f32(b->flow);
    float32x4_t value = b->accumulate ? one : zero;
    for (uint32_t e = first; e < last; e++) {
        uint32_t k = list[e];
        const float *q = s + 13 * k;
        const float32x4_t a0 = vdupq_n_f32(axis[k][0]), a1 = vdupq_n_f32(axis[k][1]), a2 = vdupq_n_f32(axis[k][2]);
        float32x4_t r0 = vsubq_f32(pos.val[0], vdupq_n_f32(q[0])), r1 = vsubq_f32(pos.val[1], vdupq_n_f32(q[1])),
                    r2 = vsubq_f32(pos.val[2], vdupq_n_f32(q[2]));
        float32x4_t depth = vaddq_f32(vaddq_f32(vmulq_f32(r0, a0), vmulq_f32(r1, a1)), vmulq_f32(r2, a2));
        float radius = fmaxf(q[7], 1e-8f);
        float32x4_t l0 = vsubq_f32(r0, vmulq_f32(depth, a0)), l1 = vsubq_f32(r1, vmulq_f32(depth, a1)),
                    l2 = vsubq_f32(r2, vmulq_f32(depth, a2));
        float32x4_t lateral = vaddq_f32(vaddq_f32(vmulq_f32(l0, l0), vmulq_f32(l1, l1)), vmulq_f32(l2, l2));
        float32x4_t radial = vdivq_f32(vsqrtq_f32(lateral), vdupq_n_f32(radius));
        float32x4_t facing = vaddq_f32(vaddq_f32(vmulq_f32(nor.val[0], a0), vmulq_f32(nor.val[1], a1)),
                                       vmulq_f32(nor.val[2], a2));
        uint32x4_t mask = vandq_u32(vandq_u32(vcgtq_f32(facing, threshold), vcltq_f32(radial, one)),
                                    vcltq_f32(vabsq_f32(depth), vdupq_n_f32(radius * limit)));
        if (eligible) {
            const uint8_t *row = eligible + (uint64_t)i * count + k;
            uint32_t allowed[4] = {row[0] ? ~0u : 0, row[count] ? ~0u : 0, row[2 * (uint64_t)count] ? ~0u : 0,
                                   row[3 * (uint64_t)count] ? ~0u : 0};
            mask = vandq_u32(mask, vld1q_u32(allowed));
        }
        if (!vmaxvq_u32(mask))
            continue;   /* no lane reached: value * (1 - 0) and fmaxf(value, 0) change nothing */
        float32x4_t a = vminnmq_f32(vmaxnmq_f32(vdivq_f32(vsubq_f32(one, radial), softness), zero), one);
        a = vmulq_f32(vmulq_f32(a, vdupq_n_f32(q[6])), flow);
        a = vbslq_f32(mask, a, zero);
        value = b->accumulate ? vmulq_f32(value, vsubq_f32(one, a)) : vmaxnmq_f32(value, a);
    }
    vst1q_f32(out + i, b->accumulate ? vsubq_f32(one, value) : value);
}
#elif defined(OP_SSE_COVERAGE)
/* coverage4 on SSE: the same operations lane by lane. The values min and max see are never
 * NaN (a lane whose radial is NaN fails its test and contributes 0). */
static void coverage4(const float *p, const float *n, uint32_t i, const float *s, const unsigned char *list,
                      uint32_t first, uint32_t last, uint32_t count, float axis[][3],
                      const op_brush_settings_t *b, float soft, float limit, const uint8_t *eligible,
                      float *out) {
    const float *x = p + 3 * (uint64_t)i, *m = n + 3 * (uint64_t)i;
    const __m128 px = _mm_setr_ps(x[0], x[3], x[6], x[9]), py = _mm_setr_ps(x[1], x[4], x[7], x[10]),
                 pz = _mm_setr_ps(x[2], x[5], x[8], x[11]);
    const __m128 nx = _mm_setr_ps(m[0], m[3], m[6], m[9]), ny = _mm_setr_ps(m[1], m[4], m[7], m[10]),
                 nz = _mm_setr_ps(m[2], m[5], m[8], m[11]);
    const __m128 one = _mm_set1_ps(1), zero = _mm_setzero_ps(), threshold = _mm_set1_ps(.05f),
                 sign_bit = _mm_set1_ps(-0.0f), softness = _mm_set1_ps(soft), flow = _mm_set1_ps(b->flow);
    __m128 value = b->accumulate ? one : zero;
    for (uint32_t e = first; e < last; e++) {
        uint32_t k = list[e];
        const float *q = s + 13 * k;
        const __m128 a0 = _mm_set1_ps(axis[k][0]), a1 = _mm_set1_ps(axis[k][1]), a2 = _mm_set1_ps(axis[k][2]);
        __m128 r0 = _mm_sub_ps(px, _mm_set1_ps(q[0])), r1 = _mm_sub_ps(py, _mm_set1_ps(q[1])),
               r2 = _mm_sub_ps(pz, _mm_set1_ps(q[2]));
        __m128 depth = _mm_add_ps(_mm_add_ps(_mm_mul_ps(r0, a0), _mm_mul_ps(r1, a1)), _mm_mul_ps(r2, a2));
        float radius = fmaxf(q[7], 1e-8f);
        __m128 l0 = _mm_sub_ps(r0, _mm_mul_ps(depth, a0)), l1 = _mm_sub_ps(r1, _mm_mul_ps(depth, a1)),
               l2 = _mm_sub_ps(r2, _mm_mul_ps(depth, a2));
        __m128 lateral = _mm_add_ps(_mm_add_ps(_mm_mul_ps(l0, l0), _mm_mul_ps(l1, l1)), _mm_mul_ps(l2, l2));
        __m128 radial = _mm_div_ps(_mm_sqrt_ps(lateral), _mm_set1_ps(radius));
        __m128 facing = _mm_add_ps(_mm_add_ps(_mm_mul_ps(nx, a0), _mm_mul_ps(ny, a1)), _mm_mul_ps(nz, a2));
        __m128 mask = _mm_and_ps(_mm_and_ps(_mm_cmpgt_ps(facing, threshold), _mm_cmplt_ps(radial, one)),
                                 _mm_cmplt_ps(_mm_andnot_ps(sign_bit, depth), _mm_set1_ps(radius * limit)));
        if (eligible) {
            const uint8_t *row = eligible + (uint64_t)i * count + k;
            mask = _mm_and_ps(mask, _mm_castsi128_ps(_mm_setr_epi32(row[0] ? -1 : 0, row[count] ? -1 : 0,
                                                                    row[2 * (uint64_t)count] ? -1 : 0,
                                                                    row[3 * (uint64_t)count] ? -1 : 0)));
        }
        if (!_mm_movemask_ps(mask))
            continue;   /* no lane reached: value * (1 - 0) and fmaxf(value, 0) change nothing */
        __m128 a = _mm_min_ps(_mm_max_ps(_mm_div_ps(_mm_sub_ps(one, radial), softness), zero), one);
        a = _mm_and_ps(mask, _mm_mul_ps(_mm_mul_ps(a, _mm_set1_ps(q[6])), flow));
        value = b->accumulate ? _mm_mul_ps(value, _mm_sub_ps(one, a)) : _mm_max_ps(value, a);
    }
    _mm_storeu_ps(out + i, b->accumulate ? _mm_sub_ps(one, value) : value);
}
#endif

op_status_t op_brush_coverage(const float *p, const float *n, uint32_t texels, const float *s,
                              uint32_t count, const op_brush_settings_t *b, const float *alpha,
                              uint32_t width, uint32_t height, const uint8_t *eligible,
                              float *out) {
    if (texels > MAX_COUNT || count > 256 || !settings_ok(b) || (texels && (!p || !n || !out)) ||
        (count && !s) || (alpha && (!width || !height || (uint64_t)width * height > MAX_COUNT)) ||
        !valid(s, (uint64_t)count * 13))
        return OP_INVALID_ARGUMENT;
    float axis[256][3], t[256][3], bt[256][3], lo[3], hi[3];
    op_brush_bounds(s, count, 0, lo, hi);
    for (uint32_t k = 0; k < count; k++) {
        memcpy(axis[k], b->planar ? b->plane : s + k * 13 + 3, 12);
        if (b->planar)
            normalize(axis[k]);
        frame(s + k * 13, b, t[k], bt[k]);
    }
    float soft = fmaxf(1 - b->hardness, 1e-5f), limit = fmaxf(b->depth_limit, 1e-3f);
    stamp_grid_t grid = {0};
    int binned = count > 4 && grid_build(&grid, s, count, axis, limit, lo, hi);
    op_status_t status = OP_OK;
    for (uint32_t i = 0; i < texels; i++) {
#if defined(OP_NEON_COVERAGE) || defined(OP_SSE_COVERAGE)
        if (binned && !alpha && i + 4 <= texels) {
            /* Four texels inside the stroke's bounds and in one cell: one pass over its stamps. */
            uint64_t cell = 0;
            int same = 1;
            for (uint32_t l = 0; l < 4 && same; l++) {
                const float *x = p + 3 * (uint64_t)(i + l);
                for (int j = 0; j < 3; j++)
                    if (x[j] < lo[j] || x[j] > hi[j])
                        same = 0;
                uint64_t c = ((uint64_t)grid_cell(&grid, 2, x[2]) * grid.dims[1] + grid_cell(&grid, 1, x[1])) *
                                 grid.dims[0] + grid_cell(&grid, 0, x[0]);
                if (!l)
                    cell = c;
                else if (c != cell)
                    same = 0;
            }
            if (same) {
                coverage4(p, n, i, s, grid.stamps, grid.start[cell], grid.start[cell + 1], count, axis, b, soft,
                          limit, eligible, out);
                i += 3;
                continue;
            }
        }
#endif
        float value = b->accumulate ? 1.0f : 0.0f;
        int inside = 1;
        for (int j = 0; j < 3; j++)
            if (p[i * 3 + j] < lo[j] || p[i * 3 + j] > hi[j])
                inside = 0;
        if (inside) {
            uint32_t first = 0, last = count;
            if (binned) {
                uint64_t c = ((uint64_t)grid_cell(&grid, 2, p[i * 3 + 2]) * grid.dims[1] +
                              grid_cell(&grid, 1, p[i * 3 + 1])) * grid.dims[0] +
                             grid_cell(&grid, 0, p[i * 3]);
                first = grid.start[c];
                last = grid.start[c + 1];
            }
            for (uint32_t e = first; e < last; e++) {
                uint32_t k = binned ? grid.stamps[e] : e;
                float a;
                if (stamp_alpha(p, n, i, s + 13 * k, k, count, axis[k], t[k], bt[k], b, soft, limit,
                                alpha, width, height, eligible, &a)) {
                    status = OP_INVALID_ARGUMENT;
                    goto done;
                }
                if (b->accumulate)
                    value *= 1 - a;
                else
                    value = fmaxf(value, a);
            }
        }
        out[i] = b->accumulate ? 1 - value : value;
    }
done:
    free(grid.start);
    free(grid.stamps);
    return status;
}
op_status_t op_surface_walk(uint32_t triangles, uint32_t samples, const uint64_t *offsets,
                            const uint32_t *neighbors, uint64_t edges, const float *normals,
                            const float *lo, const float *hi, const float *axes, const float *boxlo,
                            const float *boxhi, uint8_t *visited) {
    if (triangles > MAX_COUNT || samples > 256 || (uint64_t)triangles * samples > MAX_COUNT ||
        !offsets || offsets[0] || offsets[triangles] != edges || edges > 134217728 ||
        (edges && !neighbors) || (triangles && (!normals || !lo || !hi)) ||
        (samples && (!axes || !boxlo || !boxhi)) || (triangles && samples && !visited))
        return OP_INVALID_ARGUMENT;
    for (uint32_t i = 0; i < triangles; i++)
        if (offsets[i] > offsets[i + 1])
            return OP_INVALID_ARGUMENT;
    for (uint64_t i = 0; i < edges; i++)
        if (neighbors[i] >= triangles)
            return OP_INVALID_ARGUMENT;
    uint32_t *queue = malloc((size_t)triangles * sizeof(*queue));
    if (triangles && !queue)
        return OP_RESOURCE_EXHAUSTED;
    for (uint32_t s = 0; s < samples; s++) {
        uint32_t head = 0, tail = 0;
        for (uint32_t i = 0; i < triangles; i++)
            if (visited[(uint64_t)i * samples + s])
                queue[tail++] = i;
        while (head < tail) {
            uint32_t a = queue[head++];
            for (uint64_t e = offsets[a]; e < offsets[a + 1]; e++) {
                uint32_t t = neighbors[e];
                uint8_t *v = visited + (uint64_t)t * samples + s;
                if (*v)
                    continue;
                int inside = dot(normals + t * 3, axes + s * 3) > .05f;
                for (int j = 0; j < 3; j++)
                    if (!(lo[t * 3 + j] <= boxhi[s * 3 + j] && hi[t * 3 + j] >= boxlo[s * 3 + j]))
                        inside = 0;
                if (inside) {
                    *v = 1;
                    queue[tail++] = t;
                }
            }
        }
    }
    free(queue);
    return OP_OK;
}
static double dotd(const double *a, const double *b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
op_status_t op_triangle_distances(const double *p, const double *c, uint32_t count, double *out) {
    if (count > MAX_COUNT || (count && (!p || !c || !out)))
        return OP_INVALID_ARGUMENT;
    for (uint32_t i = 0; i < count; i++, p += 3, c += 9) {
        double ab[3], ac[3], ap[3], bp[3], cp[3], q[3];
        for (int j = 0; j < 3; j++) {
            ab[j] = c[3 + j] - c[j];
            ac[j] = c[6 + j] - c[j];
            ap[j] = p[j] - c[j];
            bp[j] = p[j] - c[3 + j];
            cp[j] = p[j] - c[6 + j];
        }
        double d1 = dotd(ab, ap), d2 = dotd(ac, ap), d3 = dotd(ab, bp), d4 = dotd(ac, bp),
               d5 = dotd(ab, cp), d6 = dotd(ac, cp);
        double vc = d1 * d4 - d3 * d2, vb = d5 * d2 - d1 * d6, va = d3 * d6 - d5 * d4, v = 0, w = 0;
        if (d1 <= 0 && d2 <= 0) {
        } else if (d3 >= 0 && d4 <= d3)
            v = 1;
        else if (d6 >= 0 && d5 <= d6)
            w = 1;
        else if (vc <= 0 && d1 >= 0 && d3 <= 0)
            v = d1 - d3 != 0 ? d1 / (d1 - d3) : 0;
        else if (vb <= 0 && d2 >= 0 && d6 <= 0)
            w = d2 - d6 != 0 ? d2 / (d2 - d6) : 0;
        else if (va <= 0 && d4 - d3 >= 0 && d5 - d6 >= 0) {
            double den = (d4 - d3) + (d5 - d6);
            w = den != 0 ? (d4 - d3) / den : 0;
            v = 1 - w;
        } else {
            double inv = va + vb + vc != 0 ? 1 / (va + vb + vc) : 0;
            v = vb * inv;
            w = vc * inv;
        }
        for (int j = 0; j < 3; j++)
            q[j] = p[j] - (c[j] + ab[j] * v + ac[j] * w);
        out[i] = sqrt(dotd(q, q));
    }
    return OP_OK;
}
op_status_t op_uv_window(const double *c, const double *uv, uint32_t count, const double lo[3],
                         const double hi[3], double margin, float *out) {
    if (count > MAX_COUNT || !lo || !hi || !isfinite(margin) || (count && (!c || !uv || !out)))
        return OP_INVALID_ARGUMENT;
    for (uint32_t i = 0; i < count; i++, c += 9, uv += 6, out += 4) {
        double a[3], b[3];
        for (int j = 0; j < 3; j++) {
            a[j] = c[3 + j] - c[j];
            b[j] = c[6 + j] - c[j];
        }
        double d00 = dotd(a, a), d01 = dotd(a, b), d11 = dotd(b, b), den = d00 * d11 - d01 * d01,
               low[2] = {INFINITY, INFINITY}, high[2] = {-INFINITY, -INFINITY};
        if (fabs(den) <= 1e-18) {
            for (int j = 0; j < 2; j++) {
                out[j] = (float)fmin(uv[j], fmin(uv[2 + j], uv[4 + j]));
                out[2 + j] = (float)fmax(uv[j], fmax(uv[2 + j], uv[4 + j]));
            }
            continue;
        }
        for (int k = 0; k < 8; k++) {
            double d[3];
            for (int j = 0; j < 3; j++)
                d[j] = ((k >> j) & 1 ? hi[j] : lo[j]) - c[j];
            double d20 = dotd(d, a), d21 = dotd(d, b), v = (d11 * d20 - d01 * d21) / den,
                   w = (d00 * d21 - d01 * d20) / den, u = 1 - v - w;
            for (int j = 0; j < 2; j++) {
                double q = u * uv[j] + v * uv[2 + j] + w * uv[4 + j];
                low[j] = fmin(low[j], q);
                high[j] = fmax(high[j], q);
            }
        }
        for (int j = 0; j < 2; j++) {
            out[j] = (float)(low[j] - margin);
            out[2 + j] = (float)(high[j] + margin);
        }
    }
    return OP_OK;
}
