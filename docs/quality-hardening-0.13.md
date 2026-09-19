# Open Painter 0.13.0 — hardening report

Implements `Open_Painter_0.13_Next_Step_Hardening_Plan.md` on the 0.13.0 candidate: export
publication and recovery (H1), the brush bound to its displayed target with per-stroke
permission and a safe default surface reach (H2), complete Fill Empty Space (H3), a real
uninterrupted painting session (H4) and the engine kit (H5). Statuses use the plan's four words
only: PASS, FAIL, NOT RUN, LIMITED TO.

## 1. Baseline, candidate, environment

| | |
| --- | --- |
| Baseline (audited commit) | `02c360c` — "0.13.0: quality remedy …", branch `task/open-graph-0.4.0`, package `dist/open_painter-0.13.0.zip` sha256 `21e550f5…2bd60` |
| Candidate | the working tree on `02c360c` (uncommitted, per the plan); package `dist/open_painter-0.13.0-hardening.zip` sha256 `0c94986e…969120` (full: `0c94986eb382a43e55e05d64e6af9b3be85c7ce5cfbf8b8c828a5d98da969120`; 127,367,263 bytes; `blender --command extension validate` OK), built by `scripts/build_extension.py --output …`; the manifest version stays 0.13.0, no release administration |
| Blender | 5.2.1 LTS, hash 9e2066aef7ef, macOS arm64 build 2026-08-25 |
| Machine | Apple M3 Pro (11 cores), 18 GB, macOS 26.6 (Darwin 25.6.0), Metal backend (Apple M3 Pro, Metal 1.2) |
| Display | 1512×787 pt window, resolution scale 2.0 (3024×1574 px), UI scale 1.0 |
| Python / NumPy | 3.13.13 (bundled) / 2.3.4 |
| Tablet | none attached: every pointer event is synthetic (`event_simulate`) |

Which code a test imports: the pure suites (`tests/unit/*`, `scripts/run_pure_tests.py`), the
background suites and the real-window probes import the checkout (`src/`); `scripts/run_tests.py
--package …` first validates and installs the ZIP into the isolated profile
(`tests/integration/install_extension.py`) and `scripts/run_clean_install.py` runs the two
clean-install sessions from the ZIP with the checkout off the path. Baseline reproductions
ran in a detached worktree of `02c360c` (`git worktree add --detach /tmp/op-baseline-02c360c
02c360c`) with its own isolated profile (`.blender-test/user-baseline`).

Changed files: `src/open_painter/export/{transaction,plan,exporter,padding,mesh_bundle}.py`,
`painting/{gesture,strokes,stroke_raster,surface_reach}.py`, `properties/stroke.py`,
`ui/{vector_brush,strokes_panel,properties_panel,export_view,target_panel}.py`,
`evaluation/{shader_graph,shader_resources,cache,dependencies,mask_eval}.py`,
`geometry/snapshots.py`, `core/{layer_stack,texture_set_manager}.py`,
`smart_materials/{serializer,loader}.py`; tests `tests/unit/{test_export_transaction,
test_surface_reach,test_export_padding}.py`, `tests/integration/{material_painting,
material_target_ui,material_session_ui,export_safety,export_safety_worker,export_padding,
quality_fixtures}.py`; `scripts/{hardening_reproductions,hardening_reproductions_blender,
run_tests,run_pure_tests}.py`; docs and changelog.

## 2. Before/after reproductions (H0)

All four audit reproductions were reconstructed against the checkout's real modules
(`scripts/hardening_reproductions.py`, disposable directories, no asserts, concrete bytes) and run
on the baseline before any fix; the two painting findings were reproduced in Blender
(`scripts/hardening_reproductions_blender.py` in background Blender, and the real modal operator
in `tests/integration/material_target_ui.py` with `OP_MATERIAL_TARGET_REPRODUCE=1`). Evidence:
`.blender-test/quality/hardening/`.

| Finding | Before (baseline `02c360c`) | After (candidate) |
| --- | --- | --- |
| A1 cleanup failure after a backup was removed (A, B existing; C new; second backup unlink fails) | FAIL — mixed outputs after recovery (`before.json`: commit raised the injected OSError, recovery preserved one new file, restored one old, dropped one) | PASS — commit reports `cleanup_pending`, every destination holds the new bytes, `recover()` finishes the cleanup (`after-h1.json`, `after-h3.json`) |
| A1 competing writer after the final existence check, `overwrite=False` | FAIL — the foreign file was overwritten, no error | PASS — `PublicationConflict: A.png was created by another writer while this export ran; nothing was replaced`, foreign bytes intact |
| A1 unreadable journal | FAIL — silently discarded (`recover()` returned nothing, journal gone) | PASS — `RecoveryRequired: … cannot be read (Expecting value …); it and any op-backup-* files … are kept`; journal and backup kept |
| A2 Fill on a sparse atlas (8-square corner island) | FAIL — unresolved empty texels 256²: 4096, 512²: 32768, 1024²: 172032 (128²: 0) | PASS — 0 unresolved at 128/256/512/1024, every donor a valid original texel |
| C1 target change while the brush stays alive (real operator; invoke on A1, stroke, select A2, stroke) | FAIL — displayed `A > A2`, both strokes recorded on A1 (`material-target-ui-baseline.json`: strokes_on_A1 2, A2 0) | PASS — A1 1, A2 1, displayed `A > A2`, same operator instance (`material-target-ui.json`) |
| C2 single-set stroke after a cross-set link (disc spills onto the linked set) | FAIL — the linked set's BASE_COLOR digest changed (`painting-baseline.json`: f9abff17… → 1f86f667…; GUI: B_changed true) | PASS — digest unchanged f9abff17… while the disc still reaches the set (`painting-after-h2.json`; GUI: disc_reaches_B true, B_changed false) |
| C3 same-facing rim 8 mm below a painted plate, brush defaults | FAIL — 889 rim texels painted (`painting-baseline.json`) | PASS — 0 rim texels painted with the default Surface reach (`painting-after-h2.json`) |

