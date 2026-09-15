# Shader-native authoring

The persistent layer, channel, mask and filter properties remain the editable
recipe. The default `LIVE` value now selects a Blender material shader graph.
Supported numeric edits update existing node input values synchronously. Texture
resolution determines authored paint and explicit bake output sizes; it does not
require a full-resolution raster evaluation for a supported shader slider edit.
Blender still redraws the material and scene. Source sampling, shader complexity
and the real viewport determine the resulting frame cost.

Bindings use Blender Value-node sockets. Blender's
[Value-node GPU implementation](https://github.com/blender/blender/blob/main/source/blender/nodes/shader/nodes/node_shader_value.cc)
passes these values as GPU uniforms. The actual-project measurements below check
the installed Blender version; this source reference does not establish frame latency.

## Why the previous path paused

Previously, `properties/mask.py:changed` called `properties/layer.py:changed` for
every enabled, strength, Levels, mapping and generator edit. That callback marked
every output image dirty and incremented the texture set's single `edit_serial`.
Layer controls used the same path. Channel controls restricted dirty outputs to
their own channel plus published-input consumers; channel-filter callbacks missed
that transitive consumer invalidation.

`evaluation/cache.py:tick` polled those flags. The CPU mode waited 80 ms, evaluated
row strips, then called `image_manager.write` for every dirty channel. That call
used `Image.pixels.foreach_set`, `Image.update`, advanced the image revision and
searched consumers. Full output publication cleared dirty flags and reported
READY. Reduced CPU previews added a separate temporary image publication before
the full-size result.

The former live GPU path avoided output CPU writes but still produced one complete
texture per channel. `live_preview.steps` captured a recipe snapshot, planned each
channel, resolved changed leaves, submitted composition and mipmap passes, then
published the resulting GPU image to the material. `live_display.publish` tagged
the material and requested redraw. Dirty CPU outputs deliberately remained dirty
until save/render/export flushed the CPU evaluator.

Its caches helped warm composition edits but did not make authoring shader-native:

| Previous edit | Remaining work |
| --- | --- |
| Layer/channel opacity, constant color or roughness | Reused unchanged leaves, but recomposited affected channel rasters and mipmaps. |
| Mask black/white/strength/invert | Changed the complete mask-stack key. Only six restricted untransformed generator kinds could regenerate entirely on GPU. |
| Generated image mask, image mapping or triplanar mask | The GPU mask preflight rejected the entire stack; CPU sampling created a scalar raster and uploaded it. |
| Height Levels output range or another channel filter | Presence of effects bypassed scalar modulation; a CPU RGBA source raster was generated and uploaded. |
| Published input | The global edit serial formed part of its mask key, so unrelated edits could invalidate the mask leaf. |
| Geometry transform with triplanar inputs | Geometry dependencies invalidated the set; cached mapped fields had to be resampled. |

Repeated RNA initialization callbacks could advance the global serial many times.
The scheduler discarded stale jobs at serial changes. Final-publication serial
checks correctly prevented obsolete pixels from replacing newer work, but they
could not remove the unnecessary raster dependency. The new design keeps these
explicit CPU evaluators available while removing them from default authoring.

## Runtime contract

`evaluation/shader_graph.py` owns compilation, binding indices, node dependency
closures and isolated raster boundaries. `evaluation/shader_preview.py` owns the
scene/texture-set graph lifetime:

1. Material repair calls `ensure(scene, ts)`. First construction and topology
   changes create the owned graph; numeric edits retain its node identities.
2. Property callbacks dirty the affected explicit-bake channels, increment the
   authored revision and call `notify(owner, context)`. An existing graph updates
   only matching changed bindings before the callback returns. Initialization
   callbacks do not build incomplete graphs.
3. `Graph.update(owner)` reports diagnostics and affected channels. A
   `RebuildRequired` exception queues topology work. Unexpected errors preserve
   the last valid graph and produce a visible FAILED status.
4. `cache.tick` never enters full CPU or former live texture composition for a
   native texture set. It repairs queued topology and calls the regular bounded
   `Graph.step_resources(budget=.006)` method for isolated unsupported inputs.
5. Source image nodes use the authored images directly. Native brush updates do
   not require full canvas readback on every timer tick; release/save detection
   still records authored source changes.
6. Save and render finish pending isolated resources and pack owned source images.
   They do not bake the complete native layer stack. Explicit evaluation/export
   still produces authoritative CPU channel textures, then material repair
   reattaches the editable shader graph.

`current` means the shader is attached to the visible surface adapter, reflects
the current authored revision, and has no pending resource job or error. Calling
`ensure` only compiles a graph; the adapter records successful wiring separately,
and the timer repairs an unattached replacement even when its bindings are fresh.
`diagnostics` exposes `state`, `serial`, `attached`,
`bound_serial`, `pending_resources`, `rebuild_pending`, `error`, and compiler
diagnostics. States are SHADER, BOUNDARY, QUEUED, FAILED, and explicit CPU.
Authored output dirty flags have a separate meaning: an explicit texture bake is
out of date. They do not mean the native material is stale.
The runtime's `current` flag verifies bindings, attachment and resource readiness;
it does not certify that Blender has displayed a new frame or that its appearance
matches the intended material.

Surface controls (coat, anisotropy, height distance and AO preview) update their
existing Principled/Bump/mix sockets directly. They do not dirty channel pixels.
The normal exporter uses the current height distance when combining evaluated
height with the independent authored normal channel.

## Units and rebuild scope

Blender re-validates a node tree on every `nodes.new` and `links.new`, so a flat
tree of N nodes costs O(N²) to construct. `Graph.build` therefore emits one
`ShaderNodeTree` per *unit* and a small top tree that instances and chains them:

| Unit key | Content | Interface |
| --- | --- | --- |
| `('CHANNEL', layer_id, channel)` | layer opacity × visibility × channel opacity × mask coverage, then the blend of the layer's source (or a folder's grouped children) over the backdrop | `In` (+ `In:Alpha` inside isolated folders), `Grouped[:Alpha]` for folders, `UV/Position/Normal`, `Ref*` → `Out[:Alpha]` |
| `('SRC', layer_id, channel, count)` | channel content after the first `count` effects; continues from the longest shorter published prefix | coordinates, `Ref*` → `Color`, `Alpha` |
| `('MASK', layer_id, through, pre_filter)` | mask coverage through one mask (or the whole stack) | coordinates, `Ref*` → `Value` |

