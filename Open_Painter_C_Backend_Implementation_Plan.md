# Open Painter: Embedded C Backend Implementation Plan

**Status:** Implementation in progress on `feat/native-c-engine`. The embedded C17 backend is integrated with stock Blender on macOS Metal through ABI 9. See [current ownership and remaining work](docs/native/migration-status.md) and [executed validation](docs/native/release-evidence.md). The full migration is not yet complete. Windows/Vulkan work is deferred to the other machine; the specification below retains the original cross-platform scope.

**Prepared:** September 29, 2026.

**Repository:** `caspianmoon/open_painter`

**Baseline branch:** `fix/windows-vulkan-performance`

**Verified baseline commit:** `5a552060988d0b43a7ef05f6e43da2aec481b323`

**Proposed implementation branch:** `feat/native-c-engine`

**Product contract:** Install a normal Blender add-on. Select an ordinary Blender object. Paint on that object inside the existing Blender 3D viewport. No additional window, external application, server, export/reimport loop, or user-managed connection.

## 1. Decision and scope

Build a Blender-independent C17 library, provisionally named `libopenpainter`, with a thin Python integration layer. The library will own painting evaluation, the incremental dependency graph, scheduling, texture caches, brush processing, and GPU work. Blender will remain the host for user interaction, scene objects, modeling, UV editing, project integration, and the visible editing area.

The GPU kernels remain shaders. C is the language of the native core, not a requirement to execute texture compositing on the CPU. A small platform-specific binding may use another language where an operating system API requires it; do not turn this into a C++ rewrite of the core without an explicit decision.

The highest-risk component is the bridge from the evaluated GPU textures to the actual Blender viewport. Prove that bridge before committing to wholesale feature migration. A DLL that computes quickly but sends each finished texture through CPU pixels does not satisfy this plan.

A Blender-independent core also does not imply that the core must create a second GPU device. Resource ownership and device sharing are separate design decisions. Either an approved host-device integration or a separately owned device with verified interoperation can qualify. The engine algorithms and document model must not depend on live Blender objects in either case.

### In scope

The full migration includes all existing material channels, layer/folder semantics, masks, procedural graphs, brush operations, projection, geometry-dependent inputs, caching, undo, save/reload, and export integration. The first working slice is much smaller: a cube, two fill layers, a spatially varying input, opacity editing, coherent multichannel output, and one real brush operation.

### Not part of the initial rewrite

Do not redesign the interface, add new material features, replace the standalone application, replace all baking infrastructure, implement a general-purpose scene renderer, or rewrite every Python module. Preserve the existing Material Director, material catalog, aging tools, project formats, and baking/export workflows through adapters while the interactive path moves into C.

No performance claims about Substance Painter are part of this specification. Comparative claims would require matched tests, scenes, settings, and hardware.

## 2. Verified starting point

The verified branch already has a custom texture-space GPU evaluator. It does not build a Blender shader node tree for the entire stack on every edit. The documented implementation contains per-unit programs, retained source and mask results, dependency stamps, structural adoption, and a checkpoint below the lowest changed layer. These are assets to preserve. [R2, R3]

The current Vulkan publication path includes a GPU readback followed by `image.pixels.foreach_set(...)`; the runtime also has a separate GPU publication path that writes mip levels for other backends. Replacing its calculations with C will not, by itself, remove that publication boundary. [R3, R4]

The existing channels are `BASE_COLOR`, `ROUGHNESS`, `METALLIC`, `NORMAL`, `HEIGHT`, `AO`, `EMISSION`, and `OPACITY`. Do not accidentally implement a four-channel replacement and call it feature-complete. [R5]

The existing Windows benchmark explicitly excludes OS input delivery, viewport FPS, and display presentation. Extend it, but do not relabel its current completion time as input-to-visible latency. [R6]

Repository documentation is not uniformly current. In particular, `docs/shader-preview-architecture.md` marks much of its older material-node description as superseded. Resolve disagreements against the pinned implementation and actual tests, not whichever document describes the most attractive architecture. [R7]

The friend's precise GPU, driver, Blender build, active backend, resolution, and installed package hash remain unverified. Capture them in Gate 0 instead of inferring Vulkan from Windows.

## 3. Non-negotiable user experience

The add-on must not create a new OS window, open a browser, launch a visible terminal, require a server, or ask the artist to export the object into a separate painting session.

The selected Blender object remains authoritative. An internal mesh snapshot, acceleration structure, or GPU vertex buffer is allowed; a duplicate artist-facing object with independent state is not. Selection, transforms, UV changes, material-slot assignments, and deletion must remain connected to the original Blender data.

Preserve the existing Open Painter panels, workflow, shortcuts, and normal Blender navigation. The internal backend name belongs in diagnostics, not in required setup steps. Compatibility limitations must nevertheless be disclosed rather than silently hidden.

