# Architecture

## Project data model

`Scene.open_painter` owns the persistent project. Texture sets store UUIDs, resolution,
explicit object/material-slot assignments, layer stacks, baked-map pointers, export and bake settings.
Layer records hold stable IDs, parent IDs, visibility, opacity, order, blend mode, per-channel
properties and sequential mask collections. Pointer properties survive object renames and reloads.
RNA values are snapshotted before collection mutations because Blender may invalidate collection
item references after additions or moves. Imports remap IDs and reject invalid folder hierarchies.

The package root only delegates lifecycle. `registration.py` is the single ordered registry for
PropertyGroups, operators, UILists, panels, timers and persistent save/load/undo/redo handlers.
The build script places package contents and manifest at ZIP root. Runtime library files use the
Blender user data directory or a user-chosen directory, never the installed extension directory.

## Layer evaluation

UDIM sets use ordinary managed images, one set per tile, rather than depending on
Blender's ambiguous `Image.pixels` access for tiled images. `core/udim.py` validates
UV faces and routes them through owned material slots while retaining original
slots. Persistent set metadata records the tile, source UV name, group and export
stem. Geometry snapshots subtract the tile origin; native and CPU display shaders
read the named source map and apply the same translation. Cycles translates only
staged mesh copies. Native paint uses an owned derived UV map and restores the
previous active map with the existing face-selection backup. Derived paint UVs
do not invalidate bake provenance. Export naming has one shared tile suffix rule.
See [UDIM workflow](docs/udim-workflow.md).

Default authoring uses the Open Painter engine (`evaluation/engine/`), managed per set by
`shader_preview.py`. The stack compiler (`shader_graph.Graph`, running as
`engine.compiler.EngineGraph` on in-memory `engine.headless` node trees) splits the stack into
compilation units: a layer channel (coverage and blend), a channel source prefix (content
after the first `count` effects) or a mask prefix (coverage through one mask). Each unit is
translated to one GPU program (`engine.glsl`, catalog procedurals in `engine.procedural_glsl`)
and rendered in texture space, one pass per unit, bottom to top per channel; the result is
padded across UV seams with the exporter's donor map and written with its mip chain into the
set's channel image (`engine.runtime`). Materials read those images through one **Open Painter
Texture Set** node (`core/texture_set_node.py`): no edit changes a material. Parameters are
uniform buffers and images are sampler bindings, so programs depend on structure only and are
shared between units of the same shape; property changes bind values and mark the session,
and the next scheduler tick re-renders the channels whose stamps (program, values, image
identities, geometry, what the unit reads) changed, reusing retained results and the
checkpoint below the lowest changed layer. Structural edits carry per-unit signatures: only
units whose signature or RNA owner addresses changed are recompiled, the rest are adopted.
Undo, redo and reload compile the restored stack again and adopt the CPU inputs it binds.
Original images remain editable inputs. See [the engine](docs/open-painter-engine.md) and the
[callback trace, operation boundaries and diagnostics](docs/shader-preview-architecture.md).
`engine.stages` times every update by stage (build, update, CPU inputs, translate, compile,
render, publish), and `Engine.profile` times each pass on the GPU for the engine report.

Custom Graph masks read a **Mask Graph** (`procedural_graph/`), built from Open Painter's own
node types. It's an `OpenPainterGraph` tree (the one Open Painter node editor) marked
`op_mask_graph`.

- Its nodes appear only in Mask Graphs, and the Open Graph's layer nodes only in texture-set
  graphs.
