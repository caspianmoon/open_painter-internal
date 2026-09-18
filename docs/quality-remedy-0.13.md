# Open Painter 0.13.0 — add-on quality remedy: report

Implementation of `Open_Painter_Addon_Quality_Remedy_Plan.md` on top of 0.12.2
(commit `8a62636`). Status terms: **PASS**, **FAIL**, **NOT RUN**, **LIMITED TO** _scope_.

Candidate package: `dist/open_painter-0.13.0.zip`, sha256
`21e550f54999eb66198103093b8dfc9ea95d92c81fb9ff09e02617468db2bd60` (also recorded in
`.blender-test/quality/environment.json`, `.blender-test/clean-install.json` and
`engine-fixtures/fixtures.json`). Reference machine: Apple M3 Pro (11 cores, 18 GB), macOS 26.6 (Darwin 25.6),
Blender 5.2.1 LTS (`9e2066aef7ef`), Metal backend, Python 3.13.13, NumPy 2.3.4
(`scripts/quality_environment.py` → `.blender-test/quality/environment.json`).

Everything below was run from the source checkout in an isolated Blender profile
(`.blender-test/user`), and the clean-install and upgrade sessions from the packaged ZIP in a
throw-away profile. The user's installed extension and existing project files were not
touched.

## 1. What existed and was reused

- **Vector strokes** (`painting/strokes.py`, `painting/stroke_raster.py`,
  `properties/stroke.py`): the stroke record (samples with pressure, radius, tilt,
  direction; brush and material values per stroke), `record()` with universal linking, the
  disc/depth-band kernel, per-set rasters memoised in the shared raster cache,
  `channel_rows` compositing, the one-texel seam dilation, Vector Strokes masks. All kept;
  the kernel gained one optional parameter (depth limit, default = the old behaviour).
- **Shader session** (`evaluation/shader_graph.py`, `shader_resources.py`,
  `shader_preview.py`): the strokes boundary per layer channel, the resource table with
  pending placeholders and atomic publication, `hold`/`renew_hold`/`release_hold` for
  gestures, undo rehydration. Kept; the boundary became incremental (below).
- **Geometry**: `geometry/snapshots.targets`, `uv_rasterizer.rasterize/tiles`,
  `baking/maps/utility._islands`, `geometry/seams` (`_placement` cache key). The export
  ownership map is the same texel-centre rasterisation and island labelling
  (`compatibility_contract.py` asserts equality with `seams.map_for`).
- **Export**: `export/exporter.py` (evaluation, presets, `pack`, `image_writer.write`),
  `export/surface_normal.combined` (chart-aware height→normal at the output resolution),
  `export/plan.py` (resolved requests, manifests, mesh), `mesh_bundle.py`, the target
  registry and the Export view. Kept; the resampling and publication paths changed.
- **Native painting** (`open_painter.paint`, `paint_session`, freeze-while-painting) and mask
  painting: untouched. The single-channel route is regression-tested in the real window
  (`material_painting_ui.py` "native stroke still paints", clean-install step 9).

## 2. What changed

### Phase 0 — baseline (M0)

`tests/integration/quality_fixtures.py` (channel plate, seam object, difficult atlas,
surface-isolation object, transparency card, 3-set/25-layer production prop),
`quality_baseline.py` (export findings → `.blender-test/quality/baseline-export.json`) and
`quality_baseline_paint_ui.py` (real-window painting findings →
`baseline-painting.json`). Findings on 0.12.2, all reproduced before any fix:

| Finding | Status on 0.12.2 | Evidence |
| --- | --- | --- |
| Export gutters: empty texels hold the layer default | QUALITY GAP | 130 k boundary texels bordering default grey on the seam object |
| 2× mip of an export mixes empty defaults into island edges | CONFIRMED DEFECT | max error 0.35 in 3 062 edge texels |
| Downsample (1024→512) box-filters empty texels into covered edges | CONFIRMED DEFECT | max error 0.22 |
| Replacing an export loses the originals when the 2nd map fails | CONFIRMED DEFECT | `T_Seams_N.png`, `T_Seams_ORM.png` lost |
| Mesh export failure after maps | CONFIRMED DEFECT | maps published, mesh gone |
| A later set failing leaves the earlier set's files | CONFIRMED DEFECT | partial folder left |
| Crash consistency between two replacements | NOT YET VERIFIED (no journal) | — |
| Fast drag leaves holes (event-rate dependent) | CONFIRMED DEFECT | largest gap 1.35 radii at 6 events |
| No material feedback during the gesture | CONFIRMED DEFECT | `material_changed_during_drag: false` |
| Release → visible: 5.7 s with a grey placeholder | CONFIRMED DEFECT | first stroke on a 2K/25-layer set |
| Esc during a gesture commits it | CONFIRMED DEFECT | stroke count 1 → 2 |
| Hits accepted on any set's object; stroke lands on the wrong set | CONFIRMED DEFECT | active set Plate, stroke on Body's object |

### Phase 1 — Paint Material (M1)

User-visible: the paint inspector shows **Paint Material** first (destination line
`Body > Mud Paint | Color, Roughness, Metallic, Height`, the tool button, Size / Opacity /
Flow / Hardness / Spacing, Alpha, Paint/Erase, four channel toggles in a two-column grid,
the values of the enabled channels, **Paint Across Sets** with the sets it may reach listed,
**Pressure Size** / **Pressure Flow**), then the collapsed **Paint Channel Brush** (the
existing native brush) and **Stamps and Stroke Details** (accumulate, projection, **Depth
Limit**, jitter/seed, the other channels, stroke editing). Mask painting is unchanged.

Runtime paths:

- `painting/gesture.py` (new): `resample` walks the screen segment between two pointer
  events at a quarter of the brush spacing, every step is re-projected by a ray cast, and
  `Gesture.place` interpolates each sample onto the exact world spacing between the last
  two re-projections, so coverage does not depend on the event rate (replays at 3, 24 and
  120 events: IoU ≥ 0.9998, largest gap 0.25 radii = the spacing). A miss, an ineligible
  face (unassigned slot, hidden object, another set without Paint Across Sets) or a
  silhouette jump (`JUMP_STEPS`) breaks the segment; returning never bridges it.
- `Scope.of` decides the eligible sets/objects/slots before the first sample: the active set
  by default; with Paint Across Sets every set, and `strokes.record(..., sets=)` links only
  the sets the pointer actually crossed (the raster alone no longer links a neighbour).
- Pressure is explicit: `pressure_size` scales the radius, `pressure_flow` the sample's flow
  column; never both through one value (the double-pressure path of 0.12.2 is gone).
- **Depth Limit** (`OP_Stroke.depth_limit`, default 1.0 for records = the old band;
  brush default 0.5): the surface-following rule is "front-facing texels within
  depth_limit × radius of the stamp plane"; a parallel surface 8 mm away stays clean at
  0.05 and receives paint at 1.0 (`material_painting.py`). Existing strokes keep 1.0.
