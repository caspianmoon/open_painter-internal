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

op_status_t op_quantize(const float *values, uint64_t count, uint32_t channels, uint32_t bits,
                        uint32_t srgb_rgb, void *out) {
    if ((count && (!values || !out)) || count > MAX_PIXELS || !channels || channels > 16 ||
        (bits != 8 && bits != 16) || srgb_rgb > 1)
        return OP_INVALID_ARGUMENT;
    const float maximum = bits == 8 ? 255.0f : 65535.0f;
    uint32_t curved = srgb_rgb ? (channels < 3 ? channels : 3) : 0;
    uint64_t total = count * channels, i = 0;
    uint8_t *bytes = out;
    uint16_t *words = out;
#ifdef OP_NEON_PIXELS
    if (!curved) {
        /* No curve: four values a step (clip, scale, round half to even, convert). */
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
    for (; i < total; i++) {
        float x = clip(values[i]);
        if ((uint32_t)(i % channels) < curved)
            x = srgb(x);
        float q = rintf(x * maximum);
        if (bits == 8)
            bytes[i] = (uint8_t)q;
        else
            words[i] = (uint16_t)q;
    }
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
