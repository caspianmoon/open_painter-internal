# Roadmap implementation status

## Ucupaint integration — 2026-09-14

Implemented from the study, outside the shader compiler and smart-material files:
signed mean-curvature and selection utility maps with Reselect Baked Faces, UDIM tile
enumeration in Inspect Bake Setup, an explicit AO Distance through an owned staging
World, and opt-in 2× supersampled Cycles bakes with a byte preflight. Details, tests and
measurements: [ucupaint-integration.md](ucupaint-integration.md). Section 7 of the
study measures the 0.2.7 shader compiler (quadratic flat-tree construction, full
rebuild on layer visibility, 64 fragment samplers on Metal) against Ucupaint's
per-layer and library group structure (46 s → 2.5 s → 0.17 s for 3,000 nodes) and
lists compiler recommendations that were not applied here.

## Ucupaint study — 2026-09-14

Research only, no code changes. [What Open Painter can take from Ucupaint 3.0.0](research/ucupaint-study.md)
compares the GPL-3.0-or-later Ucupaint source (shader-node layer stack) with the CPU evaluator and lists ranked,
source-cited candidates: a signed mean-curvature map to port (M5), UDIM tile enumeration and the file-based tile
workflow (M11, consistent with the tile-access gate), bevel/SSAA/AO bake options (M5), per-channel mask participation,
a Previous-Layers adjustment layer and non-destructive freezes (M7), a solo viewer (M3), Empty-driven decals (M10), a
data-image colour-space policy, and mask-edge transition effects. Node plumbing, tile-file renaming, atlases, the
network updater and scene-mutating bake bookkeeping are explicitly not to be taken. Each recommendation names the
Open Painter files it touches and the Blender checks required before committing.



## Generated material quality — 2026-09-14

Version **0.2.5** upgrades all **50 existing smart materials** using 15 generated
scalar images across 76 existing layers. All 229 layer records, native UI,
mesh-aware masks, fixed display material and portable v1/v2 compatibility remain.
Generated inputs are shared, packed and sampled as float32 scalar data; native
color, roughness and height remain editable. Existing saved material instances
are preserved. All 50 library thumbnails were rendered again from actual output.

Fresh validation passed 55 background integration scripts, 49 unit tests, five
real-window GPU suites and four native GUI workflows. The new sources and
portable/shared-image paths have dedicated regressions. Full 2K CPU composition
was measured at 36.566 s cold / 0.272 s warm roughness versus 30.887 / 0.244 s for
the preserved procedural recipe. Warm source/mask generation and geometry work
are zero; these numbers are CPU authority, not interactive frame latency.
The generated images are 1254px sources and are not claimed as measured 2K PBR
scans or universally seamless assets.

[Implementation, exact commands, measurements and limits](generated-material-quality.md),
[actual render gallery](generated-material-gallery.html), and
[source provenance](../src/open_painter/assets/generated/provenance.json).

---

## Licensed library sourcing — 2026-09-14

Research completed on `task/licensed-library-research`, preserving 0.2.4 commit
`25368daac46f6df9885e74deca692b10f6686390`. Checked publisher licenses specifically
for redistribution inside a **paid** add-on/library. Curated 18 CC0 candidates
across PBR surfaces, mask inputs and procedural source graphs; distinguished
source resale from permission to sell renders and documented excluded licenses.

Downloaded seven sample files from four assets, recorded SHA-256, verified four
publisher checksums and ZIP integrity, inspected supplied pixel formats and
viewed representative images. No new native smart material or runtime quality
claim follows from those source checks. No Blender tests or performance
measurements were run for this research-only task; the installed 0.2.4 release
is unchanged. The [research, concrete assets and licensing evidence](research/licensed-library-sourcing.md),
[candidate manifest](research/licensed-library-candidates.json) and
[sample record](research/licensed-library-sample-verification.json) document
integration requirements and actual verification.

---

## Bake queue hotfix — 2026-09-14

Version **0.2.4**, on `task/bake-queue-timer-fix`, preserves base
`02c88157681ea532a6a692fb35e3b5b4111775ba`. The UI bake queue attempted to access
`Event.timer`, which does not exist in the installed Blender 5.2.1 LTS API.
A real-window regression reproduced the exact exception before any map bake.
The queue now advances using its owned timer's duration, ignoring unrelated
TIMER events until that timer ticks. Cleanup also handles failed initialization
and generator-close failures.

