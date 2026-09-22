# Open Painter 0.13 — Stability and Recovery

Implementation of `Open_Painter_0.13_Stability_and_Recovery_Plan_Revised.md`. This report separates
**PASS**, **FAIL**, **NOT RUN**, and **LIMITED TO** results, and records the candidate and
environment each result is tied to.

## Candidate and environment (S0)

| Field | Value |
| --- | --- |
| Source revision at start | `16ff0be4b4cb104fdf575b0156ceb1f0c2d8eb16` (`task/open-graph-0.4.0`) |
| Local changes | `src/open_painter/export/transaction.py`, `tests/unit/test_export_transaction.py` (this pass) |
| Blender | 5.2.1 LTS |
| Python / NumPy | 3.13.13 / 2.3.4 (Blender-bundled interpreter) |
| OS / CPU | macOS (Darwin 25.6), Apple M3 Pro |
| GPU / backend | Metal (measured in the GUI runs; NOT RUN in background) |
| Test module provenance | Pure suite and transaction tests import the source checkout (`scripts/run_pure_tests.py` prepends `src/`); no installed-package run was made this pass (see S5). |

The audit archive `Open_Painter_16ff0be_Audit_Evidence.zip` was **not present on this machine**, so
the two recovery failures were reproduced directly through the real transaction path rather than
through the archive's `check_recovery.py`. Evidence: `.blender-test/quality/stability/`.

## Priority order and status

Export recovery and undo reliability are the two hard blockers. This pass completes and verifies
the first; the second and the later checkpoints are reported honestly below.

| Checkpoint | Status |
| --- | --- |
| A — S0 + S1: unchanged-file recovery corrected; transaction regression matrix | **PASS** |
| B — S2: undo-safe resource lifecycle; full 500-gesture workload | **PASS** — reproduced the crash, fixed it, full workload completes with exact undo/redo and bounded resources |
| C — S3: formerly failing shader fixture compiles and paints | **NOT RUN** (not investigated this pass) |
| D — S4: sustained-workflow timings | correctness **PASS**; responsiveness targets **FAIL**, no optimization pass (see S4) |
| E — S5: installed candidate + engines | **NOT RUN** |

---

## S1 — Recovery is safe when old and new files are identical (PASS)

### Reproduction on the audited baseline (before)

Both failing sequences were reproduced through the production `Transaction.commit()` (in a worker
process, hard-terminated at a boundary) and the public `recover()` in this process. Evidence:
`.blender-test/quality/stability/s0-recovery-before.json`.

1. **Interruption after PREPARED, before any backup, identical original and staged bytes.**
   Baseline recovery ran `removed unpublished BaseColor.png` → the unchanged original was **deleted**
   (`original_preserved: false`). The bytes at the destination equalled the *staged* digest, so the
   old code treated the untouched original as this transaction's published output and removed it.
2. **Restored identical original, then journal cleanup interrupted, recovery re-run.** After the
   first recovery restored the original from its backup, a failed journal removal left the journal in
   place; the second recovery again matched the staged digest and **deleted** the restored original.

### Root cause

Recovery inferred ownership from a content digest: "the destination equals the staged bytes,
therefore this transaction published it, therefore remove it." Equal bytes prove content equality,
not authorship — and an export whose replacement is byte-identical to the original makes the two
indistinguishable by digest.

### Fix

Ownership is now proven from evidence independent of content (`src/open_painter/export/transaction.py`):

- **Staging siblings are kept until commit.** Each staged file is copied to a sibling
  (`<name>.op-staging-<id>`) beside its destination and published by an atomic no-clobber hard link
  (`os.link`); the sibling is **not** removed at publication (as it was before) but at cleanup, after
  the durable COMMITTED decision. During pre-commit recovery a destination is provably this
  transaction's only if its staging sibling is the *same inode* (`os.path.samefile`) **and** it still
  holds the recorded staged digest. A file another writer edited in place is not deleted.