Do not change the scene's render engine globally as an incidental side effect of opening the painting workspace. Preserve the user's render settings and restore any temporary state reliably.

Painting must update during a stroke or slider drag. Waiting until release, hiding the object during recomputation, or showing a last-good frame indefinitely is not an acceptable definition of realtime.

## 4. Architecture and ownership

```text
Existing Blender window and 3D viewport
    |
    +-- Open Painter panels, operators, input, property edits
    |
    +-- Blender adapter (Python)
    |     Scene/UV/selection snapshots
    |     Stable object and document IDs
    |     Native command submission
    |     Save/load/undo lifecycle
    |     Viewport/presentation integration
    |
    +-- libopenpainter (C17, in process)
          Document and transaction model
          Incremental evaluation graph and shader compiler interface
          GPU device/resource abstraction
          Layer, mask, filter and procedural kernels
          Brush/projection and geometry caches
          GPU memory and revision lifetime management
          Completed texture-set revisions
                    |
                    +-- Verified Blender presentation bridge
                    |        -> same Blender object, same viewport
                    |
                    +-- Explicit snapshot/export/persistence boundaries
```

The Python layer converts Blender-facing changes into bounded commands and immutable snapshots. It must not serialize the entire document, traverse the full scene, or copy texture pixels for every numeric edit.

The native core accepts ordinary data, not `bpy` objects, RNA pointers, Blender image structs, or Python node trees. The same library must compile and run non-GUI tests without importing Blender.

Separate the texture evaluator from presentation. A completed material revision is a set of texture resources with ownership and synchronization metadata. It is not an instruction to rewrite eight Blender images immediately.

### State authority

The existing Blender project representation remains the persisted recipe during migration. The C document is its evaluated runtime counterpart, updated through transactions. Each accepted property edit updates the recipe and submits a matching runtime command. Validation failures must leave a consistent recipe and an explicit error state; do not silently clamp only one side.

Raster painting introduces native-authored data. Each committed stroke must have recoverable source data and a corresponding project revision. Do not rely on GPU output textures as the only copy of irreplaceable painted content.

Undo/load/redo start a new runtime generation from the restored project state. Old jobs and handles from the previous generation cannot publish into the new one.

## 5. Gate 0: baseline, inventory, and observability

### Work

Record the friend's installed add-on location and package hash, Blender build/version, Python version, OS, GPU identity, driver, backend, display refresh rate, texture resolution, layer configuration, viewport dimensions, and power mode. Distinguish hybrid-laptop adapters; verify which adapter Blender actually uses.

Pin the source and preserve reproducible fixtures. Start with the current `plain` and `masked` Windows benchmark cases. Add controlled scenes for a nonuniform image fill, a paint layer, and a procedural mask. Capture cold initialization separately from warmed editing.

Instrument these boundaries before optimization:

| Boundary | Record |
| --- | --- |
| Input/property callback | Event ID, time, property, value, callback duration |
| Native submission | Command ID, receipt/acceptance time, queue depth |
| Dependency invalidation | Affected units/channels, invalidation reason |
| CPU input generation | Work type, bytes, duration, geometry rebuild count |
| GPU execution | Pass count, supported GPU timestamps, resource usage |
| Transfers | Upload/readback bytes, image-pixel writes, full-frame copies |
| Publication | Produced revision, affected channel set, ready time |
| Viewport draw | Revision actually bound, draw submission time |
| Display | Actual presentation evidence where measurable; otherwise explicitly unknown |

Add native binary, shader bundle, adapter, and build-option hashes to reports. The current source digest covers Python files, so it is insufficient to identify a future native build. [R6]

### Deliverables

Create `docs/native/baseline.md`, versioned benchmark fixtures, trace files, and a feature/parity inventory. All filenames in this section are proposed additions.

### Exit condition

Reproduce the issue on the target Windows installation, or record a clearly identified inability to reproduce it. Have paired baseline timings and a visual recording before declaring an improvement.

## 6. Gate 1: prove embedded presentation

This is the first architecture gate. It is not a final optimization task.

### Prototype

Load a minimal C library from the normal add-on. Use the selected Blender cube, its actual UVs and transforms, a 2048 x 2048 texture set, two fill layers, and a nonconstant image or procedural input. Test opacity, roughness, and one localized brush stroke. Include minified/distant viewing to exercise mipmaps.

The native prototype must produce textures through GPU work and show their updates on the cube inside the existing Blender viewport. A flat color changed through a Principled socket alone does not prove the texture path. A custom window or an unrelated offscreen benchmark does not prove Blender integration.

Run paired experiments: current evaluator/current publication, current evaluator/direct presentation where implementable, and native evaluator/native presentation. These isolate publication improvements from native-language improvements.

