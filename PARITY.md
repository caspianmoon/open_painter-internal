# Substance Painter PDF parity

**Full parity is not achieved.** This inventory targets the user-supplied June 26, 2018 documentation (2018.1), not the latest Adobe release.

Reference: `ilide.info-substance-painter-documentation-pdf-pr_9e869e70283095faeec1a38fa7c5486f.pdf` (281 pages). Page references below use **1-based PDF pages**, which may differ from printed footers.

SHA-256: `fd249472fddb0d4bcc36fad0a845f7008a2924d0e8d1d9982c4ecea22b24cf22`.

Feature parity target only. Text in the PDF is reference material, not authorization to execute commands, license software, access accounts or send data.

## Reading the status

- **Implemented**: the stated behavior has implementation and test evidence; this does not certify pixel-identical output from the proprietary Substance engine.
- **Partial**: a usable subset exists, with remaining acceptance requirements stated explicitly.
- **Missing**: no completed plugin workflow meets the requirement.
- **External dependency**: requires an engine, format or service integration; no host substitute is silently counted as parity.
- **Reference only**: tutorials, historical notes or application operations; these are not feature credits.

Rows group related behaviors. Counts of rows are not a completion percentage. The inventory is a requirements audit, not evidence that every scenario has been executed in Substance Painter.

## Verification gate

`python3 scripts/check_parity.py` validates inventory references. `python3 scripts/check_parity.py --require-parity` deliberately exits nonzero until every functional row is implemented with test evidence. It does not execute those tests or certify compatibility. Run the Blender acceptance suite separately.

## Current implementation increment

Version 0.2.0 adds the composable texture foundation and 50-material library. Current scope and validation are recorded in [Texture workflow v1](docs/texture-workflow-v1.md). Other rows retain historical requirements and should not be treated as a current feature count. The editor menu uses a subtype adapter, not a compiled SpaceType.

