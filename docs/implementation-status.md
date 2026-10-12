# Roadmap implementation status

Version **0.2.7** introduced shader-native authoring. Existing `LIVE` texture sets
compile into persistent Blender material graphs, with parameter-owner bindings
and downstream dependency invalidation. Supported numeric edits update shader
values without complete channel texture generation. Save/render preserve source
images and finish only isolated unsupported inputs; export/CPU Reference remain
explicit raster consumers. The Image Editor composite is an explicit snapshot;
painting uses the original canvas. See the [architecture and diagnostics](shader-preview-architecture.md).
On the supplied 2048 project, the authoring trace, save/reopen/export, and actual
Cavity Dirt strength slider checks have passed. Eight held reversals changed
the real viewport through an isolated native RNA sidebar. Product panel navigation
and a 14 ms frame target are not established by this check.
Earlier small-fixture passes are secondary evidence for individual code paths.

Earlier reports below describe their named release's evaluator and measurements.


## Add-on quality remedy 0.13.0 — 2026-09-19

Report with measurements, evidence paths and limits: [quality-remedy-0.13.md](quality-remedy-0.13.md).

| Item | Status | Evidence |
| --- | --- | --- |
| Baseline fixtures and 0.12.2 findings (export gutters/mips/downsample contamination, publish losses, painting holes, no feedback, 5.7 s settle, Esc commits, wrong target) | done | `tests/integration/quality_fixtures.py`, `quality_baseline.py`, `quality_baseline_paint_ui.py` |
| Paint Material: resampled, re-projected gesture; explicit set/slot scope; pressure size/flow; depth limit; live multichannel preview; Esc discard; one undo step; save mid-stroke rule; duplicates independent | done | `tests/integration/material_painting.py` (23 checks), `material_painting_ui.py`, `clean_install_ui.py` step 9b |
| Incremental strokes boundary, transparent placeholder, canvas fallback, orphan sweep | done | `material_painting.py`, `material_session_ui.py` (53 leaves after 500 strokes) |
| Latency / settle on the 2K/25-layer prop: p95 123–147 ms (target 100), settle 0.39–0.47 s (target 250 ms); 4K settle ≈ 1.5 s | LIMITED TO the measured figures (profiled in the report) | `.blender-test/quality/material-painting-ui-2048.json`, `-4096.json` |
| UV padding: geometry-derived ownership, coverage-aware resampling, deterministic nearest-donor extension with an oracle, channel semantics, gutter diagnostics, Automatic/Custom/Fill/None with a visible migration | done | `tests/unit/test_export_padding.py`, `tests/integration/export_padding.py` |
| Export publication as a request-level transaction with backups, journal recovery and exclusive creation, for textures, target packages and bundles | done | `tests/integration/export_safety.py` |
| Unreal 5 and Unity URP validation | NOT RUN — no engine on the reference machine; scripts, fixtures and hashes delivered | `engine_validation/`, `scripts/engine_validation.py` |
| Clean install and 0.12.2 upgrade on the packaged candidate | done | `scripts/run_clean_install.py` (`.blender-test/clean-install.json`) |
| Compatibility contract (appearance, stroke placement, ownership, export scope, read-only operations) | done | `tests/integration/compatibility_contract.py` |
| Material baseline: 50 recipes byte-identical to 0.12.2 | done | `scripts/material_baseline.py` |
| Sustained session: 500 strokes, 20 undo/redo cycles, 5 saves, bounded leaves | done | `tests/integration/material_session_ui.py` |

## Focused task views and tools in context (UI plan phases 4–8) 0.12.0 — 2026-09-18

| Item | Status | Evidence |
| --- | --- | --- |
| Bake view: Setup / Maps / Results, truthful map states, recommendation without overwriting, previous map kept on failure, queue bound to its set, Stop After This Map, Add Baked Normal and AO Layer | done | `tests/integration/bake_view.py`, `tests/unit/test_bake_queue.py` |
| Export view: one resolved plan for preview and export; explicit scope with no widening; overrides and folders shown and kept; refuses existing files unless allowed; view opens without mutation | done | `tests/integration/export_view.py` |
| Material Aging: Applied To / Construction / Environment / Age first, named sections, relative-intensity sliders with endpoint meanings, modified-preset mark, separate resets, named live toggle | done | `tests/integration/contextual_tools.py`, `material_aging_ui.py` |
| Geometry Effects: applied list with selection-specific inspector, presets behind Add Geometry Effect and on the shelf, explicit bake-back outputs | done | `tests/integration/contextual_tools.py`, `geometry_effects.py`, `geometry_effects_ui.py` |
| Offline analysis relocated to its inputs (contact fields, attribute masks, performance menu, export preparation), commands searchable | done | `tests/integration/contextual_tools.py`, `offline_analysis.py` |
| Nodes: typed sockets (material bundle, colour, scalar, normal, coverage) with tooltips, one-time reconcile of older trees, Edit Nodes framed on the selection, Frame Selected / Full Graph, scope path in the header | done | `tests/integration/nodes_and_assistant.py`, `graph_mirror.py`, `graph_view_ui.py` |
| Material Assistant: Target → Instruction → Run → Changes, compact mode, sharing summary from the composed payload, connection settings in preferences | done | `tests/integration/nodes_and_assistant.py`, `material_director.py`, `material_director_ui.py` |
| One status model, Clean View, narrow-sidebar layout, reasons on disabled actions | done | `tests/integration/ui_release.py` |
| Responsiveness on the reference fixture (24 fill layers + Worn Painted Metal, 5 924 session nodes): selection p95 ≈ 11 ms; warmed control edit p95 ≈ 132 ms (uniform) / ≈ 140 ms (generator amount), callback ≈ 3 ms, the rest EEVEE re-syncing the session tree (≈ 25–30 µs per node); first compile ≈ 12 s | measured | `tests/integration/ui_response_ui.py` (`.blender-test/ui-response.json`) |
| Session graph size: ≈ 170 nodes per fill layer with a generator mask; a 48-layer stack (10 032 nodes) settles in 230–300 ms after a parameter edit and misses the 250 ms target | recorded — evaluator work, not UI (`docs/ui-refactor.md`) | `OP_UI_RESPONSE_LAYERS=48 … ui_response_ui.py` |
| Task-based sessions with five artists | not run — needs people; the automated matrix above is the release evidence | — |
| Master controls for the remaining catalog recipes | not started — authored per recipe on demand | — |