### Candidate routes, in order

**Route A: preserve live EEVEE with a verified GPU texture bridge.** Prefer this because it preserves the existing shading workflow. The exact API route must be identified and tested for each supported Blender/backend combination. Prove texture visibility, all sampled mip levels, synchronization, resource lifetime, image eviction behavior, and coherent multichannel commits.

**Route B: Open Painter preview through Blender's custom render-engine integration in the same existing viewport.** This does not require a separate window and can still render snapshots of the actual Blender objects. Blender exposes viewport update/draw hooks, but those hooks alone do not establish external-texture interoperability. [W1] This route changes the preview renderer, so it requires an explicit architecture decision, shading/color-management tests, and resolution of scene-render-engine scope, overlays, navigation, picking, and other objects. Do not silently substitute it for EEVEE.

**Route C: temporary host-GPU execution adapter.** A thin adapter may submit a C-generated evaluation plan through Blender's public GPU interfaces for an A/B experiment. Label this honestly as an intermediate architecture, not proof that a fully native GPU backend is complete. Keep business logic and document semantics out of that adapter.

### What the bridge specification must contain

Record the actual device/context acquisition method, resource creation/import method, required capabilities, format/usage restrictions, synchronization rules, mip generation, ownership transfer, and supported build matrix. For external memory, capabilities, exported/imported resources, and synchronization must be handled explicitly; an OS handle is not sufficient by itself. [W3]

Do not assume two devices on the same GPU share textures automatically. Match device identity on hybrid laptops and reject incompatible adapters. Do not assume switching to Vulkan, WebGPU, or another graphics abstraction supplies a Blender bridge.

Do not read Blender's private memory layouts, calculate undocumented pointer offsets, depend on unexported symbols, or mutate host GPU state without a verified restoration contract. Those are not acceptable foundations for a normally distributed add-on.

A GPU-to-GPU copy is allowed if necessary and measured. Literal zero-copy is not a fetish: the requirement is to remove full-resolution CPU round trips and stalls from the interactive path. A full-frame preview readback is also a CPU round trip and does not qualify.

### Pass evidence

Continuous editing works in a stock supported Blender installation; no extra user window is created; the original object remains selected/editable; numerical output and mip tests pass; no full-frame interactive readback occurs; no sampled resource is modified while still in use; no blank or mixed-channel revisions are displayed.

If Route A cannot be demonstrated, publish the exact failed capability and evaluate Route B separately. If no supported route meets the product contract, do not proceed with a full rewrite under an invented interop API. Preserve the old backend and document the platform dependency or reduced support matrix. A custom Blender build may be a research artifact, but it does not pass the stock-add-on release requirement.

## 7. Native core implementation and C API

Use C17, CMake, explicit allocation ownership, fixed-width integers, opaque handles, and a versioned public C ABI. Keep third-party dependencies pinned and review their licenses before packaging. Keep GPU backend headers out of the stable public document API.

Start with the graphics backend that passes Gate 1. Vulkan is a candidate for Windows/Linux native execution, not a pre-approved presentation solution. OpenGL may be an initial supported route if validated. Add macOS support through a separately tested backend/bridge rather than treating a renamed DLL as cross-platform support.

Use a small compiled CPython binding for production command batching, buffer lifetime management, error mapping, and ABI checks. A `ctypes` wrapper is acceptable for the first spike. Neither mechanism permits arbitrary Blender-memory access. A C ABI is distinct from a CPython ABI; build and validate against Blender's bundled interpreter or deliberately qualify a limited-ABI implementation.

### Public API surface to design

The names below are proposed interfaces, not existing functions:

```text
op_get_abi_version / op_query_capabilities
op_context_create / op_context_destroy
op_document_create / op_document_restore / op_document_destroy
op_transaction_begin / op_transaction_commit / op_transaction_cancel
op_layer_set_scalar / op_layer_set_color / op_layer_reorder
op_mask_set_parameter / op_graph_apply_patch
op_mesh_update / op_mesh_remove / op_transform_update
op_stroke_begin / op_stroke_append_batch / op_stroke_end / op_stroke_cancel
op_evaluate_request / op_poll_completions
op_frame_acquire / op_frame_release
op_snapshot_begin / op_snapshot_poll / op_snapshot_release
op_get_metrics / op_copy_last_error
```

Every asynchronous command carries document ID, generation, transaction ID, command sequence, and applicable source revisions. Use explicit status values for invalid argument, stale handle, unsupported capability, pending work, resource exhaustion, device loss, and validation failure.

Define buffer size, alignment, row pitch, format, color space, lifetime, and allocator ownership. Native code copies ordinary submission buffers before returning unless an explicit pin/release protocol is used. Never retain a transient NumPy view or Blender RNA address. No per-texel Python callbacks, Python dictionaries in the GPU scheduling loop, or millions of scalar FFI calls.

