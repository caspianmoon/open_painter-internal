# Native library on Windows, Linux and Intel Macs

libopenpainter is C17 with a C ABI, loaded through ctypes and, in a release, a compiled
CPython binding. This page records what runs on each platform today and what remains to be
done on a Windows machine.

## Two gates

- **CPU kernels** (`lifecycle.cpu_library`) run wherever the library is loaded, on any GPU
  backend. They cover export pixels and padding, PNG filtering, UV rasterization, brush
  coverage, surface reach, topology, seams and SHA-256. Each is exact against the Python it
  replaces, so the platform changes only the speed. `OP_NATIVE_ENGINE=0` turns them off.
- **The GPU engine** (`lifecycle.active_library`, `execution_library`) runs with Blender's
  Metal and Vulkan backends, and with OpenGL on Windows. It covers native scheduling,
  suffix collapse and the stamps that make undo re-render 28 units. In a window it renders
  into the images' GPU textures on every backend; a background Vulkan session publishes
  through pixels (results below). Elsewhere OpenGL keeps its Python scheduler unless
  `OP_NATIVE_HOST_BACKENDS=OPENGL` opts it in.

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
  relief, PNG filters and compact readback expansion (R, RG or gray R to RGBA).
- **Brush coverage.**
- **UV rasterize**, and the raster kernels built on its coverage loop: interpolated maps
  (`op_uv_raster_interpolate`), assigned maps (`op_uv_raster_assign`) and padding ownership
  (`op_uv_raster_ownership`).

Compact expansion only moves bits (unpack, `movlhps`/`movhlps`, shuffle and blend; NEON
`vst4`). On x86-64 an aligned result of 1 MiB or more is written with non-temporal stores
(`movntps`), which skip reading each destination line first: half the time at 1K. MSVC
compiles the loops to the instruction sequence hand-written assembly would use, with no
further room in a store-bound loop. Hand-written assembly would add three versions to keep
exact (MASM, GAS, ARM64) for no speed, so the kernels stay in intrinsics.

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
- On the Windows laptop the NEON paths of `pixels.c` and `geometry.c` were also built with
  SIMDe 0.8.2's emulated NEON intrinsics on x86-64 (`neon_pixels.c` includes `pixels.c`
  with `OP_SIMD_NEON` and SIMDe's aliases): `test_pixels_paths`, `test_pixels` and
  `test_rasterize` pass against them, and mutations of the new NEON loops fail them. That
  checks the arithmetic of a NEON change before a Mac runs it natively.
- Portability fixes for Windows:
  - **Atomics:** the global serials no longer use `<stdatomic.h>`, which MSVC compiles only
    behind `/experimental:c11atomics`. `src/core/serial.h` uses `_InterlockedExchangeAdd64`
    there instead.
  - **Counters:** tests no longer keep counts in `long`, which is 32 bits on Windows.
  - **Floating point:** MSVC builds with an explicit `/fp:precise`, which does not contract
    a multiply and an add.

## Linux and Windows builds in containers

`scripts/cross_test_native.py` builds the library and runs its C tests with the
platform's own compiler. It uses whatever `docker` the environment points at.

| Target | Compiler | Path | Result |
| --- | --- | --- | --- |
| linux-arm64 | GCC 12 | NEON | 33 of 33, no warnings |
| linux-amd64 | GCC 12 | SSE4.2 | 33 of 33, no warnings |
| windows-amd64 | MinGW-w64 GCC 12, tests under Wine | SSE4.2 | 33 of 33, no warnings |

The Windows run checks a real PE DLL and the Windows ABI, though not MSVC. Under Wine
on Rosetta it is slow (the exhaustive sRGB test takes about five minutes), so the script
raises `OP_TEST_TIMEOUT` (CMake, default 20 seconds per test). GCC also found prototypes
whose array parameters (`float low[3]`) the definitions spelled as pointers, and an
allocation it could not see initialized; both are fixed.

On Apple silicon, amd64 containers need Rosetta: `colima start <profile> --vz-rosetta`.
If `~/.docker/config.json` names a credential helper that is not installed, point
`DOCKER_CONFIG` at a directory holding `{}`.

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

## Results on the Windows laptop (2026-10)

