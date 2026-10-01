#include "openpainter/op_pixels.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#if defined(__aarch64__) && defined(__ARM_NEON) && !defined(OP_SCALAR_PIXELS)
#include <arm_neon.h>
#define OP_NEON_PIXELS 1   /* build with -DOP_SCALAR_PIXELS to compare */
#endif
#define MAX_PIXELS (UINT64_C(1) << 32)

/* NumPy's image_writer.quantize: np.clip(values, 0, 1), the sRGB curve on rgb, then
 * np.rint(values * maximum).astype(...). A NaN clips to 0 here; NumPy carries it to the
 * integer conversion, which gives 0 on the platforms this library builds for. */
static float srgb(float x) {
    return x <= 0.0031308f ? x * 12.92f : 1.055f * powf(x, (float)(1 / 2.4)) - 0.055f;
}
static float clip(float x) { return fminf(fmaxf(x, 0.0f), 1.0f); }
static void put(void *out, uint32_t bits, uint64_t i, float value, uint32_t curve, float maximum) {
    float x = clip(value);
    if (curve)
        x = srgb(x);
    float q = rintf(x * maximum);
    if (bits == 8)
        ((uint8_t *)out)[i] = (uint8_t)q;
    else
        ((uint16_t *)out)[i] = (uint16_t)q;
}

#ifdef OP_NEON_PIXELS
/* The sRGB curve without powf. Above the knee, x^(1/2.4) is 2^(5e/12) (a table, by the
 * exponent e of x in [-9, 0]) times m^(5/12) for the mantissa m in [1, 2): a degree-7
 * polynomial in m - 1.5. Over every float in (0.0031308, 1] this is within 2.4 x 2^-24 of
 * the exact power; with the table's rounding and the product's, within A = 4 x 2^-24.
 *
 * The code is rint(s) of s = (1.055 p - 0.055) x maximum. With powf faithful (within one
 * ulp, 2^-24 below 1; allow two), |p - p_approx| <= 6 x 2^-24; the subtraction and scaling
 * add at most four more roundings on each side. So |s - s_approx| <= maximum x 10.4 x 2^-24
 * + ulp(maximum): 1.7e-4 at 8 bits and 0.045 at 16. A lane whose s_approx is within GUARD
 * of a rounding tie (k + 0.5) takes powf instead; any other rounds as s would. Below the
 * knee the lanes perform the scalar operations themselves. */
static const float POLY[8] = {1.184053659439087f,     0.3289036452770233f,    -0.06395672261714935f,
                              0.022507308050990105f,  -0.009625503793358803f, 0.004566717427223921f,
                              -0.002744636731222272f, 0.0015328907174989581f};
static const float SCALE[12] = {0.07432544231414795f, 0.09921256452798843f, 0.13243289291858673f,
                                0.1767766922712326f,  0.23596857488155365f, 0.31498026847839355f,
                                0.4204482138156891f,  0.5612310171127319f,  0.7491535544395447f,
                                1.0f,                 0.0f,                 0.0f};
#define GUARD_8 2e-4f
#define GUARD_16 0.05f

typedef struct {
    float32x4_t maximum, guard;
    uint8x16x3_t scale;
} curve_t;

/* Codes of four clipped values on the curve; *exact marks lanes near a tie. */
static inline uint32x4_t curve_codes(const curve_t *k, float32x4_t x, uint32x4_t *exact) {
    uint32x4_t linear = vcleq_f32(x, vdupq_n_f32(0.0031308f));
    float32x4_t low = vmulq_f32(vmulq_f32(x, vdupq_n_f32(12.92f)), k->maximum);
    uint32x4_t bits = vreinterpretq_u32_f32(x);
    float32x4_t m = vreinterpretq_f32_u32(vorrq_u32(vandq_u32(bits, vdupq_n_u32(0x007fffffu)), vdupq_n_u32(0x3f800000u)));
    float32x4_t t = vsubq_f32(m, vdupq_n_f32(1.5f));
    float32x4_t p = vdupq_n_f32(POLY[7]);
    for (int j = 6; j >= 0; j--)
        p = vfmaq_f32(vdupq_n_f32(POLY[j]), p, t);
    /* Byte indices of SCALE[e + 9]: e + 9 is the biased exponent - 118. Out-of-range lanes
     * (below the knee, discarded) read zero bytes. */
    uint32x4_t row = vsubq_u32(vshrq_n_u32(bits, 23), vdupq_n_u32(118));
    uint8x16_t index = vreinterpretq_u8_u32(vaddq_u32(vmulq_n_u32(row, 0x04040404u), vdupq_n_u32(0x03020100u)));
    p = vmulq_f32(p, vreinterpretq_f32_u8(vqtbl3q_u8(k->scale, index)));
    float32x4_t high = vmulq_f32(vsubq_f32(vmulq_f32(p, vdupq_n_f32(1.055f)), vdupq_n_f32(0.055f)), k->maximum);
    float32x4_t s = vbslq_f32(linear, low, high);
    float32x4_t tie = vabdq_f32(vsubq_f32(s, vrndmq_f32(s)), vdupq_n_f32(0.5f));
    *exact = vbicq_u32(vcltq_f32(tie, k->guard), linear);
    return vcvtq_u32_f32(vrndnq_f32(s));
}

