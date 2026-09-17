# Changes

## 0.10.0

Material aging: a material-specific aging simulation replaces the Age Response presets.

- **What it is.** `Painter` sidebar → *Material Aging*: a substrate from a taxonomy of 31 materials
  (metals, woods, polymers, minerals; `aging/materials.py`) described by how it responds — light,
  moisture, salt and heat sensitivity, oxidation and corrosion rates, its oxide, hardness,
  porosity, brittleness — and the processes it subscribes to (`aging/model.py`: oxidation,
  atmospheric corrosion, patination, bleaching, greying, checking, water absorption, thermal
  cycling, deposits, biological growth, water staining, UV degradation, yellowing, chalking,
  embrittlement, abrasion, handling, impact); a construction on top of it — conversion coating,
  primer, paint with the artist's colour, top coat (`aging/coatings.py`) — and its starting
  condition.
- **How old, where, how used.** *Age* in years; *Environment* presets that are physical exposure
  parameters (UV, humidity, rain, temperature, cycles, salt, pollution, soil, wind) copied into
  sliders that turn the preset to *Custom* when moved; *Environment History* as ordered periods;
  *Use* (handling, abrasion, impact, cleaning) separate from age; *Maintenance* sliders and dated
  events (repaint, clean, rust removal, polish, treatments) that rewind what they act on.
- **The simulation** integrates the history monthly: rates are products of exposure, material
  response and the current state (corrosion needs exposed substrate and moisture and salts;
  cracking needs a brittle film and cycling; rust under paint lifts it; salts attack only through
  moisture), growth saturates, maintenance decays, the seed varies the particulars. *Predicted
  State* reads the outcomes (fade, gloss loss, coating loss, exposed substrate, rust, patina,
  greying…) with a label from Pristine to Derelict. Pure Python; memoised for the panel.
- **Nondestructive.** *Age Material* builds the isolated *Material Aging* folder from the state —
  one fill per outcome in causal order, placed by the mesh through Chips, Edge Wear, Rust, Cavity
  Dirt, Dust, Drips and Position (needs AO and Curvature; Position and World Normal are optional
  and the panel offers the bake and a Rebuild once they exist; recipe masks that read mesh maps
  can now be `optional`) — and afterwards only
  *drives* it: any settings change re-simulates and rewrites the driven values by `aging_role`;
  a value the artist moved inside the folder is an override until *Reset Art*; the artist's own
  layers inside the folder survive; a changed roster rebuilds; *Live* off freezes the layers;
  *Randomize History* reseeds; *Remove* deletes the folder.
- **Art direction after the physics.** Corrosion, Fading, Damage and Dirt multipliers scale the
  outcome groups after the simulation; the readout shows `68 %  (physics 34 %)`.
- **Age is a parameter.** `ts.aging.age_years` keyframes on the timeline and
  `dependencies.refresh` re-drives the folder per frame; the same settings and seed age two sets
  identically; set duplicates, material documents (`aging` block with settings, history and the
  override record; `format.read` validates it) and undo carry it.
- **Removed.** Age Response (`effect_semantic` UI, the Age / Environment State dialog,
  `core/material_states.py`, `core/environment_profiles.py`, its suite). The layer field and
  `material_state_data` stay hidden for older files.
- Tests: `tests/unit/test_aging_model.py`, `tests/integration/material_aging.py`.
  Docs: [material-aging.md](docs/material-aging.md).

## 0.9.7

Bug fixes from a full-tree audit (all background, GUI and GPU suites pass), artist-session suites
on a real asset file (`docs`: TESTING.md), and a second, multi-agent audit that confirmed and fixed
223 further defects (327 candidates from 24 finder scopes over two rounds, per-file triage, two-lens
adversarial verification, one fixer per file group, each with regression checks). Highlights of
that second pass, by subsystem:

- **Native shader compiler (`evaluation/shader_graph.py`, 26 fixes).** Sessions no longer keep an
  RNA reference to the texture set (adding or removing a set shifted every later session onto the
  wrong set); adopted units read their own graph's anchor index, boundary images and export
  sockets instead of the previous build's; a failed rebuild no longer corrupts the still-attached
  graph; orphan unit trees are not deleted while live; layer caches persist provenance without
  session-local tokens (caches no longer rebuild on every reopen, FROZEN layers are not flagged
  STALE forever); channel identities include vector strokes and decal frames; a spatial filter
  inside a mask group rasters the group's inner result; composite anchors inside isolated folders,
  premultiplied float canvases after packing, image-less decal channels, decal normal frames and
  sRGB-tagged detail images now agree with the CPU reference; strokes and halo boundaries are
  rastered once per strip band instead of per 16 rows; cache bindings notice effects and masks
  added to a cached layer; disabled mask groups stay out of rebuilt mask prefixes.
- **Scheduler and sessions (`cache.py`, `shader_preview.py`, `layer_cache.py`,
  `shader_resources.py`, `field_jobs.py`).** `on_save` packs every image even when one pack fails
  (strokes were lost on reload); saving or rendering no longer cancels pending cache refreshes and
  live-field analyses for good; the timer no longer spins at 1 kHz on a failed boundary; the live
  region rebake targets the set that owns the correction; undo no longer forgets bake-correction
  records; live-edge generators are analysed while the timer runs; a failed boundary raster stays
  reported instead of silently exporting a grey placeholder; status messages from the cache
  engine and bakes survive the tick; images removed elsewhere no longer wedge a session; channel
  opacity edits no longer re-raster boundaries through the percent mirror.
- **CPU reference (`buffers.py`, `composite.py`, `mask_eval.py`, `channel_eval.py`,
  `image_snapshots.py`, `raster_cache.py`).** The scalar fast path crashed on any Surface-space
  effect (the default) on a scalar channel; mask signatures omitted `edge_size`, `space`,
  `direction` and the geometry stamp; float fills were read premultiplied but composited straight;
  16-bit PNG/TIFF fills were double-linearised; whole-image masks and strokes thrashed the cache
  at 4k and were recomputed per strip.
- **Dependencies and geometry (`dependencies.py`, `mesh_masks.py`, `custom_graph.py`,
  `physical.py`, `snapshots.py`, `seams.py`, `influencers.py`).** Geometry Nodes inputs, shape
  keys, referenced vertex groups and crease layers take part in geometry keys and bake records;
  a mesh-mask edit no longer skips edge rebakes and live-field requests; mesh-mask and custom-graph
  rasters carry a geometry stamp; the CPU reference evaluates the view layer the artist works in
  and refuses (with a clear message) an assigned object no layer evaluates instead of reading its
  base mesh; seam maps build in bounded memory; unresolvable physical densities are cached.
