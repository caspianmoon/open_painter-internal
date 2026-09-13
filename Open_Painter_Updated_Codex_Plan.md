# Open Painter: repository-aligned implementation plan

## Optimization first, then Blender-native advantages

**Reviewed repository:** `caspianmoon/open_painter`  
**Reviewed branch:** `main`  
**Reviewed commit:** `c0b4045f45032e9a74befcbc388dd77a1e5d3bba`  
**Review date:** September 13, 2026  
**Extension version at that commit:** `0.2.0`  
**Manifest minimum:** Blender `5.2.0`  
**Repository-reported test environment:** Blender `5.2.1 LTS`, macOS Apple Silicon.

This document supersedes the earlier generic implementation plan. It is an incremental plan for the existing repository, not instructions to create another implementation.

### Evidence and limitations

This review inspected source code, the test runner, selected integration tests, architecture documents, the material-format specification, and the committed performance report. It did **not** run Blender, execute the repository's test suite, profile a live painting session, or visually certify all 50 materials. Recorded timings below belong to the repository's existing benchmark, not a new measurement made during this review.

Public Blender documentation was researched as well. Search-indexed official documentation confirms relevant GPU and geometry capabilities. Some direct API-documentation page requests failed, so exact version-specific signatures must be established by the capability tests below. The official Blender source documentation on threading was read directly. An API's existence does not prove that a proposed implementation is interactive or production-ready.

If HEAD has moved, first inspect the diff from the reviewed commit. Preserve newer fixes and update the baseline inventory before implementing this plan. Do not reset the repository to the reviewed commit.

---

# 1. Instructions for Codex

## 1.1 Non-negotiable constraints

1. Work in the existing `src/open_painter/` package. Do not create a second add-on, revive `native/` or `prototypes/`, or restore archived GPU-dock interfaces from `backups/`.
2. Preserve `Scene.open_painter`, existing UUIDs, layer ordering, folder semantics, material-slot assignments, and the native Blender UI arrangement.
3. Keep the existing CPU texture evaluator as the reference implementation. Improve it incrementally; do not replace it with an enormous material shader graph.
4. Keep the fixed-size display material. Procedurals, filters, masks, and generators remain texture operations. Geometry Nodes can produce geometry or supply explicit data, but must not become a hidden second authoring system.
5. Preserve the actual `.openpaintmaterial` format. The current writer uses version 2 and reads versions 1 and 2. Do not introduce the earlier proposed `.opmaterial` extension as a replacement.
6. Preserve all 50 catalog materials, legacy presets, 18 smart masks, 16 effects, procedural/filter identifiers, and saved parameters. Existing numerical behavior is the compatibility baseline, even when a new algorithm would look better.
7. No silent reductions of authored texture resolution, filter quality, paint precision, or export fidelity. Lower-resolution previews must be explicit and separate from authoritative output.
8. No arbitrary Python execution from asset packages. Preserve import limits, validation, rollback, and safe archive handling.
9. No background Python threads that remain active while Blender operates. Use cooperative main-thread jobs first. Any independent numerical worker must be an isolated process with explicitly owned buffers and no Blender API access. [B3]
10. Do not label a feature complete because an operator, enum entry, screenshot, or synthetic test exists. Require the acceptance tests in this plan.
11. No network service, account, subscription, or AI dependency in the core extension.
12. Do not modify user meshes, original materials, unrelated nodes, UVs, or files destructively without an explicit operation and recovery path.

## 1.2 Development method

For each work package: reproduce the problem, add a failing test or benchmark, implement the smallest compatible change, run relevant regressions, and record before/after results. Separate structural refactors from mathematical changes. Use feature flags for incomplete backends.

Maintain `docs/implementation-status.md` with: task, status, affected files, test command, result, measured performance, and limitations. Distinguish **implemented**, **tested in Blender**, **visually inspected**, and **performance-validated**.

---

# 2. What already exists: retain and extend

“Present” below means supported by the inspected code and repository documents. It is not a claim that this review independently passed the runtime tests.

| Area | Existing implementation | Updated action |
| --- | --- | --- |
| Project persistence | Scene-owned project, texture-set and layer IDs, assignments, packed images | Extend with migrations, not replacement models |
| Layer system | Fill, Paint, Folder; isolated and passthrough groups; per-channel opacity/blend | Preserve composition and add dependencies/anchors later |
| Channels | Base Color, Roughness, Metallic, Normal, Height, AO, Emission, Opacity | Optimize internal representation without removing channels |
| Evaluation | CPU/NumPy, 16-row work blocks, lazy paint allocation, constants broadcast | Add measured incremental execution and memory accounting |
| Invalidation | Channel-specific output dirtiness, edit serials, debouncing and cancellation | Refine resource revisions and dependency scope |
| Caching | Persistent procedural/mask raster cache, 256 MiB per texture set | Introduce project-wide accounting and separate geometry lifetimes |
| Native painting | Blender brush engine, one channel at a time, alpha/mask support, stroke detection | Retain native workflow; optimize transfers and add material strokes separately |
| Library | Documented 50 materials/229 editable layers, 16 effects, 18 masks, 65 sources, 52 filters | Validate quality and performance; do not rebuild the catalog |
| Projection | UV and CPU triplanar; triplanar currently uses base-mesh geometry | Share evaluated geometry with the utility baker |
| Mesh maps | Normal/AO; convex-crease curvature; evaluated position/normals/IDs/UV maps/thickness | Add diagnostics, provenance and explicit analysis jobs |
| Display material | Fixed-size managed nodes displaying texture outputs; scalar surface settings | Keep stable; do not regenerate shader effects on edits |
| Export | Blender channels, UE5, Unity Standard Metallic, custom packing; 8/16-bit PNG/TIFF | Extend profiles and mesh handoff after regression coverage |
| Smart-material files | Versioned JSON/ZIP, EXR assets, safe validation/rollback | Add optional fields through compatible migrations |
| UI | Native panels, UILists, AssetShelf, editor-subtype adapter | Preserve; do not replace with a custom modal overlay |
| Tests | Unit, integration, native UI, packaging tests and historical fixtures | Extend the active runner and add reproducible performance tests |

Relevant sources: [R1], [R2], [R3], [R4], [R5], [R6], [R7], [R8], [R9], [R10], [R11], [R12].

## 2.1 Work not yet established as complete

The current documentation explicitly excludes anchor/reference layers, global adjustment layers, simultaneous multi-channel material strokes, and UDIM authoring. It also documents limited cage support, synchronous CPU baking, base-mesh triplanar evaluation, and no automatic update of baked world-space information when geometry changes. [R1], [R3]