static inline float32x4_t clip4(float32x4_t x) {
    return vminnmq_f32(vmaxnmq_f32(x, vdupq_n_f32(0.0f)), vdupq_n_f32(1.0f));
}
static inline uint16x8_t narrow(uint32x4_t a, uint32x4_t b) { return vcombine_u16(vmovn_u32(a), vmovn_u32(b)); }

/* Positions of lanes marked exact, recomputed with powf in one loop when the list fills and
 * at the end. At 16 bits more than half the blocks have one, so the appends take no branches;
 * at 8 bits (a guard band 1/250 as wide) a block without one returns early. */
#define PENDING 256
static uint32_t settle(const float *values, void *out, uint32_t bits, float maximum, const uint64_t *at,
                       uint32_t n) {
    for (uint32_t j = 0; j < n; j++)
        put(out, bits, at[j], values[at[j]], 1, maximum);
    return 0;
}
static inline uint32_t mark(uint64_t *at, uint32_t n, uint32x4_t lo, uint32x4_t hi, uint64_t first,
                            uint64_t stride, uint32_t rare) {
    if (rare && !vmaxvq_u32(vorrq_u32(lo, hi)))
        return n;
    uint32_t lanes[8] = {vgetq_lane_u32(lo, 0), vgetq_lane_u32(lo, 1), vgetq_lane_u32(lo, 2), vgetq_lane_u32(lo, 3),
                         vgetq_lane_u32(hi, 0), vgetq_lane_u32(hi, 1), vgetq_lane_u32(hi, 2), vgetq_lane_u32(hi, 3)};
    for (int j = 0; j < 8; j++) {
        at[n] = first + j * stride;
        n += lanes[j] & 1;
    }
    return n;
}

/* Eight pixels a step: channels 3 or 4 interleaved (curve on rgb, alpha linear), or any
 * channel count entirely on the curve (one or two channels). Returns pixels done. */