- **Baking (36 fixes).** Rebakes retire the superseded map instead of packing it forever; region
  rebakes map viewport face indices onto the render-evaluated copy and no longer copy margin
  texels over neighbouring islands; painted Projection Distance raises the ray distance with the
  cage; a zero-extrusion cage no longer collapses onto the surface; AO Exclusion applies without
  high-poly sources; Bake Back keeps 'Ignore Brush Excludes' and turns projection off again on
  Remove; the edge-map job binds the set it was started for, registers an undo step and releases
  its images when cancelled; Bake Check findings select base-mesh faces; UV Border and edge-map
  bakes allocate bounded grids; resolution changes keep painted corrections.
- **Painting.** Secondary canvases of a multi-set session are no longer re-read at 60 Hz; sessions
  begun over a correction session or a swapped material slot are refused; retargets inside Paint
  Across Sets keep the session; non-square decals keep their aspect; re-pointing a decal no longer
  spawns a duplicate layer; vector-stroke rasters are shared across strips and no longer refused
  on dense meshes; decal tangent frames are keyed by geometry and resolution; Material Stamp no
  longer walks triangles through RNA.
- **Smart materials and modules.** Module base snapshots serialise datablock pointers by name;
  mask groups, locally added layers and Material Director ids survive Update/Replace; Update no
  longer leaks datablocks or re-imports unchanged assets; folders containing a module instance
  save as modules; imported EXR assets keep their colour space; large materials import (assets
  are no longer 32-bit EXR); scene-field masks with a ground object save.
- **UI and operators.** Operator properties no longer leak between clicks (Paint buttons stopping
  the session after one Finish, Add Filter converting the active mask, Choose Procedural
  rewriting a mask, Clear Anchor turning every picker into a clear, Selected Objects export
  becoming the default, Update becoming Replace); the layer drag commits one structural edit
  instead of one per row crossed; the layer list no longer recomputes diagnostics per row;
  Rasterize Stroke keeps the stroke for other sets; New Key on a Vector Strokes mask is paintable;
  the Geometry Effects panel no longer raises on redraw; planar vector strokes use the right view
  direction; shelf brushes respect the mask selection; the Painter timer survives exceptions.
- **Geometry effects.** Prototypes and published snapshots are released; instance sizes follow
  the source object's scale; Regenerate and Displace read the helper's own set and the current
  modifier values; Replace Source Mesh no longer applies the modifier stack twice; weld estimates
  no longer refuse ordinary curves.
- **Export.** Atlas staging duplicates a multi-material prop once; bundles include stitch/weld
  helpers and pack unsaved paint before writing; the custom pack of constant channels renders.
- **AI companion.** A fifth image generation no longer tears the session down; the Codex
  launcher works from a Finder-launched Blender; the director tick no longer redraws every
  viewport at 20 Hz while idle; switching sets mid-instruction keeps tool calls working.
- **Texture engine.** Large blurs use a separable/FFT path instead of a direct O(taps)
  convolution; the procedural lattice cache is released after evaluation.
- **Reconciliation of the audit's own changes** (a critic and two reviewers read the combined
  diff): the frame only enters geometry keys and boundary identities for time-dependent assets
  (animated objects, deforming or referenced-object modifiers), so scrubbing the timeline no
  longer re-rasters a static prop; `geometry_key` is memoised per update epoch; per-vertex weight
  and attribute digests are bounded (an epoch token above 250k vertices, salted per load/undo);
  every bake, doctor, diagnostics and region-rebake path evaluates the same view layer as the CPU
  reference, and an assigned object no view layer evaluates is reported by validation; superseded
  maps, canvases, module images, retired layer caches and offline rasters drop their fake user
  instead of being removed (undo keeps their pixels; the next save purges them), with retention
  bounded per set/kind; adding or removing a texture set discards only CPU evaluator jobs, not
  queued cache refreshes and analyses; the layer drag modal has a `cancel()` and session holds
  time out; sets without high-poly sources bake from themselves without a coincident copy;
  NORMAL_COMBINE detail images are read raw per evaluator without retagging the artist's image;
  degree-valued Normal Direction angles from older files migrate to radians once, under a
  project format version; vector-stroke coverage tests reuse the shared raster cache; failed
  boundary rasters are inherited by rebuilt graphs and retried only when their identity changes;
  scene-scope influencer tokens are cheap per member; the scheduler timer survives exceptions in a
  tick (reported on the status line, backing off on persistent faults); Normal Direction masks key
  on rotation only; edit-mode meshes are flushed by operator-driven bakes only; face-selection
  masks that cannot map through a topology-changing modifier are reported as warnings; portable
  materials carry the vector strokes their layers render and module updates keep instance-local
  stroke keys; switching a layer back to Live retires its cache images.
- The asset shelf's built-in entries (materials, smart materials and masks, filters, procedurals,
  brushes, No Alpha) no longer exist as image datablocks in the artist's file: they are written once
  per version to a per-user asset library ("Open Painter", `painter_assets.blend`, built by a
  background Blender at registration) that the shelf reads alongside the file's own texture and
  alpha handles. The Image Editor's browse menu, the outliner and saved files stop listing hundreds
  of `F Polished Chrome`-style previews; handles an older version wrote into a file are removed on
  the next shelf refresh.
- The Painter header has its close button back (an X next to the 3D/2D toggles) that returns the
  area to a plain 3D Viewport; the native header rewrite had dropped it.
- Switching an area to Open Painter no longer turns the viewport overlays off; the editor only
  asks for material shading and leaves the overlay toggle as the artist set it.
- Eleven findings in `src/open_painter/graph/` (Open Graph mirror) are reported to its owner and
  left unfixed here: anchor references cleared by node deletion/reroute, filter nodes addressed by
  index, mask reorders ignoring groups and the active mask, node trees never released, per-node
  full-stack redraws, repeated viewport splits, and set activation on node clicks.

- **Opening a .blend file silently stopped scene tracking.** The depsgraph handler was not marked
  persistent (the decorator sat on a helper), so after the artist opened any file, mesh masks,
  decal drags, live scene fields, triplanar re-projection and baked-map staleness on transforms no
  longer followed the scene until the add-on was re-enabled. Found by the artist-file suite.
- Moving or rotating a prop no longer marks its Normal (baked from itself), Curvature, Thickness,
  ID, UV and bevel maps stale: bake records keep only the world scale for maps that read the
  surface alone, and the full placement for AO (assembly occluders), World Normal, Position and
  Normal projected from placed high-poly sources. Records written before this stay current.
- Locking a set's sizes to metres re-identified every shader boundary and layer cache although the
  look is unchanged; the metre fields no longer take part in identities.
- The Repair Material button now puts the set's material back on assigned slots another material
  was dropped on (the message told the artist to repair, but repair left the slot alone and
  painting stayed refused). Render and save handlers still leave a swapped slot alone.
- Exporting below the authored resolution point-sampled every other texel; it box-filters now
  (normals renormalised), so fine detail no longer aliases in 1k exports of 2k sets.
