# Open Painter 0.13: Next-Step Correctness Hardening

**Purpose:** Fix the concrete failures found in the 0.13.0 audit, then finish the outstanding product-quality checks.  
**Audited baseline:** `02c360cc31a75c1897bf343d5555e4be09e3be40`, on `task/open-graph-0.4.0`.  
**Companion evidence:** `Open_Painter_0.13_Audit_Reproductions.zip`.  
**Scope:** The add-on itself. No GitHub administration, billing, release publishing, marketing, or another UI redesign.  
**Status:** Implementation instructions, not a certification that the fixes have passed.

## 1. The next step

Harden the existing 0.13 implementation. Do not restart the previous remedy plan and do not create alternative painting or export systems.

Three concrete outcomes come first:

1. **Export failure cannot damage a previously usable export or overwrite a competing file without permission.**
2. **The brush changes exactly the displayed target and explicitly permitted surfaces, including after changing selection or disabling cross-set painting.**
3. **Fill Empty Space actually fills every empty texel when a valid donor exists.**

Then verify uninterrupted painting, recovery, and the actual engine output. Those checks are distinct from implementing the fixes.

A passing script is evidence only for the behavior its assertions exercise. A test that checks a new linked layer was not created does not establish that an existing linked layer stayed unchanged.

## 2. Master direction prompt

> Continue the current Open Painter 0.13 candidate. Read this document and the accompanying audit reproductions before editing. Confirm the checked-out commit and local modifications; the audited baseline is `02c360cc31a75c1897bf343d5555e4be09e3be40`, not the older main-branch implementation.
>
> Preserve the existing UI structure, editable stroke model, material recipes, native single-channel and mask painting, shader evaluation, and export resolver. Fix the existing paths rather than creating parallel replacements.
>
> First reproduce the export and padding failures against the actual application modules. Convert them into regressions. For the painting findings, create real Blender reproductions before claiming either a defect or a fix. Then implement the hardening phases below in order.
>
> Prioritize data safety and target correctness over timing improvements. Do not hide defects by disabling normal workflows, silently changing old project appearance, weakening tests, swallowing exceptions, or marking skipped checks as passed.
>
> Keep changes confined to these failures and dependencies necessary to fix them. Do not add new materials, AI features, a new UI framework, signed-HDR height, or arbitrary node-graph serialization.
>
> Use isolated Blender profiles and disposable fixture projects. Do not install into the user's active Blender, modify their projects, commit, push, tag, or publish a release without a separate instruction. Building and testing a candidate ZIP is in scope.
>
> Finish with a requirement-by-requirement report separating verified correctness, measured performance limits, and tests that could not run. Implementing a validation script is not the same as executing it.

## 3. Starting evidence and its limits

The following observations belong to the audited commit. Recheck the actual checkout before using them as current findings.

| Finding | Evidence level | Required next action |
| --- | --- | --- |
| Failure during backup cleanup can make recovery preserve one new file, restore another old file, and delete a new file. | Reproduced by isolated tests using the audited `export/transaction.py`. [A1] | Add a durable transaction decision and test cleanup interruptions. |
| A competing writer can be overwritten with `overwrite=False` after the final existence check. | Reproduced by isolated fault injection. [A1] | Use an atomic no-clobber publication operation, not another existence check. |
| Unreadable journal JSON is silently discarded. | Reproduced by isolated fault injection. [A1] | Preserve recovery evidence and fail explicitly. |
| Fill mode leaves 32,768 empty texels without donors in a 512-square atlas containing one 8-square corner island. | Reproduced using the audited donor functions. [A2] | Make full-domain propagation complete and test sparse atlases. |
| The running brush captures its target at invocation while displayed selection can change. | Traced through code; not executed in Blender by the audit. [C1] | Reproduce selection changes without restarting the tool. |
| Disabling cross-set painting may still affect previously linked sets because scope is not enforced per stroke. | Traced through storage and rendering; not executed in Blender by the audit. [C2] | Compare excluded sets' actual pixels after an earlier cross-set stroke. |
| Nearby same-facing surfaces within the depth band can receive paint. | Acknowledged implementation limit with a fixture in the repository. [C3] | Establish a safe default for ordinary surface painting. |
| Sustained-session responsiveness and real engine behavior are not fully established. | Reported performance limits; engines marked NOT RUN. [C4] | Execute the final validation tracks without substituting generated scripts for results. |

