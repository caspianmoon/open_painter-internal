# Windows GPU and interaction validation

The subsequent [Round 3 Vulkan report](windows-vulkan-round3.md) records further
GPU-pass and constant-fill optimizations. This page preserves the earlier
measurements and validation history.

This pass reduces Vulkan publication work for proven uniform outputs, fixes
native R/RG texture readback, and removes CPU work that interrupts viewport and
Open Graph navigation. On this Windows GTX 1050, the final 2048 mask-edit
benchmark fell from 275.63 to 167.05 ms; forced publication of all channels fell
from 607.21 to 236.91 ms. These are engine interaction timings, not native
viewport frame rates or a general Vulkan advantage over OpenGL.

## Tested machine

Local validation on 2026-09-28 used:

- Windows 11, build 22631.
- Blender 5.2.2 LTS, build `d13f752e3b9c`.
- NVIDIA GeForce GTX 1050, 4 GiB VRAM; NVIDIA driver 560.70.
- Based on `task/open-graph-0.4.0` at `1645a4d`; local changes on
  `fix/windows-vulkan-performance`; extension manifest version `0.13.1`.

Each GPU benchmark process records the actual backend, renderer, driver,
Blender version, source hash, and whether pixel publication is active. A
requested backend mismatch fails the run. These historical runs used factory
startup and `BLENDER_USER_RESOURCES`, but did not verify Blender's resolved
configuration directory. Profile isolation was therefore not proven; the
[Round 3 report](windows-vulkan-round3.md#gpu-measurements) records the later
preference restoration and explicit resource-path guards.

## Changes and limits

`Device.readback()` now requests the texture's native component count for
`R16F`, `R32F`, `RG16F`, and `RG32F`. Vulkan returns packed native components;
treating these as already expanded RGBA scrambled pixel order and left missing
components incorrect. The helper expands them explicitly to RGBA, with zero
for missing color components and one for missing alpha. Its returned buffer is
reused; callers retaining an array must copy it before another readback.

The real GPU backend regression checks distinct values at every pixel for all
four compact formats, alongside GPU state restoration. This is a correctness
fix for compact intermediate textures. Final channel publication retains its
existing RGBA format and precision.

On this Blender Vulkan path, image textures are not framebuffer render targets.
Varied outputs therefore still read finished output into a reused CPU buffer and
write `Image.pixels`; Blender uploads that image for the material to sample. The
publication budget spreads changed channels over successive evaluations, but
at least one channel is published per evaluation. It is not a hard upper bound
on a callback's wall time. A save, render, export, or explicit flush still needs
all required pixels to be current.

Proven neutral data outputs now initialize their canonical CPU pixels once,
then use a Vulkan texture transfer-clear covering every mip level. Later forced
publication needs no GPU readback or repeated CPU pixel write. Keeping those CPU
pixels current prevents texture eviction or `Image.gl_free()` from resurrecting
old content and preserves save, render, and export behavior. Leaving a uniform
state invalidates its CPU initialization record; re-entering initializes it
again. Normal edit evaluation skips an unchanged uniform result altogether.

The proof accepts exact defaults through plain root fills with NORMAL blending,
including a bounded ordinary UV CELLULAR mask. Numeric and structural edits are
checked again; images, channel effects, source anchors, folders, caches, other
blend modes, and unknown state fall back. Only data-tagged images are eligible;
Base Color is excluded. In this fixture NORMAL, HEIGHT, AO, and OPACITY use the
shortcut. Other channels can still render a proved channel's unit when reading
its OUTPUT anchor. Explicit `--pixel-publish force` retains the full pixel path.

Backend-independent navigation changes are documented in
[Viewport interaction costs on Windows](viewport-interaction-performance.md).
They defer fallback whole-canvas paint scans during modal navigation, avoid a
duplicate idle Open Graph stack fingerprint, and reuse the graph row-guide GPU
batch. Lightweight scheduler/status diagnostics also avoid repeatedly building
cached-layer details. With 100 layers and 400 cached channels, the separate CPU
fixture's idle scheduler median fell from 35.701 to 0.103 ms and status polling
from 18.525 to 0.013 ms. That document includes the CPU scope and regressions.

Windows validation also exposed two export defects fixed in this pass. Export
locks now probe a process handle instead of calling `os.kill(pid, 0)`, which
terminates a process on Windows. Exclusive-create publication opens image
files in binary mode so the Windows CRT cannot translate LF bytes. The
packager reads its manifest and Markdown explicitly as UTF-8.

## Benchmark protocol

[`scripts/benchmark_windows_viewport.py`](../scripts/benchmark_windows_viewport.py)
uses protocol version 2. It launches a fresh process per texture resolution
(512, 1024, 2048) with the same two-fill, CELLULAR-mask fixture. The final runs
use 12 warmed samples for each parameter edit/publication workload and 100
unchanged callbacks for each idle workload.

The report separates these measurements:

| Field | Meaning |
| --- | --- |
| `prepare_ms` | RNA parameter update and explicit engine ticks until all changed channels are current. |
| `maximum_tick_ms` | Longest single engine tick within that interaction. |
| `completion_ms` | Acquire the published image textures, copy one texel from each into an owned 8-by-1 atlas, and read that atlas once. This includes any lazy image upload and GPU completion. |
| `wall_ms` | Preparation plus completion, excluding full-output validation. |

Eight texture samples share one 128-byte readback fence. The fence uses its own
storage and does not evict the engine's reused publication buffer. Engine pass,
compile, and readback counters exclude the benchmark's own GPU work, which is
reported separately. Full-output checksums, finite-value checks, and atlas
sample comparisons run outside the interaction samples at stage boundaries.
Their acquisition, readback, NumPy-view, and checksum costs are recorded under
`validation`.

Unchanged callbacks report their own CPU cost and whether they submitted work.
The harness drives callbacks directly and does not wait their returned timer
intervals. These results measure engine and publication costs, not native
viewport FPS, event-delivery latency, display presentation, or brush latency in
a complex artist scene. For cross-backend comparisons, include `wall_ms`:
OpenGL can submit GPU work asynchronously while Vulkan's CPU publication
already waits for readback inside `prepare_ms`.

## Final Vulkan optimization comparison

The final report, `.blender-test/windows-vulkan/vulkan-optimized.json`, passed
all three resolutions with the same environment and protocol as the earlier
Vulkan baseline, `.blender-test/windows-vulkan/vulkan-final.json`. Every final
output checksum is identical to that baseline at every resolution. The
checked-in [final measurement summary](validation/windows-vulkan-optimized.json)
includes full timing distributions, exact work counters, output hashes, and the
separate CPU callback measurements.

The baseline source SHA-256 is
`96f118ee46f0b453492ef024d82d65d7eb6e428cc0235269e5483ec04084d13a`;
the optimized source is
`4d2371b5e23a01989a6e6320c433639f80e6c6a97e93ace48ffbc96b45255603`.
Source stayed unchanged throughout each sweep. Median milliseconds across 12
warmed samples; wall time includes completion and excludes full-output hashing:

| Resolution | Workload | Before prepare | After prepare | Before wall | After wall |
| --- | --- | ---: | ---: | ---: | ---: |
| 512 | Roughness edit | 5.65 | 4.57 | 9.32 | 7.67 |
| 512 | Mask-strength edit | 14.94 | 9.49 | 25.92 | 16.31 |
| 512 | Forced republish | 25.42 | 12.08 | 45.17 | 21.83 |
| 1024 | Roughness edit | 11.67 | 8.85 | 21.16 | 18.30 |
| 1024 | Mask-strength edit | 44.93 | 28.65 | 81.81 | 56.27 |
| 1024 | Forced republish | 93.53 | 54.07 | 171.67 | 135.00 |
| 2048 | Roughness edit | 53.41 | 34.31 | 91.36 | 56.25 |
| 2048 | Mask-strength edit | 169.86 | 104.65 | 275.63 | 167.05 |
| 2048 | Forced republish | 385.85 | 144.16 | 607.21 | 236.91 |

The 2048 mask edit's measured wall time decreased 39.4%, and forced republishing
decreased 61.0%. The changed work is explicit: mask edits now render four units
in seven passes and read three outputs, previously five units, nine passes, and
four outputs. At 2048 that avoids one 64 MiB readback and image rewrite for the
unchanged NORMAL channel. Forced republishing now reads four outputs instead
of eight (256 MiB instead of 512 MiB), with four uniform clears, ten passes and
six rendered units. It deliberately discards publication stamps and is not an
ordinary editing workload.

All warmed samples recorded zero uniform CPU initializations; each cold start
initialized four uniform outputs. Roughness still uses one nonuniform output,
two passes and one readback before and after. Its observed improvement cannot
be attributed to uniform pruning. There was also substantial timing variance:
1024 forced republishing had a 135.00 ms median and 289.28 ms p95. Its first six
wall samples were 93–105 ms and the last six 165–289 ms, with identical work
counters and no repeated CPU initialization. Completion time rose from roughly
45–50 to 114–154 ms; the report does not establish the external cause.

At 2048, the median longest tick within a mask edit fell from 49.97 to 40.29 ms;
for forced republishing it fell from 85.67 to 39.51 ms. This is not a hard
callback limit: at 1024 the mask-edit median longest tick increased from 23.28
to 27.78 ms even while total interaction time decreased. All unchanged callbacks
still submitted zero engine passes, compiles, rendered units, and readbacks.

The intended installation uses Vulkan to exercise the completed optimization.
The earlier OpenGL results below remain useful context and were faster on this
machine; a new OpenGL performance sweep of the optimized source was not run.

## Earlier backend comparison (before uniform publication)

Both earlier benchmark sweeps passed. Measurements belong to these local reports:

- `.blender-test/windows-vulkan/opengl-final.json`
- `.blender-test/windows-vulkan/vulkan-final.json`

A checked-in [baseline measurement summary](validation/windows-gpu-performance.json)
records the exact timings, source hashes, hardware, idle work counters, and
output checksums. Both backends used the same source, SHA-256
`96f118ee46f0b453492ef024d82d65d7eb6e428cc0235269e5483ec04084d13a`.

Median milliseconds across 12 warmed samples; `wall` includes the actual-output
GPU fence and excludes full-output hashing:

| Resolution | Workload | OpenGL prepare | OpenGL wall | Vulkan prepare | Vulkan wall |
| --- | --- | ---: | ---: | ---: | ---: |
| 512 | Roughness edit | 4.15 | 5.43 | 5.65 | 9.32 |
| 512 | Mask-strength edit | 10.91 | 12.93 | 14.94 | 25.92 |
| 512 | Republish all channels | 26.49 | 29.46 | 25.42 | 45.17 |
| 1024 | Roughness edit | 5.27 | 6.99 | 11.67 | 21.16 |
| 1024 | Mask-strength edit | 9.71 | 12.44 | 44.93 | 81.81 |
| 1024 | Republish all channels | 15.39 | 16.90 | 93.53 | 171.67 |
| 2048 | Roughness edit | 6.64 | 15.77 | 53.41 | 91.36 |
| 2048 | Mask-strength edit | 9.61 | 14.87 | 169.86 | 275.63 |
| 2048 | Republish all channels | 12.04 | 16.45 | 385.85 | 607.21 |

OpenGL completed every measured interaction faster on this machine. At 2048,
the mask-strength update took 14.87 ms on OpenGL and 275.63 ms on Vulkan.
These measurements initially led to an OpenGL preference before the later
Vulkan optimization. They are historical results, not the current installation
choice. They do not predict another GPU's performance. Each resolution
represents one run; the report retains p95 and maximum values.

Across the 100 unchanged samples of each idle callback at every resolution,
both backends submitted zero engine passes, compiled zero programs, rendered
zero units, and performed zero engine texture readbacks. Median idle engine
callbacks ranged from 0.023 to 0.078 ms, and scheduler callbacks from 0.099 to
0.249 ms. This small fixture does not reproduce a large graph or dirty painted
canvas; those CPU costs are measured in the linked navigation report.

Full-output validation passed on both backends, including unchanged idle
pixels, parameter changes, stable republishing, and atlas/full-image agreement.
Cross-backend hashes differ for BASE_COLOR, ROUGHNESS, and METALLIC at every
resolution; the other five channels match. Hashes do not quantify numerical
error, so these results do not claim bitwise identity across backends. The
separate engine/CPU parity regressions establish their numerical tolerances.

## Repeat the measurements

From the repository root, run these commands sequentially, with other Blender
GPU work stopped:

```powershell
$blender = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
$blenderPython = 'C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe'
& $blenderPython scripts/benchmark_windows_viewport.py --blender $blender --backend vulkan --label windows-vulkan-optimized --sizes 512 1024 2048 --repeat 12 --compare .blender-test/windows-vulkan/vulkan-final.json --output .blender-test/windows-vulkan/vulkan-optimized-repeat.json
```

The command assumes the recorded baseline report exists. Omit `--compare` when
running only the current source; use a separate output path to preserve the
recorded report. For a backend comparison, run the same command sequentially
with `--backend opengl`, a distinct label/output, and no Vulkan `--compare`.

For before/after measurements on the same backend, use `--source-root` to select
a preserved checkout containing `src/open_painter`, and `--compare BEFORE.json`
for the subsequent run. The comparison requires protocol version 2 and reports
whether the environment matches. Source must remain unchanged during a run.
`--pixel-publish force` tests the CPU publication route on another actual
backend; it does not emulate a Vulkan driver.

Repeat the GPU regression matrix with:

```powershell
& $blenderPython scripts/run_gpu_compatibility.py --blender $blender --backend vulkan --output .blender-test/windows-vulkan/validation-final
```

The matrix distinguishes simulated API contracts from actual GPU tests. Its
default sampler cap is 16 per pass. It covers compact readback, procedural
parity, engine/CPU parity, publication budgets and flushes, painting, authoring,
filters, and material nodes. `--tests backend-contract pixel-publication`
selects the focused readback/publication checks. For equivalent native OpenGL
coverage, run it again with `--backend opengl` and a separate output directory.

## Validation and packaging caveats

The optimized uniform-publication GPU regression passed on Vulkan and OpenGL,
including exact base pixels, explicit mip levels, mask edits, proof exit/re-entry,
texture eviction, CPU/save pixels, and the cross-channel OUTPUT-anchor consumer.
All 15 optimized Vulkan suites passed, combining the initial matrix with a
focused stage-report rerun. That test previously assumed every compiled unit
executes; it now requires reporting every executed pass and verifies skipped
neutral work. The optimized OpenGL backend, engine/CPU parity, uniform
publication, and GPU-stroke suites all passed (four of four).

The optimized pure suite ran 316 tests: 310 passed, three skipped, two failed,
and one errored. All export transaction tests passed, including the new Windows
process-liveness regressions. Three unchanged platform-sensitive checks remain
outside GPU evaluation:

- `test_procedural_optimization.ProceduralOptimizationTests.test_frozen_sources_match_recorded_commit_hashes`:
  Git's CRLF checkout conversion changes raw fixture hashes; LF-normalized
  content matches the recorded source.
- `test_material_director_benchmark_v3.GalleryTests.test_archive_inventory_rejects_missing_file_and_incomplete_capture`:
  the gallery inventory records backslashes while ZIP normalizes member paths
  to forward slashes.
- `test_material_director_benchmark_runtime.BenchmarkRuntimeTests.test_access_only_private_copy_removed_and_source_unchanged`:
  the test expects POSIX mode `0600`, while Windows reports `0666`.

The optimized CPU integration suites `diagnostics_scheduling`,
`navigation_scheduling`, and `contributor_regressions` all passed. The real-window
Vulkan graph test passed area splitting, native node clicks, list selection, and
a native socket-link drag; it also verified row-guide GPU resource cleanup.
These checks verify interactions, not viewport frame rate. First-launch shader
compilation is outside the warmed engine timings above.

Logs are under `.blender-test/windows-vulkan/`: `matrix-vulkan-optimized`,
`matrix-vulkan-optimized-rerun`, `matrix-opengl-optimized`,
`pure-tests-optimized.log`, and the three CPU
`*-optimized.log` files. `graph-ui-optimized.json` and `graph-ui-optimized.png`
record the Vulkan GUI run.

Building an extension archive is separate from testing or installing it:

```powershell
& $blenderPython scripts/build_extension.py --output dist/open_painter-0.13.1-windows-vulkan-optimized.zip
& $blender --background --factory-startup --command extension validate dist/open_painter-0.13.1-windows-vulkan-optimized.zip
```

The source manifest's minimum Blender version is 5.2.0. Package validation uses
Blender's extension validator and `tests/integration/install_extension.py` in
an isolated profile before installing into the actual user profile. The latter
checks registration cycles, the material catalog, generated images, and engine
publication. The intended optimized installation selects Vulkan; this is a
local preference, not a backend forced by the extension.
Package and installation records use `package-optimized.json`,
`installation-optimized.json`, and `fresh-start-optimized.json` under
`.blender-test/windows-vulkan/`. They record the archive checksum, enabled
extension, and backend after a fresh Blender start.
