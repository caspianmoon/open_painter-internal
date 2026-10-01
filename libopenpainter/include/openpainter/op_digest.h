#ifndef OP_DIGEST_H
#define OP_DIGEST_H
#include "op_api.h"
#ifdef __cplusplus
extern "C" {
#endif
/* SHA-256 (FIPS 180-4), incremental. On arm64 builds with the SHA2 extension the block
 * function uses its instructions; elsewhere a portable one. Digests are identical. */
typedef struct op_sha256 {
    uint32_t state[8];
    uint64_t length;      /* bytes hashed so far */
    uint8_t buffer[64];   /* a partial block */
    uint32_t used;
    uint32_t reserved;
} op_sha256_t;

/* sizeof(op_sha256_t), for bindings that allocate the state themselves. */
uint32_t op_sha256_size(void);
op_status_t op_sha256_init(op_sha256_t *sha);
op_status_t op_sha256_update(op_sha256_t *sha, const void *data, uint64_t size);
/* The digest of everything hashed so far; the state is left as it is (more may follow). */
op_status_t op_sha256_final(const op_sha256_t *sha, uint8_t digest[32]);
#ifdef __cplusplus
}
#endif
#endif
