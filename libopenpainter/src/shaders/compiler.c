#include "openpainter/op_ir.h"
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "catalog.inc"
#include "blend.inc"
#include "effects.inc"
#include "ir_rules.h"
#define MAX_TEXT (64u * 1024u * 1024u)
typedef struct text {
    char *data;
    size_t size, capacity;
    int failed;
} text;
static void append(text *b, const char *s) {
    size_t n = strlen(s), need = b->size + n + 1;
    if (b->failed)
        return;
    if (need > MAX_TEXT) {
        b->failed = 1;
        return;
    }
    if (need > b->capacity) {
        size_t capacity = b->capacity ? b->capacity : 4096;
        while (capacity < need)
            capacity *= 2;
        char *data = realloc(b->data, capacity);
        if (!data) {
            b->failed = 1;
            return;
        }
        b->data = data;
        b->capacity = capacity;
    }
    memcpy(b->data + b->size, s, n + 1);
    b->size += n;
}
static void emit(text *b, const char *fmt, ...) {
    char value[4096];
    va_list args;
    int n;
    va_start(args, fmt);
    n = vsnprintf(value, sizeof(value), fmt, args);
    va_end(args);
    if (n < 0 || (size_t)n >= sizeof(value)) {
        b->failed = 1;
        return;
    }
    append(b, value);
}
static const char *type_name(uint32_t type) {
    return type == 1 ? "float" : type == 2 ? "vec3" : "vec4";
}
static const math_entry *math_find(const math_entry *table, const char *name) {
    for (; table->name; table++)
        if (!strcmp(table->name, name))
            return table;
    return NULL;
}
static int catalog_find(const char *name) {
    for (int i = 0; catalog[i].name; i++)
        if (!strcmp(catalog[i].name, name))
            return i;
    return -1;
}
static void expression(text *out, const char *format, const op_ir_node_t *n) {
    for (const char *c = format; *c;) {
        if (*c == '{' && c[1] && c[2] == '}') {
            const char *slot = strchr("abcs", c[1]);
            if (!slot) {
                out->failed = 1;
                return;
            }
            emit(out, "v%u", n->inputs[slot - "abcs"]);
            c += 3;
        } else {
            char s[2] = {*c++, 0};
            append(out, s);
        }
    }
}
static int inputs_valid(const op_ir_node_t *n, uint32_t index, unsigned count) {
    for (unsigned i = 0; i < count; i++)
        if (n->inputs[i] >= index)
            return 0;
    return 1;
}
static unsigned arity(uint32_t code) { return op_ir_arity(code); }
static int types_valid(const op_ir_node_t *all, const op_ir_node_t *n) {
    uint32_t type = n->type;
    uint32_t a = arity(n->opcode) > 0 ? all[n->inputs[0]].type : 0;
    switch (n->opcode) {
    case OP_IR_CONSTANT:
    case OP_IR_CONVERT:
        return 1;
    case OP_IR_PARAMETER:
        return type == OP_IR_FLOAT && n->aux[0] < 1048576;
    case OP_IR_MATH:
        if (type != OP_IR_FLOAT)
            return 0;
        for (unsigned i = 0; i < 3; i++)
            if (all[n->inputs[i]].type != OP_IR_FLOAT)
                return 0;
        return 1;
    case OP_IR_VECTOR_MATH:
        if (type != OP_IR_FLOAT && type != OP_IR_VECTOR)
            return 0;
        for (unsigned i = 0; i < 3; i++)
            if (all[n->inputs[i]].type != OP_IR_VECTOR)
                return 0;
        return all[n->inputs[3]].type == OP_IR_FLOAT;
    case OP_IR_COMBINE:
        if (type != OP_IR_VECTOR)
            return 0;
        for (unsigned i = 0; i < 3; i++)
            if (all[n->inputs[i]].type != OP_IR_FLOAT)
                return 0;
        return 1;
    case OP_IR_COMPONENT:
        return type == OP_IR_FLOAT && a != OP_IR_FLOAT;
    case OP_IR_IMAGE:
        return type == OP_IR_COLOR && a == OP_IR_VECTOR && n->aux[0] < 256 && n->aux[3] < 3;
    case OP_IR_IMAGE_COLOR:
        return type == OP_IR_COLOR && a == OP_IR_COLOR && n->aux[0] < 4;
    case OP_IR_GEOMETRY:
        return type == OP_IR_VECTOR && n->aux[0] < 256;
    case OP_IR_OBJECT:
        return type == OP_IR_VECTOR && a == OP_IR_VECTOR && n->aux[0] < 65536;
    case OP_IR_BLEND:
        return type == OP_IR_COLOR && a == OP_IR_VECTOR && n->aux[0] <= 1 &&
               all[n->inputs[1]].type == OP_IR_FLOAT && all[n->inputs[2]].type == OP_IR_VECTOR &&
               all[n->inputs[3]].type == OP_IR_FLOAT && all[n->inputs[4]].type == OP_IR_FLOAT;
    case OP_IR_MASK_FOLD:
        if (type != OP_IR_FLOAT || n->aux[0] > 1)
            return 0;
        for (unsigned j = 0; j < 6; j++)
            if (all[n->inputs[j]].type != OP_IR_FLOAT)
                return 0;
        return 1;
    case OP_IR_EFFECT: {
        if (type != OP_IR_VECTOR || a != OP_IR_VECTOR)
            return 0;
        int k = effect_find(n->operation);
        if (k < 0)
            return 1; /* emit reports unsupported */
        const effect_spec_t *spec = effect_specs + k;
        for (unsigned j = 0; j < spec->count; j++)
            if (all[n->inputs[1 + j]].type !=
                (uint32_t)((spec->vectors & (1u << j)) ? OP_IR_VECTOR : OP_IR_FLOAT))
                return 0;
        return all[n->inputs[1 + spec->count]].type == OP_IR_FLOAT &&
               all[n->inputs[2 + spec->count]].type == OP_IR_FLOAT;
    }
    case OP_IR_NORMAL_MAP:
        if (type != OP_IR_VECTOR || a != OP_IR_VECTOR || all[n->inputs[1]].type != OP_IR_FLOAT)
            return 0;
        for (unsigned i = 2; i < 5; i++)
            if (all[n->inputs[i]].type != OP_IR_VECTOR)
                return 0;
        return 1;
    case OP_IR_PROCEDURAL:
        if (type != OP_IR_FLOAT || a != OP_IR_VECTOR)
            return 0;
        for (unsigned i = 1; i < 4; i++)
            if (all[n->inputs[i]].type != OP_IR_FLOAT)
                return 0;
        return 1;
    case OP_IR_FETCH:
        return type == OP_IR_COLOR && n->aux[0] < 256;
    case OP_IR_UV:
        return type == OP_IR_VECTOR;
    default:
        return 1; /* unknown opcode is reported as unsupported */
    }
}
static op_status_t node(text *b, const op_ir_node_t *nodes, uint32_t i) {
    const op_ir_node_t *n = &nodes[i];
    uint32_t a = n->inputs[0], d = n->inputs[1], c = n->inputs[2], s = n->inputs[3];
    if (n->type < 1 || n->type > 3 || !inputs_valid(n, i, arity(n->opcode)) ||
        !memchr(n->operation, 0, sizeof(n->operation)) || !types_valid(nodes, n))
        return OP_VALIDATION_FAILED;
    emit(b, "  %s v%u = ", type_name(n->type), i);
    switch (n->opcode) {
    case OP_IR_CONSTANT:
        for (unsigned j = 0; j < 4; j++)
            if (!isfinite(n->value[j]))
                return OP_VALIDATION_FAILED;
        if (n->type == 1)
            emit(b, "float(%.17g)", n->value[0]);
        else if (n->type == 2)
            emit(b, "vec3(%.17g, %.17g, %.17g)", n->value[0], n->value[1], n->value[2]);
        else
            emit(b, "vec4(%.17g, %.17g, %.17g, %.17g)", n->value[0], n->value[1], n->value[2],
                 n->value[3]);
        break;
    case OP_IR_PARAMETER:
        emit(b, "op_params.p[%u].%c", n->aux[0] / 4, "xyzw"[n->aux[0] % 4]);
        break;
    case OP_IR_CONVERT:
        if (nodes[a].type == n->type)
            emit(b, "v%u", a);
        else if (n->type == 1 && nodes[a].type == 2)
            emit(b, "(v%u.x + v%u.y + v%u.z) * (1.0 / 3.0)", a, a, a);
        else if (n->type == 1)
            emit(b, "dot(v%u.rgb, vec3(0.2126, 0.7152, 0.0722))", a);
        else if (n->type == 2 && nodes[a].type == 3)
            emit(b, "v%u.rgb", a);
        else if (n->type == 2)
            emit(b, "vec3(v%u)", a);
        else if (nodes[a].type == 2)
            emit(b, "vec4(v%u, 1.0)", a);
        else
            emit(b, "vec4(vec3(v%u), 1.0)", a);
        break;
    case OP_IR_MATH:
    case OP_IR_VECTOR_MATH: {
        const math_entry *entry =
            math_find(n->opcode == OP_IR_MATH ? scalar_math : vector_math, n->operation);
        if (!entry)
            return OP_UNSUPPORTED;
        const char *fmt = n->type == 1 ? entry->scalar : entry->vector;
        if (!fmt) {
            append(b, n->type == 1 ? "0.0" : "vec3(0.0)");
            break;
        }
        if (n->opcode == OP_IR_MATH && n->aux[0])
            append(b, "clamp(");
        expression(b, fmt, n);
        if (n->opcode == OP_IR_MATH && n->aux[0])
            append(b, ", 0.0, 1.0)");
        break;
    }
    case OP_IR_COMBINE:
        emit(b, "vec3(v%u, v%u, v%u)", a, d, c);
        break;
    case OP_IR_COMPONENT:
        if (n->aux[0] > 3 || nodes[a].type == 1 || (nodes[a].type == 2 && n->aux[0] > 2))
            return OP_VALIDATION_FAILED;
        emit(b, "v%u.%c", a, "xyzw"[n->aux[0]]);
        break;
    case OP_IR_IMAGE:
        emit(b, "op_sample(op_s%u, v%u.xy, %s, %s)", n->aux[0], a, n->aux[1] ? "true" : "false",
             n->aux[2] ? "true" : "false");
        append(b, ";\n");
        if (n->aux[3] == 1)
            emit(b, "  v%u = vec4(v%u.rrr, v%u.g)", i, i, i);
        else if (n->aux[3] == 2)
            emit(b, "  v%u = vec4(v%u.rrr, 1.0)", i, i);
        else
            return OP_OK;
        break;
    case OP_IR_IMAGE_COLOR:
        if (n->aux[0] == 0)
            emit(b, "v%u", a);
        else if (n->aux[0] == 1 || n->aux[1] || (n->aux[0] == 2 && !n->aux[2]) ||
                 (n->aux[0] == 3 && n->aux[2]))
            emit(b, "vec4(v%u.rgb, 1.0)", a);
        else if (n->aux[0] == 2)
            emit(b, "op_unpremultiply(v%u)", a);
        else
            emit(b, "vec4(v%u.rgb * v%u.a, v%u.a)", a, a, a);
        break;
    case OP_IR_GEOMETRY:
        emit(b, "texelFetch(op_s%u, min(op_texel, textureSize(op_s%u, 0) - 1), 0).xyz", n->aux[0],
             n->aux[0]);
        break;
    case OP_IR_OBJECT:
        emit(b, "(op_params.objects[%u] * vec4(v%u, 1.0)).xyz", n->aux[0], a);
        break;
    case OP_IR_NORMAL_MAP:
        emit(b,
             "op_normalize(mix(v%u, op_normalize((v%u.x * 2.0 - 1.0) * v%u + (v%u.y * 2.0 - 1.0) * "
             "v%u + (v%u.z * 2.0 - 1.0) * v%u), max(v%u, 0.0)))",
             n->inputs[4], a, c, a, s, a, n->inputs[4], d);
        break;
    case OP_IR_PROCEDURAL: {
        int k = catalog_find(n->operation);
        if (k < 0)
            return OP_UNSUPPORTED;
        emit(b, "%s(v%u.xy, v%u, (uint(floor(v%u + 0.5)) * 65536u + uint(floor(v%u + 0.5))))",
             catalog[k].function, a, d, c, s);
        break;
    }
    case OP_IR_FETCH:
        if (n->aux[1])
            emit(b, "texelFetch(op_s%u, min(op_texel, textureSize(op_s%u, 0) - 1), 0)", n->aux[0],
                 n->aux[0]);
        else
            emit(b, "texelFetch(op_s%u, op_texel, 0)", n->aux[0]);
        break;
    case OP_IR_BLEND: {
        unsigned mode = 0;
        while (blend_modes[mode] && strcmp(blend_modes[mode], n->operation))
            mode++;
        if (!blend_modes[mode])
            return OP_UNSUPPORTED;
        emit(b, "op_blend(v%u, v%u, v%u, v%u, v%u, %u, %s)", a, d, c, s, n->inputs[4], mode,
             n->aux[0] ? "true" : "false");
        break;
    }
    case OP_IR_EFFECT: {
        int k = effect_find(n->operation);
        if (k < 0)
            return OP_UNSUPPORTED;
        const effect_spec_t *spec = effect_specs + k;
        emit(b, "op_effect(v%u", a);
        for (unsigned j = 0; j < 5; j++) {
            if (j < spec->count)
                emit(b, ", vec3(v%u)", n->inputs[1 + j]);
            else
                append(b, ", vec3(0.0)");
        }
        emit(b, ", v%u, v%u, %d)", n->inputs[1 + spec->count], n->inputs[2 + spec->count], k);
        break;
    }
    case OP_IR_MASK_FOLD: {
        const char *modes[] = {"NORMAL", "MULTIPLY", "MAX", "MIN", "ADD", NULL};
        unsigned k = 0;
        while (modes[k] && strcmp(modes[k], n->operation))
            k++;
        if (!modes[k])
            return OP_UNSUPPORTED;
        emit(b, "op_mask_fold(v%u, v%u, v%u, v%u, v%u, v%u, %u, %s)", a, d, c, s, n->inputs[4],
             n->inputs[5], k, n->aux[0] ? "true" : "false");
        break;
    }
    case OP_IR_UV:
        append(b, "vec3(op_uv, 0.0)");
        break;
    default:
        return OP_UNSUPPORTED;
    }
    append(b, ";\n");
    return OP_OK;
}
op_status_t op_ir_compile(const op_ir_node_t *nodes, uint32_t count, const op_ir_target_t *targets,
                          uint32_t target_count, char **source) {
    text b = {0};
    uint64_t dependencies = 0;
    int has_blend = 0, has_effect = 0;
    unsigned char kinds[sizeof(catalog) / sizeof(catalog[0])] = {0};
    op_status_t status = OP_OK;
    if (!source)
        return OP_INVALID_ARGUMENT;
    *source = NULL;
    if (!nodes || !count || count > 262144 || !targets || !target_count || target_count > 8)
        return OP_INVALID_ARGUMENT;
    for (uint32_t i = 0; i < count; i++) {
        has_blend |= nodes[i].opcode == OP_IR_BLEND;
        has_effect |= nodes[i].opcode == OP_IR_EFFECT || nodes[i].opcode == OP_IR_MASK_FOLD;
    }
    for (uint32_t i = 0; i < count; i++)
        if (nodes[i].opcode == OP_IR_PROCEDURAL) {
            if (!memchr(nodes[i].operation, 0, 64))
                return OP_VALIDATION_FAILED;
            int k = catalog_find(nodes[i].operation);
            if (k < 0)
                return OP_UNSUPPORTED;
            kinds[k] = 1;
            dependencies |= catalog[k].dependencies;
        }
    for (unsigned i = 0; op_preamble[i]; i++)
        append(&b, op_preamble[i]);
    for (unsigned i = 0; i < sizeof(primitive_source) / sizeof(primitive_source[0]); i++)
        if (dependencies & (UINT64_C(1) << i))
            append(&b, primitive_source[i]);
    for (unsigned i = 0; catalog[i].name; i++)
        if (kinds[i])
            append(&b, catalog[i].source);
    if (has_blend || has_effect)
        for (unsigned i = 0; blend_source[i]; i++)
            append(&b, blend_source[i]);
    if (has_effect)
        for (unsigned i = 0; effect_source[i]; i++)
            append(&b, effect_source[i]);
    append(&b, "void main() {\n  ivec2 op_texel = ivec2(gl_FragCoord.xy);\n  vec2 op_uv = "
               "(vec2(op_texel) + 0.5) * op_inv_size;\n");
    for (uint32_t i = 0; i < count; i++) {
        status = node(&b, nodes, i);
        if (status)
            goto done;
    }
    for (uint32_t i = 0; i < target_count; i++) {
        const op_ir_target_t *t = &targets[i];
        if (t->color >= count || t->alpha >= count || t->layout > 2 ||
            nodes[t->alpha].type != OP_IR_FLOAT ||
            nodes[t->color].type != (uint32_t)(t->layout == 2 ? OP_IR_FLOAT : OP_IR_VECTOR)) {
            status = OP_VALIDATION_FAILED;
            goto done;
        }
        if (t->layout == 2)
            emit(&b, "  op_out%u = vec4(v%u, v%u, v%u, 1.0);\n", i, t->color, t->color, t->color);
        else if (t->layout == 1)
            emit(&b, "  op_out%u = vec4(v%u.r, v%u, 0.0, 0.0);\n", i, t->color, t->alpha);
        else
            emit(&b, "  op_out%u = vec4(v%u, v%u);\n", i, t->color, t->alpha);
    }
    append(&b, "}\n");
    if (b.failed)
        status = OP_RESOURCE_EXHAUSTED;
    else {
        *source = b.data;
        b.data = NULL;
    }
done:
    free(b.data);
    return status;
}
void op_ir_free(void *source) { free(source); }

op_status_t op_ir_field(const char *kind, char **source) {
    if (!source)
        return OP_INVALID_ARGUMENT;
    *source = NULL;
    if (!kind)
        return OP_INVALID_ARGUMENT;
    int k = catalog_find(kind);
    if (k < 0)
        return OP_UNSUPPORTED;
    text b = {0};
    for (unsigned i = 0; i < sizeof(primitive_source) / sizeof(primitive_source[0]); i++)
        if (catalog[k].dependencies & (UINT64_C(1) << i))
            append(&b, primitive_source[i]);
    append(&b, catalog[k].source);
    append(&b,
           "void main() {\n  vec2 uv = (gl_FragCoord.xy + op_params.p[1].xy) / op_params.p[1].z;\n"
           "  uint seed = uint(op_params.p[0].y) * 65536u + uint(op_params.p[0].z);\n");
    emit(&b,
         "  op_out0 = vec4(%s(uv, op_params.p[0].x, seed), %s(uv, op_params.p[0].x, seed + 37u), "
         "0.0, 1.0);\n}",
         catalog[k].function, catalog[k].function);
    if (b.failed) {
        free(b.data);
        return OP_RESOURCE_EXHAUSTED;
    }
    *source = b.data;
    return OP_OK;
}
