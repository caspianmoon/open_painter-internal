# Masked-material Vulkan measurement follow-up

Round 4 reduces Open Graph draw-callback work, engine image-node queries,
CPU expansion of existing native R/RG readbacks, and GPU-source acquisition
for bounded CPU brush previews. An experimental scalar
publication path was rejected because every tested 2048 case became slower.
The final masked-material sweep passed exact pixel comparisons, with mixed
timing results including a slower 2048 roughness run. Component improvements
below are not a claim that all masked interactions or viewport FPS improved.
The baseline is the installed [Round 3 version](windows-vulkan-round3.md).
The [performance record](validation/windows-vulkan-round4.json) preserves the
fresh baseline samples, CPU evidence and both retained/rejected GPU probes.
Component probes that only saved medians are explicitly identified there.

## Retained CPU and readback changes

Fill-node drawing now indexes a layer's channels once per callback, preserving
the first matching channel and reading live RNA values. It avoids repeated
channel-collection scans across the controls without keeping those RNA
references across edits or undo. Image-node membership is cached inside each
compiled node collection and invalidated on insertion/removal; image bindings
and their current metadata remain live queries.

| CPU fixture | Before median | After median | Measurement |
| --- | ---: | ---: | --- |
| Draw 301 Open Graph nodes: 100 fills, 200 masks | 22.844 ms | 12.610 ms | 50 alternating pairs |
| Query all program signatures: 50 layers, 450 units, 6192 nodes | 2.778 ms | 2.092 ms | 40 samples per alternative |

The graph comparison uses the same RNA, owner caches and recording layout for
both callbacks. Its older baseline callback has the same syntax tree as the
installed Round 3 callback. Native layout drawing and GPU rendering are
excluded. The signature fixture measures CPU queries, not GPU composition.
Earlier scheduler and navigation measurements remain in
[the interaction report](viewport-interaction-performance.md).

Existing native one/two-component texture readbacks still transfer their
native component count, then return the same RGBA CPU buffer. The retained
change fills that RGBA buffer in bounded tiles, avoiding several strided
passes across the entire large destination. Actual Vulkan readback medians:

| Texture format | 1024 before | 1024 after | 2048 before | 2048 after |
| --- | ---: | ---: | ---: | ---: |
| R16F | 20.17 ms | 5.58 ms | 79.04 ms | 21.88 ms |
| R32F | 21.10 ms | 5.95 ms | 81.24 ms | 23.31 ms |
| RG16F | 25.14 ms | 6.36 ms | 98.12 ms | 30.67 ms |
| RG32F | 24.47 ms | 7.04 ms | 111.10 ms | 36.99 ms |

The probe alternated original/tiled order, discarded the first two pairs, and
retained eight timed samples per alternative. Exact pixel and GPU-state checks
passed. These are complete native readback operations including CPU expansion;
ordinary RGBA image publication remains a separate path.

## Bounded CPU brush preview acquisition

When a brush gesture needs a CPU preview from a GPU resource, it now samples
the required texels into a smaller texture before downloading them. Previously
it downloaded the full 2048/4096 source, copied its RGBA values, remapped scalar
channels, and discarded most pixels to make the 1024 preview. The new path
uses exact point samples in the source format, preserving the prior pixels.
Direct GPU painting and previews that start from a CPU image keep their
existing paths.

| Source to 1024 preview | Previous acquisition | Sampled acquisition | Native transfer before / after |
| --- | ---: | ---: | ---: |
| 2048 RG32F | 118.43 ms | 25.04 ms | 32 / 8 MiB |
| 2048 RGBA16F | 52.66 ms | 15.03 ms | 64 / 16 MiB |
| 4096 RG32F | 441.12 ms | 42.56 ms | 128 / 8 MiB |
| 4096 RGBA16F | 205.63 ms | 25.13 ms | 256 / 16 MiB |