static uint64_t quantize_curve(const float *values, uint64_t count, uint32_t channels, uint32_t bits, void *out) {
    const float maximum = bits == 8 ? 255.0f : 65535.0f;
    curve_t k = {vdupq_n_f32(maximum), vdupq_n_f32(bits == 8 ? GUARD_8 : GUARD_16),
                 {{vld1q_u8((const uint8_t *)SCALE), vld1q_u8((const uint8_t *)SCALE + 16),
                   vld1q_u8((const uint8_t *)SCALE + 32)}}};
    uint8_t *bytes = out;
    uint16_t *words = out;
    uint64_t i = 0;
    uint32x4_t ea, eb;
    uint64_t at[PENDING + 24];
    uint32_t n = 0, rare = bits == 8;
    if (channels == 4) {
        for (; i + 8 <= count; i += 8) {
            float32x4x4_t a = vld4q_f32(values + i * 4), b = vld4q_f32(values + i * 4 + 16);
            uint16x8_t q[4];
            uint32x4_t marks[6];
            for (int c = 0; c < 3; c++) {
                uint32x4_t qa = curve_codes(&k, clip4(a.val[c]), &ea), qb = curve_codes(&k, clip4(b.val[c]), &eb);
                q[c] = narrow(qa, qb);
                marks[2 * c] = ea;
                marks[2 * c + 1] = eb;
            }
            q[3] = narrow(vcvtq_u32_f32(vrndnq_f32(vmulq_f32(clip4(a.val[3]), k.maximum))),
                          vcvtq_u32_f32(vrndnq_f32(vmulq_f32(clip4(b.val[3]), k.maximum))));
            if (bits == 8) {
                uint8x8x4_t store = {{vmovn_u16(q[0]), vmovn_u16(q[1]), vmovn_u16(q[2]), vmovn_u16(q[3])}};
                vst4_u8(bytes + i * 4, store);
            } else {
                uint16x8x4_t store = {{q[0], q[1], q[2], q[3]}};
                vst4q_u16(words + i * 4, store);
            }
            for (int c = 0; c < 3; c++)
                n = mark(at, n, marks[2 * c], marks[2 * c + 1], i * 4 + c, 4, rare);
            if (n > PENDING)
                n = settle(values, out, bits, maximum, at, n);
        }
    } else if (channels == 3) {
        for (; i + 8 <= count; i += 8) {
            float32x4x3_t a = vld3q_f32(values + i * 3), b = vld3q_f32(values + i * 3 + 12);
            uint16x8_t q[3];
            uint32x4_t marks[6];
            for (int c = 0; c < 3; c++) {
                uint32x4_t qa = curve_codes(&k, clip4(a.val[c]), &ea), qb = curve_codes(&k, clip4(b.val[c]), &eb);
                q[c] = narrow(qa, qb);
                marks[2 * c] = ea;
                marks[2 * c + 1] = eb;
            }
            if (bits == 8) {
                uint8x8x3_t store = {{vmovn_u16(q[0]), vmovn_u16(q[1]), vmovn_u16(q[2])}};
                vst3_u8(bytes + i * 3, store);
            } else {
                uint16x8x3_t store = {{q[0], q[1], q[2]}};
                vst3q_u16(words + i * 3, store);
            }
            for (int c = 0; c < 3; c++)
                n = mark(at, n, marks[2 * c], marks[2 * c + 1], i * 3 + c, 3, rare);
            if (n > PENDING)
                n = settle(values, out, bits, maximum, at, n);
        }
    } else {
        uint64_t total = count * channels, j = 0;
        for (; j + 8 <= total; j += 8) {
            uint16x8_t q = narrow(curve_codes(&k, clip4(vld1q_f32(values + j)), &ea),
                                  curve_codes(&k, clip4(vld1q_f32(values + j + 4)), &eb));
            if (bits == 8)
                vst1_u8(bytes + j, vmovn_u16(q));
            else
                vst1q_u16(words + j, q);
            n = mark(at, n, ea, eb, j, 1, rare);
            if (n > PENDING)
                n = settle(values, out, bits, maximum, at, n);
        }
        i = j / channels;   /* whole pixels; the caller finishes from there */
    }
    settle(values, out, bits, maximum, at, n);
    return i;
}
#endif

op_status_t op_quantize(const float *values, uint64_t count, uint32_t channels, uint32_t bits,
                        uint32_t srgb_rgb, void *out) {
    if ((count && (!values || !out)) || count > MAX_PIXELS || !channels || channels > 16 ||
        (bits != 8 && bits != 16) || srgb_rgb > 1)
        return OP_INVALID_ARGUMENT;
    const float maximum = bits == 8 ? 255.0f : 65535.0f;
    uint32_t curved = srgb_rgb ? (channels < 3 ? channels : 3) : 0;
    uint64_t total = count * channels, i = 0;
#ifdef OP_NEON_PIXELS
    if (curved && channels <= 4)
        i = quantize_curve(values, count, channels, bits, out) * channels;
    if (!curved) {
        /* No curve: four values a step (clip, scale, round half to even, convert). */
        uint8_t *bytes = out;
        uint16_t *words = out;
        const float32x4_t zero = vdupq_n_f32(0.0f), one = vdupq_n_f32(1.0f), scale = vdupq_n_f32(maximum);
        for (; i + 8 <= total; i += 8) {
            float32x4_t a = vminnmq_f32(vmaxnmq_f32(vld1q_f32(values + i), zero), one);
            float32x4_t b = vminnmq_f32(vmaxnmq_f32(vld1q_f32(values + i + 4), zero), one);
            uint32x4_t qa = vcvtq_u32_f32(vrndnq_f32(vmulq_f32(a, scale)));
            uint32x4_t qb = vcvtq_u32_f32(vrndnq_f32(vmulq_f32(b, scale)));
            uint16x8_t q = vcombine_u16(vmovn_u32(qa), vmovn_u32(qb));
            if (bits == 8)
                vst1_u8(bytes + i, vmovn_u16(q));
            else
                vst1q_u16(words + i, q);
        }
    }
#endif
    for (; i < total; i++)
        put(out, bits, i, values[i], (uint32_t)(i % channels) < curved, maximum);
    return OP_OK;
}

