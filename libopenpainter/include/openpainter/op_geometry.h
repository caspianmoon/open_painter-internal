#ifndef OP_GEOMETRY_H
#define OP_GEOMETRY_H
#include "op_api.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct op_bounds op_bounds_t;
op_status_t op_bounds_create(const double *minimum, const double *maximum, uint32_t count,
                             op_bounds_t **out);
void op_bounds_destroy(op_bounds_t *bounds);
op_status_t op_bounds_query(const op_bounds_t *bounds, const double low[3], const double high[3],
                            uint32_t *indices, uint32_t capacity, uint32_t *written);
uint64_t op_bounds_bytes(const op_bounds_t *bounds);
typedef struct op_uv_sample {
    uint32_t row, column, triangle;
    float weights[3];
} op_uv_sample_t;
/* uv: triangles x 3 x 2; optional clip: triangles x 2 x 2.
 * Unique output is in texel order and keeps the last triangle's sample;
 * nonunique output is in triangle order.
 * The result is native-owned until op_geometry_free; no pointers are retained. */
op_status_t op_uv_rasterize(const float *uv, uint32_t triangles, uint32_t size, uint32_t unique,
                            const float *clip, op_uv_sample_t **samples, uint64_t *count);
/* The unique raster of op_uv_rasterize with corner attributes interpolated straight into maps,
 * as rasterizing, interpolating the kept samples' corners and scattering them would: for each
 * covered texel (the later triangle wins) and attribute a, outs[a][texel] = w0 c0 + w1 c1 + w2 c2
 * per component, summed left to right. corners[a]: triangles x 3 x components[a] values of
 * bytes[a] 4 (float32, float32 arithmetic, as op_mesh_interpolate) or 8 (float64: NumPy's float64
 * arithmetic, rounded to float32 once). outs[a]: size x size x components[a] float32, texels by
 * rows; texels no triangle covers keep their values. covered (optional): size x size bytes, set
 * to 1 where a triangle covers. 1 to 8 attributes of 1 to 16 components. */
op_status_t op_uv_raster_interpolate(const float *uv, uint32_t triangles, uint32_t size, uint32_t attributes,
                                     const void *const *corners, const uint32_t *components,
                                     const uint32_t *bytes, float *const *outs, uint8_t *covered);
void op_geometry_free(void *memory);
/* Exact Euclidean gutter donors, tie-broken by island then texel index.
 * Square arrays, size <= 8192, limit <= 64; covered texels have donor -1.
 * Scratch storage is bounded to a 16 MiB row band. */
op_status_t op_padding_donors(const uint8_t *covered, const int32_t *islands, uint32_t size,
                              uint32_t limit, int32_t *donors, int16_t *distance2);
/* Sorted CSR edge adjacency. Non-manifold edges connect all distinct triangles.
 * Outputs are owned until op_geometry_free; the input snapshot is not retained. */
op_status_t op_mesh_adjacency(const int64_t *vertices, uint32_t triangles, int64_t **offsets,
                              int64_t **indices, uint64_t *count);
op_status_t op_mesh_frames(const double *corners, const double *uv, uint32_t triangles,
                           double *frames);
op_status_t op_mesh_jacobians(const double *corners, const double *uv, uint32_t triangles,
                              uint32_t size, float *jacobians);
op_status_t op_mesh_directions(const int32_t *triangle, uint32_t texels, const float *jacobians,
                               uint32_t triangles, const float world[3], float *directions);
op_status_t op_mesh_interpolate(const float *weights, const float *corners, uint32_t samples,
                                uint32_t components, float *values);
#ifdef __cplusplus
}
#endif
#endif