- **The hard-link-less fallback records its claim.** Where `os.link` is unavailable, the destination
  is created with `O_EXCL`, the creation is recorded in the coordinator journal (`claimed: true`)
  *before* any byte is written, then filled; recovery treats a claimed file (complete or a prefix of
  the staged bytes) as ours, an empty unclaimed file as ambiguous (kept, error), and a claimed file
  another writer replaced as foreign (kept, error).
- **Recovery decides per file from separate facts** — original existence, a verified backup, the
  ownership proof above, and the destination's bytes — in `_undo_item`:
  - original present with its recorded digest → **preserved** (untouched or already restored), even
    when it also equals the staged bytes; a surviving backup is then a duplicate and is removed;
  - destination proven ours → removed, backup (if any) restored;
  - anything else (foreign bytes, our bytes without a proof, an empty file, a missing original with no
    backup) → **kept**, and `RecoveryRequired` names it with the journal and backups left in place.
- **Idempotent through interrupted recovery.** Journals are removed last (participants, then the
  coordinator decision); a failure there raises `RecoveryRequired('… run it again')` and the next
  recovery converges. Failpoints were added inside the recovery control flow
  (`recover_after_first_change`, `recover_before_journal_removal`, `recover_after_participant_journals`)
  so interruption *during* recovery is tested in a worker process.
- **Committed decisions are never rolled back.** The coordinator's COMMITTED record is the decision;
  writing a participant journal, removing a backup or a sibling afterwards is housekeeping and returns
  `Outcome.cleanup_pending`, never a pre-commit rollback.

Journal format is bumped to 3; format-2 journals from the earlier implementation are still recovered
(`ACCEPTED_FORMATS = {2, 3}`). Because format-2 published files kept no sibling, they cannot be
proven ours: recovery keeps them and reports them rather than deleting them — safe by construction.

### After

`.blender-test/quality/stability/s1-recovery-after.json`: both sequences now end with
`original_preserved: true` (`kept BaseColor.png (the original)` / `previous export restored`).

### Regression matrix (PASS)

`tests/unit/test_export_transaction.py` — 33 tests, all green on the Blender-bundled interpreter
(`python -m unittest tests.unit.test_export_transaction`). The 22 pre-existing hardening tests are
unchanged and still pass. New `IdenticalContentTests` cover the plan's §5.3 matrix through the real
`Transaction`/`recover` in fresh processes, asserting file existence, complete file set, byte hashes,
and outcome messages (not just "no exception"):

- unchanged originals survive every pre-commit interruption (`after_prepared` … `before_commit_decision`),
  for mixed and all-identical payloads, one and two folders;
- a restored identical original survives failed journal cleanup and repeated recovery;
- hard interruption *inside* recovery converges (three interior failpoints, either start folder);
- committed outputs survive a participant-journal failure and cleanup interruption;
- a competing writer's file (including one whose bytes equal ours) is never overwritten;
- the exclusive-create fallback: interrupted before the claim (ambiguous, kept), after the claim and
  mid-fill (ours, removed), and when a claimed file is replaced by a foreign one (kept, error);
- a missing original is reported, never "restored" over nothing;
- a previous-format (2) journal is recovered without deleting unproven files;
- successful exports with identical and changed outputs (mixed/all-same, one/two folders).

### End-to-end (PASS)

`tests/integration/export_safety.py` drives the real exporter (maps, manifest, mesh, multi-set)
through this transaction with hard process terminations at every publication boundary and recovery
afterwards. All 17 checks PASS on Blender 5.2.1
(`.blender-test/quality/export-safety.json`, log `.blender-test/quality/stability/export-safety.log`),
including the post-commit cleanup-pending path, the no-overwrite/exclusive-create refusal, a competing
file after preflight, and cross-folder recovery from either folder.

### Kept guarantees

