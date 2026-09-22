# Open Painter 0.13: Stability, Recovery, and Interactive Performance

**Purpose:** Close the failures still preventing the add-on from being trustworthy in ordinary production work.  
**Audited baseline:** `16ff0be4b4cb104fdf575b0156ceb1f0c2d8eb16` on `task/open-graph-0.4.0`.  
**Companion evidence:** `Open_Painter_16ff0be_Audit_Evidence.zip`.  
**Scope:** The add-on itself. No repository administration, billing, marketing, release publishing, or another UI redesign.  
**Status:** Implementation and verification instructions. This document does not certify the candidate.

## 1. The next step

Keep the feature set frozen. Complete the existing 0.13 work rather than starting another feature pass.

The next candidate must meet two non-negotiable requirements:

1. **Repeated painting, layer creation, Undo, and Redo do not crash Blender or leave invalid preview references.**
2. **Interrupted export and repeated recovery never delete a pre-existing texture just because its replacement has identical bytes.**

Then resolve the current shader-compilation failure and measure the uninterrupted painting workflow against the existing performance targets. Engine verification remains a separate, explicit gate.

Preserve the previous hardening work: per-gesture target capture, per-stroke texture-set permissions, Surface reach, complete Fill Empty Space, authored master controls, and safe cancellation. Do not rewrite these systems unless a new reproduction identifies a dependency that must change.

## 2. Master direction prompt

> Continue Open Painter from the current 0.13 candidate. Read this plan and the companion audit archive. Confirm the checked-out revision and local changes before editing. The audited baseline is `16ff0be`, not `main`'s older release; if the checkout has advanced, determine which failures still exist without reverting unrelated work.
>
> First reproduce the unchanged-file recovery failure against the actual transaction module and reproduce the full default sustained-session undo crash. Preserve before-fix evidence. Fix the underlying ownership, transaction, and undo-lifetime contracts, not only the supplied examples.
>
> Prioritize export safety and undo correctness. Make the live preview consistent with Blender's undo lifecycle for images and node trees. Do not suppress Undo, reduce its history, disable normal layer creation, or lower the stress workload to claim success. Do not treat “pre-existing” or “Blender internals” as an exemption from the add-on's quality requirements.
>
> After the safety fixes, reproduce the current 4K sampler-compilation failure and make that fixture usable without dropping selected channels or flattening the artist's editable document. Optimize the measured sustained painting bottlenecks without publishing stale results as current.
>
> Use isolated profiles, disposable projects, and temporary export folders. Building and testing a candidate ZIP is authorized by this plan. Installing into the user's active Blender, changing their projects, committing, pushing, tagging, or releasing requires a separate instruction.
>
> Deliver code, regression tests, exact commands, evidence, and a requirement-by-requirement report. A passing reduced workload does not cancel a crash in the required workload. Tests that did not execute remain NOT RUN. Do not present test discovery, fixture generation, or a package installation check as proof that the corresponding workflow passed.

## 3. Starting evidence and what it establishes

These observations are pinned to the audited baseline. They are not assumptions about a later checkout. Source provenance is listed in section 13.

| Observation | Evidence available | Consequence |
| --- | --- | --- |
| Recovery deletes an unchanged original after PREPARED, before any backup exists. | Prior audit ran an isolated excerpt of `_apply_decision()`, not the complete public transaction path. [A1] | Reproduce through real `Transaction.commit()` and public `recover()`, then fix. |
| A second recovery deletes an identical original restored by the first recovery when journal cleanup was interrupted. | Same isolated audit, with recorded file-existence results. [A1] | Recovery idempotence must cover identical old/new content. |
| The default 500-gesture workload crashes with undo every 25 and a new layer at 60%. | Repository hardening report; not independently reproduced by the audit. [R1] | This is a mandatory crash regression, not an optional stress limitation. |
| A modified 500-gesture run and a 100-gesture full-feature run pass. | Repository report. [R1] | Preserve these tests, but do not substitute them for the failing combined workload. |
| Sustained 2K feedback, settling, and undo/rebuild remain slow. | Reported reference-machine measurements. [R1] | Re-measure after the lifetime fix, using the same workload and comparable instrumentation. |
| Current 4K/eight-layer fixture is blocked by a Metal sampler-compilation error. | Repository report. [R1] | Reproduce compilation on this candidate; older 4K results do not clear it. |
| Sparse-corner Fill now resolves all empty texels in the tested donor cases. | Prior audit's isolated 128/256/512/1024/2048 tests. [A1] | Keep the regression; do not redesign Fill again. |
| Actual Unreal and Unity URP imports are unrun. | Repository report. [R1] | Remain NOT RUN until an editor executes the checks. |

