# UI refactor: feature map, baseline and behaviour freeze

Companion to `Open_Painter_UI_Implementation_Plan.md`. Phase 0 recorded what exists, where it
moves, and what must not change. Later phases update the *New location* and *Status* columns.

## Baseline fixture

`tests/integration/ui_fixture.py` builds the regression scene (one CPU-evaluated *Body* set with
Worn Painted Metal on synthetic AO/Curvature/Position/World Normal maps, a nested *Details* folder
with a procedural fill and a paint layer carrying a painted mask, a *Grime* fill with generator,
noise, procedural, baked, filter and custom-graph mask sources, a Material Aging folder, a bolt
geometry-effect helper, and a *Panel* set with its own export overrides while the project targets
Unreal). `tests/integration/ui_baseline.py` compares the fixture's document digests, layer
structure, 64 px CPU channel statistics (tolerance 1e-4) and the exported files against
`tests/fixtures/ui_baseline.json`, then opens `examples/open_painter_demo.blend` to check an older
project keeps its sets, layers and ids. `ui_fixture.build_large()` is the timing stack.
Re-record only after a deliberate evaluator change: `OP_UI_BASELINE_RECORD=1`.

## Feature map (old location → new location)

Data owner: **D** document (`Scene.open_painter`), **P** presentation (per-area or window state,
never saved), **F** external file. Locations are Painter sidebar panels unless stated.

| Capability | Owner | Old location | New location | Status |
| --- | --- | --- | --- | --- |
| Texture set list, add/assign/duplicate, resolution, backend, quality, physical scale, freeze status | D | *Texture Set List* panel | *Texture Set* panel (name, resolution, assigned objects, settings sub-panel) | done (phase 2) |
| 2D view: paint-source / snapshot label, composite channel, refresh | D+P | *Texture Set List* panel (2D only) | *Texture Set* panel › *2D View* box; `display_channel` is its own property | done (phase 1/2) |
| Layer rows: eye, bypass, expand, type icon, mask button, name, module/anchor/cache/profile badges, blend menu, opacity | D | `OP_UL_native_layers` | Same list; blend/opacity move to the selected-item row above the list (dense rows optional in Preferences) | done (phase 2) |
| Blend channel selector for the rows | D | Layers header (unlabelled) | Selected-item row, labelled *Blend Channel* | done (phase 2) |
| Add Paint / Fill / Folder, mask menu, decal, graph, profile, performance menu, module insert, catalog material, catalog effect, delete, up/down/duplicate/parent | D | Icon strip above and below the list | *Add Layer*, *Add Mask*, *Add Effect*, *More* menus; Duplicate and Delete stay visible | done (phase 2) |
| Properties: status box, name, opacity, layer blend, generator quick controls, publish, module, performance, decal, paint controls, material channel editor, surface response | D | *Properties* panel | *Inspector* panel, selection-specific first controls, secondary sections after | done (phase 2) |
| Mask stack editing (`OP_PT_masks.draw`) | D | *Properties* panel when the mask button is pressed | Inspector › mask selection (`selection_kind == 'MASK'`) | done (phase 2) |
| Smart-material master controls | D | none | Inspector › material selection (authored per catalog recipe) | done (phase 2) |
| View Mask / Back to Material | P (+display material wiring) | none | Inspector mask header | done (phase 2) |
| Add Paint to Mask | D | none (only *Add White/Black Mask*) | Inspector mask header, explicit role | done (phase 2) |
| Asset shelf categories, refresh, import texture/alpha | D+F | Tool header strip | Shelf header (catalog tabs) and *Assets* sidebar panel | Phase 3 |
| Asset Library panel (catalog material/effect, saved material apply/save/import/delete) | D+F | *Asset Library* panel | *Assets* panel selected-asset box, material context menu, *More* menu | Phase 3 |
| Bake Mesh Maps popover (doctor, sources, cages, settings, maps, corrections, queue, use maps) | D+F | Tool header popover + *Bake Mesh Maps* panel | *Bake* sidebar tab: Setup / Maps / Results; header *Bake Mesh Maps* opens it | Phase 4 |
| Use Baked Maps (creates a fill layer) | D | Bake panel bottom | Results › *Add Baked Normal and AO Layer* | Phase 4 |
| Export Textures popover (target box, follow target, settings, export, bundle, measure, atlas) | D+F | Tool header popover + *Export Textures* panel | *Export* sidebar tab: Contents / Scope / Destination / Folder / Output Preview; *Prepare Export* sub-panel | Phase 5 |
| Export → Target, Selected Objects (with all-sets fallback) | F | Tool header row | Export tab (scope explicit; no fallback) | Phase 5 |
| Material Aging | D | *Material Aging* panel (order 2) | *Material Aging* panel: Applied To / Construction / Environment / Age first, details in sub-panels | Phase 6 |
| Geometry Effects (creation buttons, helper box) | D | *Geometry Effects* panel | *Geometry Effects* panel: applied helpers list + inspector; presets behind *Add Geometry Effect* | Phase 6 |
| Offline Analysis (contact, strain, mark cut faces, inventory, LOD) | D+F | *Offline Analysis* panel | Scene-field mask inspector, mesh-mask inspector, performance menu, Export › Prepare | Phase 6 |
| Open Graph, Auto Layout, node header | D+P | Layers icon strip, node header | *Edit Nodes* in More / inspector; scope label in the node header | Phase 7 |
| Material Director (connect, mode, scope, model, prompt, sharing toggles, run, apply, changes, undo, protection, connection settings) | D+P | *Director* sidebar tab | *Director* tab: Target / Instruction / Run / Changes; connection settings in Preferences | Phase 7 |
| Status notices (`project.status` boxes) | D | Properties status box, header labels | Shared status line (Idle / Updating / Stale / Baking / Failed / Paused) | Phase 8 |