Committed exports stay committed during later cleanup; replacement-disabled exports preserve competing
files; all folders of one request follow one recovery decision; the existing cross-process locking and
`RecoveryRequired`/`ExportBusy`/`PublicationConflict` contracts are unchanged.

### Scope of the evidence

Process-interruption coverage on local APFS (macOS). Power-loss durability and network filesystems
(SMB/NFS/FAT) remain **NOT RUN**; the code keeps a coherent, recoverable request, not instantaneous
atomic visibility to unrelated readers.

---

## S2 — Undo-consistent preview resource lifetimes (PASS)

### Reproduction (before)

The remaining ordinary-editing crash is the pre-existing memfile-undo defect the 0.13 hardening report
dissected as *instance 2* (`docs/quality-hardening-0.13.md` §6). It does **not** manifest on reduced
workloads: a compressed run through the real modules at 512²/8 layers and the real GUI event-simulate
harness at **1024²/25 layers, undo every 10, late layer at 50, 120 gestures** both completed cleanly
(`.blender-test/quality/stability/undo-repro-before.{json,log}`). It reproduces reliably only on the
full **2048²/25-layer** workload: the unmodified candidate **crashed with SIGSEGV at gesture ~325**
(`.blender-test/quality/stability/s2-baseline-2k.log`, exit 139; crash backtrace
`s2-baseline-crash.txt`) in EEVEE `MaterialModule::end_sync → BKE_image_acquire_ibuf` — a leaf
datablock the compiled material still sampled had been freed. A memfile-undo probe in a disposable GUI
Blender confirmed a *simply* rebound `node.image` is remapped correctly by partial undo
(`scratchpad/probe/memfile.json`), so the defect needs the specific "datablock created and bound
between two undo steps (or captured mid-handoff), then undo past it" pattern the full workload
accumulates.

### Root causes and fix

Three sources let a datablock the persisted material tree still binds be freed by a later undo:

1. **Relearn re-created leaves.** After every undo, `cache._relearn` re-dirties every set and the next
   timer tick reconstructs the graph, which allocated fresh leaf datablocks *outside* any undo step. A
   subsequent undo freed them while the tree still bound them. **Fix — a post-undo freeze**
   (`shader_resources.freeze_new_leaves`, set in `cache.on_undo`, cleared in `cache.on_load`, at the
   start of a gesture in `vector_brush._begin`, and on the first authored edit to a live session in
   `shader_preview.notify`): while frozen, `Resources.request` never allocates a new leaf datablock and
   `shader_preview.ensure` never `_build`s a replacement graph when a persisted graph exists — the
   reconstruction may only rehydrate and regenerate content into datablocks the nodes already bind.
2. **Per-stroke leaf churn.** Each stroke changed a boundary's content key, so `request` created a new
   leaf datablock and the old one became a 0-user orphan swept away — a datablock created after a push
   and freed by the next undo. **Fix — stable per-boundary datablocks** (`ResourceBinding.stable`,
   `stable_image`; `Resources.request(stable=…)`): a stroke boundary keeps one leaf for its life and
   regenerates its content into it in place (the incremental base reads the same datablock while it is
   still ready, then it is re-keyed and rewritten), so no per-stroke datablock is ever created or freed.
   Leaf count is now flat at 60–64 across 476 strokes, where the churn previously left ~190.