The audit archive contains `check_recovery.py`, `recovery_excerpt.py`, `recovery_results.json`, `check_padding.py`, `padding_excerpt.py`, `padding_results.json`, and a README. Historical excerpts preserve the reproduction. New acceptance tests must exercise the application implementation, not keep testing the copied broken excerpt.

---

## 4. S0: Freeze the baseline and make failures repeatable

### Direction prompt

> Record the exact candidate, reconstruct both safety failures with minimal disposable fixtures, and preserve the full sustained-session crash. Separate executed evidence from source inspection and inherited reports.

Record source revision, local diff identity, Blender version/build, Python/NumPy, OS, CPU/GPU/backend, RAM, window dimensions, and UI scale. Record test module paths so checkout tests and installed-package tests cannot be confused.

Extract the audit archive to a disposable directory. Its recovery probe accepts the complete transaction module:

```sh
python /path/to/extracted-audit/check_recovery.py \
  --module /path/to/open_painter/src/open_painter/export/transaction.py
```

This supplemental probe constructs recovery states. Also add the end-to-end subprocess tests in S1; they must create their journals through the real commit path.

Run the unmodified sustained workload using the configured Blender executable and an isolated `BLENDER_USER_RESOURCES` directory:

```sh
OP_SESSION_STROKES=500 \
OP_SESSION_UNDO_EVERY=25 \
OP_SESSION_FRESH_AT=300 \
"$BLENDER" --factory-startup --enable-event-simulate \
  --python tests/integration/material_session_ui.py
```

Keep the harness's cross-set strokes, set/layer switches, alpha strokes, cancellations, saves, and immediate next-gesture timing enabled. Record all effective settings, not just the three environment variables above.

Capture the last completed gesture, undo/redo boundary, resource event log, application exit code, and native crash output. A missing final JSON file after a native crash is a failure, not “no result.”

Minimize the crash into a short regression as well, but retain the complete workload unchanged. If a failure does not reproduce, report the exact environment and evidence instead of declaring it fixed without a before/after check.

**S0 gate:** Each safety finding has a reproduction or a clearly bounded NOT RUN status. Existing material and project baselines are recorded before architectural changes.

---

## 5. S1: Make recovery safe when old and new files are identical

### Direction prompt

> Fix transaction recovery's ownership inference. Equal content proves content equality, not that this transaction created a file. Protect unchanged originals and make recovery idempotent through failures during restoration and cleanup. Keep the durable committed-decision and no-clobber guarantees already added.

### 5.1 Required contract

For pre-commit interruption, recovery restores the pre-request file state or stops safely with all remaining evidence retained. After a durable commit decision, recovery keeps the new export and finishes cleanup. It never decides between these outcomes from a file hash alone.

The immediate failing sequence is:

`existing BaseColor.png == staged BaseColor.png -> PREPARED journal -> interruption before backup -> recovery`

The original must remain present with its original bytes. Nothing was backed up or replaced yet.

The second failing sequence is:

`original moved to backup -> recovery restores it -> journal cleanup interrupted -> recovery runs again`

The restored original must remain present even when it also matches the staged hash.

### 5.2 Decision rules

