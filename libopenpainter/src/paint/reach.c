#include "openpainter/op_reach.h"
#include "openpainter/op_paint.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#define MAX_TRIANGLES 8388608u
#define MAX_SAMPLES 1048576u
#define MAX_PAIRS 33554432u
typedef struct polygon {
    int64_t id;
    uint32_t triangle;
} polygon_t;
struct op_reach {
    uint32_t count;
    float *corners, *normals, *low, *high;
    polygon_t *polygons;
    op_bounds_t *bounds;
};
static int compare_polygon(const void *av, const void *bv) {
    const polygon_t *a = av, *b = bv;
    if (a->id != b->id)
        return (a->id > b->id) - (a->id < b->id);
    return (a->triangle > b->triangle) - (a->triangle < b->triangle);
}
void op_reach_destroy(op_reach_t *r) {
    if (!r)
        return;
    free(r->corners);
    free(r->normals);
    free(r->low);
    free(r->high);
    free(r->polygons);
    op_bounds_destroy(r->bounds);
    free(r);
}
uint64_t op_reach_bytes(const op_reach_t *r) {
    return r ? sizeof(*r) + (uint64_t)r->count * (18 * sizeof(float) + sizeof(polygon_t)) +
                   op_bounds_bytes(r->bounds)
             : 0;
}
op_status_t op_reach_create(const double *corners, const int64_t *polygons, uint32_t count,
                            uint32_t mirrored, op_reach_t **out) {
    if (!out || count > MAX_TRIANGLES || mirrored > 1 || (count && (!corners || !polygons)))
        return OP_INVALID_ARGUMENT;
    *out = NULL;
    for (uint64_t i = 0; i < (uint64_t)count * 9; i++)
        if (!isfinite(corners[i]) || !isfinite((float)corners[i]))
            return OP_VALIDATION_FAILED;
    op_reach_t *r = calloc(1, sizeof(*r));
    if (!r)
        return OP_RESOURCE_EXHAUSTED;
    r->count = count;
    r->corners = malloc(((size_t)count * 9 + 1) * sizeof(float));
    r->normals = malloc(((size_t)count * 3 + 1) * sizeof(float));
    r->low = malloc(((size_t)count * 3 + 1) * sizeof(float));
    r->high = malloc(((size_t)count * 3 + 1) * sizeof(float));
    r->polygons = malloc(((size_t)count + 1) * sizeof(polygon_t));
    double *low = malloc(((size_t)count * 3 + 1) * sizeof(double));
    double *high = malloc(((size_t)count * 3 + 1) * sizeof(double));
    op_status_t status = OP_RESOURCE_EXHAUSTED;
    if (!r->corners || !r->normals || !r->low || !r->high || !r->polygons || !low || !high)
        goto done;
    for (uint32_t i = 0; i < count; i++) {
        const double *c = corners + (uint64_t)i * 9;
        double a[3], b[3], normal[3];
        for (unsigned j = 0; j < 3; j++) {
            a[j] = c[3 + j] - c[j];
            b[j] = c[6 + j] - c[j];
        }
        for (unsigned j = 0; j < 3; j++)
            normal[j] = a[(j + 1) % 3] * b[(j + 2) % 3] - a[(j + 2) % 3] * b[(j + 1) % 3];
        double length = sqrt(normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2]);
        for (unsigned j = 0; j < 3; j++) {
            r->normals[(uint64_t)i * 3 + j] =
                (float)(length > 1e-18 ? normal[j] / length * (mirrored ? -1 : 1) : 0);
            low[(uint64_t)i * 3 + j] = r->low[(uint64_t)i * 3 + j] =
                (float)fmin(c[j], fmin(c[3 + j], c[6 + j]));
            high[(uint64_t)i * 3 + j] = r->high[(uint64_t)i * 3 + j] =
                (float)fmax(c[j], fmax(c[3 + j], c[6 + j]));
        }
        for (unsigned j = 0; j < 9; j++)
            r->corners[(uint64_t)i * 9 + j] = (float)c[j];
        r->polygons[i] = (polygon_t){polygons[i], i};
    }
    if (count)
        qsort(r->polygons, count, sizeof(*r->polygons), compare_polygon);
    status = op_bounds_create(low, high, count, &r->bounds);
