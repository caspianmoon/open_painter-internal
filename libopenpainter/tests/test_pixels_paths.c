/* Every vector path of pixels.c (NEON or SSE) against its scalar path, bit for bit: the file is
 * compiled again here with OP_SCALAR_PIXELS under other names. Normal encoding, relief, PNG
 * filtering and compact expansion over random sizes and channel counts (tails included), with
 * values outside [0, 1], zero-length normals, chart borders, flipped row strides, 16-bit samples
 * and random float bit patterns (NaN payloads, infinities, subnormals, signed zeros). */
#include "openpainter/op_pixels.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define op_quantize scalar_quantize
#define op_normal_encode scalar_normal_encode
#define op_normal_relief scalar_normal_relief
#define op_png_filter scalar_png_filter
#define op_expand_compact scalar_expand_compact
#define OP_SCALAR_PIXELS 1
#include "../src/core/pixels.c"
#undef op_quantize
#undef op_normal_encode
#undef op_normal_relief
#undef op_png_filter
#undef op_expand_compact

static uint64_t state = 0x6c8e9cf570932bd5u;
static uint64_t next(void) {
    state ^= state << 13;
    state ^= state >> 7;
    state ^= state << 17;
    return state;
}
static float uniform(float lo, float hi) { return lo + (hi - lo) * (float)((next() >> 11) * (1.0 / 9007199254740992.0)); }

static int differs(const void *a, const void *b, size_t bytes, const char *what, int trial) {
    if (!memcmp(a, b, bytes))
        return 0;
    const unsigned char *x = a, *y = b;
    size_t at = 0;
    while (x[at] == y[at])
        at++;
    fprintf(stderr, "%s, trial %d: byte %zu differs (%u != %u)\n", what, trial, at, x[at], y[at]);
    return 1;
}

int main(void) {
    enum { N = 70 };
    static float values[N * N * 4], mine[N * N * 4], theirs[N * N * 4], height[N * N], metric[N * N * 3];
    static int32_t charts[N * N];
    static uint8_t rows[N * N * 8 + 64], lines_a[N * (N * 8 + 1)], lines_b[N * (N * 8 + 1)];
    for (int trial = 0; trial < 300; trial++) {
        uint32_t n = 1 + (uint32_t)(next() % N), channels = 3 + (uint32_t)(trial % 2);
        uint64_t count = (uint64_t)n * n;
        for (uint64_t i = 0; i < count * channels; i++) {
            uint64_t pixel = i / channels;   /* whole pixels of 0.5 (decoded) or 0 (as is): no length */
            values[i] = pixel % 17 == 5 ? 0.5f : pixel % 19 == 6 ? 0.0f : uniform(-0.2f, 1.2f);
        }
        for (uint32_t decode = 0; decode < 2; decode++) {
            if (op_normal_encode(values, mine, count, channels, decode) ||
                scalar_normal_encode(values, theirs, count, channels, decode) ||
                differs(mine, theirs, count * channels * 4, "normal encode", trial))
                return 1;
        }
        for (uint64_t i = 0; i < count; i++) {
            height[i] = uniform(-1, 1) * (i % 31 == 3 ? 1e4f : 1);
            charts[i] = (int32_t)(next() % 4);
            if ((i / n) > n / 3 && (i % n) > n / 3)
                charts[i] = 9;   /* one large chart */
            for (int k = 0; k < 3; k++)
                metric[i * 3 + k] = uniform(-3, 3);
        }
        float distance = trial % 3 == 0 ? 25.0f : uniform(0.001f, 2.0f);
        if (op_normal_relief(values, height, metric, charts, n, channels, distance, mine) ||
            scalar_normal_relief(values, height, metric, charts, n, channels, distance, theirs) ||
            differs(mine, theirs, count * channels * 4, "relief", trial))
            return 1;
        /* PNG rows: smooth, noisy or flat, 8 or 16 bits, read forward or flipped, after a row or not. */
        uint32_t pixels = 1 + (uint32_t)(next() % N), height_rows = 2 + (uint32_t)(next() % 12);
        uint32_t samples = trial % 4 == 0 ? 3 : 4, bits = trial % 3 == 1 ? 16 : 8;
        uint32_t row_bytes = pixels * samples * bits / 8, bpp = samples * bits / 8;
        for (uint32_t i = 0; i < row_bytes * height_rows; i++)
            rows[i] = trial % 5 == 0 ? (uint8_t)(i % 7) : trial % 5 == 1 ? (uint8_t)next() : (uint8_t)(i / 3 + next() % 4);
        int flipped = trial % 2;
        const uint8_t *first = flipped ? rows + (uint64_t)row_bytes * (height_rows - 1) : rows + row_bytes;
        int64_t stride = flipped ? -(int64_t)row_bytes : (int64_t)row_bytes;
        uint32_t filtered = height_rows - 1, previous = (uint32_t)(trial % 3 != 0);
        if (op_png_filter(first, stride, filtered, row_bytes, bpp, bits == 16, previous, lines_a) ||
            scalar_png_filter(first, stride, filtered, row_bytes, bpp, bits == 16, previous, lines_b) ||
            differs(lines_a, lines_b, (size_t)filtered * (row_bytes + 1), "png filter", trial))
            return 1;
        /* Compact R / RG / gray R transfers: any 32-bit pattern, each pixel count's tail. */
        uint32_t components = trial % 3 == 1 ? 2 : 1, gray = trial % 3 == 2;
        uint64_t compact = (uint64_t)(next() % ((uint64_t)N * N + 1));
        for (uint64_t i = 0; i < compact * components; i++) {
            uint32_t bits32 = (uint32_t)next();
            memcpy(values + i, &bits32, sizeof bits32);
        }
        memset(mine, 0xa5, sizeof mine);
        memset(theirs, 0x5a, sizeof theirs);
        if (op_expand_compact(values, compact, components, gray, mine) ||
            scalar_expand_compact(values, compact, components, gray, theirs) ||
            differs(mine, theirs, compact * 16, "compact expansion", trial))
            return 1;
    }
    /* Large enough for non-temporal stores (1 MiB of RGBA), into aligned and unaligned outputs,
     * with tails. */
    float *wide = malloc(70001 * 2 * sizeof(float)), *expanded = malloc((70001 * 4 + 4) * sizeof(float)),
          *reference = malloc((70001 * 4 + 4) * sizeof(float));
    if (!wide || !expanded || !reference)
        return 1;
    for (uint64_t i = 0; i < 70001 * 2; i++) {
        uint32_t bits32 = (uint32_t)next();
        memcpy(wide + i, &bits32, sizeof bits32);
    }
    const uint64_t counts[] = {65535, 65536, 65537, 65539, 70001};
    for (uint32_t layout = 0; layout < 3; layout++)   /* R, RG, gray R */
        for (int c = 0; c < 5; c++)
            for (uint32_t offset = 0; offset < 2; offset++) {   /* malloc aligns to 16 bytes; +1 float does not */
                uint32_t components = layout == 1 ? 2 : 1, gray = layout == 2;
                memset(expanded, 0xa5, (70001 * 4 + 4) * sizeof(float));
                memset(reference, 0x5a, (70001 * 4 + 4) * sizeof(float));
                if (op_expand_compact(wide, counts[c], components, gray, expanded + offset) ||
                    scalar_expand_compact(wide, counts[c], components, gray, reference + offset) ||
                    differs(expanded + offset, reference + offset, counts[c] * 16, "wide compact expansion", c))
                    return 1;
            }
    free(wide);
    free(expanded);
    free(reference);
    printf("pixel kernels' vector paths match their scalar paths\n");
    return 0;
}