/* NumPy's padding._encode (decode 0) and surface_normal.combined without relief (decode 1):
 * length = np.linalg.norm(xyz) as sqrt((x*x + y*y) + z*z), xyz / length where length > 1e-8
 * (else 0, z 1), then xyz * .5 + .5. */
static void encode(const float *p, float *q, uint32_t channels, uint32_t decode) {
    float x = p[0], y = p[1], z = p[2];
    if (decode) {
        x = x * 2.0f - 1.0f;
        y = y * 2.0f - 1.0f;
        z = z * 2.0f - 1.0f;
    }
    float length = sqrtf((x * x + y * y) + z * z);
    if (length > 1e-8f) {
        x /= length;
        y /= length;
        z /= length;
    } else {
        x = y = 0.0f;
        z = 1.0f;
    }
    if (channels == 4)
        q[3] = p[3];
    q[0] = x * 0.5f + 0.5f;
    q[1] = y * 0.5f + 0.5f;
    q[2] = z * 0.5f + 0.5f;
}

op_status_t op_normal_encode(const float *values, float *out, uint64_t count, uint32_t channels, uint32_t decode) {
    if ((count && (!values || !out)) || count > MAX_PIXELS || (channels != 3 && channels != 4) || decode > 1)
        return OP_INVALID_ARGUMENT;
    uint64_t i = 0;
#ifdef OP_NEON_PIXELS
    /* Four pixels a step, split into channels: the scalar operations lane by lane. */
    const float32x4_t two = vdupq_n_f32(2.0f), one = vdupq_n_f32(1.0f), half = vdupq_n_f32(0.5f),
                      zero = vdupq_n_f32(0.0f), floor = vdupq_n_f32(1e-8f);
#define NORMALIZE(p)                                                                                            \
    do {                                                                                                        \
        float32x4_t x = p.val[0], y = p.val[1], z = p.val[2];                                                   \
        if (decode) {                                                                                           \
            x = vsubq_f32(vmulq_f32(x, two), one);                                                              \
            y = vsubq_f32(vmulq_f32(y, two), one);                                                              \
            z = vsubq_f32(vmulq_f32(z, two), one);                                                              \
        }                                                                                                       \
        float32x4_t length = vsqrtq_f32(vaddq_f32(vaddq_f32(vmulq_f32(x, x), vmulq_f32(y, y)), vmulq_f32(z, z))); \
        uint32x4_t long_enough = vcgtq_f32(length, floor);                                                      \
        x = vbslq_f32(long_enough, vdivq_f32(x, length), zero);                                                 \
        y = vbslq_f32(long_enough, vdivq_f32(y, length), zero);                                                 \
        z = vbslq_f32(long_enough, vdivq_f32(z, length), one);                                                  \
        p.val[0] = vaddq_f32(vmulq_f32(x, half), half);                                                         \
        p.val[1] = vaddq_f32(vmulq_f32(y, half), half);                                                         \
        p.val[2] = vaddq_f32(vmulq_f32(z, half), half);                                                         \
    } while (0)
    if (channels == 4)
        for (; i + 4 <= count; i += 4) {
            float32x4x4_t p = vld4q_f32(values + i * 4);
            NORMALIZE(p);
            vst4q_f32(out + i * 4, p);
        }
    else
        for (; i + 4 <= count; i += 4) {
            float32x4x3_t p = vld3q_f32(values + i * 3);
            NORMALIZE(p);
            vst3q_f32(out + i * 3, p);
        }
#undef NORMALIZE
#endif
    for (; i < count; i++)
        encode(values + i * channels, out + i * channels, channels, decode);
    return OP_OK;
}