The audit archive contains a copied transaction module and extracted padding functions for reproduction. Keep those as historical evidence. New acceptance tests must import the current application implementation and, where appropriate, the installed candidate package, not continue testing the copied broken modules.

---

## 4. H0: Freeze the contract and reproduce failures

### Direction prompt

> Establish the exact baseline and reproduce the audit findings. Preserve unrelated local work. Record what fails before fixing it, including the concrete output or file bytes, not only exception text.

### Required work

Record source revision, local diff, candidate hash, Blender build, Python/NumPy versions, OS, CPU, GPU/backend, RAM, display size, and UI scale. State whether each test imports checkout code or installed ZIP code.

Run the supplied reproductions in disposable directories. Port their assertions into the canonical test suite against the real modules. When the archive is unavailable, reconstruct the cases from the exact scenarios in H1 and H3 rather than omitting them.

Add the two missing interactive reproductions: change targets while the same brush invocation stays alive, and disable cross-set painting after a shared stroke link already exists. Test results, not only internal metadata.

Preserve before-fix evidence. When a suspected failure does not reproduce, report the environment and explain the code path that prevents it. Do not manufacture a failure to satisfy the document.

**H0 completion:** Every item above has a reproducible failure, an explicit unverified status, or a supported explanation of why it is already fixed in the checkout.

---

## 5. H1: Make export publication and recovery genuinely safe

### Direction prompt

> Repair the existing request-level transaction used by textures, target packages, and evaluated bundles. Distinguish incomplete publication from committed output awaiting cleanup. Prove recovery at every durable boundary, including races and cleanup failures. Do not claim a set of independent file renames is globally atomic.

### 5.1 Required observable contract

For one request containing multiple texture sets, maps, manifests, and mesh files:

| Situation | Required outcome |
| --- | --- |
| Failure before a durable commit decision | Restore the pre-request output state, or preserve all recovery evidence and explicitly report why recovery is blocked. |
| Durable commit recorded, then cleanup fails | Keep every new committed output. Retain enough state to finish cleanup later. Do not roll back some files. |
| Replacement disabled and a destination appears concurrently | Refuse to replace that file; preserve its exact bytes. Roll back only changes demonstrably owned by this transaction. |
| Recovery record is malformed, incomplete, or unsupported | Report recovery required. Preserve the journal, backups, and outputs rather than guessing or deleting evidence. |
| Same recovery is invoked twice | The second invocation is harmless. |
| Two exports overlap destination directories | Serialize or safely refuse the competing export. Never recover an export that is still running. |

For arbitrary external readers, multiple ordinary files may temporarily be visible at different publication stages. Do not promise instantaneous multi-file atomic visibility. The contract here is coherent completion or recovery, with a durable record of which outcome was decided.

### 5.2 Explicit transaction state

Use an equivalent of the following state machine inside the existing implementation:

`PREPARING -> PREPARED -> PUBLISHING -> COMMITTED -> CLEANUP_COMPLETE`

Before publication, persist the request identity, all participants, destinations, staged files, original-file metadata, planned backup paths, and recovery actions. Distinguish originally absent destinations from originals moved to backups and files created by this transaction.

After every new output is durably published, persist **COMMITTED before deleting any original backup**. Cleanup after that point is retryable housekeeping, not a reason to undo committed outputs.

If cleanup fails, return a truthful result such as `Export completed; cleanup pending`, including a recoverable diagnostic. Do not return an ambiguous failure that invites an automatic destructive retry.

Recovery must consult the durable decision:

- **Pre-commit:** reverse completed operations and restore the original request state.
- **Post-commit:** retain all new files and finish removing obsolete backups/staging.
- **Unknown/corrupt state:** stop destructive recovery and preserve evidence.

Make phase transitions and recovery idempotent. Test interruptions during recovery itself.

### 5.3 Durable journals, multiple folders, and concurrency