Treat original existence, verified backup state, durable transaction intent, actual publication state, and file identity as separate information. Digests verify bytes; they do not uniquely identify a writer.

| Observed state | Safe behavior |
| --- | --- |
| Original existed; destination matches the recorded original; no backup exists | Preserve it. It may be untouched or already restored, even when it also matches the staged bytes. |
| A verified original backup exists and rollback is required | Restore coherently, accounting for a destination that is already the restored original. Do not destroy the last verified copy. |
| Destination was originally absent | Remove it only when ownership by this transaction is established under the publication protocol. Equal bytes alone are insufficient against a competing writer. |
| State is ambiguous or evidence is incomplete | Preserve potentially valuable files and raise an actionable recovery-required error. |
| Durable COMMITTED decision exists | Finish cleanup only. A later exception must not enter pre-commit rollback. |

Checking “matches original” before “matches replacement” may address a particular branch, but do not stop at that local edit. Review repeated recovery, partially completed restoration, originally absent files, exclusive-create partial writes, and multi-folder decisions.

Preserving or skipping an unchanged destination during a new export is an acceptable optimization after the contract is correct. It does not replace safe recovery of journals written by the earlier implementation.

Do not claim stronger multi-file visibility or filesystem durability than the implementation guarantees. Keep the distinction between a coherent recoverable request and instantaneous atomic visibility to unrelated readers.

### 5.3 Required regression matrix

Use the existing production `Transaction` and public `recover()` in fresh processes. Include these payloads in the same request: A unchanged, B changed, C newly created. Repeat with all outputs unchanged and across two destination folders.

| Test | Required assertion |
| --- | --- |
| Hard termination at `after_prepared`, before backups | Every original remains byte-identical and present; new destinations remain absent. |
| Termination after one backup, after all backups, and after partial publication | Whole request returns to the pre-request state. |
| Recovery restores an identical original, then journal removal fails | Running recovery again preserves that original and completes safely. |
| Hard interruption during recovery itself | Repeated recovery converges to the same correct state. |
| Termination after durable commit and during backup cleanup | All committed outputs remain; only housekeeping is retried. |
| Failure writing a participant's journal after the coordinator committed | No rollback of committed outputs. Preserve the decision and finish later. |
| Concurrent writer creates the destination at publication | No-overwrite preserves the foreign file, including when its bytes equal ours. |
| No-hard-link fallback fails halfway through copying | Preserve recoverable evidence and distinguish our partial creation from a foreign file. |
| Corrupt journal, missing coordinator, or occupied restore destination | Explicit error, retained evidence, no guessed destructive recovery. |
| Successful export with identical and changed outputs | Correct final bytes, exact output set, clean or explicitly cleanup-pending state. |

Assert file existence, complete file set, byte hashes, backup/journal disposition, and outcome messages. “No exception” is not the oracle. Missing originals must never be accompanied by a successful “restored” result.

Use failpoints on the actual control flow, not a rewritten substitute commit function. Preserve existing exclusivity and cross-process locking tests. Document process-interruption coverage separately from untested power-loss or network-filesystem behavior.

**S1 gate:** Both audit failures pass through the real transaction path, interrupted recovery is idempotent, and all existing transaction safety tests remain green. This gate is mandatory before calling the export safe.

---

## 6. S2: Fix undo-consistent preview resource lifetimes

### Direction prompt

> Resolve the remaining native crash by making persisted graph references, image/node-tree lifetimes, and deferred preview work consistent with undo. Reduce the failure to its ownership sequence, then fix that contract. Do not work around it by weakening ordinary editing or retaining every resource forever.

### 6.1 Trace the actual lifecycle

Inspect `evaluation/cache.py`, `shader_preview.py`, `shader_graph.py`, `shader_resources.py`, `painting/gesture.py`, and `ui/vector_brush.py` together.

Trace this complete sequence:

