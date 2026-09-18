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
| Asset shelf categories, refresh, import texture/alpha | D+F | Tool header strip | Shelf header (catalog tabs) and *Assets* sidebar panel | done (phase 3) |
| Asset Library panel (catalog material/effect, saved material apply/save/import/delete) | D+F | *Asset Library* panel | *Assets* panel selected-asset box, layer-row context menu, *Assets* menu | done (phase 3) |
| Bake Mesh Maps popover (doctor, sources, cages, settings, maps, corrections, queue, use maps) | D+F | Tool header popover + *Bake Mesh Maps* panel | *Bake* sidebar tab: Setup / Maps / Results; header *Bake Mesh Maps* opens it | done (phase 4) |
| Use Baked Maps (creates a fill layer) | D | Bake panel bottom | Results › *Add Baked Normal and AO Layer* | done (phase 4) |
| Export Textures popover (target box, follow target, settings, export, bundle, measure, atlas) | D+F | Tool header popover + *Export Textures* panel | *Export* sidebar tab: Contents / Scope / Destination / Folder / Output Preview; *Prepare Export* sub-panel | done (phase 5) |
| Export → Target, Selected Objects (with all-sets fallback) | F | Tool header row | Export tab (scope explicit; no fallback); `open_painter.export_target` stays callable | done (phase 5) |
| Material Aging | D | *Material Aging* panel (order 2) | *Material Aging* panel: Applied To / Construction / Environment / Age first, details in sub-panels | done (phase 6) |
| Geometry Effects (creation buttons, helper box) | D | *Geometry Effects* panel | *Geometry Effects* panel: applied helpers list + inspector; presets behind *Add Geometry Effect* | done (phase 6) |
| Offline Analysis (contact, strain, mark cut faces, inventory, LOD) | D+F | *Offline Analysis* panel | Scene-field mask inspector, mesh-mask inspector, performance menu, Export › Prepare | done (phase 6) |
| Open Graph, Auto Layout, node header | D+P | Layers icon strip, node header | *Edit Nodes* in More and the row menu, framed on the selection; scope path, Frame Selected and Full Graph in the node header | done (phase 7) |
| Material Director (connect, mode, scope, model, prompt, sharing toggles, run, apply, changes, undo, protection, connection settings) | D+P | *Director* sidebar tab | *Director* tab (Material Assistant): Target / Instruction / Run / Changes; connection settings in Preferences | done (phase 7) |
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

## Phase 3 notes (implemented)

- `shelf_library.build` writes `blender_assets.cats.txt` next to the library and sets each asset's
  `catalog_id` (`library_categories.CATALOG_IDS`, one uuid5 per category); local handles get the
  same ids, so the shelf's native catalog tabs can filter by category beside its search field.
  Blender enables catalog tabs only through the shelf's own catalog menu (no RNA/operator:
  `asset_shelf_settings.cc`), so the shelves opt into `STORE_ENABLED_CATALOGS_IN_PREFERENCES` and
  the tool header keeps one type dropdown. The manifest carries `catalogs`; a library without it
  is rebuilt.
- `native.OP_OT_native_asset.invoke` selects on the first click (`select_click`, per-area
  `DockState.pending_asset`) and applies on the second; `execute` (buttons, scripts) applies at
  once. `entry_key` / `apply_now` / `replace` are `SKIP_SAVE` properties for the Assets panel,
  the tool header's Apply button and the shelf context menu (`draw_asset_context_menu`).
  `application_target` and `missing_requirements` word the target and the missing mesh maps.
- Geometry effect presets are shelf entries of kind `GEOMETRY` (category *Geometry*); activating
  one opens the `open_painter.geometry_effect` dialog for the selected object.
- `assets_panel.apply_document` wraps a saved whole-set document in one named group.
- `native.layer_context_menu` extends Blender's list-item context menu on the layer rows.

## Phase 4 notes (implemented)

- `ui/bake_view.py`: the *Bake* sidebar tab (`OP_PT_bake_setup` / `_maps` / `_results`, 3D and 2D)
  with `draw_actions` (Bake Selected Maps, progress, *Stop After This Map*, Back to Painting) in
  Setup and Results. `open_painter.show_task` switches the sidebar category (`Painter`, `Bake`,
  `Export`); the tool header's *Bake Mesh Maps* button uses it. The old popover
  (`OP_PT_native_bake`) and the legacy `OP_PT_bake` draw the same sections stacked (`draw_compact`).
- Setup shows target names and the model's world size, hides sources, matching and the cage
  unless *Bake from High Poly* is on, and Cycles sampling unless a Cycles map is ticked.
- Maps: one collapsible row per map — checkbox, full name, state — whose body names the method
  (target-edge curvature and signed mean curvature stay distinct) and holds the map's own
  sampling/distance settings. *The material reads: …* + *Select* (`open_painter.bake` action
  `SELECT_RECOMMENDED`) only adds to the selection.
