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
/* A tangent normal of the first three of channels (3 or 4) per pixel: decoded from [0, 1]
 * (x 2 - 1) when decode is 1, normalized (zero, with z = 1, when its length is not above
 * 1e-8), encoded back (x 0.5 + 0.5). A fourth channel is copied. out is values (in place)
 * or a separate count x channels buffer. */
op_status_t op_normal_encode(const float *values, float *out, uint64_t count, uint32_t channels, uint32_t decode);
/* surface_normal.combined with height relief, per texel of an n x n map. The base normal
 * (channels 3 or 4, decoded from [0, 1]) is normalized; dH/dU and dH/dV are centered
 * differences within a chart (charts > 0 and equal), one-sided at chart and image borders,
 * scaled by n. Slopes x = dU m0 d, y = (dU m1 + dV m2) d (metric m: 3 floats per texel,
 * d = distance) tilt it as the preview's Bump does: |z| base - sign(z) (x z, y z, -x bx - y by),
 * normalized (the base where too short), encoded (x 0.5 + 0.5). A fourth channel is copied. */
op_status_t op_normal_relief(const float *normal, const float *height, const float *metric, const int32_t *charts,
                             uint32_t n, uint32_t channels, float distance, float *out);
/* engine.compact_readback.expand_compact: count pixels of components (1 or 2) float32 values
 * into count x 4 floats, the missing channels 0 (green when components is 1, and blue) and 1
 * (alpha); gray 1 (one component only) repeats the value in green and blue instead, the
 * (v, v, v, 1) a scalar channel's image holds. Pure copies: every source float keeps its bits
 * (signed zeros, NaN payloads). values and out must not overlap. */
op_status_t op_expand_compact(const float *values, uint64_t count, uint32_t components, uint32_t gray, float *out);
/* PNG scanlines with adaptive filtering. rows: count rows of row_bytes bytes, stride bytes
 * apart (negative for a flipped view); 16-bit samples (swap16 1) are native-endian and written
 * big-endian. previous 1: the row before the first (at rows - stride) is the filters' upper
 * neighbour; 0: the image's first row (zeros). bpp: bytes per pixel, 1 to 8. out: count x
 * (row_bytes + 1), each row's filter type and then its filtered bytes. A row takes the filter
 * (None, Sub, Up, Average, Paeth) whose bytes have the smallest sum of |signed byte| (libpng's
 * heuristic), the lowest type on ties. */
op_status_t op_png_filter(const void *rows, int64_t stride, uint32_t count, uint32_t row_bytes, uint32_t bpp,
                          uint32_t swap16, uint32_t previous, uint8_t *out);
#ifdef __cplusplus
}
#endif
#endif
