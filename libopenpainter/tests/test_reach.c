#include "openpainter/op_reach.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
int main(void) {
    double corners[] = {0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, .02, 1, 0, .02, 0, 1, .02};
    int64_t polygons[] = {7, 8};
    op_reach_t *r = NULL;
    assert(op_reach_create(corners, polygons, 2, 0, &r) == OP_OK);
    assert(op_reach_bytes(r) > 0);
    corners[0] = 123;
    polygons[0] = 123; /* The native snapshot is independent. */
    float anchors[] = {.2f, .2f, 0, 7, .2f, .2f, .02f, 7, .2f, .2f, 9, -1};
    float radii[] = {.1f, .1f, .1f}, band[] = {.1f, .1f, .1f}, axes[] = {0, 0, 1, 0, 0, 1, 0, 0, 1};
    uint8_t resolved[3], wanted[] = {1, 1, 1};
    op_reach_pair_t *pairs = NULL;
    uint64_t count = 0;
    assert(op_reach_validate(r, anchors, radii, 3, &pairs, &count, resolved) == OP_OK);
    assert(count == 1 && pairs[0].triangle == 0 && pairs[0].sample == 0 && resolved[0] &&
           !resolved[1]);
    op_geometry_free(pairs);
    int64_t triangles[3];
    double distances[3];
    assert(op_reach_nearest(r, anchors, axes, band, wanted, 3, triangles, distances) == OP_OK);
    assert(triangles[0] == 0 && triangles[1] == 1 && triangles[2] == -1 && isinf(distances[2]));
    assert(op_reach_seeds(r, anchors, axes, radii, band, wanted, 3, &pairs, &count, resolved) ==
           OP_OK);
    assert(count == 2 && pairs[1].triangle == 1 && pairs[1].sample == 1 && !resolved[2]);
    op_geometry_free(pairs);
    anchors[0] = NAN;
    assert(op_reach_validate(r, anchors, radii, 3, &pairs, &count, resolved) ==
           OP_VALIDATION_FAILED);
    assert(!pairs && !count);
    uint8_t valid[] = {1, 0, 0, 0, 0, 0}, selected[6];
    double nearest[] = {0, .1, INFINITY, 0, .1, INFINITY};
    assert(op_reach_choose(valid, nearest, 2, 3, selected) == OP_OK);
    assert(!selected[0] && selected[1] && !selected[2] && !selected[3] && !selected[4] &&
           !selected[5]);
    op_reach_destroy(r);
    assert(op_reach_create(NULL, NULL, 0, 0, &r) == OP_OK);
    assert(op_reach_validate(r, NULL, NULL, 0, &pairs, &count, NULL) == OP_OK && !pairs && !count);
    op_reach_destroy(r);
    puts("PASS test_reach");
    return 0;
}
