/* op_brush_coverage (stamps binned on a grid) against the loop over every stamp it replaced,
 * kept verbatim below: the outputs must be bit-identical, including FLOW's product order,
 * alpha lookups, eligibility and texels outside the stroke. */
#include "openpainter/op_paint.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
    double c = cos(b->angle), sn = sin(b->angle);
    for (int j = 0; j < 3; j++)
        t[j] = (float)(t[j] * c + bt[j] * sn);
    cross(s + 3, t, bt);
}

static int reference(const float *p, const float *n, uint32_t texels, const float *s, uint32_t count,
                     const op_brush_settings_t *b, const float *alpha, uint32_t width, uint32_t height,
                     const uint8_t *eligible, float *out) {
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
        float value = b->accumulate ? 1.0f : 0.0f;
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

static uint64_t state = 0x9e3779b97f4a7c15u;
static float uniform(float lo, float hi) {
    state ^= state << 13;
    state ^= state >> 7;
    state ^= state << 17;
    return lo + (hi - lo) * (float)((state >> 11) * (1.0 / 9007199254740992.0));
}

int main(void) {
    enum { TEXELS = 20000 };
    float *p = malloc(sizeof(float) * 3 * TEXELS), *n = malloc(sizeof(float) * 3 * TEXELS),
          *mine = malloc(sizeof(float) * TEXELS), *theirs = malloc(sizeof(float) * TEXELS);
    uint8_t *eligible = malloc((size_t)TEXELS * 256);
    float samples[256 * 13], alpha[16 * 12];
    for (int j = 0; j < 16 * 12; j++)
        alpha[j] = uniform(0, 1);
    assert(p && n && mine && theirs && eligible);
    long long pairs = 0;
    for (int trial = 0; trial < 400; trial++) {
        uint32_t count = 1 + (uint32_t)uniform(0, 256), texels = 1 + (uint32_t)uniform(0, TEXELS - 1);
        float offset = uniform(-20, 20), radius = uniform(.002f, .2f), wobble = uniform(0, 1);
        /* A stroke along a curve on a wavy sheet, some texels well off the stroke. */
        for (uint32_t k = 0; k < count; k++) {
            float *q = samples + 13 * k, u = (float)k / (float)count;
            q[0] = offset + u * uniform(.5f, 3);
            q[1] = offset + .3f * sinf(u * 7) * wobble;
            q[2] = .05f * sinf(q[0] * 5);
            float normal[3] = {uniform(-.3f, .3f), uniform(-.3f, .3f), 1};
            if (trial % 3)
                normalize(normal);   /* sample normals are not always unit length */
            memcpy(q + 3, normal, 12);
            q[6] = uniform(.2f, 1);
            q[7] = radius * uniform(.3f, 1.5f);
            q[8] = 1;
            q[9] = 0;
            q[10] = uniform(-1, 1);
            q[11] = uniform(-1, 1);
            q[12] = 0;
        }
        float lo[3], hi[3];
        op_brush_bounds(samples, count, 0, lo, hi);
        /* Half the trials scan a raster over the bounds, as a UV rasterizer hands texels over:
         * neighbours share grid cells (the vectorized path); the others scatter them. */
        uint32_t columns = 1 + (uint32_t)sqrtf((float)texels);
        for (uint32_t i = 0; i < texels; i++) {
            float x = uniform(lo[0] - .1f, hi[0] + .1f), y = uniform(lo[1] - .1f, hi[1] + .1f);
            if (trial % 4 >= 2) {
                x = lo[0] - .05f + (hi[0] - lo[0] + .1f) * (float)(i % columns) / (float)columns;
                y = lo[1] - .05f + (hi[1] - lo[1] + .1f) * (float)(i / columns) / (float)columns;
            }
            p[3 * i] = x;
            p[3 * i + 1] = y;
            p[3 * i + 2] = .05f * sinf(x * 5) + uniform(-radius, radius) * (trial % 2);
            float normal[3] = {uniform(-.4f, .4f), uniform(-.4f, .4f), trial % 7 ? 1.0f : -1.0f};
            normalize(normal);
            memcpy(n + 3 * i, normal, 12);
        }
        int with_eligible = trial % 4 == 1, with_alpha = trial % 5 == 2;
        if (with_eligible)
            for (uint64_t j = 0; j < (uint64_t)texels * count; j++)
                eligible[j] = uniform(0, 1) < .8f;
        op_brush_settings_t b = {.hardness = uniform(0, 1), .flow = uniform(.1f, 1), .angle = uniform(-3, 3),
                                 .depth_limit = trial % 6 ? uniform(.2f, 3) : 0, .accumulate = trial % 2,
                                 .rake = trial % 3 == 0, .planar = trial % 8 == 3,
                                 .plane = {uniform(-.2f, .2f), uniform(-.2f, .2f), 1}};
        const float *a = with_alpha ? alpha : NULL;
        const uint8_t *e = with_eligible ? eligible : NULL;
        int expected = reference(p, n, texels, samples, count, &b, a, 16, 12, e, theirs);
        int found = op_brush_coverage(p, n, texels, samples, count, &b, a, 16, 12, e, mine);
        if (expected != found || (expected == OP_OK && memcmp(mine, theirs, sizeof(float) * texels))) {
            for (uint32_t i = 0; i < texels; i++)
                if (mine[i] != theirs[i]) {
                    fprintf(stderr, "trial %d texel %u: %.9g != %.9g\n", trial, i, mine[i], theirs[i]);
                    break;
                }
            fprintf(stderr, "trial %d: status %d != %d\n", trial, found, expected);
            return 1;
        }
        pairs += (long long)texels * count;
    }
    printf("brush coverage matches its reference over %lld texel-stamp pairs\n", pairs);
    free(p);
    free(n);
    free(mine);
    free(theirs);
    free(eligible);
    return 0;
}
