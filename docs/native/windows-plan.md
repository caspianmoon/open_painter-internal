# Windows plan: state and next steps

This page hands the Windows work over from the macOS sessions. They left the work at commit
`fb335e1` on `feat/native-c-engine` (2026-10-01). It is written for a Claude Code
session on the user's Windows laptop. Read it before changing anything; it
replaces the context those sessions kept on the Mac.

## Rules the user set (non-negotiable)

1. **Zero failing tests.** Every red test is yours to triage and fix, including ones that
   were already red before your change. Fix the product, or update a test whose expectation
   is outdated after checking that each difference is intended. Never report "known
   failures" and move on.
2. **Memory-safe Blender runs.**
   - A 200-layer 2K benchmark once drove a Mac to 75 GB and crashed it.
   - Never run a large Blender benchmark (100+ layers, 2K+, structural loops) without the
     user's explicit approval for that run. Start at 40 layers and 1K.
   - Never run two Blender processes at once.
   - Launch Blender through the memory guard once it works on Windows (step 4).
   - The laptop's GPU has 4 GB of VRAM, so keep GPU fixtures small too.
3. **Native kernels are exact.**
   - Every C kernel must give bit-identical results to the Python (NumPy) code it replaces.
   - Don't let the compiler contract a multiply and an add. GCC/Clang get
     `-ffp-contract=off`; MSVC gets `/fp:precise`.
   - Every kernel keeps a scalar path (`-DOP_SCALAR_<kernel>`).
   - Every vector path (NEON, SSE4.2) has a test comparing it with the scalar path or the
     NumPy reference.
   - When you change a kernel, change it on both NEON and SSE and run `test_pixels_paths`.
   - Mutation-check new tests: change the arithmetic deliberately and make sure the test fails.
4. **Product rules.**
   - The managed material is exactly three nodes: Texture Set, Principled BSDF and
     Material Output.
   - There is exactly one node editor type, Open Painter Graph.
5. **Commits.** Commit as you go, with descriptive messages: what changed, why, and how it
   was verified. Work on a branch of your own (below). Ask the user before pushing.

## Progress on the Windows laptop

Branch `feat/native-windows`, 2026-10-01 to 10-02. Each commit message records its
evidence.

- **Machine:** Windows 11 22631; i7-8750H (SSE4.2, AVX2/FMA3); 12 GB RAM, about 5 GiB
  available with the desktop running.
- **GPU and Blender:** GTX 1050, 4 GiB, driver 560.70; Blender 5.2.2 LTS (`d13f752e3b9c`).
- **Toolchain:** VS 2022 Build Tools 17.14 (MSVC 14.44) with the Windows 11 SDK 10.0.26100;
  python.org CPython 3.11 for the binding headers.

| Step | State | Result |
| --- | --- | --- |
| 1. MSVC build | Done | 33/33 in the Release and scalar builds, no `/W4` warnings. `test_pixels` takes 41 s here, so pass `--test-timeout 120` to `build_native.py`. |
| 2. Unit tests | Done | 530 tests, no failures. 3 skips: no system libpng (2), POSIX shebang layout. |
| 3. Baseline matrix | Done | Vulkan 21/21 (`.blender-test/windows-vulkan/validation-native-branch`). |
| 4. Guard | Done | Job Object; `tests/unit/test_run_guarded.py`. |
| 5. Suites | Done | All pass without and with the library, on OpenGL and on Vulkan. `native_geometry_projection.py` needs the library. |
| 6. Binding | Done | In Blender, `abi()` is 13 and `compiled_binding: true`. |
| 7. Package | Done | `open_painter-windows-x64.zip`: validates, installs into a non-ASCII path on both backends with no environment variables, passes the two-phase clean install (6 GB cap), and the step 5 suites pass against its binaries. |
| 8. Native engine | Done for Vulkan | Vulkan is a default host backend, with collapse, idle arming and fused finals ported. OpenGL is opt-in (below). |
| 9. Windows on Arm | Not applicable | This laptop is x64. |
| 10. Full gate | See below | |

### Findings that change how to work here

- **Text encoding.** A plain `python.exe` reads and writes text as cp1252 on Windows.
  Blender's Python runs in UTF-8 mode, so add-on code is fine, but tests and scripts must
  pass `encoding="utf-8"`; they all do now.