Geometry flakes, moss strands, editable weld geometry, inter-object exposure analysis, animation wear analysis, and coordinated environment histories are future work. Existing effects named Moss, Rust, Wet Surface or Rust Flakes do not establish these geometry features: inspected effect recipes create editable texture layers. [R13]

Anisotropy and coat parameters are currently texture-set display settings, not independently painted layer channels. Preserve this limitation until expanded end-to-end. [R3], [R9]

---

# 3. Audit findings that determine the new priority order

## F01. The main bottleneck is already measured

The committed `docs/research/texture-performance-0.2.0.json` records Worn Painted Metal at 2048², six layer records:

| Measurement | Recorded result |
| --- | ---: |
| Initial eight-channel evaluation | 24.718 s wall time |
| Initial CPU time | 23.179 s |
| First Base Color evaluation | 19.912 s wall time |
| Roughness-only edit | 2.078 s wall time |
| Maximum cooperative step | 42.404 ms CPU, 189.686 ms wall time |
| Scalar cache | 144 MiB |
| Maximum process RSS | 919.328 MiB |

This fixture uses synthetic AO/curvature and excludes mesh baking and viewport rendering. The first channel also pays for shared source generation, so its cost is not evidence that Base Color blending alone takes 19.9 seconds. The record lacks enough hardware/environment detail for portable timing assertions. Reproduce it before changing algorithms. [R4]

The older constant-fill benchmark in `TESTING.md` is a different workload. Do not use its roughly 0.05-second roughness edit to describe procedural-material performance. [R10]

## F02. Output buffers are float RGBA even for scalar channels

`core/image_manager.py` creates float-buffer images and reads/writes full RGBA arrays. `LayerEvaluator.channel_steps()` allocates `(n, n, 4)` for full output, including roughness and metallic. [R5], [R6]

Raw float32 storage arithmetic, excluding copies, packing, GPU textures, undo and Python objects:

| Square resolution | One scalar plane | One RGBA buffer | Eight RGBA outputs | Position + normal RGB fields |
| --- | ---: | ---: | ---: | ---: |
| 2048² | 16 MiB | 64 MiB | 512 MiB | 96 MiB |
| 4096² | 64 MiB | 256 MiB | 2 GiB | 384 MiB |
| 8192² | 256 MiB | 1 GiB | 8 GiB | 1.5 GiB |

These are calculated buffer sizes, not measured Blender residency. Even perfect caching cannot make eight fully resident 8K float RGBA images cheap.

## F03. Regional evaluation still ends in a whole-image transfer

The regional path re-reads the complete output image, recomposes changed rows, then calls `image_manager.write()`. That function uses full-buffer `foreach_set()` followed by `image.update()`. CPU regional work is useful, but it is not a partial GPU upload implementation. [R5], [R6]

Do not promise a documented arbitrary subrectangle update for a Blender Image datablock without proving one on the target build. GPU textures and Blender Images are different resources. [B1], [B2]

## F04. The filter fallback is broader than necessary

`evaluator.py` disables regional evaluation when it finds any enabled channel effect or any mask FILTER entry for that channel. This includes pointwise operations such as Levels. `channel_eval.py` already has a neighborhood-operation classification, but the top-level region decision does not use an exact dependency/footprint contract. [R5], [R14]

Replace this with operation-specific affected-region propagation. Do not simply remove the fallback: blur, warp and other spatial operations really need additional input pixels.

## F05. Geometry is invalidated by unrelated edits

`raster_cache.for_texture_set()` drops `geometry` whenever the texture set's edit serial changes. Triplanar procedural keys also depend on that serial. A material-value edit can therefore invalidate geometry-derived work even when the mesh, transforms and UVs are unchanged. [R7], [R14]

Separate mesh, topology, UV, transform, assignment, source-pixel and procedural revisions.

## F06. The 256 MiB cache is not a project memory ceiling

Each texture set has its own cache. Geometry is excluded, current filter workspaces and outputs are excluded, and a single required cached raster may exceed the configured budget. Multiple sets multiply the possible cache allocation. [R7]

A global budget must distinguish evictable caches, required working memory and persistent Blender resources. Never “fix” this by discarding editable source pixels.

## F07. Native painting requires full-image detection in the current bridge

`paint_session.detect()` obtains a full raster, compares rows against a previous full raster, and retains reusable buffers. Comparison temporaries are bounded, but image transfer remains whole-buffer. Painted masks are excluded from the current regional-output shortcut. [R15]

Preserve the existing no-repeated-idle-scan behavior. Do not invent a native brush callback giving perfect dirty rectangles; the installed Blender API needs a capability test first.

## F08. Two geometry implementations have different fidelity

`painting/projection.py` uses `obj.data` and vertex normals. `baking/maps/utility.py` uses evaluated meshes, bulk array reads, render UVs, corner normals, inverse-transpose normal transforms, and cleanup via `to_mesh_clear()`. [R16], [R17]

Extract reusable snapshot/rasterization services from the stronger path. Do not direct Codex to write another unrelated mesh walker.

## F09. Scheduler identity and failure-state handling need tests

The raster cache is keyed by scene pointer plus texture-set ID, but scheduler dictionaries use texture-set ID alone. Test duplicated scenes retaining IDs before asserting an actual user-visible collision. Standardize scene-scoped runtime identity as part of the refactor. [R7], [R8]

The scheduler exception path sets `ts.dirty = False` after reporting an error. Replace this clean/dirty ambiguity with an explicit failed state and a retained last-good result. Do not clear unresolved output work merely to stop repeated exceptions. [R8]

## F10. Saving and reloading are separate performance workloads

Save currently flushes changes and packs every managed image with data. Load/undo/redo clear runtime caches and mark outputs dirty. Preserve recovery correctness, but benchmark save/reopen separately and avoid packing unchanged resources when a trustworthy content revision permits it. [R8]

## F11. Existing source-reuse tests must remain authoritative

`tests/integration/persistent_texture_cache.py` verifies zero procedural/mask regeneration for a constant roughness edit, invalidation after parameter/map/UV changes, forced refresh for external in-place image edits, and lifecycle cleanup. Keep these tests and expand them. [R11]

## F12. The current test runner is not the entire historical test inventory

The runner explicitly lists active integration/UI suites. Several historical GUI/packaging tests mentioned in `TESTING.md` are not automatically selected by its current explicit list. Audit coverage before claiming that “all tests” includes every documented workflow. [R10], [R12]

---

# 4. Execution architecture: improve what exists

Keep this direction:

```text
Scene.open_painter authoring state
        |
        v
Immutable evaluation plan + resource dependency graph
        |
        +--> existing CPU reference evaluator
        +--> optimized CPU tile evaluator
        +--> optional GPU execution backend, only after a successful spike
        |
        v
Authoritative channel images + existing fixed display material
```

