# Changes

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