Write journal updates through temporary files and atomic replacement where supported, with appropriate file and directory synchronization for the tested filesystem. A durable commit decision must not exist only in Python memory.

An export spanning different set folders remains **one request**. Use a discoverable coordinator or equivalent decision record that lists every participating directory. Recovering one participant must not independently undo it while leaving another participant committed. Missing or unavailable directories must be reported, with backups retained until a coherent recovery can finish.

Acquire locks for overlapping destination folders in deterministic order. Recover earlier interrupted transactions before validating and beginning the next request, under those locks. A fixed journal filename without ownership and a live-transaction check is insufficient.

Locks between Open Painter instances do not protect against unrelated applications. The publication operations must still enforce the selected overwrite policy.

Document the filesystem scope actually tested. Process-interruption tests do not by themselves establish power-loss durability or behavior on all network filesystems.

### 5.4 True no-overwrite publication

Do not implement no-overwrite as `exists()` followed by unconditional replacement.

Choose a platform-appropriate atomic no-clobber strategy for a completed sibling staging file, or an exclusive reservation protocol with a documented recovery contract. Verify the primitive on supported platforms and filesystems. If the required guarantee is unavailable, refuse safely with an actionable message rather than falling back silently to clobbering.

If a foreign file appears at the last possible moment, it must remain byte-identical. Recovery must distinguish transaction-owned paths from files created or replaced by another writer. Do not delete a destination solely because an earlier journal expected it to be absent.

Use the same policy for maps, manifests, mesh outputs, and bundles. There must not be one safe exporter and a legacy operator that bypasses it.

### 5.5 Recovery input safety

Validate journal structure, version, transaction identity, participant paths, and backup/staging names before changing files. Restrict recovery to validated participant directories and reject paths that escape through malformed paths or unexpected symlink resolution.

Preserve unreadable journals. Name the affected folder and recovery record in the error. Keep recoverable backups even when the last cleanup action fails.

A pre-existing output modified by an external application after an interrupted export must be treated as a conflict, not automatically deleted or overwritten by recovery.

### 5.6 Mandatory fault matrix

Execute these against the real transaction path. Use distinct known bytes and hashes for each original, replacement, and competing file.

| Test | Expected proof |
| --- | --- |
| Existing A and B, new C; fail cleanup after A's backup is removed. | Recovery leaves all committed new A/B/C, not new A + old B + missing C. |
| Interrupt immediately before and after the durable COMMITTED decision. | Before: original state recoverable. After: new state retained. |
| Failure while generating a later set, map, manifest, or mesh. | All previous output files remain intact. |
| Failure during backup creation, publication, or restoration. | No recoverable originals are discarded; incomplete restoration is explicit. |
| Competing file created after final validation, immediately before publish. | `overwrite=False` preserves the competing file byte-for-byte. |
| Competing file appears during recovery of an originally absent path. | Foreign data is not silently deleted. |
| Journal truncated, invalid JSON, unsupported version, or invalid paths. | Explicit error; journal and recoverable files retained. |
| Two folders in one request; interruption and recovery initiated from either. | One coherent request outcome across both folders. |
| Two processes export overlapping outputs. | No journal collision or recovery of an active transaction. |
| Hard process termination at each publication/cleanup boundary. | Restart recovery satisfies the state contract. |
| Recovery interrupted and restarted; then run again. | Idempotence and consistent final hashes. |

For hard-interruption coverage, terminate a subprocess running the **actual commit implementation**. Do not replace the transaction with a simplified test-only implementation. Injectable failpoints may locate boundaries; they must not change production ordering or cleanup semantics.

**H1 completion:** All entry points satisfy the same safety contract, including no-overwrite races and post-commit cleanup. Every reproduced export failure has a regression that fails on the audited baseline.

---

## 6. H2: Bind painting to the real target, not stale tool state

### Direction prompt

> Make one displayed destination, one immutable gesture target, and one enforced permission scope agree from pointer-down through preview, commit, undo, and export. Fix target changes while the tool stays alive and previously linked sets after cross-set painting is disabled. Reuse the current stroke architecture.

### 6.1 Tool state and gesture state are different