Native-event tests now complete real utility maps, preserve completed maps on
Esc or a later failure, and release the queue without changing artist geometry,
materials, selection or scene settings. This changes neither map algorithms nor
authored resolution. The native UI, GPU preview, CPU evaluator, materials and
portable formats remain intact. Each individual map bake is still synchronous;
this fixes the queue crash and does not claim a baking speedup. No performance
benchmark was rerun for this hotfix.

Fresh validation passed: 52 background integration scripts, 46 unit tests, four
native GUI workflows and isolated archive validation/install.
[Exact commands, before/after evidence and limits](bake-queue-fix.md).

---

## Live latency follow-up — 2026-09-14

Implemented incrementally on `task/live-preview-latency`, based on
`df32483a158fb3852564654c6d10f5f2ff7673d4`. The new version is **0.2.3**.
The CPU reference, native UI, fixed display material, all existing recipes and
both portable material versions remain. Supported scalar fields and mesh-aware
masks generate GPU textures directly on Metal; other inputs retain CPU
semantics. Two independent 256 MiB caches bound source and completed first-root
folder reuse. Isolated coverage and passthrough behavior remain distinct.

Fresh Worn Painted Metal **2K float32** measurements with the **production
update timer and first completed native frame**: cold 24.237 s → 0.808 s;
mask strength 4.630 s → 76.45 ms; shared procedural scale 2.860 s → 59.64 ms;
folder opacity 90.99 → 42.02 ms. Roughness did not improve: 25.00 → 28.66 ms.
These are one-machine measurements, not a 60 FPS guarantee. Authored quality
was not reduced. Older offscreen timings below complete all viewport samples
and must not be interpreted as the first native interactive frame.

The [implementation and measurement report](live-preview-latency.md),
[primary-source research](live-preview-latency-research.md) and
[validation inventory](performance/latency-validation.json) contain exact
commands, results, hashes, changed files and limitations. Full 2K comparison
of all eight channels passed within **2.861023e-6** maximum absolute error.
Current automated coverage: 52 background integration scripts, 42 Blender unit
tests, 37 independent CPU tests, 183 GPU compositing checks, 91 checkpoint
checks, 38 scalar configurations, 11 full-2K mask cases plus 17 preflight checks,
three native UI workflows and the live painting/save/export workflow.
Save/render/export still finish the latest complete recipe through CPU; these
operations and unsupported live source/filter domains can remain slow.

---

## Live procedural preview follow-up — 2026-09-13

Implemented on `task/live-procedural-preview`, preserving shared-checkout commit
`00ccf773bae22ee5f82cf787de4c7dc0ca5bca9e` and the earlier bake integration. This
follows the user's later authorization for a shader-like procedural backend.
The fixed display material, native UI, CPU correctness reference, existing
materials and version-1/version-2 material compatibility remain intact.

The new backend composes cached, dependency-keyed texture inputs on the GPU.
Unchanged channels are rejected by a pure signature pass before any texture
work. Constant and opacity edits update shader parameters; complex procedural
and filtered inputs keep exact CPU implementations. GPU-only full-precision
display images include updated mipmaps. Native float painting can reuse the
existing GPU canvas. Save, render and export flush the latest authoritative CPU
textures; no settled-edit timer forces a CPU bake during live authoring.

Actual Worn Painted Metal 2K completed-frame measurement: warm median
**296.89 ms CPU → 138.14 ms live GPU (2.15×)**. Add-on preparation/submission
was **200.90 ms → 5.97 ms**, with zero managed image transfers on warm GPU
roughness edits. These are separate timings; 5.97 ms is not displayed-frame
latency. Initial preparation still takes seconds and 60 FPS is not established.

The [implementation, commands, measured context and limits](live-procedural-preview.md)
and [raw completed-frame report](performance/live-viewport-2k.json) document this
follow-up. GPU mathematical parity, dependency/lifecycle regressions, native UI,
authoritative export and isolated packaging passed. Actual final acceptance:
51 background integration scripts, 32 Blender unit tests, 27 NumPy tests, three
native GUI workflows, 125 GPU math checks and the live workflow suite. All eight
channels of the Worn Painted Metal 2K fixture matched the CPU reference within
`1.193e-7` maximum absolute error. The
[validation inventory](performance/live-preview-validation-inventory.json)
records exact commands, results, changed files and source digest. The
historical milestone measurements and disabled GPU experiment below describe
the preceding implementation, not the new production live compositor.

---

## Bake integration follow-up — 2026-09-13