- Live feedback: `gesture.Preview` binds bounded preview images (512² for four channels,
  1024² for one or two; `preview_size`) to the layer's channel inputs of the live shader
  graph for the gesture's duration, rasterises the new samples with the same kernel, the
  same dilation and the same source-over/erase compositing as the committed raster, and
  publishes all enabled channels from one revision (≤ one publication per 16 ms). On
  release the preview stays bound until the committed leaf publishes (`retire`), then is
  removed after a grace period (never re-bound: reused preview images crashed Blender's
  undo; removed too early they crashed EEVEE's material sync — both reproduced and fixed).
- Incremental strokes boundary (`shader_graph._strokes_boundary`): a leaf records the
  strokes it holds and each stroke's rows (`op_resource_state`); the next identity starts
  from that leaf's pixels (`('base', image)`) and recomputes only the rows of the strokes
  that differ (added, edited or undone), with the stroke rasters memoised in the set's
  shared cache (`stroke_rasterisations: 1` per stroke at 2K), a bigger step budget for
  these interactive leaves (`INTERACTIVE_BUDGET`), a transparent placeholder instead of a
  grey slab, and the paint canvas itself as the fallback while a first leaf publishes.
  Published leaves nothing binds or lists are swept (`sweep_orphans`) so a long session
  keeps a bounded set (53 leaves after 500 strokes, 5 materials).
- Undo/cancel/persistence: one `undo_push` per released gesture; Esc during a gesture
  discards it (the release after it commits nothing), Esc while idle exits; an accelerator
  (Ctrl/Cmd/Alt + key, e.g. save or undo) during a gesture commits the partial stroke once
  and ends it, then the shortcut runs; `save_pre` commits a running gesture and unbinds
  previews so the file never holds a preview image; load/undo/redo/unregister drop the
  operator's handlers, previews and pending work; the geometry signature of the scope is
  checked at release (a changed mesh/transform/mode discards the gesture with a reason);
  `layer_stack.duplicate` gives copies their own stroke key with copies of the strokes.
- `stroke_raster._rasterise` and the preview clip rasterisation to the brush footprint
  (`uv_window`, with a one-texel margin so the seam dilation sees the same coverage; the
  0.12.2 file's rasters are byte-identical, `compatibility_contract.py`), and `channel_rows`
  composites only the rows and columns a stroke reaches.

### Phase 2A — export padding (M2A)

User-visible: **UV Padding** in every set's export settings (Automatic: 4/8/16/32/64 px at
512/1K/2K/4K/8K; Custom px; Fill Empty Space; None) with the effective width next to the
resolution (`UV Padding: 16 output pixels (automatic) at 2048 px`), an upgrade note for
files from before format 3, and the last export's gutter report (islands, tight gutters
with the nearest measured distance, undersized islands, overlaps).

Runtime path (`export/padding.py`, `export/exporter.py`):

1. evaluation at the set's resolution (unchanged);
2. ownership from the evaluated geometry, export UV layer and assigned slots at the source
   and output resolutions (`padding.ownership`, cached by geometry/UV/assignment/resolution
   — a UV edit re-derives it); never from pixels; islands from UV-connected faces;
   overlaps, degenerate triangles and undersized islands counted;
3. coverage-aware resampling (`padding.resample`): a box filter over valid source texels
   when shrinking, a coverage-weighted bilinear fetch when enlarging; tangent normals are
   filtered as decoded vectors and renormalised; empty output texels are marked;
4. the combined height/normal at the output resolution on the chart-aware metric
   (`surface_normal.combined`, before the gutters exist);
5. `nearest_donors`: an exact banded separable transform — vertical nearest per column,
   horizontal window of ±padding pixels, key (d², island id, texel index) so fronts and ties
   resolve deterministically, copies never averages, no wrap across the atlas border;
   Fill Empty Space continues at 8× and 64× coarser levels beyond 64 px; a brute-force
   oracle (`reference_donors`) is what the unit tests hold it to;
6. `extend` copies donor values per channel field, packed maps are packed from padded
   semantic channels, `NORMAL_DX` flips and colour encodes once, in the writer.

Opacity's exterior gutter copies the nearest authored opacity like every other field;
interior transparent texels are never edited (documented policy, separate from colour).

### Phase 2B — export failure safety (M2B)

`export/transaction.py` (new) publishes a whole request: validation (duplicate
destinations, exclusive creation when not overwriting), a journal beside the outputs,
backups by rename, flushed copies renamed into place, rollback on any exception (a rollback
that itself fails keeps the backups and the journal and names the folder), journal recovery
at the start of the next request (`recover`). `plan.execute` stages every set's
maps, manifests and the mesh (exported into staging, never unlinked first) and commits once;
`exporter.export` (single set), the Export to Target button and evaluated bundles go through
the same transaction. Export settings are overlaid (`plan._Overlay`), not edited, during a
target export.

### Phase 3 — engine validation (M3)

`engine_validation/` (README, `build_fixtures.py`, `unreal/import_and_validate.py`,
`unity/Editor/OpenPainterValidation.cs`) and `scripts/engine_validation.py`. The fixtures
for both targets are built by the candidate with hashes and padding reports in
`.blender-test/quality/engine-fixtures/fixtures.json`.