At **every new pointer-down**, resolve the current selection through the shared selection resolver. Capture a new gesture target and brush snapshot then, not only when the tool is first invoked.

Capture at least stable scene/set/layer or mask identity, participating object/slot assignments, enabled channels, brush/material values, source identities, projection mode, scope, and relevant geometry revision.

While idle, the displayed destination follows the current selection. While painting, it describes the captured gesture target. The UI must never advertise B while the renderer writes A.

Use this policy for changes during an active gesture:

- If the artist changes the target, cancel only the uncommitted gesture, restore its transient preview, and state the reason. The next gesture uses the new selection.
- Brush-setting changes apply to the next gesture. Preview and commit of the current gesture use the same captured settings.
- Save retains the existing documented commit-once behavior. Escape retains discard behavior.
- Geometry, assignment, visibility, or mode changes that invalidate the captured target cancel safely rather than redirecting the stroke.

Do not compare only vertex/face counts and object transforms when validating geometry. UV edits, slot reassignment, modifier changes, and vertex motion without count changes can invalidate a captured surface too. Reuse existing dependency revisions rather than hashing an entire dense mesh on every pointer event.

Reject hidden layers, hidden ancestors, missing masks, replaced material slots, excluded faces, and any supported protected-target state consistently with existing painting policy. A stale selection must not quietly paint a fallback layer.

### 6.2 Persist permission on each stroke

A shared `stroke_key` associates stroke history with layers. It must not grant unlimited future permission to paint every set that once shared that key.

For new strokes, record the actual permitted destinations for **that stroke**, using stable, remappable identities. Enforce those permissions when the stroke is evaluated, not just when new linked layers are created.

At minimum:

- A single-set stroke cannot affect another set that was linked by an earlier cross-set stroke.
- Enabling cross-set painting permits only eligible sets/surfaces explicitly reached by that gesture.
- Retaining an old link must not widen a new stroke's permissions.
- Shared history remains visible where it was originally authorized.
- An erase stroke follows the same scope rules as a paint stroke.

Apply scope enforcement to CPU output, live preview, shader stroke resources, mask-stroke consumers, full recomputation, incremental recomputation, and export. Include scope and its revision in relevant cache identities. A stale cached raster must not reintroduce excluded paint.

Do not fix this by deleting shared history, breaking links destructively, or duplicating the complete stroke engine.

### 6.3 Compatibility and remapping

Version the new permission metadata. Define the behavior of legacy strokes with no explicit scope so existing projects retain their previous appearance.

Handle layer/set duplication, save/reload, portable-material import, and copy operations deliberately. Scoped destination IDs must remap to the copied instance instead of referencing the original, disappearing, or expanding to all destinations.

Validate material documents before mutation. Do not infer new permissions from similarly named sets when IDs fail to resolve. Report unresolved targets.

### 6.4 Safe default surface isolation

For new ordinary material strokes, a nearby hidden surface must not receive paint merely because it lies inside the brush radius or depth band.

Use a visible-surface or verified surface-local eligibility rule for the default mode. Depth-based projection can remain an explicit choice, but **Depth Limit alone is not the safety guarantee**.

Design the persistent representation so the accepted surface remains stable after the camera moves, the project is reopened, or the stroke is rendered at another resolution. Recomputing visibility from the user's current camera would move old paint and is not acceptable.

Reuse geometry snapshots and rasterization. Avoid storing a full-resolution depth image per stroke without a measured memory justification. A connected-component check alone also does not prove occlusion protection on a folded or concave connected mesh.

Test front/back sheets, two nearby same-facing objects, disconnected pieces in the same object and slot, an occluded recess, thin shells, and UV seams on one continuous visible surface. Explicit projection mode must show what its broader reach means. Preserve legacy stroke placement rather than silently changing old projects.

### 6.5 Required Blender regressions

Run the same material-brush operator through successive gestures. Do not reconstruct the tool between the actions whose interaction is under test.

