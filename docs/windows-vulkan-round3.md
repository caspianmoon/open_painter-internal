# Further Windows Vulkan optimization

This follow-up reduces the measured 2048-pixel plain-fill interaction costs:
roughness edits fell from 79.37 to 47.84 ms, opacity edits from 283.33 to
160.22 ms, and forced republishing from 303.68 to 128.26 ms. The masked fixture
has mixed results, including regressions; a general Vulkan speedup is not
established. Open Graph and brush preparation also perform less CPU work.
The earlier Windows measurements remain
in [the previous report](windows-gpu-performance.md); this page records the
changes after that installed version.

## What changed

- **Final-pass fusion, up to 1024 pixels:** eligible non-normal CHANNEL shaders write clamped,
  padded RGBA directly into the publication texture. Scalar expansion and
  intermediate precision are preserved. OUTPUT-anchor consumers, retained
  intermediates, normals, unsupported coordinate layouts and sampler limits
  retain the ordinary path. At 2048 and above the original path is retained:
  paired measurements found that fewer passes did not reliably improve elapsed
  time. The real GPU regression compares exact pixels at
  both precisions, with actual UV padding, and checks failure cleanup.
- **Constant fill stacks:** a conservative proof admits plain root fills with
  NORMAL blending and no spatial sources, masks, effects, anchors or caches.
  Their actual GPU programs and final publication shader execute at 1×1, with
  the same layer-by-layer precision and normal normalization. Only 16 bytes
  are read back per changed constant channel. The resulting value fills its
  canonical CPU image with contiguous copies, then Blender uploads the image
  and mips when sampled. Unchanged forced publication can clear every GPU mip
  directly. CPU image writes still occur when the value changes; texture
  eviction, saving and rendering retain current pixels. The child evaluator owns
  separate textures and its own small readback
  buffer. Data-tagged outputs qualify; Base Color keeps the ordinary path.
- **CPU interaction work:** repeated graph-signature serialization is memoized
  without skipping live RNA reads. Brush surfaces retain triangle bounds and
  gather corners only for candidate triangles; DEPTH strokes reuse their
  unchanged eligibility texture during a gesture. Geometry replacement and
  canvas cleanup release the corresponding state.

## GPU measurements

The test machine is Windows 11 build 22631, Blender 5.2.2 LTS
(`d13f752e3b9c`), NVIDIA GTX 1050 with 4 GiB VRAM, driver 560.70. The benchmark
requires the actual Vulkan backend and uses factory startup. It measures
512, 1024 and 2048 textures, 12 warmed interactions per
workload, and 100 unchanged callbacks per idle workload.

Two separate fixtures distinguish the implemented paths:

- `two_fills_cellular_mask_v1`: roughness edits, CELLULAR mask-strength edits,
  and forced republishing of all channels. Nonuniform finals exercise fusion.
- `two_fills_plain_v1`: roughness edits, layer-opacity edits, and forced
  republishing, with no mask. Eligible data channels exercise one-texel
  evaluation. The opacity workload changes the authored layer, not a hidden
  benchmark approximation.

Protocol 2 separates `prepare_ms` (the edit and all required engine ticks),
`maximum_tick_ms` (the longest callback), and `completion_ms` (sampling all
published images into an 8×1 atlas and reading it once). `wall_ms` includes
preparation and GPU completion. Full-output checksums and finite-value
validation run outside those interaction timings. These are engine interaction
costs, not native viewport FPS, display latency, or a complex artist project's
complete brush-stroke latency. Forced republishing deliberately discards all
publication stamps and is a distinct workload from ordinary edits.

Both final sweeps passed, with identical final output hashes for all eight
channels at every size in both fixtures. The fresh baseline source hash starts
`4d2371b5`; the final source hash starts `6abfa340`. Source hashes remained
unchanged during each sweep. Full hashes, timing samples, median/p95/maximum
distributions, work counters and paired probes are preserved in
[the performance record](validation/windows-vulkan-round3.json). The raw reports
are `vulkan-round3[-plain]-baseline.json` and
`vulkan-round3[-plain]-final.json` under `.blender-test/windows-vulkan/`.