- States (`map_state`): *Missing*, *Up to date*, *Out of date* (provenance record differs —
  mesh, UVs, assignments, sources, settings), *Baking*, *Failed* (from `baking/history.py`, kept
  until a later success), *Not checked* (an image without a provenance record).
- `baking/history.py` records what the last queue did; `bake_steps` registers each map as it
  lands, so a failure keeps the previous map of that kind. `OP_OT_bake_queue` captures its set
  by id and address at invoke and stops if the sets change; `open_painter.bake_stop` ends the
  queue at the next map boundary (a running Cycles map cannot be interrupted, and the UI says so).
- *Use Baked Maps* is now *Add Baked Normal and AO Layer*; registration for generators happens
  when maps land, independently.

## Phase 5 notes (implemented)

- `export/plan.py`: `resolve(context, contents, scope, folder_source)` returns the `Plan` — the
  sets and objects of the scope (an empty selection stays empty with a reason), each set's
  effective preset / format / bit depth / size / naming / normal convention, every output file with
  its packing and colour space, the manifest and mesh outputs of the target route, the bundle
  folder of the bundle route, existing-file conflicts, and notes about overrides and differing
  saved folders. `execute(context, plan, overwrite)` writes exactly those files through the
  existing exporter, target naming, manifest and mesh code (borrowing the target's settings and
  putting the set's own back, as `open_painter.export_target` does) and raises if the exporter's
  names ever differ from the plan.
- `ui/export_view.py`: the *Export* tab and the compact popover/legacy panel draw the same view
  from the same plan. View choices (contents, scope, folder source, replace existing) live on
  `WindowManager.open_painter_export` with `SKIP_SAVE`; opening the view mutates no saved setting.
  *Prepare Export* holds Measure Density, Stage Atlas and Stage Mesh LOD.
- The tool header's *Export → Target* one-click button is gone; *Export* opens the tab. The
  legacy operator keeps its all-sets fallback for scripts and is documented as such.

## Phase 6 notes (implemented)

- Material Aging (`ui/aging_panel.py`): *Applied to <objects>* and the driven folder, *Follow Edits
  Live* (named, no play icon), then *Material Construction*, *Environment* + *Age in Years*, and
  sub-panels *Exposure (relative intensities)*, *Use*, *Maintenance*, *Events*, *Simulated State*
  (“a model estimate, not a measurement”) and *Art Direction (after the simulation)*. Sliders keep
  their 0–1 factors with endpoint meanings in the tooltips (temperature says “not degrees”). A
  slider moved off a preset shows *Custom · modified from <preset>* (`aging.environment_base`) with
  *Reset Environment*; *Reset Aging Setup* (age, history, use, care, events, seed, art direction)
  is separate from *Reset Art Direction*, and the construction is never reset by either.
- Geometry Effects: *Add Geometry Effect* menu (also shelf entries), the helpers applied to the set
  with visibility and a select button, and a selection-specific inspector stating what the effect
  produces (*Adds geometry · N instanced pieces*, *Changes displacement*), its distribution source,
  its parameters, Regenerate / Freeze / Remove Effect, and a *Bake Back* sub-panel that names its
  output and the Keep / Hide / Remove choice for the generated geometry.
- Offline analysis (`ui/offline_analysis.py`): the panel is no longer registered; each action names
  its input, output and reader in `ACTIONS` (tooltips and dialogs). *Sample Contact History* sits on
  Contact scene fields, *Rest-State Strain* on attribute scene fields and attribute mesh masks,
  *Mark Selected Cut Faces* on attribute / face-selection mesh masks, *Performance Inventory* in the
  Performance menu, *Stage Mesh LOD* under Export › Prepare. `open_painter.offline_analysis` stays
  searchable.

## Phase 7 notes (implemented)

- Graph sockets say what they carry: `OP_SocketContent` is the material bundle (label *Material*),
  `OP_SocketColor` one colour channel, `OP_SocketScalar` one scalar channel, `OP_SocketNormal` a
  tangent normal, `OP_SocketCoverage` mask coverage; every socket carries a tooltip description.
  `mirror.GRAPH_FORMAT` is part of the tree signature, so trees saved with the previous socket
  types reconcile once; links and positions are kept by the existing reconcile.
- `open_painter.open_graph` (label *Edit Nodes*) opens the graph framed on the selected layer, mask
  or filter (`target_node_id` through the selection resolver) or, with `focus='ALL'`, on the
  whole graph; `open_painter.graph_view` offers *Frame Selected* / *Full Graph* in the node
  header next to the scope path and *Auto Layout* (still explicit and undoable).
- The Material Assistant (`ui/material_director.py`) reads Target → Instruction → Run → Changes;
  the mode is a compact choice with Refine as the default; *What is sent* lists the attachments
  from `session.attachments_for(..., dry=True)` — the same description the request is composed
  from — and the summary of the last request from the payload itself (`session.compose`,
  `session.sharing_summary`); connection settings live in the add-on preferences only.