| Scenario | Required assertion |
| --- | --- |
| Invoke on A, finish a stroke, select B, paint again without exiting. | Only B receives the second stroke; displayed and stored destinations match. |
| Switch texture set, select through Nodes/component rows, then paint. | The shared selection and actual brush agree. |
| Change target during pointer-down. | Pending stroke discarded, preview restored, previous committed work unchanged. |
| Change settings during pointer-down. | Preview and final output agree on the captured settings; next stroke uses new settings. |
| Create a cross-set A/B link, disable cross-set, paint near B. | B's actual channel pixels remain byte-identical. |
| Repeat with erasing, overlapping geometry, and different resolutions. | Excluded destinations remain unchanged in every consumer. |
| Enable cross-set again and intentionally cross onto B. | Both authorized targets receive the new stroke and coherent feedback. |
| Duplicate/import/reopen a scoped stroke. | Copies remain independent; authorized old appearance survives. |
| Hide a layer/ancestor, change UVs or slots, delete target, change editor or window. | Safe termination or clear refusal, never silent writes elsewhere. |
| Paint on a front sheet over another same-facing sheet. | Rear pixels stay unchanged in the safe default mode. |
| Compare incremental render, full render, and export. | Scope protection holds in all three, not just in the preview. |

Compare lower-layer and excluded-set outputs, not merely sample positions or linked-layer counts. Remove always-true assertions from the scoped tests and replace them with real behavioral checks.

**H2 completion:** Target changes work during a normal uninterrupted session, and new scoped strokes never alter excluded destinations. Legacy appearance, undo, save, and duplication regressions remain green.

---

## 7. H3: Complete Fill Empty Space without regressing finite padding

### Direction prompt

> Fix the existing nearest-donor implementation so Fill Empty Space is complete on sparse atlases. Preserve finite-width padding, deterministic ownership, channel semantics, and the unchanged valid interior. Do not conceal unresolved regions with arbitrary default color.

### 7.1 Contract

When any valid source texel exists, every empty texel in Fill mode must receive an in-range donor that points to a valid original texel.

Occupied texels remain untouched. Zero-valued channels, black color, and transparent material data remain valid sources when geometry marks them valid.

When no valid texel exists, report that filling is unavailable. Do not invent surface data or claim completion.

Keep the existing exact finite-distance band and its tie-breaking contract. Approximate propagation outside that band is acceptable only with a declared, tested distance-error bound. Completeness does not make arbitrary far-away donors correct.

The production path must verify completeness before reporting success. If it cannot complete, fail the export before publication rather than silently returning unfilled regions.

### 7.2 Implementation requirements

Replace the assumption that two fixed coarse passes are sufficient with a full-domain algorithm or hierarchy that terminates only when all resolvable texels have donors.

Pay particular attention to empty fine texels inside a coarse cell that itself contains a seed. A coarse occupied cell still needs a valid representative mapped back to its empty fine texels.

Propagate original donor indices, not previously averaged colors. Validate index range and original geometric coverage. Preserve deterministic tie resolution across runs and processing order.

Do not turn Fill into repeated whole-image brute-force searches on production resolutions. Keep memory and runtime bounded and measured. Share donor/ownership work across channels.

### 7.3 Required tests

The exact audit fixture is a 512-square atlas with a valid 8-square island at one corner. Assert:

`unresolved_empty_texels == 0`

Also test:

- 128, 256, 512, and 1024-square sparse-corner layouts, including the audit's other recorded failures.
- Each corner, isolated edge seeds, one valid texel, central islands, thin diagonals, separated islands, and occupied coarse cells with mostly empty children.
- All valid, no valid, and finite zero-distance cases.
- Deterministic ties, no wrapping across atlas borders, overlapping-UV ownership, and tightly packed islands.
- Exact agreement with an independent brute-force donor oracle for small finite-band fixtures; a separately stated error bound for approximate distant donors.
- Per-channel export checks for Base Color, Normal, Height, packed scalars, and opacity/smoothness alpha.
- Unchanged interior values at equal resolution and the existing coverage-aware downsample tests.
- 2K/4K end-to-end output; an explicitly reported 8K stress run when resources permit.

A donor index of `-1` is valid for an occupied texel or an out-of-range texel in finite mode. It is not valid for an empty texel in Fill mode when any donor exists.

