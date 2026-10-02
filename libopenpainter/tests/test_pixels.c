/* op_quantize against the scalar arithmetic it replaced: identical codes for every float
 * from below the sRGB knee up to 1 at both bit depths (the vector curve decides most of them
 * without powf), and for random layouts of 1 to 5 channels with tails, NaN and infinities.
 * op_expand_compact against expansion written out by hand, bits included. */
#include "openpainter/op_pixels.h"
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static float reference_srgb(float x) {
    return x <= 0.0031308f ? x * 12.92f : 1.055f * powf(x, (float)(1 / 2.4)) - 0.055f;
}
static void reference(const float *values, uint64_t count, uint32_t channels, uint32_t bits, uint32_t srgb,
                      void *out) {
    const float maximum = bits == 8 ? 255.0f : 65535.0f;
    uint32_t curved = srgb ? (channels < 3 ? channels : 3) : 0;
    for (uint64_t i = 0; i < count * channels; i++) {
        float x = fminf(fmaxf(values[i], 0.0f), 1.0f);
        if ((uint32_t)(i % channels) < curved)
            x = reference_srgb(x);
        float q = rintf(x * maximum);
        if (bits == 8)
            ((uint8_t *)out)[i] = (uint8_t)q;
        else
            ((uint16_t *)out)[i] = (uint16_t)q;
    }
}

static uint64_t state = 0x9e3779b97f4a7c15u;
static float uniform(float lo, float hi) {
    state ^= state << 13;
    state ^= state >> 7;
    state ^= state << 17;
    return lo + (hi - lo) * (float)((state >> 11) * (1.0 / 9007199254740992.0));
}

static int compare(const void *mine, const void *theirs, uint64_t n, uint32_t bits, const float *values,
                   const char *what) {
    for (uint64_t i = 0; i < n; i++) {
        uint32_t a = bits == 8 ? ((const uint8_t *)mine)[i] : ((const uint16_t *)mine)[i];
        uint32_t b = bits == 8 ? ((const uint8_t *)theirs)[i] : ((const uint16_t *)theirs)[i];
        if (a != b) {
            fprintf(stderr, "%s, %u bits: value %llu (%.9g) gives %u, expected %u\n", what, bits,
                    (unsigned long long)i, values[i], a, b);
            return 1;
        }
    }
    return 0;
}

/* Compact R / RG pixels to RGBA: the source floats bit for bit (a quiet NaN with a payload, a
 * signalling NaN, -0, infinity, a subnormal), then 0 for missing green and blue and 1 for alpha,
 * or gray: the value in red, green and blue, then 1; each count up to 9 covers the vector steps
 * and their tails. */
static int expansion(void) {
    static const uint32_t patterns[] = {0x7fc12345u, 0x7f800001u, 0x80000000u, 0x7f800000u, 0x00000001u,
                                        0x3f800000u, 0xbf000000u, 0xffffffffu, 0x3eaaaaabu};
    enum { PATTERNS = sizeof(patterns) / sizeof(*patterns) };
    float source[2 * PATTERNS], out[4 * PATTERNS + 4];
    memcpy(source, patterns, sizeof patterns);
    memcpy(source + PATTERNS, patterns, sizeof patterns);
    for (uint32_t layout = 0; layout < 3; layout++)   /* R, RG, gray R */
        for (uint64_t count = 0; count <= PATTERNS; count++) {
            uint32_t components = layout == 1 ? 2 : 1, gray = layout == 2;
            memset(out, 0xa5, sizeof out);
            if (op_expand_compact(source, count, components, gray, out) != OP_OK)
                return 1;
            for (uint64_t i = 0; i < count; i++) {
                uint32_t expected[4] = {patterns[(i * components) % PATTERNS], 0, 0, 0x3f800000u}, got[4];
                if (components == 2)
                    expected[1] = patterns[(i * 2 + 1) % PATTERNS];
                if (gray)
                    expected[1] = expected[2] = expected[0];
                memcpy(got, out + i * 4, sizeof got);
                if (memcmp(got, expected, sizeof got)) {
                    fprintf(stderr, "expand layout %u of %llu, pixel %llu: %08x %08x %08x %08x\n", layout,
                            (unsigned long long)count, (unsigned long long)i, got[0], got[1], got[2], got[3]);
                    return 1;
                }
            }
            uint32_t after;   /* nothing written past the last pixel */
            memcpy(&after, out + count * 4, sizeof after);
            if (after != 0xa5a5a5a5u)
                return 1;
        }
    /* No components 0 or 3, no gray RG, no null pointers with pixels, no overlap; nothing to do
     * is fine. */
    if (op_expand_compact(source, 1, 0, 0, out) != OP_INVALID_ARGUMENT ||
        op_expand_compact(source, 1, 3, 0, out) != OP_INVALID_ARGUMENT ||
        op_expand_compact(source, 1, 2, 1, out) != OP_INVALID_ARGUMENT ||
        op_expand_compact(source, 1, 1, 2, out) != OP_INVALID_ARGUMENT ||
        op_expand_compact(NULL, 1, 1, 0, out) != OP_INVALID_ARGUMENT ||
        op_expand_compact(source, 1, 1, 0, NULL) != OP_INVALID_ARGUMENT ||
        op_expand_compact(out + 4, 2, 2, 0, out) != OP_INVALID_ARGUMENT ||
        op_expand_compact(out, 4, 1, 1, out + 3) != OP_INVALID_ARGUMENT ||
        op_expand_compact(NULL, 0, 1, 0, NULL) != OP_OK)
        return 1;
    printf("compact expansion keeps every bit and fills (0, 0, 1), or gray (v, v, v, 1)\n");
    return 0;
}

