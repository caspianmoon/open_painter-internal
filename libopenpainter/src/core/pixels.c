#include "openpainter/op_pixels.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "simd.h"
#if defined(OP_SIMD_NEON) && !defined(OP_SCALAR_PIXELS)
#define OP_NEON_PIXELS 1   /* build with -DOP_SCALAR_PIXELS to compare */
#elif defined(OP_SIMD_SSE) && !defined(OP_SCALAR_PIXELS)
#define OP_SSE_PIXELS 1
#endif
#define MAX_PIXELS (UINT64_C(1) << 32)
#define STREAM_PIXELS (UINT64_C(1) << 16)   /* op_expand_compact: 1 MiB of RGBA */

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

#if defined(OP_NEON_PIXELS) || defined(OP_SSE_PIXELS)
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
#endif

#ifdef OP_NEON_PIXELS
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

#ifdef OP_SSE_PIXELS
#define ROUND_EVEN (_MM_FROUND_TO_NEAREST_INT | _MM_FROUND_NO_EXC)
typedef struct {
    __m128 maximum, guard;
} curve_t;

/* curve_codes on SSE4.1: the same operations, the polynomial without fused multiply-add (the
 * exhaustive test holds it to the same guard band). maxps returns its second operand for a
 * NaN, as fmaxf(NaN, 0) gives 0. */
static inline __m128 clip4(__m128 x) { return _mm_min_ps(_mm_max_ps(x, _mm_setzero_ps()), _mm_set1_ps(1.0f)); }

static inline __m128i curve_codes(const curve_t *k, __m128 x, __m128 *exact) {
    __m128 linear = _mm_cmple_ps(x, _mm_set1_ps(0.0031308f));
    __m128 low = _mm_mul_ps(_mm_mul_ps(x, _mm_set1_ps(12.92f)), k->maximum);
    __m128i bits = _mm_castps_si128(x);
    __m128 m = _mm_castsi128_ps(_mm_or_si128(_mm_and_si128(bits, _mm_set1_epi32(0x007fffff)),
                                             _mm_set1_epi32(0x3f800000)));
    __m128 t = _mm_sub_ps(m, _mm_set1_ps(1.5f));
    __m128 p = _mm_set1_ps(POLY[7]);
    for (int j = 6; j >= 0; j--)
        p = _mm_add_ps(_mm_mul_ps(p, t), _mm_set1_ps(POLY[j]));
    /* SCALE[e + 9] per lane; lanes below the knee (discarded) read SCALE[11] = 0. */
    __m128i row = _mm_min_epu32(_mm_sub_epi32(_mm_srli_epi32(bits, 23), _mm_set1_epi32(118)), _mm_set1_epi32(11));
    p = _mm_mul_ps(p, _mm_setr_ps(SCALE[_mm_cvtsi128_si32(row)], SCALE[_mm_extract_epi32(row, 1)],
                                  SCALE[_mm_extract_epi32(row, 2)], SCALE[_mm_extract_epi32(row, 3)]));
    __m128 high = _mm_mul_ps(_mm_sub_ps(_mm_mul_ps(p, _mm_set1_ps(1.055f)), _mm_set1_ps(0.055f)), k->maximum);
    __m128 s = _mm_blendv_ps(high, low, linear);
    __m128 tie = _mm_andnot_ps(_mm_set1_ps(-0.0f), _mm_sub_ps(_mm_sub_ps(s, _mm_floor_ps(s)), _mm_set1_ps(0.5f)));
    *exact = _mm_andnot_ps(linear, _mm_cmplt_ps(tie, k->guard));
    return _mm_cvtps_epi32(_mm_round_ps(s, ROUND_EVEN));
}

static inline uint32_t mark(uint64_t *at, uint32_t n, __m128 exact, uint64_t first, uint64_t stride, uint32_t rare) {
    int lanes = _mm_movemask_ps(exact);
    if (rare && !lanes)
        return n;
    for (int j = 0; j < 4; j++) {
        at[n] = first + j * stride;
        n += (uint32_t)(lanes >> j) & 1;
    }
    return n;
}