- Changing a set's resolution while the shader session was live, then editing a layer with a
  CPU boundary (a blur, a generator's noise, a triplanar image), failed the session with
  "could not broadcast input array" — units moved between graph builds kept rastering at the
  previous resolution. Units are recompiled across a resolution change.
- Bypassing a folder whose child composite is published (a GROUP anchor another layer reads)
  failed the shader session ('key "Group" not found'): the pass-through unit now exports the
  Group sockets (transparent), so consumers keep their wiring.
- Deleting a decal layer (or its set) left its Empty in the scene; the Empty the layer created
  goes with it unless another layer still projects from it (a hand-picked Empty stays).
- A cached folder cached all eight channels, including those none of its children touch; those
  pass-throughs stay live, halving the cache memory and file growth of a typical folder
  (4 × 64 MiB at 2048 instead of 8 ×).
- Renaming an assigned object, a mesh mask's collection/object/material or a decal Empty left the
  name mirrors stale, so portable materials saved afterwards carried the old names and "Missing
  object" messages named a datablock that no longer existed; the mirrors follow renames.
- Stroke and bake-correction dilation no longer wraps around the canvas: a stroke touching the top
  edge used to bleed onto the bottom row (and left onto right) through `np.roll`; a non-wrapping
  `filters.shift` replaces it.
- The bake-correction brush polled the full correction canvas every 0.4 s for as long as a
  correction session stayed open (a 4k session read 64 MiB per tick while idle); polling now stops
  2 s after the last stroke.
- Bake Check's pre-bake tier no longer stalls on dense sources: exact UV-overlap, normal-consistency
  and duplicate-vertex analysis are bounded to 250k faces there (the full report keeps them), the
  seam check is vectorised, and normals are compared with `foreach_get` instead of per-face loops.
- Seam maps for surface filters use `int32` rasters and keep at most three cached maps, halving
  their memory at 4k.
- Dragging a decal Empty issues a numeric graph update instead of a structural repair per frame, so
  the viewport follows the transform without rebuilding the shader.
- The test runner read `material_director_ui.py`'s report with the wrong key and reported a false
  GUI-phase failure.
- Loading a portable material with a mesh mask lost the selector's collection/object/material name
  (the other selector fields' callbacks re-mirrored the still-empty pointers), so the mask came back
  empty; only the edited pointer mirrors its name now, and clearing a pointer clears its name.
- Duplicating a layer, duplicating a texture set or loading a material did not remap mask group
  membership: the copied members pointed at the original group's id and fell out of their group.
- Duplicating a texture set shared the source's layer cache images and bake-correction canvases
  (clearing either set orphaned the other) and did not remap layer output/folder anchors.
- Removing a texture set left its cache images and correction canvases packed in the file.
- Picking another decal Empty did not update the re-link name, so a deleted Empty fell back to the
  one chosen before; the name now follows the pointer.
- Changing a decal Empty's display size or image offset (compiled constants) only sent a numeric
  update; it restructures again while drags stay numeric.
- Painting vertex-group weights, attribute values or sculpt face sets did not re-evaluate mesh masks
  (the mesh signature covers positions, topology and UVs only); the mesh update epoch now takes part.
- Bake-correction snapshots, rasters and seam maps keyed by scene/image addresses survived a file
  load; they are cleared on load, unregister and when a correction session ends.
- The physical-scale density cache keyed on the full object matrix, so a drag or an animation
  re-measured the surface; it keys on the scale part only.
- An assigned, high-poly or cage object carrying a Decimate, Boolean or Data Transfer modifier
  broke bake provenance: their enum-flag settings are Python sets, which the provenance JSON could
  not serialise (the bake panel raised on every redraw and bakes could not record their inputs).
  They are recorded as sorted lists; geometry keys and custom-graph signatures do the same.

## 0.9.6

Decal objects.

- **Add Decal** creates an image Empty (`OP_Decal_<name>`) and a fill layer projecting from it:
  move, rotate, scale or Shift+D the Empty in the viewport and the decal follows, with projection
  depth, edge softness and front-face gating. Base Color, Roughness, Metallic, Normal, Height and
  Emission channels; decal normals rotate into the target's tangent frame.
  [Details](docs/decal-objects.md).
- The shader projects decals natively (Texture Coordinate bound to the object); the Open Graph
  layer node exposes the Empty's transform.

## 0.9.5

Physical scale awareness.

- Texture sets know their **density** (metres per UV tile) and texel size from the assigned meshes.
- Channels, masks and effects gain **Physical** units: procedural feature sizes and filter radii in
  metres, resolved to repeats and texels per asset and kept in sync through geometry, assignment,
  resolution and projection changes. [Details](docs/physical-scale.md).
- **Lock Sizes to Metres** converts an existing material without changing its look; imported
  materials re-normalize to the new asset automatically.

## 0.9.4

Seamless surface filters.

- Every neighbourhood effect (blur, sharpen, high pass, dilate/erode, edge detect, warps,
  directional and slope blur) gains a **Space** switch: *UV* or *Surface* (default). Surface
  space follows the mesh across UV seams through a per-set seam map and filters each island on
  its own cross-seam padding; directional effects take a world-space **Direction**.
  [Details](docs/surface-filters.md).
- The shader's isolated filter inputs take the same seam-aware path; mesh edits invalidate
  surface effects.

## 0.9.3

Mask architecture.

- **Mask Groups**: a nested mask stack as a source — members combine first, then the group blends
  into the stack with its own operation, strength, levels and invert. Groups nest; list buttons
  move entries into and out of groups and move groups as blocks. Both evaluators walk the same
  tree. [Details](docs/mask-architecture.md).
- **Custom Graph** mask source: your own shader node group, native in the viewport and baked with
  Cycles for export.
- Mask list shows nesting, source icons and each entry's blend operation.

## 0.9.2

Mesh masks.

- **Mesh Mask** source: object, collection (children included), material (including the slot's
  original material), vertex group, face set, face selection, UV island, normal direction or a
  named geometry attribute of the assigned meshes, rasterized exactly through the render UVs.
  Collection membership, slot materials, weights and attributes are tracked through the
  depsgraph, so adding a screw to the collection updates the material. [Details](docs/mesh-masks.md).
- Open Graph mask nodes show the mesh selector; portable materials re-link selectors by name.

## 0.9.1

Bake Doctor.

- **Bake Check** before every bake: UVs, normals, applied scale, low/high naming (including
  near-miss suffixes), duplicate vertices, render-modifier mismatches, cage intersections and UV
  island seams; a full check adds sampled cage-ray coverage. Click a finding to select the faces,
  vertices, edges or objects it reports. [Details](docs/bake-doctor.md).
- **Bake Correction Brush**: paint Projection Distance, Skew Correction, Projection Direction,
  Ignore High Poly and AO Exclusion per region with the native brush. Painted distances and
  directions become an owned cage per part; exclusions are composited second passes. A wire overlay
  shows the corrected cage and the painted region is rebaked into the existing maps when a stroke
  ends.

## 0.9.0

Layer performance.

- **Profile Layers**: per-layer compile time, node and sampler counts, attributed CPU input work
  and a bounded CPU composite pass; cost badges in the layer rows and a Performance section in the
  layer properties. [Details](docs/layer-performance.md).
- **Bypass in Preview**: a preview-only per-layer (and per-folder) switch that removes the layer's
  units and CPU work from the shader; export, bakes and snapshots still include it.
- **Layer caches**: Cached (auto-refresh once edits settle) and Frozen (explicit Refresh) modes
  sample a persisted managed image per channel instead of compiling a layer or an isolated folder.
- **Freeze While Painting** (on by default): a brush stroke no longer restarts full-resolution
  boundary regenerations every 33 ms; the material catches up once after release. Scene-field
  analysis and cache refreshes wait for the stroke too.
- Stroke-time paint inputs skip the structural signature walk; a frozen set never spins the
  scheduler timer.

## 0.8.0

Material Director.

- Native **Director** sidebar with Create, Refine, Plan and Explain; model and isolated-part views up to 768 px, mesh-map previews with provenance, and appearance-reference input.
- **Scene Materials** scope coordinates up to 16 existing material regions with per-part visual inspection, one validated commit and persisted grouped undo.
- Native Codex image generation for editable surface textures and masks, enabled by default.
  Verified generated artifacts import as packed Blender images with channel-appropriate color spaces;
  Plan, protected content, transactional rollback and AI Undo also cover image assignments.
- Codex app-server companion with ChatGPT sign-in, account/model discovery, allowance display,
  cancellation, OS keyring or session-only credentials, and isolated application state.
- Validated authoring batches create editable fill/folder layers, channel controls, masks, scene
  fields, filters, shared anchors, and bundled material/effect/module recipes.
- Scoped edits, protected paint and explicit layer/channel/mask locks, stale-input detection,
  bounded persisted change receipts, rollback, idempotency, and conflict-aware AI undo.
- One Blender undo step per committed batch. Numeric edits retain the native shader path without
  rasterizing texture outputs. Load, undo, disconnect and cancellation invalidate pending drafts.
- Real ChatGPT-account use cases tested through Codex `0.154.0-alpha.6.2`: creation, refinement,
  spatial follow-ups, viewport critique, and Plan/Apply/Undo. First-pass realism remains weak;
  see the [live acceptance report](docs/material-director-live-validation.md).
- Live testing fixes: recent conversation for follow-ups, accurate mask/default-channel guidance,
  a conservative proposal texture budget, and scene-analysis readiness checks.
- Fixed scalar scene-attribute interpolation, distinct unpublished field resources, and live field
  parameter changes retaining stale analysis.
- Robustness fixes: a material edited during a Create/Refine turn reports a stale draft instead of
  disconnecting Codex; the companion queues its output so a stalled Blender main thread (bake,
  shader compile) no longer ends the session; a late interrupt error cannot cancel the next
  instruction; 4K+ mesh-map and reference previews read pixels once instead of seconds per map;
  change reports name removed masks and filters; `set_filter` on a channel requires its
  `effect_id`; linked-object bindings resolve by full name.
- AI Undo keeps the artist's current layer and mask order (a layer added or moved after the AI
  change no longer jumps in the stack); an extra generated image is ignored rather than fatal; small
  3D-view regions are no longer upsampled for capture.
- Asset-shelf handles (one image per library material, mask, filter, procedural, brush and
  alpha) are no longer written into saved .blend files; they are rebuilt on load as before.
- Open Graph is now a node-based editor of the stack: masks and channel filters are nodes with
  their controls, chained into each layer; composition links show every layer's output feeding the
  layer above, children feeding their folder and the top layer feeding the Texture Set Output.
  Shift+A adds masks (generators, procedurals, scene fields, filters, anchors, published inputs)
  and channel filters to the active layer; dragging chain or backdrop links reorders masks,
  filters and layers; deleting a mask/filter node deletes it. See [Open Graph](docs/open-graph.md).
- Graph edits are diffed against what the tree last showed, so a link edit made before the graph
  caught up with a panel change no longer clears a fresh reference; new mask/filter nodes are placed
  next to their (possibly moved) layer node; duplicated or pasted nodes and deleted frames are
  reconciled; filter nodes are labelled by kind.
- See [Material Director](docs/material-director.md) for setup and supported scope.

## 0.7.0

Material → Geometry.

- Displace from Height (or a mask): an owned copy of the evaluated source, subdivided and
  displaced by Geometry Nodes from the published channel through the set's UVs, with
  regenerate, apply and an explicit Replace Source Mesh.
- Extract Curves: a mask's centre lines become an editable world-space curve on the surface
  (thinning, tracing, simplification, seam-aware projection); Stitches on Curve instances
  thread prototypes along it; Seam Bead on Curve is a low weld profile.
- New scatter prototypes with owned materials: Bolts, Scales, Rocks, Bark.
- Bake Back to Normal / AO: every generated helper of the set is staged as a paired high-poly
  source (instances realised), the existing Cycles bake runs, the maps read Ready until a
  helper regenerates, and the geometry is kept, hidden or removed.
- See docs/geometry-effects.md.

## 0.6.1

- Native painting across texture sets: Paint Across Sets runs the brush in Blender's
  material paint mode with a canvas per set (linked paint layers created where missing);
  one stroke down a seam writes every set it crosses, and all of them re-evaluate.

## 0.6.0

Universal canvas and resolution-independent strokes.

- Vector Brush: strokes are recorded as data (samples with pressure, radius, tilt and
  direction, brush settings, projection, channel values) on the project and rasterised
  per texture set at its own resolution. One gesture across several sets stays one
  editable stroke: linked paint layers are created where the stroke reaches. Strokes
  cross UV seams, follow mesh edits, re-render on resolution change, undo as data and
  survive reload; edit, reorder, duplicate, erase, rasterise or purge them from the layer.
- Vector Strokes mask source; native paint keeps working under the strokes.
- CPU and shader-native parity; stroke edits re-identify the boundary without a rebuild.
- Output Resolution changes now report paint images at another size with an explicit
  Resample Paint Images action (everything else already re-renders).
- See docs/vector-strokes.md.

## 0.5.1

Target renderer: pick the engine once.

- Project-level Target (Blender, Unreal Engine 5, Unity HDRP, Unity URP, Godot 4, Custom)
  configures every following set's export preset, format and bit depth, file naming
  (`T_{set}_BC` for Unreal, `albedo/orm/normal` for Godot, …), the resolution cap and the
  paired mesh export (FBX with Unreal's axes, glTF for Godot).
- Engine Preview: the display material shows what the engine computes from the exported
  files — 8-bit quantisation through sRGB, DirectX green round trip, BC5 Z reconstruction,
  smoothness round trip, AO on indirect light only, Standard view — as role-tagged adapter
  nodes; no graph rebuild, verified against numpy to 2/255.
- Export → <Target>: one click writes the textures, a per-set manifest of conventions and
  colour spaces, and the mesh; the set's own settings are restored afterwards.
- See docs/target-renderer.md.

## 0.5.0

Geometry-aware materials: the scene is a live input.

- Scene fields analyse cooperatively: a LIVE field whose inputs changed is requested
  and computed in strips by the scheduler after a 150 ms debounce, published atomically
  and only then invalidates the set; evaluation never blocks on an analysis and the
  status line reports progress. Object queries run through Geometry Nodes in a
  disposable scene (all texels in one multithreaded pass, per-object radii), with the
  per-point BVH path as the verified fallback. Analysis resolution up to 1024.
- New kinds: Convexity, Concavity and Edge Exposure measured live on the evaluated
  mesh (bevel an edge, the wear follows), Distance to Ground, World Height and
  Proximity with per-object radius (`open_painter_field_radius`). Influencers can be
  every other visible mesh in the scene (with an exclusion collection and a cap), so a
  newly added bolt re-triggers the field. Falloff curves apply at sampling time.
- Edge Wear / Chips: *Edges From → Live Geometry* takes the edges from a live Edge
  Exposure field on the mask (CPU and shader-native parity ≤ 1/255), no curvature bake.
- Mask combine modes Lighten (Max) and Darken (Min); scene-field presets (crevice dirt
  near other parts, radial rust from bolts, ground mud, live edge exposure).
- Bake settings: opt-in Auto-rebake Edge Maps after a target mesh's geometry changes.
- See docs/scene-fields.md.

## 0.4.2

Material modules: reusable, versioned folder subtrees that update in place.

- Save Folder as Module writes a versioned `.openpaintmaterial` (format 4, a `module`
  block naming the root) with the subtree in the module's own id space; Insert Module
  places instances under a fresh namespace so several instances coexist and references
  from outside into an instance survive updates. Update / Update All apply a newer
  version as a three-way merge: the definition's changes land, your edits stay, both-
  changed values keep yours and are listed; painted images are always the instance's;
  layers you added inside stay. Detach opts out; Replace discards local edits.
- Catalog materials insert as module instances of their recipe (deterministic ids, same
  pixels) so a recipe version bump updates existing materials the same way. Built-in
  catalog modules (first: Damage) can be inserted directly or nested in recipes with
  per-recipe overrides that survive updates of the shared module.
- Layer rows badge module instances with their version; folder properties carry the
  module box. See docs/material-modules.md.

## 0.4.0

Open Graph release: the first step of the live-graph programme.

- Open Graph: the Layers tool strip's node icon splits the Painter area into a node view of
  the active texture set. Nodes mirror layers (folders as frames), sockets mirror anchor
  producers and consumers, and dragging a link sets the corresponding anchor reference —
  from a layer's Output, a folder's Composite or any anchor point into a fill channel's
  Content, a Published Input mask or a generator's Micro Details. Cycles and ineligible
  pairings are refused without touching the stack; deleting a link clears the reference.
  Clicking a node selects the layer and the list selection selects the node; Auto Layout
  re-places nodes by stack order and dependency rank. See docs/open-graph.md.
- Published outputs: Layer Output publishes a layer's blended result (in its group's
  context) and Folder Composite publishes a folder's child composite before the folder
  blends, as anchors every existing consumer can pick, choosing the channel it reads.
  A hidden or disabled producer publishes its backdrop. CPU and shader agree to float
  precision; toggling a publish flag rebuilds only the affected units and undo rehydrates.
- Legacy whole-folder channel references now say to publish the folder composite instead.
- Test fixtures: phase05 expects the four-reach edge map set.

- Layers: drag a row up or down to move the layer (a folder moves with its contents); moving
  sideways while dragging changes how deeply it nests, Esc cancels and the drop is one undo step.
  The row's press-to-select now also selects the layer content, as the name button did, and
  double-clicking a name renames it in place.
- Layers: rows follow the Substance layout in Blender widgets: visibility, type icon, mask
  button and name on the left; a compact blend menu and a whole-percent opacity number on the
  right (`opacity_percent` is now an integer). Short blend names appear in narrow sidebars. The
  channel selector and add/delete tools form one strip above the list, the list grows with the
  stack and the reorder/duplicate/parent buttons are icon-only with per-action tooltips.

## 0.3.0

Performance release. Numerical behaviour is unchanged: every optimisation is either
bit-identical to 0.2.9 (verified by output hashes and the frozen-reference suites) or
differs only in float rounding of an interpolation, noted per item below.

- Shader compiler: the native preview is compiled as one node group per unit (a layer
  channel, a channel source prefix or a mask prefix) instanced from a small top tree,
  instead of one flat tree. Blender re-validates a tree on every node/link insertion,
  so construction is now the sum of the squared unit sizes: Worn Painted Metal builds
  in 0.41 s (was 4.6 s), Post-Apocalyptic Scrap Metal in 0.70 s (was 16.1 s), the
  artist's 2K project in 0.64 s (was 5.1 s). Parity with the CPU reference is unchanged.
- Layer visibility and the enabled flag of pointwise effects are shader uniforms: hiding a
  layer writes one socket (2.8 ms on the 73-unit material, same graph, no shader
  recompile) where it used to rebuild the whole material. Hidden layers with content that
  cannot compile pass through as stubs with a warning until they are shown.
- Structural edits rebuild only the units whose signature changed and whose RNA owners
  kept their addresses; the rest are adopted: a blend-mode change rebuilds 4 of 73
  units in 80 ms, adding a mask rebuilds 1 in 127 ms.
- Undo, redo, reload and re-enabling the add-on rehydrate the shader session from the
  persisted node groups (bindings, image and boundary samplers, dependency closure) and
  correct any drifted uniform, instead of rebuilding and recompiling: an undo reaches a
  current shader in 30 ms with the same material instance.
- Numeric slider edits bind without re-deriving the graph structure: 0.41-0.73 ms median
  per traced control on the 2K project (1.45-1.61 ms in 0.2.7). A regression flips every
  layer, channel, effect, mask and folder field to check that structural fields still
  flag a rebuild.
- Scheduler timer: 10 Hz when idle (was 60 Hz for the life of any native project),
  re-armed immediately by structural edits, pruning only when the live set changes;
  status writes are change-only. Real-window idle cost: 58.8 -> 9.8 ticks/s, 7.0 -> 1.6 ms
  CPU per second.
- Panels: the layer list no longer walks the hierarchy per row (O(n^2) per redraw), bake
  provenance status is memoised against a depsgraph epoch instead of serialising mesh
  signatures per redraw, and asset handles are prepared once per file load.
- Mesh-map bakes and the triplanar geometry raster rasterize all UV triangles of a target
  in batches (exact same float32 barycentric sequence, later triangle wins), crease
  detection and UV islands pair shared edges with arrays, border falloff is stamped once
  per island edge, thickness rays get their frames in NumPy, and the mean-curvature
  kernel runs per grid cell with zero-kernel samples pruned. On the 2K project:
  POSITION 2.15 -> 0.79 s, WORLD_NORMAL 2.18 -> 0.80 s, MATERIAL_ID 1.85 -> 0.65 s,
  UV_ISLAND 2.99 -> 1.31 s, UV_BORDER 9.65 -> 1.78 s, SELECTION 1.72 -> 0.65 s,
  THICKNESS 2.97 -> 0.83 s, MEAN_CURVATURE 28.7 -> 11.5 s, CURVATURE 28.7 -> 11.0 s,
  triplanar geometry 2.36 -> 1.24 s. ID, island, border, selection and curvature maps are
  bit-identical; the interpolated maps differ only in float rounding.
- CPU reference/export: lattice hashing, fade/mix/gradient chains, cellular and scratch
  fields, texture gathers and filter sanitising reuse buffers; separable morphology builds
  its window by doubling (exact). Bit-identical outputs; paired cold worn/2K medians
  16.03 -> 14.50 s.
- Adding an edge-driven effect, smart mask or material to a project whose Curvature map
  is still crease-only (baked before 0.2.8) no longer freezes Blender for the rebake: the
  effect is added at once and the edge map rebakes as a cooperative modal job with
  progress in the status line (Esc stops it, the old map is kept); the effect updates
  when the map lands. The Rebake Edge Map buttons use the same job.
- The edge map is baked as a set of four reaches from one Curvature Radius: thin (x0.5),
  normal (x1), wide (x2) and extreme (x4), sharing the same crease lines. Edge Wear and
  Chips masks gain a live **Edge Size** control (0 thin, 1 normal, 2 wide, 3 extreme) that
  blends between the maps in the shader and on the CPU reference alike, and Edge Width then
  narrows the chosen band; neither needs a rebake. Projects with only the single map keep
  working (Edge Size is offered once the set is baked); adding an edge-driven effect to such
  a project queues the background rebake of the set.
- The layer view shows a generator mask's Amount, Edge Size, Edge Width (Edge Wear/Chips)
  and Contrast directly, with the rebake warning and button, instead of only behind the
  mask selection.
- Undo/redo with a shader boundary job or a CPU job in flight, full and linked scene copies
  and a second window are covered by regressions; rehydration refuses a top tree that a
  copied material still shares, so scene copies compile their own graph.
- Displayed-frame latency is measured end to end (`tests/performance/shader_native_frame.py`):
  slider 32 ms, hide 32 ms, show 99 ms, blend-mode change 211 ms to the settled frame, undo
  33 ms, on the 2K Worn material in a real window.
- Developer Mode shows units, node counts, last build/reuse figures and unique images
  against the fragment sampler limit. New benchmarks: shader_build_scaling,
  shader_structural_edits, idle_timer, bake_maps; the paired CPU benchmark selects the
  CPU backend again (native sets have allocated no outputs since 0.2.7).

## 0.2.9

- Anchor points: Add Anchor Point in a mask stack publishes the coverage before it; Anchor in a channel's filter stack publishes the channel after the filters above it. Published Input masks, fill channel content (with the channel's own detail and filters on top) and generator Micro Height / Micro Normal inputs reference them, on the CPU reference and shader-native evaluators alike.
- Micro details: Edge Wear, Chips, Dirt, Cavity Dirt, Rust and Drips take anchored height or tangent-normal relief, so painted rivets and scratches get worn rims and dirt rings with live intensity sliders and a texel radius.
- Reference rules: dependency cycles are checked per stack item (any acyclic reference is allowed, including inside one layer); a missing anchor or published source evaluates as neutral and is reported as a warning instead of aborting evaluation. Anchors list their consumers, consumers locate their anchor, and deleting an anchor or its layer reports the references it broke.
- Smart material format 3: anchor ids on masks, channels and filter items are remapped on import, group duplication and texture-set duplication; unknown ids import as missing anchors; cyclic files are refused.

