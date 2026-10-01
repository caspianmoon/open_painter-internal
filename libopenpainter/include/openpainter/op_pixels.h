#ifndef OP_PIXELS_H
#define OP_PIXELS_H
#include "op_api.h"
#ifdef __cplusplus
extern "C" {
#endif
/* Export pixel kernels. Each performs the float32 arithmetic of the NumPy code it replaces,
 * operation for operation and in the same order, so its results are identical. */

/* values: count pixels x channels float32. Clipped to [0, 1]; the first min(3, channels)
 * channels take the sRGB transfer when srgb is 1; then round half to even of value x
 * (2^bits - 1). out: count x channels uint8 (bits 8) or uint16 (bits 16). */
op_status_t op_quantize(const float *values, uint64_t count, uint32_t channels, uint32_t bits,
                        uint32_t srgb, void *out);
/* A tangent normal of the first three of channels (3 or 4) per pixel, in place: decoded
 * from [0, 1] (x 2 - 1) when decode is 1, normalized (zero, with z = 1, when its length
 * is not above 1e-8), encoded back (x 0.5 + 0.5). A fourth channel is left as it is. */
op_status_t op_normal_encode(float *values, uint64_t count, uint32_t channels, uint32_t decode);
#ifdef __cplusplus
}
#endif
#endif
