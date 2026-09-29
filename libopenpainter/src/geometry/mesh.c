#include "openpainter/op_geometry.h"
#include <math.h>
#include <stdlib.h>

#define MAX_TRIANGLES 8388608u
#define MAX_PAIRS 33554432u
typedef struct edge {
    int64_t a, b;
    uint32_t triangle;
} edge_t;
typedef struct pair {
    uint32_t a, b;
} pair_t;
static int edges_compare(const void *av, const void *bv) {
    const edge_t *a = av, *b = bv;
    if (a->a != b->a)
        return (a->a > b->a) - (a->a < b->a);
    if (a->b != b->b)
        return (a->b > b->b) - (a->b < b->b);
    return (a->triangle > b->triangle) - (a->triangle < b->triangle);
}
static int pairs_compare(const void *av, const void *bv) {
    const pair_t *a = av, *b = bv;
    if (a->a != b->a)
        return (a->a > b->a) - (a->a < b->a);
    return (a->b > b->b) - (a->b < b->b);
}
static uint64_t group_end(const edge_t *edges, uint64_t at, uint64_t count) {
    uint64_t end = at + 1;
    while (end < count && edges[end].a == edges[at].a && edges[end].b == edges[at].b)
        end++;
    return end;
}
op_status_t op_mesh_adjacency(const int64_t *vertices, uint32_t triangles, int64_t **offsets,
                              int64_t **indices, uint64_t *count) {
    if (!offsets || !indices || !count || triangles > MAX_TRIANGLES || (triangles && !vertices))
        return OP_INVALID_ARGUMENT;
    *offsets = NULL;
    *indices = NULL;
    *count = 0;
    uint64_t n = (uint64_t)triangles * 3, total = 0;
    edge_t *edges = n ? malloc(n * sizeof(*edges)) : NULL;
    if (n && !edges)
        return OP_RESOURCE_EXHAUSTED;
    for (uint64_t i = 0; i < n; i++) {
        int64_t a = vertices[i], b = vertices[(i / 3) * 3 + (i + 1) % 3];
        if (a < 0 || b < 0) {
            free(edges);
            return OP_VALIDATION_FAILED;
        }
        edges[i] = (edge_t){a < b ? a : b, a < b ? b : a, (uint32_t)(i / 3)};
    }
    if (n)
        qsort(edges, n, sizeof(*edges), edges_compare);
    for (uint64_t at = 0; at < n;) {
        uint64_t end = group_end(edges, at, n), width = end - at;
        /* Check before enumerating a potentially pathological non-manifold edge. */
        if (width * (width - 1) > MAX_PAIRS - total) {
            free(edges);
            return OP_RESOURCE_EXHAUSTED;
        }
        if (width == 2)
            total += 2; /* Preserve the reference's degenerate-edge entries. */
        else if (width > 2)
            for (uint64_t a = at; a < end; a++)
                for (uint64_t b = at; b < end; b++)
                    total += edges[a].triangle != edges[b].triangle;
        at = end;
    }
    pair_t *pairs = total ? malloc(total * sizeof(*pairs)) : NULL;
    int64_t *ptr = calloc((size_t)triangles + 1, sizeof(*ptr));
    int64_t *idx = total ? malloc(total * sizeof(*idx)) : NULL;
    if (!ptr || (total && (!pairs || !idx))) {
        free(edges);
        free(pairs);
        free(ptr);
        free(idx);
        return OP_RESOURCE_EXHAUSTED;
    }
    uint64_t written = 0;
    for (uint64_t at = 0; at < n;) {
        uint64_t end = group_end(edges, at, n), width = end - at;
        if (width == 2) {
            pairs[written++] = (pair_t){edges[at].triangle, edges[at + 1].triangle};
            pairs[written++] = (pair_t){edges[at + 1].triangle, edges[at].triangle};
        } else if (width > 2)
            for (uint64_t a = at; a < end; a++)
                for (uint64_t b = at; b < end; b++)
                    if (edges[a].triangle != edges[b].triangle)
                        pairs[written++] = (pair_t){edges[a].triangle, edges[b].triangle};
        at = end;
    }
    free(edges);
    if (total)
        qsort(pairs, total, sizeof(*pairs), pairs_compare);
    for (uint64_t i = 0; i < total; i++) {
        ptr[pairs[i].a + 1]++;
        idx[i] = pairs[i].b;
    }
    for (uint32_t i = 0; i < triangles; i++)
        ptr[i + 1] += ptr[i];
    free(pairs);
    *offsets = ptr;
    *indices = idx;
    *count = total;
    return OP_OK;
}