## 0.2.8

- Edge Width on Edge Wear and Chips masks: a live per-layer control that narrows the wear band within the baked Curvature Radius without a rebake; the radius, crease settings and Rebake Edge Map sit beside it in the mask properties.
- Curvature map V2: crease lines plus a texel-domain curvature estimate (turning over area within Curvature Radius, relative to the surface median), so Edge Wear and Chips work on smooth, bevelled and scanned meshes. Old crease-only maps read as stale and are rebaked automatically when an edge-driven material, effect or smart mask is added.
- Geometry effects reworked: variant prototypes with per-instance rotation, lean, size and spacing, owned materials, CC0 Poly Haven `moss_01` scanned moss cards, and a sunk, rippled weld bead. Placeholder box/cone/octahedron shapes are gone.
- Damaged Bevel and Rounded Edges surface effects from Cycles-baked Bevel Mask and Bevel Normal maps; they render in EEVEE, Cycles and export alike.
- Bake diagnostics: Inspect Bake Setup (exact UV overlap across the set, sampled cage-ray coverage), Select Problem Faces, UDIM tile enumeration; Mean Curvature and Selected Faces utility maps with Reselect Baked Faces; explicit AO Distance; opt-in 2x supersampled bakes with a memory preflight.
- Research: UDIM tile-access gate rerun (per-tile files and native paint onto tile 1002 pass), node-stack add-on study with measured shader-compiler findings.

