#ifndef OP_SEAM_H
#define OP_SEAM_H
#include "op_geometry.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct op_seam_sample {
    uint32_t row, column;
    int64_t island;
    float x, y, depth, normal[2];
    uint32_t nearest; /* global closest seam; depth ties choose the lower island */
} op_seam_sample_t;
/* Each side has six 2D float vectors: source endpoints/centroid then opposite
 * endpoints/centroid. Returns island-major texel order, closest seam per island
 * with ties to the earlier side. Covered texels are never changed. */
op_status_t op_seam_bands(const uint8_t *covered, uint32_t size, float halo, const float *sides,
                          const int64_t *islands, uint32_t count, op_seam_sample_t **samples,
                          uint64_t *written);
#ifdef __cplusplus
}
#endif
#endif
