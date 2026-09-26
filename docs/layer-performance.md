# Layer performance (0.9.0)

Four preview-side tools for heavy stacks. None of them changes what export, bakes,
published outputs or 2D composite snapshots produce: those keep evaluating the
authoritative full stack.

## Why

A shader-native texture set pays three kinds of cost the artist could not see or
control before 0.9.0:

1. **Compile cost** — Python time to build each unit's node group, plus node and
   sampler counts (the Metal fragment-sampler limit is 64).
2. **Boundary cost** — CPU rasters for operations the shader cannot express
   (legacy hashed noise, spatial filters such as blur or warp, mask prefixes
   behind an unsupported filter mask, vector strokes). Each is regenerated at
   full resolution whenever one of its inputs changes.
3. **Stroke-time cost** — every 33 ms of a brush stroke the scheduler reported
   the painted image as changed. Besides rebinding the canvas that ran the full
   structural-signature walk and re-identified every boundary whose identity
   folds in the painted image's pixel revision; each such boundary then
   allocated a new float image and restarted a full CPU job that never finished
   while the brush stayed down.

## Profile Layers

*Layers* tool strip → hourglass button, or *Performance* menu → *Profile Layers*
(`open_painter.profile_layers`, resolution 256/512/1024, default 512).

The pass composes every touched channel of the set on the CPU reference path at
the chosen resolution with a per-layer timer, then merges three sources into one
table per layer:

| Column | Source | Meaning |
| --- | --- | --- |
| Compile ms, nodes, samplers | every shader build/rehydration | the sum of the layer's units' build time, node count, unique images sampled. Adopted units keep the figure of the build that compiled them. |
| CPU inputs ms | every resource step | CPU boundary work attributed to the owning layer, cumulative until *Clear Profile*. A layer with a figure here re-renders after every edit inside it. |
| CPU reference ms | the profile pass | exclusive per-layer composite time; folders also record an inclusive figure. |
| GPU ms | the profile pass, engine sets | the layer's passes timed on the GPU at the set's resolution (`shader_preview.profile_engine`): what one full update of the live preview costs for that layer. |
| State | live | Live · Cached · Frozen · Stale · Pending · Bypassed |

`cost = compile + CPU inputs + CPU reference`. A layer gets a **badge** in its
row when its cost is at least 2 ms and 5 % of the set's total; the badge turns
to the error icon at 25 % or whenever the layer has CPU-input work. The
*Performance* section of the layer properties shows the breakdown, including the GPU
figure, and marks figures *out of date* after any edit.

## Engine Report