`authored stroke -> undo snapshot -> deferred resource creation/publication -> undo -> runtime relearn -> resource reconstruction -> next undo/redo -> first draw`

The report attributes the open crash to recreated image datablocks becoming inconsistent with later memfile undo. Treat that as a concrete hypothesis supported by its traces, then reproduce and verify the exact lifetime in the current code.

Distinguish three categories explicitly:

| Category | Contract |
| --- | --- |
| Authored data | Strokes, parameters, source images, IDs, scopes, and layer structure must be restored exactly. |
| Persisted Blender graph references | Material, image, and node-tree links must always reference valid datablocks in the restored state. |
| Derived runtime state | Jobs, cached pixels, lookup tables, preview buffers, and pending publications must be invalidated or reconstructed without corrupting the first two categories. |

Instrument creation, binding, publication, retirement, removal, undo snapshots, and undo/redo handler entry. Use stable recorded identities and a history/generation token. Avoid diagnostic code that itself walks known-invalid pointers.

### 6.2 Required invariants

An undo step must not restore a material that points to an image or node tree already freed by that same restore. A deferred task created for a previous state must not publish into a newly restored document merely because a pointer or name looks familiar.

Before undo/redo/load transitions, prevent outstanding work from committing into the changing state. After restoration, rebuild runtime lookup state from valid authoritative data. Do not assume `undo_post` can repair an already-dangling native pointer by traversing it.

Derived resource creation, linking, replacement, and destruction must follow one explicit undo-safe ownership policy. Verify the first frame after undo and the next stroke, not only a settled image much later.

The existing first-stroke structural fix must remain effective. Generalize the guarantee to every affected texture set and resource type, including cross-set strokes. A failed consistency operation must not be swallowed and followed by publication of an unsafe graph or a false current status.

Preserve one visible undo step per gesture. Do not add hidden timer-generated undo steps or make Ctrl+Z undo cache housekeeping instead of the artist's last action.

### 6.3 Choose the smallest proven lifecycle change

Prototype a solution on the minimal reproduction before broad refactoring. Possible approaches include stable, correctly owned binding datablocks with updated derived content, or resource/graph transitions integrated into validated undo-aware authoring boundaries. These are design options, not a requirement to implement both.

Whichever approach is chosen, demonstrate:

- A resource referenced by a reachable undo state cannot be destroyed prematurely.
- Undo/redo restores authored state without allowing stale jobs to overwrite it.
- Derived state can be recovered after save/reopen without being mistaken for authored pixels.
- Cache reclamation remains bounded after many edits and a finite undo history.

Do not substitute longer deletion grace periods, arbitrary sleeps, global Undo disablement, fewer undo levels, forced rasterization, permanent preview disablement, or unbounded retention of image datablocks. Catching Python exceptions is not protection against an invalid native reference.

A confirmed Blender defect can be documented with a minimal upstream reproduction. That does not close this product gate unless the supported build has a verified fix or the add-on has a verified safe integration strategy.

### 6.4 Required tests

Keep the default failing session and add short tests for the exact transitions:

| Transition | Required outcome |
| --- | --- |
| First stroke on an existing or newly created layer, then immediate undo/redo | No crash, exact authored state, valid graph before the first draw. |
| Undo, allow deferred reconstruction, undo again | No invalid image or node-tree reference. |
| Redo, allow reconstruction, undo again | Same safety guarantee; no stale result publication. |
| Cross-set stroke creates a source on another set, then undo | Every affected set is consistent, not only the active set. |
| Rapid alternating undo/redo before settling | Correct final revision, no lost or duplicated strokes. |
| New layer halfway through a long session | Full original workload completes without removing this action. |
| Alpha stroke, save, reopen, undo/redo where applicable | Authored alpha values and modes remain correct. |
| Switching set, editor, or file while work is pending | Jobs cannot update deleted, replaced, or unrelated state. |
| Resource-generation failure during a transition | Explicit failed/stale state with a valid previous display or safe fallback, not a crash. |

