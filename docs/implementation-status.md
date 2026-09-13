# Roadmap implementation status

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
| M5 | Shared evaluated geometry, scene-specific dependency graph, baked-map provenance/staleness, scale/UV/cage/degenerate diagnostics, map-boundary cancellable bake queue. | Evaluated projection, utility-map and diagnostics tests. A single Cycles map remains blocking. Exact UV overlap/ray-coverage diagnosis and arbitrary modifier-input provenance are not complete. |
| M6 | Explicit object/collection scene fields: orientation, position, distance, contact, exposure, heat/moisture/soot, named attributes and vertex groups. Bounded analysis resolution; manual/live/frozen modes; last-good retention and frozen portable serialization. | Panel/bolt field fixture verifies moving/deleting influencers, material-edit reuse and frozen round-trip. Fields are artistic scalar analyses, not fluid/thermal/physical simulation. |
| M7 | Stable layer/mask references, pre/post-filter stages, dependency traversal and cycle rejection, published packed masks, duplicate/import remapping. | Reference consumers, cycles, edits, group/set duplication and save/import tests. Folder channel-composite references reject explicitly. Some image edits invalidate conservatively; published snapshots require explicit republishing. |
| M8 | Published-mask-driven rust flakes/moss instances, deterministic area-weighted placement, Geometry Nodes ownership, count/triangle limits, regenerate/freeze/remove, editable curve weld geometry and logical coating-depth masks. | Actual evaluated instances, source preservation, transforms, ownership and limits tested. Geometry is optional and bounded. No volumetric substrate cutting or automatic physical damage. |
| M9 | Explicit semantic layer tags, versioned environment profiles, Clean/Used/Damaged controls, deterministic variations, local opacity overrides, optional state serialization. | Three separate texture sets reproduce coordinated but distinct states; paint layers remain unchanged. Existing materials are not silently retagged; shared outputs cannot vary independently. |
| M10 | Separate one-click Material Stamp with coordinated Base Color/Roughness/Metallic/Height, shared coverage, UV seam mapping, slot/backface/occlusion restrictions, pressure, packed immutable source snapshots and native undo. Existing fill projection supports editable decal transforms. | Seam/multi-slot CPU fixture and real Blender undo/redo. Continuous multi-channel strokes, full brush modifiers and dedicated scratch-curve fields are not complete. Packing/copying a stamp is not claimed fast continuous painting. |
| M11 | Godot/glTF/Unity URP/HDRP profiles, independent packed channels, evaluated mesh/texture/manifest bundle, optional realized geometry, texel-density measurement, new-UV staged atlas, failure cleanup. | Export-byte, bundle, atlas preservation and rollback tests. UDIM `Image.pixels` tile-addressing probe failed; native two-tile paint/bake/save/export acceptance is NOT passed and no UDIM UI is advertised. Atlas uses grid cells, not a production packing optimizer. |
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