The recorded runs set `BLENDER_USER_RESOURCES` but did not verify Blender's
resolved configuration directory, so user-profile isolation was not proven.
A later GUI profile-seeding operation exposed fallback to the real profile;
the verified preferences backup was restored. The launchers now create
explicit config/scripts/extensions/datafiles directories, replace inherited
resource overrides, and check Blender's resolved CONFIG path before test work.
This harness correction does not change the measured add-on source or timings;
the historical results retain this isolation limitation.

All values below are warmed medians in milliseconds. Preparation includes all
engine callbacks needed to finish an edit; wall time additionally waits for the
published GPU images.

| Masked fixture | Size | Prepare before | Prepare after | Wall before | Wall after |
| --- | ---: | ---: | ---: | ---: | ---: |
| Roughness | 512 | 6.17 | 4.86 | 10.22 | 7.84 |
| Mask strength | 512 | 9.25 | 9.02 | 16.03 | 15.53 |
| Forced republish | 512 | 12.70 | 30.04 | 22.96 | 60.42 |
| Roughness | 1024 | 10.33 | 20.47 | 19.87 | 41.22 |
| Mask strength | 1024 | 30.37 | 36.17 | 57.75 | 69.33 |
| Forced republish | 1024 | 50.69 | 45.78 | 95.09 | 90.64 |
| Roughness | 2048 | 31.21 | 34.51 | 56.17 | 57.13 |
| Mask strength | 2048 | 106.36 | 120.95 | 178.55 | 239.83 |
| Forced republish | 2048 | 136.49 | 160.37 | 234.89 | 271.45 |

| Plain fixture | Size | Prepare before | Prepare after | Wall before | Wall after |
| --- | ---: | ---: | ---: | ---: | ---: |
| Roughness | 512 | 5.32 | 3.50 | 8.26 | 6.86 |
| Layer opacity | 512 | 7.96 | 7.52 | 14.71 | 15.19 |
| Forced republish | 512 | 10.49 | 7.74 | 19.22 | 13.25 |
| Roughness | 1024 | 15.57 | 8.90 | 28.11 | 18.25 |
| Layer opacity | 1024 | 22.91 | 20.35 | 46.02 | 48.18 |
| Forced republish | 1024 | 36.80 | 31.64 | 73.87 | 55.38 |
| Roughness | 2048 | 44.14 | 23.36 | 79.37 | 47.84 |
| Layer opacity | 2048 | 164.64 | 83.86 | 283.33 | 160.22 |
| Forced republish | 2048 | 181.57 | 77.77 | 303.68 | 128.26 |

For plain 2048 roughness edits, engine readback falls from 64 MiB to 16 bytes;
the canonical image still receives a 64 MiB CPU write when its value changes.
The longest callback median is 22.93 ms, compared with 47.84 ms for the entire
interaction. Masked 2048 work retains full-size readbacks: a mask edit reads
192 MiB, with a 47.49 ms longest-callback median and 239.83 ms wall median.
Its wall p95 is 681.19 ms. The sampled masked regressions remain unresolved;
lower pass counts alone are not evidence of improved responsiveness.

Separate same-process probes alternated each optimization off and on for 12
pairs, preserving pixels. At 2048, constant evaluation reduced split wall
medians from 80.42 to 61.61 ms for roughness, 215.82 to 187.10 ms for opacity,
and 229.21 to 160.06 ms for forced republishing; it was faster in 12, 11 and
11 of the corresponding 12 adjacent pairs. Fusion at 1024 reduced forced
republish medians from 95.41 to 90.82 ms and won 11 of 12 pairs, but gave no
mask-edit improvement. Its 2048 mask and republish results did not justify
enabling it there, so the final implementation caps fusion at 1024. These
probes support the specific paths retained; they do not erase the mixed final
masked results. Earlier uncapped/eager-upload sweeps are marked superseded in
the performance record.

