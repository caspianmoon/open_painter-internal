#include "../core/internal.h"
#include "openpainter/op_probe.h"
#include <string.h>
op_status_t op_probe_build(op_context_t *ctx, op_document_t *doc, op_probe_plan_t *out) {
    if (!ctx || !doc || !out)
        return OP_INVALID_ARGUMENT;
    if (!op_document_valid(ctx, doc))
        return OP_STALE_HANDLE;
    if (doc->open_tx || doc->layer_count != 2)
        return OP_UNSUPPORTED;
    for (size_t i = 0; i < 2; ++i) {
        const op_layer_t *layer = &doc->committed[i];
        if (layer->blend != OP_BLEND_NORMAL || layer->enabled_mask != OP_ALL_CHANNELS_MASK)
            return OP_UNSUPPORTED;
        for (size_t c = 0; c < OP_CHANNEL_COUNT; ++c)
            if (layer->values[c][3] != 1.0f)
                return OP_UNSUPPORTED;
    }
    if (doc->committed[0].opacity != 1.0f)
        return OP_UNSUPPORTED;
    memcpy(out->base, doc->committed[0].values, sizeof(out->base));
    memcpy(out->coat, doc->committed[1].values, sizeof(out->coat));
    out->opacity = doc->committed[1].opacity;
    return OP_OK;
}
const char *op_probe_fragment_source(void) {
    return "void main() {\n"
           " vec2 uv = gl_FragCoord.xy / op_size;\n"
           " float coverage = 0.25 + 0.75 * uv.x;\n"
           " vec4 value = mix(op_base, op_coat, op_opacity * coverage);\n"
           " float dab = op_brush.z > 0.0 ? (1.0 - smoothstep(0.0, op_brush.z, distance(uv, "
           "op_brush.xy))) * op_brush.w : 0.0;\n"
           " op_out0 = vec4(mix(value.rgb, op_coat.rgb, dab), 1.0);\n"
           "}\n";
}