These are eight warmed samples per alternative with alternating order. Both
alternatives already use the tiled R/RG expansion described above, so this
is an incremental improvement, not another direct comparison with Round 3.
Timings include GPU sampling where needed, completed readback, CPU copying
and channel remapping. They exclude the remaining preview-image setup and
the full stroke. Exact-pixel regressions cover all six R/RG/RGBA half/full
formats, odd reduction factors, dimensions, GPU state, borrowed storage and
failure cleanup/retry. The raw samples and readback counters are preserved
in the performance record.

## Rejected scalar publication experiment

A separate experiment published scalar channels into compact GPU storage,
then expanded their values on the CPU before writing Blender's RGBA image.
It preserved pixels, but CPU expansion outweighed the smaller transfer at
2048. All six measured 2048 configurations became slower, so the publication
change and its proposed runtime flag were not included.

| 2048 publication case | Original wall median | Experiment wall median |
| --- | ---: | ---: |
| Roughness, half intermediates, no padding | 55.60 ms | 58.51 ms |
| Roughness, half intermediates, padding | 54.64 ms | 61.25 ms |
| Roughness, precise intermediates, no padding | 57.94 ms | 61.76 ms |
| Roughness, precise intermediates, padding | 62.06 ms | 66.69 ms |
| Height, no padding | 61.16 ms | 64.44 ms |
| Height, padding | 60.98 ms | 63.24 ms |

Publication wall time includes sampling the resulting Blender image and
waiting for GPU completion. The experiment's mixed 1024 results and detailed
component timings are retained with the rejected evidence, rather than used
to advertise a smaller transfer as a general speedup.

## Final masked-material comparison

The installed archive `dist/open_painter-0.13.1-windows-vulkan-round3.zip` was
extracted to `.blender-test/windows-vulkan/round4-baseline/src/open_painter`.
All 310 Python files matched the installed add-on byte for byte before
extraction. The source files are read-only. The snapshot manifest records:

- Source SHA256: `6abfa340c732a075f12068519b6ee47096dbbd9923f6b40ef9c1eb928c90b71c`.
- Archive SHA256: `ec3f234c817868478cc51210788aa74a9c1feae5c0f84ffcab8bc427e2767b2c`.

The fresh masked benchmark passed at 512, 1024 and 2048, with 12 warmed samples
per edit and 100 unchanged callbacks. All final channel hashes match the
previous Round 3 results. Both baseline and final runs explicitly set all four user resource
directories and verified Blender's resolved CONFIG path before loading the
add-on. It used factory startup and actual Vulkan on the same Windows 11,
Blender 5.2.2 LTS, GTX 1050 / NVIDIA 560.70 machine. The full report is
`.blender-test/windows-vulkan/vulkan-round4-baseline.json`; the comparison is
`vulkan-round4-final.json` in the same directory. The final source SHA256 is
`073cdc8e5a9263dd266ec70bfb01147644ef025fe62436113824733a5d81be89`.
Source hashes remained stable during each run. Backend, environment and
protocol matched; all 24 final channel hashes are identical to the baseline.

Warmed medians, milliseconds:

| Size | Edit | Prepare before | Prepare after | Wall before | Wall after |
| ---: | --- | ---: | ---: | ---: | ---: |
| 512 | Roughness | 4.40 | 4.91 | 7.07 | 7.77 |
| 512 | Mask strength | 9.33 | 8.62 | 17.37 | 15.31 |
| 512 | Forced republish | 10.28 | 10.51 | 19.13 | 20.09 |
| 1024 | Roughness | 26.91 | 9.83 | 57.18 | 18.81 |
| 1024 | Mask strength | 35.34 | 25.33 | 67.85 | 50.58 |
| 1024 | Forced republish | 43.61 | 36.13 | 87.86 | 73.05 |
| 2048 | Roughness | 33.41 | 54.66 | 57.15 | 100.55 |
| 2048 | Mask strength | 107.54 | 107.87 | 180.88 | 180.71 |
| 2048 | Forced republish | 154.98 | 139.19 | 265.29 | 229.82 |

The final 2048 roughness wall p95 is 183.96 ms; the mask-edit p95 is
301.10 ms. Preparation, longest callback, GPU completion and wall-time samples,
work counters and all summary distributions remain in the performance record.
This two-fill masked fixture does not exercise the new bounded CPU-preview
acquisition. Large whole-material differences are not attributed to the
targeted component optimizations.