## Everyday editing workflow (UI plan phases 0–3) 0.11.0 — 2026-09-18

| Item | Status | Evidence |
| --- | --- | --- |
| Baseline fixture and recorded reference (digests, structure, CPU channels, exports, older project) | done | `tests/integration/ui_baseline.py`, `tests/fixtures/ui_baseline.json` |
| One editing target by stable id: layers, nodes, inspector, shelf, assistant; three separate channel properties; breadcrumb; deleted-mask fallback; second-window independence | done | `tests/integration/selection_target.py` |
| Readable layer rows, selected-item blend row, Add Layer / Add Mask / Add Effect / More menus, drag placement text | done | `tests/integration/inspector_ui.py`, `layer_drag_ui.py` |
| Selection-specific inspector; authored master controls (Worn Painted Metal, Heavy Chipped Paint, Factory Powder-Coated Metal) bound by instance ids; broken bindings reported | done | `tests/integration/inspector_panels.py`, `inspector_ui.py` |
| Master controls for all fifty recipes (0.12.1): direct and computed bindings, duplication, save/reopen, undo/redo; departures and recipe findings in `docs/master-controls.md` | done — rendered sheets inspected by eye | `tests/integration/master_controls.py`, `master_controls_ui.py`, `scripts/render_master_controls.py` |
| Launch remedy (0.12.2): coverage-gated amounts (0 = none), effective recipes (galvanized oxidation, ash, dents, clearcoat scratches), artist-facing names, labelled Blend Channel, audited workflows, clean-install test | done | `tests/integration/master_controls.py`, `launch_audit_ui.py`, `scripts/run_clean_install.py` (`.blender-test/clean-install.json`) |
| Human usability gate (3–5 Blender artists, unassisted) | not run — needs people | — |
| View Mask / Back to Material as an explicit mask snapshot; Add Paint to Mask with an explicit role | done | `tests/integration/inspector_panels.py`, `inspector_ui.py` |
| Companion inspector in a Properties area (Tool tab), following the screen's session | done (prototype accepted) | `tests/integration/inspector_ui.py` |
| Shelf catalogs, select-then-apply, Assets panel with target and requirements, explicit Replace, geometry presets on the shelf, layer-row context menu | done | `tests/integration/asset_workflow.py`, `asset_categories.py`, `native_ui.py` |
| Catalog tabs enabled by default | not possible — Blender exposes no API; the artist enables them once in the shelf's catalog menu (persisted in the preferences) | `docs/asset-categories.md` |
| Master controls for the remaining 47 catalog recipes | not started — authored per recipe on demand | — |

## Geometry effects follow the material 0.10.1 — 2026-09-17

| Item | Status | Evidence |
| --- | --- | --- |
| Placements from aging outcomes, mesh features, layer masks, painted spots and the whole surface; facing along the coating-loss edge | done | `tests/integration/geometry_sources.py` |
| Surface colour per piece, the artist's own prototypes, follow-the-material regeneration (manual, all, after aging) | done | `tests/integration/geometry_sources.py` |
| Aging document: shared delamination pattern, hairline cracks in remaining film, deposits on range-matched grunges | done | `tests/unit/test_aging_model.py`, `scripts/render_material_geometry.py` |
| Smart materials and shelf presets set the aging construction and, on an unaged set, the depicted history; textiles and leather in the taxonomy | done | `tests/unit/test_aging_model.py`, `tests/integration/material_aging.py` |
| Placement by scene context (neighbouring objects casting shade, real ground plane) | not covered — shade and the ground are the set's own faces | — |

## Material aging 0.10.0 — 2026-09-17

| Item | Status | Evidence |
| --- | --- | --- |
| Material taxonomy with per-material response and process subscriptions; coatings; environment, usage and maintenance presets as physical parameters | done | `tests/unit/test_aging_model.py` |
| Monthly history simulation (segments, dated events, seed), labels, readout, art direction after the physics | done | `tests/unit/test_aging_model.py` |
| Aging folder built by role and re-driven nondestructively; overrides; Live; randomize; roster rebuild; missing-map guard | done | `tests/integration/material_aging.py` |
| Age as a keyframable parameter; document, duplicate and undo portability | done | `tests/integration/material_aging.py` |
| Age Response removed (fields kept hidden for older files) | done | `tests/integration/material_aging.py` |
| Age exposed as an Open Graph node input | not started (graph/ is Codex-owned) | — |
| Geometry placement: convexity, occlusion, upward/downward faces, streak flow, ground proximity (Position); optional maps degrade to procedural bases and join on Rebuild | done | `tests/integration/material_aging.py` |
| Drainage, water collection and sun exposure as dedicated fields | not covered — placement uses AO, curvature, world normal and position | — |

## Decal objects 0.9.6 — 2026-09-16

| Item | Status | Evidence |
| --- | --- | --- |
| Decal Empties driving fill layers: move/rotate/scale/duplicate, depth, facing, softness, tangent-frame normals, native shader path, live sync, graph node transforms, portable re-link | done (shader parity 0.0 / 6e-8) | `tests/integration/decal_objects.py` |
| Wrapping around strongly curved geometry beyond the planar projection depth; mirrored-island bitangent sign | not covered | — |

## Physical scale awareness 0.9.5 — 2026-09-16

| Item | Status | Evidence |
| --- | --- | --- |
| Density per set, Physical units on channels/masks/effects, sync on every relevant change, lock/unlock, import re-normalization | done | `tests/integration/physical_scale.py` |
| Per-island density, edge-wear widths and micro-detail radii in metres | not covered | — |

## Seamless surface filters 0.9.4 — 2026-09-16

| Item | Status | Evidence |
| --- | --- | --- |
| Seam map (cross-seam correspondence with corner fill, per-island padding, tangent Jacobians), Space switch on every neighbourhood effect | done — blur error at seams 0.18 (UV) → 0.013 (Surface) on a six-island cube, exact on a plane | `tests/integration/surface_filters.py` |
| Directional blur/warp along a world direction | done | `surface_filters.py` |
| Shader isolated inputs and mask filters on the seam-aware path | done (boundary equals the CPU result) | `surface_filters.py` |
| Generator-internal blurs (micro details) in Surface space; non-manifold seams | not covered | — |

## Mask architecture 0.9.3 — 2026-09-16