/* surface_normal._derivative at one texel: step 1 along a row (dU), n down the rows (dV). */
static float derivative(const float *height, const int32_t *charts, uint64_t at, uint64_t step, int has_before,
                        int has_after, float size) {
    int32_t chart = charts[at];
    float value = height[at];
    int after = has_after && chart > 0 && charts[at + step] == chart;
    int before = has_before && chart > 0 && charts[at - step] == chart;
    float difference = after ? height[at + step] - value : 0.0f;
    difference = difference + (before ? value - height[at - step] : 0.0f);
    float count = (float)after + (float)before;
    return count > 0 ? difference * size / count : 0.0f;
}

static float sign(float x) { return x > 0 ? 1.0f : x < 0 ? -1.0f : x == 0 ? 0.0f : x; }

static void relief(const float *normal, const float *height, const float *metric, const int32_t *charts, uint32_t n,
                   uint32_t channels, float distance, uint32_t r, uint32_t c, float *out) {
    const float size = (float)n;
    uint64_t at = (uint64_t)r * n + c;
    float du = derivative(height, charts, at, 1, c > 0, c + 1 < n, size);
    float dv = derivative(height, charts, at, n, r > 0, r + 1 < n, size);
    const float *m = metric + at * 3;
    float sx = du * m[0] * distance;
    float sy = (du * m[1] + dv * m[2]) * distance;
    const float *p = normal + at * channels;
    float *q = out + at * channels;
    float bx = p[0] * 2.0f - 1.0f, by = p[1] * 2.0f - 1.0f, bz = p[2] * 2.0f - 1.0f;
    float length = sqrtf((bx * bx + by * by) + bz * bz);
    if (length > 1e-8f) {
        bx /= length;
        by /= length;
        bz /= length;
    } else {
        bx = by = 0.0f;
        bz = 1.0f;
    }
    float a = fabsf(bz), s = sign(bz);
    float px = a * bx - s * (sx * bz), py = a * by - s * (sy * bz), pz = a * bz - s * (-sx * bx - sy * by);
    float tilted = sqrtf((px * px + py * py) + pz * pz);
    if (tilted > 1e-8f) {
        px /= tilted;
        py /= tilted;
        pz /= tilted;
    } else {
        px = bx;
        py = by;
        pz = bz;
    }
    if (channels == 4)
        q[3] = p[3];
    q[0] = px * 0.5f + 0.5f;
    q[1] = py * 0.5f + 0.5f;
    q[2] = pz * 0.5f + 0.5f;
}

#ifdef OP_NEON_PIXELS
/* derivative() for four texels: the neighbours at -step and +step, each used where its
 * chart matches (has_before/has_after clear a whole side at the image border). */
static inline float32x4_t derivative4(const float *height, const int32_t *charts, uint64_t at, uint64_t step,
                                      int has_before, int has_after, float32x4_t size) {
    const float32x4_t zero = vdupq_n_f32(0.0f), one = vdupq_n_f32(1.0f);
    float32x4_t value = vld1q_f32(height + at);
    int32x4_t chart = vld1q_s32(charts + at);
    uint32x4_t inside = vcgtq_s32(chart, vdupq_n_s32(0));
    uint32x4_t after = vdupq_n_u32(0), before = vdupq_n_u32(0);
    float32x4_t ahead = value, behind = value;
    if (has_after) {
        ahead = vld1q_f32(height + at + step);
        after = vandq_u32(inside, vceqq_s32(vld1q_s32(charts + at + step), chart));
    }
    if (has_before) {
        behind = vld1q_f32(height + at - step);
        before = vandq_u32(inside, vceqq_s32(vld1q_s32(charts + at - step), chart));
    }
    float32x4_t difference = vbslq_f32(after, vsubq_f32(ahead, value), zero);
    difference = vaddq_f32(difference, vbslq_f32(before, vsubq_f32(value, behind), zero));
    float32x4_t count = vaddq_f32(vbslq_f32(after, one, zero), vbslq_f32(before, one, zero));
    return vbslq_f32(vcgtq_f32(count, zero), vdivq_f32(vmulq_f32(difference, size), count), zero);
}