### Phase 4 — product regression (M4)

Clean-install session extended with a Paint Material gesture, the padding line of each
export and a 0.12.2 upgrade session (`clean_install_ui.py` step 18); a project format
migration (3) that flags automatic padding visibly; `compatibility_contract.py`;
`scripts/material_baseline.py`; the 500-stroke session and 4K probes; the runner registers
every new suite and prints SKIPPED for the compatibility check when its 0.12.2 file is
absent.

## 3. What was executed

Commands (from the checkout, `BLENDER_USER_RESOURCES=.blender-test/user`):

```
blender --background --factory-startup --python tests/integration/quality_baseline.py       # M0 (on 0.12.2)
blender --factory-startup --enable-event-simulate --python tests/integration/quality_baseline_paint_ui.py
blender --background ... --python tests/integration/material_painting.py                    # M1 replay (23 checks)
blender --factory-startup --enable-event-simulate --python tests/integration/material_painting_ui.py   # 2K/25 layers
OP_MATERIAL_UI_RESOLUTION=4096 OP_MATERIAL_UI_LAYERS=8 OP_MATERIAL_UI_COMPILE_SECONDS=900 ... material_painting_ui.py
blender --factory-startup --enable-event-simulate --python tests/integration/material_session_ui.py    # 500 strokes
python tests/unit/test_export_padding.py                                                    # 11 pure tests
blender --background ... --python tests/integration/export_padding.py                       # 15 checks
blender --background ... --python tests/integration/export_safety.py                        # 11 checks
python scripts/engine_validation.py                                                          # fixtures + NOT RUN
blender --background ... --python scripts/material_baseline.py -- --src <0.12.2 worktree>/src --output ...-0.12.2.json
blender --background ... --python scripts/material_baseline.py -- --compare ...-0.12.2.json  # 50 identical
blender --background ... --python tests/integration/compatibility_contract.py -- --build <0.12.2 src>; -- --record <0.12.2 src>; (candidate)
python scripts/build_extension.py; python scripts/run_clean_install.py --package dist/open_painter-0.13.0.zip
python scripts/run_tests.py --package dist/open_painter-0.13.0.zip [--gui]                   # canonical runner
```

Canonical runner on the candidate (`scripts/run_tests.py --package dist/open_painter-0.13.0.zip`,
then `--gui-only` and `--gpu-only`): the package validates and installs into the isolated
profile; 125 background integration scripts plus the compatibility contract, the unit
discovery (172 tests) and the pure NumPy suite (118 tests) pass — one existing expectation
was updated for the new export semantics (`artist_file_workflow.py` compares the
downsampled export over fully covered 2×2 blocks; `production_export.py` checks the
upscaled border over covered texels); the 25 real-window probes (including
`material_painting_ui.py` and `quality_baseline_paint_ui.py`), the GUI workflow stroke test
and the GPU/display checks pass. The 500-stroke session runs on request
(`OP_RUN_SESSION=1`; its last run is in `material-session-2048.json`, PASS).

Evidence: `.blender-test/quality/*.json` (baseline-export, baseline-painting,
material-painting, material-painting-ui-2048/-4096, material-session-2048, export-padding,
export-safety, compatibility-contract, material-baseline-0.12.2/-candidate(-comparison),
engine-fixtures/fixtures.json, engine-validation-status.json, environment.json) and
`.blender-test/clean-install.json`; screenshots next to the UI reports.

## 4. Results

### Painting (M1)