Every `source()`/`mask()` call from another unit imports the producer through group
sockets (`Ref<n>` inputs), so an anchor consumer and the owning layer share one
chain; constants are inlined. The import graph is a DAG by construction (a cycle is
refused while compiling). The top tree holds the coordinate nodes, the instances,
the per-channel `Final:` normalisation and the group output; wiring follows the
CPU evaluator's composition order. Blender inlines groups when generating shader
code, so the GPU program is the same as the flat tree's.

Structure is described by a global signature (resolution, baked maps), the wiring
(layer order, parents, folder modes, touched channels) and one signature per unit.
Layer visibility and the `enabled` flag of pointwise effects are uniforms (hidden
layers compile behind a zero coverage; hidden content that cannot compile becomes a
pass-through stub with a warning). `Graph.update` raises `RebuildRequired` with the
mismatching unit keys; `Graph.build(reuse=previous)` adopts every unit whose
signature *and RNA owner addresses* are unchanged (collection reallocation moves
PropertyGroups, so captured references are only trusted while every owner keeps its
address) and recompiles the rest. Numeric property callbacks tell the session the
structure is unchanged and skip the signature walk.

Value nodes, image samplers and boundary samplers carry JSON descriptors keyed by
stable ids; unit trees record flags, exports and compiled effects; the top tree
records the signature it was built for. After undo, redo, reload or re-enabling
the add-on, `Graph.rehydrate` adopts the persisted graph when the signature still
matches, rebuilds all bindings and the dependency closure from the trees and runs
a full binding update to correct any drifted uniform; otherwise it compiles
normally. Developer Mode lists units, node counts, last build/reuse figures and
unique images against `gpu.capabilities.max_textures_frag_get()`.

## Explicit 2D snapshots and painting

The Image Editor requires an image, so opening a native 2D composite requests one
selected CPU channel through `composite_snapshot.request`. Other channel outputs
remain unallocated or dirty. The panel labels the result as a snapshot and adds
“out of date” when a dependent edit changes its recipe. Refresh Composite Snapshot
is an explicit one-channel raster request. Shader slider edits never refresh that
image in the background. Changing the composite-channel selector takes effect
when the artist refreshes the snapshot.

An active native paint session always displays its authored source canvas instead
of a composite. Finishing a native paint session keeps that source image visible
in View mode and updates the shader; it does not composite the stack. Requesting
a snapshot afterward produces a distinct image and leaves the painted source
unchanged. Explicit CPU preview retains its existing image-view behavior.