Geometry integration is an explicit side pipeline:

```text
User mesh / evaluated snapshot / chosen scene influencers
        |
        v
Versioned geometry fields or analysis textures
        |
        v
Existing mask + layer composition
        |
        +--> channel images
        +--> published mask image/attribute
                    |
                    v
Managed Geometry Nodes helper effects
```

Generated effect geometry is excluded from its own source analysis by default. No circular dependency from geometry output back into the texture that generates it.

## 4.1 Resource identity and revisions

Add revisioned identities for:

- project/scene and texture set;
- layer structure, ordering, folder behavior and parameters;
- image pixels, not just image pointer and size;
- mesh topology, vertex positions, render UVs, corner normals and assignments;
- object transform, evaluation frame and selected modifier state;
- baked-map provenance;
- influencer collection membership and transforms;
- preview resolution and operation/backend version.

Pointers can be used for transient lookup. Persist stable IDs where cross-save references are required. Never keep a raw evaluated mesh or BVH beyond its valid snapshot lifetime without explicit ownership.

Internal image writers increment a pixel revision. External edits still have a visible Refresh command; relevant Blender events may mark them stale, but do not hash every image on every timer tick. A dependency handler should mark revisions or queue work, not perform heavy evaluation inside the handler.

## 4.2 Evaluation plan, not a shader compiler

Create pure-Python descriptors from persistent properties. Resolve visible layers, channel flags, source dependencies, folder behavior and operation contracts once per structural revision. Avoid repeated property searches inside every row tile.

A changed roughness value should reuse source masks and geometry. A changed AO map should invalidate its consumers, not unrelated wood grain. A folder opacity change can reuse a valid isolated-group result. Passthrough groups depend on the incoming backdrop and must not be cached as backdrop-independent images.

Do not cache every prefix at every layer by default. Select checkpoints using cost/reuse estimates and the global budget, otherwise intermediate storage grows with layers × channels × tiles.

## 4.3 Operation contract

Each filter, procedural and generator needs metadata:

```text
id + version
input/output channel layout
pointwise / finite-neighborhood / bounded-warp / global
required input footprint for a requested output rectangle
forward affected-region propagation
boundary mode: clamp / wrap / mirror / transparent
reference-resolution parameter semantics
alpha and normal-vector rules
geometry/map dependencies
estimated transient memory
CPU/GPU support
```

Levels is pointwise. Gaussian blur needs a defined finite support and separable passes. Morphology needs radius-dependent support. A bounded warp needs the displacement bound plus interpolation support. A global reduction or unbounded transform can explicitly request the whole image. A name containing “Histogram” does not prove it needs a global histogram: inspect the implemented math.

Compose footprints through ordered stacks. Normal conversion needs derivative neighborhoods. Padding must distinguish texture-edge behavior from UV-island gutters. Do not silently make existing image filters geodesic across mesh seams.

---

# 5. Ordered work packages

## M0. Baseline, capability probes and regression coverage

**Goal:** establish reproducible evidence before optimizing.

### Existing files to use

`blender_manifest.toml`, `scripts/run_tests.py`, `scripts/build_extension.py`, `TESTING.md`, `tests/integration/persistent_texture_cache.py`, and `docs/research/texture-performance-0.2.0.json`.

### New files to add

```text
scripts/benchmark_textures.py
scripts/probe_blender_capabilities.py
tests/performance/fixtures.py
docs/performance/baseline.json
docs/implementation-status.md
```

### Tasks

Record commit, OS, CPU/SoC, RAM, Blender build/hash, NumPy version, renderer/backend, resolution, active channels, material, mesh complexity, map origin, cold/warm status and whether viewport rendering is included.

Benchmark at least: existing constant fills; Worn Painted Metal; Abandoned Machinery; a mask with Gaussian Blur; a bounded warp; a painted mask; triplanar with modifiers; three texture sets; save/reopen; and export. Start at 512 and 2K. Run 4K/8K only after memory preflight.

Use repeated runs and median/p95 values where sample count supports them. Record CPU and wall time separately. Instrument source generation, masking, filtering, blending, geometry extraction, pixel reads, pixel writes, packing, cache hits/misses and allocated bytes. Record the first usable preview separately from final-quality completion.

Use a real GUI stroke fixture for painting latency. Output revision time is not proof of display frame time. Measure cold and warm behavior separately; do not let a 512 preview contaminate the cold 2K benchmark.

Probe GPU context creation, supported shader creation and texture formats, readback, Image transfer, headless behavior, evaluated geometry, corner normals, Geometry Nodes attributes, and native tiled-image paint/bake addressing on the actual supported Blender build. Produce a machine-readable capability report. Unsupported capabilities disable experimental paths rather than breaking installation.

Audit which historical tests should enter the active runner, especially real viewport painting, packaging and save/reopen. Add CI jobs for pure logic and background Blender tests; GUI/Metal validation remains a separate explicit run where no suitable CI host is available.

### Acceptance

The existing test commands run in isolated user resources, the packaged extension validates, and a fresh installation opens old projects. Baselines are measured, not invented. Benchmark fixtures and outputs are reproducible. Record any existing failures before modifying behavior.

---

## M1. Correctness-safe invalidation, identity and cache lifetimes

**Depends on:** M0.

### Modify

`evaluation/cache.py`, `evaluation/raster_cache.py`, `evaluation/mask_eval.py`, `evaluation/channel_eval.py`, `core/layer_stack.py`, `core/image_manager.py`, and relevant callbacks in `properties/`.

### Add

```text
evaluation/dependencies.py
evaluation/resource_keys.py
evaluation/metrics.py
tests/integration/resource_invalidation.py
tests/integration/multi_scene_scheduling.py
```

### Tasks

1. Standardize scheduler keys to scene-scoped identities. Cover copied scenes, multiple open windows, removal and reload.
2. Separate geometry revisions from authoring edit serials. Retain geometry after color/roughness changes and invalidate it after the relevant mesh, UV, transform, modifier, frame or assignment changes.
3. Add source-image pixel revisions for Open Painter writes and make stale external data explicit.
4. Replace broad baked-map signatures with actual map dependencies. Keep a conservative path for unknown/custom generators.
5. Snapshot job parameters and target revision. Cancel superseded work before publication. Keep the last good image while evaluating.
6. Add explicit queued/running/stale/failed/ready states. Errors remain visible and retryable; they must not masquerade as clean output.
7. Preserve existing source-reuse and force-refresh semantics.

### Acceptance