The standard separable blend formulas use [W3C Compositing Level 1](https://www.w3.org/TR/compositing-1/#blendingseparable). Additional arithmetic formulas are defined and tested in our code; the manual does not establish numerical equivalence. The current evaluator includes nonseparable color modes and four normal blend modes; exact Adobe numerical equivalence is not claimed.

## Dependency order for remaining work

1. Dynamic channels/precision and correct signed height/normal/coverage composition.
2. Layer and mask effect stacks, anchors and dependency invalidation, then linked instances and smart masks.
3. Recorded multi-channel strokes, material picker, polygon fill, projection/stencil, smudge/clone and reprojection.
4. Mesh-derived world normal/position/ID/thickness/signed-curvature maps, batch baking and UDIM sets.
5. Seam padding, batch/preset/HDR/PSD export, resource lifecycle and advanced viewport controls.
6. Runtime/format/service compatibility for SBSAR/SPSM, particles, shaders/rendering and live links.

## Projects

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `project.creation`<br>pp. 11, 12, 13, 84 | **Partial** — Mesh import, templates, texture sets from material IDs, map auto-assignment, tangent convention and project resolution. | New sets immediately create and assign six initialized texture images and a connected material for the selected mesh slot. Still add mesh/template import, automatic one-set-per-material creation, inferred map slots and tangent conventions.<br>Evidence: [texture_set_manager.py](src/open_painter/core/texture_set_manager.py), [phase01.py](tests/integration/phase01.py), [phase13.py](tests/integration/phase13.py) |
| `project.persistence`<br>pp. 13, 78, 203, 204 | **Partial** — Portable projects, compact/incremental save, per-set history and configurable autosave/backup retention. | Test crash recovery and rotating backups including save while painting; native .blend packing works, but Painter-style per-set history, incremental saving and autosave controls are absent.<br>Evidence: [project_manager.py](src/open_painter/core/project_manager.py), [phase11.py](tests/integration/phase11.py) |
| `project.reimport`<br>pp. 84, 179, 247, 250, 272 | **Missing** — Mesh reload, texture-set reassignment, preserving brush strokes and UV reprojection. | Store stroke samples and replay them after topology/UV/resolution changes; distinguish 2D strokes from world-space strokes and test changed material IDs. |
| `project.udim`<br>pp. 11, 179, 180, 181 | **Implemented** — 2018 UDIM workflow: one texture set per tile, material names ignored. | Create one set per occupied tile from selected unassigned meshes, regardless of material names. Validate face boundaries and coordinates; independently paint, bake, save and export numbered tiles. Native and material brushes can also cross tiles via Paint Across Sets. This is the per-tile set model, not a shared layer stack or automatic migration of existing painted sets.<br>Evidence: [udim.py](src/open_painter/core/udim.py), [udim-workflow.md](docs/udim-workflow.md), [udim_workflow.py](tests/integration/udim_workflow.py), [udim_material.py](tests/integration/udim_material.py), [udim_paint_ui.py](tests/integration/udim_paint_ui.py) |

## Texture sets

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `sets.management`<br>pp. 92, 93, 94, 214 | **Partial** — Set naming, visibility/solo/all, mesh picking, shader instances, slot assignments. | Automate creation and mesh picking, add per-set visibility and shader selection. Verify multi-material meshes. Manual assignment, naming and selection already work.<br>Evidence: [phase01.py](tests/integration/phase01.py), [phase11.py](tests/integration/phase11.py) |
| `sets.channels`<br>pp. 93, 94, 172, 173, 182, 188, 190, 242 | **Partial** — Editable channel list and precision: sRGB8, L/RGB8/16, L16F/L32F/RGB32F; custom User0–7 names. | Eight fixed float channels now include Base Color, Roughness, Metallic, Normal, Height, AO, Emission and Opacity, independently enabled/blended. User-defined channels and per-channel storage precision remain.<br>Evidence: [constants.py](src/open_painter/constants.py), [channel.py](src/open_painter/properties/channel.py) |
| `sets.resolution`<br>pp. 93, 94, 179, 243 | **Partial** — Independent width/height, resolution changes and supported 8K workflows. | Evaluate and export non-square sets at every supported precision, demonstrate bounded memory and stroke replay on resolution increases. Current square 512–8192 options resample raster inputs.<br>Evidence: [phase03.py](tests/integration/phase03.py), [evaluator.py](src/open_painter/evaluation/evaluator.py) |

## Interface

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `ui.layout`<br>pp. 73, 74, 75, 76, 77, 84, 89, 135, 136 | **Partial** — Standard Painter dock arrangement, configurable panels, menus and precise numeric controls in Blender. | Painter layout preserved with assets, layer/settings tabs, effects and projection controls. Registration lifecycle is tested, but the menu entry remains a subtype adapter. A dedicated native SpaceType is not implemented.<br>Evidence: [editor.py](src/open_painter/ui/editor.py), [registration_lifecycle.py](tests/integration/registration_lifecycle.py), [reference_layout_ui.py](tests/integration/reference_layout_ui.py) |
| `ui.views`<br>pp. 85, 86, 87, 88, 89, 90, 91, 92, 212 | **Partial** — 3D/2D/both layouts, lit 2D, channel/material/baked-map previews and camera controls. | Native 3D and unlit 2D views share texture-set state and can be switched in the existing area. Lit 2D, automatic paired layout and a dedicated compiled SpaceType remain outside the current implementation.<br>Evidence: [native.py](src/open_painter/ui/native.py), [native_ui.py](tests/integration/native_ui.py) |
| `ui.preferences`<br>pp. 78, 79, 80, 81, 82, 83, 135, 136 | **Partial** — Preferences, shortcut editing, shelf paths, precision sliders and tablet settings. | Expose relevant plugin settings through Blender preferences/keymaps; preserve Shift precision and native numeric entry. Do not duplicate Adobe application settings blindly.<br>Evidence: [native_ui.py](tests/integration/native_ui.py), [preferences.py](src/open_painter/preferences.py) |

## Layers

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `layers.structure`<br>pp. 95, 96, 97, 98 | **Partial** — Paint/fill/folder hierarchy, reorder, multiselect, copy/paste/group, visibility and undo. | Nested collapsible groups, multi-selection, drag reparenting/reordering, grouping/ungrouping, deletion and duplication are implemented. Layer clipboard operations and broader mixed-hierarchy selection cases still need work.<br>Evidence: [layer_stack.py](src/open_painter/core/layer_stack.py), [phase02.py](tests/integration/phase02.py), [phase13.py](tests/integration/phase13.py), [repair_ui.py](tests/integration/repair_ui.py) |
| `layers.channel_composition`<br>pp. 95, 97, 100 | **Implemented** — Independent channel opacity and blend selection on paint/fill/folder layers. | Change roughness blending without changing metallic; preserve channel settings through duplicate, .blend reload and smart-material roundtrip. Overall layer opacity multiplies channel opacity; legacy layers inherit their previous blend.<br>Evidence: [channel.py](src/open_painter/properties/channel.py), [evaluator.py](src/open_painter/evaluation/evaluator.py), [phase12.py](tests/integration/phase12.py), [compositing_ui.py](tests/integration/compositing_ui.py) |
| `layers.folders`<br>pp. 100, 163, 166 | **Implemented** — Isolated and passthrough folders with opacity and masks. | Passthrough children must see the actual lower stack; opacity/masks attenuate the completed group once; isolated first Multiply child must remain visible. Verify nested groups and empty groups.<br>Evidence: [evaluator.py](src/open_painter/evaluation/evaluator.py), [phase12.py](tests/integration/phase12.py) |
| `layers.blends`<br>pp. 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 189, 190 | **Partial** — Full documented linear-space blends, alpha replacement and normal-specific blends. | Still add Replace, Tint, Saturation, Color, Value and Detail/Inverse Detail/Combine normal modes, including reference renders and transparent coverage semantics. Twenty arithmetic/standard modes are implemented; no pixel-identical Substance engine claim.<br>Evidence: [blend_modes.py](src/open_painter/evaluation/blend_modes.py), [test_compositing.py](tests/unit/test_compositing.py) |
| `layers.instances`<br>pp. 110, 111, 112, 113, 221 | **Missing** — Linked instances across texture sets, source navigation, folder propagation and loop protection. | Edits to a source update every instance and its children; reject cycles. Respect the PDF limitation that raster paint strokes do not transfer to different sets. |

## Fills

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `fills.projection`<br>pp. 109, 110 | **Partial** — UV/triplanar mapping, transforms, hardness, filtering and tiling. | UV and triplanar transforms, hardness, Nearest/Bilinear and Repeat/None are implemented. Add the documented high-quality/sharp filters and validate projection/orientation on production meshes.<br>Evidence: [projection.py](src/open_painter/painting/projection.py), [phase12.py](tests/integration/phase12.py), [test_compositing.py](tests/unit/test_compositing.py) |

## Masks

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `masks.stack`<br>pp. 98, 99, 100, 163, 176, 202 | **Partial** — Grayscale masks, paint, invert, disable, isolate, copy/paste and content/mask effect selection. | Explicit white/black/bitmap masks and independent mask Levels/filter effects work. Mask clipboard, isolated preview, color-selection and height-combination workflows remain.<br>Evidence: [mask_eval.py](src/open_painter/evaluation/mask_eval.py), [phase04.py](tests/integration/phase04.py), [phase11.py](tests/integration/phase11.py), [properties_panel.py](src/open_painter/ui/properties_panel.py), [phase15.py](tests/integration/phase15.py) |
| `masks.quick`<br>pp. 153, 202 | **Missing** — Temporary quick mask with paint, reset, invert and polygon-fill interaction. | Quick-mask edits constrain strokes without adding a permanent mask or changing the project after dismissal. |

## Effects

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `effects.stack`<br>pp. 163, 164, 165, 166, 167 | **Partial** — Ordered Paint, Fill, Generator, Levels, Filter and Anchor effects on layer content and masks. | Ordered 52 texture filters operate on channel content and accumulated grayscale masks, with native reorder/removal and portable persistence. Procedural fills and mesh generators are reusable mask sources. Anchor references, adjustment layers, effect clipboard and complete Painter effect behavior remain.<br>Evidence: [channel_eval.py](src/open_painter/evaluation/channel_eval.py), [mask_eval.py](src/open_painter/evaluation/mask_eval.py), [phase15.py](tests/integration/phase15.py), [texture_operations.py](tests/integration/texture_operations.py) |
| `effects.levels`<br>pp. 165, 166 | **Partial** — Levels histogram, input black/gamma/white, output black/white, RGB component editing and clamp. | Input black/gamma/white, output black/white and opacity operate on channel content and grayscale masks with independent pixel tests. Component-specific levels, histogram UI, unclamped HDR processing and Adobe numerical equivalence are not implemented.<br>Evidence: [filters.py](src/open_painter/texture_engine/filters.py), [effect.py](src/open_painter/properties/effect.py), [test_effects.py](tests/unit/test_effects.py), [phase15.py](tests/integration/phase15.py) |
| `effects.anchors`<br>pp. 168, 169, 170, 214, 218 | **Implemented** — Named anchors, references, dependency updates, broken-reference feedback and navigation. | Anchor rows in mask and channel filter stacks; Published Input masks, fill content and generator micro details reference them within one set; any acyclic reference is allowed (item-granular cycle check refuses loops when picked); dependent fills and generators re-evaluate; missing anchors read as neutral with a warning; anchors list consumers and consumers locate their anchor; deleting reports broken references.<br>Evidence: [anchors.py](src/open_painter/evaluation/anchors.py), [references.py](src/open_painter/evaluation/references.py), [anchors.py](src/open_painter/ui/anchors.py), [anchor_points.py](tests/integration/anchor_points.py), [shader_sources.py](tests/integration/shader_sources.py) |
| `effects.generators`<br>pp. 164, 174, 175, 176, 177, 178, 217, 218, 220 | **Partial** — Mesh-aware generators, smart masks, parameterized 3D noises and linear gradients. | Add world-position/normal-aware dirt/dust, Perlin/fractal/Simplex/Worley noises, 3D gradients and reusable smart masks. Micro Height / Micro Normal anchor inputs wear and soil painted relief. Current edge wear/dirt and UV noise are approximations.<br>Evidence: [generators](src/open_painter/generators), [micro_details.py](src/open_painter/generators/micro_details.py), [phase06.py](tests/integration/phase06.py), [micro_detail_anchors.py](tests/integration/micro_detail_anchors.py) |
| `effects.substance`<br>pp. 170, 171, 172, 173, 174, 175, 243 | **External Dependency** — SBSAR evaluation, custom Substance Designer filters/generators and input/output metadata. | Integrate a usable Substance runtime or explicitly approved compatible implementation, including parameter/UI/color-space bindings. Native NumPy generators do not load SBSAR files. |

## Painting

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `paint.raster`<br>pp. 136, 137, 138, 140, 152 | **Partial** — Paint and erase on layers/masks in 2D/3D, brush values, alpha and material-slot isolation. | Retain verified brush routing/isolation; add multi-channel material painting and all documented brush behaviors. Current strokes write one selected channel at a time.<br>Evidence: [paint_session.py](src/open_painter/painting/paint_session.py), [paint_workflow.py](tests/integration/paint_workflow.py) |
| `paint.material`<br>pp. 85, 136, 140 | **Missing** — Simultaneous multi-channel material brush, per-channel bitmap/procedural inputs and material picker. | A single pressure-sensitive stroke writes selected PBR channels in registration, and the picker samples the complete material rather than one RGB image. |
| `paint.dynamics`<br>pp. 85, 137, 138, 214, 242 | **Partial** — Flow versus per-stroke opacity, angle/follow-path, jitter, alignment, culling, pressure, lazy mouse and straight-line snapping. | Expose and verify supported native dynamics, implement missing flow/opacity distinctions and material-wide behavior, test tablet buffering and continued strokes. Current UI exposes size, strength, spacing and alpha.<br>Evidence: [brush_bridge.py](src/open_painter/painting/brush_bridge.py), [paint_workflow.py](tests/integration/paint_workflow.py) |
| `paint.symmetry`<br>pp. 86, 87, 88 | **Missing** — Symmetry plane display/manipulation and mirrored painting. | Expose and validate mirrored strokes for every enabled channel and mask, with plane transform controls. |
| `paint.stencil`<br>pp. 139, 153 | **Missing** — Independent screen-space stencil and projection painting. | Transform/hide the stencil independently of the brush alpha; project material channels with correct occlusion and viewport feedback. Imported alpha alone is not stencil support. |
| `paint.polygon_fill`<br>pp. 153, 154 | **Missing** — Triangle/polygon, connected-mesh and UV-island fill, including masks. | Fill exactly the selected region, preserving other material slots/islands, with undo and consistent multichannel values. |
| `paint.smudge_clone`<br>pp. 154, 155 | **Missing** — Smudge and clone, aligned source and nondestructive reading of lower content. | Implement tools and their pass-through effect workflow; invoking Blender brushes alone cannot guarantee nondestructive Painter semantics. |
| `paint.particles`<br>pp. 141, 142, 143, 144, 145, 146, 147, 148, 149, 150, 151, 152 | **External Dependency** — Physical paint/erase/projection using PopcornFX particles, presets and custom emitters. | Provide a compatible simulation/runtime and deterministic stroke recording. Native brush jitter is not physical particle painting. |
| `paint.seams`<br>pp. 24, 25, 26, 27, 90, 215, 219 | **Missing** — UV and 3D-neighbor seam padding after strokes, layer edits and resolution changes. | Demonstrate seamless results across disconnected UV borders in 2D and 3D, with selectable UV versus 3D padding and export dilation. |
| `paint.flow_map`<br>pp. 186, 187 | **Missing** — Direction-following normal/flow-map brush workflow. | Paint the documented directional texture along curved paths and verify exported vector orientation. |

## Baking

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `bake.pipeline`<br>pp. 156, 157, 161, 162, 163, 243 | **Partial** — All-set batch baking, high/low/cages, matching by name, antialiasing and ray controls. | Add batch queue/cancellation and low/high suffix matching; validate cage and ray limits on multiple meshes and all sizes. Explicit normal/AO Cycles baking is implemented.<br>Evidence: [baking](src/open_painter/baking), [phase05.py](tests/integration/phase05.py) |
| `bake.normal`<br>pp. 157, 158 | **Partial** — Tangent normal baking, normal convention and tangent-basis choices. | Verify MikkTSpace/per-fragment tangent settings and DirectX/OpenGL output against mesh fixtures; current Cycles bake lacks the full documented settings.<br>Evidence: [baking](src/open_painter/baking), [phase05.py](tests/integration/phase05.py) |
| `bake.world_normal`<br>pp. 158 | **Missing** — World-space normals combining geometry and baked tangent detail. | Test rotated objects, high-poly detail and transform changes against known world directions. |
| `bake.id`<br>pp. 158, 159 | **Missing** — ID maps from vertex color, material color, object and polygroup, with palette options. | Produce stable IDs from every source and use color-selection masks without edge contamination. |
| `bake.ao`<br>pp. 159 | **Partial** — AO from high/low geometry and normals, ray distribution/distances/spread/attenuation and backface controls. | Expose and validate the documented source and ray choices; current Cycles AO supports a subset.<br>Evidence: [phase05.py](tests/integration/phase05.py) |
| `bake.curvature`<br>pp. 159, 160 | **Partial** — Signed concave/convex curvature, per-pixel/per-vertex variants, seam and detail control. | Flat must be neutral gray, convex white and concave black with tunable scale. Current unsigned tangent-normal gradient does not satisfy this.<br>Evidence: [curvature.py](src/open_painter/baking/maps/curvature.py) |
| `bake.position`<br>pp. 160, 161, 216 | **Missing** — Position maps, RGB/axis output and bounding-box/sphere, scene/per-material normalization. | Match known coordinates for multiple transformed objects and use the results in procedural masks. |
| `bake.thickness`<br>pp. 161 | **Missing** — Thickness through inverted AO with ray controls and normalization. | Distinguish thin and thick regions reliably and support high/low meshes and batch export. |

## Advanced channels

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `advanced.height`<br>pp. 188, 189 | **Missing** — Signed HDR height, bitmap remapping, additive accumulation and height-to-normal composition. | Preserve values outside 0–1, map neutral bitmap gray to zero, and combine painted height with baked normals both in the viewport and export. Current height is unsigned/clamped. |
| `advanced.normal`<br>pp. 189, 190, 191 | **Partial** — Detail/inverse-detail/combine normals, per-input DX/OpenGL and baked-normal combine/replace. | Validate reoriented normal blending and independent input conventions, including combined height. Current RGB normalization and export green inversion are insufficient.<br>Evidence: [evaluator.py](src/open_painter/evaluation/evaluator.py), [export](src/open_painter/export) |
| `advanced.ao`<br>pp. 182, 183, 184, 185, 186 | **Partial** — Baked AO plus painted AO with multiply/replace choices. | Expose set-level mixing and prove paint can remove baked detail as well as add occlusion. Current baked-map fill layers provide a manual subset.<br>Evidence: [baking_panel.py](src/open_painter/ui/baking_panel.py) |
| `advanced.material_layering`<br>pp. 192, 193, 194, 195 | **Missing** — Dynamic material layering sub-stacks, material/mask shader inputs and shader-parameter JSON. | Roundtrip layered shader parameters and render mask-controlled material sub-stacks. |

## Assets

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `assets.browser`<br>pp. 113, 114, 115, 116, 117, 118, 119, 120, 121 | **Partial** — Shelf usage filters, fuzzy/regex/field/negative queries, saved searches, subwindows and multiple paths. | Implement the documented query/filter and library organization behaviors. Current shelf has simple text search, tabs and one material library path.<br>Evidence: [assets.py](src/open_painter/ui/dock/assets.py), [library.py](src/open_painter/smart_materials/library.py) |
| `assets.import`<br>pp. 119, 120, 121 | **Partial** — Batch import with usage/tags, session/project/library lifetime and resource previews. | Import texture/material/alpha resources at each lifetime and preserve metadata and user previews; verify no project resource disappears on reload.<br>Evidence: [alphas.py](src/open_painter/painting/alphas.py), [assets_panel.py](src/open_painter/ui/assets_panel.py) |
| `assets.presets`<br>pp. 117, 118, 176, 177, 178, 179 | **Partial** — Brush/tool/material presets, folder-scoped smart materials and reusable smart masks. | Eight original self-contained PBR shelf presets with rendered previews are available; complex material application creates editable groups. Still add brush/tool preset saving, smart masks, and folder-scoped portable export; the current archive saves the whole stack.<br>Evidence: [serializer.py](src/open_painter/smart_materials/serializer.py), [phase07.py](tests/integration/phase07.py), [phase12.py](tests/integration/phase12.py) |
| `assets.formats`<br>pp. 121, 170, 176 | **External Dependency** — Substance SPSM/SPPR/SBSAR compatibility. | Roundtrip documented asset formats or provide a verified importer/runtime. Open Painter JSON/ZIP is its own format, not a compatible SPSM file. |
| `assets.update`<br>pp. 205, 206, 247, 250 | **Missing** — Outdated-resource tracking, replacement and automatic updating of project references. | Replace a resource once, update its users safely, preserve paint, and offer undo or recovery. |
| `assets.catalog`<br>pp. 206, 207, 208, 209, 210, 211, 212, 213, 217, 218, 220, 243 | **External Dependency** — Substance Source account/library and bundled materials, alphas, grunges, fonts and particle presets. | Provide authorized access/compatible content, including dynamic font text. Three original starter materials are not equivalent to the documented catalog. |

## Rendering

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `viewer.environment`<br>pp. 121, 122, 123, 124, 198, 199, 200 | **Partial** — HDR lighting, exposure/rotation/background opacity, shader selection and camera focus/FOV/aperture. | Expose and verify complete viewport controls with predictable exports. Current native shading and environment rotation are preserved.<br>Evidence: [interaction.py](src/open_painter/ui/dock/interaction.py), [native_ui.py](tests/integration/native_ui.py) |
| `viewer.post`<br>pp. 124, 125, 126, 127, 128, 129, 130, 131, 132, 133, 134, 135 | **External Dependency** — Yebis antialiasing, color correction, DOF, tone mapping, thirteen glare styles, vignette and lens distortion. | Supply artist-visible equivalent controls and comparison renders or a compatible renderer; Blender availability alone is not tested parity. |
| `viewer.color`<br>pp. 181, 182, 243 | **Partial** — Color profiles/LUTs, white point, linear/sRGB interpretation and OCIO workflow. | Load the documented encoded LUT, control tone mapping, and verify viewport/export color consistency. Host color management exists but is not integrated to this specification.<br>Evidence: [image_writer.py](src/open_painter/export/image_writer.py) |
| `viewer.iray`<br>pp. 195, 196, 197, 198, 199, 200, 201, 202 | **External Dependency** — Iray rendering, progress/pause, sample/time budgets, render saving, dome/ground and MDL materials. | Provide the documented rendering/material semantics or explicitly accepted equivalent; Cycles is a different renderer, not Iray/MDL compatibility. |
| `viewer.shaders`<br>pp. 122, 192, 198, 202, 244, 245 | **External Dependency** — Custom GLSL/MDL shaders, shader UI scripting, animated parameters and layered shader JSON. | Load supported shaders with parameter UI and animation, and document any translation/runtime limitations. A Principled material graph is a subset. |

## Export

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `export.formats`<br>pp. 14, 15, 16, 17, 18 | **Partial** — All documented raster/HDR formats and PSD layered texture-set export. | Add BMP, ICO, JPEG, JNG, PBM, PGM, PPM, TGA, WBMP, XPM, GIF, HDR, EXR, J2K/JPEG2000, PFM and PSD as needed by the PDF. Verify precision, alpha and color-space roundtrip. Current export is PNG/TIFF 8/16.<br>Evidence: [image_writer.py](src/open_painter/export/image_writer.py), [phase09.py](tests/integration/phase09.py) |
| `export.presets`<br>pp. 16, 17, 20, 21, 22, 23 | **Partial** — Editable reusable export presets, arbitrary map outputs, component/alpha bindings, naming variables and folders. | Create/copy/rename/delete/import presets, emit multiple custom files and use mesh/set variables and real painted coverage. Current custom export emits one scalar-packed RGBA file.<br>Evidence: [channel_pack.py](src/open_painter/export/channel_pack.py), [exporter.py](src/open_painter/export/exporter.py), [phase09.py](tests/integration/phase09.py) |
| `export.targets`<br>pp. 16, 17, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65 | **Partial** — Documented renderer/game presets and metal/rough ↔ spec/gloss conversion. | Cover Adobe Standard/OBJ/MDL, document channels, mesh maps, Arnold 4/5, Corona, CryEngine3, Dota2, Keyshot, LensStudio, Lumberyard, Redshift, Shade3D, Unity4/5 metallic/specular, UE4, V-Ray, Octane, PBR and non-PBR variants; validate maps in representative consumers. Current Blender/UE5/Unity-metallic presets are only a subset.<br>Evidence: [presets](src/open_painter/export/presets), [phase09.py](tests/integration/phase09.py) |
| `export.padding`<br>pp. 18, 24, 25, 26, 27 | **Missing** — Pass-through, infinite dilation, finite dilation plus transparent/default/diffusion padding. | Test map-specific neutral colors, alpha and UV edge dilation with no unrelated-island bleed, including per-set overrides. |
| `export.batch`<br>pp. 18, 19, 20 | **Missing** — Select all/none texture sets, per-set format/resolution overrides, progress/cancel and diagnostics. | Export several sets in one operation with configurable outputs and recovery from cancellation. Current exporter operates on the selected set only. |
| `export.resolution`<br>pp. 19, 179 | **Partial** — Lower-resolution bilinear export and higher-resolution source/stroke reevaluation. | Recompute procedural content and replay strokes at export resolution rather than upscaling a finished bitmap; current overrides resample evaluated images.<br>Evidence: [exporter.py](src/open_painter/export/exporter.py) |
| `export.services`<br>pp. 16, 17, 195 | **External Dependency** — Sketchfab/glTF and ArtStation publishing workflows. | Implement local glTF packaging and authorized service upload integrations with reviewable output and error recovery. No account uploads are authorized by this parity request. |

## Integration

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `integration.api`<br>pp. 203, 204, 207, 208, 244, 245 | **Missing** — JavaScript/QML plugin API, events and documented CLI project/mesh/map/export arguments. | Provide an explicit compatibility layer or a separately documented Blender Python API/CLI with equivalent tested workflows; host Python availability is not JS/QML compatibility. |
| `integration.unity`<br>pp. 204, 205 | **Missing** — Unity live link for texture/material updates. | Demonstrate edits appearing in a connected Unity project with color spaces, packing and material links preserved. |
| `integration.diagnostics`<br>pp. 68, 69, 70, 71, 72, 73, 251, 252, 253, 254, 270, 271, 272, 273, 274 | **Partial** — Performance guidance, logging, project validation and recovery diagnostics. | Supply actionable diagnostics for missing maps/resources, UV/mesh issues and memory/driver failures; benchmark real workloads. Native platform diagnostics need not duplicate DXDiag on macOS.<br>Evidence: [validation.py](src/open_painter/core/validation.py), [phase10.py](tests/integration/phase10.py), [phase11.py](tests/integration/phase11.py) |

## Reference material

| ID / PDF pages | Feature / status | Acceptance and evidence |
| --- | --- | --- |
| `reference.tutorials`<br>pp. 5, 6, 7, 8, 9, 10, 11, 28, 65 | **Reference Only** — Tutorials: peeled paint, tiling material painting and external rendering setup. | Treat tutorials as acceptance scenarios for layers/anchors/material brushes/export; do not count each tutorial screenshot as an independently implemented feature. |
| `reference.operations`<br>pp. 66, 67, 68, 251, 254, 255, 256, 257, 258, 259, 260, 261, 262, 263, 264, 265, 266, 267, 268, 269, 270, 275, 276, 277, 278, 279, 280, 281 | **Reference Only** — Adobe license activation, installation paths, registry and platform-specific troubleshooting. | These describe operating the referenced application, not instructions to execute or required Adobe licensing in this Blender extension. Functional counterparts are tracked above. |
| `reference.releases`<br>pp. 208, 209, 210, 211, 212, 213, 214, 215, 216, 217, 218, 219, 220, 221, 222, 223, 224, 225, 226, 227, 228, 229, 230, 231, 232, 233, 234, 235, 236, 237, 238, 239, 240, 241, 242, 243, 244, 245, 246, 247, 248, 249, 250 | **Reference Only** — Historical release notes through 2018.1. | Unique features in release notes are incorporated above (seam padding, 3D noises, anchors, instancing, LUTs, fonts, shader animation, stroke opacity and resource updates). Historical bugfix lists are regression context, not separate feature credits. |
