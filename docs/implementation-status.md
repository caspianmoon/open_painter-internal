# Roadmap implementation status

The requested scope is the full updated plan. M0 was the starting order, not
completion of that scope. Work continues incrementally on
`task/m0-m1-evaluation-correctness`; no main reset, user-profile installation,
material replacement, authored-resolution reduction, or format migration.
Upstream main was fetched again and remains `15ef25ec532f466eff18a7265fa7d7ef5019e8ef`.

## Current continuation

| Package | Implemented in this continuation | Remaining acceptance work |
| --- | --- | --- |
| M2 memory | One configurable process-wide disposable cache; geometry included; unique NumPy allocation ownership; LRU eviction across sets; oversized disposable entries are uncached. Oversized geometry is explicitly held once per running channel job and released on completion/cancellation, with a separate measured byte counter. Native preference exposes allowance/current bytes. | This bounds retained cache, not Blender RSS, editable images, decoded per-job images, or all filter temporaries. Full reservation/preflight and mandatory-memory accounting remain. |
| M2 typed buffers | Proven scalar-compatible channels use float32 scalar composition and scalar procedural fills with separate coverage. Images, color-producing filters and nonseparable blends retain RGB reference behavior. | RGB sources still have existing RGBA boundaries; no global buffer pool or precision reduction. |
| M2 spatial work | Pointwise and finite-footprint mask/channel filters use full-width strips with composed clamped halos. Native mask paint regions propagate to all dependent channels. Same-grid painted masks use direct slices. | Coordinate-dependent warps/unknown operations retain whole-image fallback. Sparse distant strokes use a conservative enclosing row interval. Regional publication still reads/writes a full Blender Image; no claim of sub-image transfers. |
| M3 scheduling/save | Active set/channel first with fair rotation; pending jobs cannot starve other sets. Verified dirty-image state plus managed identity skips unchanged packing; native writes still pack. Save flushes latest authoritative edits. | Explicit Interactive proxy mode, adaptive compute slices, transfer-aware paint polling and displayed-frame latency gates remain. Full-resolution output is still the sole authority. |
| M5 evaluated geometry | Utility baker's owned evaluated-mesh scope and UV rasterizer extracted into shared services; triplanar now uses evaluated render UVs/corner normals and transformed positions. Base corner-normal changes enter geometry keys. Empty duplicated sets retain neutral unassigned previews. | Map provenance/staleness UI, diagnostics, queued bake cancellation, full arbitrary modifier dependency tracking and scene-aware fields remain. |
| M4 | Not promoted or advertised. | Conditional GPU experiment depends on the remaining M2/M3 work and end-to-end parity/performance gate. |
| M6–M11 | Existing functionality is preserved; these roadmap packages are not claimed complete. | Scene fields; published references; owned geometry effects; coordinated material states; material stamps; UDIM/atlas and export extensions require their implementation and acceptance tests. |
| M12 | Optional packages remain separate, as specified by the plan. | No AI, physical wear, inheritance or advanced geometry claims. |

The original M0/M1 evidence below is historical to commit `36ece775`, not a
new timing or a claim that the roadmap is complete. Continuation evidence:

- Full background acceptance: 37 integration scripts and 32 Blender unit tests;
  candidate extension validation/installation use isolated user resources.
- Native GUI acceptance: native elements, T/N, asset and filter controls, an
  actual paint stroke and save all passed (three scripts). Shutdown regression
  was fixed and GUI rerun; Blender's pre-existing five-block / 0.000481 MiB
  shutdown report remains, with no Python finalizer exception on the rerun.
- 27 bpy-independent tests passed with Blender's NumPy Python. Compilation and
  whitespace checks passed. No remote CI, 4K/8K, or physical-tablet latency claim.
- Shared-budget regression covers three sets, skipped oversized entries,
  exactly one geometry build per job under an intentionally tiny cache budget,
  and release on cancellation. Scalar/RGB oracle comparison is pixel-exact at
  512 on Worn Painted Metal with isolated and passthrough groups. Finite filter
  strips match full reference within atol=1e-7, rtol=1e-6; existing exact
  Gaussian mask partition assertions remain exact.