**H3 completion:** The supplied sparse-atlas failure and the new hierarchy-edge cases are fixed; finite padding and resampling retain their established output behavior.

---

## 8. H4: Prove a real uninterrupted painting session

### Direction prompt

> Validate the corrected brush under continuous use, including starting another stroke before the previous one settles. Measure what the artist sees and distinguish visual feedback, final-resolution settling, undo, and save delays. Optimize measured bottlenecks without weakening correctness or hiding stale output.

### Required workload

Reuse the 2K production fixture with at least 25 layers and multiple sets, plus the 4K stress fixture. Run at least 500 gestures with representative short corrections, long drags, erasing, alphas, changing layer/set, toggling scope, cancellation, and undo/redo.

Do not wait for full evaluation after every gesture. Include bursts where another stroke begins before the previous full-resolution result is ready. A lost-update bug can be hidden by an artificial settle-before-every-action test.

Compare the final result against a fresh full recomputation. Include saves/reopens and exact undo/redo verification. Confirm every enabled channel represents the same committed gesture revision and no temporary preview becomes authoritative saved data.

Test a fresh paint layer separately from warmed layers. In particular, a missing image or new channel must not silently omit that channel from the first visible material preview.

### Measurements

| Measure | What to record |
| --- | --- |
| Input to actual visible material change | p50/p95/p99/max, fresh and warm strokes, early and late session. |
| Pointer release to final current result | Separate from initial visible feedback and from whether another stroke can begin. |
| Input loss and destination errors | Lost gestures, missing samples, unexpected channel changes, and writes outside scope. |
| Resource behavior | RSS, GPU memory where measurable, resource leaves, live/retired previews, and steady-state growth. |
| Expensive operations | Undo, redo, save, reopen, export, and their worst pauses, reported separately. |

Keep the previous quality targets visible: at 2K on the pinned reference fixture, p95 input-to-visible at most 100 ms and ordinary warmed release-to-current at most 250 ms. These are quality targets, not a claim that this build already achieves them. Do not loosen them after measuring in order to manufacture PASS.

Report sustained painting separately from the combined session distribution. Save and undo delays are relevant, but should not be mixed into ordinary stroke timing without explanation. Also report first-gesture startup cost instead of hiding it in warm-up.

A publication followed by a redraw callback is a timing proxy, not direct proof that the changed material pixels were visible. Use frame captures or equivalent pixel evidence for representative checks and correlate event/preview/render revision IDs. Missing measurements must not be silently dropped from a percentile calculation.

Use repeatable trajectories at several event rates and separate physical-tablet validation from synthetic pressure attributes. Synthetic events do not establish real tablet behavior.

### Targeted optimization only

Profile the paths that remain expensive: full-resolution image readback/upload, four-channel publication, repeated history scans, and overlapping retired previews. Bound work per UI turn, coalesce obsolete pending work, and retain the latest valid preview until its replacement is ready.

Do not display an older stroke result as current, silently reduce final export resolution, or drop selected channels to meet a latency target.

**H4 completion:** Correctness and responsiveness are reported independently. A safe candidate may have documented performance limits; it must not be called fully responsive while missing the targets. Real hardware or platform checks that did not execute remain NOT RUN.

---

## 9. H5: Run the existing engine kit, do not build another one

### Direction prompt

> Use the delivered Unreal and Unity URP validation kit against exports from the corrected candidate. Execute actual imports and inspect real mipmapped/compressed results. Do not substitute fixture generation, a PNG comparison, or Blender screenshots for engine validation.

Record exact engine/editor versions, render pipeline, platform/GPU, texture importer settings, mesh tangent/normal settings, candidate hash, and exported file hashes. Verify the kit scripts against the installed engine APIs before executing them.

Use the difficult atlas, seam object, transparency card, asymmetric normal marker, and production prop. Include finite padding and Fill, lower-resolution exports, grazing views, and distance/mip transitions.

Check Base Color encoding, scalar-map interpretation, channel packing, normal orientation, combined height/normal contribution, alpha behavior, mesh/material assignment, compression, and UV boundary artifacts.