/* Four pixels a step: RGBA (curve on rgb, alpha linear), or one to three channels all on the
 * curve. Returns pixels done; a pixel the flat loop only began is redone by the caller. */
static uint64_t quantize_curve(const float *values, uint64_t count, uint32_t channels, uint32_t bits, void *out) {
    const float maximum = bits == 8 ? 255.0f : 65535.0f;
    curve_t k = {_mm_set1_ps(maximum), _mm_set1_ps(bits == 8 ? GUARD_8 : GUARD_16)};
    uint8_t *bytes = out;
    uint16_t *words = out;
    uint64_t i = 0, at[PENDING + 16];
    uint32_t n = 0, rare = bits == 8;
    __m128 e;
    if (channels == 4) {
        for (; i + 4 <= count; i += 4) {
            const float *v = values + i * 4;
            __m128 r = _mm_loadu_ps(v), g = _mm_loadu_ps(v + 4), b = _mm_loadu_ps(v + 8), a = _mm_loadu_ps(v + 12);
            _MM_TRANSPOSE4_PS(r, g, b, a);
            __m128 q0 = _mm_castsi128_ps(curve_codes(&k, clip4(r), &e));
            n = mark(at, n, e, i * 4, 4, rare);
            __m128 q1 = _mm_castsi128_ps(curve_codes(&k, clip4(g), &e));
            n = mark(at, n, e, i * 4 + 1, 4, rare);
            __m128 q2 = _mm_castsi128_ps(curve_codes(&k, clip4(b), &e));
            n = mark(at, n, e, i * 4 + 2, 4, rare);
            __m128 q3 = _mm_castsi128_ps(_mm_cvtps_epi32(_mm_round_ps(_mm_mul_ps(clip4(a), k.maximum), ROUND_EVEN)));
            _MM_TRANSPOSE4_PS(q0, q1, q2, q3);   /* back to pixels: shuffles move the codes' bits */
            __m128i first = _mm_packus_epi32(_mm_castps_si128(q0), _mm_castps_si128(q1));
            __m128i second = _mm_packus_epi32(_mm_castps_si128(q2), _mm_castps_si128(q3));
            if (bits == 8)
                _mm_storeu_si128((__m128i *)(bytes + i * 4), _mm_packus_epi16(first, second));
            else {
                _mm_storeu_si128((__m128i *)(words + i * 4), first);
                _mm_storeu_si128((__m128i *)(words + i * 4 + 8), second);
            }
            if (n > PENDING)
                n = settle(values, out, bits, maximum, at, n);
        }
    } else {
        uint64_t total = count * channels, j = 0;
        for (; j + 4 <= total; j += 4) {
            __m128i q = curve_codes(&k, clip4(_mm_loadu_ps(values + j)), &e);
            __m128i w = _mm_packus_epi32(q, q);
            if (bits == 8) {
                int32_t packed = _mm_cvtsi128_si32(_mm_packus_epi16(w, w));
                memcpy(bytes + j, &packed, 4);
            } else
                _mm_storel_epi64((__m128i *)(words + j), w);
            n = mark(at, n, e, j, 1, rare);
            if (n > PENDING)
                n = settle(values, out, bits, maximum, at, n);
        }
        i = j / channels;
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
#elif defined(OP_SSE_PIXELS)
    if (curved && channels <= 4)
        i = quantize_curve(values, count, channels, bits, out) * channels;
    if (!curved) {
        uint8_t *bytes = out;
        uint16_t *words = out;
        const __m128 scale = _mm_set1_ps(maximum);
        for (; i + 8 <= total; i += 8) {
            __m128i qa = _mm_cvtps_epi32(_mm_round_ps(_mm_mul_ps(clip4(_mm_loadu_ps(values + i)), scale), ROUND_EVEN));
            __m128i qb = _mm_cvtps_epi32(_mm_round_ps(_mm_mul_ps(clip4(_mm_loadu_ps(values + i + 4)), scale), ROUND_EVEN));
            __m128i q = _mm_packus_epi32(qa, qb);
            if (bits == 8)
                _mm_storel_epi64((__m128i *)(bytes + i), _mm_packus_epi16(q, q));
            else
                _mm_storeu_si128((__m128i *)(words + i), q);
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
#elif defined(OP_SSE_PIXELS)
    /* RGBA, four pixels a step, split into channels by a transpose (only shuffles). */
    const __m128 two = _mm_set1_ps(2.0f), one = _mm_set1_ps(1.0f), half = _mm_set1_ps(0.5f),
                 zero = _mm_setzero_ps(), floor = _mm_set1_ps(1e-8f);
    if (channels == 4)
        for (; i + 4 <= count; i += 4) {
            const float *v = values + i * 4;
            __m128 x = _mm_loadu_ps(v), y = _mm_loadu_ps(v + 4), z = _mm_loadu_ps(v + 8), w = _mm_loadu_ps(v + 12);
            _MM_TRANSPOSE4_PS(x, y, z, w);
            if (decode) {
                x = _mm_sub_ps(_mm_mul_ps(x, two), one);
                y = _mm_sub_ps(_mm_mul_ps(y, two), one);
                z = _mm_sub_ps(_mm_mul_ps(z, two), one);
            }
            __m128 length = _mm_sqrt_ps(_mm_add_ps(_mm_add_ps(_mm_mul_ps(x, x), _mm_mul_ps(y, y)), _mm_mul_ps(z, z)));
            __m128 long_enough = _mm_cmpgt_ps(length, floor);
            x = _mm_blendv_ps(zero, _mm_div_ps(x, length), long_enough);
            y = _mm_blendv_ps(zero, _mm_div_ps(y, length), long_enough);
            z = _mm_blendv_ps(one, _mm_div_ps(z, length), long_enough);
            x = _mm_add_ps(_mm_mul_ps(x, half), half);
            y = _mm_add_ps(_mm_mul_ps(y, half), half);
            z = _mm_add_ps(_mm_mul_ps(z, half), half);
            _MM_TRANSPOSE4_PS(x, y, z, w);
            float *o = out + i * 4;
            _mm_storeu_ps(o, x);
            _mm_storeu_ps(o + 4, y);
            _mm_storeu_ps(o + 8, z);
            _mm_storeu_ps(o + 12, w);
        }
#endif
    for (; i < count; i++)
        encode(values + i * channels, out + i * channels, channels, decode);
    return OP_OK;
}

/* compact_readback.expand_compact: R or RG pixels to RGBA with (0, 0, 1) filled in, or R to
 * (r, r, r, 1) when gray. Only loads, shuffles and stores, never arithmetic, so each float's bits
 * pass through unchanged. */
op_status_t op_expand_compact(const float *values, uint64_t count, uint32_t components, uint32_t gray, float *out) {
    if ((count && (!values || !out)) || count > MAX_PIXELS || (components != 1 && components != 2) || gray > 1 ||
        (gray && components != 1))
        return OP_INVALID_ARGUMENT;
    if (count) {
        uintptr_t source = (uintptr_t)values, target = (uintptr_t)out;
        if (source < target + count * 16 && target < source + count * components * 4)
            return OP_INVALID_ARGUMENT;
    }
    uint64_t i = 0;
#ifdef OP_NEON_PIXELS
    /* vst4 interleaves four registers, one per channel, into four RGBA pixels. */
    const float32x4_t zero = vdupq_n_f32(0.0f), one = vdupq_n_f32(1.0f);
    if (gray)
        for (; i + 4 <= count; i += 4) {
            float32x4_t r = vld1q_f32(values + i);
            float32x4x4_t p = {{r, r, r, one}};
            vst4q_f32(out + i * 4, p);
        }
    else if (components == 1)
        for (; i + 4 <= count; i += 4) {
            float32x4x4_t p = {{vld1q_f32(values + i), zero, zero, one}};
            vst4q_f32(out + i * 4, p);
        }
    else
        for (; i + 4 <= count; i += 4) {
            float32x4x2_t rg = vld2q_f32(values + i * 2);
            float32x4x4_t p = {{rg.val[0], rg.val[1], zero, one}};
            vst4q_f32(out + i * 4, p);
        }
#elif defined(OP_SSE_PIXELS)
    /* ba holds (0, 1) twice: movelh/movehl pair each pixel's R or RG with it. Gray broadcasts a
     * lane and blends alpha in from one. */
    const __m128 zero = _mm_setzero_ps(), ba = _mm_set_ps(1.0f, 0.0f, 1.0f, 0.0f), one = _mm_set1_ps(1.0f);
#define EXPAND(STORE)                                                                                           \
    do {                                                                                                        \
        if (gray)                                                                                               \
            for (; i + 4 <= count; i += 4) {                                                                    \
                __m128 r = _mm_loadu_ps(values + i);                                                            \
                float *o = out + i * 4;                                                                         \
                STORE(o, _mm_blend_ps(_mm_shuffle_ps(r, r, 0x00), one, 8)); /* r0 r0 r0 1 */                    \
                STORE(o + 4, _mm_blend_ps(_mm_shuffle_ps(r, r, 0x55), one, 8));                                 \
                STORE(o + 8, _mm_blend_ps(_mm_shuffle_ps(r, r, 0xaa), one, 8));                                 \
                STORE(o + 12, _mm_blend_ps(_mm_shuffle_ps(r, r, 0xff), one, 8));                                \
            }                                                                                                   \
        else if (components == 1)                                                                               \
            for (; i + 4 <= count; i += 4) {                                                                    \
                __m128 r = _mm_loadu_ps(values + i);                                                            \
                __m128 lo = _mm_unpacklo_ps(r, zero), hi = _mm_unpackhi_ps(r, zero); /* r0 0 r1 0, r2 0 r3 0 */ \
                float *o = out + i * 4;                                                                         \
                STORE(o, _mm_movelh_ps(lo, ba));     /* r0 0 0 1 */                                             \
                STORE(o + 4, _mm_movehl_ps(ba, lo)); /* r1 0 0 1 */                                             \
                STORE(o + 8, _mm_movelh_ps(hi, ba));                                                            \
                STORE(o + 12, _mm_movehl_ps(ba, hi));                                                           \
            }                                                                                                   \
        else                                                                                                    \
            for (; i + 2 <= count; i += 2) {                                                                    \
                __m128 rg = _mm_loadu_ps(values + i * 2); /* r0 g0 r1 g1 */                                     \
                float *o = out + i * 4;                                                                         \
                STORE(o, _mm_movelh_ps(rg, ba));     /* r0 g0 0 1 */                                            \
                STORE(o + 4, _mm_movehl_ps(ba, rg)); /* r1 g1 0 1 */                                            \
            }                                                                                                   \
    } while (0)
    /* A result of a megabyte or more outgrows the caches its reader would find it in. Non-temporal
     * stores skip reading each destination line before writing it: half the time at 1K, and the
     * reader that follows (Image.pixels.foreach_set) is no slower. */
    if (count >= STREAM_PIXELS && ((uintptr_t)out & 15) == 0) {
        EXPAND(_mm_stream_ps);
        _mm_sfence();
    } else
        EXPAND(_mm_storeu_ps);
#undef EXPAND
#endif
    static const float fill[3] = {0.0f, 0.0f, 1.0f};
    for (; i < count; i++) {   /* memcpy: a byte copy on any target, whatever the float */
        memcpy(out + i * 4, values + i * components, components * sizeof(float));
        if (gray) {
            memcpy(out + i * 4 + 1, values + i, sizeof(float));
            memcpy(out + i * 4 + 2, values + i, sizeof(float));
            memcpy(out + i * 4 + 3, fill + 2, sizeof(float));
        } else
            memcpy(out + i * 4 + components, fill + components - 1, (4 - components) * sizeof(float));
    }
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

#ifdef OP_SSE_PIXELS
static inline __m128 derivative4(const float *height, const int32_t *charts, uint64_t at, uint64_t step,
                                 int has_before, int has_after, __m128 size) {
    const __m128 one = _mm_set1_ps(1.0f);
    __m128 value = _mm_loadu_ps(height + at);
    __m128i chart = _mm_loadu_si128((const __m128i *)(charts + at));
    __m128i inside = _mm_cmpgt_epi32(chart, _mm_setzero_si128());
    __m128 after = _mm_setzero_ps(), before = _mm_setzero_ps(), ahead = value, behind = value;
    if (has_after) {
        ahead = _mm_loadu_ps(height + at + step);
        after = _mm_castsi128_ps(_mm_and_si128(
            inside, _mm_cmpeq_epi32(_mm_loadu_si128((const __m128i *)(charts + at + step)), chart)));
    }
    if (has_before) {
        behind = _mm_loadu_ps(height + at - step);
        before = _mm_castsi128_ps(_mm_and_si128(
            inside, _mm_cmpeq_epi32(_mm_loadu_si128((const __m128i *)(charts + at - step)), chart)));
    }
    /* A mask keeps a value's bits or gives 0.0: the selects of the scalar code. */
    __m128 difference = _mm_and_ps(after, _mm_sub_ps(ahead, value));
    difference = _mm_add_ps(difference, _mm_and_ps(before, _mm_sub_ps(value, behind)));
    __m128 count = _mm_add_ps(_mm_and_ps(after, one), _mm_and_ps(before, one));
    return _mm_and_ps(_mm_cmpgt_ps(count, _mm_setzero_ps()), _mm_div_ps(_mm_mul_ps(difference, size), count));
}

/* relief() for texels c..c+3 of row r of an RGBA map (0 < c, c + 3 < n - 1). */
static inline void relief4(const float *normal, const float *height, const float *metric, const int32_t *charts,
                           uint32_t n, float distance, uint32_t r, uint32_t c, float *out) {
    const __m128 zero = _mm_setzero_ps(), one = _mm_set1_ps(1.0f), two = _mm_set1_ps(2.0f), half = _mm_set1_ps(0.5f),
                 floor = _mm_set1_ps(1e-8f), d = _mm_set1_ps(distance), size = _mm_set1_ps((float)n),
                 sign_bit = _mm_set1_ps(-0.0f);
    uint64_t at = (uint64_t)r * n + c;
    __m128 du = derivative4(height, charts, at, 1, 1, 1, size);
    __m128 dv = derivative4(height, charts, at, n, r > 0, r + 1 < n, size);
    const float *m = metric + at * 3;
    __m128 m0 = _mm_setr_ps(m[0], m[3], m[6], m[9]), m1 = _mm_setr_ps(m[1], m[4], m[7], m[10]),
           m2 = _mm_setr_ps(m[2], m[5], m[8], m[11]);
    __m128 sx = _mm_mul_ps(_mm_mul_ps(du, m0), d);
    __m128 sy = _mm_mul_ps(_mm_add_ps(_mm_mul_ps(du, m1), _mm_mul_ps(dv, m2)), d);
    const float *v = normal + at * 4;
    __m128 bx = _mm_loadu_ps(v), by = _mm_loadu_ps(v + 4), bz = _mm_loadu_ps(v + 8), w = _mm_loadu_ps(v + 12);
    _MM_TRANSPOSE4_PS(bx, by, bz, w);
    bx = _mm_sub_ps(_mm_mul_ps(bx, two), one);
    by = _mm_sub_ps(_mm_mul_ps(by, two), one);
    bz = _mm_sub_ps(_mm_mul_ps(bz, two), one);
    __m128 length = _mm_sqrt_ps(_mm_add_ps(_mm_add_ps(_mm_mul_ps(bx, bx), _mm_mul_ps(by, by)), _mm_mul_ps(bz, bz)));
    __m128 long_enough = _mm_cmpgt_ps(length, floor);
    bx = _mm_blendv_ps(zero, _mm_div_ps(bx, length), long_enough);
    by = _mm_blendv_ps(zero, _mm_div_ps(by, length), long_enough);
    bz = _mm_blendv_ps(one, _mm_div_ps(bz, length), long_enough);
    __m128 a = _mm_andnot_ps(sign_bit, bz);
    __m128 s = _mm_blendv_ps(_mm_blendv_ps(_mm_blendv_ps(bz, zero, _mm_cmpeq_ps(bz, zero)), _mm_xor_ps(one, sign_bit),
                                           _mm_cmplt_ps(bz, zero)),
                             one, _mm_cmpgt_ps(bz, zero));
    __m128 px = _mm_sub_ps(_mm_mul_ps(a, bx), _mm_mul_ps(s, _mm_mul_ps(sx, bz)));
    __m128 py = _mm_sub_ps(_mm_mul_ps(a, by), _mm_mul_ps(s, _mm_mul_ps(sy, bz)));
    __m128 pz = _mm_sub_ps(_mm_mul_ps(a, bz),
                           _mm_mul_ps(s, _mm_sub_ps(_mm_mul_ps(_mm_xor_ps(sx, sign_bit), bx), _mm_mul_ps(sy, by))));
    __m128 tilted = _mm_sqrt_ps(_mm_add_ps(_mm_add_ps(_mm_mul_ps(px, px), _mm_mul_ps(py, py)), _mm_mul_ps(pz, pz)));
    __m128 straight = _mm_cmpgt_ps(tilted, floor);
    px = _mm_blendv_ps(bx, _mm_div_ps(px, tilted), straight);
    py = _mm_blendv_ps(by, _mm_div_ps(py, tilted), straight);
    pz = _mm_blendv_ps(bz, _mm_div_ps(pz, tilted), straight);
    px = _mm_add_ps(_mm_mul_ps(px, half), half);
    py = _mm_add_ps(_mm_mul_ps(py, half), half);
    pz = _mm_add_ps(_mm_mul_ps(pz, half), half);
    _MM_TRANSPOSE4_PS(px, py, pz, w);
    float *o = out + at * 4;
    _mm_storeu_ps(o, px);
    _mm_storeu_ps(o + 4, py);
    _mm_storeu_ps(o + 8, pz);
    _mm_storeu_ps(o + 12, w);
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
#elif defined(OP_SSE_PIXELS)
        if (channels == 4) {
            relief(normal, height, metric, charts, n, channels, distance, r, 0, out);
            for (c = 1; c + 4 < n; c += 4)
                relief4(normal, height, metric, charts, n, distance, r, c, out);
        }
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
#elif defined(OP_SSE_PIXELS)
static inline __m128i absdiff8(__m128i x, __m128i y) { return _mm_or_si128(_mm_subs_epu8(x, y), _mm_subs_epu8(y, x)); }
static inline __m128i le8(__m128i x, __m128i y) { return _mm_cmpeq_epi8(_mm_min_epu8(x, y), x); }
/* (a + b) >> 1: pavgb rounds up, so its carried low bit comes off again. */
static inline __m128i halve8(__m128i a, __m128i b) {
    return _mm_sub_epi8(_mm_avg_epu8(a, b), _mm_and_si128(_mm_xor_si128(a, b), _mm_set1_epi8(1)));
}
static inline __m128i paeth16(__m128i a, __m128i b, __m128i c) {
    const __m128i zero = _mm_setzero_si128();
    __m128i pa = absdiff8(b, c), pb = absdiff8(a, c);
    __m128i low = _mm_abs_epi16(_mm_add_epi16(_mm_sub_epi16(_mm_unpacklo_epi8(a, zero), _mm_unpacklo_epi8(c, zero)),
                                              _mm_sub_epi16(_mm_unpacklo_epi8(b, zero), _mm_unpacklo_epi8(c, zero))));
    __m128i high = _mm_abs_epi16(_mm_add_epi16(_mm_sub_epi16(_mm_unpackhi_epi8(a, zero), _mm_unpackhi_epi8(c, zero)),
                                               _mm_sub_epi16(_mm_unpackhi_epi8(b, zero), _mm_unpackhi_epi8(c, zero))));
    __m128i pc = _mm_packus_epi16(low, high);   /* saturates at 255, as on NEON */
    __m128i use_a = _mm_and_si128(le8(pa, pb), le8(pa, pc));
    return _mm_blendv_epi8(_mm_blendv_epi8(c, b, le8(pb, pc)), a, use_a);
}
static inline __m128i filtered16(uint32_t type, __m128i x, __m128i a, __m128i b, __m128i c) {
    switch (type) {
    case 1: return _mm_sub_epi8(x, a);
    case 2: return _mm_sub_epi8(x, b);
    case 3: return _mm_sub_epi8(x, halve8(a, b));
    case 4: return _mm_sub_epi8(x, paeth16(a, b, c));
    default: return x;
    }
}
static inline __m128i magnitude16(__m128i f) { return _mm_min_epu8(f, _mm_sub_epi8(_mm_setzero_si128(), f)); }
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
#elif defined(OP_SSE_PIXELS)
    __m128i total[5];
    const __m128i zero = _mm_setzero_si128();
    for (int k = 0; k < 5; k++)
        total[k] = zero;
    for (; i + 16 <= n; i += 16) {
        __m128i vx = _mm_loadu_si128((const __m128i *)(x + i)), va = _mm_loadu_si128((const __m128i *)(x + i - bpp));
        __m128i vb = _mm_loadu_si128((const __m128i *)(b + i)), vc = _mm_loadu_si128((const __m128i *)(b + i - bpp));
        /* psadbw against zero sums each half's eight magnitudes into a 64-bit lane. */
        total[0] = _mm_add_epi64(total[0], _mm_sad_epu8(magnitude16(vx), zero));
        total[1] = _mm_add_epi64(total[1], _mm_sad_epu8(magnitude16(_mm_sub_epi8(vx, va)), zero));
        total[2] = _mm_add_epi64(total[2], _mm_sad_epu8(magnitude16(_mm_sub_epi8(vx, vb)), zero));
        total[3] = _mm_add_epi64(total[3], _mm_sad_epu8(magnitude16(_mm_sub_epi8(vx, halve8(va, vb))), zero));
        total[4] = _mm_add_epi64(total[4], _mm_sad_epu8(magnitude16(_mm_sub_epi8(vx, paeth16(va, vb, vc))), zero));
    }
    for (int k = 0; k < 5; k++)
        sums[k] += (uint64_t)_mm_cvtsi128_si64(total[k]) + (uint64_t)_mm_extract_epi64(total[k], 1);
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
#elif defined(OP_SSE_PIXELS)
    for (; i + 16 <= n; i += 16)
        _mm_storeu_si128((__m128i *)(out + i),
                         filtered16(type, _mm_loadu_si128((const __m128i *)(x + i)),
                                    _mm_loadu_si128((const __m128i *)(x + i - bpp)),
                                    _mm_loadu_si128((const __m128i *)(b + i)),
                                    _mm_loadu_si128((const __m128i *)(b + i - bpp))));
#endif
    for (; i < n; i++)
        out[i] = filtered(type, x[i], x[i - bpp], b[i], b[i - bpp]);
}

static void swap_row(const uint8_t *row, uint32_t n, uint8_t *out) {
    uint32_t i = 0;
#ifdef OP_NEON_PIXELS
    for (; i + 16 <= n; i += 16)
        vst1q_u8(out + i, vrev16q_u8(vld1q_u8(row + i)));
#elif defined(OP_SSE_PIXELS)
    const __m128i swap = _mm_setr_epi8(1, 0, 3, 2, 5, 4, 7, 6, 9, 8, 11, 10, 13, 12, 15, 14);
    for (; i + 16 <= n; i += 16)
        _mm_storeu_si128((__m128i *)(out + i), _mm_shuffle_epi8(_mm_loadu_si128((const __m128i *)(row + i)), swap));
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