Final-source paired measurements are in
[`roadmap-paired-control.json`](performance/roadmap-paired-control.json).
Three alternating fresh processes per variant on Apple M3 Pro / 18 GiB,
Blender 5.2.1 LTS `9e2066aef7ef`, NumPy 2.3.4: Worn Painted Metal, six layer
records, eight full 2048² float32 outputs, the unchanged synthetic 32² baked-map
fixture, no viewport or bake time, no concurrent Blender test workload.

| Median wall seconds | Reconstructed baseline | Continuation |
| --- | ---: | ---: |
| Cold full set | 22.3083 | 18.7846 |
| Warm roughness constant edit | 1.9976 | 0.1844 |

The warm edit is 10.83× faster; cold evaluation is 15.8% faster, **not** the 2×
cold target. Warm edits regenerate zero sources/masks/geometry and still write
one full 64 MiB image. The 512 MiB eight-output transfer is not elided in cold
measurements. Cache allowance is not a process memory cap. Process peak RSS is
recorded per run, including Blender and fixture setup; no large RSS reduction
is claimed. The earlier M2-stage paired run is retained separately in
`m2-paired-control.json`; historical timings below are not new measurements.

The complete changed-file inventory and tested source hash are in
[`continuation-validation.json`](performance/continuation-validation.json).

Exact validation commands (from repository root):

```sh
python3 scripts/build_extension.py --output /private/tmp/open-painter-roadmap.zip
python3 scripts/run_tests.py --blender /Applications/Blender.app/Contents/MacOS/Blender --package /private/tmp/open-painter-roadmap.zip
python3 scripts/run_tests.py --blender /Applications/Blender.app/Contents/MacOS/Blender --gui-only
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 scripts/run_pure_tests.py
python3 scripts/benchmark_comparison.py --baseline-root /private/tmp/op-m0-baseline-control --report /private/tmp/op-roadmap-paired.json
python3 -m compileall -q src/open_painter scripts tests
git diff --check
```

The reconstructed baseline and patch provenance are documented in the original
M0 section below. Every benchmark subprocess command and source hash is retained
in the paired JSON. Two extracted geometry files had terminal blank lines
removed after measurements; AST equality was checked, and both source hashes
and the exact whitespace delta are recorded in the validation JSON. No release version or existing published ZIP was replaced.
The full roadmap remains in progress; the table above states the remaining
gates rather than treating M0 or this continuation as whole-plan completion.

---

# M0 baseline and initial M1 correctness work

This is an incremental change on `task/m0-m1-evaluation-correctness`, based on
`15ef25ec532f466eff18a7265fa7d7ef5019e8ef`. `git fetch origin` confirmed that this
was also upstream main at the start. The plan reviewed `c0b4045`; the newer
`06048d2` asset categories and `15ef25e` inline blend/opacity controls are retained.
The supplied untracked `Open_Painter_Updated_Codex_Plan.md` is preserved unchanged.
No reset, live-profile installation, release-version bump or replacement of the
published extension ZIP is part of this task.

## Scope and evidence levels