C4 (sustained session, engines) is addressed in §6 and §7.

## 3. Export transaction contract (H1)

`export/transaction.py` (rewritten). One request = one transaction over one or more folders.

- **State** (journal `.open_painter_export_journal.json` per folder, format 2, written with
  temp-file + fsync + rename + directory fsync): `PREPARING → PREPARED → PUBLISHING → COMMITTED →
  CLEANUP_COMPLETE`. A multi-folder request names one *coordinator* folder; the decision lives in
  its journal and every participant references it, so all folders share one outcome.
- **Durable decision**: everything before the COMMITTED record (staging copies, backups, partial
  publication) is undone by recovery; everything after it is completed (backups removed, journals
  cleared). The decision is one atomic file replacement.
- **No-clobber publication**: a staged file is published with `os.link(staging, destination)` and,
  where the filesystem refuses links (errno EPERM/ENOTSUP/EOPNOTSUPP/EMLINK/ENOSYS), with
  exclusive create (`O_CREAT|O_EXCL`) plus copy; both fail atomically when the destination appeared
  meanwhile → `PublicationConflict`, nothing replaced. With `overwrite=True` the original is backed
  up (`.op-backup-<id>`) and replaced by rename.
- **Locks**: a pid-owned `.open_painter_export.lock` per folder; a live owner refuses a second
  export (`ExportBusy`), a dead owner's lock is broken; recovery refuses a folder whose
  transaction is still running.
- **Recovery input safety**: a journal that cannot be parsed, has the wrong format, references
  a missing coordinator or describes files inconsistently is never acted on: `RecoveryRequired`
  names it and every backup stays. Recovery is idempotent (interrupted and restarted).
- **Outcome** (`Outcome(destinations, cleanup_pending, message)`) is surfaced by the exporter,
  the Export view, the target panel and mesh bundles.

Filesystem scope actually tested: APFS on the local SSD (hard links supported) and a temporary
directory; the exclusive-create fallback is exercised by forcing the link path to fail in the
unit suite; SMB/NFS/FAT were NOT RUN.

Fault matrix (all executing production control flow):

- `tests/unit/test_export_transaction.py` — 22 tests: commit/cleanup, no-overwrite refusal, the exclusive-create fallback when hard links are refused (bytes and a competing file),
  cleanup failure keeps every committed output, hard process termination (`os._exit(3)` in a
  subprocess running the real commit) at 8 failpoints (after PREPARED, after the first backup,
  after all backups, after the first publish, before/after the commit decision, after the first
  backup removed, during recovery) with restoration or completion asserted on file bytes, two
  folders → one outcome from either folder, competing file at publish / during recovery / after
  an interrupted publish, invalid journals kept and reported, missing coordinator, a second
  process refused while one publishes, recovery refusing a running transaction, failed
  restoration keeps backups and journal. PASS.
- `tests/integration/export_safety.py` — 18 checks through the real exporter and target export,
  including 5 hard terminations of a background Blender worker (`export_safety_worker.py`)
  at the same failpoints with coherent recovery, post-commit cleanup failure keeping the
  complete export, the folder lock refusing an overlapping export, a file appearing after the
  preflight not being overwritten. PASS.

## 4. Painting: target capture, per-stroke scope, default surface reach (H2)

**Target capture** (`ui/vector_brush.py`, `painting/gesture.py`). The operator no longer builds a
gesture at invocation: at every pointer-down it resolves the selection through
`ui.selection.resolve` (`material_destination`), captures that gesture's set/layer or Vector
Strokes mask, the participating objects/slots (`Scope.of`), the enabled channels and every
brush value (`brush_snapshot`, a frozen namespace the preview, re-projection and record read),
the projection and the geometry revision. While idle the destination label follows the
selection; while painting it names the captured target (`Gesture.describe`) or the reason
nothing is paintable. During a gesture every event re-checks (`Gesture.invalidated`): a
selection moved to another destination, a hidden layer or ancestor, a deleted layer or mask, a
re-keyed mask, or a geometry change cancels the gesture and restores its preview with the
reason; the next pointer-down starts over. Geometry validity reads the existing dependency
revisions — per-mesh and per-object depsgraph epochs (`dependencies.mesh_epoch`, new
`object_epoch`), modifier values, transform, mode, visibility and the sets' slot assignments —
never a hash of the dense mesh per event. A re-typed or replaced editor ends the tool. Save and
Escape keep their documented behaviour.

