#ifndef OP_PAINT_H
#define OP_PAINT_H
#include "op_api.h"
#ifdef __cplusplus
extern "C" {
#endif
/* All arrays are contiguous. Samples have 13 float32 components. Calls retain
 * no input pointers. Counts and products are bounded before allocation. */
typedef struct op_brush_settings {
    float hardness, flow, angle, depth_limit;
    uint32_t accumulate, rake, planar;
    float plane[3];
} op_brush_settings_t;
op_status_t op_brush_uniforms(const float *samples, uint32_t count,
                              const op_brush_settings_t *settings, float *vec4s);
op_status_t op_brush_coverage(const float *positions, const float *normals, uint32_t texels,
                              const float *samples, uint32_t count,
                              const op_brush_settings_t *settings, const float *alpha,
                              uint32_t width, uint32_t height, const uint8_t *eligible,
                              float *result);
op_status_t op_brush_resample(const float *points, uint32_t count, float spacing, uint32_t *kept,
                              uint32_t *written);
op_status_t op_brush_bounds(const float *samples, uint32_t count, float margin, float low[3],
                            float high[3]);
op_status_t op_surface_walk(uint32_t triangles, uint32_t samples, const uint64_t *offsets,
                            const uint32_t *neighbors, uint64_t edges, const float *normals,
                            const float *low, const float *high, const float *axes,
                            const float *box_low, const float *box_high, uint8_t *visited);
op_status_t op_triangle_distances(const double *points, const double *corners, uint32_t count,
                                  double *distances);
op_status_t op_uv_window(const double *corners, const double *uv, uint32_t count,
                         const double low[3], const double high[3], double margin, float *windows);
#ifdef __cplusplus
}
#endif
#endif