A frame-acquire operation returns a lease to a completed immutable material revision. An unready request returns a nonblocking status. A frame-release operation releases host use only under the bridge's synchronization contract; it must not free a texture still sampled by a GPU draw.

### Threads and lifecycle

All Blender data access stays on the Blender-owned thread. Native workers operate only on copied or explicitly owned data. They do not call `bpy`, invoke Python callbacks during evaluation, or touch the host graphics context without its validated threading contract.

Release the GIL for suitable long native operations, but remember that releasing it does not move the calling Blender UI thread into the background. Runtime submission still needs to return promptly. Python's C API has explicit thread-state and object-access requirements. [W5]

Keep graphics work on the context-owning thread when required. Only move GPU submission onto a worker if the selected device/queue integration supports it. Poll completion without busy-waiting. Do not add per-edit `glFinish`, `vkDeviceWaitIdle`, or blocking readback to fake correctness.

On disable, reload, or shutdown: stop accepting work, cancel future jobs, drain or safely retire in-flight work, detach presentation, release resources while the context is valid, join workers, and only then destroy native state. Do not hot-unload a library while callbacks or GPU work can still reference it.

## 8. Document semantics and parity

Do not translate the entire Python package line by line. Define a compact native document and evaluation representation that preserves existing semantics.

Retain stable IDs, layer ordering, folder parenting, isolated and passthrough behavior, visibility, per-layer and per-channel opacity, blend overrides, source/effect ordering, masks, references, and disabled-channel behavior. Preserve the separation between coverage alpha and the authored `OPACITY` channel. [R2, R5]

Preserve the exact procedural seed scheme and integer hashing where required. Use existing reference fixtures to identify acceptable floating-point differences. Do not replace a legacy noise algorithm with a different one because it is easier to implement.

Maintain per-unit dependencies and structural versus numeric edits. Opacity changes should not compile a shader or rerasterize unchanged geometry. A layer reorder may change downstream dependencies, but unchanged source assets and equivalent compiled programs should remain reusable.

Port a normalized, Blender-independent intermediate representation. An initial Python translator may emit that representation from existing recipe data; it must run on structural changes, not on every slider step. Port the shader generation layer progressively after equivalent behavior is established.

### Comparison rules

Compare native outputs against both the existing evaluator and independent mathematical fixtures. If the old evaluator is wrong on a fixture, document the correction rather than preserving a bug invisibly.

The existing engine documents approximate parity thresholds of `5e-4` in its precise path and `1e-3` in its display path. Treat these as starting thresholds for matching corresponding fixtures, not a universal tolerance for every channel. Add stricter coverage/ID tests, angular normal comparisons, seam tests, and explicit height-detail checks. [R3]

Do not enable broad fast-math transformations or quantize all channels to 8-bit merely to pass performance goals. Test finite/NaN behavior, near-zero alpha, HDR emission, clamping, and color-space conversion separately.

## 9. Evaluation and coherent display pipeline

### Numeric edits

1. Receive a small parameter update and validate it.
2. Advance the requested document revision.
3. Mark only the affected dependency closure dirty.
4. Reuse unchanged source, mask, geometry, and lower-stack checkpoint results.
5. Evaluate the affected suffix and required channels.
6. Generate necessary padding and mip levels.
7. Mark one complete material revision ready.
8. Let the presentation bridge acquire that revision for a draw.

A global opacity change may affect every texel of the relevant outputs. Do not claim that all edits can be solved by dirty rectangles.

### Commands and cancellation

Coalesce replaceable parameter updates by property within valid transaction boundaries. The newest pending opacity value can replace older unscheduled opacity values. Do not reorder updates across layer deletion, graph mutation, undo, or save barriers.

Brush samples are not disposable slider values. Preserve stroke order, pressure, timing, and coverage. Batch them for efficiency and apply tested resampling; do not drop samples merely because an evaluation is behind. Use explicit queue limits and a recoverable overflow policy.

Maintain one displayed revision, a bounded amount of in-flight GPU work, and a bounded pending-latest parameter state. Avoid an unbounded FIFO of obsolete slider evaluations. Do not assume already submitted GPU work can be cancelled safely.

### Revision commit

A displayed revision must reference compatible base color, roughness, metallic, normal, height, AO, emission, and opacity resources. Unchanged channel resources may be shared. A logical document revision does not require regenerating every channel.

Build changed results in staging resources. After all required outputs, padding, and mip work complete, switch the displayed revision coherently. For a bridge that writes host-owned targets, prove an equivalent all-before-draw commit ordering instead of assuming an atomic pointer swap is available.

Retain the previous valid revision until the replacement is usable. If an evaluation fails, show an error while retaining a valid display; never replace it with blank textures. Track displayed-revision age so repeated old frames cannot disguise stalled evaluation.