## 0.2.7

- Replace default live texture composition with a persistent internal Blender shader graph. Numeric layer, mask, generator and pointwise-filter edits update existing shader bindings; no complete channel texture is regenerated.
- Track parameter owners and downstream shader dependencies, including published source stages. Keep baked-output invalidation separate from authoring preview state.
- Isolate legacy hash fields, neighborhood filters and scene analysis as reusable input resources. Keep final mask levels and filter strength shader-native, publish changed inputs atomically and discard superseded jobs.
- Preserve editable image sources through save/reload without baking the native layer stack. Retain explicit CPU reference evaluation and texture export.
- Add Developer Mode dependency diagnostics, rendered numerical comparisons, boundary-isolation and real-window interaction regressions.

## 0.2.6

- Re-author all 50 smart materials using thirteen new AI-generated wear and relief inputs, including actual scratch, chipped-coating, weld, blister, rubber-crack and grease masks. Replace scattered procedural scratch ticks and striped paint fragments with image coverage and material-specific relief.
- Give coatings editable elevated height ranges, visible primer boundaries and recessed damage. Project isotropic damage through triplanar coordinates to avoid stretched UV-pole patterns; retain directional mapping for grain and weave.
- Include height relief in exported normals, accounting for UV charts, mesh scale, authored normals and DirectX/OpenGL conventions. Preserve height distance in saved materials and copied texture sets; keep the preview's single bump evaluation.
- Add tapered scratches, open branching fissures and sharply fractured paint as new texture sources. Preserve the numerical behavior of existing procedural identifiers and saved material instances.
- Compose editable procedural/image masks with shared seeds, levels and constant recessed height. Reuse packed library image masks after material-only edits.
- Fix aliased fingerprint shelf previews, refresh all material renders, and add per-material before/after review and regressions.