A roughness constant edit causes zero source regeneration and zero geometry rebuilds. Replacing AO affects only its consumers. In-place writes through the managed API invalidate correct consumers. Moving a triplanar object changes the result; unrelated scene movement does not. Undo during a pending job cannot publish stale pixels or access invalid data. Two copied scenes cannot share a pending job accidentally.

---

## M2. Memory accounting and exact CPU optimization

**Depends on:** M1.

### Modify

`evaluation/evaluator.py`, `evaluation/channel_eval.py`, `evaluation/mask_eval.py`, `evaluation/raster_cache.py`, `core/image_manager.py`, and `texture_engine/filters.py`.

### Add

```text
evaluation/memory.py
evaluation/buffers.py
evaluation/operation_contracts.py
evaluation/tiles.py
tests/unit/test_filter_footprints.py
tests/integration/dirty_tiles.py
tests/integration/memory_budget.py
```

### A. Project-wide memory manager

Track evictable cached arrays, pinned in-flight work, geometry fields, image snapshots, output buffers, GPU resources if present, and persistent Blender image estimates. Keep per-set fairness, but enforce one configurable cache allowance across all sets.

The cache allowance is not a hard cap on Blender's whole process. Report both controlled cache use and estimated mandatory allocations. Account for shared array ownership without double-counting views; avoid counting a tiny NumPy view as though it owns an entire new buffer.

An oversized cache entry must be streamed, skipped for caching, or admitted through a documented temporary-memory reservation. It must not silently evade the cache limit. Protect editable source data from eviction. Release retired job buffers and temporary geometry promptly.

Add preflight for resolution changes, heavy filters, duplication, UDIM additions, and geometry realization. Explain what consumes memory and allow cancellation before allocation. Do not estimate compression ratios as guaranteed RAM savings.

### B. Typed working buffers

Use scalar float32 internally for scalar channels and masks, RGB for color/normal data, and a separate coverage plane when needed. Preserve the existing Blender Image boundary initially, even if that means RGBA expansion for publication.

Use broadcast constants and pooled buffers; reduce temporary concatenations and repeated meshgrids. Use in-place NumPy operations only when ownership proves the source is not shared. Keep normals normalized and preserve premultiplied paint/folder math.

Do not enable float16 accumulation globally. Optional half-precision cache/GPU storage requires measured error thresholds and explicit format metadata. The existing float32 path remains the oracle.

### C. Spatial incremental evaluation

Replace the blanket filter/full-image fallback with operation contracts. Move from a single changed-row interval toward a dirty-tile set; begin with bounded full-width strips if that is the safer incremental change.

Propagate changes through finite halos, filter stacks, folder masks, normal derivatives and reference dependencies. Skip disabled branches. A local painted-mask edit should invalidate dependent channels in the affected area, not automatically every pixel of every channel.

Keep full-image fallbacks for operations without a proven finite footprint. Explicitly test wrap/clamp behavior, image edges, non-repeating projections, nested groups and sparse distant strokes. Do not assume screen-space brush bounds map to one UV rectangle.

### D. Reuse decoded source images and masks

Avoid decoding/resampling the same unchanged source separately for each channel. Use direct slices for same-grid masks where mathematically equivalent, rather than bilinear sampling every texel. Preserve handling of different resolutions, UV transforms, alpha and color space.

The old regional path performs a complete output read before patching. Maintain a revisioned CPU output buffer where the memory manager permits it; otherwise fall back honestly. Do not add an unbounded duplicate of every Blender image.

### Acceptance

Tiled and full CPU results match within defined float tolerances across all blend/filter fixtures. Tolerance changes require documented numerical reasoning, not looser tests to hide seams. Output is independent of tile partitioning, scheduler yield timing and cache eviction order.

A pointwise filter preserves regional evaluation. A finite blur updates only the provably affected region. A large warp correctly widens the region or falls back. Cache use across three sets stays within the configured allowance, with separate reporting of mandatory buffers. Cancellation releases temporary allocations.

---

## M3. Responsive preview and publication policy

**Depends on:** M2.

### Modify

`evaluation/cache.py`, `evaluation/evaluator.py`, `painting/paint_session.py`, `core/image_manager.py`, and existing native UI panels.

### Add

```text
evaluation/preview_policy.py
tests/integration/preview_authority.py
tests/integration/save_during_evaluation.py
```

### Tasks

Provide explicit preview quality modes, for example Full and Interactive. In Interactive mode, a 512/1024 proxy may update during parameter dragging, followed by authored resolution after settling. Show the current preview resolution and pending full-quality state.

Never downscale editable paint images merely to speed a preview. Keep world-space procedural scale, UV coordinates and final-pixel filter radii consistent across preview resolutions. Height-to-normal strength must not change accidentally with proxy size. ID and mask resampling need channel-appropriate behavior.

Separate last-good authoritative outputs from disposable preview proxies. Keep output identities stable where existing persistence depends on them. Save/export/render requests drain or restart work against an immutable final-resolution revision. If final evaluation fails, retain recoverable source state, surface the failure and do not export a proxy as final output.

Adapt tile size to measured operation cost and memory. The existing six-millisecond scheduler budget is cooperative: one non-yielding operation can still exceed it. Split expensive work where feasible and log indivisible upload/readback costs separately.

Prioritize the active texture set and active channel with fair scheduling for other work. Coalesce repeated writes so each changed channel is published once per completed snapshot, not once per layer/tile. Retain no repeated idle scans. Tune live paint detection using measured transfer cost, not a fixed aspiration of 30 full-image reads per second.

Optimize packing only when pixel revisions reliably establish that an already packed resource is unchanged. Keep authoring state safe even if preview flush fails during Blender's save lifecycle.

### Proposed performance targets, not promises

On the same reference hardware and fixture used for the reproduced baseline:

| Workload | Initial engineering target |
| --- | --- |
| Constant-only roughness edit | No regression beyond measured noise |
| Worn Painted Metal 2K cold evaluation | At least 2× faster than reproduced baseline |
| Same material, warm roughness constant edit | At least 3× faster than reproduced baseline |
| First useful Interactive preview | Aim for under 200 ms on the designated preview fixture |
| Small CPU compute slices | p95 below 12 ms; report all longer steps |
| Idle painter without edits | Zero repeated canvas reads and zero output writes |
| Full Image transfer | Report separately; do not hide it in a compute-only metric |

These are development goals, not cross-hardware guarantees or flaky absolute unit-test thresholds. If transfer cost prevents the goal, document the limit and use the explicit proxy path. Release claims must use measured end-to-end results.

### Acceptance