### Tiles and filters

Introduce tile-based dirtiness for localized strokes and suitable filters only after the initial full-texture path is correct. Start by benchmarking 128- and 256-texel tiles rather than locking a size without data.

Expand filter dependencies by their support radius. Propagate dirty regions into lower mip levels. Account for UV-seam padding donors and cross-island dependencies. Global statistics, wide warps, full-layer opacity, and some procedural parameter changes require broader invalidation. Model these cases explicitly.

## 10. GPU memory, formats, and caches

Maintain a global budget across all documents and texture sets, not only a generous cache for each set. Include active textures, source images, intermediate caches, staging revisions, mipmaps, frame leases, brush data, undo data, and bridge duplication. Keep reserve for Blender and the driver.

Use compact formats only when channel semantics and hardware support permit. Candidate working formats include half-float color/normal resources, scalar or scalar-plus-coverage masks/data, and full-float height where needed. Do not hard-code a format table before parity and capability tests.

For planning, one 4096 x 4096 RGBA16F base image is 128 MiB; its full mip chain approaches 170.7 MiB. Duplicating every output for every layer is not viable as a general strategy. These are storage calculations, not measurements of this branch.

Keep small constant resources genuinely small until rasterization is necessary. Preserve alpha/coverage semantics rather than promoting every scalar field to RGBA. Share unchanged outputs between revisions and lazily allocate disabled channels.

Use generation-tagged resource handles, retirement fences, reference-counted leases or equivalent checked ownership, and measured eviction policy. Never recycle an input texture while a consumer pass or viewport draw still uses it.

Evict recomputable caches before irreplaceable paint data. Bound shader and pipeline caches as well as textures. Version persistent shader caches by source, compiler options, device/driver compatibility, and engine build. Treat a cache miss as normal, not a corrupted project.

Handle memory pressure with an explicit policy: reclaim caches, reduce unnecessary staging, or notify the user. Do not silently lower texture resolution. Optional draft quality must be explicit and never the basis of full-resolution benchmark claims.

## 11. Painting directly on Blender objects

Snapshot only the necessary evaluated mesh data: positions, triangulation, per-corner UVs, normals/tangents, material-slot routing, object transforms, selection restrictions, visibility information, and source object IDs. Preserve the selected UV map and UDIM tile rules.

Cache geometry by content revision. An opacity edit must not extract the mesh again. Transform changes update the relevant transform state; topology changes invalidate only the geometry-dependent data that actually changed.

Define behavior for modifiers, shape keys, armatures, nonuniform/mirrored scale, overlapping UVs, multiple material slots, and multiple assigned objects. Where a mapping is ambiguous, match existing tested behavior or present an explicit limitation. Do not paint onto a stale snapshot after topology changes.

For a stroke: capture the viewport camera and input state, hit-test against current geometry, map to the correct texture set/tile, generate dabs, apply coverage/pressure/blend logic, propagate the dirty region, and publish a coherent revision. Handle front-face/back-face and occlusion rules deliberately.

The first cube proof must be followed by a real UV-unwrapped asset and a dense mesh. Include orbiting during painting, transformed objects, seam crossings, masking by selected faces, multiple objects, and deletion during pending work.

The same underlying textures should eventually serve any existing 2D editing view. That is a separate presentation integration requirement; do not restore per-edit CPU publication simply because an Image Editor happens to be open.

## 12. Persistence, undo, export, and feature preservation

Separate display currency, durable source currency, and exported-material currency. A texture may be ready for display while its recoverable checkpoint is pending. A GPU-only cache is not a durable project.

Group a slider gesture or completed brush stroke into sensible undo transactions. Store parameter diffs for numerical edits and recoverable source deltas/checkpoints for painting. Tie Blender undo state to native document generations; invalidate obsolete async completions after restore.

Allow asynchronous persistence work outside the critical interactive path. A save operation must capture a defined accepted revision and ensure its recipe plus authored sources are durable. Do not save a recipe referring to a raster stroke that exists only in volatile GPU memory.

Use a versioned, validated snapshot format with bounded lengths and checksums. Keep caches separate from project truth. Test Save As, moved projects, packed assets, missing files, old-file migration, cancelled saves, and recovery after native initialization failure. Never write caches into the installed extension directory.

CPU readback is allowed for explicit export, baking integration, authored-source checkpointing, diagnostics, and supported synchronization with Blender render consumers. It is not allowed as the ordinary display mechanism for each opacity event. Amortized asynchronous source persistence must be measured separately from the editing gate.

Preserve Blender rendering through a defined synchronization boundary. Switching to Cycles or triggering a render must produce current maps. Do not silently show stale data as current. Preserve the user's render settings and existing restoration logic.

