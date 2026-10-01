/* Process-wide serial counters (internal). MSVC compiles C11 atomics only behind
 * /experimental:c11atomics, so there its interlocked intrinsic does the same increment. */
#ifndef OPENPAINTER_OP_SERIAL_H
#define OPENPAINTER_OP_SERIAL_H
#include <stdint.h>
#if defined(_MSC_VER) && !defined(__clang__)
#include <intrin.h>
typedef volatile __int64 op_serial_t;
/* The value before the increment. */
static inline uint64_t op_serial_next(op_serial_t *serial) {
    return (uint64_t)_InterlockedExchangeAdd64(serial, 1);
}
#else
#include <stdatomic.h>
typedef atomic_uint_fast64_t op_serial_t;
/* The value before the increment. */
static inline uint64_t op_serial_next(op_serial_t *serial) { return (uint64_t)atomic_fetch_add(serial, 1); }
#endif
#endif