3. **Gesture previews captured by the push.** A stroke's undo push could land while the node still
   showed its transient 512² gesture preview (the full-resolution leaf had not published yet); the
   snapshot then referenced the preview, and after undo the (correctly) frozen reconstruction could not
   rebuild it, stranding the node on the 512 preview. **Fix — `Preview.retire()` hands each node back
   to the persistent leaf it owned before the gesture** (which then regenerates in place), so an undo
   push captures that leaf, never a transient preview. (An earlier attempt that swapped `node.image`
   around `ed.undo_push` raced EEVEE's async sync and is not used.)

Do not assume `undo_post` can repair an already-dangling pointer: the fix keeps the dangling from ever
being created. `retire_unloaded_leaves` (undo/redo/load) and the base-leaf pinning are preserved.

### Verification (after)

Full **2048²/25-layer, 500 gestures, undo/redo every 25, save every 100, cross-set strokes, a fresh
paint layer created at gesture 300**, on the reference machine (`s2-fixed-run1.json`):

- **No native crash** (exit 0, `MATERIAL_SESSION_PASS`); the baseline crashed at ~325.
- **Exact undo/redo:** all five row-digest checks (gestures 99/199/299/399/499) `exact: True`; all 20
  undo/redo cycles reached the current result.
- **Correctness:** every painted layer's shader leaves equal a fresh full recomputation — 20 leaves,
  max error 1.2e-07; 0 lost gestures, 0 misdirected strokes, 0 writes outside scope.
- **Save/reopen:** 476 strokes saved and restored, all channel outputs byte-identical, 0 preview
  images in the file, no preview bound to a layer.
- **Bounded resources:** RSS flat at ≈6085 MB from gesture 99 to 499 (peak ≈8224 MB transient); leaves
  60–64, previews 0 at settle, 0 released-datablock leaks — the bound is one leaf per live boundary and
  the finite undo history, not unbounded retention.

Short regression `tests/integration/undo_leaf_lifetime.py` (background Blender) locks the invariants:
the post-undo freeze creates no new bound leaf, repeated undo/redo leaves every bound leaf alive, and
the freeze releases so a structural edit rebuilds. Registered in `scripts/run_tests.py`.

**Repeat runs (S2 gate): all PASS.** Three consecutive full runs on the original seed
(`s2-fixed-run1/2/3.json`) and a second seed (`s2-fixed-seed11.json`) each completed with no crash,
all five exact undo/redo checks `True`, 0 mismatches / 0 lost / 0 misdirected, and byte-identical
reopen with 0 preview images in the file (seed 11 agreement max error 6.0e-08).

**No regression in the existing suites.** The stroke, shader-lifecycle and undo background suites pass
unchanged after these lifecycle changes: `undo_leaf_lifetime`, `shader_undo`, `shader_lifecycle_edges`,
`shader_adoption`, `shader_boundaries`, `shader_sources`, `shader_runtime`, `shader_units`,
`shader_structural_fields`, `resource_keys`, `vector_strokes`, `stroke_parity`, `multi_set_paint`,
`paint_freeze`, `layer_cache`, `texture_set_lifecycle`, `shader_nodes`, `shader_preview` (all PASS);
the 169-test pure suite and the 33-test transaction suite also remain green.

---

## S3 — Shader-sampler compilation (NOT RUN this pass)

The 4K/eight-layer fixture's Metal "too many samplers" error (hardening report §6; the fixture uses
8 layers with no folder cache, which exceeds the 16-sampler Metal limit while the 25-layer fixture
compiles) was not re-investigated or fixed this pass. It remains a documented FAIL from the baseline
until reproduced and corrected on this candidate on the Metal reference backend. Note the S2
stable-datablock change reduces the number of distinct leaf datablocks a set holds (one per boundary
instead of a per-stroke churn), which may reduce sampler pressure — but this is unverified against the
4K fixture and is **not** a claim that S3 is resolved.

## S4 — Sustained responsiveness (correctness PASS; timing targets FAIL / not optimized)

With S2 fixed, the full workload now completes, so it can be profiled. Correctness is intact (S2
above). The responsiveness targets are **not met**, and no optimization pass was performed here — the
timing work of S4.3 is left for a follow-up. Reported from `s2-fixed-run1.json` (high-instrumentation
pass; the machine was **not** quiesced — an interactive Blender session was open — so these are
diagnostic upper bounds, not the clean low-instrumentation measurement S4 specifies):