Keep the old CPU evaluator as a reference/export fallback during migration. Route Material Director results, baked AO/curvature/normal/world-normal/position/thickness maps, material aging updates, and imported textures through the same source/parameter revision system. These integrations do not require rewriting their entire workflows in C.

Export without Open Painter remains a separate explicit workflow. The baseline engine documentation notes limitations when a file is opened without the add-on, so do not promise add-on-free editable materials as an existing capability. [R3]

## 13. Proposed repository layout and migration map

All paths under `native/`, `docs/native/`, and `src/open_painter/native_bridge/` below are proposed additions.

```text
native/
  CMakeLists.txt
  include/openpainter/op_api.h
  src/core/             # document, revisions, transactions, error handling
  src/graph/            # IR, dependencies, scheduling, compiler interface
  src/gpu/              # resources, backend interface, selected implementation
  src/paint/            # dabs, projection, stroke processing
  src/geometry/         # snapshots, acceleration, UV/mesh maps
  src/persistence/      # checkpoints and snapshot validation
  shaders/              # compositing, masks, filters, projection, mip work
  bindings/python/      # small compiled CPython binding
  tests/                # unit, parity, fuzz, ownership tests

src/open_painter/native_bridge/
  loader.py
  capabilities.py
  document_adapter.py
  scene_adapter.py
  commands.py
  presentation.py
  lifecycle.py
  diagnostics.py

docs/native/
  baseline.md
  bridge-decision.md
  supported-matrix.md
  parity-manifest.md
  release-evidence.md
```

| Existing area | Migration action |
| --- | --- |
| `properties/layer.py` and other property callbacks | Submit narrow value/structure commands; retain UI data and validation |
| `evaluation/engine/compiler.py`, `headless.py`, `glsl.py` | Extract semantics into portable IR; reuse fixtures and shader math |
| `evaluation/engine/runtime.py` | Replace evaluation/resource orchestration incrementally; isolate `_publish` and synchronization boundaries |
| `evaluation/shader_preview.py` | Adapt session lifetime and readiness to native revision state |
| `evaluation/cache.py` | Schedule short native submissions/completion polling; remove default full-output publication from editing |
| `core/texture_set_node.py` | Preserve EEVEE material adapter if Route A succeeds; explicit compatibility role otherwise |
| `painting/projection.py` and GPU paint modules | Introduce revisioned snapshots and migrate brush/projection work |
| `procedural_graph/` | Translate graph definitions into portable native IR; preserve graph UI |
| `registration.py` | Add safe load/undo/save/shutdown lifecycle for native sessions |
| `scripts/benchmark_windows_viewport.py` | Retain component benchmark; add native identities and a distinct GUI interaction protocol |

Verify exact symbols before patching. The table is a migration map, not authorization to delete whole modules immediately.

## 14. Delivery sequence and completion gates

### P0. Measurement and compatibility inventory

Depends on no native rewrite. Deliver Gate 0, pinned fixtures, transfer counters, and a complete feature inventory. Finish before interpreting speedup claims.

### P1. Native loader and presentation spike

Build the minimal C library and Gate 1 prototype together. Deliver a working installed add-on, bridge report, supported configuration, trace, and visual proof on the actual Windows laptop. Loading a library alone is not completion.

### P2. Document, ABI, and revision infrastructure

Deliver stable IDs, transactions, command batching, snapshot validation, generation-safe handles, nonblocking completions, frame leases, and lifecycle tests. Pure C tests run without Blender. This may proceed in parallel with the bridge experiment, but no full feature port is justified until P1 passes.

### P3. Fill layers and coherent compositing

Implement all existing channels, opacity, color/data edits, blend math, isolated/passthrough folders, per-channel masks, resource reuse, and complete-revision publication. Initially support a clearly bounded subset of layer types. Unsupported types must remain on an explicit legacy session path, not disappear.

Exit on correct two-layer and deeper-stack fixtures, no warm numeric shader compilations, no geometry rebuild on opacity, and passing full-resolution interaction tests.

### P4. Masks, procedurals, and effects

Port the existing mask graph, pointwise effects, procedural sources, references, and supported spatial filters in measured groups. Each family requires numerical parity and invalidation tests. Preserve changed-source versus downstream-opacity cache behavior.

### P5. Native paint and geometry

Deliver real 3D painting, pressure-aware dab batching, erasing, existing supported brush behavior, UV/UDIM routing, selection/occlusion, seam handling, geometry snapshots, and tile propagation where useful. A fill-only backend is not a painting-engine release.

### P6. Persistence and workflow compatibility

Complete Blender undo/redo, save/load, source checkpoints, export/render synchronization, Material Director input compatibility, and old-project migration. Implement these contracts earlier as infrastructure, then qualify the full combinations here.

### P7. Packaging and platform qualification

