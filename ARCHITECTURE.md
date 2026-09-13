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

Interactive evaluation uses `evaluation/live_preview.py` to capture dependency
signatures, resolve cached texture inputs and compose changed channels on the GPU.
`live_inputs.py` selects validated GPU scalar/mask kernels or exact CPU inputs;
`live_procedurals.py` and `live_masks.py` write disposable float32 textures on Metal,
using CPU coordinate tables and explicitly sampled baked maps. Unsupported sources,
transforms and filters keep the CPU path. `live_gpu.py` implements
the existing blend math in float32; `live_display.py` publishes disposable image
views with GPU mipmaps to the fixed material and 2D editor. Unchanged dependencies
do no source work. A 256 MiB leaf cache is separate from the 256 MiB completed-folder
cache. The latter only caches the first root folder: passthrough children use a
known default backdrop; isolated children retain transparent coverage. Child and
group-mode signatures invalidate it, while its own opacity/mask can reuse it. Identity fills and unit passthrough groups avoid redundant passes.
The `LIVE` scheduler state leaves authoritative outputs dirty;
save, render and export complete them through the CPU evaluator described below.
Headless or incompatible work falls back to CPU. See the
[live backend design and limits](docs/live-procedural-preview.md).

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

Mask stacks resolve painted data, baked maps or generators, followed by levels, invert and
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

Each texture set gets a managed material with a fixed-size graph: eight output textures, Principled,
Normal Map, Bump, AO multiply, UV Tangent and Material Output. Emission and Opacity connect to the
corresponding surface inputs; scalar anisotropy and clearcoat settings control display response. Nodes carry ownership plus stable role tags.
Repair adds/reconnects missing managed nodes and retains unrelated nodes. Assigning a mesh preserves
its prior material per slot. Unassignment restores it only if the slot still references this texture
set's material. Slots are retained to avoid silently reindexing other assignments.

## Baking

Explicit high/low lists are validated before Cycles work. Temporary per-slot materials own active
bake image nodes. Unassigned slots use a scratch target. Normal and AO are Cycles CPU bakes; curvature
uses convex creases from the evaluated target mesh. Position, world/object normals, material/object
IDs, UV islands/borders and raycast thickness use evaluated-mesh utility baking. A `finally` block restores material slots, render engine,
Cycles device/samples, bake properties, visibility, selection, active object and mode. Temporary
materials and scratch images are removed. Successfully completed maps are registered independently.
Baked map use in shading is explicit via a generated fill layer.

## Caching and painting

Dirty flags live on texture sets, layers and channels/output records. Channel property edits only
invalidate that channel in its owning texture set. Mask, folder and layer edits invalidate the
owning set. Output revisions make scoped recomposition testable. An edit serial cancels stale work;
CPU preview uses an 80 ms property-edit debounce. Live GPU and active brush work
bypass that debounce. Geometry/source caches use their own dependency signatures,
so material-only edits reuse unchanged geometry across evaluations.
Saving packs images; load and undo/redo invalidate outputs. No critical state depends on module globals.

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

Native widget styling follows Blender preferences. Region defaults are applied on entry, rather
than continuously overriding the artist's visibility or shading choices. Selecting another editor
type detaches the adapter; registration does not create workspaces or rearrange other areas.

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
this is a CPU compositor, not a GPU tile engine, and large projects can still exceed the work budget.

## Texture procedures and filters (0.2.0)

Procedural fills and masks evaluate through `texture_engine/procedurals.py`; filters operate on
texture arrays through `texture_engine/filters.py`. Per-channel effects and mask filter records are
persistent RNA data. Spatial filters request their required texture neighborhood; unchanged
procedural and mask rasters can be reused through the bounded raster cache. These are CPU texture
operations, not a GPU compositor or shader-effect graph. Triplanar source projection currently
uses assigned base-mesh geometry; utility mesh-map baking separately uses evaluated geometry.