| Item | Status | Evidence |
| --- | --- | --- |
| Mask groups (nested stacks) in the CPU reference and the shader compiler; structure operators | done (exact CPU model; shader parity 1e-4) | `tests/integration/mask_groups.py` |
| Custom Graph source: native instance in the shader, Cycles bake for the CPU reference, content signature | done | `mask_groups.py` |
| Open Graph editing of group membership; portable custom graphs | not covered (groups show as ordinary mask nodes; graphs refused by the serializer) | — |

## Mesh masks 0.9.2 — 2026-09-16

| Item | Status | Evidence |
| --- | --- | --- |
| Mesh Mask source with nine selector kinds, exact UV rasterization, signature-keyed CPU cache and shader boundary | done (shader/CPU parity 0.0 off the edge band) | `tests/integration/mesh_masks.py` |
| Live updates through the depsgraph (collection membership, materials, weights, attributes) | done | `mesh_masks.py` (simulated depsgraph update re-evaluates the session) |
| Graph node selector, portable re-linking by name | done | `mesh_masks.py` |
| Object-mode face selection without a depsgraph update; per-object island picking by click | not covered | — |

## Bake Doctor 0.9.1 — 2026-09-16

| Item | Status | Evidence |
| --- | --- | --- |
| Bake Check: nine checks with element/object selection, pre-bake gate (errors stop, warnings pass), full tier with ray coverage | done, 8–14 ms pre-bake tier on the fixtures | `tests/integration/bake_doctor.py` |
| Correction brush: Projection Distance / Skew / Direction as an owned per-part cage; Ignore High Poly and AO Exclusion as composited passes; provenance marks maps stale | done (plate appears only where painted; region rebake restores baseline) | `bake_corrections.py` |
| Live region rebake from the scheduler after a real brush stroke; corrected-cage overlay | done | `bake_correction_ui.py` (1305 texels rebaked in ~1 s at 512) |
| Margin-vs-island-gap and anti-aliasing checks; per-texel (rather than per-vertex) cage distances | not covered | — |

## Layer performance 0.9.0 — 2026-09-16

| Item | Status | Evidence |
| --- | --- | --- |
| Freeze while painting: zero boundary regenerations during a stroke, one catch-up per touched owner on release; multi-set, save, undo, backend switch, session end | done | `tests/integration/paint_freeze.py`, `paint_freeze_ui.py` (real brush drag: 30 frozen ticks, 0 requests, 1 resume, p95 tick 0.6 ms) |
| Bypass in Preview: pass-through units, no content/mask units, shader parity with the layer hidden (0.0), export and snapshots unchanged, folder semantics, scoped rebuild, rehydration | done | `layer_bypass.py`, `shader_structural_fields.py` |
| Layer caches: Cached/Frozen, parity with the live compile (3e-6 layer, 2e-5 folder), stale detection, debounced refresh, explicit Refresh, paused while painting, persistence across undo and reopen, resolution change, passthrough refusal, lifecycle hygiene | done | `layer_cache.py` |
| Profile Layers: compile/boundary/CPU columns, badges, states, Clear | done | `layer_profile.py` |
| Benchmark (2048, 14 layers, 60 emulated inputs) | stroke tick p50 0.97 ms / p95 6.5 ms and 0 regenerations with the gate on, versus 17.3 / 28.7 ms and 60 regenerations off; heavy folder 260 ms · 2098 nodes live, 110 ms · 288 nodes cached (refresh 12.3 s, cooperative), 184 nodes bypassed | [layer-performance-0.9.0.json](performance/layer-performance-0.9.0.json) |
| Open Graph badges for bypass/cache; anchors into a cached folder with a backdrop | deferred | — |

Figures come from emulated strokes and a bounded CPU pass; no GPU frame time is measured
(Blender does not expose per-node shader execution). See [layer-performance.md](layer-performance.md).

## Material → Geometry 0.7.0 — 2026-09-15

| Item | Status | Evidence |
| --- | --- | --- |
| Height/mask → displaced owned copy; seam rule; source untouched; limits; regenerate/apply/replace | done, 2.5e-3 on a gradient | `tests/integration/geometry_displace.py` |
| Mask → curves (thinning, tracing, simplification, projection) → stitches / seam bead / weld | done | `curve_geometry.py`, `tests/unit/test_curve_extraction.py` |
| Bolt, scale, rock, bark prototypes and materials | done | `bake_back.py` (bolts), manager KINDS |
| Bake back into NORMAL/AO with restored settings and provenance | done | `bake_back.py` |
| Render gallery of the new prototypes | deferred | — |

## Vector strokes and universal canvas 0.6.0 — 2026-09-15

| Item | Status | Evidence |
| --- | --- | --- |
| Stroke kernel (disc, depth band, facing, pressure, flow/max union, alpha rake, planar) | done | `tests/unit/test_stroke_raster.py` |
| Universal canvas: one stroke rendered by linked layers of every set it reaches; seam crossing | done | `tests/integration/vector_strokes.py` |
| Edits, erase, delete, resolution independence (IoU > .95 at 512 vs 1024), mesh re-projection, undo, reload | done | `vector_strokes.py` |
| Shader boundary parity, re-identification, rehydration; Vector Strokes mask | done, 2.3/1000 | `stroke_parity.py` |
| Vector Brush modal with overlay; real-window drag records 25 samples | done | `vector_brush_ui.py` |
| Native multi-set session (material paint mode, canvas per set, union of slots, both sets evaluated) | done (0.6.1) | `tests/integration/multi_set_paint.py`, `multi_set_paint_ui.py` |
| Precise per-sample occlusion, live stamps while dragging, UDIM tile files | deferred | plan |

## Target renderer 0.5.1 — 2026-09-15

| Item | Status | Evidence |
| --- | --- | --- |
| Registry of engine conventions, naming, mesh options, manifest | done | `tests/unit/test_targets.py` |
| Engine Preview adapters (quantisation, DX round trip, Z reconstruction, smoothness, AO indirect, view transform), numeric-only toggling | done, 2/255 | `tests/integration/engine_preview.py` |
| Export → Target with restored settings, mesh export, refusal to overwrite | done | `tests/integration/export_target.py` |
| Packed-texture snapshot preview (BC block compression emulation) | deferred | plan |

## Live geometry-aware fields 0.5.0 — 2026-09-15