Create ordinary installable extension packages, validate bundled native modules, test capability-based fallback, and qualify the Windows GPU/vendor matrix. Preserve existing non-Windows backends until native replacements pass their own tests. Do not mark untested platforms supported.

### P8. Default-backend rollout

Promote native mode only for qualified combinations. Keep a diagnostic legacy fallback and reversible project migration. Remove old paths only after feature parity, persistence, stability, and real-device performance gates pass.

Every phase ends with a runnable artifact and evidence. A folder tree, stubbed API, headless screenshot, or passing mock suite is not phase completion.

## 15. Acceptance tests and performance targets

These are proposed product targets, not measured results or promises about arbitrary hardware.

### Primary reference workload

Use the friend's recorded Windows hardware, stock qualified Blender build, 60 Hz or faster display where available, fixed viewport size, two fills with one nonuniform source, all relevant channels enabled, and full 2048 x 2048 output. Add separate fixtures for one procedural mask and one paint layer.

| Metric | Initial target |
| --- | --- |
| Warm parameter submission on UI thread | p95 <= 2 ms, p99 <= 5 ms |
| Simple 2K edit input-to-visible response | p95 <= 33 ms, p99 <= 50 ms |
| Feedback cadence during a simple sustained drag | Target one new coherent revision per 60 Hz frame; report achieved cadence |
| Final value after release | Visible within 50 ms for the primary fixture |
| Blank, default-texture, or mixed-revision frames | Zero observed in controlled captures |
| Warm numeric graph rebuilds/shader compilations | Zero for the established fixture |
| Geometry extraction on opacity changes | Zero |
| Full-frame readback/image rewrites for interactive presentation | Zero |
| Queue behavior | Bounded; no steadily increasing lag |
| Memory after a repeated identical workload | Stable bounded plateau after warmup; no monotonic leak |

Treat 4K, deep stacks, costly filters, UDIMs, and low-memory hardware as separate workload classes. Set and publish their budgets after measurement. Do not claim the simple cube target applies universally.

### Measurement protocol

Run cold startup separately. For warmed tests, use at least five sustained 60-second interaction runs and enough sampled transitions to make p95/p99 meaningful. Randomize or alternate before/after runs to reduce thermal/order effects. Record driver, power mode, native artifact hashes, and quality settings.

Distinguish event receipt, accepted command, GPU completion, draw submission, and actual presentation. When actual display presentation cannot be measured, label the result input-to-draw and retain independent visual evidence. Do not equate `view_draw` returning or a one-pixel readback with photons reaching the display.

For coalesced input, log which commands were superseded. Report displayed-state age and tracking error during continuous dragging as well as isolated-step latency. Do not improve percentiles by ignoring the majority of skipped events. Never count repeating an old texture at 60 FPS as 60 Hz material feedback.

Use test-only revision markers or controlled debug channels for visual coherence checks. Keep intrusive readbacks and captures outside production performance measurements, and quantify capture overhead. Test correct nonuniform texels and mip levels; a uniform-only case can hide broken texture publication.

### Required correctness/stability cases

Include nested folders, all blend modes, per-channel overrides, color-space conversions, transparency, height/normal details, references/cycle rejection, spatial-filter halos, procedural seeds, overlapping UVs, seams, multiple texture sets, multiple viewport areas, and resizing/minimizing/restoring.

Include object deletion, undo during evaluation, immediate save after painting, Save As, reload, resolution changes, shader compilation failure, allocation failure, low-memory eviction, disabled add-on, device/context loss, and shutdown with pending work.

Run CPU-only native tests with address/undefined-behavior sanitizers where supported, parser/command fuzzing, allocation failure injection, and resource-lifetime tests. A native memory fault can terminate the hosting process; a Python exception wrapper is not a general crash sandbox. [W4]

## 16. Packaging and release behavior

Ship prebuilt native code. Users must not install a compiler, run `pip`, modify PATH, or manually start a process. Build platform-specific artifacts for the supported OS, architecture, and bundled Python configuration.

Blender documents bundled wheels and platform-specific extension packages. Validate the actual target release's package rules, manifest, dependency closure, and loader behavior; do not assume manually placing a `.dll` in a ZIP is sufficient. [W2]

Use safe absolute library paths and controlled dependency lookup. Test a clean Windows account with no development tools, non-ASCII install/project paths, and a machine lacking the developer's redistributable assumptions. Validate hashes and ABI versions before enabling the native path.

Capability-check without corrupting the current session. A supported native path starts automatically. An unsupported path falls back to the existing implementation where possible, with a plain-language compatibility notice and a diagnostic report. Never call a CPU-pixel fallback the accelerated GPU-resident path.

A first native release can qualify Windows configurations while leaving macOS/Linux on the existing engine. That is safer than destabilizing working platforms to make a universal marketing claim.