Switching from CPU reference back to native authoring immediately reconnects the
existing procedural group even when its cached bindings are already current.
Disabling the extension detaches runtime bindings while keeping the last usable
shader group and referenced images in the material.

## Operation compatibility

| Operation | Native authoring behavior | When pixels or topology work are needed |
| --- | --- | --- |
| Constant channel values, layer/channel opacity | Existing value sockets update directly. | None for a numeric edit. |
| Image fills, generated scalar detail, painted images, baked-map masks | Image nodes sample existing source images. UV and triplanar image scale, rotation, offset and hardness are bindings. | Replacing an image updates its image binding; changing source/projection/sampling types can rebuild topology. |
| Mask black/white, strength, inversion and supported generator amount/contrast/breakup | Existing mask arithmetic sockets update directly, including downstream published consumers. | No mask-stack raster is generated for these controls. |
| Edge wear, chips, dirt, cavity dirt, rust, dust/orientation, IDs, position, utility-map generators | Baked-map sampling and combining math run in the shader. | Legacy hashed FBM/Voronoi/scratch/drip fields are cached source boundaries; changing their seed/scale regenerates the field. |
| Solid, linear/radial gradients, checker, stripes, dots, grid, tile and brick patterns | Analytic shader nodes evaluate these fields and their mapping. | Changing operation type rebuilds its graph. |
| Other legacy procedural fields | Their established CPU math creates an isolated reusable source image; color/roughness/height modulation remains native. | Changing a field input or its legacy projection mapping refreshes that boundary. |
| Levels/remaps, gamma, invert/clamp, contrast, threshold/histogram, posterize/quantize, color/HSL operations, normal strength/combine | Pointwise effects compile to native nodes. The compatibility list is `shader_effects.POINTWISE`. | An operation outside that list requests an isolated source/filter-prefix boundary. |
| Blur, morphology, warp and other spatial filters | Shader composition consumes a separately cached result for the affected source/filter prefix. Downstream opacity and Levels remain native. | Changing an input used by that prefix reruns it; a full-frame footprint can still require full-size work for that prefix. |
| Height shading and authored tangent normal composition | The fixed surface adapter uses Normal Map plus Bump nodes; coat/anisotropy/height distance are direct sockets. | Generating a tangent normal image from a procedural/detail source remains a derivative boundary. Exported normals combine evaluated height explicitly. |
| Scene analysis fields | The graph samples their isolated analysis result. Manual/live/frozen analysis rules remain meaningful. | Changed geometry or explicit analysis inputs can refresh the bounded analysis/source resource. |
| Published layer channel/mask references, folders and blend modes | The compiler links the actual source stage and native composition math; it tracks their dependency closure. | Layer order, parenting, enable/visibility, operation type and reference target/stage changes may rebuild topology. |
| 2D composite or exported textures | These are explicit pixel consumers. A 2D snapshot evaluates one channel; the current exporter evaluates every dirty output channel before encoding its selected preset. | Their cost depends on requested resolution and recipe. They are never silently invoked by a native slider. |

These boundaries preserve the existing CPU definitions instead of replacing
legacy hashed sources with visually different built-in Blender noise. The graph
and diagnostics make the remaining pixel work explicit. A supported downstream
slider does not invalidate a cached boundary whose own inputs did not change.

## Dependency instrumentation and validation

Instrumentation must attribute an edit to its owner and changed field, list
changed bindings and dependency-closure nodes, and distinguish those logical
changes from actual GPU execution. It must separately count graph builds,
boundary invalidations/evaluations, CPU source calls, image reads/writes and bytes,
old live composition passes, callback duration, and time until the displayed
material changes. `Metrics` already supports opt-in counters, phases and sample
distributions; the native runtime records callback duration, binding updates,
topology invalidations, graph builds, resource steps and failures.

Enable Developer Mode in extension preferences to log each actual binding update
with `parameter_changes`, `invalidated_nodes`, `affected_channels`,
`updated_bindings`, `raster_boundaries_queued`, `submission_ms` and
`texture_bake_triggered`. Resource publications have separate counters. These
lists describe dependency invalidation; Blender does not expose execution counts
for individual shader nodes. Updating one binding does not mean that only one
node runs on the GPU: Blender may re-evaluate the complete material for visible
pixels and redraw the scene. The add-on narrows Python binding work and cached
resource invalidation; it does not schedule a partial GPU shader execution.
Submission milliseconds measure synchronous Python and node updates, not a
completed viewport frame.