/* relief() for texels c..c+3 of row r, all with both row neighbours (0 < c, c + 3 < n - 1). */
static inline void relief4(const float *normal, const float *height, const float *metric, const int32_t *charts,
                           uint32_t n, uint32_t channels, float distance, uint32_t r, uint32_t c, float *out) {
    const float32x4_t zero = vdupq_n_f32(0.0f), one = vdupq_n_f32(1.0f), two = vdupq_n_f32(2.0f),
                      half = vdupq_n_f32(0.5f), floor = vdupq_n_f32(1e-8f), d = vdupq_n_f32(distance),
                      size = vdupq_n_f32((float)n);
    uint64_t at = (uint64_t)r * n + c;
    float32x4_t du = derivative4(height, charts, at, 1, 1, 1, size);
    float32x4_t dv = derivative4(height, charts, at, n, r > 0, r + 1 < n, size);
    float32x4x3_t m = vld3q_f32(metric + at * 3);
    float32x4_t sx = vmulq_f32(vmulq_f32(du, m.val[0]), d);
    float32x4_t sy = vmulq_f32(vaddq_f32(vmulq_f32(du, m.val[1]), vmulq_f32(dv, m.val[2])), d);
    float32x4x4_t p;
    if (channels == 4)
        p = vld4q_f32(normal + at * 4);
    else {
        float32x4x3_t three = vld3q_f32(normal + at * 3);
        p.val[0] = three.val[0], p.val[1] = three.val[1], p.val[2] = three.val[2], p.val[3] = zero;
    }
    float32x4_t bx = vsubq_f32(vmulq_f32(p.val[0], two), one);
    float32x4_t by = vsubq_f32(vmulq_f32(p.val[1], two), one);
    float32x4_t bz = vsubq_f32(vmulq_f32(p.val[2], two), one);
    float32x4_t length = vsqrtq_f32(vaddq_f32(vaddq_f32(vmulq_f32(bx, bx), vmulq_f32(by, by)), vmulq_f32(bz, bz)));
    uint32x4_t long_enough = vcgtq_f32(length, floor);
    bx = vbslq_f32(long_enough, vdivq_f32(bx, length), zero);
    by = vbslq_f32(long_enough, vdivq_f32(by, length), zero);
    bz = vbslq_f32(long_enough, vdivq_f32(bz, length), one);
    float32x4_t a = vabsq_f32(bz);
    float32x4_t s = vbslq_f32(vcgtq_f32(bz, zero), one,
                              vbslq_f32(vcltq_f32(bz, zero), vnegq_f32(one), vbslq_f32(vceqq_f32(bz, zero), zero, bz)));
    float32x4_t px = vsubq_f32(vmulq_f32(a, bx), vmulq_f32(s, vmulq_f32(sx, bz)));
    float32x4_t py = vsubq_f32(vmulq_f32(a, by), vmulq_f32(s, vmulq_f32(sy, bz)));
    float32x4_t pz = vsubq_f32(vmulq_f32(a, bz),
                               vmulq_f32(s, vsubq_f32(vmulq_f32(vnegq_f32(sx), bx), vmulq_f32(sy, by))));
    float32x4_t tilted = vsqrtq_f32(vaddq_f32(vaddq_f32(vmulq_f32(px, px), vmulq_f32(py, py)), vmulq_f32(pz, pz)));
    uint32x4_t straight = vcgtq_f32(tilted, floor);
    px = vbslq_f32(straight, vdivq_f32(px, tilted), bx);
    py = vbslq_f32(straight, vdivq_f32(py, tilted), by);
    pz = vbslq_f32(straight, vdivq_f32(pz, tilted), bz);
    p.val[0] = vaddq_f32(vmulq_f32(px, half), half);
    p.val[1] = vaddq_f32(vmulq_f32(py, half), half);
    p.val[2] = vaddq_f32(vmulq_f32(pz, half), half);
    if (channels == 4)
        vst4q_f32(out + at * 4, p);
    else {
        float32x4x3_t three = {{p.val[0], p.val[1], p.val[2]}};
        vst3q_f32(out + at * 3, three);
    }
}
#endif

op_status_t op_normal_relief(const float *normal, const float *height, const float *metric, const int32_t *charts,
                             uint32_t n, uint32_t channels, float distance, float *out) {
    uint64_t texels = (uint64_t)n * n;
    if ((texels && (!normal || !height || !metric || !charts || !out)) || texels > MAX_PIXELS ||
        (channels != 3 && channels != 4))
        return OP_INVALID_ARGUMENT;
    for (uint32_t r = 0; r < n; r++) {
        uint32_t c = 0;
#ifdef OP_NEON_PIXELS
        relief(normal, height, metric, charts, n, channels, distance, r, 0, out);
        for (c = 1; c + 4 < n; c += 4)   /* the last column keeps its scalar border */
            relief4(normal, height, metric, charts, n, channels, distance, r, c, out);
#endif
        for (; c < n; c++)
            relief(normal, height, metric, charts, n, channels, distance, r, c, out);
    }
    return OP_OK;
}

