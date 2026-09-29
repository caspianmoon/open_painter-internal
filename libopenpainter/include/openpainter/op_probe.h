/* Unstable Gate 1 Route C experiment. NOT the production GPU ABI.
 * Native code supplies the bounded two-fill plan and GLSL kernel. Blender
 * executes it using its public GPU API. No external texture interop claimed.
 */
#ifndef OPENPAINTER_OP_PROBE_H
#define OPENPAINTER_OP_PROBE_H
#include "op_api.h"
typedef struct op_probe_plan {
    float base[OP_CHANNEL_COUNT][4];
    float coat[OP_CHANNEL_COUNT][4];
    float opacity;
} op_probe_plan_t;
op_status_t op_probe_build(op_context_t *ctx, op_document_t *doc, op_probe_plan_t *out);
/* Static UTF-8 source, owned by the loaded library for its entire lifetime.
 * uniforms: vec4 op_base, op_coat, op_brush; float op_opacity, op_size.
 * op_brush = UV center, radius in UV, strength. Radius 0 disables the dab.
 * The dab is a diagnostic shader operation, not durable authored paint. */
const char *op_probe_fragment_source(void);
#endif