## 17. Risks that must not be hidden

**Interop feasibility:** A compiled library does not grant access to Blender's GPU textures. Gate 1 decides the viable route.

**Renderer substitution:** An embedded custom renderer can remain inside Blender but still change shading, overlays, or render-engine scope. Validate the product behavior, not only the lack of a new window.

**C safety:** In-process native bugs can crash Blender. Ownership, validation, sanitizers, and recovery checkpoints are release work, not optional cleanup.

**GPU cost:** C cannot make global texture operations free. Dependency reuse, formats, scheduling, transfer removal, and bounded work are the actual mechanisms.

**Feature drift:** Procedural/normal/folder math may change during a port. Require fixture-level parity and explicit migration notes.

**False performance evidence:** Fast callbacks, fast offscreen compute, or a static viewport at 60 FPS do not prove responsive painting.

**Persistence loss:** Keeping textures GPU-resident is not permission to keep the only copy of brushwork in volatile memory.

**Cross-platform scope:** One validated graphics backend is not all Windows laptops, and Windows support is not macOS support.

## 18. Coding-agent kickoff

Start from the pinned baseline and create the proposed feature branch without deleting the existing engine. Read the current engine implementation, constants, project architecture, parity tests, and Windows benchmark. Do not follow superseded material-node documentation as though it described the current path.

Implement P0 and the smallest P1 vertical slice first. Preserve the normal add-on interface and existing viewport. Build a C17 library, load it through the add-on, use the selected cube's real UVs, evaluate a nonuniform two-layer material, and prove continuous opacity and brush feedback without full-frame CPU publication. Document the exact graphics integration and configuration that made this possible.

Do not invent native texture import APIs, fake fast results by changing a BSDF constant, claim zero-copy from matching handle numbers, or replace the viewport with a separate window. Do not claim success based on compile time, a DLL import, a mock test, or an offscreen benchmark. Keep explicit compatibility fallback and the old project path intact.

For every completed phase, provide the source diff, installed artifact identity, executed commands, test results, unresolved limitations, numerical comparison, performance traces, visual evidence, and a recommended next gate. Mark unrun tests as unrun. The first real milestone is the friend's cube becoming responsive in Blender, not the number of lines rewritten in C.

## 19. Source notes

Repository observations are pinned to the verified commit. External documentation was consulted on September 29, 2026. These references support starting facts and platform constraints; the proposed design and targets remain engineering decisions.

- **R1:** Branch head metadata. `https://api.github.com/repos/caspianmoon/open_painter/commits?sha=fix%2Fwindows-vulkan-performance&per_page=1`
- **R2:** Project architecture, first 200 lines inspected. `https://github.com/caspianmoon/open_painter/blob/5a552060988d0b43a7ef05f6e43da2aec481b323/ARCHITECTURE.md`
- **R3:** Current engine design, semantics, publication, parity, and limits. `https://github.com/caspianmoon/open_painter/blob/5a552060988d0b43a7ef05f6e43da2aec481b323/docs/open-painter-engine.md`
- **R4:** Runtime publication and synchronization, lines 1360-1560 inspected in this planning pass. `https://github.com/caspianmoon/open_painter/blob/5a552060988d0b43a7ef05f6e43da2aec481b323/src/open_painter/evaluation/engine/runtime.py`
- **R5:** Existing channels, defaults, blend modes, and resolution identifiers. `https://github.com/caspianmoon/open_painter/blob/5a552060988d0b43a7ef05f6e43da2aec481b323/src/open_painter/constants.py`
- **R6:** Windows component benchmark and its stated measurement limits, first 130 lines inspected. `https://github.com/caspianmoon/open_painter/blob/5a552060988d0b43a7ef05f6e43da2aec481b323/scripts/benchmark_windows_viewport.py`
- **R7:** Shader-preview architecture, including its superseded-status notice. `https://github.com/caspianmoon/open_painter/blob/5a552060988d0b43a7ef05f6e43da2aec481b323/docs/shader-preview-architecture.md`
- **W1:** Blender 5.2 RenderEngine API, viewport update/draw hooks. `https://docs.blender.org/api/5.2/bpy.types.RenderEngine.html`
- **W2:** Blender 5.2 manual, bundled wheels and platform builds. Indexed official documentation consulted. `https://docs.blender.org/manual/id/5.2/advanced/extensions/python_wheels.html`
- **W3:** Khronos Vulkan Guide, external memory and synchronization. `https://docs.vulkan.org/guide/latest/extensions/external.html`
- **W4:** Python ctypes documentation, native pointer behavior and foreign-function boundary. `https://docs.python.org/3/library/ctypes.html`
- **W5:** Python C API, thread states and GIL. `https://docs.python.org/3/c-api/threads.html`