## 0.2.5

- Add fifteen generated scalar surface-detail images to all 50 existing smart materials, with separate editable color, roughness and height amounts. Preserve mesh-map wear and all 50 recipes.
- Share packed source images and cached scalar inputs across channels; material value edits reuse the live GPU texture instead of writing new source files.
- Keep detail inputs portable in version-2 smart material archives and packed Blender projects; retain version-1 compatibility. Add native source controls and missing-image validation.

## 0.2.4

- Fix Bake Selected Maps failing on native timer events in Blender 5.2. Gate work on the queue timer without reading unsupported Event properties.
- Ignore unrelated timer ticks and release queue resources on completion, cancellation and failed initialization. Retain completed maps if a later map fails.
- Add real-window bake queue regressions for actual map output, cancellation and failure cleanup.

## 0.2.3

- Generate supported procedural sources and mesh-aware masks directly into float32 GPU textures on Metal, with the CPU evaluator retained for other domains and final save/export.
- Reuse completed first-folder contents across opacity and mask edits with a bounded cache. Skip proven unchanged channel fills and redundant passthrough copies.
- Avoid unused cellular-noise outputs and generator computations without changing CPU pixels. Preserve all existing materials, native controls and portable material formats.
- Add native EEVEE frame benchmarks, GPU/CPU pixel regressions, cache lifecycle checks and documented research and limits.