Windows 11, i7-8750H, GTX 1050 (4 GiB), Blender 5.2.2 LTS, MSVC 14.44. The
[Windows plan](windows-plan.md#progress-on-the-windows-laptop) has the details.

- **Build.**
  - MSVC compiles the library, the tests and the binding with no warnings at `/W4`.
  - All 33 C tests pass in the vector (SSE4.2) and scalar builds.
  - `dumpbin` shows the SSE4 instructions in `openpainter.dll`, so `test_pixels_paths`
    really compares the SSE path with the scalar one.
- **Binding.**
  - `_native.pyd` is built against the 3.11 limited API and imports `python3.dll`, which
    Blender ships.
  - It shares the UCRT heap with `openpainter.dll`.
- **CPU kernels.** They run on both GPU backends, and every Blender suite of the plan passes
  with and without the library, on OpenGL (the factory default there) and on Vulkan.
- **Engine on OpenGL.** On Windows the native scheduler is the default (`6ae821b`): every
  engine suite passes on it except the 4K suite, which needs more memory than the laptop
  has.
  - Warm edits are 2.5–6.6 times faster than with the Python scheduler. The deep stack
    peaks at 2.61 GiB against 1.99 with the Python scheduler (Vulkan's: 2.72). In a window
    OpenGL's commit charge runs higher than Vulkan's: `material_painting_ui.py` at 1K peaks
    at 4.78 GiB against 4.14 (4.54 with the Python scheduler).
  - Every backend publishes in place, without native revisions: as fast, faster after an
    undo, and about 0.45 GiB less at 1K (`windows-plan.md`, "Undo and publication in
    place"). Metal followed on 2026-10-11, with the same 40-layer stack at 1K: warm edits
    within noise, undo 37–38 → 32–33 ms, and three 1K sets in a window 4.51 → 4.23 GiB.
    `OP_REVISION_BACKENDS=METAL` restores revisions for comparison.
  - **Chain fusion on Metal** (2026-10-11). Blender compiles Metal shaders with fast math.
    A fused chain handing a full-float value to its next member then let the compiler fuse
    their arithmetic, and its one-time check retired it: in `native_fusion.py`, two fused
    shaders of one case retired at display precision. The handoff now passes through
    `op_opaque`, an identity the compiler cannot see through. None retires, and that case
    renders 48 fused passes instead of 36. At full
    float (`engine.precise`, used by parity tests) a Metal shader can still differ in the
    last bits and retire, so the result stays sequential. The round-to-nearest store
    emulation no longer calls `roundEven`, which Blender's Metal shading language lacks.
- **Engine on Vulkan.** Image textures are render targets on Vulkan in Blender 5.2.2 (an
  earlier belief otherwise came from a simulated GPU), so in a window the engine renders
  into them, as on Metal and OpenGL: edits at 1K are 3–10 times faster than through pixels
  (`windows-plan.md`, "Publication on Vulkan").
  - Vulkan is a default host backend (`lifecycle.host_backends()`).
  - A background session publishes through pixel readback, within a time budget per tick:
    nothing draws the GPU textures there, Vulkan image textures count toward the process
    commit charge on Windows, and released GPU resources are destroyed only after a wait
    or a frame end.
  - Channels publish in place, and `InPlacePresentation` restores an evicted image texture
    from the one it replaced.
  - The pixel route keeps suffix collapse, idle arming and fused finals. Through pixels,
    warm edits were about 1.4–3.3 times faster than the legacy scheduler, structural edits
    up to 3.5 times, and the cold build about 1.8 times. Every workflow and engine suite
    passes except the 4K one.
  - Through pixels, scalar channels publish one component (R16F, or R32F for Height): a
    quarter of the read-back bytes and of the half-to-float conversion, expanded to
    (v, v, v, 1) by `op_expand_compact`. At 1K a scalar channel reads back in 0.98 ms
    instead of 3.45, and edits that publish scalar channels got 10–24% faster.
- **Native raster kernels.** projection.geometry() (world positions and normals for
  triplanar fills, projections and the engine's geometry textures) takes 34 ms instead of
  260 at 1K, bit-identical; the padding ownership raster 8 instead of 110 ms. Without their
  per-texel sample arrays the 4K export fits the 4 GB cap (`export_padding.py`, 3.94 GiB on
  Vulkan).
  - Pixel publication has no revision transactions: channels can land a tick apart.
- **Defaults.** `host_backends()` is Metal, Vulkan and, on Windows, OpenGL; elsewhere OpenGL
  is opt-in.