| Measurement (2K production prop, 25 layers, live shader) | 0.12.2 | 0.13.0 | Target | Status |
| --- | --- | --- | --- | --- |
| Fast drag, 6 pointer events: samples kept / largest gap | 7 / 1.35 radii | 33 / 0.25 radii (= spacing) | no holes | PASS |
| Material changes under the pointer | no | yes, all four channels, same revision | yes | PASS |
| Event → visible, ordinary drag (40 events), p50 / p95 | — | 67 / 123 ms (event→publish p95 77 ms, publish→draw p95 47 ms) | ≤ 100 ms p95 | LIMITED TO 123 ms p95 (see §5) |
| Event → visible, fast drag (6 events), p50 / p95 | — | 48 / 147 ms | ≤ 100 ms p95 | LIMITED TO 147 ms p95 |
| Release → shader current (settle), ordinary / fast drag | 5.70 s | 0.39 s / 0.47 s | ≤ 250 ms | LIMITED TO ~0.4–0.5 s (see §5) |
| First stroke on a layer without strokes (structural rebuild) | 5.70 s + grey slab | 0.69 s, transparent placeholder, canvas fallback | — | PASS (documented one-time rebuild) |
| Esc during a gesture | commits | discards, tool stays | discard | PASS |
| Ctrl+Z after a stroke | — | removes exactly that stroke | one step | PASS |
| Native single-channel stroke | works | works (settle 0.09 s) | unchanged | PASS |
| 4K stress (8 layers): latency p95 / settle | — | 76–145 ms / 1.4–1.5 s (native route: 1.5 s) | — | LIMITED TO 4K settle ≈ 1.5 s |
| 500-stroke session (2K/25 layers): committed / lost | — | 500 / 0; 20 undo+redo cycles exact; 5 saves (5.8–10.1 s each, 655 MB file) | — | PASS |
| Session settle p50 / p95 / max; event pause p95 / max | — | 0.63 / 1.15 / 5.9 s; 0.11 / 1.2 s | — | reported (worst pauses are saves and undo/redo rebuilds) |
| Session memory: RSS after compile / peak; leaves and previews after 500 strokes | — | 2.9 GB / 4.3 GB; 53 leaves, 0 previews (bounded) | no leak | PASS |

