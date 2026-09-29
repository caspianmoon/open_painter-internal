#include "openpainter/op_topology.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#define MAX_FACES 1048576u
#define MAX_LOOPS 4194304u
#define MAX_SIDES 1048576u

typedef struct edge {
    int64_t a, b;
    uint32_t face, first, low, high;
} edge_t;
static int edge_order(const void *left, const void *right) {
    const edge_t *a = left, *b = right;
    if (a->a != b->a)
        return (a->a > b->a) - (a->a < b->a);
    if (a->b != b->b)
        return (a->b > b->b) - (a->b < b->b);
    return (a->first > b->first) - (a->first < b->first);
}
static int continuous(const edge_t *a, const edge_t *b, const float *uv) {
    for (unsigned d = 0; d < 2; d++)
        if (fabsf(uv[2 * a->low + d] - uv[2 * b->low + d]) > 1e-6f ||
            fabsf(uv[2 * a->high + d] - uv[2 * b->high + d]) > 1e-6f)
            return 0;
    return 1;
}
static uint32_t root(uint32_t *parents, uint32_t i) {
    while (parents[i] != i) {
        parents[i] = parents[parents[i]];
        i = parents[i];
    }
    return i;
}
void op_uv_topology_free(op_uv_topology_t *t) {
    if (t) {
        free(t->labels);
        free(t->islands);
        free(t->borders);
        free(t->sides);
        free(t);
    }
}
static void side(op_uv_side_t *out, const edge_t *a, const edge_t *b, const float *uv,
                 const double *centroids, const int64_t *islands, uint32_t size) {
    out->island = islands[a->face];
    for (unsigned d = 0; d < 2; d++) {
        out->vectors[d] = uv[2 * a->low + d] * (float)size;
        out->vectors[2 + d] = uv[2 * a->high + d] * (float)size;
        out->vectors[4 + d] = (float)(centroids[2 * a->face + d] * size);
        out->vectors[6 + d] = uv[2 * b->low + d] * (float)size;
        out->vectors[8 + d] = uv[2 * b->high + d] * (float)size;
        out->vectors[10 + d] = (float)(centroids[2 * b->face + d] * size);
    }
}
op_status_t op_uv_topology_build(const op_uv_face_t *faces, uint32_t face_count,
                                 const int64_t *vertices, const float *uv, uint32_t loop_count,
                                 uint32_t size, op_uv_topology_t **out) {
    if (!out)
        return OP_INVALID_ARGUMENT;
    *out = NULL;
    if (face_count > MAX_FACES || loop_count > MAX_LOOPS || !size || size > 8192 ||
        (face_count && !faces) || (loop_count && (!vertices || !uv)))
        return OP_INVALID_ARGUMENT;
    uint32_t edge_count = 0, previous = 0;
    for (uint32_t i = 0; i < face_count; i++) {
        const op_uv_face_t *f = faces + i;
        if (f->start < previous || f->start > loop_count || f->count > loop_count - f->start ||
            f->flags > 3 || ((f->flags & OP_UV_SEAM) && !(f->flags & OP_UV_ASSIGNED)))
            return OP_VALIDATION_FAILED;
        previous = f->start + f->count;
        if (f->flags & OP_UV_ASSIGNED)
            edge_count += f->count;
    }
    for (uint32_t i = 0; i < loop_count; i++)
        if (vertices[i] < 0 || !isfinite(uv[2 * i]) || !isfinite(uv[2 * i + 1]) ||
            fabsf(uv[2 * i]) > 1e8f || fabsf(uv[2 * i + 1]) > 1e8f)
            return OP_VALIDATION_FAILED;
    op_status_t status = OP_RESOURCE_EXHAUSTED;
    op_uv_topology_t *t = calloc(1, sizeof(*t));
    edge_t *edges = calloc((size_t)edge_count + 1, sizeof(*edges));
    uint32_t *parents = malloc(((size_t)face_count + 1) * sizeof(*parents));
    uint32_t *joins = malloc(((size_t)loop_count + 1) * sizeof(*joins));
    unsigned char *border = calloc((size_t)loop_count + 1, 1);
    int64_t *compact = malloc(((size_t)face_count + 1) * sizeof(*compact));
    double *centroids = calloc((size_t)face_count * 2 + 1, sizeof(*centroids));
    if (!t || !edges || !parents || !joins || !border || !compact || !centroids)
        goto done;
    t->face_count = face_count;
    t->labels = malloc(((size_t)face_count + 1) * sizeof(*t->labels));
    t->islands = malloc(((size_t)face_count + 1) * sizeof(*t->islands));
    if (!t->labels || !t->islands)
        goto done;
    for (uint32_t i = 0; i < loop_count; i++)
        joins[i] = UINT32_MAX;
    uint32_t at = 0;
    for (uint32_t i = 0; i < face_count; i++) {
        const op_uv_face_t *f = faces + i;
        parents[i] = i;
        compact[i] = t->labels[i] = t->islands[i] = -1;
        if (!(f->flags & OP_UV_ASSIGNED))
            continue;
        for (uint32_t j = 0; j < f->count; j++) {
            uint32_t a = f->start + j, b = f->start + (j + 1) % f->count;
            int flip = vertices[a] > vertices[b];
            edges[at++] = (edge_t){
                vertices[flip ? b : a], vertices[flip ? a : b], i, a, flip ? b : a, flip ? a : b};
            border[a] = 1;
            centroids[2 * i] += uv[2 * a];
            centroids[2 * i + 1] += uv[2 * a + 1];
        }
        if (f->count) {
            centroids[2 * i] /= f->count;
            centroids[2 * i + 1] /= f->count;
        }
    }
    qsort(edges, edge_count, sizeof(*edges), edge_order);
    /* Discover joins in edge-key order but apply them in original insertion
     * order: changing that order changes baked island colors. */
    for (uint32_t begin = 0; begin < edge_count;) {
        uint32_t end = begin + 1, candidates = 0;
        const edge_t *a = NULL, *b = NULL;
        while (end < edge_count && edges[end].a == edges[begin].a && edges[end].b == edges[begin].b)
            end++;
        if (end - begin == 2 && continuous(edges + begin, edges + begin + 1, uv)) {
            joins[edges[begin].first] = edges[begin + 1].face;
            border[edges[begin].first] = border[edges[begin + 1].first] = 0;
        }
        for (uint32_t j = begin; j < end; j++)
            if (faces[edges[j].face].flags & OP_UV_SEAM) {
                if (candidates++ == 0)
                    a = edges + j;
                else
                    b = edges + j;
            }
        if (candidates == 2 && !continuous(a, b, uv)) {
            if (t->side_count >= MAX_SIDES) /* two sides are appended per pair */
                goto done;
            t->side_count += 2;
        }
        begin = end;
    }
    for (uint32_t i = 0; i < face_count; i++) {
        const op_uv_face_t *f = faces + i;
        for (uint32_t j = f->start; j < f->start + f->count; j++)
            if (joins[j] != UINT32_MAX)
                parents[root(parents, joins[j])] = root(parents, i);
    }
    for (uint32_t i = 0; i < face_count; i++)
        if (faces[i].flags & OP_UV_ASSIGNED) {
            t->labels[i] = root(parents, i);
            compact[t->labels[i]] = 0;
        }
    for (uint32_t i = 0; i < face_count; i++)
        if (compact[i] >= 0)
            compact[i] = t->island_count++;
    for (uint32_t i = 0; i < face_count; i++)
        if (t->labels[i] >= 0)
            t->islands[i] = compact[t->labels[i]];
    for (uint32_t i = 0; i < loop_count; i++)
        t->border_count += border[i];
    t->borders = calloc((size_t)t->border_count + 1, sizeof(*t->borders));
    t->sides = calloc((size_t)t->side_count + 1, sizeof(*t->sides));
    if (!t->borders || !t->sides)
        goto done;
    at = 0;
    for (uint32_t i = 0; i < face_count; i++) {
        const op_uv_face_t *f = faces + i;
        for (uint32_t j = 0; j < f->count; j++) {
            uint32_t a = f->start + j, b = f->start + (j + 1) % f->count;
            if (!border[a])
                continue;
            if (vertices[a] > vertices[b]) {
                uint32_t swap = a;
                a = b;
                b = swap;
            }
            op_uv_border_t *output = t->borders + at++;
            output->island = t->labels[i];
            memcpy(output->endpoints, uv + 2 * a, 2 * sizeof(float));
            memcpy(output->endpoints + 2, uv + 2 * b, 2 * sizeof(float));
        }
    }
    at = 0;
    for (uint32_t begin = 0; begin < edge_count;) {
        uint32_t end = begin + 1, candidates = 0;
        const edge_t *a = NULL, *b = NULL;
        while (end < edge_count && edges[end].a == edges[begin].a && edges[end].b == edges[begin].b)
            end++;
        for (uint32_t j = begin; j < end; j++)
            if (faces[edges[j].face].flags & OP_UV_SEAM) {
                if (candidates++ == 0)
                    a = edges + j;
                else
                    b = edges + j;
            }
        if (candidates == 2 && !continuous(a, b, uv)) {
            side(t->sides + at++, a, b, uv, centroids, t->islands, size);
            side(t->sides + at++, b, a, uv, centroids, t->islands, size);
        }
        begin = end;
    }
    *out = t;
    t = NULL;
    status = OP_OK;
done:
    op_uv_topology_free(t);
    free(edges);
    free(parents);
    free(joins);
    free(border);
    free(compact);
    free(centroids);
    return status;
}