| Work package | Implementation and affected files | Evidence / remaining gate |
| --- | --- | --- |
| M0 measurements | `evaluation/metrics.py`; phase hooks in evaluator, source/filter/mask/blend, image manager, geometry and raster cache; `scripts/benchmark_textures.py`; `tests/performance/fixtures.py` | Fresh-process CPU measurements, including full-resolution transfers. No FPS claim. |
| M0 capability probes | `scripts/probe_blender_capabilities.py` | Actual Blender 5.2.1 background and GUI probes. Full float Image transfer, evaluated geometry/corner normals and stored GN attribute pass. Metal RGBA32F clear/readback and built-in shader creation pass in GUI. No GPU authoring backend added. |
| M0 test/packaging audit | `scripts/run_tests.py`, `scripts/build_extension.py`, `scripts/run_pure_tests.py`, `.github/workflows/tests.yml`; native stroke/install tests | Test ZIP installs under `bl_ext` in isolated resources. Pure CI configured; Blender background CI requires explicit dispatch to a labelled host. CI has not been run remotely. |
| M1 scene identity | `evaluation/resource_keys.py`, `evaluation/cache.py`, `painting/paint_session.py` | Copied scenes preserve authoring UUIDs and have separate pending jobs, evaluators and paint regions. Deletion/load prune runtime state. |
| M1 geometry lifetime | `evaluation/dependencies.py`, `evaluation/raster_cache.py`, `evaluation/channel_eval.py`, `evaluation/mask_eval.py` | Material constants reuse geometry and triplanar scalar sources. Geometry/UV/render-UV/transform/modifier/frame/assignment changes invalidate the geometry key; forced CPU results remain equal. |
| M1 source revisions | `core/image_manager.py`, `evaluation/dependencies.py`, `evaluation/mask_eval.py` | Managed writes increment image pixel revisions. Direct-channel writes dirty only consuming channels. Masks use their actual map dependencies, including optional AO for edge wear; unknown generators remain conservative. External unmanaged pixel edits still require native paint detection or explicit Refresh. |
| M1 failed/stale work | `evaluation/cache.py`, `evaluation/evaluator.py` | Queued/running/stale/failed/ready runtime states; failed revision parked, dirty outputs retained, last completed pixels preserved. Explicit retry, edit, save or export can retry. Superseded jobs stop at the next yield and cannot publish or cache new-revision masks from old decoded pixels. Save/reopen includes the latest successful edit; failed export writes no files. |

The current CPU/NumPy evaluator remains the correctness reference. All authored
resolutions, float32 evaluation, eight channels, existing materials/masks/effects,
fixed display material, native UI, UUIDs, folder ordering and version-1/version-2
`.openpaintmaterial` readers/writer are retained. No presets, geometry effects,
UDIM authoring, new editor, shader-graph rewrite or GPU evaluator were added.

## Baseline and benchmark interpretation

The committed 0.2.0 report remains **historical**. A new run of its original script
is recorded separately in `performance/original-fixture-reproduction.json`:
22.041 s cold, 2.092 s roughness edit. That reproduction overlapped the initial
acceptance suite and is **not** used in the before/after comparison.

`performance/baseline.json` contains three independent fresh-process runs at each
of 512 and 2048, with instrumentation enabled before M1 behavior changes. The
2K baseline median is 24.953 s cold and 2.157 s for a warm constant roughness edit.
The 512 baseline medians are 1.723 s and 0.141 s respectively. Reports record the
base Git commit plus the actual source-tree SHA256 because the changes were
uncommitted during measurement. These are new measurements, not copied historical
values. Three runs support a median, not a reliable across-run p95 estimate.

Reference context: macOS / Apple M3 Pro, 18 GiB RAM, Blender 5.2.1 LTS build
`9e2066aef7ef`, bundled NumPy 2.3.4. Worn Painted Metal has six layer records and
eight evaluated outputs. The fixture uses synthetic 32² AO/curvature maps exactly
as the committed benchmark does; there is no assigned mesh, bake, viewport render,
preview downsampling or precision reduction in this workload. First channel
publication is reported separately; it is **not** a first displayed frame.

Phase durations are nested inclusive/exclusive CPU and wall times. Cache counters
count dictionary membership hits/misses, not user edits. Transfer bytes are actual
float RGBA `foreach_get/set` amounts. Allocation totals cover output buffers,
new read buffers and cache admissions; they are not a complete allocation trace.
Cache/geometry/output gauges are last-observed sizes at instrumentation points
(the initial baseline schema called them `current_bytes`); they do not establish
continuous residency. Peak RSS is the OS process-lifetime high-water mark in each
fresh Blender process, including Blender and setup. Unknown NumPy temporaries,
GPU memory and packing allocations are not presented as controlled cache memory.

## Final-source performance and alternating-order control

