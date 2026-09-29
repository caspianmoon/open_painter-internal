#ifndef OP_REACH_H
#define OP_REACH_H
#include "op_geometry.h"
#ifdef __cplusplus
extern "C" {
#endif
/* Owned evaluated surface snapshot. Inputs are copied; no host pointers retained.
 * Geometry is computed in double then stored in float, matching paint records.
 * Result pairs are ordered by sample, then original triangle within its polygon.
 * All result allocations are released with op_geometry_free. */
typedef struct op_reach op_reach_t;
typedef struct op_reach_pair {
    uint32_t triangle, sample;
} op_reach_pair_t;
op_status_t op_reach_create(const double *corners, const int64_t *polygons, uint32_t triangles,
                            uint32_t mirrored, op_reach_t **out);
void op_reach_destroy(op_reach_t *reach);
uint64_t op_reach_bytes(const op_reach_t *reach);
op_status_t op_reach_copy(const op_reach_t *reach, float *corners, float *normals, float *low,
                          float *high, int64_t *order, int64_t *polygons);
op_status_t op_reach_query(const op_reach_t *reach, const double low[3], const double high[3],
                           uint32_t *indices, uint32_t capacity, uint32_t *written);
op_status_t op_reach_validate(const op_reach_t *reach, const float *anchors, const float *radii,
                              uint32_t samples, op_reach_pair_t **pairs, uint64_t *count,
                              uint8_t *resolved);
op_status_t op_reach_nearest(const op_reach_t *reach, const float *anchors, const float *axes,
                             const float *band, const uint8_t *wanted, uint32_t samples,
                             int64_t *triangles, double *distances);
op_status_t op_reach_seeds(const op_reach_t *reach, const float *anchors, const float *axes,
                           const float *radii, const float *band, const uint8_t *fallback,
                           uint32_t samples, op_reach_pair_t **pairs, uint64_t *count,
                           uint8_t *resolved);
/* Select proximity fallback only when no target validated the anchor; ties
 * choose the first target. Arrays are target-major, each targets x samples. */
op_status_t op_reach_choose(const uint8_t *resolved, const double *distances, uint32_t targets,
                            uint32_t samples, uint8_t *fallback);
#ifdef __cplusplus
}
#endif
#endif
