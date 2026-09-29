#include "openpainter/op_topology.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    op_uv_face_t faces[] = {{0, 3, 3}, {3, 3, 3}, {6, 3, 3}};
    int64_t vertices[] = {0, 1, 2, 1, 0, 3, 0, 1, 4};
    float uv[] = {0, 0, 1, 0, 0, 1, 1, 0, 0, 0, 1, -1, 0, 0, 1, 0, .5f, 1};
    op_uv_topology_t *t = NULL;
    assert(op_uv_topology_build(faces, 2, vertices, uv, 6, 128, &t) == OP_OK);
    assert(t->face_count == 2 && t->island_count == 1 && t->border_count == 4 && !t->side_count);
    assert(t->labels[0] == 0 && t->labels[1] == 0);
    op_uv_topology_free(t);
    uv[7] = uv[9] = .5f;
    assert(op_uv_topology_build(faces, 2, vertices, uv, 6, 128, &t) == OP_OK);
    assert(t->island_count == 2 && t->border_count == 6 && t->side_count == 2);
    assert(t->sides[0].island == 0 && t->sides[1].island == 1);
    assert(t->sides[0].vectors[7] == 64 && t->sides[1].vectors[1] == 64);
    float old = t->sides[1].vectors[1];
    uv[9] = .75f;
    assert(t->sides[1].vectors[1] == old); /* owned, never borrowed */
    uv[9] = .5f;
    op_uv_topology_free(t);
    /* Nonmanifold keys never join. Only the triangulated subset pairs seams. */
    assert(op_uv_topology_build(faces, 3, vertices, uv, 9, 128, &t) == OP_OK);
    assert(t->island_count == 3 && t->border_count == 9 && !t->side_count);
    op_uv_topology_free(t);
    faces[2].flags = OP_UV_ASSIGNED;
    assert(op_uv_topology_build(faces, 3, vertices, uv, 9, 128, &t) == OP_OK);
    assert(t->island_count == 3 && t->border_count == 9 && t->side_count == 2);
    op_uv_topology_free(t);
    faces[0].flags = 0;
    assert(op_uv_topology_build(faces, 3, vertices, uv, 9, 128, &t) == OP_OK);
    assert(t->labels[0] == -1 && t->islands[0] == -1 && t->island_count == 2 && !t->side_count);
    op_uv_topology_free(t);
    faces[0].flags = 3;
    for (unsigned i = 0; i < 9; i++)
        vertices[i] += INT64_MAX - 8;
    assert(op_uv_topology_build(faces, 3, vertices, uv, 9, 8192, &t) == OP_OK);
    assert(t->island_count == 3 && t->side_count == 2);
    op_uv_topology_free(t);
    assert(op_uv_topology_build(NULL, 0, NULL, NULL, 0, 1, &t) == OP_OK);
    assert(!t->face_count && !t->island_count && !t->border_count && !t->side_count);
    op_uv_topology_free(t);
    assert(op_uv_topology_build(NULL, 1048577, NULL, NULL, 0, 1, &t) == OP_INVALID_ARGUMENT && !t);
    assert(op_uv_topology_build(NULL, 0, NULL, NULL, 4194305, 1, &t) == OP_INVALID_ARGUMENT && !t);
    assert(op_uv_topology_build(faces, 3, vertices, uv, 9, 0, &t) == OP_INVALID_ARGUMENT && !t);
    faces[1].start = 2;
    assert(op_uv_topology_build(faces, 3, vertices, uv, 9, 128, &t) == OP_VALIDATION_FAILED && !t);
    faces[1].start = 3;
    faces[1].count = UINT32_MAX;
    assert(op_uv_topology_build(faces, 3, vertices, uv, 9, 128, &t) == OP_VALIDATION_FAILED && !t);
    faces[1].count = 3;
    for (unsigned flags = 2; flags <= 4; flags += 2) {
        faces[1].flags = flags;
        assert(op_uv_topology_build(faces, 3, vertices, uv, 9, 128, &t) == OP_VALIDATION_FAILED &&
               !t);
    }
    faces[1].flags = 3;
    uv[0] = NAN;
    assert(op_uv_topology_build(faces, 3, vertices, uv, 9, 128, &t) == OP_VALIDATION_FAILED && !t);
    uv[0] = 0;
    vertices[0] = -1;
    assert(op_uv_topology_build(faces, 3, vertices, uv, 9, 128, &t) == OP_VALIDATION_FAILED && !t);
    op_uv_topology_free(NULL);
    int32_t raster[] = {-1, 0, 2, -1, 2, 0, 0, -1, 2};
    op_uv_bounds_t bounds[3];
    assert(op_uv_island_bounds(raster, 3, 3, bounds) == OP_OK);
    assert(bounds[0].row_min == 0 && bounds[0].row_max == 2 && bounds[0].column_min == 0 &&
           bounds[0].column_max == 2);
    assert(bounds[1].row_max == -1);
    assert(bounds[2].row_min == 0 && bounds[2].row_max == 2 && bounds[2].column_min == 1);
    op_uv_bounds_t before[3];
    memcpy(before, bounds, sizeof(bounds));
    raster[8] = 3;
    assert(op_uv_island_bounds(raster, 3, 3, bounds) == OP_VALIDATION_FAILED);
    assert(!memcmp(before, bounds, sizeof(bounds)));
    assert(op_uv_island_bounds(raster, 0, 3, bounds) == OP_INVALID_ARGUMENT);
    int32_t uncovered = -1;
    assert(op_uv_island_bounds(&uncovered, 1, 0, NULL) == OP_OK);
    puts("PASS test_topology");
    return 0;
}