These timings show substantial variability between runs of identical add-on
source. For example, the previous 512 forced-republish median was 60.42 ms,
while its fresh median is 19.13 ms. This difference is not attributed to a
code optimization. GPU clocks and driver memory-management behavior were not
recorded, so they cannot be assigned as the cause.

## Comparing a candidate fairly

Normal sweeps retain protocol 2. The new `--paired-flag` mode uses protocol 3
and requires an actual boolean in the engine runtime. It fails if the flag is
missing, rather than silently benchmarking an unchanged path. Each pair uses
the same authored transition with the flag off and on. Order alternates
between pairs. Completed, untimed target-then-initial edits make the immediately
preceding reset a real edit for both alternatives. Forced republishing changes
no authoring values and does not use those resets.

Every timed interaction samples all eight published images into a small atlas
and reads it once. The read waits for preceding draws and any lazy image upload
needed by those samples. The next timing therefore starts after that completion
point. Paired runs additionally separate image acquisition, atlas draw submission
and fence-read wall time. These components locate where the CPU waits; they
are not GPU timestamp measurements and do not identify a driver-internal cause.

The two variants must produce identical atlas samples for every pair. After
all timed pairs in a stage, full outputs are read and compared for both
variants at the same authored state. Full checksums are outside the timing loop
to avoid perturbing one alternative's following sample. Reports retain raw
off/on samples, work counters, median/p95/maximum distributions, adjacent-pair
deltas and the number of pairs in which the candidate is faster. Paired reports
cannot be passed to protocol 2's `--compare`.

Forced republishing clears all engine publication stamps deliberately. In the
2048 masked baseline it reads back 256 MiB across four nonuniform channels,
writes those canonical CPU image buffers, and refreshes four uniform GPU
outputs. The CPU writes invalidate the corresponding image textures and mips,
whose subsequent use is included in completion time. This is real work imposed
by the forced-publication fixture; it is not an unchanged viewport callback.
Ordinary mask edits and roughness edits remain separate workloads. The atlas
fence prevents an unbounded queue of unfinished uploads between samples, but
does not prevent driver allocation variability or establish native viewport FPS.

Run sequentially when the GPU is otherwise idle:

```powershell
$blender = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
$python = 'C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe'
& $python scripts/benchmark_windows_viewport.py --blender $blender --backend vulkan --source-root .blender-test/windows-vulkan/round4-baseline --fixture masked --sizes 512 1024 2048 --repeat 12 --idle-repeat 100 --label round4-baseline-repeat --output .blender-test/windows-vulkan/vulkan-round4-baseline-repeat.json
& $python scripts/benchmark_windows_viewport.py --blender $blender --backend vulkan --fixture masked --sizes 512 1024 2048 --repeat 12 --idle-repeat 100 --label round4-final-repeat --compare .blender-test/windows-vulkan/vulkan-round4-baseline-repeat.json --output .blender-test/windows-vulkan/vulkan-round4-final-repeat.json
```

The generic `--paired-flag RUNTIME_BOOLEAN` option remains available for future
experiments with an existing engine boolean. No compact-publication flag was
added to production.

The paired scheduler has one separate pure regression covering matched input
transitions, alternating order, full-output mismatch rejection, absent-flag
rejection and restoration of the original runtime setting after failure.

## Validation

All 19 Vulkan compatibility suites passed: one simulated contract check and
18 actual GPU or forced-publication checks. These include native compact
readback and sampled preview readback, with a real mixed GPU-resource/CPU-image
preview fallback and restoration check.

Native Open Graph and painting GUI workflows passed. Both screenshots were
reviewed, and the checks left the actual user's preferences unchanged; the
local record is `.blender-test/windows-vulkan/gui-round4.json`.

Complete Vulkan/OpenGL compatibility, CPU and pure-test outcomes, package
validation, installation and fresh-start results are recorded separately in
[the release verification record](validation/windows-vulkan-round4-release.json).
That record preserves failures and limitations alongside successful checks.
