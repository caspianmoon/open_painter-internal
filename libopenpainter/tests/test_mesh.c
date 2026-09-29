#include "openpainter/op_geometry.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
int main(void) {
    int64_t mesh[] = {0, 1, 2, 1, 0, 3, 0, 1, 4, 10, 11, 12}, *ptr, *indices;
    uint64_t count;
    assert(op_mesh_adjacency(mesh, 4, &ptr, &indices, &count) == OP_OK);
    assert(count == 6 && ptr[0] == 0 && ptr[1] == 2 && ptr[3] == 6 && ptr[4] == 6);
    assert(indices[0] == 1 && indices[1] == 2 && indices[2] == 0);
    op_geometry_free(ptr);
    op_geometry_free(indices);
    mesh[0] = -1;
    assert(op_mesh_adjacency(mesh, 4, &ptr, &indices, &count) == OP_VALIDATION_FAILED);
    assert(!ptr && !indices && !count);
    double corners[] = {0, 0, 0, 2, 0, 0, 0, 3, 0}, uv[] = {0, 0, 1, 0, 0, 1}, frame[6];
    assert(op_mesh_frames(corners, uv, 1, frame) == OP_OK);
    assert(frame[0] == 1 && frame[4] == 1 && frame[1] == 0 && frame[3] == 0);
    uv[0] = NAN;
    assert(op_mesh_frames(corners, uv, 1, frame) == OP_VALIDATION_FAILED);
    float weights[] = {.5f, .25f, .25f}, attributes[] = {1, 2, 3, 4, 5, 6}, output[2];
    assert(op_mesh_interpolate(weights, attributes, 1, 2, output) == OP_OK);
    assert(output[0] == 2.5f && output[1] == 3.5f);
    uv[0] = 0;
    float jacobian[6];
    assert(op_mesh_jacobians(corners, uv, 1, 12, jacobian) == OP_OK);
    assert(jacobian[0] == 6 && jacobian[4] == 4);
    int32_t triangles[] = {0, -1};
    float axis[] = {0, 1, 0}, directions[4];
    assert(op_mesh_directions(triangles, 2, jacobian, 1, axis, directions) == OP_OK);
    assert(directions[0] == 0 && directions[1] == 1 && directions[2] == 0 && directions[3] == 0);
    triangles[0] = 1;
    assert(op_mesh_directions(triangles, 2, jacobian, 1, axis, directions) == OP_VALIDATION_FAILED);
    uint8_t covered[9] = {1, 0, 1, 0, 0, 0, 0, 0, 0};
    int32_t islands[9] = {2, -1, 1, -1, -1, -1, -1, -1, -1}, donors[9];
    int16_t distance[9];
    assert(op_padding_donors(covered, islands, 3, 2, donors, distance) == OP_OK);
    assert(donors[0] == -1 && distance[0] == 0 && donors[1] == 2 && distance[1] == 1);
    assert(donors[4] == 2 && distance[4] == 2 && donors[7] == -1);
    islands[0] = -1;
    assert(op_padding_donors(covered, islands, 3, 2, donors, distance) == OP_VALIDATION_FAILED);
    puts("PASS test_mesh");
    return 0;
}
