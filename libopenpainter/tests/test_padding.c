/* op_padding_donors against the unpruned kernel it replaced (padding_reference.inc): donors and
 * distances must be identical, ties between islands and texels included. */
#include "padding_reference.inc"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static uint64_t state = 0x2545f4914f6cdd1du;
static uint32_t below(uint32_t n) {
    state ^= state << 13;
    state ^= state >> 7;
    state ^= state << 17;
    return (uint32_t)(state % n);
}

/* op_padding_extend over these donors, out of place and in place, against the plain gather. */
static int extension(const int32_t *donors, uint64_t count, uint32_t components) {
    enum { MAX = 512 };
    static float src[MAX * MAX * 4], out[MAX * MAX * 4], same[MAX * MAX * 4], expected[MAX * MAX * 4];
    for (uint64_t i = 0; i < count * components; i++)
        src[i] = (float)below(1u << 20) / 1024.0f - 512.0f;
    for (uint64_t i = 0; i < count; i++) {
        uint64_t from = donors[i] >= 0 ? (uint64_t)donors[i] : i;
        memcpy(expected + i * components, src + from * components, components * sizeof(float));
    }
    memcpy(same, src, count * components * sizeof(float));
    if (op_padding_extend(src, donors, count, components, out) != OP_OK ||
        op_padding_extend(same, donors, count, components, same) != OP_OK)
        return 1;
    return memcmp(out, expected, count * components * sizeof(float)) ||
           memcmp(same, expected, count * components * sizeof(float));
}

static void extension_rejects(void) {
    float values[16] = {0}, out[16] = {0};
    int32_t chained[4] = {-1, 0, 1, -1}, outside[4] = {-1, 4, -1, -1}, fine[4] = {-1, 0, -1, 2};
    assert(op_padding_extend(values, chained, 4, 4, out) == OP_VALIDATION_FAILED);   /* a donor's donor */
    assert(op_padding_extend(values, outside, 4, 4, out) == OP_VALIDATION_FAILED);
    assert(op_padding_extend(values, fine, 4, 4, values + 2) == OP_INVALID_ARGUMENT);   /* partial overlap */
    assert(op_padding_extend(values, fine, 4, 5, out) == OP_INVALID_ARGUMENT);
    assert(op_padding_extend(NULL, fine, 4, 4, out) == OP_INVALID_ARGUMENT);
    for (int i = 0; i < 16; i++)
        values[i] = (float)i;
    assert(op_padding_extend(values, fine, 4, 4, out) == OP_OK);
    /* Texel 1 takes texel 0, texel 3 takes texel 2; 0 and 2 keep their own. */
    assert(out[4] == 0.0f && out[7] == 3.0f && out[12] == 8.0f && out[15] == 11.0f && out[9] == 9.0f);
}

int main(void) {
    enum { MAX = 512 };
    static uint8_t covered[MAX * MAX];
    static int32_t islands[MAX * MAX], mine[MAX * MAX], theirs[MAX * MAX];
    static int16_t near[MAX * MAX], far[MAX * MAX];
    long long texels = 0;
    extension_rejects();
    for (int trial = 0; trial < 120; trial++) {
        uint32_t size = 8 + below(MAX - 8), limit = below(65), discs = 1 + below(12);
        memset(covered, 0, (size_t)size * size);
        for (uint64_t i = 0; i < (uint64_t)size * size; i++)
            islands[i] = -1;
        for (uint32_t d = 0; d < discs; d++) {
            int32_t cy = (int32_t)below(size), cx = (int32_t)below(size), r = 1 + (int32_t)below(size / 4 + 1);
            int32_t island = trial % 3 ? (int32_t)d : (int32_t)below(3);   /* islands that touch, some shared */
            for (int32_t y = cy - r; y <= cy + r; y++)
                for (int32_t x = cx - r; x <= cx + r; x++)
                    if (y >= 0 && x >= 0 && y < (int32_t)size && x < (int32_t)size &&
                        (x - cx) * (x - cx) + (y - cy) * (y - cy) <= r * r) {
                        covered[(uint64_t)y * size + x] = 1;
                        islands[(uint64_t)y * size + x] = island;
                    }
        }
        if (trial % 5 == 4)   /* scattered single texels: many equal distances */
            for (uint32_t j = 0; j < size; j++) {
                uint64_t i = (uint64_t)below(size) * size + below(size);
                covered[i] = 1;
                islands[i] = (int32_t)below(7);
            }
        op_status_t a = op_padding_donors(covered, islands, size, limit, mine, near);
        op_status_t b = reference_donors(covered, islands, size, limit, theirs, far);
        uint64_t count = (uint64_t)size * size;
        if (a != b || (a == OP_OK && (memcmp(mine, theirs, count * 4) || memcmp(near, far, count * 2)))) {
            fprintf(stderr, "trial %d (size %u, limit %u): status %d/%d differs\n", trial, size, limit, a, b);
            return 1;
        }
        if (a == OP_OK && extension(mine, count, 1 + (uint32_t)trial % 4)) {
            fprintf(stderr, "trial %d (size %u, limit %u): extension differs\n", trial, size, limit);
            return 1;
        }
        texels += (long long)count;
    }
    printf("padding donors and extension match their references over %lld texels\n", texels);
    return 0;
}