Compare authored document state and evaluated results to a fresh, uncached reference. Check scopes, masks, channels, and hierarchy, not just stroke count. No temporary gesture preview should become saved authoritative content.

Run the full original 500-gesture workload at least three consecutive times with its original seed and settings after the fix, then run a second recorded seed. Short reduced cases remain diagnostic tests, not replacements. Confirm the smallest deterministic crash regression and the full workflow against the installed candidate ZIP as well.

**S2 gate:** No native crash in the previously failing workload, exact undo/redo results, valid immediate post-undo display, and bounded resource lifetime. If the original combination still crashes, this gate is FAIL even when reduced runs pass.

---

## 7. S3: Resolve the current shader-sampler compilation failure

### Direction prompt

> Reproduce the current 4K/eight-layer fixture's Metal compilation failure, identify the resource binding that exceeds the actual backend capability, and fix or safely handle it without dropping required channels or destructively changing the authored material.

Use the same candidate, fixture, backend, and shader settings that produced the reported “too many samplers” error. Preserve the compiler log and generated material description before changing anything.

Inventory unique sampled resources and their roles: channel images, masks, generated leaves, brush previews, alpha textures, and other inputs. Separate repeated references to the same image from genuinely distinct sampling requirements. Compare fresh compilation, first stroke, warmed painting, and post-undo reconstruction.

Do not assume that the 4K dimension is the cause, or that fewer layer rows necessarily mean fewer sampled resources. Do not hard-code an assumed universal sampler limit from one error message.

Optimize based on that inventory. Reuse equivalent bindings where semantics truly match. Derived intermediate composition or compatible scalar packing is acceptable when it preserves filtering, coordinates, precision, color-space interpretation, and editable source data. Splitting nodes into groups is not evidence that backend resource use decreased; check the compiled result.

When a material cannot compile, detect and report that state. Keep the previous valid material or an explicitly identified safe fallback. An endless “updating” state or a blank material must not count as successful compilation.

Regression coverage must include the formerly failing fixture, the 2K/25-layer fixture, multi-set painting, first strokes, alpha painting, and undo/redo. Compare valid results to the authoritative evaluator. Test a deliberately over-budget configuration for truthful failure handling.

**S3 gate:** The specific failing fixture compiles and paints on the supported reference backend with its required content intact. Its sustained 4K test then executes. An older build's successful 4K probe does not satisfy this gate; unavailable hardware remains NOT RUN.

---

## 8. S4: Make sustained painting responsive without hiding work

### Direction prompt

> Profile the now-stable uninterrupted workflow. Reduce latency in the paths that dominate real sessions, including fresh layers and Undo. Keep visual correctness, full-resolution results, and the complete stroke history intact.

### 8.1 Preserve the established workload and targets

Use the same 2K/25-layer, three-set fixture with 500 gestures, undo every 25, a layer added at gesture 300, saves, scope changes, alpha strokes, and immediate subsequent gestures. Keep the 4K workload separate so its result cannot be inferred from 2K.

The existing targets remain:

| Metric | Target on the pinned 2K reference fixture |
| --- | --- |
| Input to actual visible material change, warmed strokes | p95 at most 100 ms |
| Ordinary warmed release to fully current result | p95 at most 250 ms |

The earlier hardening report recorded approximately 441 ms and 984 ms respectively. Those are reported baselines, not new independent measurements or acceptable replacement thresholds. [R1]

Report correctness and timing separately. A correct stroke that takes too long is not lost data, but it also does not pass the responsiveness target. Do not manufacture PASS by raising targets after measurement.

For ordinary one-stroke undo/redo, use **1 second p95 to fully correct visible/current state** as a proposed additional acceptance budget for this pass, recorded before optimization. This is a new target, not an already established measurement. Report structural layer changes and file loading separately. If the budget cannot be met, leave an explicit performance failure rather than silently redefining the operation.

### 8.2 Measure what the artist actually sees