Compare like-for-like lighting and diagnostic channel views. Do not require beauty renders from different renderers and tone mappers to be pixel-identical. Do require correct data, direction, assignment, and absence of unexplained seam or color errors within the fixture's documented limits.

Tightly packed or undersized islands cannot be guaranteed clean at every mip. Keep those limits explicit rather than making an impossible all-distance promise.

If an engine is unavailable, record NOT RUN, the exact missing dependency, and the command/configuration needed to execute the existing kit. Do not install large engines, accept licenses, purchase access, or alter the user's workstation without authorization. Engine unavailability does not prevent completing H1-H3; it prevents claiming engine-verified output.

**H5 completion:** Actual engine reports and captures exist for each claimed target. Unity URP validation does not establish Built-in or HDRP validation.

---

## 10. Integration, compatibility, and test integrity

Every new acceptance test must be registered in the canonical runner or in a clearly named extended-quality command. Safety and target-isolation regressions are mandatory, not opt-in stress tests.

Test source code and the final candidate ZIP in isolated profiles. Record the package hash associated with every packaged result. Confirm imports actually resolve to the installed extension when the test claims to validate packaging.

Preserve the previous material baselines and representative older `.blend` files. New scope metadata must round-trip through save/reopen, duplicate, and supported material documents. Do not reset authored brush data or alter the 50 built-in recipes to make unrelated tests pass.

Fault-injection tests must execute production control flow. Crash testing must include hard process termination, not only catchable exceptions. Pure donor tests must call the current donor implementation. GUI tests must reuse the running operator for lifecycle tests.

Use these statuses only:

| Status | Meaning |
| --- | --- |
| PASS | The stated assertion executed and passed in the recorded environment. |
| FAIL | It executed and violated the requirement. |
| NOT RUN | It did not execute. A script or test file may exist. |
| LIMITED TO | A specific narrower scope was verified, or a named performance/quality limitation remains. |

Never describe “no recorded failure” as PASS for tests that were skipped or omitted. Do not change expected output merely because the new implementation produces it; tie any baseline update to a deliberate contract change.

## 11. Suggested implementation map

These are responsibility boundaries, not instructions to create all-new modules.

| Responsibility | Existing paths to inspect first |
| --- | --- |
| Transaction state, no-clobber publication, recovery | `export/transaction.py`, `export/plan.py`, `export/exporter.py`, `export/mesh_bundle.py` |
| Interactive destination and per-gesture lifetime | `ui/vector_brush.py`, `painting/gesture.py`, `ui/selection.py`, painting lifecycle handlers |
| Persistent permissions and remapping | `properties/stroke.py`, `painting/strokes.py`, `painting/stroke_raster.py`, layer/set duplication and smart-material serialization |
| Preview and authoritative agreement | `evaluation/shader_graph.py`, `evaluation/shader_resources.py`, `evaluation/shader_preview.py`, stroke resource keys |
| Full-domain padding | `export/padding.py`, `export/exporter.py`, existing ownership/rasterization helpers |
| Regressions and evidence | `tests/integration/export_safety.py`, `material_painting.py`, `material_painting_ui.py`, `material_session_ui.py`, `tests/unit/test_export_padding.py`, `export_padding.py`, canonical runners |
| External validation | Existing `engine_validation/` kit and `scripts/engine_validation.py` |

## 12. Delivery order and stopping rules

**Checkpoint A: H0 + H1.** Prove export safety. Do not trade data integrity for performance or simplify recovery into “delete the journal and retry.”

**Checkpoint B: H2.** Prove the brush's target, permission scope, and default surface isolation with the same running tool. Keep legacy appearance and ordinary native painting working.

**Checkpoint C: H3.** Prove Fill completeness and retain finite padding and resize correctness.

**Checkpoint D: H4 + H5.** Execute sustained-use and external-output checks. Optimize only measured bottlenecks relevant to the scoped work.

Stop expanding scope after these outcomes. Do not turn this into another feature-parity roadmap. Do not start version/release administration to make an unfinished product gate look complete.

A candidate with H1-H3 failures is not ready for a production-quality claim. A candidate with those corrected but unrun engine/tablet checks is an explicitly limited evaluation build, not proof that every ordinary artist workflow is validated. Performance limitations remain visible even when integrity tests pass.