## CPU measurements

The same machine's separate CPU fixtures show:

| Work | Before | After |
| --- | ---: | ---: |
| Idle scheduler, 100 layers / 400 cached channels | 35.701 ms | 0.103 ms |
| Complete Open Graph sync, 100 layers / 200 masks | 7.570 ms | 5.482 ms |
| DEPTH brush preparation, 200,000 triangles | 100.791 ms | 14.265 ms |
| SURFACE brush preparation, 200,000 triangles | 123.284 ms | 36.670 ms |

Scheduler timings use 40 warmed samples; graph sync also uses 40 without
cProfile; brush preparation uses 25 warmed dabs. These fixtures do not measure
GPU composition or full stroke latency. The bounds consume 4,800,000 CPU bytes
for that float32 mesh and take 91.7 ms to build once. The previous navigation
scan deferral, scheduler changes and row-guide reuse were already present in
the GPU baseline and remain active. Methodology, exact scope and
reproduction commands are in
[Viewport interaction costs on Windows](viewport-interaction-performance.md).

## Validation and reproduction

All 17 Vulkan compatibility suites passed: one simulated backend check and 16
actual GPU or forced-publication checks. All six OpenGL suites passed after a
targeted constant-publication test correction: its initial comparison mixed
the CPU publication and native OpenGL paths; the corrected check preserves
the native path. The original failure and successful rerun remain in the
release evidence. All six CPU regression scripts passed.

The publication regressions cover arbitrary authored values, both precisions,
normal normalization, half-texture clear rounding, proof exit/re-entry, actual
image eviction, save pixels, OUTPUT anchors, and independent engine ownership.
Native Open Graph UI checks and cleanup passed, with the screenshot reviewed.
Native brush painting, operator execution and changed-pixel assertions also
passed, followed by a reviewed unobscured viewport screenshot. These checks
exercise real GUI operations; the timing tables above
remain engine measurements rather than viewport FPS.

The pure suite ran 327 tests: 321 passed, three skipped, two failed and one
errored. The remaining three checks are the same pre-existing Windows CRLF
fixture hash, ZIP path separator, and POSIX permission expectations documented
in the
[previous validation report](windows-gpu-performance.md#validation-and-packaging-caveats).

Run each benchmark sequentially with other Blender GPU work stopped:

```powershell
$blender = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
$python = 'C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe'
& $python scripts/benchmark_windows_viewport.py --blender $blender --backend vulkan --fixture masked --label round3-repeat --sizes 512 1024 2048 --repeat 12 --compare .blender-test/windows-vulkan/vulkan-round3-baseline.json --output .blender-test/windows-vulkan/vulkan-round3-repeat.json
& $python scripts/benchmark_windows_viewport.py --blender $blender --backend vulkan --fixture plain --label round3-plain-repeat --sizes 512 1024 2048 --repeat 12 --compare .blender-test/windows-vulkan/vulkan-round3-plain-baseline.json --output .blender-test/windows-vulkan/vulkan-round3-plain-repeat.json
& $python scripts/run_gpu_compatibility.py --blender $blender --backend vulkan --tests fused-publication constant-publication --output .blender-test/windows-vulkan/round3-publication-repeat
```

Omit `--compare` if the local baseline reports are unavailable. The complete GPU
matrix includes both publication regressions by default. `FUSE_PIXEL_PUBLISH`
and `CONSTANT_OUTPUTS` are test opt-outs; individual-pass profiling uses the
ordinary paths for consistent unit timing.

The candidate archive is `dist/open_painter-0.13.1-windows-vulkan-round3.zip`:

```powershell
& $python scripts/build_extension.py --output dist/open_painter-0.13.1-windows-vulkan-round3.zip
& $blender --background --factory-startup --command extension validate dist/open_painter-0.13.1-windows-vulkan-round3.zip
```

Package validation, isolated installation and the user-profile installation
are recorded separately in `docs/validation/windows-vulkan-round3-release.json`.
The intended user backend remains Vulkan.