int main(void) {
    enum { CHUNK = 1 << 20 };
    float *values = malloc(CHUNK * 5 * sizeof(float));
    uint16_t *mine = malloc(CHUNK * 5 * sizeof(uint16_t)), *theirs = malloc(CHUNK * 5 * sizeof(uint16_t));
    if (!values || !mine || !theirs)
        return 1;
    /* Every float in [2^-9, 1]: the knee (0.0031308) and the whole curve above it. */
    uint64_t exhaustive = 0;
    for (uint32_t first = 0x3b000000u; first <= 0x3f800000u; first += CHUNK) {
        uint32_t n = 0x3f800000u - first + 1 < CHUNK ? 0x3f800000u - first + 1 : CHUNK;
        for (uint32_t j = 0; j < n; j++) {
            uint32_t bits = first + j;
            memcpy(values + j, &bits, sizeof(float));
        }
        for (uint32_t bits = 8; bits <= 16; bits += 8) {
            if (op_quantize(values, n, 1, bits, 1, mine) != OP_OK)
                return 1;
            reference(values, n, 1, bits, 1, theirs);
            if (compare(mine, theirs, n, bits, values, "exhaustive"))
                return 1;
        }
        exhaustive += n;
    }
    /* Layouts: channel counts, tails, linear alpha, values outside [0, 1]. */
    const float special[] = {0.0f, -0.0f, 1.0f, -1.0f, 2.0f, NAN, INFINITY, -INFINITY, 0.0031308f,
                             nextafterf(0.0031308f, 1.0f), 1e-45f, 0.5f / 255, 254.5f / 255, 32767.5f / 65535};
    uint64_t compared = 0;
    for (int trial = 0; trial < 400; trial++) {
        uint32_t channels = 1 + trial % 5;
        uint64_t count = 1 + (uint64_t)uniform(0, (float)(trial % 7 == 0 ? CHUNK - 1 : 300));
        for (uint64_t i = 0; i < count * channels; i++)
            values[i] = i % 13 == 5 ? special[(i / 13) % (sizeof(special) / sizeof(*special))] : uniform(-.2f, 1.2f);
        for (uint32_t bits = 8; bits <= 16; bits += 8)
            for (uint32_t srgb = 0; srgb < 2; srgb++) {
                if (op_quantize(values, count, channels, bits, srgb, mine) != OP_OK)
                    return 1;
                reference(values, count, channels, bits, srgb, theirs);
                if (compare(mine, theirs, count * channels, bits, values, srgb ? "layout, srgb" : "layout"))
                    return 1;
                compared += count * channels;
            }
    }
    if (op_quantize(values, 1, 0, 8, 0, mine) != OP_INVALID_ARGUMENT ||
        op_quantize(values, 1, 4, 12, 0, mine) != OP_INVALID_ARGUMENT)
        return 1;
    if (expansion())
        return 1;
    printf("quantize matches its reference over %llu floats in [2^-9, 1] and %llu layout values\n",
           (unsigned long long)exhaustive, (unsigned long long)compared);
    free(values);
    free(mine);
    free(theirs);
    return 0;
}
