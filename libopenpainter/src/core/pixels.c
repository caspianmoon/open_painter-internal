#include "openpainter/op_pixels.h"
#include <math.h>
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
static void encode(float *p, uint32_t decode) {
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
    p[0] = x * 0.5f + 0.5f;
    p[1] = y * 0.5f + 0.5f;
    p[2] = z * 0.5f + 0.5f;
}

op_status_t op_normal_encode(float *values, uint64_t count, uint32_t channels, uint32_t decode) {
    if ((count && !values) || count > MAX_PIXELS || (channels != 3 && channels != 4) || decode > 1)
        return OP_INVALID_ARGUMENT;
    uint64_t i = 0;
#ifdef OP_NEON_PIXELS
    if (channels == 4) {
        /* Four pixels a step, deinterleaved: the scalar operations lane by lane. */
        const float32x4_t two = vdupq_n_f32(2.0f), one = vdupq_n_f32(1.0f), half = vdupq_n_f32(0.5f),
                          zero = vdupq_n_f32(0.0f), floor = vdupq_n_f32(1e-8f);
        for (; i + 4 <= count; i += 4) {
            float32x4x4_t p = vld4q_f32(values + i * 4);
            float32x4_t x = p.val[0], y = p.val[1], z = p.val[2];
            if (decode) {
                x = vsubq_f32(vmulq_f32(x, two), one);
                y = vsubq_f32(vmulq_f32(y, two), one);
                z = vsubq_f32(vmulq_f32(z, two), one);
            }
            float32x4_t length = vsqrtq_f32(vaddq_f32(vaddq_f32(vmulq_f32(x, x), vmulq_f32(y, y)), vmulq_f32(z, z)));
            uint32x4_t long_enough = vcgtq_f32(length, floor);
            x = vbslq_f32(long_enough, vdivq_f32(x, length), zero);
            y = vbslq_f32(long_enough, vdivq_f32(y, length), zero);
            z = vbslq_f32(long_enough, vdivq_f32(z, length), one);
            p.val[0] = vaddq_f32(vmulq_f32(x, half), half);
            p.val[1] = vaddq_f32(vmulq_f32(y, half), half);
            p.val[2] = vaddq_f32(vmulq_f32(z, half), half);
            vst4q_f32(values + i * 4, p);
        }
    }
#endif
    for (; i < count; i++)
        encode(values + i * channels, decode);
    return OP_OK;
}