## 0.2.2

- Place blend mode and channel opacity side by side in native layer rows. Use an editable numeric percentage field instead of a slider; click the value, type a number and press Enter.
- Verify actual mouse/keyboard entry of 37.5% and native 3D/2D controls.

## 0.2.1

- Restore all seven asset types as labeled native toolbar buttons in 3D and 2D, filtering the bottom shelf. Separate Materials from Smart Materials.
- Populate Filters, Brushes, Alphas and Textures with working assets; route filter/procedural/image clicks to the selected channel or mask. Add texture import and library refresh.
- Cache generated previews outside drawing; preserve imported source images and reuse their handles after renaming. Verify category isolation, actual activation and native UI lifecycle.

## 0.2.0

- Add 50 original smart materials with 229 editable layers, 16 stackable effects and 18 smart masks, with actual rendered previews.
- Add 65 reusable procedural sources and 52 texture filters in channel and mask stacks, with native searchable controls.
- Add evaluated-mesh Position, World/Object Normal, Material/Object ID, UV Island/Border and raycast Thickness maps.
- Add Emission/Opacity channels, nonseparable color blends and four normal blend modes; persist anisotropy/coat display settings in portable materials.
- Reuse procedural/mask textures across channels with bounded LRU memoization; retain full-resolution filter neighborhoods without row seams.
- Correct large-seed mask artifacts, low-amount rust coverage, nested mask filter serialization and UV tangent orientation; preserve geometric edge gating.
- Expose native brush tool, pressure, jitter, stabilization and alpha/stencil controls.
- See [v1 scope and validation](docs/texture-workflow-v1.md).


## 0.1.16

- Replace tangent-normal image differentiation with a geometric convex-crease texture bake of the evaluated target mesh. Coplanar triangulation, UV seams and sub-threshold tessellation do not become edge coverage.
- Separate Minimum Edge Angle and Edge Width from generator intensity. Add Rebake Edge Map directly to Edge Wear properties; it replaces only Curvature and leaves Normal/AO maps intact. Flag legacy generated curvature maps for rebaking.
- Regenerate library previews on a beveled cube using the actual geometric edge bake.
- Validate cube triangulation invariance, clean dense sphere at maximum intensity, concave-fold exclusion, packed images, direct rebake and existing texture evaluation.
- This is a target-mesh crease map, not high-to-low continuous curvature transfer. Fine detail present only in a high-poly normal bake is not captured. Existing curvature images require an explicit rebake.

## 0.1.15

- Fix edge wear producing grunge on zero-curvature pixels: noise now breaks up curvature coverage instead of adding new coverage across flat surfaces. Amount cannot introduce wear on zero-curvature pixels.
- Add Bake Mask Stack to Texture: evaluate the effect stack into a packed grayscale UV image, then replace it with an editable painted mask. Evaluation succeeds before the stack is replaced; the operator supports Undo.
- Regenerate smart-mask previews from actual evaluated texture pixels. Verify off-edge coverage is zero, baked/composited pixels match before and after conversion, saved masks reload intact, and shader node topology stays unchanged.
- The existing normal-image curvature approximation remains; this change does not implement geometric convex/concave curvature baking.

## 0.1.14

- Give Layers a native scrollable list with a stable name column, separate action toolbar, compact blend labels and percentage opacity.
- Compact Texture Set controls and show one selected material channel in Properties, with labeled Fill and UV Transform sections.
- Start with the modeling toolbar hidden outside painting; T still toggles the native toolbar. Preserve assets below the viewport and native Blender theme controls.

## 0.1.13

- Replace the active GPU overlay and modal input handler with Blender Panel/UILayout controls and native Asset Shelves in both 3D and 2D. Keep assets below the viewport and texture sets/layers/properties on the right.
- Use native T/N behavior, inline channel blending, explicit content/mask selection, and named mask targets. Remove the visible Close control.
- Add white/black mask actions and Normal mask-effect blending. Smart-mask clicks accumulate editable effects; Ctrl/Cmd-click replaces them after required-map validation. Existing saved effects retain their operation.
- Verify real shelf clicks, native panel drawing, T/N, 2D/3D switching, save/reopen, UV projection, materials and incremental compositing.

## 0.1.12

- Put per-channel blend selection and draggable opacity percentages directly in layer rows; remove the generic Channel Blending button from the main dock.
- Add Assets > Masks: six original editable edge/dirt smart-mask presets, baked-map preflight, and thumbnails rendered from their actual output on a baked preview mesh. Applying a mask selects mask properties without adding a material layer.
- Show actionable status messages across the footer instead of clipping them to the narrow tool rail.
- Verify distinct mask evaluation, missing-map atomicity, per-row channel targeting, GPU-drawn controls and T/N behavior.

## 0.1.11

- Separate projected fill masks from hand-painted UV masks. Painted Steel and Dirty Plastic now project coating coverage onto mesh positions through its render UVs rather than stamping a fixed mask across the atlas.
- Expose mask projection, filtering, wrap and transform controls. Existing painted masks retain their data and behavior; they can explicitly be switched to Fill in mask settings.
- Align projection rasterization with Blender’s render UV layer, even when another map is selected for editing. Regenerate previews with actual texture-set mesh assignments.
- Regression: identical surface positions in disjoint UV islands receive identical projected coverage; UV fills and painted masks retain atlas-based behavior.

