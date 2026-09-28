# Windows Vulkan: dense-mesh painting and bounded resource reuse

Round 5 accelerates repeated material-brush callbacks on dense geometry,
bounds unused GPU texture storage as each texture is returned, and avoids
rechecking the same live engine state within one evaluation. Allocation
failures now release resources acquired earlier in the failed operation.
The [performance evidence](validation/windows-vulkan-round5.json) retains
raw samples, distributions, counters, exact-pixel checks and measurement
limits. Earlier [Round 4 results](windows-vulkan-round4.md) and
[navigation/scheduler measurements](viewport-interaction-performance.md)
remain separate historical measurements.

## Completed material-brush callbacks

The spatial lookup narrows triangle-bound queries before the existing exact
eligibility calculation. Surface reach also maps only candidate triangles
back into the captured surface, avoiding a full-mesh lookup for a small dab.
It preserves triangle order, eligible masks and dirty rectangles. The lookup
belongs to the captured surface and is released with it. This benefits dense
meshes without changing composition or brush pixels.

Actual Vulkan on Windows 11, Blender 5.2.2 LTS, NVIDIA GTX 1050 / driver 560.70:
200,000 triangles, a 1024 canvas, Base Color/Roughness/Height, and 100 paired
warmed dabs per reach mode. Baseline and candidate alternate order within the
same process and use the same current GPU device implementation. The baseline
Canvas/Surface/reach code comes from the verified installed Round 4 snapshot.
All final channel pixels match exactly; all owned texture bytes are released
after cleanup. Times are milliseconds:

| Reach | Callback before | Callback after | Complete before | Complete after | Complete p95 before | Complete p95 after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DEPTH | 14.33 | 5.54 | 14.75 | 6.01 | 22.11 | 7.43 |
| SURFACE | 35.70 | 12.72 | 36.55 | 13.56 | 40.92 | 15.91 |

The callback includes `Canvas.stamp` and `Canvas.publish`. Complete time adds
a GPU completion fence. It excludes event dispatch, gesture ray casting,
final document commit, preview-graph evaluation and native viewport drawing;
these numbers do not measure whole-application input latency or viewport FPS.
The original CPU-only `Surface.eligible` fixture separately measured
14.254 to 5.105 ms for DEPTH and 37.551 to 20.024 ms for SURFACE over
25 paired callbacks at 2048, with exact masks and rectangles.
Those CPU-only results measure the initial bounds index, before the additional
sparse surface lookup included in the final Canvas timings above.

Setup and the first dab are recorded separately from warmed timings. In the
final SURFACE run, setup was 1226.15 to 1408.31 ms and the
first completed dab was 152.94 to 164.99 ms. The index adds
retained CPU storage: the SURFACE fixture used 6,437,536 bytes after its
queries versus 4,800,000 bytes for the previous surface. The evidence retains
cold results and every slow warmed sample, rather than attributing all pauses
to the optimized query.

## Resource bounds and failure recovery

Returned textures now obey the global free-pool budget and per-format spare
limit immediately. Previously arbitrary scratch sizes could accumulate until
a later explicit trim. The default free-pool budget remains 512 MiB; active
textures are accounted separately. Duplicate returns are ignored safely and
empty buckets are removed. This bounds reusable engine storage, not total
driver VRAM or all resources in a large document.

The actual Vulkan soak passed six resolution/structure cycles across
512, 1024 and 2048 in half and precise modes. Four full CPU outputs and four
full GPU outputs matched their respective fresh-engine references in each
cycle. The largest recorded free pool was 404 MiB. An additional 48 irregular
scratch returns each respected an intentionally smaller 16 MiB test budget
without changing a live sentinel. Final leases and free-pool bytes returned
to zero.

A separate CPU accounting stress test returned four fake textures for each
of 256 distinct extents. The former release behavior would retain
6,789,881,856 bytes before trimming; the new path peaked at 33,550,880 bytes
under a 32 MiB test budget. This test allocated no GPU memory and is not a
measured VRAM reduction. Failed multi-texture allocations are covered by six
focused regressions that preserve prior leases and verify successful retry
for engine targets, UV/surface filters and Canvas resources.

## Evaluation-local state checks