**Per-stroke scope** (`properties/stroke.py`, `painting/strokes.py`). Version 1 metadata:
`scope` (comma-separated texture-set ids) and `scope_version` (0 = legacy). `record()` decides
the scope once: the own set, plus — for a cross-set layer stroke — every set the gesture reached
that already renders the key or that the raster marks now (which links it); a stroke recorded
without cross-set painting names its own set only. `strokes_for(project, key, ts)` filters by
permission and is what every consumer uses: CPU channel rows and mask rows, the live shader
strokes boundary (identity, dirty rows, parts), mask identities, the CPU export, the strokes
list, rasterise-to-pixels and the "Also on / Not permitted on" labels. Scope is part of every
cache identity through the filtered `stroke_identity`; edits to the field bump the revision.
Legacy strokes (`scope_version` 0) render wherever their key is shared, unchanged. Remapping:
layer duplication copies only the strokes the set may render, scoped to that set; set
duplication re-keys its layers' and masks' strokes (previously the copy shared the source's keys)
and scopes the copies to the copy; smart-material documents serialise only the strokes the saved
set may render and the loader scopes the imports to the importing set, validating anchors and
samples before mutation. Erase strokes follow the same rules.

**Default surface reach** (`painting/surface_reach.py`; `reach` on brush and stroke). Each sample
stores an *anchor*: the exact hit point and the evaluated polygon the ray hit (`Gesture.hit`).
With `reach == 'SURFACE'` (the brush default) a stamp may paint only the triangles reached
from its anchor polygon by walking shared edges onto neighbours that face the stamp axis
(dot > 0.05 with the geometric normal) and lie inside the stamp's box — over the whole mesh
of the object (all slots, so a slot seam is not a fold) — then the kernel's own facing, radial
and depth-band tests apply. Anchors are validated against every target first
(point-to-triangle distance ≤ 1e-3 × radius); an anchor no target holds (edited or renumbered
mesh) seeds from the nearest facing triangle within the band on the nearest target only; a
sample that finds no surface paints nothing on that target. The rule reads the geometry
snapshot only, so the accepted surface is identical after the camera moves, after reopening
and at every resolution; nothing per stroke is stored beyond 16 bytes per sample. Strokes
recorded before the field (`reach` default `DEPTH`) keep their placement; *Depth Band* is the
explicit broader reach. Known reach (LIMITED TO): a surface that continues smoothly under
something else (an open floor running under a lip) is one surface and is painted along; a
smoothly ramped tunnel is not blocked; smooth-shaded texels whose triangle turns away are
excluded at creases on coarse meshes.

Regressions (all PASS on the candidate):

- `tests/unit/test_surface_reach.py` (13): edge adjacency, point-triangle distance, rear
  sheet 8 mm behind excluded while the kernel alone would paint it, two objects 2 mm apart,
  a wall stopping the walk and a gentle ramp passing, a thin closed box, planar axis, lost
  anchors seeding the nearest target only, chunk windows, the kernel honouring eligibility,
  anchor packing.
- `tests/integration/material_painting.py` (31 checks; replaces the always-true assertion and
  the depth-band expectation): the rear sheet stays clean in Surface reach with a 10 cm band
  and receives paint in Depth Band; cross-set link permits only the crossed sets; a single-set
  stroke after the link leaves the linked set byte-identical in the CPU evaluation, the shader
  identity, a full recompute at 1024 and the export (with widening controls proving the checks
  see the difference); erase likewise; the symmetric case from the linked set; an occluded
  recess (shelf fixture) clean vs. painted in Depth Band; two nearby same-facing objects;
  legacy strokes keep placement and render everywhere; save/reopen; duplicated layer and set
  scoped and independent; imported strokes scoped to the importing set.
- `tests/integration/material_target_ui.py` (real window, one running operator, 8 scenarios):
  target change between gestures, set switch through the shared selection, target change
  during pointer-down (pending stroke discarded, previews unbound, committed work unchanged),
  settings change during pointer-down (captured values recorded, next gesture uses the new),
  hidden layer/folder refused (the label reads "The target layer is hidden (or lies under a
  hidden folder)."), a deleted target never receives a stroke (the shared selection falls back
  to the next layer and the label says so before the click, so the display and the actual
  destination still agree), UV edit and slot reassignment during pointer-down discard the
  gesture, cross-set link then disabled → B's pixels identical while the disc still reaches B,
  Esc while idle exits and a re-typed editor ends the tool without a stroke.

Four further defects found by the session probe (H4) and fixed belong here too:

- published stroke leaves re-read by an undo, redo or file load lose their pixels and were
  regenerated blank by the first draw while still flagged ready — a blank leaf then showed as
  current and seeded incremental updates (`shader_resources.retire_unloaded_leaves`, run in the
  undo/redo/load handlers before any draw);
- after an undo the retired leaf a node still binds used to be *replaced*: a fresh leaf was
  produced, the node moved to it and the old datablock was removed — and that removal, in
  any form tried (immediately, 2 s later, 30 s later, or only its pixels dropped), crashed
  the draw engine in the long session (`MaterialModule::end_sync → BKE_image_acquire_ibuf`,
  `material_pass_get → image_get_gpu_texture`; five reproductions), while removing
  never-rebound orphans as 0.13.0 does never did. The fresh leaf is now produced *into the
  datablock the node binds* (`Resources.request(..., reuse=)`), so nothing is swapped or
  removed under a material that may still sample it; unbound retired leaves stay as
  datablock headers until reopen. Datablocks nothing ever rebinds are removed as before;
- erased texels kept a colour under alpha 0, so incremental leaves differed from a full
  recompute in transparent texels (`channel_rows` and the preview zero them; incremental = full
  bit for bit);
