#ifndef OP_TOPOLOGY_H
#define OP_TOPOLOGY_H
#include "op_api.h"
#ifdef __cplusplus
extern "C" {
#endif
/* Polygon loops are disjoint, ordered ranges. Vertex identifiers are nonnegative
 * int64 values, compared as pairs (never multiplied into overflow-prone keys).
 * ASSIGNED selects UV islands/borders; SEAM selects the subset with triangles.
 * Input UVs are finite float32 pairs in the target's local tile coordinates. */
enum { OP_UV_ASSIGNED = 1, OP_UV_SEAM = 2 };
typedef struct op_uv_face {
    uint32_t start, count, flags;
} op_uv_face_t;
typedef struct op_uv_border {
    int64_t island; /* original union-find representative, for stable baked IDs */
    float endpoints[4];
} op_uv_border_t;
typedef struct op_uv_side {
    int64_t island;    /* compact, ascending representative order */
    float vectors[12]; /* source endpoints/centroid, opposite endpoints/centroid */
} op_uv_side_t;
typedef struct op_uv_topology {
    uint32_t face_count, island_count, border_count, side_count;
    int64_t *labels, *islands; /* -1 for unassigned faces */
    op_uv_border_t *borders;   /* polygon/loop insertion order */
    op_uv_side_t *sides;       /* vertex-pair order, then first side followed by opposite */
} op_uv_topology_t;
/* Both outputs and all scratch are bounded: 1M faces, 4M loops, 1M seam sides.
 * Inputs are borrowed for this call only. Output is independently owned and is
 * never partial on failure. UV continuity uses a float32 tolerance of 1e-6. */
op_status_t op_uv_topology_build(const op_uv_face_t *faces, uint32_t face_count,
                                 const int64_t *vertices, const float *uv, uint32_t loop_count,
                                 uint32_t size, op_uv_topology_t **out);
void op_uv_topology_free(op_uv_topology_t *topology);
typedef struct op_uv_bounds {
    int32_t row_min, row_max, column_min, column_max;
} op_uv_bounds_t;
/* Bounds of compact island IDs in an owned raster; -1 texels are uncovered.
 * Caller supplies island_count output records. Empty islands have row_max=-1.
 * Validate the entire input before writing output; at most 1M islands. */
op_status_t op_uv_island_bounds(const int32_t *islands, uint32_t size, uint32_t island_count,
                                op_uv_bounds_t *bounds);
#ifdef __cplusplus
}
#endif
#endif