Replay tests (`material_painting.py`, 23 checks, all PASS): four channels share one
footprint (max error 6e-8 vs the predicted composite), a disabled channel is untouched,
black/zero values are painted, replays at 3/24/120 events agree (mean |Δ| ≤ 2e-5, IoU ≥
0.9998), pressure size/flow are explicit, the live preview equals the committed leaf
where it painted (max error 0.0), incremental leaves equal a full recomputation, the
canvas stands in until the first leaf publishes, a seam crossing is continuous (gap 0.25
radii across three islands), a silhouette jump lifts, misses and excluded slots and
hidden objects lift without a bridge or a new linked layer, a parallel surface 8 mm away
stays clean under the depth limit, cross-set painting is explicit (a raster that would
have marked a neighbour does not link it; crossing with the option on does; without it
the stroke lifts at the set's edge), erase reveals the layer below, cancel and a geometry
change commit nothing, save/reopen reproduces the strokes, duplicates edit independently.

### Export (M2A/M2B)

`test_export_padding.py` (11 PASS): the transform equals the brute-force reference on
random layouts, ties, diagonals, narrow gaps, no seeds, full coverage, zero limit, no wrap;
fill mode is complete and deterministic; 4K/32 px in 2.1 s with 121 MiB scratch (target
≤ 384 MiB); 8K/64 px on the pure transform: 640 MiB scratch (bounded, above the 4K target
by design of the 4× larger image).

`export_padding.py` (15 PASS): all four modes at 512 keep the interior byte-exact and copy
donors; 1K/2K/4K exports; the close-packed atlas reports "4 island(s) closer than 8 px
(nearest 1 px): gutters meet, mips may mix them · 1 island under 16 texels · 1681
overlapping texels" instead of hiding it; black stays black; the transparency card's cutout
coverage is unchanged; 2K→512 matches an independent coverage-weighted reference (max
error 6e-8; 964 fractional boundary blocks where the plain box filter differed); upsampling
stays in the valid range; normal angular error p99 0.30°, max 0.34° (8-bit, 2K→1K with
height); URP MetallicGlossMap alpha = 1 − roughness within one code value, and its gutter
is a copy; repeat exports byte-identical; authoring images untouched; a UV edit re-derives
ownership.

`export_safety.py` (12 PASS): failures publishing the second map, the manifest, the mesh;
the mesh exporter failing; a later set failing; a crash between two replacements
recovered from the journal (all nine files restored) and recovered automatically by the
next request; a read-only destination refused before any write; exclusive creation
refused; a file that appeared at a destination after the preflight refused and left intact; the plain single-set export transactional too. Every case: originals byte-identical
(FBX excepted only for its creation timestamp after a *successful* replacement), no
staging/backup/journal files left.

### Engines (M3)

Unreal Engine 5 metallic/roughness: **NOT RUN** (no Unreal Editor on this machine).
Unity URP Lit: **NOT RUN** (no Unity Editor on this machine). Built-in and HDRP: NOT RUN.
Delivered: fixtures for both targets from the candidate with sha256 of every file
(`engine-fixtures/fixtures.json`), the Unreal Python import/validation script and the Unity
C# editor script with the exact texture, normal, packing and mesh import settings they apply
and record, scene definitions and the measurements to take. Nothing was purchased or
deployed.

### Product (M4)

- Clean install of `open_painter-0.13.0.zip` in an empty profile, two sessions, 17 steps
  plus the material stroke with live feedback and the padding line on both exports: PASS.
- Upgrade: a file saved by the 0.12.2 build opens with the installed 0.13.0, migrates
  visibly (format 3, "Set to Automatic when this file was upgraded"), its strokes are intact
  and it exports: PASS.
- Compatibility contract (`compatibility_contract.py`, 6 checks): authored appearance,
  stroke placement, component ownership and export scope identical to what the 0.12.2 code
  produces from the same file; read-only operations and an export leave edit serials,
  stroke revisions and settings alone: PASS.
- Material baseline: all 50 recipes evaluate byte-identically to 0.12.2 on the preview
  cube (`material-baseline-candidate-comparison.json`: 50 identical, 0 different): PASS.
  Visual close-detail review of representative materials: LIMITED TO the numeric identity
  (no pixel changed, so the 0.12.2 review stands); the new brush controls (depth limit,
  pressure size/flow) are covered by replay tests, not by a visual review.
- Small window: the Paint Material controls are one control per row with the channel
  toggles in a two-column grid (`inspector_panels.py` asserts the layout; screenshots
  `material-painting-ui-2048.png`); an enlarged UI scale was not exercised: LIMITED TO
  the default UI scale.
- Runner integrity: `material_painting.py`, `export_padding.py`, `export_safety.py`
  (background), `test_export_padding.py` (pure), `material_painting_ui.py` and
  `quality_baseline_paint_ui.py` (real window), `material_session_ui.py` (opt-in with
  `OP_RUN_SESSION=1`, ~15 min) and `compatibility_contract.py` (skipped explicitly without
  its 0.12.2 file) are in `scripts/run_tests.py`; `quality_baseline.py` is a Phase 0
  evidence script and deliberately not a test (its findings describe 0.12.2).

## 5. What remains limited or unverified

- **Latency and settle targets.** The 100 ms p95 event-to-visible target is met by the
  operator's own work (event→publish p95 77 ms, ~15 ms of it in the gesture; the rest is
  Blender's event delivery behind ~50 ms EEVEE frames of the 25-layer material) but not by
  the measured end-to-end p95 (123 ms ordinary drag, 147 ms fast drag). The 250 ms settle
  target is not met at 2K with four channels: 0.39–0.47 s, of which ~200 ms produce four
  64 MB leaves in one step and ~100–150 ms are the viewport's re-upload of them; one or two
  channels settle faster. At 4K the settle is ~1.5 s for the material and the native route
  alike (256 MB leaves). Classified LIMITED TO these numbers; the blocking path is profiled
  in this report and in `material-painting-ui-*.json` (`ticks`, `preview_profile`).
- **Fill Empty Space beyond 64 px** takes the nearest donor at 8×/64× coarser resolution
  (deterministic, exact up to the coarse cell); the exact band is 64 px.
- **8K padding** is bounded (640 MiB scratch for the transform) but above the 384 MiB
  figure the plan gives for 4K; PNG encoding of 8K maps dominates the export time anyway.
- **Engines**: NOT RUN (above). The import settings in the scripts follow the vendors'
  documentation; they are not proven by an actual import here.
- **Tablet input**: pressure/tilt paths are tested with synthetic attributes; no tablet
  was available. Perspective/orthographic views, zoom changes, a release outside the
  viewport and navigation gestures are exercised by the real-window probe only in the
  top orthographic view of the fixtures.
- **Thin shells / nearby surfaces**: handled by the depth band (front-facing, within
  depth_limit × radius); there is no per-texel visibility test, so a same-facing surface
  closer than the depth limit still receives paint (documented in the brush tooltip and in
  `docs/vector-strokes.md`).
- **Enlarged UI scales** and other platforms were not run (macOS/arm64 only).
- **Undo entanglement**: a preview bound at undo-push time crashed Blender's memfile undo
  when the same image datablock was re-bound later; the fix (fresh preview images per
  gesture, removed after a grace period, never re-bound) is verified by 20 undo/redo cycles
  in the 500-stroke session and by the 2K/4K probes, not by a root-cause fix inside Blender.
- **Sustained-session pauses**: the worst redraw gap of 62 s in the 500-stroke session is a
  save of the 655 MB project plus Blender's own file write, not a painting stall; the worst
  event pause is 1.2 s and the worst settle 5.9 s (once, after an undo/redo rebuild); the
  session's settle p95 (1.15 s) is above the single-stroke probe's (0.4–0.5 s) because the
  strokes there land on top of hundreds of earlier ones and the undo/redo cycles rebuild the
  material.

## 6. Final quality gate (plan §12)

| Gate item | Status | Where |
| --- | --- | --- |
| Paint Material is available through the normal paint workflow | PASS | inspector first section; `inspector_panels.py`, clean-install step 9b |
| All enabled PBR channels share one continuous gesture and coherent visible feedback | PASS | `material_painting.py` (footprint, preview parity), `material_painting_ui.py` |
| Input rate, pressure, flow, spacing and alpha behaviour tested | PASS (alpha: existing raster tests; brush alpha goes through the same kernel call) | `material_painting.py`, `vector_strokes.py`, `stroke_parity.py` |
| Unassigned faces, nearby surfaces and excluded texture sets protected | PASS; nearby same-facing surfaces LIMITED TO the depth band | `material_painting.py` §8–9 |
| One gesture is one undoable change; cancellation and save/export mid-stroke safe | PASS | `material_painting_ui.py` (undo, Esc), `material_session_ui.py`, `vector_brush.py` save rule |
| Existing single-channel and mask painting remain functional | PASS | `material_painting_ui.py`, clean-install step 9, `paint_freeze.py`, `multi_set_paint.py`, `inspector_ui.py` |
| UV padding uses actual geometric coverage and deterministic ownership | PASS | `export/padding.py`, `test_export_padding.py`, `export_padding.py` |
| Downsampling does not contaminate valid boundary texels before padding | PASS | `export_padding.py` (reference comparison, 964 fractional blocks) |
| Normal direction, height contribution, colour encoding, packed scalars and alpha correct | PASS (p99 normal error 0.30°) | `export_padding.py` |
| Tight-gutter limitations reported, no all-mip guarantee | PASS | Export view "gutters meet, mips may mix them"; `export_padding.py` |
| Failed replacement exports preserve or recover textures, manifests and mesh | PASS | `export_safety.py` (12 cases incl. crash journal) |
| Actual Unreal and Unity runs reported separately with configuration and evidence | NOT RUN (no engines here); scripts, fixtures, hashes delivered | `engine_validation/`, `engine-validation-status.json` |
| Clean-install and 0.12.2 upgrade tests pass on the candidate package | PASS | `.blender-test/clean-install.json` |
| Sustained 2K/4K session behaviour and material baselines reviewed | PASS (session, 4K probe); material baseline 50/50 identical | `material-session-2048.json`, `material-painting-ui-4096.json`, `material-baseline-candidate-comparison.json` |
| New tests executed by the canonical runner; skipped checks not counted as passed | PASS (compatibility contract prints SKIPPED without its 0.12.2 file; the session probe is opt-in) | `scripts/run_tests.py` |