- Edit Mask Graph enters it on the texture set's graph editor path. Tab returns.
- Graphs saved as their own tree type by earlier 0.13 builds load through a hidden type
  (`poll` False, so it's never an editor). `convert_legacy` rebuilds them on `load_post`.

- `program.build` reads the tree into ordered steps, validating links itself, since
  `NodeTree.update` runs before Blender validates a new link.
- `gpu.emit` compiles the steps into the graph's own engine unit, `('GRAPH', layer, mask)`,
  never inlined into its consumer. Every number is a uniform bound to the node that owns it.
- `cpu.evaluate` runs the same formulas in NumPy for export.
- `edits` routes property callbacks and `NodeTree.update` to the masks using the graph. A
  numeric edit writes uniforms. A structural edit, detected by `program.unit_signature`
  (structure plus the node addresses carrying it), rebuilds that unit alone.
- Before reading a graph binding, `update()` checks that its node, socket or stop still
  lives in the tree with the same RNA type. A removed node, reallocated stops or a deleted
  graph rebuild the unit instead.

Masks from earlier files that point at a shader group use the CPU reference. See
[Mask Graphs](docs/mask-graphs.md).

Scene analysis, neighbourhood filters, strokes, mesh masks, legacy value noise and metric
material mapping are isolated cached CPU inputs (leaves). Only a changed input is evaluated
again; levels, mixing and pointwise effects stay in the programs. Saves write the leaves the
engine binds and pack the authored sources; the engine's channel images are re-rendered after
the file opens (a CPU-mode set's outputs are packed). Explicit export and CPU reference mode
use `LayerEvaluator`, which the engine matches (`tests/integration/engine_parity.py`).

The older `live_preview.py`, `live_gpu.py` and `live_display.py` texture compositor
remains available for reference regressions; it is no longer the default edit path.

`LayerEvaluator` walks bottom to top into one float image per channel. Constant layers remain
small broadcast arrays until a raster source requires full pixels. Raster operations use 16-row
tiles. Blend math is independent of Blender. RGB values are linear; sRGB image inputs are decoded.
Scalar channels use the red component. Paint images allocate lazily and use alpha for coverage.
Normal results are normalized in signed vector space. Isolated folders use premultiplied
source-over composition with explicit backdrop coverage: blending only interacts where lower
content exists. Passthrough folders evaluate children against the current parent RGB and coverage,
then interpolate the completed change by group opacity and mask. This also handles nested groups.
Channel opacity multiplies layer opacity; channel blend overrides default to INHERIT for backwards
compatibility. New folders have eight enabled channel records; old folders without records retain
neutral behavior and initialize settings when edited. Disabled channels/layers/blends are skipped.
Output image alpha remains opaque; the separate OPACITY channel drives the display material alpha.

The CPU reference mask stacks resolve painted data, baked maps or generators, followed by levels, invert and
normal/multiply/add operations. Procedural masks and ordered texture filters are also evaluated as grayscale pixels. Missing required maps produce an actionable message. Triplanar fills
rasterize assigned mesh UV triangles into cached world position/normal images;
three projected texture samples are weighted by normal components. CPU automatic
updates run cooperative row-tile jobs with a 6 ms work budget. Geometry dependency
signatures permit reuse after material-only edits; saves/exports finish synchronously.

## Image ownership

Owned images have `open_painter_managed = True` and fake users. Output images also record the texture
set and channel. Imported user image pointers are not claimed or deleted. Editable duplicated raster
layers receive separate images. Managed images are packed on save; output pixels and baked maps remain
usable without the extension. Generated output image identities remain stable across reevaluation.
Removal deliberately leaves owned datablocks recoverable for manual orphan cleanup.

## Material ownership

Each texture set gets a managed material of three nodes: the Open Painter Texture Set node, a
Principled BSDF and a Material Output. A new material keeps the BSDF and output Blender creates
for it. The node feeds the BSDF directly: Base Color, Metallic, Roughness, Normal, Emission,
Alpha and Tangent. Everything else the surface shows lives inside the node's hidden per-set
group, never in the material:

- the eight channel samplers;
- the normal map with the height as bump;
- AO in the preview (Base Color);
- the UV tangent;
- Engine Preview's target adapters;
- View Mask's snapshot;
- the images a CPU-reference display binds instead of the set's own.

Scalar anisotropy, clearcoat and specular settings control display response on the BSDF. The
node can also be added to any material (Add ▸ Input). Nodes carry ownership plus stable role
tags. Repair works as follows:

- It reconnects the three nodes and retains unrelated nodes.
- It removes the wrapper nodes of earlier layouts: Normal Map, Bump, AO multiply, UV Tangent,
  per-channel images, engine adapters and the Procedural Graph group.
- It removes the untouched default BSDF and output that earlier versions left beside their own. Assigning a mesh preserves
its prior material per slot. Unassignment restores it only if the slot still references this
texture set's material. Slots are retained to avoid silently reindexing other assignments.

## Baking

Explicit high/low lists are validated before Cycles work. Utility maps and the triplanar
geometry raster rasterize all UV triangles of a target in batches (`geometry/uv_rasterizer.rasterize`,
same float32 barycentric sequence as the per-triangle path, later triangle wins);
`geometry/snapshots.targets` exposes triangle index arrays and builds RNA triangle lists only on
request. Temporary per-slot materials own active
bake image nodes. Unassigned slots use a scratch target. Normal, AO, the Bevel maps and the
Curvature edge set are Cycles bakes (the edge set runs a transient Bevel-normal/cross-product
shader, one reach per Bevel radius multiple). Position, world/object normals, material/object
IDs, UV islands/borders and raycast thickness use evaluated-mesh utility baking. A `finally` block restores material slots, render engine,
Cycles device/samples, bake properties, visibility, selection, active object and mode. Temporary
materials and scratch images are removed. Successfully completed maps are registered independently.
A rebake overrides the live map image in place (same datablock and name, so no
.001 duplicates and every bound channel keeps reading it); a failed rebake
restores its pixels instead of dropping the image. Baked map use in shading is explicit via a generated fill layer.

## Material aging (0.10.0)

`OP_TextureSet.aging` (`properties/aging.py`) holds what the thing is (material, conversion,
primer, paint, top coat, paint colour, coating condition), how old (`age_years`), where
(environment preset + exposure sliders, or `periods`), how used (`usage` + `use_*`), how kept
(`maintenance` + `care_*`, dated `events`), the seed and the art-direction multipliers. Every
update callback runs `engine.apply` while `live` and a folder exist; preset callbacks copy their
values under `engine.batch()` so the copy is one re-drive, and a slider moved off its preset
turns the selector to CUSTOM. `aging/model.py` (pure Python) integrates the history monthly:
`State` (24 outcomes), `Construction`, `Segment`, `Event`, `step`, `apply_event`, `simulate`,
`severity`/`label`, `readout`, `directed`. `aging/document.py` turns a state into a catalog-style
recipe with a role per layer; `aging/engine.py` builds the isolated *Material Aging* folder through
`composer._fill` (raising `MissingMeshMaps` before touching the stack) and then drives it:
`_driven_values` per role with `mask_pairs` (spec masks paired to built masks by kind, so optional
geometry masks the folder was built without are skipped and a restructured chain stops the
pairing), `_read`/`_write` by field path, an override record in `aging.state_data` (`resolved`
values and `overrides` keys), `_stale` roster comparison for rebuilds, `optional_maps` /
`rebuild_pending` for the panel's bake and Rebuild hints (`composer.mask_available` decides
whether an optional recipe mask is built), `prediction` memoised on `signature(aging)`, `follow_animation` from
`dependencies.refresh`, `export_settings`/`import_settings` for the serializer, loader and set
duplicate. Layers carry `OP_Layer.aging_role` (an RNA field, so snapshots, documents and copies
keep it).

## Decal objects (0.9.6)

`OP_Layer.decal_object` (an image Empty, `open_painter_decal` = layer id) is the projection frame
of channels with `projection == 'DECAL'`. CPU: `painting/decals.sample` maps the world-position
raster into decal-local coordinates, gates by depth, facing and softness, samples the image
(clipped) and rotates normal images through the Empty's axes into the per-texel tangent frame
(`painting/projection.tangents`). Shader: `_decal_signal` compiles a Texture Coordinate node bound
to the Empty with depth/facing/softness/axes uniforms (`decal_axes`, `decal_scale`, mirrored by
`decals.sync` from `dependencies.refresh`, which also adopts duplicated Empties as new layers).
`geometry_key` carries every decal frame so moves invalidate the set; the Empty's display size and
image offset are part of the SRC unit signature.

## Physical scale (0.9.5)

`evaluation/physical.density(ts)` is √(world area / UV area) over the assigned faces, cached by
assignment records; `texel_size` divides by the resolution. Owners with `units == 'PHYSICAL'`
carry metre fields (`feature_size`, `physical_radius`); `expected(owner, ts)` maps them to the
texture fields the evaluators and shader bindings read, and `sync` writes them through RNA (their
own callbacks notify). Sync runs from the metre fields' callbacks, from the owners' structural
callbacks (projection/source/kind change the mapping), from `dependencies.refresh` (geometry;
`uses_geometry` covers Physical sets), assignment changes, `resolution_changed`, material import
and file load. `lock`/`unlock` convert between the two authoring modes without changing values.

## Surface filters (0.9.4)

`OP_TextureEffect.space` selects UV or Surface space for neighbourhood effects
(`operation_contracts.SURFACE_KINDS`). Surface effects report a whole-image footprint, so
`channel_eval.source`, the mask stack and the shader's isolated filter inputs evaluate the full
raster once and cache it. `geometry/seams.SeamMap` (cached per set, resolution and evaluated
geometry) holds island coverage, per-island bands of outside texels mapped across their seams
(first-order continuation of the adjacent face, corner squares clamped to the edge ends), and
per-triangle tangent Jacobians. `channel_eval._apply_per_island` pads the image once from the
closest seams, then filters each island's crop with its own band re-padded and writes back the
island's texels; directional effects receive a per-texel UV direction of a world vector.

## Mask architecture (0.9.3)

A mask stack is a flat ordered list where every entry is a source with its own operation,
strength, levels and invert. `source == 'GROUP'` entries are followed by their contiguous member
run (`parent_id` chains; `core/mask_stack.py` keeps runs contiguous through moves, adoption,
lifting and dissolving). `mask_eval._stack` and `shader_graph._mask_chain` walk the list
recursively: a group's members fold among themselves, then the group folds into the outer
result like any source; continuations of published prefixes never start inside a run.
`source == 'GRAPH'` instances the artist's `ShaderNodeTree` natively in the mask unit
(`_custom_graph`) and, for the CPU reference, bakes its first output with Cycles through the
bake staging (`evaluation/custom_graph.py`) under a content signature of the tree.

## Mesh masks (0.9.2)

`OP_Mask.source == 'MESH'` reads an `OP_MeshSelector` (kind plus object/collection/material
pointers mirrored by name, attribute name, face set, island, direction). `evaluation/mesh_masks`
rasterizes the assigned faces through the render UVs with per-face membership or per-corner
weights and caches the raster under `signature(ts, mask)`, which names collection membership,
slot and original materials, mesh epochs, selection tokens and transforms as the kind needs.
The CPU stack and the shader boundary (`_mask_source_boundary`) key on that signature;
`dependencies.geometry_key` carries the signatures and `on_depsgraph` treats collection,
material and object updates as relevant, so `refresh` invalidates a set whose masks changed.

## Bake Doctor (0.9.1)

`baking/doctor.py` runs exact, non-destructive checks whose `Finding`s carry base-mesh
faces, vertices, edges or object names; `select` highlights them (selection is the only
change). `bake_manager.bake_steps` runs the pre-bake tier first. `baking/corrections.py`
turns painted correction images into geometry and passes at bake time: distance, skew and
direction are sampled per vertex of the staged render-evaluated target (corners nudged inside
their faces) into an owned cage per part that Cycles bakes with; source and AO exclusions are
second passes composited by the painted weight; `rebake_region` routes untouched faces to the
scratch image and composites the result into the registered map. The brush is the native paint
session targeting a correction image (`project.paint_correction`); the shader ignores it, the
scheduler rebuilds the cage overlay and triggers the live region rebake once a stroke settles,
and correction revisions enter map provenance.

## Caching and painting

Dirty flags describe explicit baked outputs independently of native shader state.
Property callbacks retain prior output invalidations and add only dependent
channels. Parameter-owner bindings and published source stages narrow the shader
update. Source/geometry identities invalidate affected compatibility inputs.
Native edits have no debounce or full texture job. CPU reference mode retains its
cooperative raster scheduler and 80 ms debounce. Runtime shader resources and
bindings are reconstructed from the persisted node groups after load/undo/redo.
The scheduler timer polls at 10 Hz when idle and is re-armed immediately by a
structural edit; 60 Hz is used only while painting or while a shader session has
topology or isolated inputs left to finish. While a brush stroke is in progress
the project is frozen by default: boundary re-identification, cache refreshes and
scene-field analysis wait and catch up once on release (see
[Layer performance](#layer-performance-090)).

The paint bridge activates a native essential brush and selects `image_paint.canvas`. Persistent
paint references identify the layer/channel/mask. Eligible unfiltered float paint
uses the native GPU canvas and revision notifications without full CPU scans.
For other paint paths, while a brush is active, raster snapshots are
compared at most every 33 ms when no partial job is running. Changed rows drive regional evaluation
only when earlier output data is valid; otherwise a full evaluation is scheduled. Snapshot comparison
also detects erasing. Runtime snapshots and pending jobs are cleared on lifecycle changes. A future stroke-replication layer can reuse the explicit channel target and invalidation API.

## Serialization and export

Smart materials contain versioned JSON and optional EXR assets in a ZIP container. Import validates
size, version and folder hierarchy before applying, remaps all IDs, packs assets, and rolls back new
layers/images if loading fails. Local library deletion verifies the chosen directory. Legacy built-ins are read-only JSON materials; the 50 authored catalog recipes expand into editable
layer stacks before serialization. The writer emits format version 2 and the reader also accepts version 1. Filesystem writes are distinct from Blender undo.

Export evaluates dirty outputs, optionally resamples, and packs explicit channels. A small PNG/TIFF
writer encodes unsigned 8/16-bit values directly: Blender's float render-save path unpremultiplies
alpha and can destroy RGB when a packed data alpha is zero. Direct encoding preserves independent
RGBA values. BaseColor alone receives a linear-to-sRGB transform. Files are staged first and created
exclusively; partial new files are removed if final writing fails. Export never changes scene color settings.


## Editor selector and docked area

`OP_EditorType` registers a NodeTree subtype with the label Open Painter. Blender includes registered
node subtypes in its native Editor Type enum. A scoped adapter detects selection and mounts the
native View3D in the same area. This is a Python subtype entry backed by existing native editors,
not a compiled SpaceType. It creates no workspace and never maximizes or rearranges the screen.
Screen custom properties store active area indices/backends across saves; runtime area pointers
are rebuilt on load. The header retains Blender's editor selector and adds Open Painter and 3D/2D controls.

The active interface uses native Blender Panel, UIList, UILayout and AssetShelf widgets. Texture
sets, layers and properties occupy the right sidebar; assets use the bottom shelf. Native T/N
shortcuts control the toolbar/sidebar. The modeling toolbar starts hidden. Legacy GPU dock drawing
and modal input routing remain historical code paths and do not render the active interface.

Layer rows reorder by mouse without custom drawing or event interception. Blender applies a list
row on mouse press, which updates `active_layer`; that property's update callback, when the row
under the mouse belongs to the Painter layer list (`context.ui_list`), invokes a modal operator
from the same press. The operator passes events through until the mouse leaves the drag threshold,
then maps vertical motion to a gap between the other visible rows and horizontal motion to a
nesting depth among the candidates `layer_stack.drop_targets` lists for that gap, repositioning the
layer live. Release finishes as one undo step; Esc restores the saved arrangement. The row widgets
are non-interactive text and icons so the press reaches the list row itself; the blend menu,
opacity field and mask button keep their own behaviour.

Native widget styling follows Blender preferences. Region defaults are applied on entry, rather
than continuously overriding the artist's visibility or shading choices. Selecting another editor
type detaches the adapter; registration does not create workspaces or rearrange other areas.

## Open Graph (0.4.0)

`graph/` holds a second NodeTree subtype, `OpenPainterGraph`, that is a *projection* of a texture
set rather than a document. `mirror.project(ts)` derives the expected nodes (one per layer, a frame
per folder containing the folder node and its children, one output node), sockets (anchor
producers on the right: *Output*, folder *Composite*, mask and channel anchors, read-only legacy
whole-layer outputs while used; consumers on the left: enabled fill channels, Published Input
masks, generator micro-detail slots; one *stack* input per root layer on the output node)
and links (every consumer field, nested siblings through Backdrop, every root layer's output
into its own output socket; each stack socket is a bundle spanning every channel, recorded
under the output spec's background ``maps`` key. Root order reads from Y rows, so roots
never link to each other).
`mirror.reconcile` edits the tree in place to match and stores a
structural signature; the UI lifecycle tick reconciles open graphs whose signature changed and
mirrors the active selection both ways. `NodeTree.update()` diffs the tree's links against the
projection and translates the difference into anchor fields through `graph/edits.py`, validated
by `references.issues`; refused links leave the stack untouched and vanish on the next sync.
Node identity is the `op_id` property, never a cached RNA reference; the tree is a fake-user
datablock owned by the set id, so undo restores stack and mirror together. The compiled shader
graph stays derived from the stack and is never the editing surface. Image Texture nodes
(`OPImageNode`, `image:` ids) are the one tree-only exception the mirror preserves: linking
one into a fill channel copies its image and mapping onto that channel and records the feed
in `op_image_feeds` for the redraw; cutting the wire takes the image back off, deleting the
node keeps it as a panel value. Fill nodes carry a channel switch grid with per-channel
image pickers and one shared mapping block. Edits to that block apply to every fill
channel; ordinary channel properties and image feeds retain independent mappings.

Composite anchors back the graph's *Output* and *Composite* sockets: `publish_output` /
`publish_group` on `OP_Layer` own the derived anchor ids `<id>:out` / `<id>:group`;
`evaluation/anchors.py` indexes them, `references.graph` models the composition chain with
`OUT`/`BELOW`/`GROUP` nodes so cycle rules follow from the item-granular check,
`evaluation/composite.Composer` (the shared CPU composition loop) publishes them, and the shader
imports the producer CHANNEL unit's `Out` or the folder unit's `Group` export.

## Layer performance (0.9.0)

Preview-side state on `OP_Layer` — `bypass` and `cache_mode` with `OP_LayerCache` rows — is
structural for the shader and invisible to export: its callback bumps the authoring serial
without dirtying outputs, `values()` never folds it into an identity, and portable materials
serialize it as live. `evaluation/purpose.preview()` marks CPU work done for the preview
(resource steps, cache jobs); only there does `Composer` skip bypassed layers.

A bypassed layer, and everything under a bypassed folder, compiles as a pass-through CHANNEL
unit with no imports, so its SRC/MASK units and their boundaries leave the plan. A cached layer's
CHANNEL unit samples one managed image per channel (`op_cache` descriptor) through an image
binding on the cache row plus a `CacheBinding` whose content identity — `_channel_identity` and
mask dependencies for a layer, the descendants' stamps for an isolated folder — is built with
name-based datablock keys and compared with the row's persisted provenance. A mismatch marks the
binding stale; CACHED rows request a job from `evaluation/layer_cache.py` (debounced 0.3 s, 16-row
strips under the scheduler budget, atomic publish of a new packed image plus provenance, superseded
when the identity moves on), FROZEN rows wait for the explicit operator. Passthrough folders are
refused because their composite depends on the backdrop. A publication notifies the row, which
swaps the sampler through the ordinary image-binding update.

`cache.painting_active(scene)` is the single freeze predicate (brush operator modal, or released
less than 120 ms ago, in a project with *Freeze While Painting*); `cache.stroke_finished(scene)`
is the single resume point. While active, `shader_preview.paint_touch` runs `Graph.update` with
`resources=False` — image bindings only — and records the owner in a deferred table;
`shader_preview.tick` neither repairs nor steps resources; `field_jobs` and `layer_cache` consult
the same predicate. Resume runs one owner-scoped update per deferred owner. `evaluation/profile.py`
keeps the per-layer table: unit build times and node/sampler counts from every build, boundary
seconds attributed by the owning layer id carried on each resource entry, and the explicit
profile pass (`Composer` with a `LayerProfiler`). Details and limits: [layer-performance.md](docs/layer-performance.md).

## Paint-session ownership

The scene stores the active target's set/layer/channel/mask identity separately from requested UI
selection. A target switch flushes the old image before rebinding, and the lifecycle handles native
panel selection changes. Non-paintable
selection ends the session. Export and save explicitly flush pending raster edits.

The session stores the paint object, original face selection and face-mask toggle in persistent
RNA. While painting in 3D, only assigned material-slot faces are selected. Finishing restores
those values; save/reload retains the restoration data. Topology changes during a session are
not supported: restoration skips a saved face list if its length no longer matches the mesh.

Imported alpha images are packed Non-Color images bound to a brush mask texture. Blender's linked
essential brush assets reject local texture references, so Open Painter activates a reusable local
copy through the supported LOCAL brush-asset API. Those copies and alpha images remain in the
blend and do not modify the bundled brush library.


## Workspace repairs (0.1.3)

Groups are displayed by a hierarchy traversal, not by flat collection order. Collapse state lives
on the layer; transient multi-selection and drag state live on the area. Reparenting validates the
entire destination chain before changing any selected roots. Group creation preserves the selected
siblings' stack location. Delete removes a subtree; ungroup promotes children.

New texture sets synchronously initialize eight linear outputs and a connected material before
returning. Shader repair only changes links, locations and image pointers that differ, preventing
unnecessary recompilation on each paint update.

The current interface uses native T/N region visibility. Asset thumbnails are rendered from the
same texture compositions used by the editor; `scripts/render_catalog_previews.py` bakes a recessed
preview mesh and renders all 50 authored material recipes. See `docs/material-catalog.md`.

Paint input and Blender's native modal-operator list identify stroke activity, including release
events consumed by the brush operator. During a stroke, snapshot composition runs cooperatively;
a final canvas check after release catches the last pixels. Idle Painter canvases are not repeatedly
scanned. Painting through the legacy N-panel retains periodic detection. Saves/exports force a final
canvas check. Blender image transfers and triplanar geometry generation are still whole-buffer work;
these limits apply to explicit raster evaluation and isolated compatibility inputs.

## Texture procedures and filters (0.2.0)

Procedural fills and masks evaluate through `texture_engine/procedurals.py`; filters operate on
texture arrays through `texture_engine/filters.py`. Per-channel effects and mask filter records are
persistent RNA data. Spatial filters request their required texture neighborhood; unchanged
procedural and mask rasters can be reused through the bounded raster cache. These exact CPU implementations remain the export/reference path. Native authoring
compiles pointwise filters and analytic/image triplanar sampling into shader nodes;
nonlocal filters and legacy hashed sources remain explicit compatibility boundaries.