The live-state cache lasts only for a synchronous evaluation and is cleared
on exit, including exceptions. It does not persist RNA/image bindings between
edits. A same-process off/on comparison used 24 fill layers with a cellular
coat mask at 512, 30 pairs for each edit, and 100 unchanged callbacks. Each
alternative received the same completed authored reset before timing.
Prepare time covers engine work; wall time also includes image acquisition,
sampling all eight published outputs and waiting for GPU completion.

| Edit | Prepare off | Prepare on | Wall off | Wall on | Wall p95 off | Wall p95 on |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Roughness | 9.48 | 9.32 | 12.19 | 12.24 | 14.00 | 13.94 |
| Mask strength | 14.76 | 14.48 | 21.98 | 21.69 | 27.37 | 25.07 |
| Republish | 23.03 | 21.81 | 33.79 | 32.86 | 61.00 | 47.57 |

Timing results are mixed; this is not a claim of broad material-edit speedup.
The deterministic reduction is signature checks: 76 to 73 for roughness,
82 to 73 for the mask, and 148 or 221 to 73 for forced republication. The latter
range includes extra callbacks after the publication time budget expires.
Every pair preserved rendered-unit counts, GPU passes, readback counts/bytes and
publication counts. Full hashes of all eight channels match after each stage.
The record includes adjacent-pair deltas, p95/maximum and individual samples.
The engine benchmark uses nearest-rank p95; the spatial probes use NumPy's
interpolated percentile, so tail values from these different fixtures should
not be treated as one shared distribution.

## Rejected publication experiment

Packing half values before readback reduced transfer size but added CPU
unpacking. At 2048, complete Base Color publication increased from
59.66 to 96.10 ms, Normal from 71.43 to 106.53 ms, and padded Roughness from
62.40 to 98.56 ms. Directly fusing that packing also changed half-rounded
pixels. Neither candidate is retained. The rejected probe's ten alternating
warmed samples per case and exact mismatch evidence remain in the record.

## Reproduction and validation

The installed Round 4 ZIP was extracted read-only to
`.blender-test/windows-vulkan/round5-baseline/src/open_painter`. All 311 Python
files matched the installed add-on byte for byte. Its source SHA256 is
`073cdc8e5a9263dd266ec70bfb01147644ef025fe62436113824733a5d81be89`; the snapshot manifest and archive digest are
included in the performance record. Final GPU measurements use source SHA256
`9e93f9b9c3f25d7994822b73bfcd258f9e3cfa95ae3d639221f5ca2b18baf6df`. Both final reports verify this source, actual Vulkan and explicit
isolated CONFIG/SCRIPTS/EXTENSIONS/DATAFILES paths. Earlier measurements before
the allocation-failure cleanup remain separate raw observations.

Run the maintained deep-stack comparison with Blender's bundled Python:

```powershell
$blender = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
$python = 'C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe'
& $python scripts/benchmark_windows_viewport.py --blender $blender --backend vulkan --fixture masked --layers 24 --sizes 512 --repeat 30 --idle-repeat 100 --paired-flag CACHE_EVALUATION_STATES --require-same-gpu-work --label round5-repeat --output .blender-test/windows-vulkan/state-cache-round5-repeat.json
```

The default two-fill fixture remains unchanged. The paired harness rejects
unequal GPU work or nondecreasing signature checks for this CPU-only change;
fixture/protocol mismatches cannot produce a reported speedup. Full output
checksums run outside the timed loop. Do not run other GPU or CPU benchmarks
concurrently.

The three prior Windows-only pure-suite failures are corrected: frozen source
hash checks normalize checkout line endings without changing recorded sources
or hashes; archive inventory paths use ZIP-standard separators; and private
temporary-file tests inspect Windows ACLs while retaining POSIX mode checks.
All 37 affected tests passed, as did both paired-harness regressions. The state
cache and memory soak are included in the maintained GPU runners; texture-pool
and allocation-failure cases are included in the pure runner.

The [release validation record](validation/windows-vulkan-round5-release.json)
records the complete compatibility, CPU/pure, native GUI, package, installation
and fresh-start results for `dist/open_painter-0.13.1-windows-vulkan-round5.zip`.
Its actual results, including any failure history, are authoritative for the
release; the performance checks above do not stand in for those checks.
