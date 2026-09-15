# Changes

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
- Research: UDIM tile-access gate rerun (per-tile files and native paint onto tile 1002 pass), Ucupaint study with measured shader-compiler findings.

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