The same information is available from Blender's Python Console:

```python
import bpy
from importlib import import_module
addon = next(name for name in bpy.context.preferences.addons
             if name == 'open_painter' or name.endswith('.open_painter'))
runtime = import_module(addon + '.evaluation.shader_preview')
scene = bpy.context.scene
ts = scene.open_painter.texture_sets[scene.open_painter.active_texture_set]
runtime.diagnostics(scene, ts)
```

For a source checkout imported as `open_painter`, import
`open_painter.evaluation.shader_preview` directly. `graph_builds`, `binding_updates`
and `resource_steps` are cumulative within the current runtime session. Native
save/render does not reset them or conceal a pending texture bake.

The meaningful test matrix includes constant channel values, layer/channel
opacity, mask Levels/strength/inversion, UV/triplanar transforms, channel Levels,
painted inputs, source-image replacement, published dependencies, unsupported
filters, structural layer edits, save/reopen and explicit export. Tests should
keep real callbacks and timers enabled, warm initial resources, then verify:

- Supported numeric edits keep group/node identities and perform zero complete
  channel evaluations, old live composition calls and image writes.
- Only actual dependent bindings/nodes change; unrelated channels and cached
  unsupported inputs remain untouched.
- A changed unsupported input refreshes its own boundary, preserving unrelated
  resources and shader structure.
- Reordered/added/deleted layers rebuild correctly, with a failed replacement
  retaining the last valid material and visible error.
- Native save/reopen preserves the recipe and image sources without a full-stack
  bake; explicit CPU output/export still matches its numerical reference.
- Held-slider interaction changes the real viewport frame while the mouse is
  held. Submit time alone is not presented as visible frame latency.

Existing `live_input_dependencies`, `immutable_jobs`, `resource_invalidation`,
`live_masks`, and `live_surface_relief` remain reference coverage for the old
explicit raster path. New native runtime and GUI tests cover the new authoring
contract rather than weakening those old numerical assertions.


## Validation on the supplied project

**Current acceptance scope:** the actual-project authoring trace,
save/reopen/export, and held Cavity Dirt strength slider checks have passed.
The visual check uses a native RNA slider in an isolated test sidebar; navigation
through the product's layers and property panels is not covered. Archive
installation is validated separately; these viewport results do not cover it.

The primary project validation uses `open painter test.blend` at its saved
**2048 × 2048** resolution. It retains ten mesh objects, **215,185 vertices and
315,225 polygons**, all UVs, eleven 2K mesh maps, and the existing six-layer Worn
Painted Metal stack. The material is assigned to `defaultMaterial.003`; the other
nine components and their material assignments remain intact. Original source
file SHA-256: `dc8d39b8faadf7e67764e7c6b2c6c2d47101ad23bd7440579226664fa3b6af79`.

A single [authoring trace](validation/shader-user-project-authoring.json) exercised
seven controls: Chips amount, abrasion black level, rust amount, recess dirt,
blue-coat opacity, coating height output and steel roughness. Each ran 49 changes
in both directions, with normal scheduler ticks. All retained the same 1,474-node
graph, allocated no raster jobs and wrote no source or output images. Median
callback-plus-scheduler time ranged from 1.45 to 1.61 ms; the slowest control's
p95 was 6.13 ms. These are submission measurements, not visible frame latency.
Invalid activation of the existing coating Levels effect retained the valid graph
and recovered when its values were restored. The original .blend remained unchanged.

The measured headless first build on this project took 5.08 seconds, with a further
3.52 seconds for isolated source preparation under concurrent validation load.
Opening the real viewport also includes file loading, source inspection and
initial Metal shader compilation; these startup costs are distinct from the warm
slider path. No lower-resolution version of the project was used.

The [actual-project save/export validation](validation/actual-project-persistence-export.json)
preserved all fifteen authored image/map hashes, the complete recipe, all mesh
positions/topology and UV hashes through native save and reopen. The actual Chips
edit and save made zero channel-evaluation and image-write calls. Explicit export
then evaluated eight channels and wrote eight 2048 PNG16 files. Independently
decoded roughness, height and combined normal match current evaluated output;
normal XY RMS is 0.06227, with normalization error 1.79e-7. The original file is
byte-identical after the complete process.

