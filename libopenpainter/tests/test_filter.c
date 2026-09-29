#include "openpainter/op_filter.h"
#include <assert.h>
#include <math.h>
int main(void) {
    op_filter_plan_t p;
    assert(op_filter_plan_build("BLUR", 3.4, 16, &p) == OP_OK && p.weight_count == 9 &&
           p.pass_count == 2);
    for (unsigned i = 0; i < p.weight_count; i++)
        assert(fabsf(p.weights[i] - 1.f / 9) < 1e-7f);
    assert(op_filter_plan_build("GAUSSIAN_BLUR", 2, 16, &p) == OP_OK && p.weight_count == 13);
    float total = 0;
    for (unsigned i = 0; i < p.weight_count; i++) {
        total += p.weights[i];
        assert(p.weights[i] == p.weights[p.weight_count - i - 1]);
    }
    assert(fabsf(total - 1) < 1e-6f);
    assert(op_filter_plan_build("DILATE", 192, 16, &p) == OP_OK);
    unsigned covered[2] = {0};
    for (unsigned i = 0; i < p.pass_count; i++) {
        assert(p.passes[i].kind == 1 && p.passes[i].maximum == 1);
        covered[p.passes[i].axis] += p.passes[i].distance;
    }
    assert(covered[0] == 192 && covered[1] == 192);
    assert(op_filter_plan_build("SLOPE_BLUR", 4, 99, &p) == OP_OK && p.repeats == 32 && p.field);
    assert(op_filter_plan_build("BLUR", NAN, 1, &p) == OP_INVALID_ARGUMENT);
    assert(op_filter_plan_build("UNKNOWN", 1, 1, &p) == OP_UNSUPPORTED);
    return 0;
}