/* ---- PNG filters ---------------------------------------------------------------------------- */

static uint8_t paeth(uint8_t a, uint8_t b, uint8_t c) {
    int p = a + b - c, pa = abs(p - a), pb = abs(p - b), pc = abs(p - c);
    return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
}
static uint8_t filtered(uint32_t type, uint8_t x, uint8_t a, uint8_t b, uint8_t c) {
    switch (type) {
    case 1: return (uint8_t)(x - a);
    case 2: return (uint8_t)(x - b);
    case 3: return (uint8_t)(x - ((a + b) >> 1));
    case 4: return (uint8_t)(x - paeth(a, b, c));
    default: return x;
    }
}
static uint32_t magnitude(uint8_t f) { return f < 128 ? f : 256u - f; }

#ifdef OP_NEON_PIXELS
static inline uint8x16_t paeth16(uint8x16_t a, uint8x16_t b, uint8x16_t c) {
    uint8x16_t pa = vabdq_u8(b, c), pb = vabdq_u8(a, c);   /* |p - a| = |b - c|, |p - b| = |a - c| */
    int16x8_t low = vabsq_s16(vaddq_s16(vreinterpretq_s16_u16(vsubl_u8(vget_low_u8(a), vget_low_u8(c))),
                                        vreinterpretq_s16_u16(vsubl_u8(vget_low_u8(b), vget_low_u8(c)))));
    int16x8_t high = vabsq_s16(vaddq_s16(vreinterpretq_s16_u16(vsubl_u8(vget_high_u8(a), vget_high_u8(c))),
                                         vreinterpretq_s16_u16(vsubl_u8(vget_high_u8(b), vget_high_u8(c)))));
    /* |p - c| = |a + b - 2c| saturates at 255: above that it exceeds pa and pb anyway. */
    uint8x16_t pc = vcombine_u8(vqmovun_s16(low), vqmovun_s16(high));
    uint8x16_t use_a = vandq_u8(vcleq_u8(pa, pb), vcleq_u8(pa, pc));
    return vbslq_u8(use_a, a, vbslq_u8(vcleq_u8(pb, pc), b, c));
}
static inline uint8x16_t filtered16(uint32_t type, uint8x16_t x, uint8x16_t a, uint8x16_t b, uint8x16_t c) {
    switch (type) {
    case 1: return vsubq_u8(x, a);
    case 2: return vsubq_u8(x, b);
    case 3: return vsubq_u8(x, vhaddq_u8(a, b));
    case 4: return vsubq_u8(x, paeth16(a, b, c));
    default: return x;
    }
}
static inline uint8x16_t magnitude16(uint8x16_t f) { return vminq_u8(f, vsubq_u8(vdupq_n_u8(0), f)); }
#endif

/* The five filters' sums of |signed byte| over one row (x the row, b the one above). */
static void filter_sums(const uint8_t *x, const uint8_t *b, uint32_t n, uint32_t bpp, uint64_t sums[5]) {
    uint32_t i = 0;
    for (int k = 0; k < 5; k++)
        sums[k] = 0;
    for (; i < bpp && i < n; i++)
        for (uint32_t k = 0; k < 5; k++)
            sums[k] += magnitude(filtered(k, x[i], 0, b[i], 0));
#ifdef OP_NEON_PIXELS
    uint32x4_t total[5];
    for (int k = 0; k < 5; k++)
        total[k] = vdupq_n_u32(0);
    while (i + 16 <= n) {
        /* 128 steps at most per 16-bit partial sum (2 x 128 x 128 < 65536). */
        uint16x8_t partial[5];
        for (int k = 0; k < 5; k++)
            partial[k] = vdupq_n_u16(0);
        for (uint32_t steps = 0; steps < 128 && i + 16 <= n; steps++, i += 16) {
            uint8x16_t vx = vld1q_u8(x + i), va = vld1q_u8(x + i - bpp);
            uint8x16_t vb = vld1q_u8(b + i), vc = vld1q_u8(b + i - bpp);
            partial[0] = vpadalq_u8(partial[0], magnitude16(vx));
            partial[1] = vpadalq_u8(partial[1], magnitude16(vsubq_u8(vx, va)));
            partial[2] = vpadalq_u8(partial[2], magnitude16(vsubq_u8(vx, vb)));
            partial[3] = vpadalq_u8(partial[3], magnitude16(vsubq_u8(vx, vhaddq_u8(va, vb))));
            partial[4] = vpadalq_u8(partial[4], magnitude16(vsubq_u8(vx, paeth16(va, vb, vc))));
        }
        for (int k = 0; k < 5; k++)
            total[k] = vpadalq_u16(total[k], partial[k]);
    }
    for (int k = 0; k < 5; k++)
        sums[k] += vaddvq_u32(total[k]);
#endif
    for (; i < n; i++)
        for (uint32_t k = 0; k < 5; k++)
            sums[k] += magnitude(filtered(k, x[i], x[i - bpp], b[i], b[i - bpp]));
}

