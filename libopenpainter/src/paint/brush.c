#include "openpainter/op_paint.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
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
op_status_t op_brush_bounds(const float *s, uint32_t count, float margin, float *lo, float *hi) {
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
    for (uint32_t i = 0; i < texels; i++) {
        float value = b->accumulate ? 1 : 0;
        int inside = 1;
        for (int j = 0; j < 3; j++)
            if (p[i * 3 + j] < lo[j] || p[i * 3 + j] > hi[j])
                inside = 0;
        for (uint32_t k = 0; inside && k < count; k++) {
            const float *q = s + 13 * k;
            float rel[3], lat[3];
            for (int j = 0; j < 3; j++)
                rel[j] = p[i * 3 + j] - q[j];
            float depth = dot(rel, axis[k]), radius = fmaxf(q[7], 1e-8f);
            for (int j = 0; j < 3; j++)
                lat[j] = rel[j] - depth * axis[k][j];
            float radial = sqrtf(dot(lat, lat)) / radius, a = 0;
            if (dot(n + 3 * i, axis[k]) > .05f && radial < 1 && fabsf(depth) < radius * limit &&
                (!eligible || eligible[(uint64_t)i * count + k])) {
                a = fminf(fmaxf((1 - radial) / soft, 0), 1) * q[6] * b->flow;
                if (alpha) {
                    float u = dot(lat, t[k]) / radius * .5f + .5f,
                          v = dot(lat, bt[k]) / radius * .5f + .5f;
                    if (!isfinite(u) || !isfinite(v))
                        return OP_INVALID_ARGUMENT;
                    uint32_t x = (uint32_t)fminf(fmaxf(u * width, 0), (float)(width - 1)),
                             y = (uint32_t)fminf(fmaxf(v * height, 0), (float)(height - 1));
                    a *= alpha[(uint64_t)y * width + x];
                }
            }
            if (b->accumulate)
                value *= 1 - a;
            else
                value = fmaxf(value, a);
        }
        out[i] = b->accumulate ? 1 - value : value;
    }
    return OP_OK;
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
op_status_t op_uv_window(const double *c, const double *uv, uint32_t count, const double *lo,
                         const double *hi, double margin, float *out) {
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
