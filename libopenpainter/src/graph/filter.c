#include "openpainter/op_filter.h"
#include <math.h>
#include <string.h>
op_status_t op_filter_plan_build(const char *kind, double radius, int32_t samples,
                                 op_filter_plan_t *out) {
    if (!kind || !out || !isfinite(radius))
        return OP_INVALID_ARGUMENT;
    const char *known[] = {"BLUR",
                           "GAUSSIAN_BLUR",
                           "HIGH_PASS",
                           "SHARPEN",
                           "SMOOTH_MASK",
                           "DILATE",
                           "ERODE",
                           "MORPHOLOGICAL_EXPAND",
                           "MORPHOLOGICAL_CONTRACT",
                           "EDGE_DETECT",
                           "HEIGHT_TO_NORMAL",
                           "DIRECTIONAL_BLUR",
                           "RADIAL_BLUR",
                           "SLOPE_BLUR",
                           "WARP",
                           "DIRECTIONAL_WARP",
                           "VECTOR_WARP",
                           "UV_DISTORTION",
                           "TWIRL",
                           "RIPPLE",
                           "WAVE",
                           "NON_UNIFORM_TRANSFORM",
                           NULL};
    int supported = 0;
    for (unsigned i = 0; known[i]; i++)
        if (!strcmp(kind, known[i]))
            supported = 1;
    if (!supported)
        return OP_UNSUPPORTED;
    memset(out, 0, sizeof(*out));
    out->repeats = 1;
    radius = fmax(radius, 0);
    int gaussian = !strcmp(kind, "GAUSSIAN_BLUR") || !strcmp(kind, "HIGH_PASS") ||
                   !strcmp(kind, "SHARPEN") || !strcmp(kind, "SMOOTH_MASK");
    if (gaussian || !strcmp(kind, "BLUR")) {
        uint32_t half = (uint32_t)fmin(ceil(radius * (gaussian ? 3 : 1)), 192);
        out->weight_count = half * 2 + 1;
        float sum = 0, den = (float)(2 * radius * radius);
        for (uint32_t i = 0; i < out->weight_count; i++) {
            float x = (float)i - half;
            float v = gaussian && radius >= 1e-5 ? expf(-(x * x) / den) : 1;
            out->weights[i] = v;
            sum += v;
        }
        for (uint32_t i = 0; i < out->weight_count; i++)
            out->weights[i] /= sum;
        for (uint32_t axis = 0; axis < 2; axis++)
            out->passes[out->pass_count++] = (op_filter_pass_t){0, axis, half, 0};
    } else if (!strcmp(kind, "DILATE") || !strcmp(kind, "ERODE") ||
               !strcmp(kind, "MORPHOLOGICAL_EXPAND") || !strcmp(kind, "MORPHOLOGICAL_CONTRACT")) {
        uint32_t maximum = !strcmp(kind, "DILATE") || !strcmp(kind, "MORPHOLOGICAL_EXPAND");
        for (uint32_t axis = 0; axis < 2; axis++) {
            uint32_t remaining = (uint32_t)fmin(ceil(radius), 192), covered = 0;
            while (remaining) {
                uint32_t distance = remaining < covered * 2 + 1 ? remaining : covered * 2 + 1;
                out->passes[out->pass_count++] = (op_filter_pass_t){1, axis, distance, maximum};
                remaining -= distance;
                covered += distance;
            }
        }
    }
    out->field = !strcmp(kind, "WARP") || !strcmp(kind, "DIRECTIONAL_WARP") ||
                 !strcmp(kind, "VECTOR_WARP") || !strcmp(kind, "UV_DISTORTION") ||
                 !strcmp(kind, "SLOPE_BLUR");
    if (!strcmp(kind, "SLOPE_BLUR"))
        out->repeats = samples < 1 ? 1 : samples > 32 ? 32 : (uint32_t)samples;
    return OP_OK;
}