Record per gesture the target, selected channels, event timestamps, captured revision, preview publication, observed pixel change, and final committed revision. Correlate the same gesture across these stages.

Separate warm and fresh layers, early and late session, event pacing, single-set and cross-set strokes, and drawing while the previous result is still pending. Report p50/p95/p99/max and sample counts. Expose expensive Undo, Redo, save, reopen, and export operations separately from ordinary stroke timing.

Keep pixel evidence and publication/redraw proxies separate. The measurement probe itself adds work: record its polling/capture cost and run a matched low-instrumentation timing pass. Do not simply subtract an assumed overhead from the headline figures.

Classify every watched gesture that showed no detected change. Possible causes include no actual eligible coverage, an intentional no-op, a detector miss, or a genuine feedback failure. Retain their counts and reasons. For positive feedback tests, choose visible contrasting changes in enabled channels; do not rely only on a colored guide line or a revision counter.

### 8.3 Profile and change the costly paths

Investigate first-stroke setup, geometry/reach work per event, preview image readback/upload, repeated stroke-history scans, full-resolution leaf publication, and undo-triggered reconstruction. Separate add-on CPU time, backend work, and diagnostic overhead.

Make invalidation as local as the dependency model permits. Undoing one stroke should not needlessly mark unrelated sets and every channel for complete regeneration. Reuse valid derived state by stable identity and revision, without reviving the lifetime crash.

Retain the latest correct visible preview while a full-resolution replacement is pending. Another gesture must start safely before the previous final leaf is ready. Coalescing superseded work is allowed; dropping authored gestures or enabled channels is not.

Avoid doing unrelated full recomputations inside the interactive path. If a required operation cannot be broken up, report its cost and prevent stale work from repeating it unnecessarily. A faster status label or spinner is not a faster material update.

### 8.4 Memory and long-session quality

Measure current process memory separately from peak process memory. A maximum-resident-size counter cannot demonstrate that current memory has stabilized. Also record live/retired image counts, allocated pixel buffers, pending jobs, and memory attributed to the finite undo history. GPU memory remains NOT RUN when it cannot be measured reliably.

Resource growth must have an explained bound based on live data and configured history. Do not “fix” the crash by retaining every retired image forever, then call the absence of a crash a memory pass.

Measure export from the accumulated-stroke project separately. The reported 162.8-second eight-file export warrants profiling, but do not confuse export time with the ordinary brush latency distribution. Optimize relevant redundant evaluation without changing output semantics.

**S4 gate:** All gestures and permissions remain correct, full results match fresh recomputation, and each timing target has its own PASS or FAIL. When a smaller fixture passes, name that verified envelope explicitly; it does not clear the required reference fixture.

---

## 9. S5: Validate the exact candidate and actual engine output

### Direction prompt

> Validate the same built candidate that contains the fixes. Prove installed-code execution, old-project compatibility, and actual external output where the required editors are available. Do not build another validation framework instead of running the existing one.

### Installed candidate

Build a candidate ZIP without changing the user's installation or publishing a release. Record its full checksum and source/diff identity. Use an empty Blender profile.

Log the loaded extension module's path and hash in package tests. Several historical tests prepend `src/` to `sys.path`; a preceding `--package` installation step does not turn those into installed-package tests. Preserve reusable test helpers, but ensure the application under test actually comes from the installed ZIP when that is the claim.

Execute the critical recovery regressions, minimized undo regression, complete default sustained session, and formerly failing shader fixture against the candidate. Keep source-run and installed-run results separate.

Reopen representative 0.12.2 and pre-hardening 0.13 files. Verify material appearance, legacy stroke reach, new stroke scope, alpha modes, masks, layer/set duplication, and supported smart-material import/export. Keep all 50 built-in recipes unchanged unless an independently reproduced recipe defect requires a separately documented change.

Retain the previous target-selection, cross-set isolation, Fill, finite-padding, normal, color encoding, and export-scope tests. Do not rerecord expected images merely because the candidate produces different pixels.