## 0.1.10

- Insert paint/fill layers and empty folders above the selection. Duplicate adjacent to the original and keep the duplicated folder selected, with independent child IDs and paint images.
- Add a layer-stack channel selector independent of brush targeting. Row values show channel opacity; click them to edit channel opacity/blending. Folder creation no longer groups the selected layer implicitly.
- Validate against Adobe’s current layer-stack documentation and Blender integration tests.

## 0.1.9

- Continue from the restored 0.1.6 Blender add-on. Move Assets to the bottom shelf; add a narrow tool strip and contextual top brush controls, preserving live Blender theme colors/fonts and T/N panel toggles.
- Cache rectangle/tool GPU geometry. Reuse paint scan buffers and bound comparison memory; preserve regional and dirty-channel evaluation.
- Bundle checksummed CC0 Poly Haven maps for Rough Wood, Fine Wood and Weathered Paint. Improve Copper and Dirty Plastic and render all library thumbnails from actual outputs.
- Validate interactive shelf/brush controls and T/N, material assignment, folders, packing, save/reopen and regional/full composite equivalence. Local performance measurements and research sources are included in docs/research.

## 0.1.7

- Restore the existing Painter layout after withdrawing the sidebar-only candidate. Registration is idempotent and rolls back callbacks/classes on failure; remove visible Close controls. This still uses an editor-subtype adapter, not a newly registered native SpaceType.

- Open into paired native 3D/2D views with shared painting state and T/N toggles; combine views using 3D/2D. Preserve the surrounding workspace.
- Add denser adjustable Assets, Layers/Texture Set Settings tabs, per-row blend/opacity controls and fill projection/filtering/wrap/UV transformation controls.
- Add white/black/bitmap masks, linked projection settings and nondestructive ordered Levels/pointwise filters on content and masks, including effect copy/paste and portable persistence.
- Full Painter parity remains incomplete: lit 2D, anchors, paint/fill effects, advanced painting tools and Substance engine formats are still open.

## 0.1.6

- Empty paint layers/folders no longer invalidate texture outputs; new fills invalidate only their enabled channels. Avoid redundant channel enabling and defer paint-image packing until save.
- Remove redundant UV resampling for paint rasters at output resolution. Recompose only changed rows for eligible paint strokes; masks, structural edits and mismatched/color-converted rasters retain full evaluation.
- Preview jobs yield between 32-row tiles with a 6 ms work budget, cancel on edits, and update during native strokes. Whole-image reads/uploads and geometry preparation remain indivisible.
- Added exact pixel/cancellation tests and a real 2K cube held-stroke test. Measured moved-stroke preview latency about 122 ms and maximum evaluation callback 34 ms; this is not a claim of Substance Painter GPU performance or verified 4K/8K latency.

## 0.1.5

- Match dock buttons and search fields to Blender’s 20-unit widget height; use tool/toggle widget styles, selected outlines and text, and custom theme shading.
- Use native panel background, outline, roundness, heading and text colors. Resolve panel transparency against the UI background, and preserve search-field and viewport text contrast in Light themes.
- Added real-window Dark/Light/custom-theme visual checks.

## 0.1.4

- Hide native toolbar/sidebar immediately when mounting or repairing the painter area, avoiding overlapping region fade animations. T/N keep controlling Assets and Properties; held-key repeats no longer toggle repeatedly.
- Recover dock input when Blender removes an area-bound modal handler. Follow the native editor space through maximize/unmaximize, preserving dock visibility.
- Added real keyboard regressions for an empty project, native region recovery, editor reopening, stale handler tracking, maximization, 2D/3D switching, and ordinary Blender shortcuts after closing Painter.

## 0.1.3

- Fixed new texture sets: create, initialize and wire all six texture images into the assigned material immediately. Initial color values are written explicitly in linear space. Invalid/occupied mesh slots are rejected before creating an orphan set.
- Fixed group presentation and interaction: nested contiguous rows, collapse arrows, Ctrl/Shift selection, Ctrl+G grouping, drag into/out of groups, drag reordering, ungrouping and subtree deletion.
- T/N toggle the actual Open Painter docks. The maintenance timer no longer repeatedly overrides panel state.
- Rebuilt the eight shelf surfaces with coordinated PBR textures and actual Cycles-rendered previews. The shelf presets work without missing bake maps.
- Removed per-frame swatch generation, cached widget geometry and asset discovery, and avoided rewiring unchanged shader graphs. Automatic evaluation yields between channels and waits until native strokes finish; idle sessions do not repeatedly scan the canvas.
- Added immediate-pixel/material, group, shader-link, preset, incremental-update and real keyboard/mouse painting regressions. Full Substance Painter parity remains incomplete.

## 0.1.2

- Added independent channel opacity and blend selection for paint, fill and folder layers, with native Blender controls. Existing projects inherit their previous layer-wide blend mode.
- Added passthrough folders: child blends evaluate against the underlying stack; group opacity and masks apply once. Corrected isolated folder alpha composition so a first Multiply child remains visible.
- Added Divide, Darken, Lighten, Difference, Exclusion, Screen, Linear Burn, Color Burn/Dodge, Soft/Hard/Vivid/Linear/Pin Light and Disable blends. Replace, color-component and specialized normal blends remain pending.
- Added nearest/bilinear fill filtering, non-repeating transparent texture borders and adjustable triplanar hardness.
- Added numerical, nested-folder, dirty-channel, persistence, smart-material roundtrip and real UI regressions.
- Added a page-referenced June 2018 PDF feature inventory and strict parity gate. Full Substance Painter feature parity is not achieved.

## 0.1.1

- Paint strokes follow the selected paint layer, channel and painted mask. Selecting non-paintable content ends painting; finishing flushes the actual session target.
- 3D strokes are limited to material slots assigned to the texture set. The artist's face selection and paint-mask state are restored after painting and survive save/reload.
- Unrelated image editors and viewport shading are preserved when starting paint. A finished 2D paint session returns to a read-only preview.
- Brush color/value, size, strength, spacing, erase and alpha controls are accessible from the main editor. Alpha images are packed; local brush assets support assignment and persistence.
- All mask entries, reorder/delete, generator parameters and levels are reachable in the scrolling Properties dock. Fill properties now expose all six channels.
- Texture-set renaming and smart-material library management are accessible in the main editor.
- Fresh strokes are included when exporting or saving before the paint polling interval expires.
- Missing evaluated outputs rebuild, missing source textures report errors, and invalid folder hierarchies are rejected before output writes.
- Added background regressions and real brush-stroke tests for target switching, face isolation, imported alpha masks and save/reload restoration.

## 0.1.0

Initial texture-set, PBR layer, mask, Cycles bake, smart-material and game texture export workflows, with an Editor Type entry and Blender-themed painting docks.