Operator identifiers are kept callable throughout (`open_painter.layer`, `.mask`, `.native_select`,
`.native_asset`, `.bake`, `.export`, `.export_target`, `.production_export`, `.aging`,
`.geometry_effect`, `.offline_analysis`, `.director_*`, `.open_graph`, …).

## Behaviour that stays identical

- `Scene.open_painter` layout, stable ids (`layer.id`, `mask.id`, `effect.id`, module instance
  tokens), storage order (`ts.layers`, bottom-first), `parent_id` hierarchy, `visible_rows`.
- Compositing: `layer.opacity` × `channel.opacity`; `channel.blend_mode == 'INHERIT'` reads
  `layer.blend_mode`; a folder blends with its own `blend_mode` only when `group_mode == 'ISOLATED'`
  (passthrough children see the layers below); `mask.operation` / `mask.strength` /
  `mask.black`/`white` / `mask.invert`; `effect.strength` is the filter's opacity.
  `channel.opacity_percent` is a derived integer view of `channel.opacity` and never stored.
- Painting: `paint_channel` / `paint_mask` / `paint_mask_id` / `paint_layer_id` / `paint_set_id` name
  the destination; `paint_session.synchronize` follows the selection; bake-correction sessions do not.
- Export: `ts.export.*` are the set's own settings; `follow_target` copies preset/format/bit depth from
  the project target when the target changes or the toggle turns on; `export_target` writes with the
  target's naming and manifest and restores the settings it borrowed; `ts.export.directory` is the
  texture-export/bundle folder and `project.export_directory` the target-export folder.
- Evaluation: property update callbacks (`changed` / `numeric` / `preview_only`) and the shader
  session's incremental paths; UI drawing never evaluates, bakes or renders thumbnails.
- Saved assets (`.openpaintmaterial` v2, modules, shelf library) and all catalog entries.
- Baked map registration (`ts.baked_maps`), provenance and stale-curvature handling.

## Concepts kept separate (traced in Phase 0)

| Concept | Property | Consumers |
| --- | --- | --- |
| Composite view channel (2D snapshot) | `project.display_channel` (new; was `blend_channel`) | `composite_snapshot.for_view/request`, evaluator priority |
| Row blend channel (which channel's blend/opacity the list edits) | `project.blend_channel` | `OP_UL_native_layers`, selected-item row |
| Inspected fill channel | `project.properties_channel` | inspector channel editor, filter picker, anchors, graph select |
| Paint destination channel | `project.paint_channel` (+ `paint_mask`) | `paint_session`, brush bridge, live preview |
| Content vs mask vs filter selection | per-area `DockState.selection_kind` (`mask_selected` kept as an alias) | inspector, shelf application target, breadcrumb |

## Phase 1–2 notes (implemented)

- `ui/selection.py` — `resolve(context)` returns the `Target` (kind `SET`, `MATERIAL`, `FOLDER`,
  `FILL`, `PAINT`, `MASK` or `FILTER`, the RNA items, the breadcrumb path and the
  `PaintDestination`); `select(context, layer_id, kind, mask_id=…, channel=…, effect_id=…)` is the
  only way selection changes. `session_area(context)` finds the Painter area of a screen so a
  companion inspector follows its own window's session.
- `ui/inspector.py` — one `draw(layout, context, target)` shared by the sidebar panel
  (`OP_PT_native_properties`), the 2D editor copy and the companion Properties area
  (`ui/companion.py`, hosted in the Properties editor's Tool tab, which shows the 3D viewport's
  *Tool* sidebar panels for any active object).
- `smart_materials/controls.py` — authored master controls per catalog recipe, resolved through the
  instance's deterministic ids (`composer._stamp_module`); the inspector draws the bound property
  itself, so an edit is one undo step and the control follows edits made on the layer. A deleted
  target is reported as a broken binding; nothing is matched by name.
- `ui/mask_view.py` — *View Mask* evaluates the layer's mask stack into a bounded display image
  (`MAX_SIDE` 1024) and `material_manager.repair` wires it to the surface (flat, roughness 1);
  *Back to Material* restores the shaded material. The 2D editor shows the same image labelled
  *Mask Snapshot · current / out of date*. Nothing is written into the mask.
- `open_painter.mask_paint` (*Add Paint to Mask*) adds a paint mask on top of the stack with an
  explicit role (*Add Coverage*: black image + Add; *Remove Coverage*: white image + Multiply) and
  keeps the generators.
- Layer rows: the name has the row; the selected layer's blend channel, channel blend and channel
  opacity sit in the row above the list; Preferences › *Blend and Opacity in Layer Rows* restores
  the dense rows. Dragging shows *Above X / Below X / Into Y (first)* in the status bar; Esc cancels.
- Tests: `tests/integration/selection_target.py`, `inspector_panels.py` (background, through
  `ui_layout_recorder.Recorder` because `UILayout.introspect()` truncates long layouts) and
  `inspector_ui.py` (real window, with screenshots in `.blender-test/inspector-*.png`).