### Engines and hardware

Use the existing Unreal and Unity URP kit with exports from this exact candidate. Record editor versions, render pipeline, importer settings, texture/mesh hashes, and captures of mipmapped/compressed results.

Validate the diagnostic channel plate, seam object with and without height, difficult atlas, transparency card, and production prop. Compare data interpretation, packing, normal direction, and boundary behavior. Different renderers' beauty images need not be pixel-identical.

If editors are absent, record NOT RUN and the exact command/dependency needed. Do not install large engines, accept licenses, purchase access, or change the user's workstation without separate authorization. Fixture generation alone is not engine validation. Unity URP results do not establish Built-in or HDRP results.

Physical tablet behavior and additional platforms/UI scales remain explicitly unverified unless tested. Their absence does not justify postponing the two safety fixes, but it limits supported-quality claims.

**S5 gate:** Each claimed environment has executed evidence tied to the same package. Unavailable environments remain NOT RUN, not green checkboxes.

---

## 10. Delivery order and stopping rules

| Checkpoint | Deliverable | Do not move the gate by |
| --- | --- | --- |
| A: S0 + S1 | Reproduced and corrected unchanged-file recovery; mandatory transaction regressions | Testing only distinct old/new bytes or calling an excerpt the full transaction. |
| B: S2 | Undo-safe resource lifecycle; full original workload passes | Reducing undo frequency, removing the late layer, or running only 100 gestures. |
| C: S3 | Formerly failing shader fixture compiles and paints | Dropping channels, destructive flattening, or citing older candidate results. |
| D: S4 | Comparable sustained-workflow timings and correctness | Hiding first-stroke cost, omitting missing feedback, or lowering final quality silently. |
| E: S5 | Exact installed-candidate and environment-specific evidence | Running checkout code under an installed-package label or substituting engine scripts for execution. |

Export safety and undo correctness are hard blockers. Complete them before optional optimization work expands. If available time ends, deliver the verified checkpoint and clearly list remaining failures; do not mark the entire plan complete.

A limited test build may be useful while performance or engine checks remain open. A build with the reproduced recovery failure or known ordinary-editing native crash is not a production-safe candidate.

## 11. Test-runner integrity and evidence

Use existing runners and add new regressions to them. Short data-safety and minimized undo cases must be mandatory. The long quality command may remain separately invoked, but its failure must prevent an overall full-quality PASS.

Use these outcomes consistently:

| Status | Meaning |
| --- | --- |
| PASS | The named assertion ran successfully on the recorded candidate and environment. |
| FAIL | An executed requirement failed, including a native crash or missed fixed timing target. |
| NOT RUN | The check did not execute, timed out before exercising its requirement, or lacked an environment. Distinguish timeout failures where the workflow itself exceeded its allowed deadline. |
| LIMITED TO | A narrower named scope is verified. It does not override FAIL for a required broader workload. |

For every run, retain exact commands and effective environment variables, start/end state, exit code, logs, output hashes, native crashes, and metric sample counts. Clear stale report files first. Verify the current run's ID and final completion marker before reading PASS.

Keep the original failing configuration as a first-class test. Make it impossible for a shorter command to overwrite its failed report and appear to clear it.

Required report: `docs/quality-stability-0.13.md`, containing the baseline, source changes, before/after reproductions, lifetime design, transaction decision rules, exact workload definitions, installed-code provenance, timings, and outstanding issues. Name new test files according to the repository's conventions; do not create a parallel testing infrastructure.

## 12. Final acceptance checklist

### Safety and correctness