## 13. Required handoff report

Create one concise report, for example `docs/quality-hardening-0.13.md`, with:

1. Baseline and candidate identifiers, package hash, changed files, and the tested environment.
2. Before/after reproductions for each export/padding defect and both painting lifecycle findings.
3. Transaction state and recovery contract, no-clobber mechanism, and the filesystem scope actually tested.
4. Per-stroke scope representation, legacy behavior, remapping rules, and default surface-isolation behavior.
5. Executed commands and test results, plus raw hashes/captures for the critical assertions.
6. Early/late-session performance and resource measurements, actual engine results, and exact NOT RUN items.

Report the following final gate without collapsing it into a single “all green” sentence:

- [ ] Post-commit cleanup failures keep the complete committed export.
- [ ] Pre-commit interruption restores originals coherently across all request folders.
- [ ] No-overwrite publication survives a competing writer at the final boundary.
- [ ] Corrupt journals retain recovery evidence and cause an explicit error.
- [ ] Recovery is idempotent and cannot act on a still-running export.
- [ ] Changing targets while the brush remains active changes the next stroke's actual destination.
- [ ] Active gestures use one immutable target/settings snapshot.
- [ ] Disabling cross-set painting protects already-linked sets' actual outputs.
- [ ] Default surface painting does not reach nearby hidden surfaces.
- [ ] Stroke scopes survive old-project loading, duplication, import, save/reload, and undo.
- [ ] Sparse-atlas Fill resolves every empty texel with a valid original donor.
- [ ] Finite padding, resampling, channel semantics, and valid interior values retain their contracts.
- [ ] Sustained rapid painting loses no strokes and matches fresh full recomputation.
- [ ] Actual visible feedback and final settle are measured separately, with misses reported.
- [ ] Candidate ZIP clean-install and upgrade checks pass without source-checkout dependencies.
- [ ] Actual Unreal and Unity URP output checks are executed, or remain explicitly NOT RUN.

**Definition of done:** No known reproduced data-safety failure remains; the brush cannot silently diverge from its displayed target or permission scope; padding fulfills its selected mode; and the remaining usability, performance, hardware, and engine claims are bounded by actual evidence.

---

## Evidence references

**[A1] Export audit reproductions.** `Open_Painter_0.13_Audit_Reproductions.zip` contains `op13_audit/reproduce_export_issues.py`, `export_reproduction_results.json`, and the audited `transaction.py`. Its recorded Git blob SHA-1 is `3d6dad493a194f747f5e2036186e192d1f6fdedb`.

**[A2] Padding audit reproductions.** The same archive contains `reproduce_padding_issue.py`, `padding_reproduction_results.json`, and unmodified donor functions extracted from the audited `export/padding.py`. Recorded unresolved empty texels: 256-square: 4,096; 512-square: 32,768; 1024-square: 172,032, each with an 8-square corner island.

**[C1] Target lifecycle evidence.** At the audited commit, `ui/vector_brush.py` constructs the gesture/scope at invocation; `painting/gesture.py` retains target IDs; the inspector destination follows current selection. Read together with `ui/selection.py`. This is a code-traced finding until exercised in Blender.

**[C2] Cross-set scope evidence.** At the audited commit, `painting/strokes.py::record(..., sets=...)` restricts creation of links; `painting/stroke_raster.py::channel_rows` reads matching strokes by shared key; `properties/stroke.py` does not persist the proposed per-stroke destination policy. Existing-link output isolation needs a dedicated regression.

**[C3] Surface-isolation evidence.** `tests/integration/material_painting.py` tests the depth band on a nearby parallel surface. `docs/quality-remedy-0.13.md` acknowledges that closer same-facing surfaces still receive paint.

**[C4] Validation evidence.** `docs/quality-remedy-0.13.md` reports the short and sustained-session measurements, pending engine checks, and platform/tablet limits. `tests/integration/material_painting_ui.py` measures publication followed by redraw as a visibility proxy. These are recorded implementation results, not independently rerun Blender or engine results in the audit archive.