Rapid edits cannot publish an older result after a newer one. Save/export immediately after a stroke includes its final pixels at requested resolution. Proxy and final states remain distinguishable. Native navigation, T/N, brushes, undo, target switching and material-slot protection still work.

---

## M4. Optional GPU backend feasibility gate

**Depends on:** M2/M3. This is a bounded experiment, not a mandatory rewrite.

Blender exposes GPU shaders, textures, framebuffers/offscreen facilities and readback. Apple systems translate supported GLSL through Blender's Metal path. This supports an experiment, not an assumption of a seamless zero-copy painting engine. [B1], [B2]

### Add behind a disabled-by-default feature flag

```text
evaluation/backends/base.py
evaluation/backends/cpu.py
evaluation/backends/gpu_experimental.py
tests/integration/gpu_parity.py
```

Implement only a representative vertical slice: fill, mask, source-over, Levels and separable blur. Preserve the native display material and UI. Do not turn the experiment into another viewport renderer.

Measure the complete path: native painted Image -> GPU resource -> operations -> necessary readback -> authoritative Blender Image -> visible update. Include shader compilation, resource creation and memory use separately from warm execution.

Keep data on one backend for a compatible chain. Avoid a GPU-to-CPU-to-GPU round trip between every filter. Unsupported operations must explicitly fall back without changing results.

Use documented `gpu` facilities available on the target build, not raw `bgl` or undocumented graphics pointers. Probe compute support instead of assuming it; an offscreen fragment-pass implementation is a possible alternative. Respect context lifetime, restore graphics state and release resources on editor/window closure and unregister. Headless tests use the CPU path when no GPU context is available.

### Promotion gate

Promote only if representative end-to-end workloads improve materially, for example at least 2× including transfers, while numerical/normal parity, memory bounds, undo and persistence pass. Mac/Metal is a required validation target because that is the repository's reported platform. A fast shader microbenchmark alone fails this gate.

If it fails, retain the spike as experimental or remove it cleanly; ship the CPU improvements. Do not block the rest of the roadmap on GPU access that the public API does not provide.

---

## M5. Shared evaluated geometry, map provenance and bake diagnostics

**Depends on:** M1/M2. Can proceed independently of a successful GPU experiment.

### Existing code to reuse

`baking/maps/utility.py`, `baking/maps/curvature.py`, `baking/bake_manager.py`, `baking/cycles_baker.py`, `painting/projection.py`, and `core/validation.py`.

### Add

```text
geometry/snapshots.py
geometry/uv_rasterizer.py
geometry/provenance.py
baking/diagnostics.py
baking/jobs.py
tests/integration/evaluated_projection.py
tests/integration/bake_diagnostics.py
```

Extract the utility baker's safe evaluated-mesh acquisition and cleanup. Use render UVs, corner normals, bulk array access and consistent transform handling. Cache immutable arrays or bounded raster tiles, not dangling evaluated-mesh references.

Unify triplanar inputs with this service. Define behavior for topology-changing modifiers: use evaluated UVs if valid; warn when modifiers remove or invalidate required UV/material assignments. Never force destructive modifier application simply because the current projection path is limited.

Add baked-map provenance: target/source identities and revisions, UV identity, transforms, frame, bake settings, algorithm version and resolution. Mark stale maps in the UI. Never rebake everything automatically while an artist is dragging a mesh.

Add diagnostics in increasing cost tiers: missing UVs/assignments, degenerate faces/UVs, unsupported tile/overlap conditions, scale mismatch, missing high/low pairs, custom-cage topology, then optional sampled ray/cage analysis. Do not report sampled ray-miss percentages as exact coverage. Mirrored/overlapping UVs can be deliberate; warn according to workflow instead of claiming every overlap is invalid.

Normal/AO bakes currently force CPU and call Cycles synchronously. Preserve state restoration. Queue work between objects/maps and permit cancellation at supported boundaries. Do not label a modal wrapper around a blocking bake call “non-blocking.” A cancellable external Blender bake process is a separate option requiring scene snapshotting, stale-result rejection, disk-space checks and cleanup.

CPU remains a fallback. GPU baking is an optional capability-tested choice, not a universal speed guarantee.

### Acceptance

Modifier-aware triplanar and utility maps agree on geometry/UV coordinates. Flat/sharp/custom normals, non-uniform and negative scale, multiple assigned slots and object movement are covered. Source objects are unchanged after success, cancellation and failure. Diagnostics point to selectable problem regions where feasible.

---

## M6. Scene-aware masks: first Blender-native flagship

**Depends on:** M5 and the dependency/memory systems.

### Add

```text
geometry/influencers.py
geometry/queries.py
generators/scene_fields.py
properties/influencer.py
tests/integration/scene_masks.py
```

### First supported fields

- Upward/downward orientation using transformed normals.
- Height against an explicit object/collection reference and stored normalization bounds.
- Distance to chosen ground or another object/collection.
- Contact-proximity bands around chosen parts.
- Directional occlusion/exposure against an explicit occluder collection.
- Artist-defined radial/directional heat, moisture and soot influence fields.
- Named float/color attribute and vertex-group inputs.

Geometry Proximity provides closest-location/distance operations. Ray queries and evaluated geometry can underpin exposure tests. Geometry Nodes or cached BVH analysis are possible backends, but each field must publish an explicit texture/attribute contract. [B4], [R17]

### Sampling and accuracy

A field stored only on a low-poly mesh cannot represent a narrow rust halo around a tiny bolt accurately. Support an explicit sampling mode: coarse geometry-domain preview or UV-space sampled analysis. Display the analysis resolution separately from texture resolution. Use a bounded sampling grid and cached spatial acceleration; do not issue millions of Python ray calls on every viewport frame.

Distance alone is not collision, friction, moisture or physical corrosion. Contact wear is an authored proximity model unless motion/contact analysis has actually been performed. Rain exposure is a directional visibility approximation, not fluid flow.

Use explicit object/collection inputs. Exclude the source surface from self-intersection tests where appropriate, offset ray origins by a scale-aware epsilon, and handle transforms consistently. No whole-scene scan by default.

Expose Live Preview, Update Analysis and Freeze Analysis. Live means debounced bounded updates on relevant changes, not unlimited every-frame recomputation. Source deletion produces a clear missing-input state.

### First demo and acceptance

A painted panel, movable bolt and ground plane: move the bolt, update a dirt/rust halo without rebaking unrelated Normal/AO; rotate the panel and update orientation-driven dust; remove the bolt and report the missing reference safely. Changing paint color reuses every geometry query. Reopening a file preserves references and the last valid analysis.

---

## M7. Anchors, references and published masks

**Depends on:** M1/M2; publish stable mask outputs before general geometry effects.

### Add