static void filter_row(uint32_t type, const uint8_t *x, const uint8_t *b, uint32_t n, uint32_t bpp, uint8_t *out) {
    uint32_t i = 0;
    for (; i < bpp && i < n; i++)
        out[i] = filtered(type, x[i], 0, b[i], 0);
#ifdef OP_NEON_PIXELS
    for (; i + 16 <= n; i += 16)
        vst1q_u8(out + i, filtered16(type, vld1q_u8(x + i), vld1q_u8(x + i - bpp), vld1q_u8(b + i), vld1q_u8(b + i - bpp)));
#endif
    for (; i < n; i++)
        out[i] = filtered(type, x[i], x[i - bpp], b[i], b[i - bpp]);
}

static void swap_row(const uint8_t *row, uint32_t n, uint8_t *out) {
    uint32_t i = 0;
#ifdef OP_NEON_PIXELS
    for (; i + 16 <= n; i += 16)
        vst1q_u8(out + i, vrev16q_u8(vld1q_u8(row + i)));
#endif
    for (; i + 1 < n; i += 2) {
        out[i] = row[i + 1];
        out[i + 1] = row[i];
    }
}

op_status_t op_png_filter(const void *rows, int64_t stride, uint32_t count, uint32_t row_bytes, uint32_t bpp,
                          uint32_t swap16, uint32_t previous, uint8_t *out) {
    if ((count && (!rows || !out)) || !bpp || bpp > 8 || swap16 > 1 || previous > 1 || (swap16 && row_bytes % 2) ||
        (uint64_t)count * (row_bytes + 1ull) > (UINT64_C(1) << 36))
        return OP_INVALID_ARGUMENT;
    if (!count || !row_bytes) {
        for (uint32_t r = 0; r < count; r++)
            out[(uint64_t)r * (row_bytes + 1)] = 0;
        return OP_OK;
    }
    const uint8_t *base = rows;
    uint8_t *scratch = calloc(swap16 ? 3 : 1, row_bytes);   /* zeros above the first row; swapped rows */
    if (!scratch)
        return OP_RESOURCE_EXHAUSTED;
    uint8_t *above = swap16 ? scratch + row_bytes : NULL, *current = swap16 ? scratch + 2 * (uint64_t)row_bytes : NULL;
    const uint8_t *b = scratch;
    if (previous) {
        b = base - stride;
        if (swap16) {
            swap_row(b, row_bytes, above);
            b = above;
        }
    }
    for (uint32_t r = 0; r < count; r++) {
        const uint8_t *x = base + (int64_t)r * stride;
        if (swap16) {
            swap_row(x, row_bytes, current);
            x = current;
        }
        uint64_t sums[5];
        filter_sums(x, b, row_bytes, bpp, sums);
        uint32_t best = 0;
        for (uint32_t k = 1; k < 5; k++)
            if (sums[k] < sums[best])
                best = k;
        uint8_t *line = out + (uint64_t)r * (row_bytes + 1);
        line[0] = (uint8_t)best;
        filter_row(best, x, b, row_bytes, bpp, line + 1);
        if (swap16) {   /* the swapped row is the next row's upper neighbour */
            uint8_t *t = above;
            above = current;
            current = t;
            b = above;
        } else
            b = x;
    }
    free(scratch);
    return OP_OK;
}