*Performance* menu → *Profile Engine* or *Copy Engine Report*
(`open_painter.engine_report`). The engine records every update by stage: build,
update, CPU inputs, translate, compile, render and publish. Profile Engine renders
every pass once more timed on the GPU, and each Mask Graph is a pass of its own. Both
entries copy a plain-text report to the clipboard and print it to the console. It covers
the machine (Blender, OS, GPU backend, renderer and driver), the last update, statistics
over the recent ones, the heaviest passes and the cost per layer. It is the report to
send when the preview is slow on a particular computer. The stages are described in
[the engine's documentation](open-painter-engine.md#measuring-it).

## Bypass in Preview

Row toggle (the "disable in viewport" glyph next to the eye) or the *Performance*
section, plus *Bypass Selected / Restore Selected / Restore All* in the menu.

A bypassed layer — and every layer under a bypassed folder — compiles as a
two-node pass-through: its content, mask and boundary units leave the plan, so
their CPU work stops and the material shrinks. Unlike hiding (a uniform), bypass
restructures the layer's units, so toggling it is a per-unit rebuild. Anchors
reading a bypassed layer's output see its backdrop. Export, bakes, published
outputs and 2D snapshots ignore bypass; CPU work done *for the preview*
(boundary factories and layer caches) honours it. Invalid content under a
bypassed layer never raises; the error surfaces when the layer is restored.

## Layer caches

*Performance* section → *Cache*: **Live** (default), **Cached**, **Frozen**.

A cached layer samples one managed image per channel instead of compiling its
chain. For a paint or fill layer the image holds the content through all its
effects as colour and *source alpha × mask coverage* as alpha; layer opacity,
visibility, channel opacity and blend mode stay live in the shader. For an
isolated folder the image holds the child composite before the folder blends;
the folder's own mask, opacity and blend stay live and its children are not
compiled at all. A passthrough folder cannot be cached — its composite depends
on the backdrop — and reports so; set it to Isolated or cache its children.

The compiler compares the row's stored provenance with the live identity of the
cached content. While they differ the layer is **stale** and the previous image
keeps showing:

- **Cached** requests a refresh that starts 0.3 s after the last edit inside the
  layer settled (and never while a stroke is in progress), runs in 16-row
  strips under the scheduler budget, and publishes atomically — a new image,
  the pointer swap and the provenance. A job whose inputs changed underneath it
  never publishes.
- **Frozen** keeps the image until *Refresh Cache* (`open_painter.layer_cache`,
  actions Refresh / Clear / Refresh All / Clear All).

Images are managed, packed on save and referenced from RNA, so undo, redo and
reopening keep them; the identity is built with datablock names so a persisted
cache still compares current after reload. A resolution change makes the cache
missing until it is refreshed at the new size. Duplicated layers start without
images; portable materials serialize cached layers as Live; removing a layer
removes its images. Each cached channel costs a float RGBA image at the set
resolution (256 MiB at 4k), shown in the *Performance* section; a folder only
caches the channels its children touch (a folder of colour, roughness, metallic
and height layers caches four images, its normal, AO, emission and opacity
channels pass their backdrop through live).

Limits: an anchor elsewhere that reads a child of a cached folder compiles that
child without a backdrop (a warning is reported). Cache images serve the shader
preview only; the CPU reference backend, export and snapshots evaluate live.

## Freeze While Painting

*Texture Set List* → **Freeze While Painting** (project setting, on by default),
also in the *Performance* menu.

While Blender's brush operator is modal — or was released less than 120 ms ago
— the scheduler treats the project as *frozen*:

- Each native paint input still bumps the image revision, marks the painted
  channel or mask dirty and rebinds the canvas sampler, so the viewport shows
  the stroke. No structural walk, boundary re-identification or resource
  request happens; the touched owner is remembered instead.
- Isolated-input steps, layer-cache refreshes and scene-field analysis pause.
  A frozen set never spins the timer; it waits at the painting rate.
- The status line reads *Frozen while painting · catches up after the stroke*.

On release — the scheduler's post-release check, ending a paint session,
switching targets, saving, rendering or turning the setting off — the remembered
owners get one update each: one regeneration per boundary that actually changed,
instead of one per input. Multi-set canvases catch up per member. Undo clears
the deferred table with the rest of the runtime; switching a set to the CPU
reference backend drops its deferred update; the CPU reference backend keeps its
own regional stroke evaluation and is not frozen. A regeneration queued in the
gap between two strokes is superseded by the next resume rather than finished
in parts.

## Diagnostics

`shader_preview.diagnostics(scene, ts)` adds `frozen`, `deferred_owners`,
`cache_states` (`layer:channel → CURRENT | STALE | MISSING`) and per-unit
`unit_build_ms`; update reports add `cache_requests` and `deferred`. Counters:
`shader_paint_deferrals`, `shader_paint_resumes`, `layer_cache_requests`,
`layer_cache_publications`, `layer_cache_failures`. Developer Mode lists the
frozen state and the per-layer profile in the texture-set panel.

## Tests and benchmark

Background: `tests/integration/layer_bypass.py`, `layer_cache.py`,
`layer_profile.py`, `paint_freeze.py`, plus the bypass and cache-mode probes in
`shader_structural_fields.py`. Real window: `paint_freeze_ui.py` drives an actual
brush drag with `--enable-event-simulate` and reports the tick time during the
stroke. Benchmark: `tests/performance/layer_performance.py` (stroke ticks with
the gate on and off, live versus cached heavy folder, bypass, profile pass);
the curated run is [layer-performance-0.9.0.json](performance/layer-performance-0.9.0.json).
At 2048 with 14 layers (two blurred fills in a folder, a blurred paint layer): the
scheduler tick during a 60-input stroke went from p50 17.3 ms / p95 28.7 ms with 60
boundary regenerations (gate off) to p50 0.97 ms / p95 6.5 ms with none (gate on); the
catch-up after release is the same single 2.2 s regeneration either way. The heavy folder
compiles in 110 ms with 288 nodes when cached against 260 ms and 2,098 nodes live, and 184
nodes bypassed; its cache refresh takes 12.3 s of cooperative CPU time, and a slider inside
it costs a 24 ms identity check per callback (the folder's twelve children are re-stamped),
which is why refreshes are debounced rather than immediate.
