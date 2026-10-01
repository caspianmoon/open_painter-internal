/* op_sha256 against the FIPS 180-4 examples and against a separate portable implementation:
 * every length from 0 to 1100 bytes, random lengths up to 4 MiB, split into random updates. */
#include "openpainter/op_digest.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }
static void reference(const uint8_t *data, uint64_t size, uint8_t out[32]) {
    static const uint32_t k[64] = {
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
    uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    uint64_t padded = (size + 9 + 63) / 64 * 64;
    for (uint64_t at = 0; at < padded; at += 64) {
        uint8_t block[64];
        for (int i = 0; i < 64; i++) {
            uint64_t j = at + (uint64_t)i;
            block[i] = j < size ? data[j] : j == size ? 0x80 : 0;
            if (j >= padded - 8)
                block[i] = (uint8_t)((size * 8) >> (8 * (padded - 1 - j)));
        }
        uint32_t w[64];
        for (int i = 0; i < 16; i++)
            w[i] = (uint32_t)block[4 * i] << 24 | (uint32_t)block[4 * i + 1] << 16 | (uint32_t)block[4 * i + 2] << 8 |
                   block[4 * i + 3];
        for (int i = 16; i < 64; i++)
            w[i] = w[i - 16] + (rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3)) + w[i - 7] +
                   (rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10));
        uint32_t v[8];
        memcpy(v, h, sizeof(v));
        for (int i = 0; i < 64; i++) {
            uint32_t t1 = v[7] + (rotr(v[4], 6) ^ rotr(v[4], 11) ^ rotr(v[4], 25)) + ((v[4] & v[5]) ^ (~v[4] & v[6])) +
                          k[i] + w[i];
            uint32_t t2 = (rotr(v[0], 2) ^ rotr(v[0], 13) ^ rotr(v[0], 22)) + ((v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]));
            memmove(v + 1, v, 7 * sizeof(uint32_t));
            v[4] += t1;
            v[0] = t1 + t2;
        }
        for (int i = 0; i < 8; i++)
            h[i] += v[i];
    }
    for (int i = 0; i < 32; i++)
        out[i] = (uint8_t)(h[i / 4] >> (24 - 8 * (i % 4)));
}

static int hex_is(const uint8_t digest[32], const char *hex) {
    char text[65];
    for (int i = 0; i < 32; i++)
        snprintf(text + 2 * i, 3, "%02x", digest[i]);
    return strcmp(text, hex) == 0;
}

static uint64_t state = 0x2545f4914f6cdd1du;
static uint64_t next(void) {
    state ^= state << 13;
    state ^= state >> 7;
    state ^= state << 17;
    return state;
}

static int hashed(const uint8_t *data, uint64_t size, int split, uint8_t digest[32]) {
    op_sha256_t sha;
    if (op_sha256_init(&sha) != OP_OK)
        return 1;
    for (uint64_t at = 0; at < size;) {
        uint64_t take = split ? next() % (split == 1 ? 130 : 70000) : size;
        take = take > size - at ? size - at : take;
        if (op_sha256_update(&sha, data + at, take) != OP_OK)
            return 1;
        at += take;
    }
    return op_sha256_final(&sha, digest) != OP_OK;
}

int main(void) {
    uint8_t digest[32], expected[32];
    struct { const char *message, *hex; } fips[] = {
        {"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
        {"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"},
        {"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
         "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"},
        {"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu",
         "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1"}};
    for (size_t i = 0; i < sizeof(fips) / sizeof(*fips); i++)
        if (hashed((const uint8_t *)fips[i].message, strlen(fips[i].message), 0, digest) || !hex_is(digest, fips[i].hex)) {
            fprintf(stderr, "FIPS example %zu differs\n", i);
            return 1;
        }
    enum { BIG = 4 << 20 };
    uint8_t *data = malloc(BIG);
    if (!data)
        return 1;
    memset(data, 'a', 1000000);
    if (hashed(data, 1000000, 2, digest) ||
        !hex_is(digest, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0")) {
        fprintf(stderr, "a million 'a' differs\n");
        return 1;
    }
    for (uint64_t i = 0; i < BIG; i++)
        data[i] = (uint8_t)(next() >> 24);
    for (uint64_t size = 0; size <= 1100; size++)
        for (int split = 0; split < 2; split++) {
            reference(data, size, expected);
            if (hashed(data, size, split, digest) || memcmp(digest, expected, 32)) {
                fprintf(stderr, "length %llu (split %d) differs\n", (unsigned long long)size, split);
                return 1;
            }
        }
    for (int trial = 0; trial < 12; trial++) {
        uint64_t size = next() % BIG;
        reference(data, size, expected);
        if (hashed(data, size, 1 + trial % 2, digest) || memcmp(digest, expected, 32)) {
            fprintf(stderr, "random length %llu differs\n", (unsigned long long)size);
            return 1;
        }
    }
    /* final leaves the state: hashing more continues the same message. */
    op_sha256_t sha;
    op_sha256_init(&sha);
    op_sha256_update(&sha, data, 100);
    op_sha256_final(&sha, digest);
    op_sha256_update(&sha, data + 100, 200);
    op_sha256_final(&sha, digest);
    reference(data, 300, expected);
    if (memcmp(digest, expected, 32) || op_sha256_size() != sizeof(op_sha256_t) ||
        op_sha256_update(NULL, data, 1) != OP_INVALID_ARGUMENT)
        return 1;
    free(data);
    printf("sha256 matches FIPS 180-4 and its reference\n");
    return 0;
}