The final-source batch (`performance/after-m1.json`) measured **35.891 s cold /
3.387 s warm** at 2K (three-run medians). It was slower than both the initial
baseline and the earlier after-M1 batch. Those results are retained, not discarded.
512² medians were 1.761 s / 0.142 s.

To investigate the changing conditions, six more fresh processes alternated
between an isolated, SHA256-verified reconstruction of the baseline and the final
source, with order before/after, after/before, before/after.
`performance/paired-control.json` retains every subprocess command and result:

| 2K Worn Painted Metal, three runs each | Baseline control | Final source |
| --- | ---: | ---: |
| Cold median wall time | 23.069 s | 26.233 s |
| Warm roughness median wall time | 2.151 s | 2.277 s |
| Cold median CPU time | 22.178 s | 23.955 s |
| Warm roughness median CPU time | 2.099 s | 2.113 s |

**There is no Worn Painted Metal speedup to claim.** The paired cold wall median
is 13.7% higher, and warm wall median 5.9% higher; CPU differences are +8.0% and
+0.6%. Variability clearly affects these runs, but these data do not establish
that the whole difference is host noise. Treat this as an unresolved performance
regression/limit for follow-up, not a passed speed gate. The verified gain is
source/geometry reuse and correct job lifecycle, without altered texture math.

The paired run executed `python3 /private/tmp/op-m0-paired-control.py`; the same
harness is provided with configurable paths as `scripts/benchmark_comparison.py`
(CLI parsing was also checked). Reproduce its inputs without resetting any
checkout:

```sh
mkdir -p /private/tmp/op-m0-baseline-control/scripts /private/tmp/op-m0-baseline-control/tests/performance
git archive 15ef25e src/open_painter blender_manifest.toml | tar -x -C /private/tmp/op-m0-baseline-control
git -C /private/tmp/op-m0-baseline-control apply /Volumes/SSD/projects/open_painter/docs/performance/baseline-instrumentation.patch
cp scripts/benchmark_textures.py /private/tmp/op-m0-baseline-control/scripts/
cp tests/performance/fixtures.py /private/tmp/op-m0-baseline-control/tests/performance/
python3 scripts/benchmark_comparison.py --baseline-root /private/tmp/op-m0-baseline-control
```

## Initial measured result of this increment

| Worn Painted Metal workload | Before M1 median | After M1 median |
| --- | ---: | ---: |
| 512² cold eight-channel | 1.723 s | 1.804 s |
| 512² warm roughness edit | 0.141 s | 0.137 s |
| 2048² cold eight-channel | 24.953 s | 26.104 s |
| 2048² warm roughness edit | 2.157 s | 2.268 s |

This first after-M1 batch is preserved in `performance/after-m1-initial.json`.
It precedes the final guard that cancels stale work immediately after a yield;
its source hash is retained. The final-source batch is recorded separately.

These measurements do **not** demonstrate a Worn Painted Metal speedup. At 2K,
cold wall-time median is 4.6% higher and the warm edit median is 5.2% higher.
Cold CPU medians are 23.249 s before and 23.731 s after (+2.1%); warm CPU medians
are 2.079 s and 2.127 s (+2.3%). After-run cold wall times range from 23.141 to
30.683 seconds. Host contention/thermal conditions were not controlled, and three
runs cannot separate a small regression from noise. The benefit established here
is correctness and avoided geometry work for material-only **triplanar** edits;
the Worn fixture is UV-based and already reused its sources before this change.

All after-run warm Worn edits generate zero procedural sources, zero mask stacks,
and zero geometry rasters. A 2K output still transfers 64 MiB; initial eight-channel
publication transfers 512 MiB. Scalar caches remain 144 MiB. Initial process peak
RSS ranges 842–870 MiB before and 757–867 MiB after; this is not proof of a memory
optimization. No authored resolution or precision was lowered.

## Additional M0 workload observations

`performance/workloads.json` records each fixture at 512 and 2048 in fresh
processes, one observation per case. These establish workload coverage, not
performance percentiles or improvement claims. They use the source hash before
the final stale-resume guard, which adds cancellation checks without changing
uninterrupted numerical evaluation. Times below are wall seconds at 2048²:

| Fixture | Cold | Warm roughness | Save | Reopen | Export |
| --- | ---: | ---: | ---: | ---: | ---: |
| constant | 0.488 | 0.085 | 1.139 | 0.308 | 2.577 |
| abandoned | 37.479 | 3.095 | 3.798 | 0.429 | 43.192 |
| gaussian_mask | 1.721 | 0.127 | 0.492 | 0.208 | 3.001 |
| warp | 3.481 | 0.139 | 0.514 | 0.248 | 4.475 |
| painted_mask | 0.994 | 0.668 | 1.020 | 0.157 | 2.271 |
| triplanar_modifiers | 3.368 | 0.317 | 0.626 | 0.226 | 6.219 |
| three_sets | 97.402 | 8.713 | 7.016 | 1.633 | 74.799 |

Save includes a new material edit, full authoritative flush and packing. Reopen
times measure file loading; they exclude subsequent recomposition. Export makes
another edit after reopening and includes that texture set's cold recomposition,
encoding and file writes. The three-set fixture evaluates/saves all three sets;
export targets its first set, as the existing exporter API does. Reports include
all raw phase/transfer/cache/RSS counters. The modifier fixture proves cache
lifetime on a mesh with a modifier present; it does not claim evaluated-modifier
projection, which remains M5.

```sh
python3 scripts/benchmark_textures.py --fixtures constant abandoned gaussian_mask warp painted_mask triplanar_modifiers three_sets --resolutions 512 2048 --repeats 1 --io --stage m0-workload-coverage --report docs/performance/workloads.json
```

The native 512² cube stroke took 33.5 ms in the brush engine and 64.1 ms through
complete roughness output in the first GUI run (`performance/native-stroke.json`).
It is a five-point `bpy.ops.paint.image_paint` brush-engine fixture, not a physical
tablet/input-to-display latency measurement or a p95 figure.

## Regressions and test audit

Before M1 changes, all three added tests failed at the intended assertions, saved
in `performance/regressions-before.json`:

- `resource_invalidation.py`: a constant roughness edit rebuilt geometry.
- `multi_scene_scheduling.py`: copied scene IDs collided in `_pending`.
- `failed_evaluation.py`: a failed job was marked clean.

The active integration runner now includes those three tests and
`scan_regression.py`. Historical `phase13.py`, `phase14.py` and the scan fixture
now inject scene-scoped scheduler identities. Native brush simulation in the scan
test writes directly to Blender pixels, matching how the native engine bypasses
the managed Python writer. Existing source reuse, force refresh, regional/full
comparison, persistence, material catalog, import and export tests remain active.

`gui_workflow.py` is promoted into the GUI runner: the old `view_mode` argument
and hidden-panel assertions belonged to the archived UI. It now uses the current
operator, verifies a real native brush-engine stroke and saved output, and leaves
T/N assertions to `native_ui.py`. The stroke report separates engine/output time
from unmeasured display latency. Registration handler cleanup is checked in the
active `native_registration.py`.

The archived `registration_lifecycle.py` does **not** describe current registration:
it assumes `_registered` / rollback bookkeeping and the absence of the current
editor adapter. An audit run failed on its second consecutive `register()` call.
It remains outside the active runner; double-register/partial-registration
recovery is an existing limitation, not claimed fixed by M1. Other archived modal
dock, separate-window and hidden-panel tests remain historical. No test was
silently weakened to accept an incorrect pixel result.

The test runner preserves bundled `.blend` fixtures and the tracked UI screenshot
around runs. Blender user resources are under `.blender-test/user`; benchmark and
probe profiles are separate temporary directories. The artist's Blender profile
and original working scene are not modified.

## Validation results

