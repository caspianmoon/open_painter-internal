# Viewport interaction costs on Windows

The Windows pass addresses two CPU costs that can interrupt navigation even
without a material edit. These changes apply to all GPU backends.

- The ordinary viewport's fallback paint detector defers its whole-canvas scan
  while a native orbit, pan, zoom, dolly, roll, walk or fly operator is modal.
  Image Editor pan and zoom receive the same treatment. It keeps the pending
  scan timestamp intact and catches any unobserved stroke on the next available
  tick after navigation. Forced scans, stroke-release processing and bake
  corrections retain their existing behavior.
- Open Graph's row-order check only fingerprints the layer stack when root
  nodes have actually moved. Its normal reconciliation still checks the stack
  every UI tick, so panel edits retain precedence over node positions.
- Static graph row guides retain their GPU batch across redraws. Unregistering
  releases both the batch and shader; re-enabling creates new GPU resources.

The navigation identifiers are verified against the installed Blender 5.2.2
RNA in `tests/integration/navigation_scheduling.py`. Detection reads the
window's modal operators. Blender's [3D navigation implementation](https://github.com/blender/blender/blob/main/source/blender/editors/space_view3d/view3d_navigate.cc)
and [Image Editor pan/zoom operators](https://github.com/blender/blender/blob/main/source/blender/editors/space_image/image_ops.cc)
describe the native navigation lifetimes. The optimization does not cover
instantaneous wheel events or claim a viewport frame-rate improvement.

## Local measurements, 2026-09-28

Windows, Blender 5.2.2 LTS (`d13f752e3b9c`), background CPU measurement with
100 fill layers and two procedural masks per layer. Both runs used cProfile,
20 UI ticks and six canvas-scan samples. These are synthetic CPU timings, not
GPU or end-to-end viewport frame times.

| Work | Before | After |
| --- | ---: | ---: |
| Open Graph UI lifecycle median | 16.80 ms | 9.17 ms |
| Full stack fingerprints per idle UI tick | 2 | 1 |
| Warm evaluator idle tick median | 0.119 ms | 0.127 ms |
| Unchanged 4096-square RGBA canvas scan median | 78.46 ms | 78.26 ms |

The row-order change reduced this graph fixture's median UI timer cost by 45%.
Canvas scanning itself has the same cost; postponing the fallback scan keeps
that measured 78 ms interruption out of a modal navigation gesture. At 2048
square the initial scan median was 18.65 ms. A 4096-square float RGBA raster
contains 256 MiB, which the previous fallback could copy and compare every
0.75 seconds after a dirty paint session in an ordinary viewport.

The focused regression confirms that a short native pixel edit survives five
deferred navigation ticks with no pixel read, reaches the output after
navigation, and still reaches the output through explicit and stroke-release
forced scans. It runs with actual Blender images and CPU output evaluation;
navigation state is supplied because background Blender cannot orbit a view.

Repeat the CPU measurement:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background --factory-startup --python-exit-code 1 --python tests/performance/viewport_interaction.py -- --profile
```

Its JSON report is `.blender-test/viewport-interaction.json`. Omit `--profile`
for timings without profiler overhead. Run `tests/integration/navigation_scheduling.py`
and `tests/integration/contributor_regressions.py` with the same Blender flags
for pixel catch-up and graph reorder regressions.

## Cached-layer polling

The scheduler and status labels previously requested detailed diagnostics twice
per idle tick. Building those diagnostics searched the complete layer list for
each cached channel, even though the callers only consumed readiness and errors.
At 100 layers with four cached channels each, those repeated searches dominated
the otherwise idle scheduler.

Timer and status callers now use lightweight diagnostics. Explicit diagnostics
still report every cache state and the engine report by default. The full cache
state report also resolves its layer IDs once per call; it retains no RNA map
between calls, so cache publication, removed layers and undo cannot stale it.

On the same Windows Blender 5.2.2 installation, 40 samples after warming imports:

| Work (100 layers, 400 cached channels) | Before | After |
| --- | ---: | ---: |
| Idle scheduler median | 35.701 ms | 0.103 ms |
| Engine status median | 18.525 ms | 0.013 ms |
| Full diagnostics median | 18.167 ms | 2.383 ms |
| UI status median | not recorded | 0.036 ms |

The scheduler reduction is 99.7% for this metadata-only fixture. These figures
are CPU callback costs, not end-to-end viewport frame rates. The fixture uses
actual Blender layer/cache RNA and the actual graph cache-state resolver, with
one shared 512-square image to avoid allocating hundreds of textures merely to
measure metadata access. It performs no GPU compilation or compositing.

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background --factory-startup --python-exit-code 1 --python tests/performance/scheduler_diagnostics.py
```

The report is `.blender-test/scheduler-diagnostics.json`. The integration suite
`diagnostics_scheduling.py` verifies that idle/status polling performs no cache
detail or engine-report reads, full diagnostics reflect image/binding/layer
changes without an edit-serial bump, and both detail levels agree on readiness,
failure, pending-input and painting-freeze states.

## Graph fingerprint reuse

The graph still reads every structural RNA value on each sync. Renames, raw
collection edits and edits inside a replay can bypass `edit_serial`, so that
serial is not a safe cache key. Only conversion of an identical, immutable
content tuple into its serialized SHA-256 digest is memoized, with at most 64
entries. The serialized digest is unchanged for existing saved trees. Disabling
the extension clears the memo and the graph owner index.

On the same machine, 40 warmed samples with 100 layers and 200 masks, without
cProfile:

| Work | Before digest memo | After digest memo |
| --- | ---: | ---: |
| Structural signature median | 5.474 ms | 3.575 ms |
| Complete `mirror.sync` median | 7.570 ms | 5.482 ms |

This is a further 27.6% reduction in graph sync CPU time after the earlier row
optimization. The raw mutation regression in `graph_signature_cache.py` checks
live node labels, links, collection order, unchanged saved-digest semantics,
bounded retention and register/unregister cycles. Existing `graph_mirror.py`
also passes projection, incremental reconciliation, structural edits and undo.

## CPU preparation of GPU brush dabs

GPU strokes previously gathered every triangle's three world-space corners
and recomputed both bounds for every dab. A surface now keeps those bounds
alongside its immutable geometry snapshot, and gathers corners only for
triangles selected by the brush box and reach test. A replacement snapshot
builds fresh bounds. Bounds remain available while a canvas pins an evicted
surface, and their CPU bytes count toward active or retained resource usage.

A regular 200,000-triangle plane, 2048-square surface and 0.03-world-unit brush
radius gave these CPU-only results over 25 warmed dabs:

| Per-dab preparation | Before | After |
| --- | ---: | ---: |
| DEPTH reach median | 100.791 ms | 14.265 ms |
| SURFACE reach median | 123.284 ms | 36.670 ms |

The reductions are 85.8% and 70.3%, respectively. Both paths preserve the same
128-by-128 pixel dab rectangle; DEPTH admits all 200,000 triangles and SURFACE
admits 896. The new bounds retain 4,800,000 CPU bytes for this float32 mesh and
took 91.7 ms to build once for the surface. These figures exclude the original
one-time UV raster and GPU texture creation, and do not measure full stroke
latency or viewport frame rate.

`stroke_surface_bounds.py` verifies exact eligibility and UV rectangles against
an uncached per-triangle reference for both reach modes, two nearby sheets,
float32/float64 geometry, edge/miss/large brushes, changed snapshots and pinned
resource release. Repeat the dense-mesh CPU measurement with:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background --factory-startup --python-exit-code 1 --python tests/performance/stroke_surface_bounds.py
```

The report is `.blender-test/stroke-surface-bounds.json`. On DEPTH strokes the
canvas also retains its all-ones GPU eligibility texture for the gesture,
instead of uploading and discarding the identical texture for every dab.
SURFACE masks remain specific to each hit. For the measured mesh this avoids
an 802,816-byte R32F texture upload and allocation on subsequent DEPTH dabs;
the retained texture is counted as active memory and released with its canvas.

## Graph controls during redraw

Each fill node previously searched its channel collection 25 times while
building channel switches, values, texture mappings and the selected channel's
procedural controls. Those searches ran again whenever Blender redrew the
graph. A draw now builds one local channel map, preserving the first matching
entry and the original control order. The map is discarded after the draw, so
raw reorders, removals, replay and undo cannot leave retained RNA references.

A quiet paired CPU measurement used the same live 100-layer, 200-mask Blender
fixture for both callbacks. Across 50 alternating before/after samples, drawing
all 301 graph nodes took **22.844 ms before and 12.610 ms after**, a **44.8%**
reduction. This measures the real Python callbacks and RNA access with a no-op
layout recorder; native UI layout and GPU rendering are excluded. It is not a
displayed frame-rate measurement. The paired report is
`.blender-test/windows-vulkan/round4-graph-draw-paired.json`.

`tests/performance/viewport_interaction.py` now includes the
`graph_draw_callbacks` measurement. `graph_channel_controls.py` verifies the
actual RNA owners bound to switches, values, image pickers, shared mappings and
the inspector after channel disablement, reorder, removal, duplicate identifiers,
edits during replay and collection clearing. It also checks that drawing does
not alter the document. The graph signature and contributor regressions pass
with the new callback.
