#include "openpainter/op_digest.h"
#include <string.h>
/* The Arm SHA2 instructions: GCC and Clang say so with __ARM_FEATURE_SHA2; MSVC (Windows on
 * Arm, whose processors all have them) defines only _M_ARM64. */
#if ((defined(__aarch64__) && defined(__ARM_FEATURE_SHA2)) || (defined(_M_ARM64) && !defined(__clang__))) && \
    !defined(OP_SCALAR_SHA256)
#include <arm_neon.h>
#define OP_ARM_SHA256 1   /* build with -DOP_SCALAR_SHA256 to compare */
#endif

static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};

#ifdef OP_ARM_SHA256
/* Four rounds a step: SHA256H/SHA256H2 take ABCD and EFGH as they are stored, and
 * SHA256SU0/SU1 extend the message schedule four words at a time. */
static void blocks(uint32_t state[8], const uint8_t *data, uint64_t count) {
    uint32x4_t abcd = vld1q_u32(state), efgh = vld1q_u32(state + 4);
    for (; count; count--, data += 64) {
        uint32x4_t m0 = vreinterpretq_u32_u8(vrev32q_u8(vld1q_u8(data)));
        uint32x4_t m1 = vreinterpretq_u32_u8(vrev32q_u8(vld1q_u8(data + 16)));
        uint32x4_t m2 = vreinterpretq_u32_u8(vrev32q_u8(vld1q_u8(data + 32)));
        uint32x4_t m3 = vreinterpretq_u32_u8(vrev32q_u8(vld1q_u8(data + 48)));
        uint32x4_t abcd0 = abcd, efgh0 = efgh, wk, a;
#define ROUNDS(i, m)                                   \
    wk = vaddq_u32(m, vld1q_u32(K + 4 * (i)));         \
    a = abcd;                                          \
    abcd = vsha256hq_u32(abcd, efgh, wk);              \
    efgh = vsha256h2q_u32(efgh, a, wk)
#define EXTEND(w, x, y, z) w = vsha256su1q_u32(vsha256su0q_u32(w, x), y, z)
        ROUNDS(0, m0);  EXTEND(m0, m1, m2, m3);
        ROUNDS(1, m1);  EXTEND(m1, m2, m3, m0);
        ROUNDS(2, m2);  EXTEND(m2, m3, m0, m1);
        ROUNDS(3, m3);  EXTEND(m3, m0, m1, m2);
        ROUNDS(4, m0);  EXTEND(m0, m1, m2, m3);
        ROUNDS(5, m1);  EXTEND(m1, m2, m3, m0);
        ROUNDS(6, m2);  EXTEND(m2, m3, m0, m1);
        ROUNDS(7, m3);  EXTEND(m3, m0, m1, m2);
        ROUNDS(8, m0);  EXTEND(m0, m1, m2, m3);
        ROUNDS(9, m1);  EXTEND(m1, m2, m3, m0);
        ROUNDS(10, m2); EXTEND(m2, m3, m0, m1);
        ROUNDS(11, m3); EXTEND(m3, m0, m1, m2);
        ROUNDS(12, m0);
        ROUNDS(13, m1);
        ROUNDS(14, m2);
        ROUNDS(15, m3);
#undef ROUNDS
#undef EXTEND
        abcd = vaddq_u32(abcd, abcd0);
        efgh = vaddq_u32(efgh, efgh0);
    }
    vst1q_u32(state, abcd);
    vst1q_u32(state + 4, efgh);
}
#else
static uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }
static void blocks(uint32_t state[8], const uint8_t *data, uint64_t count) {
    for (; count; count--, data += 64) {
        uint32_t w[64];
        for (int i = 0; i < 16; i++)
            w[i] = (uint32_t)data[4 * i] << 24 | (uint32_t)data[4 * i + 1] << 16 | (uint32_t)data[4 * i + 2] << 8 |
                   data[4 * i + 3];
        for (int i = 16; i < 64; i++) {
            uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
            uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
        uint32_t e = state[4], f = state[5], g = state[6], h = state[7];
        for (int i = 0; i < 64; i++) {
            uint32_t t1 = h + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i];
            uint32_t t2 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
            h = g;
            g = f;
            f = e;
            e = d + t1;
            d = c;
            c = b;
            b = a;
            a = t1 + t2;
        }
        state[0] += a;
        state[1] += b;
        state[2] += c;
        state[3] += d;
        state[4] += e;
        state[5] += f;
        state[6] += g;
        state[7] += h;
    }
}
#endif

uint32_t op_sha256_size(void) { return (uint32_t)sizeof(op_sha256_t); }

op_status_t op_sha256_init(op_sha256_t *sha) {
    static const uint32_t initial[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    if (!sha)
        return OP_INVALID_ARGUMENT;
    memset(sha, 0, sizeof(*sha));
    memcpy(sha->state, initial, sizeof(initial));
    return OP_OK;
}

op_status_t op_sha256_update(op_sha256_t *sha, const void *data, uint64_t size) {
    if (!sha || (size && !data) || sha->used >= 64 || size > UINT64_MAX / 8 - sha->length)
        return OP_INVALID_ARGUMENT;
    const uint8_t *bytes = data;
    sha->length += size;
    if (sha->used) {
        uint32_t take = 64 - sha->used < size ? 64 - sha->used : (uint32_t)size;
        memcpy(sha->buffer + sha->used, bytes, take);
        sha->used += take;
        bytes += take;
        size -= take;
        if (sha->used < 64)
            return OP_OK;
        blocks(sha->state, sha->buffer, 1);
        sha->used = 0;
    }
    blocks(sha->state, bytes, size / 64);
    bytes += size / 64 * 64;
    sha->used = (uint32_t)(size % 64);
    memcpy(sha->buffer, bytes, sha->used);
    return OP_OK;
}

op_status_t op_sha256_final(const op_sha256_t *sha, uint8_t digest[32]) {
    if (!sha || !digest || sha->used >= 64)
        return OP_INVALID_ARGUMENT;
    uint32_t state[8];
    uint8_t tail[128] = {0};
    memcpy(state, sha->state, sizeof(state));
    memcpy(tail, sha->buffer, sha->used);
    tail[sha->used] = 0x80;
    uint32_t total = sha->used + 9 <= 64 ? 64 : 128;
    uint64_t bits = sha->length * 8;
    for (int i = 0; i < 8; i++)
        tail[total - 1 - i] = (uint8_t)(bits >> (8 * i));
    blocks(state, tail, total / 64);
    for (int i = 0; i < 8; i++) {
        digest[4 * i] = (uint8_t)(state[i] >> 24);
        digest[4 * i + 1] = (uint8_t)(state[i] >> 16);
        digest[4 * i + 2] = (uint8_t)(state[i] >> 8);
        digest[4 * i + 3] = (uint8_t)state[i];
    }
    return OP_OK;
}