op_status_t op_uv_island_bounds(const int32_t *islands, uint32_t size, uint32_t island_count,
                                op_uv_bounds_t *bounds) {
    if (!islands || !size || size > 8192 || island_count > MAX_FACES || (island_count && !bounds))
        return OP_INVALID_ARGUMENT;
    for (uint64_t i = 0; i < (uint64_t)size * size; i++)
        if (islands[i] < -1 || (islands[i] >= 0 && (uint32_t)islands[i] >= island_count))
            return OP_VALIDATION_FAILED;
    for (uint32_t i = 0; i < island_count; i++)
        bounds[i] = (op_uv_bounds_t){(int32_t)size, -1, (int32_t)size, -1};
    for (uint32_t row = 0; row < size; row++)
        for (uint32_t column = 0; column < size; column++) {
            int32_t island = islands[(uint64_t)row * size + column];
            if (island < 0)
                continue;
            op_uv_bounds_t *b = bounds + island;
            if ((int32_t)row < b->row_min)
                b->row_min = (int32_t)row;
            b->row_max = (int32_t)row;
            if ((int32_t)column < b->column_min)
                b->column_min = (int32_t)column;
            if ((int32_t)column > b->column_max)
                b->column_max = (int32_t)column;
        }
    return OP_OK;
}
