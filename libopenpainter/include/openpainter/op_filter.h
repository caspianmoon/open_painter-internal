#ifndef OP_FILTER_H
#define OP_FILTER_H
#include "op_api.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct op_filter_pass {
    uint32_t kind, axis, distance, maximum;
} op_filter_pass_t;
typedef struct op_filter_plan {
    uint32_t weight_count, pass_count, repeats, field;
    float weights[385];
    op_filter_pass_t passes[32];
} op_filter_plan_t;
op_status_t op_filter_plan_build(const char *kind, double radius, int32_t samples,
                                 op_filter_plan_t *out);
#ifdef __cplusplus
}
#endif
#endif