done:
    free(low);
    free(high);
    if (status)
        op_reach_destroy(r);
    else
        *out = r;
    return status;
}
op_status_t op_reach_copy(const op_reach_t *r, float *corners, float *normals, float *low,
                          float *high, int64_t *order, int64_t *polygons) {
    if (!r || (r->count && (!corners || !normals || !low || !high || !order || !polygons)))
        return OP_INVALID_ARGUMENT;
    if (r->count) {
        memcpy(corners, r->corners, (size_t)r->count * 9 * sizeof(float));
        memcpy(normals, r->normals, (size_t)r->count * 3 * sizeof(float));
        memcpy(low, r->low, (size_t)r->count * 3 * sizeof(float));
        memcpy(high, r->high, (size_t)r->count * 3 * sizeof(float));
    }
    for (uint32_t i = 0; i < r->count; i++) {
        order[i] = r->polygons[i].triangle;
        polygons[i] = r->polygons[i].id;
    }
    return OP_OK;
}
op_status_t op_reach_query(const op_reach_t *r, const double low[3], const double high[3],
                           uint32_t *indices, uint32_t capacity, uint32_t *written) {
    return r ? op_bounds_query(r->bounds, low, high, indices, capacity, written)
             : OP_INVALID_ARGUMENT;
}
static int finite_array(const float *a, uint64_t n) {
    if (n && !a)
        return 0;
    for (uint64_t i = 0; i < n; i++)
        if (!isfinite(a[i]))
            return 0;
    return 1;
}
static double distance(const op_reach_t *r, uint32_t triangle, const float *point) {
    double p[3], c[9], result;
    for (unsigned i = 0; i < 3; i++)
        p[i] = point[i];
    for (unsigned i = 0; i < 9; i++)
        c[i] = r->corners[(uint64_t)triangle * 9 + i];
    op_triangle_distances(p, c, 1, &result);
    return result;
}
static op_status_t append(op_reach_pair_t **pairs, uint64_t *count, uint64_t *capacity,
                          uint32_t triangle, uint32_t sample) {
    if (*count == MAX_PAIRS)
        return OP_RESOURCE_EXHAUSTED;
    if (*count == *capacity) {
        uint64_t next = *capacity ? *capacity * 2 : 64;
        if (next > MAX_PAIRS)
            next = MAX_PAIRS;
        op_reach_pair_t *p = realloc(*pairs, next * sizeof(*p));
        if (!p)
            return OP_RESOURCE_EXHAUSTED;
        *pairs = p;
        *capacity = next;
    }
    (*pairs)[(*count)++] = (op_reach_pair_t){triangle, sample};
    return OP_OK;
}
op_status_t op_reach_validate(const op_reach_t *r, const float *anchors, const float *radii,
                              uint32_t samples, op_reach_pair_t **pairs, uint64_t *count,
                              uint8_t *resolved) {
    if (!pairs || !count)
        return OP_INVALID_ARGUMENT;
    *pairs = NULL;
    *count = 0;
    if (!r || samples > MAX_SAMPLES || (samples && !resolved))
        return OP_INVALID_ARGUMENT;
    if (!finite_array(anchors, (uint64_t)samples * 4) || !finite_array(radii, samples))
        return OP_VALIDATION_FAILED;
    if (samples)
        memset(resolved, 0, samples);
    uint64_t capacity = 0;
    for (uint32_t s = 0; s < samples; s++) {
        const float *a = anchors + (uint64_t)s * 4;
        /* Avoid undefined conversion at the int64 endpoints; negative means unknown. */
        if (a[3] <= -1 || (double)a[3] >= 9223372036854775808.0)
            continue;
        int64_t polygon = (int64_t)a[3];
        uint32_t lo = 0, hi = r->count;
        while (lo < hi) {
            uint32_t mid = lo + (hi - lo) / 2;
            if (r->polygons[mid].id < polygon)
                lo = mid + 1;
            else
                hi = mid;
        }
        float tolerance = fmaxf(1e-3f * radii[s], 1e-5f);
        for (uint32_t at = lo; at < r->count && r->polygons[at].id == polygon; at++) {
            uint32_t triangle = r->polygons[at].triangle;
            if (distance(r, triangle, a) > tolerance)
                continue;
            op_status_t status = append(pairs, count, &capacity, triangle, s);
            if (status) {
                free(*pairs);
                *pairs = NULL;
                *count = 0;
                memset(resolved, 0, samples);
                return status;
            }
            resolved[s] = 1;
        }
    }
    return OP_OK;
}
op_status_t op_reach_nearest(const op_reach_t *r, const float *anchors, const float *axes,
                             const float *band, const uint8_t *wanted, uint32_t samples,
                             int64_t *triangles, double *distances) {
    if (!r || samples > MAX_SAMPLES || (samples && (!wanted || !triangles || !distances)))
        return OP_INVALID_ARGUMENT;
    if (!finite_array(anchors, (uint64_t)samples * 4) ||
        !finite_array(axes, (uint64_t)samples * 3) || !finite_array(band, samples))
        return OP_VALIDATION_FAILED;
    uint32_t *near = malloc(((size_t)r->count + 1) * sizeof(*near));
    if (!near)
        return OP_RESOURCE_EXHAUSTED;
    for (uint32_t s = 0; s < samples; s++) {
        triangles[s] = -1;
        distances[s] = INFINITY;
        if (!wanted[s] || band[s] < 0)
            continue;
        const float *point = anchors + (uint64_t)s * 4, *axis = axes + (uint64_t)s * 3;
        double lo[3], hi[3];
        for (unsigned j = 0; j < 3; j++) {
            lo[j] = (float)(point[j] - band[s]);
            hi[j] = (float)(point[j] + band[s]);
        }
        uint32_t count = 0;
        op_status_t status = op_bounds_query(r->bounds, lo, hi, near, r->count, &count);
        if (status) {
            free(near);
            return status;
        }
        double best = INFINITY;
        int64_t found = -1;
        for (uint32_t at = 0; at < count; at++) {
            uint32_t t = near[at];
            const float *normal = r->normals + (uint64_t)t * 3;
            float facing = normal[0] * axis[0] + normal[1] * axis[1] + normal[2] * axis[2];
            if (facing <= .05f)
                continue;
            double d = distance(r, t, point);
            if (d < best) {
                best = d;
                found = t;
            }
        }
        if (best <= band[s]) {
            triangles[s] = found;
            distances[s] = best;
        }
    }
    free(near);
    return OP_OK;
}
op_status_t op_reach_seeds(const op_reach_t *r, const float *anchors, const float *axes,
                           const float *radii, const float *band, const uint8_t *fallback,
                           uint32_t samples, op_reach_pair_t **pairs, uint64_t *count,
                           uint8_t *resolved) {
    if (!pairs || !count)
        return OP_INVALID_ARGUMENT;
    *pairs = NULL;
    *count = 0;
    if (samples > MAX_SAMPLES || (samples && !fallback))
        return OP_INVALID_ARGUMENT;
    op_status_t status = op_reach_validate(r, anchors, radii, samples, pairs, count, resolved);
    if (status)
        return status;
    uint8_t *wanted = calloc((size_t)samples + 1, 1);
    int64_t *triangles = malloc(((size_t)samples + 1) * sizeof(*triangles));
    double *distances = malloc(((size_t)samples + 1) * sizeof(*distances));
    if (!wanted || !triangles || !distances) {
        status = OP_RESOURCE_EXHAUSTED;
        goto done;
    }
    for (uint32_t s = 0; s < samples; s++)
        wanted[s] = !resolved[s] && fallback[s];
    status = op_reach_nearest(r, anchors, axes, band, wanted, samples, triangles, distances);
    if (status)
        goto done;
    /* realloc can conservatively grow from the used count; it owns the buffer. */
    uint64_t capacity = *count;
    for (uint32_t s = 0; s < samples; s++)
        if (triangles[s] >= 0) {
            status = append(pairs, count, &capacity, (uint32_t)triangles[s], s);
            if (status)
                goto done;
            resolved[s] = 1;
        }
done:
    free(wanted);
    free(triangles);
    free(distances);
    if (status) {
        free(*pairs);
        *pairs = NULL;
        *count = 0;
        if (samples && resolved)
            memset(resolved, 0, samples);
    }
    return status;
}
op_status_t op_reach_choose(const uint8_t *resolved, const double *distances, uint32_t targets,
                            uint32_t samples, uint8_t *fallback) {
    uint64_t count = (uint64_t)targets * samples;
    if (targets > 65536 || samples > MAX_SAMPLES || count > MAX_PAIRS ||
        (count && (!resolved || !distances || !fallback)))
        return OP_INVALID_ARGUMENT;
    for (uint64_t i = 0; i < count; i++)
        if (isnan(distances[i]) || distances[i] < 0)
            return OP_VALIDATION_FAILED;
    if (count)
        memset(fallback, 0, count);
    for (uint32_t s = 0; s < samples; s++) {
        int known = 0;
        uint32_t best = targets;
        double distance = INFINITY;
        for (uint32_t t = 0; t < targets; t++) {
            uint64_t index = (uint64_t)t * samples + s;
            known |= resolved[index] != 0;
            if (distances[index] < distance) {
                best = t;
                distance = distances[index];
            }
        }
        if (!known && best < targets)
            fallback[(uint64_t)best * samples + s] = 1;
    }
    return OP_OK;
}