| Item | Status | Evidence |
| --- | --- | --- |
| Cooperative field jobs (debounce, budget, atomic publish, supersede, edit-mode defer, discard when current) | done | `tests/integration/live_fields.py`, `live_fields_ui.py` (republish 0.33 s after a move, no synchronous analysis) |
| Geometry Nodes batched proximity/raycast with BVH fallback | done, matches reference to 1e-4 | `live_fields.py` |
| Kinds: convexity, concavity, edge exposure (live), ground, world height, proximity; scene scope, exclusions, cap, per-object radius, falloff | done | `live_fields.py`, `live_edge_exposure.py` |
| Live edges for Edge Wear/Chips on CPU and shader; bevel follows; structural/numeric split | done, parity 0.004 | `live_edge_exposure.py` |
| MAX/MIN combine, presets | done | `live_fields.py` (falloff), presets operator |
| Auto edge rebake (opt-in, mesh edits only) | done | `auto_edge_rebake.py` |
| Live curvature semantics vs the baked kernel | documented difference (per-vertex, 25th-percentile baseline) | docs/scene-fields.md |

## Material modules 0.4.1 / 0.4.2 — 2026-09-15

| Item | Status | Evidence |
| --- | --- | --- |
| Module documents (format 4), subtree save with stable ids, namespaced instances, outside references surviving updates, v1–v3 readers | done | `tests/integration/module_documents.py` |
| Three-way update: applied, kept, conflict-reported, painted images local, replace, rollback, equals a fresh insert with the same overrides | done | `tests/integration/module_update.py`, `tests/unit/test_module_merge.py` |
| Catalog recipes as module instances; catalog modules (Damage) nested with overrides; catalog updates preserving nested instances | done, pixels unchanged | `tests/integration/catalog_modules.py` |
| Graph detail frame (per-mask/effect nodes for the active layer) | deferred to a later release | plan |

## Open Graph and published outputs 0.4.0 — 2026-09-15

First release of the `gamechanger.md` programme (pillar 1: a live node view with real
branching). Evidence per item:

| Item | Status | Evidence |
| --- | --- | --- |
| Layer output / folder composite anchors, every channel selectable, in root, isolated and passthrough contexts | done; CPU and shader agree to 1.5e-8 | `tests/integration/output_anchors.py`, `shader_output_anchors.py`, `tests/unit/test_output_graph.py` |
| Cycle rules derived from composition nodes (output feeds layers above only; own output; composite from inside a folder) | done | `test_output_graph.py`, `output_anchors.py` |
| Scoped rebuilds when publish flags toggle; undo rehydration with composite imports | done | `shader_output_anchors.py` |
| Graph view: projection, incremental reconcile, positions kept, undo | done | `tests/integration/graph_mirror.py` |
| Link edits → anchor fields; refusal keeps the stack; replacement; legacy links | done | `tests/integration/graph_link_edits.py` |
| Real window: split area, node click selects the layer, list selection follows, dragged link becomes a reference | done | `tests/integration/graph_view_ui.py` → [open_graph.png](open_graph.png) |
| Cross-texture-set references | deferred (UV spaces differ; needs the reprojection of the universal canvas) | plan |

## Performance release 0.3.0 — 2026-09-15

Measured on Blender 5.2.1 LTS, macOS Apple Silicon; every figure has a script and a
JSON record. Numerical behaviour is unchanged (output hashes and frozen-reference
suites), except float rounding of barycentric interpolation in four bake maps.

| Area | Before (0.2.9) | After | Evidence |
| --- | --- | --- | --- |
| Shader build, Worn Painted Metal / Post-Apocalyptic / 2K project | 4.6 s / 16.1 s / 5.1 s | 0.41 s / 0.70 s / 0.64 s | `tests/performance/shader_build_scaling.py`, [shader-build-0.2.9.json](performance/shader-build-0.2.9.json), `shader_user_project.py` |
| Hide layer / blend-mode change / add mask (73-unit material) | full rebuild + recompile (16 s) | 2.8 ms same graph / 80 ms, 4 units / 127 ms, 1 unit | `tests/performance/shader_structural_edits.py` |
| Undo to a current shader | full rebuild + recompile | 30 ms, same instance | `tests/integration/shader_undo.py` |
| Slider callback median (2K project, 7 controls) | 1.45–1.61 ms | 0.41–0.73 ms | `tests/performance/shader_user_project.py` |
| Idle scheduler (real window) | 58.8 ticks/s, 7.0 ms CPU/s | 9.8 ticks/s, 1.6 ms CPU/s | `tests/performance/idle_timer.py`, [idle-timer-0.3.0.json](performance/idle-timer-0.3.0.json) |
| Mesh-map bakes on the 2K project | POSITION 2.15 s … UV_BORDER 9.65 s, MEAN_CURVATURE 28.7 s | 0.79 s … 1.78 s, 11.5 s | `tests/performance/bake_maps.py`, [bake-maps-0.3.0.json](performance/bake-maps-0.3.0.json) |
| CPU reference cold worn/2K (paired medians) | 16.03 s | 14.50 s | `scripts/benchmark_comparison.py`, [paired-cpu-0.3.0.json](performance/paired-cpu-0.3.0.json) |

Implemented and tested in Blender: per-unit shader compilation with adoption on
structural edits, uniform visibility/enable flags, undo rehydration, numeric
fast-path callbacks, 10 Hz idle scheduler, panel draw caches, batched UV
rasterisation for geometry and mesh-map bakes, vectorised islands/creases/curvature
kernel, buffer-reusing noise and filter kernels. Not done: library node groups for
repeated blend/effect arithmetic (the heavy catalog material already builds under
one second, so the further gain was not needed), deferred boundary rasters for
hidden layers, and a compiled CPU path (the CPU reference is now bound by real
NumPy work on 16-row strips).

Displayed-frame latency is now measured end to end on the shader-native path
(`tests/performance/shader_native_frame.py`, real window, 3x3 framebuffer fence on the
ordinary VIEW_3D draw; [shader-native-frame-0.3.0.json](performance/shader-native-frame-0.3.0.json)):
redraw alone 1.9 ms; slider edit 32 ms to the first fenced frame; hide 32 ms and show
99 ms to the settled frame; a blend-mode change 7 ms to the first frame and 211 ms to the
settled frame (about 0.9 s when EEVEE still has to compile the new shader variant); undo
33 ms. These include Blender's event loop and the 2K Worn material on the default cube;
they are not presentation or input latency.

### Plan closure — `Open_Painter_Updated_Codex_Plan.md`

