/* Native affine-suffix kernels. Not generated from the Python reference.
 *
 * An affine run of layer units maps its input x to out[c] = A[c] * x[s[c]] + B[c]
 * per texel (op_ir_backdrop_affine). Arming evaluates each unit's own program
 * with constant backdrops 0 and 1: slope = f(1) - f(0), offset = f(0). Composing
 * bottom-up, A'[c] = slope[c] * A[s[c]] and B'[c] = slope[c] * B[s[c]] + offset[c]
 * where s is the unit's component map, packed two bits per component. Inputs
 * may be one-texel constants; fetches clamp into them. */
#define SUFFIX_COMMON                                                                   \
    "vec4 op_suffix_fetch(sampler2D t, ivec2 p) {\n"                                  \
    "  return texelFetch(t, min(p, textureSize(t, 0) - 1), 0);\n"                      \
    "}\n"                                                                              \
    "float op_suffix_pick(vec4 v, int k) {\n"                                          \
    "  return k == 0 ? v.x : (k == 1 ? v.y : (k == 2 ? v.z : v.w));\n"                 \
    "}\n"                                                                              \
    "vec4 op_suffix_swizzle(vec4 v, int s) {\n"                                        \
    "  return vec4(op_suffix_pick(v, s & 3), op_suffix_pick(v, (s >> 2) & 3),\n"       \
    "              op_suffix_pick(v, (s >> 4) & 3), op_suffix_pick(v, (s >> 6) & 3));\n" \
    "}\n"
static const char suffix_accumulate[] =
    SUFFIX_COMMON
    "void main() {\n"
    "  ivec2 p = ivec2(gl_FragCoord.xy);\n"
    "  vec4 offset = op_suffix_fetch(op_f0, p);\n"
    "  vec4 slope = op_suffix_fetch(op_f1, p) - offset;\n"
    "  op_out0 = slope * op_suffix_swizzle(op_suffix_fetch(op_a, p), op_swizzle);\n"
    "  op_out1 = slope * op_suffix_swizzle(op_suffix_fetch(op_b, p), op_swizzle) + offset;\n"
    "}\n";
static const char suffix_apply[] =
    SUFFIX_COMMON
    "void main() {\n"
    "  ivec2 p = ivec2(gl_FragCoord.xy);\n"
    "  vec4 x = op_suffix_swizzle(op_suffix_fetch(op_source, p), op_swizzle);\n"
    "  op_out0 = op_suffix_fetch(op_a, p) * x + op_suffix_fetch(op_b, p);\n"
    "}\n";
#undef SUFFIX_COMMON
static const struct {
    const char *name, *source;
} native_shaders[] = {{"suffix.accumulate", suffix_accumulate},
                      {"suffix.apply", suffix_apply},
                      {NULL, NULL}};
