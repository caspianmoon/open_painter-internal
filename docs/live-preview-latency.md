# Live preview latency — 0.2.3

This incremental change accelerates the existing Blender add-on. It keeps the
native UI, 14-node display material on the fixture, all material recipes, full
float32 authoring, and version-1/version-2 `.openpaintmaterial` readers. Save,
render and export still finish the latest complete edits through the CPU
reference. Research and verification ran on September 13–14, 2026 local time.

The implementation follows research into Blender's draw and GPU APIs, EEVEE
invalidation and Metal arithmetic/memory traffic. The [research record](live-preview-latency-research.md)
links the primary sources and explains both adopted and rejected approaches.

## What changed

- Eight supported scalar primitives and six mesh-map generator families can
  write cached R32F textures directly on Metal. Small float32 coordinate tables
  preserve CPU octave rounding. Masks sample actual baked maps with the CPU's
  UV-center and bilinear rules. This avoids full CPU raster generation/upload
  for eligible inputs; it does not generate a material-node graph.
- GPU inputs share content keys. A change to mask strength reuses its noise and
  baked maps; changing a shared procedural scale generates one new scalar field
  for all consuming channels. Unsupported parameters, filters, projections and
  other GPU backends retain the CPU path.
- Completed first-root folders have a separate 256 MiB LRU cache. Isolated
  folders retain premultiplied RGB and coverage; passthrough folders evaluate
  against the channel default. Their own mask/opacity can reuse their children.
  Child/group-mode edits, owner deletion, explicit invalidation and teardown
  invalidate those results. Unit unmasked passthrough folders skip an
  unnecessary copy/blend. Known identity fills avoid unchanged channel work.
- CPU cellular sources compute only requested outputs while retaining the full
  5×5 search and strict tie ordering. Generators stop computing unused breakup
  noise. Frozen pre-change implementations test exact CPU array equality.

## Fresh before/after measurement

Baseline: `df32483a158fb3852564654c6d10f5f2ff7673d4`, archived without modifying
the shared checkout. Final source SHA-256:
`9bfcbce25d69ba51d1c62341385c2507fa13ad3ab95057d75c204f92f4edccdb`.
The source digest hashes sorted repository-relative Python paths followed by
file bytes under `src/open_painter`. Both runs checked it again at completion.
No historical timing was reused as a fresh baseline.

The same committed **Worn Painted Metal** fixture ran in separate fresh Blender
processes: assigned default cube, six layer records, eight **2048² float32**
channels, synthetic 32² AO/curvature inputs. The normal EEVEE material viewport
was **2477×1364**, with factory 16 viewport/64 render samples, temporal
reprojection, `AUTO` preview pixel size and `forest.exr`. No texture resolution,
precision or sampling setting was lowered. Machine: Apple M3 Pro, 18 GiB,
macOS, Blender **5.2.1 LTS `9e2066aef7ef`**, Metal, NumPy 2.3.4.

The main comparison retains production `cache.tick`; it includes timer waits,
cooperative yields, dependency processing and the first completed native frame
that began after the requested revision was current. A 3×3 readback from the
native color framebuffer fences completion. These are not submission-only
numbers. Raw [before](performance/latency-native-before.json) and
[after](performance/latency-native-after.json) use the same harness digest.
Each warm row is the median of five edits; cold is one sample per process.

| Work | Before | After |
| --- | ---: | ---: |
| Cold live evaluation → first current native frame | 24.237 s | 0.808 s |
| No-edit redraw control | 7.33 ms | 7.76 ms |
| Roughness value | 25.00 ms | 28.66 ms |
| Folder opacity | 90.99 ms | 42.02 ms |
| Coat generator-mask strength | 4,629.66 ms | 76.45 ms |
| Shared Orange Peel scale in color/roughness/height | 2,860.25 ms | 59.64 ms |

The largest improvements are **60.6× for mask strength**, **48.0× for procedural
scale**, and **30.0× for cold live evaluation**. Folder opacity improved 2.17×.
**Roughness did not improve**: its median increased by 3.66 ms. Changed child
contents replace the cached group, currently allocating a new 64 MiB result on
this workload; avoiding that allocation is a possible follow-up, not a shipped
optimization. These samples do not establish statistical significance or a
universal frame-rate guarantee.

Fixture creation is separate: 0.368 s before and 0.362 s after. Cold includes
new add-on GPU programs/resources and first publication, but begins after
fixture setup. The framebuffer was nonempty in both cold samples; the harness
does not certify that every asynchronous EEVEE material job or later temporal
sample has finished. Warm phases follow explicit viewport warmup. OS/driver
shader caches were not purged. Application startup, physical display
presentation and physical tablet-to-screen latency are outside this test.

A second measurement consumes the live job explicitly, excluding production
timer yields: [before](performance/latency-native-explicit-before.json) versus
[after](performance/latency-native-explicit-after.json). Cold first-frame time
was 16.454 s → 0.562 s, roughness 26.29 → 27.59 ms, opacity 86.09 → 36.33 ms.
The harness gained optional extra-workload arguments between these runs; the
unselected explicit measurement path did not change. The scheduled pair above
uses one identical final harness and is the primary comparison.

Earlier `GPUOffScreen.draw_view3d` reports complete **all viewport samples**.
They remain valid for that different workload and are preserved in the
[initial backend report](live-procedural-preview.md). They must not be described
as first-native-frame latency or directly compared with this table.

## Counters, memory and correctness

In the final scheduled run:

- Cold work generated nine unique scalar fields and four mask stacks on GPU.
  Managed uploads totaled **2,981,896 bytes**, principally small coordinate
  tables and baked maps. Final-output pixel writes and packing were zero.
- Folder opacity reused four cached groups and four unchanged channels. GPU
  passes fell from 226 to 100, including mips. No scalar generation, masking,
  source uploads, image reads/writes, packing or geometry work occurred.
- Mask strength regenerated one GPU mask, reused its fields/maps and updated
  four affected channels. Procedural scale generated one shared field and
  updated three channels; its only upload was a 327,680-byte coordinate table.
- Observed leaf-cache peak reached **256 MiB**, independently of the **256 MiB**
  folder cache. Scratch peak was 192 MiB, mip scratch approximately 21.33 MiB.
  The conservative additional-resource estimate was **2,231,369,728 bytes**.
- Process peak RSS was **1,609,678,848 bytes before** and **1,719,697,408 after**.
  RSS includes Blender and benchmark work; it is not a complete accounting of
  driver/GPU allocations and is not interchangeable with cache counters.

The 3×3 frame fence requests 144 bytes of output. That is not a measurement of
GPU driver traffic; synchronization and attachment resolve costs are included.
The mask diagnostic test fences a dependent 1×1 copy; the scalar diagnostic
test reads one pixel directly from its output attachment. Both keep the
subsequent full-image validation readback outside that completion interval.
Production evaluation performs neither diagnostic readback.

The [full 2K eight-channel comparison](performance/latency-parity-2k.json)
passed against the CPU evaluator; maximum absolute error was **2.861023e-6**.
Individual regressions retain declared float32 tolerances rather than claiming
bitwise GPU equality. Frozen CPU optimization tests require array equality.

- [GPU composition](performance/latency-gpu-math.json): 183 checks, max error
  2.563e-6; all blend enums, coverage, groups, normals, mips and GPU state.
- [Folder checkpoints](performance/latency-gpu-checkpoints.json): 91 checks,
  exactly equal cached/uncached pixels; isolated coverage, mode/child changes,
  masks, eviction, bounds, failure and owner/lifecycle cleanup.
- [GPU procedurals](performance/latency-gpu-procedurals.json): 38 configurations,
  max error 6.259e-6, including full 2K fields, varied seeds/scales and exact
  cellular winner identity.
- [GPU masks](performance/latency-gpu-masks.json): 11 full-2K parity cases,
  max error 5.305e-6; 17 unsupported-domain/map preflight checks with no input
  callbacks/target allocations. Unloaded optional maps retain CPU failure
  semantics rather than being silently ignored.
- [Live workflow](performance/latency-live-workflow.json): painting, partial-job
  cancellation, failed-job recovery, scene ownership, authoritative save/export
  and teardown. [Native controls](performance/latency-native-ui.json) and
  [texture controls](performance/latency-texture-ui.json) remain intact.

The [validation inventory](performance/latency-validation.json) records exact
commands, test outcomes, changed files and source provenance, including the
full background and independent CPU suites and isolated package installation.
An early GPU-suite process received SIGTERM after its scalar checks; it was
not counted as acceptance. The complete final suite subsequently exited cleanly.

## Reproduction

Run sequentially from the repository root, using the installed Blender path:

```sh
python3 scripts/run_tests.py
python3 scripts/run_tests.py --gui-only
python3 scripts/run_tests.py --gpu-only
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 scripts/run_pure_tests.py
OP_LIVE_BENCHMARK=1 OP_LIVE_REPORT=/private/tmp/op-latency-parity-final-2k.json BLENDER_USER_RESOURCES=/private/tmp/op-latency-parity-final-user /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python tests/integration/live_preview.py -- -ApplePersistenceIgnoreState YES
BLENDER_USER_RESOURCES=/private/tmp/op-latency-native-extended-after-user /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python tests/performance/live_native_frame.py -- --report /private/tmp/op-latency-native-extended-after.json --scheduled --procedural-edits -ApplePersistenceIgnoreState YES
python3 scripts/build_extension.py --output /private/tmp/open-painter-latency-candidate.zip
```

The test runner sets `BLENDER_USER_RESOURCES` to `.blender-test/user` and restores
its generated tracked fixtures. For an independent before run, extract `src`,
`scripts`, `tests` and `blender_manifest.toml` from `df32483` into a new temporary
directory using `git archive`. Run the **current** native-frame harness with
`OP_BENCH_REPO` set to that archive, a different report and a fresh user-resource
directory. The checked-in JSON records the actual commands and environment.

## Remaining limits

New scalar/mask kernels are enabled only on the validated Metal backend.
Nonidentity UV transforms, other generators, filters, reference masks,
procedural normals, narrow/reversed Levels and high-gain scratch masks use the
CPU reference. This preserves their output but those edits can still take
seconds. Full CPU save/export remains blocking and is not made instantaneous
by the disposable live textures.

Folder reuse is limited to the first root; unchanged inputs of complex stacks
may still be regenerated if their separate leaf cache is evicted. High-resolution
Image-backed displays retain CPU storage as well as GPU resources. The default
4096 MiB preflight covers additional live resources, including both caches;
it is not a process-wide cap. Oversized work falls back with a reason instead
of reducing authored resolution. This run does not certify 4K/8K, arbitrary
production meshes, Windows/Linux, other GPUs or physical tablets. It does not
claim 60 FPS or numerical equivalence to Adobe's proprietary algorithms.