```text
evaluation/references.py
core/published_outputs.py
tests/integration/reference_layers.py
```

Implement a small explicit reference model: source layer/mask, channel, pre/post-filter stage and revision. Start within one texture set. Add cycle detection before allowing cross-set references.

Enable a damage mask to control coating loss, rust placement and later geometry density without copying three unrelated editable masks. Published results are derived resources, not replacements for the source stack.

Respect isolated and passthrough folder semantics. Define duplication/remapping and missing-reference behavior. No automatic sharing of mutable paint buffers between duplicated independent layers.

Texture-to-attribute and attribute-to-texture are sampled conversions with a stated domain/resolution, not lossless equivalence. Sparse mesh attributes will lose fine texture detail. Freeze a snapshot or use explicit dependency boundaries when a round trip would create a cycle.

### Acceptance

One painted damage mask drives multiple consumers; an edit invalidates only those consumers. Cycles are rejected. Save/reload and `.openpaintmaterial` round trips either preserve supported references or explicitly bake/embed a snapshot with user-visible loss of editability. Never silently drop an anchor.

---

## M8. Geometry effects: second Blender-native flagship

**Depends on:** M5/M7. Do not require the GPU experiment.

### Add

```text
geometry_effects/manager.py
geometry_effects/rust_flakes.py
geometry_effects/moss.py
geometry_effects/weld.py
geometry_effects/limits.py
tests/integration/geometry_effects.py
```

### Architecture

Use managed Geometry Nodes node groups and helper objects/collections with owner IDs. Initially keep effect geometry separate from the source object. The source object's analysis never includes generated helpers unless an explicit frozen workflow is chosen.

An effect consumes a published mask or supported attribute and optional parameters. Use instancing for repeated flakes/clumps. Delay realization until needed for export. Bound total estimated instances, triangles and memory at object and project level. Distinct viewport and render quality are explicit settings; rendering does not get an unbounded exception.

Preserve source UV access when sampling a texture mask. Sampling on point/face/corner domains needs tests. Avoid an enormous subdivision of the source mesh just to match a 4K mask.

### Initial features

**Rust flakes:** masked scatter, size/thickness/lift, stable seed and material assignment. It produces actual helper geometry, unlike the existing RUST_FLAKES texture procedural.

**Moss:** instanced clumps or curves driven by a mask, with bounded density and orientation. Strand detail is a quality option.

**Editable weld:** selected curve/edge path defines bead geometry. Heat tint and soot are separate published fields. Do not promise reliable automatic weld detection between arbitrary intersecting meshes.

**Damage-depth prototype:** a painted scalar mask exposes paint, primer and substrate through defined logical depth thresholds. Optional displacement on a managed copy/helper is separate. This is not yet a true cut through physical volumetric coatings.

Defer snow volume, peeling-paint boundary extraction and wood splinters until this pipeline proves stable. They can reuse the system later.

### Acceptance

Density zero creates no effect geometry. Identical seeds yield reproducible placement on unchanged topology; topology changes have documented reseeding behavior. Moving source objects does not detach effects. Undo/delete/unregister do not remove user geometry. Limits work across multiple objects. Freeze, regenerate and remove are explicit and recoverable.

Baking geometry into textures cannot preserve silhouette or parallax at all views. Export must state that tradeoff rather than promise equivalent results.

---

## M9. Coordinated age, environment and variations

**Depends on:** M6/M7; geometry effects are optional consumers.

### Add

```text
core/environment_profiles.py
core/material_states.py
core/variation_rules.py
tests/integration/material_states.py
```

Implement normalized Age, Usage, Maintenance and an environment profile as deterministic artist controls. Each compatible material has separate response curves for dirt, fade, scratches, oxidation and coating loss. Do not multiply every effect by the same age value.

A displayed age in years is an artistic narrative parameter unless the material model is calibrated. No physical decay claims. Semantic tags such as Handle, Wheel or Exhaust are user inputs, not automatically inferred facts.

A collection profile coordinates direction, palette and environment while per-asset seeds create variations. Preserve manually painted layers and local overrides. Save resolved parameters and profile versions for reproducibility.

Important current-architecture constraint: objects sharing the same UVs and one texture-set image cannot receive different baked per-object wear merely by changing a hidden object seed. Offer shared identical output, separate texture sets, or a deliberate atlas workflow. Estimate additional memory before duplicating outputs.

Start with Clean/Used/Damaged parameter snapshots and bounded variation ranges. Material inheritance with parent-update merging is later work after override semantics stabilize.

### Acceptance

Three assets age coherently but not identically when authored with separate outputs. The same seed and profile version reproduce results. Switching states does not erase strokes or change unrelated objects. Old material files load with neutral profile defaults.

---

## M10. Material strokes, decals and curve-based marks

**Depends on:** stable M2/M3/M7.

Retain existing native single-channel painting as a supported mode. Add a separate, explicitly named Material Stamp/Material Stroke workflow.

Start with deterministic stamps writing coordinated Base Color/Roughness/Metallic/Height and coverage. One gesture produces one logical undo operation. Native erase, clone, smear and soften cannot simply be replayed across channels and assumed equivalent.

Before continuous multi-channel painting, prove surface hit -> triangle -> UV mapping, pressure/spacing, seam behavior, material-slot restriction, occlusion/backface rules, undo and save. Do not intercept normal navigation or replay synthetic mouse input to pretend simultaneous painting exists.

Add non-destructive decal transforms using existing fill/projection infrastructure where possible. Optional mesh decals have explicit depth offset, UV/material ownership and export behavior.

Editable scratch curves can provide local fields and optional geometry, with projection/shrinkwrap and fallback for missing targets. True topology-cutting gouges remain experimental.

### Acceptance

A stamp writes coordinated channels to the same surface area, preserves untouched pixels, and undoes atomically. Save/export immediately after the gesture includes all channels. A seam fixture and multi-material object demonstrate behavior rather than relying on a flat plane.

---

## M11. Production export, UDIM and atlasing

**Depends on:** M2/M3/M5 and reliable snapshots.

### Export extension

Retain current UE5, Unity Standard Metallic, Blender and Custom outputs. Preserve byte-level packing, linear-data handling, normal convention selection, no-overwrite behavior and independent RGBA channels. Do not route packed data back through a color-save path that changes RGB when alpha is zero. [R1], [R2], [R10]

Add Godot, glTF and specific Unity render-pipeline profiles only with current official profile documentation and tests. Do not silently equate Unity Standard, URP and HDRP. Mesh export is a separate capability from existing texture export.

Export an evaluated staging copy, optional generated geometry, textures and a machine-readable manifest. Restore all source state. Clearly select actual geometry, baked approximation or omitted effects. Do not promise generated Unreal material assets unless the integration actually produces and tests them.