| Metric | Target | Measured (run 1) | Result |
| --- | --- | --- | --- |
| Warmed input → visible pixels, p95 | ≤ 100 ms | 460.6 ms (p50 147 ms) | **FAIL** |
| Warmed release → current, p95 | ≤ 250 ms | ≈6.0 s (p50 836 ms; two 6 s outliers at saves) | **FAIL** |
| Ordinary one-stroke undo/redo, p95 | ≤ 1 s (new budget) | undo 13.1 s / redo 13.5 s | **FAIL** |

Correlated evidence is retained per gesture (pixel `input_to_visible_pixels` vs the
`input_to_visible_proxy` publication/redraw estimate kept separate; 9 watched gestures showed no
detected pixel change and are listed as `missing`; the probe's own capture cost is recorded).
Fresh-layer first strokes (~825 ms p50) and the 167 s eight-file export are reported separately from
ordinary stroke latency. Current memory is bounded (S2); GPU memory remains **NOT RUN**. Targets were
not lowered — the misses stand as FAIL.

## S5 — Installed candidate and engines (NOT RUN this pass)

No candidate ZIP was built or installed this pass; the transaction fix was verified against the source
checkout. Unreal and Unity URP validation remain **NOT RUN**: only the Epic Games Launcher and Unity
Hub exist on this machine, with no Unreal editor + Python Editor Script Plugin and no Unity URP
project (see `docs/quality-hardening-0.13.md` §7 for the exact commands and dependencies).

---

## Test commands

```sh
# Pure suite (Blender-bundled NumPy interpreter), includes the S1 transaction matrix — 169 tests PASS:
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 scripts/run_pure_tests.py

# The transaction regression matrix alone — 33 tests PASS:
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 -m unittest tests.unit.test_export_transaction

# End-to-end export safety through the real exporter — 17 checks PASS:
BLENDER_USER_RESOURCES="$PWD/.blender-test/user" \
  /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
  --python-exit-code 1 --python tests/integration/export_safety.py

# S1 before/after reproduction (writes the two JSON evidence files):
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 \
  scripts/stability_recovery_repro.py "$PWD"  # writes s0-recovery-before.json / s1-recovery-after.json

# The undo crash reproduction / S2 gate (full workload; ~18 min each, GUI). Repeat for the gate;
# OP_SESSION_SEED picks the seed (default 7):
OP_SESSION_STROKES=500 OP_SESSION_UNDO_EVERY=25 OP_SESSION_FRESH_AT=300 \
  OP_SESSION_REPORT="$PWD/.blender-test/quality/stability/s2-fixed-run1.json" \
  BLENDER_USER_RESOURCES="$PWD/.blender-test/user" \
  /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --enable-event-simulate \
  --python tests/integration/material_session_ui.py

# The S2 leaf-lifetime regression (background Blender) — PASS:
BLENDER_USER_RESOURCES="$PWD/.blender-test/user" \
  /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
  --python-exit-code 1 --python tests/integration/undo_leaf_lifetime.py
```

## Outstanding

- **S1 (hard blocker): done and verified.** Export recovery preserves unchanged originals.
- **S2 (hard blocker): done and verified.** The full 2K/500 undo-every-25 + mid-session-layer workload
  no longer crashes; undo/redo is exact, resources are bounded.
- **S4 responsiveness: FAIL, not optimized.** Warmed input-to-visible (461 ms), release-to-current
  (~6 s) and undo/redo (~13 s) all miss their targets; no optimization pass was done and the machine
  was not quiesced. This bounds "responsive" claims but is not a data-loss or crash issue.
- **S3 shader compilation: NOT RUN.** The 4K/eight-layer Metal sampler failure was not investigated.
- **S5 installed candidate + engines: NOT RUN.** No candidate ZIP built; Unreal/Unity remain NOT RUN
  (no editors on this machine).

Both hard blockers (S1, S2) are delivered and verified. S3, S4 (optimization), and S5 remain; per the
plan's stopping rules they are listed as their true status rather than the plan being marked complete.