Compared the supplied G-Ready 2.1 source with Open Painter and integrated the
useful workflow improvements on `task/gready-bake-integration`: explicit/name
source matching, isolated render-configured staging, assembly/part AO scopes,
editable cages, evaluated topology validation and independent extrusion/device
controls. Original materials, nodes, images, scene settings and transforms are
preserved on success and failure.

The [comparison and validation report](gready-bake-comparison.md) contains source
evidence, real pixel regressions, commands, usage and limits. No claim of a new
bake engine or bake speedup is made. G-Ready source/icons are not included in the
extension package; the user's later `00ccf77` commit added the supplied comparison
source to the repository separately.

The roadmap implementation and Worn Painted Metal timings below describe the
preceding `0f44580` continuation. They were not remeasured for this bake change.

---

This continuation implements work across M0–M12 on
`task/m0-m1-evaluation-correctness`. It preserves the newer main changes and
shared-checkout commit `98a18e8ef0a90d3143d26445168c0cc981cb9ecd` (`latest updates`).
It does **not** certify every acceptance criterion as complete. In particular,
the UDIM gate failed, the GPU experiment remains disabled, and interactive
whole-material latency is not established.

The CPU float32 evaluator remains the correctness reference. Existing materials,
native UI, fixed-size display material, native single-channel painting, and
version-1/version-2 material readers remain. New format fields are optional and
material files remain data-only. Preview resolution is explicitly selected;
authored images and save/export outputs retain their resolution and precision.

## Implemented work and acceptance boundaries

| Milestone | Implemented | Evidence and remaining limits |
| --- | --- | --- |
| M0 | Fresh-process performance harness, phase/transfer/cache/RSS instrumentation, capability probes, isolated package install and regression runners. Compute-slice distributions added. | Paired performance context and exact commands below. RSS includes Blender, not just evaluator allocations. No physical tablet or displayed-frame latency measurement. |
| M1 | Scene-scoped scheduler identity, failed-revision parking, stale-publication guards, immutable parameter snapshots, dependency revisions, geometry reuse. Registration now rolls back partial initialization and supports repeated registration/unregistration. | Existing lifecycle tests plus immutable jobs and injected registration failure. Arbitrary external image writes still need detection/Refresh; unknown modifier dependency graphs remain conservative. |
| M2 | Global disposable-cache allowance, scalar float32 working buffers, finite-halo strip evaluation, immutable decoded image reuse across a revision, retained active-channel output for regional edits without evicting source caches. | CPU parity and cache tests. Mandatory images, some full-frame filter temporaries and all process allocations are not a hard project-memory cap. No general buffer pool or subrectangle Blender Image upload. |
| M3 | Explicit 512/1024 interactive proxy option, independent proxy cache and image ownership, adaptive cooperative compute batching, active-channel/fair scheduling, settled final evaluation, authoritative save/render flush. Deleted-set proxies are released. | Proxy/save and existing native paint tests. Individual full-frame operations and image transfers can still exceed the compute budget. No claim of universal real-time behavior or 2× cold speedup. |
| M4 | Disabled-by-default CPU/GPU image-chain contract: fill, masked over, Levels and separable Gaussian blur; context-scoped resources and transfer/readback timing. Unsupported operations/parameters reject. | Actual Metal float32 parity experiment; headless CPU fallback. Isolated-chain speed does not establish a whole-material benefit. No production GPU routing or shader-stack rewrite. |
| M5 | Shared evaluated geometry, scene-specific dependency graph, baked-map provenance/staleness, scale/UV/cage/degenerate diagnostics, map-boundary cancellable bake queue. 2026-09-14: exact UV overlap analysis across all objects of a set, stacked/mirrored classification per polygon, sampled cage-ray coverage, Inspect Bake Setup and Select Problem Faces in the bake panel ([bake diagnostics](bake-diagnostics.md)). | Evaluated projection, utility-map and diagnostics tests; 126k evaluated faces inspect in 4.7 s; 20k sampled cage rays against a 126k-face source take 0.78 s and are reported as an estimate with the missed faces. A single Cycles map remains blocking. Arbitrary modifier-input provenance is not complete; face selection needs matching base/evaluated face counts. |
| M6 | Explicit object/collection scene fields: orientation, position, distance, contact, exposure, heat/moisture/soot, named attributes and vertex groups. Bounded analysis resolution; manual/live/frozen modes; last-good retention and frozen portable serialization. | Panel/bolt field fixture verifies moving/deleting influencers, material-edit reuse and frozen round-trip. Fields are artistic scalar analyses, not fluid/thermal/physical simulation. |
| M7 | Stable layer/mask references, pre/post-filter stages, dependency traversal and cycle rejection, published packed masks, duplicate/import remapping. | Reference consumers, cycles, edits, group/set duplication and save/import tests. Folder channel-composite references reject explicitly. Some image edits invalidate conservatively; published snapshots require explicit republishing. |
| M8 | Published-mask-driven rust flakes/moss instances, deterministic area-weighted placement, Geometry Nodes ownership, count/triangle limits, regenerate/freeze/remove, editable curve weld geometry and logical coating-depth masks. | Actual evaluated instances, source preservation, transforms, ownership and limits tested. Geometry is optional and bounded. No volumetric substrate cutting or automatic physical damage. |
| M9 | Explicit semantic layer tags, versioned environment profiles, Clean/Used/Damaged controls, deterministic variations, local opacity overrides, optional state serialization. | Three separate texture sets reproduce coordinated but distinct states; paint layers remain unchanged. Existing materials are not silently retagged; shared outputs cannot vary independently. |
| M10 | Separate one-click Material Stamp with coordinated Base Color/Roughness/Metallic/Height, shared coverage, UV seam mapping, slot/backface/occlusion restrictions, pressure, packed immutable source snapshots and native undo. Existing fill projection supports editable decal transforms. | Seam/multi-slot CPU fixture and real Blender undo/redo. Continuous multi-channel strokes, full brush modifiers and dedicated scratch-curve fields are not complete. Packing/copying a stamp is not claimed fast continuous painting. |
| M11 | Godot/glTF/Unity URP/HDRP profiles, independent packed channels, evaluated mesh/texture/manifest bundle, optional realized geometry, texel-density measurement, new-UV staged atlas, failure cleanup. | Export-byte, bundle, atlas preservation and rollback tests. UDIM gate rerun 2026-09-14 ([udim-tile-access.md](udim-tile-access.md)): `Image.pixels` and `gpu.texture.from_image` reach only tile 1001, but per-tile files through a `<UDIM>` path and native painting onto tile 1002 both pass. Two-tile paint/bake/save/export acceptance is still NOT run and no UDIM UI is advertised. Atlas uses grid cells, not a production packing optimizer. |
| M12 | Bounded offline pair contact history with broad-phase bounds and topology checks, rest-strain calculation, explicit cut-face attributes, snow/splinter/peel prototypes under M8 limits, three-way inheritance data preview, declarative recipe API, resource inventory and staged mesh LOD. | Offline/registration and geometry fixtures. Contact proximity/origin travel is not tangential friction. Persistent parent-version inheritance workflow, physical fatigue, true cross-sections and AI reference fitting are not complete. These packages remain optional/experimental individually, as specified by the plan. |

