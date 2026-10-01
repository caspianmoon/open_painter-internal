# Native library on Windows, Linux and Intel Macs

libopenpainter is C17 with a C ABI, loaded through ctypes and, in a release, a compiled
CPython binding. This page records what runs on each platform today and what remains to be
done on a Windows machine.

## Two gates

- **CPU kernels** (`lifecycle.cpu_library`) run wherever the library is loaded, on any GPU
  backend. They cover export pixels and padding, PNG filtering, UV rasterization, brush
  coverage, surface reach, topology, seams and SHA-256. Each is exact against the Python it
  replaces, so the platform changes only the speed. `OP_NATIVE_ENGINE=0` turns them off.
- **The GPU engine** (`lifecycle.active_library`, `execution_library`) runs only with
  Blender's Metal backend. That covers native scheduling, suffix collapse and the stamps
  that make undo re-render 28 units. On Vulkan or OpenGL, the engine keeps its Python
  scheduler. Enabling it there is a Windows qualification step (below).

## Vector paths

`src/core/simd.h` selects one instruction set per build. Every kernel keeps a
`-DOP_SCALAR_<kernel>` switch for its scalar path.

| Target | Path | How it is selected |
| --- | --- | --- |
| macOS, Linux arm64 | NEON | `__aarch64__` |
| Windows on Arm | NEON (SHA2 too) | `_M_ARM64` (MSVC); clang-cl defines `__aarch64__` |
| x86-64 (Windows, Linux, Intel Mac) | SSE4.2 | `__SSE4_2__` (CMake adds `-msse4.2` for GCC/Clang), `_M_X64` (MSVC) |
| anything else | scalar | |

Blender 4.2 and later require SSE4.2 on x86-64, so every Blender 5.2 user has it. There is
no AVX path: Rosetta, where it is tested, has no AVX, and low-end Windows laptops often lack
it too. SHA-256 has no SHA-NI path, for the same reason (it cannot be tested here); x86
uses the portable block function (about 250 MB/s, three times Blender's hashlib).

The SSE kernels mirror their NEON counterparts operation for operation:

- **Pixels:** quantize, including the sRGB curve with its tie guard, normal encoding,
  relief and PNG filters.
- **Brush coverage.**
- **UV rasterize.**

Where SSE has no matching instruction, the replacement is exact:

- `_MM_TRANSPOSE4_PS` for NEON's interleaved loads (it only moves bits);
- lane extracts for the sRGB exponent table;
- `pavgb` minus the carried bit for `vhaddq_u8`;
- `psadbw` for the magnitude sums;
- `pshufb` for the byte swap.

The polynomial runs without fused multiply-add, and the exhaustive sRGB test holds it to
the same guard band. Under Rosetta, 2K RGBA, SSE against scalar: sRGB quantize 208 → 31 ms
(8-bit), relief 112 → 26 ms.

## Tests on this Mac

- `cmake -S libopenpainter -B <dir> -DCMAKE_OSX_ARCHITECTURES=x86_64` builds the x86-64
  library and tests. ctest runs them under Rosetta, which has SSE4.2 but no AVX. All 33
  pass. These include the exhaustive sRGB test over every float in [2⁻⁹, 1] and the
  coverage, padding and rasterize tests against their reference implementations.
- `test_pixels_paths` compiles `pixels.c` a second time with `OP_SCALAR_PIXELS` under other
  names. It then compares every vector path, NEON or SSE, with the scalar one bit for bit:
  normal encoding (zero-length pixels included), relief (chart borders, cliffs) and PNG
  filters (8 and 16 bits, flipped strides).
- Portability fixes for Windows:
  - **Atomics:** the global serials no longer use `<stdatomic.h>`, which MSVC compiles only
    behind `/experimental:c11atomics`. `src/core/serial.h` uses `_InterlockedExchangeAdd64`
    there instead.
  - **Counters:** tests no longer keep counts in `long`, which is 32 bits on Windows.
  - **Floating point:** MSVC builds with an explicit `/fp:precise`, which does not contract
    a multiply and an add.

## On the Windows machine

1. **Build and test.** Run `python scripts/build_native.py` with MSVC (x64, and arm64 for
   Snapdragon laptops), then run ctest. The SSE paths compile there without flags. Watch for
   MSVC warnings at `/W4`.
2. **Binding.** Qualify the CPython binding. CMake refuses to build it on Windows until it
   is qualified. It needs Blender's Python headers and `python3.lib` for the limited API.
   Then remove that `FATAL_ERROR` guard.
3. **Package.** Run `build_native.py --package`. `build_extension.py` names the bundle
   after the machine it runs on (`windows-amd64`, `windows-arm64`).
4. **Run the pure and integration suites** with `OP_LIBOPENPAINTER_PATH` set. The CPU
   kernels run as soon as the library loads.
5. **Engine.** To qualify the GPU engine on Vulkan or OpenGL, run the deep-stack benchmark
   and the engine suites with `OP_NATIVE_HOST_BACKENDS=VULKAN` (or `OPENGL`). That switch
   lets `execution_library` select the native scheduler on those backends. It is off by
   default until those suites pass there.