- **Guard measure.** On Windows the guard caps the job's commit charge. GPU drivers commit
  system memory alongside video memory, so GUI runs commit 4–5 GiB with a 1.2–1.8 GiB
  working set. The summary reports `working_set_peak_gib`, `processes_peak` and peak
  dedicated GPU memory.
- **Approved caps.** The user approved these caps on this laptop:
  - GUI runs: 6 GB;
  - `resolution_switch.py` and `native_publication_4k.py`: 4 GB;
  - `utility_maps.py` under the OpenGL native scheduler: 5 GB. It commits 3.2 GiB on
    OpenGL otherwise, and `run_selected_tests.py` gives it 4 GB.
- **Bevel UVs are not bit-stable.** Re-evaluating a bevelled mesh can move a few corner
  UVs by one ulp, even with one thread. Tests that compare two evaluations at about 1e-6
  must read one snapshot (see `native_geometry_projection.py`, `evaluated_projection.py`).
- **Budgeted publication.** On Vulkan one tick publishes channels within a time budget,
  not all of them. A test may assert "current after one tick" only where
  `runtime.device().pixel_publish` is false.
- **GPU allocations at exit.** Some Vulkan runs print `VMA: UNFREED ALLOCATION` at exit, for
  example `layer_store.py`, `structural_patch.py` and `moved_layer_adoption.py`, with the
  legacy scheduler too. Each was a run that ended without `open_painter.unregister()`, which
  releases the device (`forget_device`) and unfreezes the collector (`gc_policy.release`).
  The likely cause: frozen reference cycles that hold GPU textures outlive Blender's GPU
  shutdown. A user who quits Blender sees the same, because Blender does not unregister
  add-ons on quit. No test fails, and it is cosmetic at exit.

### Step 8: the native scheduler on Vulkan and OpenGL

**Vulkan is a default host backend** (`lifecycle.host_backends()` is Metal and Vulkan;
OpenGL stays opt-in). Vulkan publishes through pixels because image textures cannot be
render targets. Four native features were missing on that path and are now ported:

- **Suffix collapse:** it ran only on the native channel path, which pixel publication
  does not take.
- **Idle arming:** it is enabled once no channel waits for the publication budget.
- **Fused finals:** these failed for native programs, which start with helper functions.
- **Reentry guard:** `evaluate`, `close` and `adopt` refuse during an evaluation.

Pixel publication keeps its per-tick budget (about 13 ms per channel at 1K, 50 ms at 2K on
the GTX 1050), which keeps drags responsive. It has no revision transactions: channels can
land a tick apart, and a fault leaves the channels published before it. The native suites
check each publication model's own guarantees.

Medians from `tests/integration/native_deep_stack.py` (40 layers, 1024, `OP_PREARM=1`), two
alternating rounds, native scheduler against `OP_NATIVE_ENGINE=0`, in ms:

| Case | Vulkan native | Vulkan legacy | OpenGL native | OpenGL legacy |
| --- | ---: | ---: | ---: | ---: |
| Cold build (s) | 1.8 | 3.0–3.4 | 1.9–2.8 | 2.9–5.4 |
| Bottom leaf roughness | 11 | 36 | 7 | 28 |
| Middle leaf opacity | 63–67 | 129–132 | 11 | 69 |
| Middle mask black | 60–62 | 132–134 | 26–27 | 73 |
| Bottom folder opacity | 129–135 | 178–194 | 13 | 78 |
| Blend mode (structural) | 63–64 | 144–162 | 20–71 | 76–94 |
| Remove layer (structural) | 34–39 | 119–184 | 13–14 | 58–59 |
| Apply smart material | 206–319 | 328 | 114–124 | 190–192 |
| Undo (numeric) | 280–287 | 303–710 | 285–289 | 302–314 |

Undo medians are noisy on both schedulers: each series of three starts with a cold sample
of about 700 ms. The OpenGL columns were measured before the Vulkan port.

- **Vulkan, as the default:**
  - 24 of 25 native suites, every step 5 suite and the GPU matrix (at `--sampler-limit`
    16 and 8) pass.
  - `native_publication_4k.py` exceeds the approved 4 GB cap (4.46 GiB commit) and is not
    run on this laptop.
