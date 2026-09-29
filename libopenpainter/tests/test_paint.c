#include "openpainter/op_paint.h"
#include <assert.h>
#include <math.h>
int main(void) {
    float samples[26] = {0};
    samples[5] = samples[18] = 1;
    samples[6] = .5f;
    samples[19] = 1;
    samples[7] = samples[20] = 1;
    op_brush_settings_t b = {.hardness = .8f, .flow = 1, .depth_limit = 1, .rake = 1};
    float values[32], p[9] = {0, 0, 0, 2, 0, 0, 0, 0, 2}, n[9] = {0, 0, 1, 0, 0, 1, 0, 0, 1},
                      out[3];
    assert(op_brush_uniforms(samples, 2, &b, values) == OP_OK && values[7] == .5f &&
           values[23] == 1);
    assert(op_brush_coverage(p, n, 3, samples, 2, &b, NULL, 0, 0, NULL, out) == OP_OK &&
           out[0] == 1 && out[1] == 0 && out[2] == 0);
    samples[19] = .5f;
    b.accumulate = 1;
    assert(op_brush_coverage(p, n, 3, samples, 2, &b, NULL, 0, 0, NULL, out) == OP_OK &&
           out[0] == .75f);
    samples[0] = NAN;
    assert(op_brush_uniforms(samples, 2, &b, values) == OP_INVALID_ARGUMENT);
    uint64_t offsets[] = {0, 1};
    uint32_t invalid[] = {1};
    uint8_t visited = 1;
    assert(op_surface_walk(1, 1, offsets, invalid, 1, n, p, p, n, p, p, &visited) ==
           OP_INVALID_ARGUMENT);
    return 0;
}
