/* The vector instruction set kernels compile for (internal). Each kernel file also takes a
 * -DOP_SCALAR_<kernel> switch that builds its scalar path instead, for comparison.
 *
 * NEON: 64-bit Arm with GCC or Clang (__aarch64__) or MSVC (_M_ARM64, Windows on Arm).
 * SSE4.2: x86-64; Blender 4.2 and later require it. MSVC allows the intrinsics without a
 * flag; GCC and Clang need -msse4.2, which CMake adds for x86-64 targets. */
#ifndef OPENPAINTER_OP_SIMD_H
#define OPENPAINTER_OP_SIMD_H
#if defined(__aarch64__) || defined(_M_ARM64)
#include <arm_neon.h>
#define OP_SIMD_NEON 1
#elif defined(__SSE4_2__) || (defined(_M_X64) && !defined(_M_ARM64EC))
#include <nmmintrin.h>
#define OP_SIMD_SSE 1
#endif
#endif