### UDIM: a dedicated subsystem

The existing code assumes one square 0-1 texture in many places. Creating a Blender tiled image alone does not implement UDIM painting.

Introduce tile identity in source keys, output handles, caches, painting targets, map provenance, projection, analysis, filters, package paths and export names. Begin with two tiles, 1001 and 1002, at equal resolution. Require sparse allocation and bounded tile residency.

Prove actual native paint targeting, save/reopen, tile pixel access, mesh-map baking and export on the supported Blender build before changing the UI to advertise UDIM support. Define whether filters clamp within a tile or intentionally sample adjacent tiles. Do not imply that adjacent UDIM numbers are always adjacent painted surface regions.

### Atlasing and density

Implement measurement first: assigned surface area, UV area, texel density, occupancy and gutter estimates. Normalize density/repack only through previewable operations on a new UV set or staging copy. Preserve originals and intentional overlaps.

Atlas multiple props into a new texture set, rebake all selected channels and reassign the staged copies. Geometry-aware effects may need frozen source coordinates. Do not silently change world-space noise when repacking UVs.

### Acceptance

Existing export byte tests still pass. Two tiles survive paint/save/reopen/export without tile mixing. Budget limits work across tiles. Atlas output matches source appearance within documented bake/resampling tolerances and originals remain recoverable.

---

## M12. Advanced offline analysis and remaining extensions

These remain separate optional work packages, not reasons to delay a usable optimized release.

**Animation-driven wear:** sample chosen object pairs over explicit frames; broad-phase bounds before nearest/ray queries; accumulate contact duration and relative tangential motion in stable source coordinates. Proximity alone is not friction. Reject or explicitly handle topology changes. Cache results; no full-scene analysis on every timeline frame.

**Deformation wear:** use selected vertex groups as an inexpensive art-directed mode. Optional strain analysis needs a rest state and sampled deformation; weights alone are not a physical measure of fabric fatigue.

**Boolean-aware surfaces:** start with controlled modifier workflows or explicit cut-surface marking/material indices. Arbitrary modifier-history recovery is not guaranteed. Do not infer a cut face solely because a normal changed.

**Advanced geometry:** snow, peeling paint, splinters and more elaborate displacement reuse M8 limits and ownership. True cross-sections need actual volume/layer geometry, not just shader thresholds.

**Material inheritance:** parent UUID/version plus local overrides, explicit update preview, migration and conflict resolution. No silent changes to finished assets.

**Community scripting:** expose a declarative API over existing authoring state after it stabilizes. Executable add-ons are installed as trusted code; material files remain data-only.

**AI reference fitting:** opt-in later, outputting editable material parameters. A photograph does not uniquely determine roughness, metallic, lighting or hidden geometry. Keep provenance and uncertainty; no AI dependency in the core.

**Performance analyzer/material LOD:** report texture memory estimates, layer/filter costs, effect geometry and backend dependencies. Actual GPU cost needs measurement. Simplified material/geometry variants require explicit visual checks rather than a one-button quality guarantee.

---

# 6. Mapping the earlier 50 Blender-native ideas to this roadmap

This preserves the product direction without treating all ideas as equally ready.

| Earlier idea numbers | Scope | Updated destination |
| --- | --- | --- |
| 1, 2, 17 | Geometry-aware masks, live inputs, topology data | M5/M6; cached analyses, not universal bake-free evaluation |
| 3, 40, 41 | Geometry-changing materials and two-way attribute bridge | M7/M8; explicit one-way dependency boundaries |
| 4 | Material modifier stack | Existing layers/effects; improve current UI rather than duplicate it |
| 5, 9 | Object-to-object and scene-aware dirt | M6 |
| 6 | Physics-driven weathering | M12; consume explicit simulation fields or offline results |
| 7, 8, 10 | Age, environment and semantic usage | M9 with art-directed tags/curves |
| 11, 12 | Animation/deformation wear | M12 |
| 13, 14 | Decals and 3D scratches | M10 |
| 15, 16 | Weld and damage painting | M8/M10 |
| 18, 46 | Boolean/history-based texturing | Controlled M12 workflows; no arbitrary-history promise |
| 19, 20 | Bake/export and engine profiles | Extend existing export in M11 |
| 21, 22, 24 | UDIM, texel density and atlases | M11 |
| 23, 25, 26 | Resolution/LOD optimization and cost analysis | M2/M3, then M12 |
| 27, 28, 29, 30 | States, variations and coordinated asset appearance | M9; respect shared-texture constraints |
| 31, 32 | Layer-aware brushes and coating depth | M8/M10 |
| 33 | True material cross-sections | M12 experimental geometry, not a shader trick |
| 34, 35 | Renderer-aware inspection and lighting | Preserve display material; add reversible inspection after M3 |
| 36, 37, 38, 39 | QA, diagnostics, high/low management and modifiers | Existing foundation plus M5/M11 |
| 42, 43, 44, 45 | Scripting, packages, assets and inheritance | Existing shelf/format first; M7/M12 extensions |
| 47, 48 | AI decomposition/reference matching | M12 optional research |
| 49, 50 | Narrative history and whole-asset aging | M9 plus bounded M12 analysis, not a physical simulator |

---

# 7. Required regression suite

## Numerical and incremental correctness

Preserve isolated/passthrough group behavior, per-channel overrides, premultiplied painting and all existing normal blend modes. Test full-versus-tiled output, local edits at tile boundaries, multi-filter halos, wide warps, texture wrap/clamp, normal derivative edges, zero opacity and empty groups.

Test cache results against a forced evaluation after every supported mutation. Include source-pixel writes without pointer replacement, modifier edits, render-UV changes, object transforms, frame changes and map replacement. Cache reuse must never affect the result.

## Lifecycle and resources

Repeated register/unregister, partial registration failure, save/reopen, undo/redo during a pending job, deleted source images, removed texture sets, duplicated scenes, duplicated layers, editor changes and multiple windows. Count handlers, timers, temporary meshes, cache bytes and generated datablocks before/after.

## Painting and UI

Real 3D strokes, 2D painting, imported alpha, black/white masks, material-slot face protection, target switches, final release pixels, erasing, save/export while work is pending, native T/N and navigation. No idle full-image scan loop.

## Persistence and export

Read version-1 and version-2 `.openpaintmaterial` fixtures, remap IDs, reject invalid graphs/packages, round-trip filters and surface settings, retain imported textures. Keep byte-accurate export tests for packed data, normal orientation, color transfer, alpha zero, filenames and collision rollback.

## Geometry