## Validation

Final validation results and measurements are recorded below after execution.
All Blender commands use isolated user resources. The artist profile, original
working scene and published extension ZIP are not replaced. The candidate is
`/private/tmp/open-painter-milestones.zip`.

The historical M0/M1 and earlier CPU optimization reports are preserved in
[implementation-status-through-e22e9a8.md](implementation-status-through-e22e9a8.md).
Their timings are historical, not measurements of this continuation.

Final-source acceptance on Blender 5.2.1 LTS `9e2066aef7ef`, Apple M3 Pro,
18 GiB, NumPy 2.3.4:

- 48 background integration scripts and 32 Blender unit tests: PASS.
- Extension manifest validation and actual candidate ZIP installation: PASS.
- 27 bpy-independent tests with Blender's bundled Python/NumPy: PASS.
- Three existing native GUI scripts: PASS (elements, T/N, asset selection,
  2D/3D, channel/filter controls, actual native stroke and save).
- Additional material-stamp GUI test: actual undo and redo PASS.
- Final logs contain no Python traceback or RNA registration error. An earlier
  operator PointerProperty registration error was found in log review and fixed
  using native string-backed object search; a regression asserts its RNA type.
- Compilation and `git diff --check`: PASS.

GUI correctness tests overlapped background acceptance; their stroke timings are
not used as performance evidence. The paired benchmark runs separately after
all Blender test processes stop. No remote CI, Windows/Linux GPU, 4K/8K or
physical-tablet/display-latency result is claimed.

Exact commands from the repository root:

```sh
python3 scripts/build_extension.py --output /private/tmp/open-painter-milestones.zip
python3 scripts/run_tests.py --blender /Applications/Blender.app/Contents/MacOS/Blender --package /private/tmp/open-painter-milestones.zip
python3 scripts/run_tests.py --blender /Applications/Blender.app/Contents/MacOS/Blender --gui-only
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 scripts/run_pure_tests.py
BLENDER_USER_RESOURCES=/private/tmp/op-stamp-final-user /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python tests/integration/material_stamp_ui.py
BLENDER_USER_RESOURCES=/private/tmp/op-gpu-final-user /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python tests/integration/gpu_parity.py
BLENDER_USER_RESOURCES=/private/tmp/op-udim-probe-user /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python scripts/probe_udim.py
python3 scripts/benchmark_comparison.py --baseline-root /private/tmp/op-m0-baseline-control --report /private/tmp/op-all-milestones-paired.json
python3 -m compileall -q src/open_painter scripts tests
git diff --check
```

## Capability measurements

[Metal result](performance/milestones-gpu-metal.json): one 512² RGBA32F
fill/over/Levels/Gaussian chain, max absolute CPU difference `2.68221e-7`.
CPU end-to-end 0.041857 s; process-first GPU execution 0.034739 s;
warm GPU execution 0.015778 s; 16 MiB GPU image resources.
Shader construction took 0.002038 s separately. Driver shader caches were not
cleared, so this is not an uncached shader compilation claim. The report repeats
the one-time construction cost in its warm metadata; it is not paid again.
The experiment includes source Image reads, upload, readback and final Image
write, but not a production material graph or displayed frame. It remains
**disabled by default**, with CPU fallback in headless mode.

[UDIM probe](performance/milestones-udim-probe.json): writing different colors
after switching `tiles.active_index` returned the same green pixels for both
1001 and 1002 through `Image.pixels`. This API path failed independent tile
addressing. Native tile painting and baking were therefore **not run**. This
is a failed capability gate, not a claim that every possible UDIM access method
is impossible. A different tile-storage/transfer path and complete two-tile
acceptance are required before advertising support.
The 2026-09-14 rerun in
[udim-tile-access.md](udim-tile-access.md) found that path: per-tile files.

## Fresh paired 2K CPU benchmark

[Raw paired results](performance/milestones-paired-control.json) contain all six
fresh-process commands, timestamps, source hashes, phase counters, transfers,
cache observations, compute-slice distributions and peak process RSS. Three
alternating pairs compare the reconstructed instrumented `15ef25e` baseline with
this continuation. This is not a comparison to the intervening `e22e9a8` release.

Worn Painted Metal: six layer records, eight 2048² float32 final outputs, the
committed synthetic 32² AO/curvature fixture, no assigned mesh, no bake or viewport
time, full authored resolution, no GPU backend and no concurrent Blender test
workload. Three runs support medians, not across-run latency percentiles.

| Median wall seconds | Reconstructed baseline | Current continuation |
| --- | ---: | ---: |
| Cold full texture set | 23.2503 | 19.8988 |
| Warm roughness constant edit | 2.0663 | 0.2078 |

Warm edits are 9.94× faster; cold evaluation is 14.4% faster, **not** the proposed
2× cold target. Warm edits regenerate zero procedural sources, masks or geometry
and read zero image bytes, while still publishing a full 64 MiB output. Cold
publication still transfers all eight 64 MiB outputs.

Peak process RSS increased: baseline 864–874 MiB; current 934–945 MiB. This
includes Blender and setup. Retaining an active-channel CPU image to avoid later
readback contributes working memory; the cache allowance is not an RSS cap.
No overall memory reduction is claimed.

Cold compute-slice p95 ranges 25.7–28.3 ms across these runs; the largest observed
indivisible computation was 238.3 ms. Adaptive batching cannot interrupt an
individual NumPy/full-frame operation. These counters exclude event-loop wait
and displayed-frame latency. The proposed interactive latency gate is therefore
not established, despite the warm constant-edit improvement.

The [validation inventory](performance/milestones-validation.json) records exact
commands, results, changed files and the tested source-tree SHA256. Production
source was unchanged between the final suite and paired measurements. Commit
`98a18e8` was preserved; subsequent fixes and this report are in the following
task-branch commit. No main reset or user-profile installation occurred.

See [milestone workflows](milestone-workflows.md) for entry points to the new
controls. The table above is the authoritative remaining-work list; passing the
current regression suite is not a certification of every roadmap feature.