All work packages M0–M12 of the plan are implemented with the limits recorded above.
Of its remaining acceptance items, three were pursued in 0.3.0: the displayed-frame
measurement (above), lifecycle regressions for undo/redo with a job in flight, full and
linked scene copies (`tests/integration/shader_lifecycle_edges.py`) and a second window
(`tests/integration/two_windows_ui.py`), and the M3 interactive targets, which the
shader-native path supersedes (a numeric edit binds in under a millisecond; the CPU
reference is the explicit secondary path and its warm-edit target is retired). Not
pursued, deliberately: UDIM as a product feature (Blender's `Image.pixels` reaches only
tile 1001), the separate GPU compute backend (redundant with the shader-native path),
and continuous multi-channel strokes. The plan is closed as a backlog; new work is
tracked here.

See [shader-preview-architecture.md](shader-preview-architecture.md#units-and-rebuild-scope)
for the compiler structure and the [changelog](../CHANGELOG.md#030).

## Anchor points — 2026-09-15

Substance-style anchor points: an *Anchor Point* row in a mask stack publishes
the coverage before it, an *Anchor* row in a channel's filter stack publishes
the channel after the filters above it. Published Input masks, fill channel
content and generator Micro Height / Micro Normal inputs reference them; anchors
list their consumers and consumers locate their anchor. References are checked
per stack item (any acyclic reference is allowed, cycles are refused when picked)
and a missing anchor evaluates as neutral with a warning instead of aborting.
CPU and shader-native evaluation agree on ten new parity cases (max error
2.1e-6); the shader memoizes every stack prefix so consumers share the owner's
nodes. Smart-material format 3 remaps anchor ids on import and duplication.
[Workflow, semantics and evidence](anchor-points.md).

## Geometry effects reworked — 2026-09-14

The M8 helpers instanced placeholder shapes (a box, a cone, an octahedron, a
pentagon, two quads) in one size with the painted surface's material. They now
instance real prototypes with variants, per-instance rotation/scale/lean, spacing
and slope bias, owned procedural materials, CC0 Poly Haven `moss_01` scanned
clump cards for moss, and a sunk, rippled weld bead. Adding an edge-driven
material, effect or smart mask also rebakes a crease-only Curvature map so edge
wear cannot silently be empty. Before/after renders and provenance:
[geometry-effects.md](geometry-effects.md).

## Damaged Bevel effects — 2026-09-14

The supplied "Damaged Variable Bevel" script depends on the Cycles-only Bevel and
AO shader nodes. Its edge amount and rounded normal are now `BEVEL_MASK` and
`BEVEL_NORMAL` mesh maps baked through the owned Cycles staging scene, and two
effects, **Damaged Bevel** and **Rounded Edges**, build the same result from layer
data, so EEVEE renders it (11.9 % of pixels changed, Cycles agreement 0.98).
Recipes gained baked and optional mask sources and normal-map layers.
[Mapping, evidence and limits](damaged-bevel.md).

## Curvature V2 — 2026-09-14

The crease-only curvature map left every mesh-aware mask empty on smooth geometry
(0.018 % of texels above 0.01 on the supplied 39k-face shell), which is why materials
looked flatly applied. `CURVATURE` now combines crease lines with a texel-domain
kernel estimate (turning over area within Curvature Radius, relative to the surface
median), giving edge wear on bevels and tessellated rims after a rebake; old maps show
as stale. Before/after renders and measurements: [node-stack-integration.md](node-stack-integration.md).

## Node-stack add-on integration — 2026-09-14

Implemented from the study, outside the shader compiler and smart-material files:
signed mean-curvature and selection utility maps with Reselect Baked Faces, UDIM tile
enumeration in Inspect Bake Setup, an explicit AO Distance through an owned staging
World, and opt-in 2× supersampled Cycles bakes with a byte preflight. Details, tests and
measurements: [node-stack-integration.md](node-stack-integration.md). Section 7 of the
study measures the 0.2.7 shader compiler (quadratic flat-tree construction, full
rebuild on layer visibility, 64 fragment samplers on Metal) against the node-stack add-on's
per-layer and library group structure (46 s → 2.5 s → 0.17 s for 3,000 nodes) and
lists compiler recommendations that were not applied here.

## Node-stack add-on study — 2026-09-14

Research only, no code changes. [What Open Painter can take from the node-stack add-on (3.0.0)](research/node-stack-study.md)
compares the GPL-3.0-or-later node-stack add-on source (shader-node layer stack) with the CPU evaluator and lists ranked,
source-cited candidates: a signed mean-curvature map to port (M5), UDIM tile enumeration and the file-based tile
workflow (M11, consistent with the tile-access gate), bevel/SSAA/AO bake options (M5), per-channel mask participation,
a Previous-Layers adjustment layer and non-destructive freezes (M7), a solo viewer (M3), Empty-driven decals (M10), a
data-image colour-space policy, and mask-edge transition effects. Node plumbing, tile-file renaming, atlases, the
network updater and scene-mutating bake bookkeeping are explicitly not to be taken. Each recommendation names the
Open Painter files it touches and the Blender checks required before committing.


## Generated masks and surface relief — 2026-09-14

Version **0.2.6** reauthors all 50 smart materials using 13 additional generated
wear and relief inputs. Image coverage replaces procedural scratch ticks and
striped coating fragments. Raised paint/primer levels, pitted corrosion,
grain/weave relief, weld beads, blisters and recessed rubber fissures remain
editable. Exported normals include the rendered Height relief and respect
connected UV charts, geometry scale and separately authored normals.

See the [current 50-material review](material-surface-review.html),
[implementation and validation](material-surface-quality.md), and
[exact generated source record](research/material-relief-source-index.md).
The 0.2.5 report below records the earlier release, not this replacement.

## Generated material quality — 2026-09-14

Version **0.2.5** upgrades all **50 existing smart materials** using 15 generated
scalar images across 76 existing layers. All 229 layer records, native UI,
mesh-aware masks, fixed display material and portable v1/v2 compatibility remain.
Generated inputs are shared, packed and sampled as float32 scalar data; native
color, roughness and height remain editable. Existing saved material instances
are preserved. All 50 library thumbnails were rendered again from actual output.

Fresh validation passed 55 background integration scripts, 49 unit tests, five
real-window GPU suites and four native GUI workflows. The new sources and
portable/shared-image paths have dedicated regressions. Full 2K CPU composition
was measured at 36.566 s cold / 0.272 s warm roughness versus 30.887 / 0.244 s for
the preserved procedural recipe. Warm source/mask generation and geometry work
are zero; these numbers are CPU authority, not interactive frame latency.
The generated images are 1254px sources and are not claimed as measured 2K PBR
scans or universally seamless assets.

[Implementation, exact commands, measurements and limits](generated-material-quality.md),
[actual render gallery](generated-material-gallery.html), and
[source provenance](../src/open_painter/assets/generated/provenance.json).

---

## Licensed library sourcing — 2026-09-14

Research completed on `task/licensed-library-research`, preserving 0.2.4 commit
`25368daac46f6df9885e74deca692b10f6686390`. Checked publisher licenses specifically
for redistribution inside a **paid** add-on/library. Curated 18 CC0 candidates
across PBR surfaces, mask inputs and procedural source graphs; distinguished
source resale from permission to sell renders and documented excluded licenses.

Downloaded seven sample files from four assets, recorded SHA-256, verified four
publisher checksums and ZIP integrity, inspected supplied pixel formats and
viewed representative images. No new native smart material or runtime quality
claim follows from those source checks. No Blender tests or performance
measurements were run for this research-only task; the installed 0.2.4 release
is unchanged. The [research, concrete assets and licensing evidence](research/licensed-library-sourcing.md),
[candidate manifest](research/licensed-library-candidates.json) and
[sample record](research/licensed-library-sample-verification.json) document
integration requirements and actual verification.

---

## Bake queue hotfix — 2026-09-14

Version **0.2.4**, on `task/bake-queue-timer-fix`, preserves base
`02c88157681ea532a6a692fb35e3b5b4111775ba`. The UI bake queue attempted to access
`Event.timer`, which does not exist in the installed Blender 5.2.1 LTS API.
A real-window regression reproduced the exact exception before any map bake.
The queue now advances using its owned timer's duration, ignoring unrelated
TIMER events until that timer ticks. Cleanup also handles failed initialization
and generator-close failures.

Native-event tests now complete real utility maps, preserve completed maps on
Esc or a later failure, and release the queue without changing artist geometry,
materials, selection or scene settings. This changes neither map algorithms nor
authored resolution. The native UI, GPU preview, CPU evaluator, materials and
portable formats remain intact. Each individual map bake is still synchronous;
this fixes the queue crash and does not claim a baking speedup. No performance
benchmark was rerun for this hotfix.

Fresh validation passed: 52 background integration scripts, 46 unit tests, four
native GUI workflows and isolated archive validation/install.
[Exact commands, before/after evidence and limits](bake-queue-fix.md).

---

## Live latency follow-up — 2026-09-14

Implemented incrementally on `task/live-preview-latency`, based on
`df32483a158fb3852564654c6d10f5f2ff7673d4`. The new version is **0.2.3**.
The CPU reference, native UI, fixed display material, all existing recipes and
both portable material versions remain. Supported scalar fields and mesh-aware
masks generate GPU textures directly on Metal; other inputs retain CPU
semantics. Two independent 256 MiB caches bound source and completed first-root
folder reuse. Isolated coverage and passthrough behavior remain distinct.

Fresh Worn Painted Metal **2K float32** measurements with the **production
update timer and first completed native frame**: cold 24.237 s → 0.808 s;
mask strength 4.630 s → 76.45 ms; shared procedural scale 2.860 s → 59.64 ms;
folder opacity 90.99 → 42.02 ms. Roughness did not improve: 25.00 → 28.66 ms.
These are one-machine measurements, not a 60 FPS guarantee. Authored quality
was not reduced. Older offscreen timings below complete all viewport samples
and must not be interpreted as the first native interactive frame.

The [implementation and measurement report](live-preview-latency.md),
[primary-source research](live-preview-latency-research.md) and
[validation inventory](performance/latency-validation.json) contain exact
commands, results, hashes, changed files and limitations. Full 2K comparison
of all eight channels passed within **2.861023e-6** maximum absolute error.
Current automated coverage: 52 background integration scripts, 42 Blender unit
tests, 37 independent CPU tests, 183 GPU compositing checks, 91 checkpoint
checks, 38 scalar configurations, 11 full-2K mask cases plus 17 preflight checks,
three native UI workflows and the live painting/save/export workflow.
Save/render/export still finish the latest complete recipe through CPU; these
operations and unsupported live source/filter domains can remain slow.

---

## Live procedural preview follow-up — 2026-09-13

Implemented on `task/live-procedural-preview`, preserving shared-checkout commit
`00ccf773bae22ee5f82cf787de4c7dc0ca5bca9e` and the earlier bake integration. This
follows the user's later authorization for a shader-like procedural backend.
The fixed display material, native UI, CPU correctness reference, existing
materials and version-1/version-2 material compatibility remain intact.

The new backend composes cached, dependency-keyed texture inputs on the GPU.
Unchanged channels are rejected by a pure signature pass before any texture
work. Constant and opacity edits update shader parameters; complex procedural
and filtered inputs keep exact CPU implementations. GPU-only full-precision
display images include updated mipmaps. Native float painting can reuse the
existing GPU canvas. Save, render and export flush the latest authoritative CPU
textures; no settled-edit timer forces a CPU bake during live authoring.

Actual Worn Painted Metal 2K completed-frame measurement: warm median
**296.89 ms CPU → 138.14 ms live GPU (2.15×)**. Add-on preparation/submission
was **200.90 ms → 5.97 ms**, with zero managed image transfers on warm GPU
roughness edits. These are separate timings; 5.97 ms is not displayed-frame
latency. Initial preparation still takes seconds and 60 FPS is not established.

The [implementation, commands, measured context and limits](live-procedural-preview.md)
and [raw completed-frame report](performance/live-viewport-2k.json) document this
follow-up. GPU mathematical parity, dependency/lifecycle regressions, native UI,
authoritative export and isolated packaging passed. Actual final acceptance:
51 background integration scripts, 32 Blender unit tests, 27 NumPy tests, three
native GUI workflows, 125 GPU math checks and the live workflow suite. All eight
channels of the Worn Painted Metal 2K fixture matched the CPU reference within
`1.193e-7` maximum absolute error. The
[validation inventory](performance/live-preview-validation-inventory.json)
records exact commands, results, changed files and source digest. The
historical milestone measurements and disabled GPU experiment below describe
the preceding implementation, not the new production live compositor.

---

## Bake integration follow-up — 2026-09-13

Bake workflow improvements: explicit/name source matching, isolated render-configured
staging, assembly/part AO scopes, editable cages, evaluated topology validation and
independent extrusion/device controls. Original materials, nodes, images, scene settings
and transforms are preserved on success and failure. No claim of a new bake engine or
bake speedup is made.

The roadmap implementation and Worn Painted Metal timings below describe the
preceding `0f44580` continuation. They were not remeasured for this bake change.

---

This continuation implements work across M0–M12 on
`task/m0-m1-evaluation-correctness`. It preserves the newer main changes and
shared-checkout commit `98a18e8ef0a90d3143d26445168c0cc981cb9ecd` (`latest updates`).
It does **not** certify every acceptance criterion as complete. In particular,
the UDIM gate failed, the GPU experiment remains disabled, and interactive
whole-material latency is not established.

The CPU float32 evaluator remains the correctness reference. Existing materials,
native UI, fixed-size display material, native single-channel painting, and
version-1/version-2 material readers remain. New format fields are optional and
material files remain data-only. Preview resolution is explicitly selected;
authored images and save/export outputs retain their resolution and precision.

## Implemented work and acceptance boundaries

| Milestone | Implemented | Evidence and remaining limits |
| --- | --- | --- |
| M0 | Fresh-process performance harness, phase/transfer/cache/RSS instrumentation, capability probes, isolated package install and regression runners. Compute-slice distributions added. | Paired performance context and exact commands below. RSS includes Blender, not just evaluator allocations. No physical tablet or displayed-frame latency measurement. |
| M1 | Scene-scoped scheduler identity, failed-revision parking, stale-publication guards, immutable parameter snapshots, dependency revisions, geometry reuse. Registration now rolls back partial initialization and supports repeated registration/unregistration. | Existing lifecycle tests plus immutable jobs and injected registration failure. Arbitrary external image writes still need detection/Refresh; unknown modifier dependency graphs remain conservative. |
| M2 | Global disposable-cache allowance, scalar float32 working buffers, finite-halo strip evaluation, immutable decoded image reuse across a revision, retained active-channel output for regional edits without evicting source caches. | CPU parity and cache tests. Mandatory images, some full-frame filter temporaries and all process allocations are not a hard project-memory cap. No general buffer pool or subrectangle Blender Image upload. |
| M3 | Explicit 512/1024 interactive proxy option, independent proxy cache and image ownership, adaptive cooperative compute batching, active-channel/fair scheduling, settled final evaluation, authoritative save/render flush. Deleted-set proxies are released. | Proxy/save and existing native paint tests. Individual full-frame operations and image transfers can still exceed the compute budget. No claim of universal real-time behavior or 2× cold speedup. |
| M4 | Disabled-by-default CPU/GPU image-chain contract: fill, masked over, Levels and separable Gaussian blur; context-scoped resources and transfer/readback timing. Unsupported operations/parameters reject. | Actual Metal float32 parity experiment; headless CPU fallback. Isolated-chain speed does not establish a whole-material benefit. No production GPU routing or shader-stack rewrite. |
| M5 | Shared evaluated geometry, scene-specific dependency graph, baked-map provenance/staleness, scale/UV/cage/degenerate diagnostics, map-boundary cancellable bake queue. 2026-09-14: exact UV overlap analysis across all objects of a set, stacked/mirrored classification per polygon, sampled cage-ray coverage, Inspect Bake Setup and Select Problem Faces in the bake panel ([bake diagnostics](bake-diagnostics.md)). | Evaluated projection, utility-map and diagnostics tests; 126k evaluated faces inspect in 4.7 s; 20k sampled cage rays against a 126k-face source take 0.78 s and are reported as an estimate with the missed faces. A single Cycles map remains blocking. Arbitrary modifier-input provenance is not complete; face selection needs matching base/evaluated face counts. |
| M6 | Explicit object/collection scene fields: orientation, position, distance, contact, exposure, heat/moisture/soot, named attributes and vertex groups. Bounded analysis resolution; manual/live/frozen modes; last-good retention and frozen portable serialization. | Panel/bolt field fixture verifies moving/deleting influencers, material-edit reuse and frozen round-trip. Fields are artistic scalar analyses, not fluid/thermal/physical simulation. |
| M7 | Stable layer/mask references, pre/post-filter stages, dependency traversal and cycle rejection, published packed masks, duplicate/import remapping. | Reference consumers, cycles, edits, group/set duplication and save/import tests. Folder channel-composite references reject explicitly. Some image edits invalidate conservatively; published snapshots require explicit republishing. |
| M8 | Published-mask-driven rust flakes/moss instances, deterministic area-weighted placement, Geometry Nodes ownership, count/triangle limits, regenerate/freeze/remove, editable curve weld geometry and logical coating-depth masks. | Actual evaluated instances, source preservation, transforms, ownership and limits tested. Geometry is optional and bounded. No volumetric substrate cutting or automatic physical damage. |
| M9 | Explicit semantic layer tags, versioned environment profiles, Clean/Used/Damaged controls, deterministic variations, local opacity overrides, optional state serialization. | Three separate texture sets reproduce coordinated but distinct states; paint layers remain unchanged. Existing materials are not silently retagged; shared outputs cannot vary independently. |
| M10 | Separate one-click Material Stamp with coordinated Base Color/Roughness/Metallic/Height, shared coverage, UV seam mapping, slot/backface/occlusion restrictions, pressure, packed immutable source snapshots and native undo. Existing fill projection supports editable decal transforms. | Seam/multi-slot CPU fixture and real Blender undo/redo. Continuous multi-channel strokes, full brush modifiers and dedicated scratch-curve fields are not complete. Packing/copying a stamp is not claimed fast continuous painting. |
| M11 | Godot/glTF/Unity URP/HDRP profiles, independent packed channels, evaluated mesh/texture/manifest bundle, optional realized geometry, texel-density measurement, new-UV staged atlas, failure cleanup. | Export-byte, bundle, atlas preservation and rollback tests. UDIM gate rerun 2026-09-14 ([udim-tile-access.md](udim-tile-access.md)): `Image.pixels` and `gpu.texture.from_image` reach only tile 1001, but per-tile files through a `<UDIM>` path and native painting onto tile 1002 both pass. Two-tile paint/bake/save/export acceptance is still NOT run and no UDIM UI is advertised. Atlas uses grid cells, not a production packing optimizer. |
| M12 | Bounded offline pair contact history with broad-phase bounds and topology checks, rest-strain calculation, explicit cut-face attributes, snow/splinter/peel prototypes under M8 limits, three-way inheritance data preview, declarative recipe API, resource inventory and staged mesh LOD. | Offline/registration and geometry fixtures. Contact proximity/origin travel is not tangential friction. Persistent parent-version inheritance workflow, physical fatigue, true cross-sections and AI reference fitting are not complete. These packages remain optional/experimental individually, as specified by the plan. |

## Validation

Final validation results and measurements are recorded below after execution.
All Blender commands use isolated user resources. The artist profile, original
working scene and published extension ZIP are not replaced. The candidate is
`/private/tmp/open-painter-milestones.zip`.

The historical M0/M1 and earlier CPU optimization reports are preserved in
[implementation-status-through-e22e9a8.md](implementation-status-through-e22e9a8.md).
Their timings are historical, not measurements of this continuation.

Final-source acceptance on Blender 5.2.1 LTS `9e2066aef7ef`, Apple M3 Pro,
18 GiB, NumPy 2.3.4:

- 48 background integration scripts and 32 Blender unit tests: PASS.
- Extension manifest validation and actual candidate ZIP installation: PASS.
- 27 bpy-independent tests with Blender's bundled Python/NumPy: PASS.
- Three existing native GUI scripts: PASS (elements, T/N, asset selection,
  2D/3D, channel/filter controls, actual native stroke and save).
- Additional material-stamp GUI test: actual undo and redo PASS.
- Final logs contain no Python traceback or RNA registration error. An earlier
  operator PointerProperty registration error was found in log review and fixed
  using native string-backed object search; a regression asserts its RNA type.
- Compilation and `git diff --check`: PASS.

GUI correctness tests overlapped background acceptance; their stroke timings are
not used as performance evidence. The paired benchmark runs separately after
all Blender test processes stop. No remote CI, Windows/Linux GPU, 4K/8K or
physical-tablet/display-latency result is claimed.

Exact commands from the repository root:

```sh
python3 scripts/build_extension.py --output /private/tmp/open-painter-milestones.zip
python3 scripts/run_tests.py --blender /Applications/Blender.app/Contents/MacOS/Blender --package /private/tmp/open-painter-milestones.zip
python3 scripts/run_tests.py --blender /Applications/Blender.app/Contents/MacOS/Blender --gui-only
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 scripts/run_pure_tests.py
BLENDER_USER_RESOURCES=/private/tmp/op-stamp-final-user /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python tests/integration/material_stamp_ui.py
BLENDER_USER_RESOURCES=/private/tmp/op-gpu-final-user /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python tests/integration/gpu_parity.py
BLENDER_USER_RESOURCES=/private/tmp/op-udim-probe-user /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python scripts/probe_udim.py
python3 scripts/benchmark_comparison.py --baseline-root /private/tmp/op-m0-baseline-control --report /private/tmp/op-all-milestones-paired.json
python3 -m compileall -q src/open_painter scripts tests
git diff --check
```

## Capability measurements

[Metal result](performance/milestones-gpu-metal.json): one 512² RGBA32F
fill/over/Levels/Gaussian chain, max absolute CPU difference `2.68221e-7`.
CPU end-to-end 0.041857 s; process-first GPU execution 0.034739 s;
warm GPU execution 0.015778 s; 16 MiB GPU image resources.
Shader construction took 0.002038 s separately. Driver shader caches were not
cleared, so this is not an uncached shader compilation claim. The report repeats
the one-time construction cost in its warm metadata; it is not paid again.
The experiment includes source Image reads, upload, readback and final Image
write, but not a production material graph or displayed frame. It remains
**disabled by default**, with CPU fallback in headless mode.

[UDIM probe](performance/milestones-udim-probe.json): writing different colors
after switching `tiles.active_index` returned the same green pixels for both
1001 and 1002 through `Image.pixels`. This API path failed independent tile
addressing. Native tile painting and baking were therefore **not run**. This
is a failed capability gate, not a claim that every possible UDIM access method
is impossible. A different tile-storage/transfer path and complete two-tile
acceptance are required before advertising support. The 2026-09-14 rerun in
[udim-tile-access.md](udim-tile-access.md) found that path: per-tile files.

## Fresh paired 2K CPU benchmark

[Raw paired results](performance/milestones-paired-control.json) contain all six
fresh-process commands, timestamps, source hashes, phase counters, transfers,
cache observations, compute-slice distributions and peak process RSS. Three
alternating pairs compare the reconstructed instrumented `15ef25e` baseline with
this continuation. This is not a comparison to the intervening `e22e9a8` release.

Worn Painted Metal: six layer records, eight 2048² float32 final outputs, the
committed synthetic 32² AO/curvature fixture, no assigned mesh, no bake or viewport
time, full authored resolution, no GPU backend and no concurrent Blender test
workload. Three runs support medians, not across-run latency percentiles.

| Median wall seconds | Reconstructed baseline | Current continuation |
| --- | ---: | ---: |
| Cold full texture set | 23.2503 | 19.8988 |
| Warm roughness constant edit | 2.0663 | 0.2078 |

Warm edits are 9.94× faster; cold evaluation is 14.4% faster, **not** the proposed
2× cold target. Warm edits regenerate zero procedural sources, masks or geometry
and read zero image bytes, while still publishing a full 64 MiB output. Cold
publication still transfers all eight 64 MiB outputs.

Peak process RSS increased: baseline 864–874 MiB; current 934–945 MiB. This
includes Blender and setup. Retaining an active-channel CPU image to avoid later
readback contributes working memory; the cache allowance is not an RSS cap.
No overall memory reduction is claimed.

Cold compute-slice p95 ranges 25.7–28.3 ms across these runs; the largest observed
indivisible computation was 238.3 ms. Adaptive batching cannot interrupt an
individual NumPy/full-frame operation. These counters exclude event-loop wait
and displayed-frame latency. The proposed interactive latency gate is therefore
not established, despite the warm constant-edit improvement.

The [validation inventory](performance/milestones-validation.json) records exact
commands, results, changed files and the tested source-tree SHA256. Production
source was unchanged between the final suite and paired measurements. Commit
`98a18e8` was preserved; subsequent fixes and this report are in the following
task-branch commit. No main reset or user-profile installation occurred.

See [milestone workflows](milestone-workflows.md) for entry points to the new
controls. The table above is the authoritative remaining-work list; passing the
current regression suite is not a certification of every roadmap feature.