Sharp and smooth normals, custom corner normals, non-uniform/negative scale, evaluated modifiers, multiple assignments, UV overlaps and missing UVs, explicit influencer deletion, generated-effect exclusion, stable seeds, limits and non-destructive export staging.

## Material quality

All 50 entries must instantiate with declared map requirements, produce finite correctly shaped outputs, remain editable, serialize and export. Numerical distinctness alone is not visual quality. Render a representative subset on a rounded hard-surface object, a flat UV surface and an irregular mesh; inspect wear placement, grain direction, roughness and seams.

Do not claim a material is physically invalid solely because a filtered Metallic pixel lies between zero and one. Mixed coverage and antialiasing legitimately produce intermediate values. Separate numerical bounds, encoding mistakes and physically questionable authoring advice.

---

# 8. Suggested release gates

These are feature groupings, not calendar estimates.

**Release A: performance and reliability**  
M0-M3, current library retained, honest benchmark report, no data migration surprises. This should ship before advanced geometry increases the workload.

**Release B: Blender-aware texturing**  
M5-M7 with modifier-aware projection, diagnostics, scene fields and published masks. Existing exports remain correct.

**Release C: geometry materials and asset states**  
M8-M9 with bounded flakes/moss/weld workflows and coherent art-directed aging.

**Release D: production expansion**  
M10-M11, introducing material strokes, UDIM/atlas workflows and new export profiles only after their respective acceptance tests.

M4 GPU work is conditional. M12 features graduate individually after evidence, not as a large all-or-nothing release.

---

# 9. Immediate task to execute

Start with M0 and the smallest M1 improvements. Do not begin with moss geometry, 50 new presets, a shader rewrite or a new editor.

1. Inspect the current checkout and diff from `c0b4045f45032e9a74befcbc388dd77a1e5d3bba` without resetting it.
2. Run the existing tests and extension validation in isolated resources.
3. Reproduce the Worn Painted Metal 2K benchmark and add detailed phase/memory counters.
4. Add regression tests for geometry reuse after material-only edits, scene-scoped scheduling and failed-job state.
5. Implement the smallest compatible revision/identity changes that make those tests pass.
6. Produce before/after measurements and a factual status report, including unresolved bottlenecks.

Existing commands, using the actual local Blender executable:

```bash
BLENDER_BIN="/Applications/Blender.app/Contents/MacOS/Blender"
python3 scripts/build_extension.py
python3 scripts/run_tests.py --blender "$BLENDER_BIN"
python3 scripts/run_tests.py --blender "$BLENDER_BIN" --gui-only
```

On another platform, change `BLENDER_BIN`. New benchmark/capability scripts must be implemented before invoking them. Do not pretend uncreated command-line flags already exist. Cross-platform GUI arguments in the current runner also need verification.

The first implementation report must contain the exact commit, test commands/results, benchmark context, modified files, measured differences and remaining risks. It must not claim to have completed the whole roadmap.

---

# 10. Source ledger

Repository links are pinned to the reviewed commit. Repository timing/test claims remain repository-reported unless rerun by Codex.

## Repository evidence

- **R1:** [README and current limitations](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/README.md).
- **R2:** [Architecture](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/ARCHITECTURE.md).
- **R3:** [Texture workflow v1, catalog counts and limitations](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/docs/texture-workflow-v1.md).
- **R4:** [Raw 0.2.0 performance report](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/docs/research/texture-performance-0.2.0.json).
- **R5:** [LayerEvaluator and regional fallback](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/src/open_painter/evaluation/evaluator.py).
- **R6:** [Image creation and full-buffer transfers](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/src/open_painter/core/image_manager.py).
- **R7:** [Per-texture-set raster cache](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/src/open_painter/evaluation/raster_cache.py).
- **R8:** [Scheduler and save/load lifecycle](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/src/open_painter/evaluation/cache.py).
- **R9:** [Smart-material format v2](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/SMART_MATERIAL_FORMAT.md).
- **R10:** [Testing documentation](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/TESTING.md).
- **R11:** [Persistent cache integration test](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/tests/integration/persistent_texture_cache.py).
- **R12:** [Active test runner](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/scripts/run_tests.py).
- **R13:** [Material/effect composer](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/src/open_painter/smart_materials/composer.py), inspected through line 170.
- **R14:** [Channel evaluation, source handling and filter classification](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/src/open_painter/evaluation/channel_eval.py), inspected source portions, not every line.
- **R15:** [Native paint detection](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/src/open_painter/painting/paint_session.py), inspected detection/reset section.
- **R16:** [Base-mesh triplanar rasterization](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/src/open_painter/painting/projection.py).
- **R17:** [Evaluated utility-map snapshot/rasterization code](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/src/open_painter/baking/maps/utility.py), inspected first 160 lines.
- **R18:** [Cycles baking and restoration](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/src/open_painter/baking/cycles_baker.py).
- **R19:** [Mask signatures and evaluation](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/src/open_painter/evaluation/mask_eval.py).
- **R20:** [Extension manifest](https://github.com/caspianmoon/open_painter/blob/c0b4045f45032e9a74befcbc388dd77a1e5d3bba/blender_manifest.toml).

## Public technical references

- **B1:** [Blender GPU module](https://docs.blender.org/api/current/gpu.html). Official search-indexed documentation confirms the GPU/offscreen API and Apple shader-translation considerations. Exact target-build capabilities require M0 probes.
- **B2:** [Blender GPU types](https://docs.blender.org/api/current/gpu.types.html). Official search-indexed documentation exposes textures/framebuffers/readback. No zero-copy Image integration is assumed.
- **B3:** [Blender's threading guidance, official source documentation](https://github.com/blender/blender/blob/main/doc/python_api/rst/info_gotchas_threading.rst). Read directly; fetched file blob SHA `e733fd0a4ded91a9c94730f88ad61a1d0e6ac1d9`. It warns against background threads running alongside Blender and recommends independent-process approaches.
- **B4:** [Geometry Proximity node](https://docs.blender.org/manual/en/latest/modeling/geometry_nodes/geometry/sample/geometry_proximity.html). Official search-indexed manual supports closest-location/distance fields. Implementation performance depends on sampling domain and workload.

Additional API pages for Codex to verify locally or against the matching release documentation: dependency graph/evaluated meshes, Geometry Nodes Raycast, Store Named Attribute, tiled Images, baking and asset APIs. They are implementation references to check, not a claim that this review executed those APIs.

## Final product decision

Open Painter already has a meaningful texture-painting foundation. The next valuable step is not more menu entries. It is an evaluator that stays correct under edits, uses predictable memory, and updates quickly enough to support the Blender-aware features above.