- a stroke's brush alpha image was not an authored input of the save: a generated alpha
  reloaded blank (every stroke painted through it rendered differently after reopening), and a
  straight-alpha float alpha reloaded premultiplied. The save now packs brush alphas (generated
  float ones as channel-packed full-precision data) and `alpha_pixels` decodes them like every
  other image input;
- smart-material documents lost the alpha mode of their image assets, so a channel-packed
  brush alpha came back as a premultiplied EXR and read as all-ones; the document records the
  alpha mode and the loader restores it (assets shared by content are reused only with the
  same mode; older documents keep Blender's default).

## 5. Fill Empty Space (H3)

`export/padding.py`: exact banded EDT within `FILL_BAND = 64` px of every island, then a
hierarchical propagation (coarse step 8, occupied cells map their own representative texel,
band widened to `ceil(nc·√2)+1` at each level until one band spans the grid) that terminates on
any atlas. Contract: every empty texel receives a valid original donor or `donors_valid` fails and
the exporter raises (never a silent partial fill); declared error bound beyond the exact band
2·√2·c texels; deterministic tie-break (distance², island, index). Finite padding, resampling,
channel semantics and interior values are untouched (`test_export_padding.py` 16 tests incl. the
sparse corner islands at 128–1024, hierarchy edge cases, the error bound and a bounded-time
check at 2K — "fill 2K sparse: 1.0 s"; `export_padding.py` 16 checks incl. "fill empty space is
complete per channel" for BLENDER/UNREAL/UNITY_URP presets). PASS.

## 6. Sustained session (H4)

`tests/integration/material_session_ui.py` (rewritten) drives one running Paint Material
operator on the 2K production prop (three sets, 25 layers, live material preview on all three)
with a seeded, repeatable workload: short corrections, long drags at 8/16/33 ms event pacing,
erase and alpha strokes, Esc cancellations, layer and set changes through the shared selection
without restarting the tool, cross-set painting toggled on for crossing strokes and off again,
undo/redo cycles (exact row digests at the hundreds), and periodic saves. The next gesture
starts 0–80 ms after the previous release; only every 50th gesture waits for the session to
settle (a *settled probe*). All events are synthetic; tablet behaviour is NOT RUN. Every knob
(`OP_SESSION_STROKES`, `…_UNDO_EVERY`, `…_SET_EVERY`, `…_FRESH_AT`, `…_SINGLE_SET`, …) is
overridable, so one harness produces both the runs below and the crash reproductions in §6's
sidebar.

The measurements below are the **500-gesture endurance run** (`material-session-2048.json`:
three sets with cross-set strokes, undo/redo every 100, a save every 100, no mid-session layer
added). The **100-gesture full-feature run** (`material-session-probe.json`: undo/redo every
25, a late paint layer created and painted at 60 %, cross-set toggled) covers the plan's heavier
undo cadence and its mid-session layer; both pass, and both are honest about the same pixel
evidence. The one workload that does **not** complete — 500 gestures with undo every 25 *and* a
mid-session layer — hits the pre-existing memfile-undo crash dissected in the sidebar; it is a
FAIL, reported as one, not smoothed over.

**What the artist sees** is measured as pixel evidence, not a timing proxy: for every 5th
gesture (and every first stroke on a layer, and every cross-set stroke) the square of
48 px around the pointer is re-rendered by the viewport engine into an offscreen with
the view's own matrices and GPU textures, before the press and after every event, and the
first render in which ≥ 6 pixels differ by more than 2/255 from the pre-press render is the
visible change (polled between events at ≈ 30 ms per probe, so the figure carries that
cadence). The tool's guide line is not drawn during the session so only the material counts.
The publication→redraw proxy is reported beside it.

| Measure (2K/25 layers, 500 gestures) | Result |
| --- | --- |
| Gestures committed / cancelled on purpose / lost | 476 committed / 24 cancelled on purpose / 0 lost |
| Strokes on a layer other than the displayed one; pixels changed on sets the gesture never reached | 0 strokes on the wrong layer; 0 sets changed outside a stroke’s scope |
| Input → visible material change, pixel evidence, warm strokes (p50 / p95 / p99 / max) | 156 / 441 / 655 / 1626 ms (n=85) |
| … early session (first 100 committed) / late session (last 100) | 115 / 754 / 781 / 788 ms (n=21) / 347 / 736 / 1448 / 1626 ms (n=16) |
| … first stroke on a fresh layer (no live preview until its leaf publishes) | 754 / 784 / 787 / 788 ms (n=3, first stroke on each pre-existing layer; a mid-session layer is the 100-gesture run: 2189 ms) |
| … per event pacing 8 / 16 / 33 ms | 443 / 598 / 374 ms p95 at 8 / 16 / 33 ms pacing (n=27 / 32 / 29) |
| … watched gestures with no visible change (reported, not dropped) | 9 of 97 watched gestures showed no pixel change within the probe window (reported, not dropped): 10, 185, 195, 210, 245, 250, 425, 460, 480 |
| Input → first preview publication (all committed gestures) | 116 / 374 / 432 / 453 ms (n=467) |
| Input → first redraw after the first publication (the timing proxy; all committed gestures) | 206 / 488 / 597 / 746 ms (n=467) |
| Release → current (settled probes: warm / all) | warm 725 / 984 / 985 / 986 ms (n=8); all 725 / 984 / 985 / 986 ms (n=10) |
| Burst settle: last release of a burst → current | last release of a burst → current: 294–986 ms across 10 bursts (median ≈ 730 ms) |
| Worst pause inside a gesture (event handling) | 146 / 396 / 464 / 878 ms (n=500) |
| Undo / redo (per cycle, incl. the leaf rebuild until current) | undo 6.9 / 16.2 / 17.3 / 17.5 s; redo 8.0 / 13.6 / 14.3 / 14.5 s (n=5 each; a full-recompute path, not interactive) |
| Save (copy) / final save / reopen / export (2K body, 8 files) | save copy 4.8–8.6 s; final save 18.9 s; reopen 20.7 s; export (2K body, 8 files) 162.8 s |
| RSS after compile / at 50 gestures / at 500 / peak (the test process, its own recomputations included) | compile 4429 / at 50 4429 / at 500 5086 / peak 7141 MB (peak during export) |
| Leaves / preview images at the end; retired-blank leaves per undo | 190 leaves (93 ready) / 0 preview images; 5–8 bufferless leaves retired per undo |
| Final agreement: every painted layer × enabled channel, shader leaf vs. fresh full recomputation (max abs error), leaf identity = committed strokes | 16 painted layer×channel pairs, max abs error 1.19e-07, leaf identity = committed strokes on every one |
| Reopen of the final save: stroke count, CPU outputs of the three sets vs. the session's | 476 / 476 strokes; the three sets’ CPU outputs equal the session’s to 0.0 on every channel |
| Preview images in the saved file / bound to layers after reopen | 0 preview images saved in the file; 0 bound to layers after reopen |

Quality targets kept visible (plan §8): input-to-visible p95 ≤ 100 ms and warm release-to-current
≤ 250 ms at 2K. Neither target is met at 2K on the 25-layer production prop on this hardware: pixel-evidence input-to-visible p95 is 441 ms (redraw proxy p95 488 ms; first-publication p95 374 ms) against the 100 ms target, and warm release-to-current settle p95 is 984 ms against the 250 ms target. This is the documented performance floor of the live 25-layer 2K material (the 0.13.0 quality remedy recorded ≈120–150 ms p95 and ≈0.4–0.5 s settle at 2K with 4 channels); the targets hold only at lower layer counts or resolution. Reported LIMITED TO this fixture and machine.

GPU memory is not measurable from the add-on (NOT RUN). The 4K stress fixture (8 layers) does
not compile on Metal in the session harness (EEVEE: "fragment function uses too many samplers
(17)" for the 8-layer body material; the 25-layer material compiles) — the 4K session is NOT
RUN; the single-stroke 4K probe of 0.13.0 (`material-painting-ui-4096.json`) stands as the 4K
evidence.

### A memfile-undo crash class the session found (one instance fixed, one open)

The long runs exposed a crash class in Blender's **partial memfile undo**, not in the hardening
diff: undoing across a *timer-deferred* structural rebuild of the live shader graph leaves the
material's persisted graph pointing at datablocks the undo has freed. The dangling pointer is
created by Blender's own undo, before any add-on handler runs (confirmed by reading the raw
`node.node_tree` pointer in an `undo_post` handler placed before ours), and it surfaces as one of
several native back-traces depending on what next touches the material — a depsgraph node-tree
build, an EEVEE `image_get_gpu_texture`, an RNA read, or `subtype_dealloc` while the graph walks
its images. Undo matches datablocks by session id, so renaming does not help, and a *kept*
datablock (the material, or the top group tree) that points at a *replaced/freed* one is remapped
incorrectly.

**Instance 1 — a stroke's source unit — is fixed.** The first stroke on a layer adds a source
unit to the graph; that rebuild was deferred to the next `shader_preview.tick`, i.e. after the
stroke operator's `ed.undo_push`, so a following Ctrl+Z freed the new node trees and left the
material's Procedural Graph node dangling. `ui/vector_brush.OP_OT_vector_brush._end` now builds
that structural change synchronously (`shader_preview.ensure`) *before* `ed.undo_push` when the
gesture is the first source on its target (`gesture.Gesture.new_source`), so the pushed step
captures a consistent graph and undo reverts between two consistent graphs — never freeing a
datablock a surviving node still points at. Non-structural strokes are unchanged. This is
deterministic to reproduce on the pre-fix tree and to confirm on the fixed tree:
`OP_SESSION_STROKES=40 OP_SESSION_SET_EVERY=20 OP_SESSION_UNDO_EVERY=20` crashed at gesture 19
before the fix (switch set, first stroke on it, undo) and runs to completion after it; the six
stroke/undo/shader suites (`vector_strokes`, `stroke_parity`, `multi_set_paint`, `shader_undo`,
`shader_lifecycle_edges`, `shader_adoption`) pass.

**Instance 2 — leaves re-created by the post-undo relearn — is open.** After every undo,
`cache._relearn` marks all texture sets dirty; the next tick rebuilds them, allocating fresh leaf
image datablocks *after* that undo's push. A subsequent undo frees those images while the
persisted graph's texture nodes still reference them, and the next `shader_preview.ensure` →
`Graph.rehydrate` → `unique_images` walks a freed image and crashes (`crash-fresh-layer-300.txt`).
It reproduces around gesture 300 of the 500-gesture multi-set workload with undo every 25, at the
point the harness adds a late paint layer and the tick rehydrates. It cannot be repaired from the
add-on the way instance 1 was: the dangling is on images the graph did not create in this step,
and both walking the persisted graph and tearing it down to rebuild dereference the freed pointers
(node removal decrements image users). A robust fix requires the live-preview rebuild to be
undo-consistent for image datablocks too — i.e. not to re-create leaves outside an undo step —
which is a change to the timer-driven preview model beyond this hardening pass and is filed as a
known defect. The `_build`/`rehydrate`/naming/`compose` machinery is byte-identical to the 0.13.0
baseline `02c360c`, so this predates the hardening work; it bounds the sustained gate below.


## 7. Engines (H5)

The delivered kit (`engine_validation/`, `scripts/engine_validation.py`) was run against the
hardened candidate: the fixtures (channel plate, seam object with and without height, difficult
atlas, transparency card, production prop) were exported for the Unreal (metallic/roughness,
DirectX normal, ORM) and Unity URP (Lit metallic, smoothness in alpha) targets through the
ordinary export plan — the transactional publication and the complete Fill of this candidate —
and `fixtures.json` records the package checksum, the Blender build and the sha256 of every
exported file (`.blender-test/quality/engine-fixtures/`, status
`engine-validation-status.json`: fixtures PASS — 6 fixture folders per target (channel_plate, seam_object, seam_object_height, difficult_atlas, transparency_card, production_prop) for Unreal and Unity URP, package `open_painter-0.13.0-hardening.zip`, commit 02c360c + working tree, Blender 5.2.1).

**Unreal Engine: NOT RUN.** Missing dependency: an Unreal Engine 5 editor with the Python
Editor Script Plugin. Only the Epic Games Launcher app exists on this machine
(`/Users/Shared/UnrealEngine/Launcher/Unreal Engine.app`; no engine under
`/Users/Shared/Epic Games`). Command once an editor and a project exist:
`OP_UNREAL_EDITOR=<UnrealEditor-Cmd> OP_UNREAL_PROJECT=<project.uproject> python3
scripts/engine_validation.py --skip-fixtures` (runs
`engine_validation/unreal/import_and_validate.py` with `OP_FIXTURES=<engine-fixtures/unreal>`).

**Unity URP: NOT RUN.** Missing dependency: a Unity Editor with a URP project. Unity Hub's
configuration exists (`~/Library/Application Support/UnityHub`, no projects, `releases.json`
lists downloadable editors) but no editor is installed (`/Applications/Unity` absent). Command:
`OP_UNITY_EDITOR=<Unity binary> OP_UNITY_PROJECT=<URP project> python3 scripts/engine_validation.py
--skip-fixtures` (copies `engine_validation/unity/Editor/OpenPainterValidation.cs` into the project
and runs `OpenPainter.Validation.Run` in batch mode).

The kit scripts could not be verified against installed engine APIs for the same reason.
Nothing was installed, licensed or purchased. Unity URP validation, when it runs, will not
establish Built-in or HDRP validation.


## 8. Commands and results

All from the checkout with `BLENDER_USER_RESOURCES=.blender-test/user` (baseline runs from the
`02c360c` worktree with `.blender-test/user-baseline`); `blender` is
`/Applications/Blender.app/Contents/MacOS/Blender`, `python3` the bundled
`…/Resources/5.2/python/bin/python3.13` for the pure suites.

```
python3 scripts/hardening_reproductions.py --out .blender-test/quality/hardening/{before,after-h1,after-h3}.json
blender --background --factory-startup --python scripts/hardening_reproductions_blender.py -- --out .blender-test/quality/hardening/painting-{baseline,after-h2}.json
OP_MATERIAL_TARGET_REPRODUCE=1 OP_MATERIAL_TARGET_REPORT=….../material-target-ui-baseline.json blender --factory-startup --enable-event-simulate --python tests/integration/material_target_ui.py   (baseline worktree)
python3 scripts/run_pure_tests.py                                                            # 158 pure tests incl. transaction, padding, surface reach
blender --background --factory-startup --python-expr "import unittest; …discover('tests/unit')…"  # 212 unit tests
blender --background … --python tests/integration/export_safety.py                           # 18 checks
blender --background … --python tests/integration/export_padding.py                          # 16 checks
blender --background … --python tests/integration/material_painting.py                       # 31 checks
blender --factory-startup --enable-event-simulate --python tests/integration/material_target_ui.py     # 8 scenarios
blender --factory-startup --enable-event-simulate --python tests/integration/material_painting_ui.py   # 2K/25 layers
blender --factory-startup --enable-event-simulate --python tests/integration/vector_brush_ui.py
blender --factory-startup --enable-event-simulate --python tests/integration/quality_baseline_paint_ui.py
OP_SESSION_STROKES=500 OP_SESSION_UNDO_EVERY=100 OP_SESSION_FRESH_AT=99999 blender --factory-startup --enable-event-simulate --python tests/integration/material_session_ui.py   # H4 endurance (PASS)
OP_SESSION_STROKES=100 blender --factory-startup --enable-event-simulate --python tests/integration/material_session_ui.py   # H4 full-feature: undo/25 + mid-session layer (PASS)
OP_SESSION_STROKES=40 OP_SESSION_SET_EVERY=20 OP_SESSION_UNDO_EVERY=20 blender --factory-startup --enable-event-simulate --python tests/integration/material_session_ui.py   # instance-1 undo crash: PASS after fix, crashed at gesture 19 before
# OP_SESSION_STROKES=500 (defaults: undo/25 + mid-session layer) reproduces the open instance-2 memfile-undo crash (§6 sidebar)
blender --background … --python scripts/material_baseline.py -- --compare .blender-test/quality/material-baseline-0.12.2.json --output .blender-test/quality/material-baseline-hardening.json
python3 scripts/build_extension.py --output dist/open_painter-0.13.0-hardening.zip
python3 scripts/run_clean_install.py --package dist/open_painter-0.13.0-hardening.zip
python3 scripts/engine_validation.py                                                          # fixtures + NOT RUN
python3 scripts/run_tests.py --package dist/open_painter-0.13.0-hardening.zip [--gui-only]     # canonical runner
```

Results:

| Check | Status | Evidence |
| --- | --- | --- |
| Export reproductions before/after (cleanup failure, competing writer, unreadable journal) | FAIL on baseline → PASS on candidate | `hardening/before.json`, `after-h1.json`, `after-h3.json` |
| Sparse-atlas Fill reproduction (128/256/512/1024) | FAIL (256+) on baseline → PASS on candidate | same files |
| Painting reproductions C1/C2/C3 (real operator + background) | FAIL on baseline → PASS on candidate | `hardening/material-target-ui-baseline.json`, `painting-baseline.json`, `material-target-ui.json`, `painting-after-h2.json` |
| Pure suite (`run_pure_tests.py`, 158 tests incl. 22 transaction, 16 padding, 13 reach) | PASS | terminal |
| Unit discovery in Blender (212 tests) | PASS | terminal |
| `export_safety.py` (18 checks, 5 hard terminations of a background worker) | PASS | `export-safety.json` |
| `export_padding.py` (16 checks) | PASS | `export-padding.json` |
| `material_painting.py` (31 checks) | PASS | `material-painting.json` |
| `material_target_ui.py` (8 scenarios, one running operator) | PASS | `material-target-ui.json` |
| `material_painting_ui.py` 2K/25 (latency, settle, Esc, undo, native route) | PASS | `material-painting-ui-2048.json` |
| `vector_brush_ui.py`, `quality_baseline_paint_ui.py` | PASS | `.blender-test/vector-brush-ui.json`, `quality-baseline-painting.json` |
| Background suites touching strokes, selection, documents, undo and caches: `vector_strokes`, `stroke_parity`, `multi_set_paint`, `texture_set_lifecycle`, `module_documents`, `selection_target`, `inspector_panels`, `layer_workflows`, `artist_file_workflow`, `artist_file_edits`, `catalog_modules`, `module_update`, `shader_undo`, `paint_freeze`, `layer_cache`, `shader_lifecycle_edges`, `resource_keys`, `compatibility_contract` (0.12.2 file: appearance, stroke rasters and export scope unchanged) | PASS | terminal |
| Material baseline: all 50 built-in recipes vs 0.12.2 | PASS — 50 identical, 0 different, 0 missing | `material-baseline-hardening-comparison.json` |
| 500-gesture 2K session (H4) | PASS — 500 gestures, undo/100, cross-set: 476 committed, 0 lost, 0 misdirected, 0 writes outside scope, agreement 1.19e-07, reopen byte-exact. The undo-every-25 + mid-session-layer variant is a FAIL on a pre-existing memfile-undo crash (§6 sidebar); the 100-gesture run (`material-session-probe.json`) covers that cadence and the mid-session layer | `material-session-2048.json` |
| Engine kit fixtures on the candidate / Unreal / Unity URP | fixtures PASS / NOT RUN / NOT RUN | `engine-fixtures/engine-validation-status.json` |
| Candidate ZIP: validate, clean install (two sessions, 18 steps incl. the 0.12.2 upgrade) | PASS — packaged `0c94986e` build in a fresh profile, checkout off `sys.path`: phase 1 (11 steps — set-up, material, master controls, layers, mask, bake, paint, save), phase 2 (6 steps — reopen, export for Blender and Unreal, disable/re-enable, reopen), incl. step 18 (a 0.12.2 file opened, migrated visibly, exported 9 files) — `CLEAN_INSTALL_PASS` | `run_clean_install.py` output |
| Canonical runner on the package (`run_tests.py --package`, background phase) | PASS — on the packaged `0c94986e` build: 130 background integration suites, `compatibility_contract.py` (0.12.2 file), 34 pure tests in Blender, and 212 unit tests (“ALL TESTS PASSED”); log archived at `hardening/runner-background-package.log` | terminal |
| Canonical runner GUI phase (`--gui-only`) | PASS — 27 real-window GUI probes on the packaged `0c94986e` build, incl. `MATERIAL_TARGET_UI_PASS` (H2 target capture), `MATERIAL_PAINTING_UI_PASS` (2K/25 layers), `VECTOR_BRUSH_UI_PASS`, `MASTER_CONTROLS_UI_PASS`, `INSPECTOR_UI_PASS`, `LAUNCH_AUDIT_PASS`, `QUALITY_BASELINE_PAINT_PASS`; “ALL TESTS PASSED” (log `hardening/runner-gui-package.log`) | terminal |



## 9. Final gate

- [x] Post-commit cleanup failures keep the complete committed export — PASS (`test_cleanup_failure_keeps_every_committed_output`, `test_interrupt_after_first_backup_removed_keeps_new_state`; `export_safety.py` "post-commit cleanup failure keeps the complete export", hard termination during cleanup).
- [x] Pre-commit interruption restores originals coherently across all request folders — PASS (`test_interrupt_*_restores_originals`, `test_two_folders_one_outcome_from_either_folder`; 3 pre-decision hard terminations in `export_safety.py`).
- [x] No-overwrite publication survives a competing writer at the final boundary — PASS (`test_competing_file_at_publish_is_preserved`, `test_exclusive_create_fallback_without_hard_links`; `export_safety.py` "a file that appeared after the preflight is not overwritten"; reproduction before/after). LIMITED TO local APFS and the forced no-link fallback; network and FAT volumes NOT RUN.
- [x] Corrupt journals retain recovery evidence and cause an explicit error — PASS (`test_invalid_journals_are_kept_and_reported`, `test_missing_coordinator_keeps_evidence`; reproduction before/after).
- [x] Recovery is idempotent and cannot act on a still-running export — PASS (`test_recovery_interrupted_and_restarted`, `test_recovery_refuses_a_running_transaction`, `test_second_process_is_refused_while_one_publishes`; `export_safety.py` lock case).
- [x] Changing targets while the brush remains active changes the next stroke's actual destination — PASS (`material_target_ui.py` scenarios 1–2 on the same operator; FAIL reproduced on the baseline).
- [x] Active gestures use one immutable target/settings snapshot — PASS (`material_target_ui.py` scenarios 3–4: a target change discards the pending stroke and restores the preview, settings changed mid-gesture are recorded as captured and apply to the next gesture).
- [x] Disabling cross-set painting protects already-linked sets' actual outputs — PASS (`material_painting.py` "a single-set stroke never alters a set an earlier cross-set stroke linked" — CPU rows, shader identity, 1024 recompute, export, erase, symmetric case; `material_target_ui.py` scenario 7; reproductions before/after).
- [x] Default surface painting does not reach nearby hidden surfaces — PASS for the rear sheet 8 mm behind, two objects 2 mm apart, disconnected pieces of one object and a pocket under a lip (`material_painting.py`, `test_surface_reach.py`; C3 reproduction before/after). LIMITED TO the surface-local rule: a surface continuing smoothly under something else is painted along from its own side; a smoothly ramped tunnel is not blocked.
- [x] Stroke scopes survive old-project loading, duplication, import, save/reload, and undo — PASS (`material_painting.py`: legacy strokes render everywhere and keep their placement, reopen, duplicated layer and set, imported document; `compatibility_contract.py` on a 0.12.2 file; the session's 20 undo/redo cycles restore strokes with their scopes).
- [x] Sparse-atlas Fill resolves every empty texel with a valid original donor — PASS (`test_export_padding.py` sparse islands at 128–1024 with the brute-force oracle, `export_padding.py` per preset; reproduction before/after).
- [x] Finite padding, resampling, channel semantics, and valid interior values retain their contracts — PASS (`test_export_padding.py`, `export_padding.py`, `compatibility_contract.py`, 50 recipes identical).
- [x] Sustained rapid painting loses no strokes and matches fresh full recomputation — LIMITED TO — verified across a 500-gesture endurance run (undo/100, cross-set: 476 committed, 0 lost, 0 misdirected, shader leaves match a fresh full recomputation to 1.19e-07, reopen byte-exact) and a 100-gesture full-feature run (undo/25, a mid-session paint layer: 0 lost, agreement 5.96e-08, reopen byte-exact). No completed run ever loses or misdirects a stroke. The full plan workload (500 gestures with undo every 25 *and* a mid-session layer together) aborts on a pre-existing memfile-undo image crash (§6, instance 2), which bounds the endurance run's undo cadence; a related stroke-undo crash (instance 1) was found and fixed.
- [x] Actual visible feedback and final settle are measured separately, with misses reported — PASS — input-to-visible is pixel evidence (offscreen re-render, ≈30 ms poll) reported separately from the publication→redraw proxy and from release-to-current settle, per event pacing and early/late; watched gestures with no visible change are counted, not dropped (9 of 97 in the 500-gesture run). The latency and settle targets themselves are not met at 2K/25 layers (§6), reported as measured (LIMITED TO this hardware).
- [x] Candidate ZIP clean-install and upgrade checks pass without source-checkout dependencies — PASS — the packaged ZIP installs and runs both clean-install sessions in a fresh profile with the source checkout off `sys.path`, including the 0.12.2 → 0.13.0 upgrade (step 18); `run_clean_install.py` → `CLEAN_INSTALL_PASS` (log `hardening/clean-install-package.log`).
- [ ] Actual Unreal and Unity URP output checks are executed, or remain explicitly NOT RUN — NOT RUN (no engine installed; fixtures built with the candidate; commands in §7).

Definition of done, applied: no reproduced data-safety failure remains (H1, H3); the brush cannot
silently diverge from its displayed target or permission scope (H2, GUI-verified on one running
operator); padding fulfils its selected mode (H3); the remaining claims are bounded by evidence:
responsiveness numbers are reported as measured (§6) against the plan's targets, the tablet and
the engines are NOT RUN, and the default surface reach has a stated limit. The sustained session
(H4) uncovered a pre-existing memfile-undo crash class: one instance — undoing a first stroke on
a layer — is fixed and verified, the other — a leaf image freed by a later undo after the
post-undo relearn re-created it — remains open and bounds the heaviest 500-gesture workload; both
are dissected in §6 and neither is reported as passing. This is an explicitly limited evaluation
build, not a claim that every ordinary artist workflow is validated, and the open undo crash is a
release blocker to resolve before shipping.