- Initial pre-M1 active background suite: PASS, including 28 unit tests.
- Final candidate extension validation and real ZIP installation: PASS; three enable/disable cycles and material evaluation in the extension namespace.
- Final active background suite: 31 integration suites and 28 unit tests PASS, including the new regressions.
- Final GUI suite: PASS; native regions, asset click, T/N, 3D/2D, filter controls, search selection and actual native brush-engine stroke/save.
- NumPy-only reference runner using Blender's bundled Python executable directly (no Blender process): 23 tests PASS. System `python3` has no NumPy; it was not used to claim a pure-suite pass.
- Capability probes: background/GUI reports retained; skipped/unproven capabilities are explicitly marked.
- GUI shutdown logged five small unfreed Blender blocks (0.000481 MB), as in prior runs. No ownership/leak conclusion is drawn from that diagnostic.

## Exact validation commands

Run from the repository root. Temporary report/log paths can be changed.

```sh
python3 scripts/build_extension.py --output /private/tmp/open-painter-m0-m1.zip
python3 scripts/run_tests.py --blender /Applications/Blender.app/Contents/MacOS/Blender --package /private/tmp/open-painter-m0-m1.zip
python3 scripts/run_tests.py --blender /Applications/Blender.app/Contents/MacOS/Blender --gui-only
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 scripts/run_pure_tests.py
python3 scripts/benchmark_textures.py --resolutions 512 2048 --repeats 3 --stage before-m1 --report docs/performance/baseline.json
python3 scripts/benchmark_textures.py --resolutions 512 2048 --repeats 3 --stage after-m1 --report docs/performance/after-m1.json
BLENDER_USER_RESOURCES=/private/tmp/op-m0-probe-user /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python scripts/probe_blender_capabilities.py -- --report docs/performance/capabilities-background.json
BLENDER_USER_RESOURCES=/private/tmp/op-m0-probe-user /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python scripts/probe_blender_capabilities.py -- --report docs/performance/capabilities-gui.json
```

The exact pre-M1 instrumentation is archived in
`performance/baseline-instrumentation.patch`. Apply it to an **isolated copy** of
`15ef25e` (not this working branch), then copy in the benchmark script and fixture.
Its reconstructed `src/open_painter/**/*.py` SHA256 was verified against the report:
`3d09b5a39fc376c6e4bb2e0dac7478c6c220a4f1fda7a5c73e4b57f075ca4d8d`.
The baseline command requires that pre-M1 instrumented source state; running that
command on the finished branch produces another **after** measurement, regardless
of the label. Use recorded source hashes to distinguish the results.

## Changed-file inventory

The complete changed-file list, exact test commands/results and tested source
SHA256 are in [`performance/validation-results.json`](performance/validation-results.json).
The production changes are confined to `core/image_manager.py`, evaluator/cache
and dependency modules, the paint-region key bridge, and timing decorators on
existing CPU operations. All other changes are tests, measurement scripts,
packaging/CI support and documentation/evidence. Material assets, persistence
formats, display-material construction and native UI layout files are unchanged.

## Unresolved gates

This completes the immediate regression-focused increment, not M0–M3 or the
whole roadmap. M0's capability matrix remains partial: native tiled-image
paint/bake addressing, subrectangle Image uploads, additional texture formats,
and cross-platform GUI behavior are not established. No 4K/8K run was attempted.
No sustained tablet-input or displayed-frame latency is established.

Geometry signatures retain the existing base-mesh triplanar evaluator, so
modifiers invalidate work but do not yet influence its projected geometry. Base
mesh fingerprints are refreshed after reported Mesh updates, with bulk reads
outside handlers. Arbitrary external mesh edits without update notification,
Geometry Nodes input/property graphs and evaluated modifier dependencies need
M5's shared snapshot/provenance service. Baked world-space maps are not live bakes.

The scheduler retains last-good results per output channel, not an atomic
all-channel snapshot. Full immutable evaluation plans and dependency scheduling
remain future work. Material/map changes can still dirty more output channels
than necessary even though unrelated cached sources survive. Whole-image float
RGBA transfers, the blanket filter regional fallback and full-size filter
workspaces remain. Cache budgets are still per-set and exclude geometry and
mandatory Blender images; the metrics do not turn that into a global memory cap.
No M2/M3 speed target or real-time performance guarantee is claimed.