- [ ] Unchanged originals survive interruption after PREPARED before backup creation.
- [ ] Identical old/new outputs survive every tested pre-commit interruption.
- [ ] Repeated recovery after restoration and failed journal cleanup is harmless.
- [ ] Durable committed output is never rolled back because later cleanup or participant recording fails.
- [ ] Competing files, malformed journals, and incomplete recovery retain safe behavior.
- [ ] The original 500-gesture, undo-every-25, late-layer workload completes without a native crash.
- [ ] The same workload passes repeat runs; the minimized regression also passes.
- [ ] Undo/redo restores exact authored state and valid immediate graph references.
- [ ] Pending jobs cannot publish into a restored or replaced state with an obsolete generation.
- [ ] One gesture remains one meaningful undoable action.
- [ ] Target isolation, permissions, alpha preservation, Surface reach, and legacy behavior retain their contracts.
- [ ] Fill and finite padding regressions remain green.

### Working experience and verified scope

- [ ] The current failing shader fixture compiles and its 4K workflow actually runs.
- [ ] Warmed 2K input-to-visible p95 is at most 100 ms on the pinned reference fixture.
- [ ] Warmed 2K release-to-current p95 is at most 250 ms on that fixture.
- [ ] Ordinary one-stroke undo/redo meets the recorded additional budget or remains explicitly failed.
- [ ] Fresh-layer delays, no-visible-change cases, long-session memory, and expensive operations are reported without omission.
- [ ] Installed ZIP tests prove their module provenance and match the tested candidate hash.
- [ ] Older files, the 50-material baseline, native painting, masks, and exports remain compatible.
- [ ] Unreal and Unity URP each have actual results, or are clearly NOT RUN.
- [ ] No failed workload is converted into a checked completion item through a narrower passing run.

**Definition of done:** Repeated ordinary editing is crash-free, recovery protects existing work even when replacement bytes are unchanged, the required shader fixture works, and responsiveness and external compatibility claims are supported by their own evidence. An honest partial report is preferable to another “everything passed” summary that excludes the failing workflow.

## 13. Source provenance

This plan is based on the prior audit and the supplied repository excerpts, not a new live checkout inspection. Revalidate current paths and behavior in S0.

- **[A1]** `Open_Painter_16ff0be_Audit_Evidence.zip`: README, isolated reproduction scripts, and recorded recovery/padding JSON results. The executed recovery excerpt is not an end-to-end Blender or transaction test.
- **[R1]** `docs/quality-hardening-0.13.md` at `16ff0be`, blob `bd1e6358675922b7dd5194f1c47aa1539d71f0a4`: full/reduced workload results, open undo failure, performance figures, shader failure, and unrun engines.
- **[R2]** `src/open_painter/export/transaction.py` at `16ff0be`, blob `dbf91774805fec042e9518106b4efc7149d41fb3`: commit/recovery ordering and identical-content ambiguity.
- **[R3]** `tests/unit/test_export_transaction.py`, blob `31521ce783316962c7f329599ed65f3584e0673d`: existing payloads and interruption tests.
- **[R4]** `src/open_painter/evaluation/cache.py`, blob `33ba94e84f068f5ab83f374e156d945d067e6ddd`, and `src/open_painter/ui/vector_brush.py`, blob `bacfa0fcd80b335e3870c5acf3990de2a395ffe6`: undo relearn and first-stroke structural synchronization.
- **[R5]** `tests/integration/material_session_ui.py`, blob `8912c220523fa68a650c69a3794d20cf39fe9d4e`: default sustained workload and measurement harness.
- **[R6]** `src/open_painter/export/padding.py`, blob `797bc6e2929d83b227af2e6f77061a7afcd76abd`, and `src/open_painter/painting/strokes.py`, blob `be3618a11bcf2c6755ac02ed5a443815cc2da22d`: completed donor propagation and persistent permission behavior to preserve.

## 14. Explicit non-goals

Do not add more materials, new AI capabilities, signed-HDR height, arbitrary custom-graph portability, another painting engine, a new UI framework, or a Substance feature-parity campaign. Do not spend this pass on GitHub configuration, billing, tags, marketing copy, or publication.

**Finish the safety contract and the ordinary working experience of the add-on already built.**