- **OpenGL** (`OP_NATIVE_HOST_BACKENDS=OPENGL`):
  - The same result, and the 4K suite is likewise not run.
  - The native scheduler doubles video memory (1.4 against 0.6 GiB).
  - The user keeps OpenGL opt-in until that suite has run.

## Where things stand

### The native library

- `libopenpainter` is C17 with a C ABI. The current ABI is 13; the add-on manifest says
  0.14.1.
- The add-on loads it with ctypes. A release package also needs a compiled CPython binding
  (`_native.abi3.so` on macOS, `_native.pyd` on Windows) beside it, with SHA-256 hashes in
  a manifest. The loader rejects a bundled library without its binding.
- Development runs bypass the bundle: set `OP_LIBOPENPAINTER_PATH` (Blender) or
  `OP_TEST_NATIVE_LIBRARY` (unit tests) to a built library.

There are two gates (`src/open_painter/native_bridge/lifecycle.py`):

| Gate | What it enables | Where it runs today |
| --- | --- | --- |
| `cpu_library()` | CPU kernels: export pixels and padding, PNG filters, UV rasterize, brush coverage, surface reach, topology, seams, SHA-256 | Any platform and GPU backend once the library loads |
| `active_library()` / `execution_library(backend)` | The native GPU engine scheduler (incremental plans, suffix collapse, stamps that let undo re-render 28 units) | Metal and Vulkan. `OP_NATIVE_HOST_BACKENDS=OPENGL` opts OpenGL in, for qualification |

`src/core/simd.h` selects the vector paths:

| Target | Path | Selected by |
| --- | --- | --- |
| Windows x64, Linux x64, Intel Mac | SSE4.2 (Blender 4.2+ requires it) | `_M_X64` (MSVC, no flag needed); `__SSE4_2__` (CMake adds `-msse4.2` for GCC/Clang) |
| Windows on Arm | NEON, plus SHA2 | `_M_ARM64` (MSVC); clang-cl defines `__aarch64__` |
| macOS and Linux arm64 | NEON | `__aarch64__` |

### What has been verified