op_status_t op_mesh_frames(const double *corners, const double *uv, uint32_t triangles,
                           double *frames) {
    if (triangles > MAX_TRIANGLES || (triangles && (!corners || !uv || !frames)))
        return OP_INVALID_ARGUMENT;
    for (uint64_t i = 0; i < (uint64_t)triangles * 9; i++)
        if (!isfinite(corners[i]))
            return OP_VALIDATION_FAILED;
    for (uint64_t i = 0; i < (uint64_t)triangles * 6; i++)
        if (!isfinite(uv[i]))
            return OP_VALIDATION_FAILED;
    for (uint32_t i = 0; i < triangles; i++) {
        const double *p = corners + (uint64_t)i * 9, *u = uv + (uint64_t)i * 6;
        double *out = frames + (uint64_t)i * 6;
        double a = u[2] - u[0], b = u[3] - u[1], c = u[4] - u[0], d = u[5] - u[1];
        double det = a * d - b * c;
        double inverse[4] = {0};
        if (fabs(det) > 1e-14) {
            inverse[0] = d / det;
            inverse[1] = -b / det;
            inverse[2] = -c / det;
            inverse[3] = a / det;
        }
        for (unsigned axis = 0; axis < 2; axis++) {
            double length = 0;
            for (unsigned k = 0; k < 3; k++) {
                out[axis * 3 + k] = inverse[axis * 2] * (p[3 + k] - p[k]) +
                                    inverse[axis * 2 + 1] * (p[6 + k] - p[k]);
                length += out[axis * 3 + k] * out[axis * 3 + k];
            }
            length = fmax(sqrt(length), 1e-12);
            for (unsigned k = 0; k < 3; k++)
                out[axis * 3 + k] /= length;
        }
    }
    return OP_OK;
}
op_status_t op_mesh_interpolate(const float *weights, const float *corners, uint32_t samples,
                                uint32_t components, float *values) {
    if (!components || components > 16 || samples > MAX_PAIRS ||
        (samples && (!weights || !corners || !values)))
        return OP_INVALID_ARGUMENT;
    for (uint32_t i = 0; i < samples; i++)
        for (uint32_t c = 0; c < components; c++) {
            const float *w = weights + (uint64_t)i * 3,
                        *p = corners + (uint64_t)i * 3 * components + c;
            values[(uint64_t)i * components + c] =
                w[0] * p[0] + w[1] * p[components] + w[2] * p[2 * components];
        }
    return OP_OK;
}

op_status_t op_mesh_jacobians(const double *corners, const double *uv, uint32_t triangles,
                              uint32_t size, float *jacobians) {
    if (triangles > MAX_TRIANGLES || !size || size > 32768 ||
        (triangles && (!corners || !uv || !jacobians)))
        return OP_INVALID_ARGUMENT;
    for (uint64_t i = 0; i < (uint64_t)triangles * 9; i++)
        if (!isfinite(corners[i]))
            return OP_VALIDATION_FAILED;
    for (uint64_t i = 0; i < (uint64_t)triangles * 6; i++)
        if (!isfinite(uv[i]))
            return OP_VALIDATION_FAILED;
    for (uint32_t i = 0; i < triangles; i++) {
        const double *p = corners + (uint64_t)i * 9, *u = uv + (uint64_t)i * 6;
        double e[2][3], f[2][2], gram[2][2] = {{0}}, inverse[2][2] = {{0}}, product[2][2] = {{0}};
        for (unsigned a = 0; a < 2; a++) {
            for (unsigned j = 0; j < 3; j++)
                e[a][j] = p[(a + 1) * 3 + j] - p[j];
            for (unsigned j = 0; j < 2; j++)
                f[a][j] = u[(a + 1) * 2 + j] * size - u[j] * size;
        }
        for (unsigned a = 0; a < 2; a++)
            for (unsigned b = 0; b < 2; b++)
                for (unsigned j = 0; j < 3; j++)
                    gram[a][b] += e[a][j] * e[b][j];
        double det = gram[0][0] * gram[1][1] - gram[0][1] * gram[1][0];
        if (fabs(det) > 1e-18) {
            inverse[0][0] = gram[1][1] / det;
            inverse[1][1] = gram[0][0] / det;
            inverse[0][1] = -gram[0][1] / det;
            inverse[1][0] = -gram[1][0] / det;
        }
        for (unsigned a = 0; a < 2; a++)
            for (unsigned b = 0; b < 2; b++)
                for (unsigned j = 0; j < 2; j++)
                    product[a][b] += f[j][a] * inverse[j][b];
        for (unsigned a = 0; a < 2; a++)
            for (unsigned j = 0; j < 3; j++)
                jacobians[(uint64_t)i * 6 + a * 3 + j] =
                    (float)(product[a][0] * e[0][j] + product[a][1] * e[1][j]);
    }
    return OP_OK;
}
op_status_t op_mesh_directions(const int32_t *triangle, uint32_t texels, const float *jacobians,
                               uint32_t triangles, const float world[3], float *directions) {
    if (triangles > MAX_TRIANGLES || texels > 67108864u || !world || (triangles && !jacobians) ||
        (texels && (!triangle || !directions)))
        return OP_INVALID_ARGUMENT;
    for (unsigned j = 0; j < 3; j++)
        if (!isfinite(world[j]))
            return OP_VALIDATION_FAILED;
    for (uint32_t i = 0; i < texels; i++)
        if (triangle[i] >= 0 && (uint32_t)triangle[i] >= triangles)
            return OP_VALIDATION_FAILED;
    float length = sqrtf(world[0] * world[0] + world[1] * world[1] + world[2] * world[2]), axis[3];
    for (unsigned j = 0; j < 3; j++)
        axis[j] = world[j] / fmaxf(length, 1e-12f);
    for (uint32_t i = 0; i < texels; i++) {
        float *out = directions + (uint64_t)i * 2;
        out[0] = out[1] = 0;
        if (triangle[i] < 0)
            continue;
        const float *m = jacobians + (uint64_t)triangle[i] * 6;
        float x = m[0] * axis[0] + m[1] * axis[1] + m[2] * axis[2],
              y = m[3] * axis[0] + m[4] * axis[1] + m[5] * axis[2];
        float magnitude = sqrtf(x * x + y * y);
        if (magnitude > 1e-9f) {
            out[0] = x / magnitude;
            out[1] = y / magnitude;
        }
    }
    return OP_OK;
}