The [actual-project held-slider report](validation/shader-user-project-ui.json)
records eight back-and-forth changes of the existing Recess dirt → Cavity Dirt
**strength**, approximately 0.15 ↔ 0.846, during one mouse press. Each change was
visible in the captured material region before the next movement and before
release. All retained the same graph and updated one parameter binding; there
were zero CPU or legacy GPU composites, texture reads/writes, isolated resource
publications, or graph rebuilds. All ten meshes, fifteen authored source/map
images, saved lighting, and the 2048 texture-set resolution were preserved.
[View the actual-project captures](validation/shader-user-project-contact.html).

Observed event-to-completed-capture times were **295–395 ms**, median **309.94 ms**.
They include **200–240 ms** saving the screenshot and **51–58 ms** decoding and
reading it. The 8×8 block-averaged material RGB difference was 0.0151–0.0257 across
the eight reversals, separating persistent visible changes from fine-detail
temporal antialiasing. These are completed screenshot observations, not display
scanout latency or a 14 ms viewport claim. The check establishes this control's
visible response while held; it does not establish every control's frame time.

The earlier Chips attempt did not establish visible responsiveness. The
[saved-mask diagnosis](validation/shader-user-project-chip-diagnosis.json) found
that only 0.01829% of the saved curvature texels exceed 0.01; changing Chips amount
changed only 0.01237% of mask texels by more than 0.01. Its mean surviving blue
coverage change was 0.00002935, too sparse for the whole-region visual threshold.
Those CPU mask statistics explain the chosen test control; they are neither a
Chips viewport PASS nor a measurement of GPU execution. No source map or recipe
structure was replaced for the final Cavity Dirt check; the tested strength was
restored afterward.

## Secondary development evidence and remaining costs

The following results come from smaller constructed fixtures and catalog checks
run earlier in development. They establish their specific numerical and runtime
contracts; they do not establish visual correctness or interaction speed on
`open painter test.blend` and are not the remaining acceptance path.

On Blender 5.2.1 LTS / macOS Metal, 58 shader arithmetic swatches across two
parameter revisions matched the CPU reference within 5.96e-8; 24 image,
projection, generator, filter and published-source comparisons matched within
1.11e-5. All 50 catalog recipes compiled. See the
[source comparisons](validation/shader-sources.json),
[runtime dependencies](validation/shader-runtime.json) and
[isolated boundary checks](validation/shader-boundaries.json).

The 2,834-node Post-Apocalyptic material processed 121 actual mask RNA assignments
with median 2.87 ms, p95 3.40 ms and maximum 4.07 ms submission time. It retained
all node identities and performed no source generation, masking, filtering,
blending, image read/write or compatibility-resource requests. These timings
measure Python/RNA submission, not GPU completion or displayed frame rate.
[Heavy-material timing record](validation/shader-heavy-material-timing.json).

Those 0.2.7 timings were taken on the flat single-tree compiler, whose construction
was quadratic in node count (19.7 s for that material). Since 0.3.0 the graph is
compiled per unit (see [Units and rebuild scope](#units-and-rebuild-scope)): the same
material builds in 0.70 s, a blend-mode change rebuilds 4 of its 73 units in 80 ms,
hiding a layer writes one uniform in 2.8 ms, and an undo rehydrates the session in
tens of milliseconds. Records: [construction per material](performance/shader-build-0.3.0.json)
against the [0.2.9 baseline](performance/shader-build-0.2.9.json). None of this is
evidence that arbitrary materials or every operation meet a 14 ms viewport target.

The [2D workflow test](validation/shader-snapshot-ui.json) verifies an explicit
one-channel composite snapshot, a visibly stale state after a shader edit,
explicit refresh, distinct authored paint canvas, and no automatic bake when
finishing a stroke.


The smaller real-window [held-slider report](validation/shader-preview-ui.json) verifies
six native mask edits in directions `+,+,-,-,+,-` during one held mouse press,
with a changed material region observed before each following movement and before
release. All six updated one binding, preserved the graph and source hashes, and
triggered zero CPU/legacy compositing, image writes or compatibility publications.
[View the captured material frames](validation/shader-preview-contact.html).
The [rendered functional test](validation/shader-preview-functional.json) also
checks seven property edits, reversible noise-input changes, visibility/reorder,
save/reload, and unchanged authored sources.

Observed event-to-captured-frame times were 272–389 ms (median 300.5 ms), including
193–245 ms of PNG screenshot saving and 61–67 ms of image load/readback. This test
proves material changes during the drag, but its capture overhead prevents using
those numbers as clean viewport latency or a 14 ms frame-rate claim. The frame
captures are outside the authoring path and are not preview textures.