| Configuration | Result |
| --- | --- |
| macOS arm64 (Apple clang) | 33/33 C tests; unit tests; integration suites; Blender with Metal |
| x86-64 macOS build under Rosetta (SSE4.2, no AVX) | 33/33 C tests, including the exhaustive sRGB test |
| Linux arm64 and amd64, GCC 12 (`scripts/cross_test_native.py`) | 33/33, no warnings |
| Windows x64, MinGW-w64 GCC 12, tests under Wine | 33/33, no warnings |
| Windows x64, MSVC 14.44 (VS 2022 Build Tools), Release and scalar builds | 33/33, no warnings at `/W4` |
| Windows x64, unit tests with the MSVC library and its `_native.pyd` | 530 run, no failures |
| Windows x64, Blender 5.2.2, Vulkan and OpenGL | The step 5 suites with and without the library, the GPU matrix, the package install; see [progress](#progress-on-the-windows-laptop) |

Not verified anywhere yet:

- MSVC on ARM64 (step 9; the laptop is x64);
- the native GPU engine as the default on OpenGL (opt-in until the 4K suite has run);
- `native_publication_4k.py` on Windows (it needs more memory than this laptop allows).

The C code has no variable-length arrays (checked with `-Wvla`) and no GCC builtins. Two
global counters use `_InterlockedExchangeAdd64` under MSVC (`src/core/serial.h`), because
MSVC compiles `<stdatomic.h>` only behind `/experimental:c11atomics`.

### What the Mac work changed that runs on Windows too (Python, no library needed)

These are untested on Windows. Step 5 covers them.

- Undo reuses the units the undo left unchanged. It re-points them to the restored structs
  instead of compiling all of them again.
  - Code: `shader_graph.undo_index`, `_undo_structs`, `_follow_undo`.
  - Switches: `OP_VERIFY_UNDO=1` compares every reused unit with a fresh compile;
    `OP_UNDO_ADOPTION=0` turns reuse off.
- Image dependency epochs survive undo and redo (`dependencies.clear(undo=True)`).
- PNG export:
  - deflates 1 MiB segments in parallel threads, each segment primed with a dictionary;
  - applies row filters where a sample says they pay;
  - has a NumPy filter that gives the same bytes when the library is absent.
- Export also has native relief normals and SHA-256 hashing (the library fallbacks are
  NumPy and hashlib).

### Earlier Windows work in this repo (same laptop)

`docs/windows-gpu-performance.md` and `docs/windows-vulkan-round3.md`, `round4.md` and
`round5.md` cover earlier rounds:

- **Machine:** Windows 11 (22631), NVIDIA GTX 1050 with 4 GiB, driver 560.70, Blender
  5.2.2 LTS (`d13f752e3b9c`), Vulkan backend.
- **What changed:** they sped up the Python engine's Vulkan publication.
- **Tools:**
  - `scripts/benchmark_windows_viewport.py` for timings;
  - `scripts/run_gpu_compatibility.py` for the GPU regression matrix;
  - `scripts/collect_gate0_baseline.py` for the environment record.

  All three launch Blender themselves, so they work on Windows today.

Vulkan facts that matter (Blender 5.2):

- **Image textures:** they cannot be framebuffer targets on Vulkan, so the engine publishes
  through pixel readback. `PIXEL_PUBLISH` follows the backend, and
  `OP_ENGINE_PIXEL_PUBLISH=1` forces it elsewhere.
- **Pixel writes:** `Image.pixels.foreach_set` frees the image's GPU textures.
- **Shader cache:** SPIR-V is cached on disk.
- **Publication budget:** about 20 ms per evaluation.
- **Suites to rerun** whenever publication changes: `engine_pixel_publish.py` and
  `resolution_switch.py`.

## Setup

```powershell
git clone -c core.autocrlf=false -c core.longpaths=true https://github.com/caspianmoon/open_painter.git
cd open_painter
git switch feat/native-c-engine
git switch -c feat/native-windows      # the Mac sessions keep working on feat/native-c-engine
$blender = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
$py = 'C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe'   # has NumPy
```

- **Line endings:** keep `core.autocrlf=false`. The repo has no line-ending rules, and
  CRLF would change source digests and patch files. Git for Windows sets
  `core.autocrlf=true` system-wide, so an existing checkout has CRLF files: on a clean
  tree, `git config core.autocrlf false`, then `git rm -r --cached .` and
  `git reset --hard` rewrites them with LF.
- **Compiler:** install Visual Studio 2022 Build Tools with the Desktop C++ workload (MSVC
  v143, Windows SDK, CMake). Build from a *Developer PowerShell for VS 2022*, which puts
  `cl.exe` and `cmake` on PATH. On an ARM64 laptop, use the ARM64 native environment.
  Without the Windows SDK component, CMake fails at `LNK1181: cannot open input file
  'kernel32.lib'`; add `Microsoft.VisualStudio.Component.Windows11SDK.26100` from an
  elevated installer.
- **Binding (step 6):** a python.org CPython 3.11 or later x64 install provides the
  headers and `libs\python3.lib`; the module needs only the 3.11 limited API. Blender
  ships neither, but ships `python3.dll`. Ask the user before installing one.
- **Check the GPU backend** Blender uses (Preferences ▸ System, or
  `gpu.platform.backend_type_get()`). `--gpu-backend vulkan|opengl` forces one. Factory
  settings on this laptop select **OpenGL**, so pass `--gpu-backend vulkan` to
  `run_selected_tests.py`, `run_native_metal_tests.py` and `run_clean_install.py` (and
  `--backend vulkan` to `run_gpu_compatibility.py`) to test Vulkan.

## Steps

Do them in order. Each step says when it is done.

### 1. MSVC build and C tests

```powershell
& $py scripts/build_native.py
```

This configures `libopenpainter` with CMake (the Visual Studio generator), builds Release
and runs ctest. The library lands at `.blender-test\native\build\Release\openpainter.dll`.

- **Done when:** 33/33 C tests pass, with no MSVC warnings at `/W4`. Fix warnings rather
  than silencing them.
- **Likely trouble on ARM64:** NEON intrinsics MSVC may spell or support differently.
  Look at `vqtbl3q_u8` and the `uint8x16x3_t` initializer in `pixels.c`, the SHA2
  intrinsics in `sha256.c`, and `vrndnq_f32`/`vminnmq_f32`.
- **Timeouts:** a slow laptop may hit the 20 s per-test limit. Pass `--test-timeout 120`
  to `build_native.py` (it sets `-DOP_TEST_TIMEOUT`) rather than weakening a test. On the
  i7-8750H, `test_pixels` takes 41 s.
- Also run `& $py scripts/native_ctypes_spike.py --lib <dll>`, which CI ran on
  `windows-latest` (`.github/workflows/native-core.yml`; the Actions billing has been
  blocked, so CI has not been running).

### 2. Unit tests against the DLL

```powershell
$env:OP_TEST_NATIVE_LIBRARY = "$PWD\.blender-test\native\build\Release\openpainter.dll"
& $py -m unittest discover -s tests/unit -p "test_*.py"
```

- **Done when:** everything passes or skips for a stated platform reason. The Windows
  process-probe tests in `test_export_transaction.py` run here (the Mac skips them);
  they may need fixing.
- **Parity tests that matter:**
  - `test_native_pixels`: quantize, normal encoding, relief and PNG filters against
    NumPy;
  - `test_native_digest`: SHA-256 against hashlib;
  - `test_png_segments`: segmented, filtered PNG writing.

### 3. Re-establish the Windows baseline (no native library)

The branch has a lot of Python work since the last Windows round. Without
`OP_LIBOPENPAINTER_PATH`, the add-on runs its Python paths. Run the GPU matrix:

```powershell
& $py scripts/run_gpu_compatibility.py --blender $blender --backend vulkan --output .blender-test/windows-vulkan/validation-native-branch
```

- **Done when:** the matrix passes on Vulkan, or every failure is triaged and fixed (rule 1).
- The round documents above describe the earlier results to compare against.

### 4. Make the guarded test runners work on Windows

**Done.** `scripts/run_guarded.py` now runs on Windows with the same command line and
output. It starts the command suspended, puts it in a Job Object and resumes it, polls
the job's commit charge, and has the kernel limit the job to the cap plus 1 GiB. It also
reports the job's peak working set, process count and dedicated GPU memory.
`run_gpu_compatibility.py` and `run_clean_install.py` launch Blender through it too.

The original instructions, kept for reference:

`scripts/run_guarded.py` was macOS-only: it used `libproc` for process footprints,
`vm_stat` for free memory and `fcntl` for its lock. `run_tests.py`,
`run_selected_tests.py` and `run_native_metal_tests.py` all launch Blender through it.

Port it, keeping its command line and its `GUARD_SUMMARY` / `GUARD_KILLED` output:

- **Free memory:** `GlobalMemoryStatusEx` (`ullAvailPhys`), via ctypes.
- **Footprint cap:** put Blender in a Job Object. Either have the kernel enforce
  `JOB_OBJECT_LIMIT_JOB_MEMORY`, or poll the job's memory usage and call
  `TerminateJobObject` at the cap. Add `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Child
  processes join the job automatically.
- **VRAM:** a discrete GPU's dedicated memory is not in the job's commit charge, unlike the
  Mac footprint, which included GPU allocations. Say so in the summary.
- **Lock:** `msvcrt.locking` on a lock file, or an `O_EXCL` file.
- **Blender path:** every runner defaults `--blender` to the macOS path, so pass `--blender`.

Test the guard with a small Python child that allocates past a 1 GB cap. **Done when:**
`run_selected_tests.py --blender $blender shader_undo.py` runs and passes through the guard.

### 5. Blender suites on Windows, with and without the library

Run the suites below twice: once as they are, and once with
`$env:OP_LIBOPENPAINTER_PATH` set to the DLL, which turns the CPU kernels on. Add
`OP_VERIFY_STRUCTURE=1`, `OP_VERIFY_INCREMENTAL=1` and `OP_VERIFY_UNDO=1` for the undo and
patch suites.

- **Export:**
  - `production_export.py`, `export_target.py`, `export_view.py`;
  - `surface_relief_export.py`, `live_surface_relief.py`;
  - `export_safety.py`, `quality_baseline.py`.
- **Painting and geometry:**
  - `vector_strokes.py`, `stroke_parity.py`, `gpu_strokes.py`;
  - `paint_freeze.py`, `stroke_surface_bounds.py`;
  - `utility_maps.py`, `mesh_masks.py`, `evaluated_projection.py`;
  - `native_geometry_projection.py`, `decal_objects.py`;
  - `geometry_effects.py`, `geometry_sources.py`, `geometry_uses_memo.py`.
- **Undo and patches:**
  - `shader_undo.py`, `undo_leaf_lifetime.py`;
  - `layer_list_ops.py`, `layer_store.py`, `layer_cache.py`;
  - `structural_patch.py`, `moved_layer_adoption.py`, `shader_adoption.py`;
  - `surface_filters.py`, `engine_filters.py`.
- **Publication:** `engine_pixel_publish.py`, `resolution_switch.py`.

**Done when:** all pass in both runs. Windows-only failures are product bugs to fix, for
example a file still open while it is deleted or renamed in the export transaction.

Some suites assume Metal and belong to step 8: those in `run_native_metal_tests.TESTS`,
starting with `native_host_metal.py`, which asserts the execution backend
`c17-host-metal`.

### 6. The CPython binding (`_native.pyd`)

A release package needs it, and it is the faster GPU submission path for the native
engine.

- **CMake (`libopenpainter/CMakeLists.txt`):**
  - Replace the `WIN32` `FATAL_ERROR` in the `OP_PYTHON_INCLUDE` block.
  - Link `python3.lib` (the module uses `Py_LIMITED_API 0x030b0000`).
  - Set `SUFFIX ".pyd"`, keeping `PREFIX ""` and `OUTPUT_NAME "_native"`.
- **Check Blender's DLLs** in `5.2\python\bin`. A stable-ABI module imports
  `python3.dll`; if Blender does not ship it, link the versioned library instead and record
  why.
- **Build:** `& $py scripts/build_native.py --python-include <CPython 3.13>\include`.

**Done when:** in Blender, `load_library` loads the binding (`module.abi()` returns 13),
`_op_binding` is set, and the native diagnostics (`lifecycle.diagnostics()`) say
`compiled_binding: True`.

### 7. Package and install in an isolated profile

```powershell
& $py scripts/build_native.py --python-include <...> --package dist/open_painter-windows-x64.zip
```

`build_extension.py` bundles the library and binding under
`native_bridge/bin/windows-amd64/`, which is the name the loader looks for. It writes
their SHA-256 hashes into the manifest and sets the manifest's `platforms`.

Install only into an isolated profile, never the artist's real one:
`run_tests.py --package <zip>`, or `scripts/run_clean_install.py --package <zip> --blender
$blender`.

**Done when:** the clean install loads the bundled library and binding without
environment variables, and the suites in step 5 pass from the installed package.

### 8. Qualify the native GPU engine on Vulkan (then OpenGL)

Set `$env:OP_NATIVE_HOST_BACKENDS = 'VULKAN'`. `execution_library` then selects the native
scheduler on Vulkan, and the engine reports `c17-host-vulkan`.

1. **Generalize Metal-specific assertions:** the tests below expect `c17-host-metal`;
   make them expect `c17-host-<backend>`.
   - `native_host_metal.py`, `native_paint_reopen.py`, `native_update_latency.py`;
   - `native_sustained_ui.py`, `vector_brush_ui.py`.
2. **Run the suites:**
   - the suites in `run_native_metal_tests.TESTS` (pass `--blender`);
   - the undo and patch suites, with all three `OP_VERIFY_*` switches.
3. **Watch for:**
   - pixel publication: native revisions are skipped when `dev.pixel_publish` is set,
     which is always the case on Vulkan;
   - R/RG readback;
   - the binding's feedback-hazard checks on Vulkan;
   - the sampler limit (`run_gpu_compatibility.py --sampler-limit`).
4. **Measure** with the deep-stack benchmark, small first, comparing with
   `OP_NATIVE_ENGINE=0`. It needs `OP_DEEP_REPORT`, plus `OP_DEEP_LAYERS=40`,
   `OP_DEEP_RES=1024` and `OP_PREARM=1`. See how `benchmark_windows_viewport.py` launches
   Blender on Vulkan. Ask the user before going above 40 layers or 1K.

**Done when:** every engine suite passes on Vulkan and the benchmark shows the native
scheduler is not slower. Then:

- add `VULKAN` to the default `host_backends()`;
- record the evidence in `docs/native/supported-matrix.md` and `docs/native/platforms.md`;
- repeat the whole step for OpenGL. The engine already special-cases OpenGL uniforms
  (`_has_uniform` in `runtime.py`).

### 9. Windows on Arm (only on an ARM64 machine)

Repeat steps 1, 2 and 6 in the ARM64 native environment. The NEON paths, SHA2 included,
compile under `_M_ARM64`.

### 10. The full gate

`run_tests.py --keep-going`, then `--gui-only` and `--gpu-only`, one after another, never
overlapping. Background takes about 35 minutes on the Mac. **Done when:** zero failures.

## Reference

### Environment switches

| Variable | Effect |
| --- | --- |
| `OP_LIBOPENPAINTER_PATH` | Blender loads this library instead of the bundle (no binding needed) |
| `OP_TEST_NATIVE_LIBRARY` | Library for the unit tests |
| `OP_NATIVE_ENGINE=0` | No native library use at all (CPU kernels and engine) |
| `OP_NATIVE_HOST_BACKENDS` | Extra GPU backends for the native engine scheduler (`OPENGL`; Metal and Vulkan are defaults) |
| `OP_VERIFY_STRUCTURE=1` | Compare every incremental plan with a full one |
| `OP_VERIFY_INCREMENTAL=1` | Compare incremental native descriptions and stamps, rebound programs and adoption with full ones |
| `OP_VERIFY_UNDO=1` | Compare every unit an undo reused with a fresh compile |
| `OP_UNDO_ADOPTION=0` | Compile every unit after an undo (the old path) |
| `OP_ENGINE_PIXEL_PUBLISH=1` | Force Vulkan-style pixel publication on another backend |
| `OP_PREARM=1` | Idle suffix arming in background Blender (benchmarks) |
| `OP_DEEP_*` | Deep-stack benchmark options (see `tests/integration/native_deep_stack.py`) |
| CMake `-DOP_TEST_TIMEOUT=<s>` | Seconds per C test (default 20) |
| CMake `-DOP_SCALAR_<kernel>` | Build a kernel's scalar path (`PIXELS`, `COVERAGE`, `RASTER`, `SHA256`) |

### Test-runner facts

- Unit tests need NumPy, so use Blender's bundled Python.
- Run Blender suites one at a time.
- Suites that end with `bpy.app.timers.register(...)` (most `director_*.py`) do nothing
  in `--background` and pass vacuously. Run them in a real window, as `run_tests.py
  --gui-only` does.
- In background Blender, a failing `bpy.ops` call raises `RuntimeError`.
- RNA references to layers go stale after `stack.add()` and after undo. Re-resolve them by
  id.
- A suite can need things beyond the add-on before its failures mean anything:
  - the local artist file;
  - the Material Director's Codex CLI;
  - an isolated user profile (the runners create it with `scripts/blender_test_profile.py`).
- **Heavy on the Mac (memory):**
  - `native_publication_4k.py`, `resolution_switch.py`, `mask_groups.py`;
  - `artist_file_*.py` with the local artist file;
  - `auto_edge_rebake.py`, `bake_back.py`, `bake_corrections.py`, `geometric_curvature.py`
    (Cycles kernel compilation).

  Run them only with the user's go-ahead and a raised cap.

### Documents

- `docs/native/platforms.md`: the platform design behind this plan.
- `docs/native/deep-stacks.md`: how the engine works, the performance history and the
  open work. It covers undo, export kernels and PNG compression.
- `docs/native/supported-matrix.md`, `migration-status.md`, `bridge-decision.md`: what C owns
  and the qualification record. They still say ABI 11, but the library is ABI 13.
- `TESTING.md`, plus the Windows round documents above.

### Not for this machine unless the user asks

The remaining macOS-side performance work:

- opening a deep file (3.3 s);
- applying a smart material (~90 ms);
- the first undo after a build (~0.4 s);
- collapse inside isolated folders.

Kernel arithmetic changes belong on `feat/native-c-engine` with both vector paths updated.
