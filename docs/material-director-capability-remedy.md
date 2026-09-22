# Material Director capability remedy

Implementation and validation record for `Open_Painter_Material_Director_Capability_Remedy.md`.
Finalized at the user's request on 2026-09-22. The live campaign was stopped before its planned completion. Execution success does not establish visual quality.

## Current frozen build and status

- Product: `improved-product-v3-04`, source SHA-256 `ef159e797072e8860afeade84515976b2a5fa36db5ede66a57e6a6db23094203`.
- Runtime SHA-256: `eb853bf35131a531c66085707e41a4379f45f63732addae1968c68cd2163767c`.
- Current calibration selection: all five cases from attempt-09, recorded in [frozen_selection_09.json](../.blender-test/material-director-calibration/frozen_selection_09.json). It is a deterministic engine track with zero provider calls, excluded from AI inputs and live benchmark results.
- Portable calibration delivery: [selected-calibration09-evidence.zip](../.blender-test/material-director-remedy-delivery/selected-calibration09-evidence.zip), with [README](../.blender-test/material-director-remedy-delivery/README-calibration09.md) and [verification](../.blender-test/material-director-remedy-delivery/calibration09-verification.json). All 877 archive members passed hash checks; 290 standalone PNGs and 131 embedded gallery images decoded. The archive includes the 213 selected artifacts, 263 recapture files, fixtures and matching product source. Its SHA-256 is `5bdd5f00cc019814aba1391fbf37930345f3639779b25fea2d23e923bf2b7b54`.
- Smoke 05 is **INSPECTED**: four complete three-stage workflows and one provider-capacity failure. The full comparison is **STOPPED_AT_USER_REQUEST**: 44 sessions completed all edits, eight failed, two were cancelled during Create, and 36 never submitted an instruction. The planned 90-session comparison is incomplete.
- Independent reviewer: **PENDING**. Artistic ratings: **NOT_SCORED**.

The earlier builds and attempts below are retained history, not the current product or calibration selection.

The campaign is [remedy-v3-paired-01](../.blender-test/material-director-benchmark/remedy-v3-paired-01/manifest.json), manifest SHA-256 `9208d9804dd7b53c5167a13b033b8c9476e790aee672d7696f29441b9e3ee031`. Its 90 unique scheduled sessions use paired baseline/improved waves, followed by the separate correction-disabled control. Configuration SHA-256 is `4e07110a942fe29001fd9ab5297a43a3064d735f2d6ba8d0e24fa47a7a5bb005`. The baseline is the unmodified `2a9ff0d` product archive. The two workers used isolated profiles and the exact frozen smoke-05 runtime. Both workers and their owned Blender/Codex processes have exited; independent ratings and semantic annotations remain pending.

### Final delivery and remaining limits

- [Partial comparison report](../.blender-test/material-director-benchmark/remedy-v3-paired-01/report.md) and [stop record](../.blender-test/material-director-benchmark/remedy-v3-paired-01/operator_stop.json) retain every scheduled outcome. Raw statuses are 44 `COMPLETED`, eight `FAILED`, three `INTERRUPTED`, and 35 `NOT_RUN`. One interrupted session was cancelled before submission, so only two interruptions were active AI attempts; 36 sessions were never submitted. All 30 correction-disabled controls are unrun.
- The eight failures comprise four improved-chair refusals because the requested weave was unresolved, two improved-toolbox local instruction deadlines, and two baseline-toolbox targeting-validation failures. The local deadlines retain their original broad transport labels alongside source-confirmed diagnostic annotations. The deadline sidecar also identifies one unverifiable, pre-submission interrupted record; it is not a provider failure or aesthetic result.
- All 30 primary native sessions ran: baseline delivered 14/15 and improved delivered 10/15. Of the 11 naturally terminal hybrid sessions per build, each delivered 10. These are delivery counts, not quality scores or evidence that one build looks better. The stopped hybrid population and absent correction controls do not support a complete comparison.
- [First-round metadata audit](../.blender-test/material-director-benchmark/remedy-v3-paired-01/first_round_audit.json) and [second-round metadata audit](../.blender-test/material-director-benchmark/remedy-v3-paired-01/second_round_audit.json) found no hidden input, preservation or evidence failures in those 40 terminal sessions. The chair hybrid repeat that committed still explicitly acknowledged unresolved fine weave. Its wood and revision controls were preserved; that does not prove visual compliance.
- [Verified final-smoke portable review](../.blender-test/material-director-benchmark/remedy-v3-smoke-05-portable/pages/index.html) and the verified calibration ZIP above are available. Original partial-campaign images, traces, channels and editable Blender files remain on disk through the comparison report. A full comparison portable review, complete comparison ZIP and provenance companion were **not produced** before the stop; their assembly plans/scripts are not completed deliverables.
- Posthoc gallery fixes distinguish generation permission from observed calls and require evidence validation for a complete export. All seven focused gallery tests passed. The frozen product/runtime and original campaign records were not rewritten by these exporter changes.
- Human ratings are `NOT_SCORED`, reviewer is `PENDING`, and no aesthetic improvement is claimed. Fine weave, native surface appearance, the generic deadline cause label, pending semantic annotations, and broader generalization remain disclosed limitations.

### Reporting limitation found during the frozen campaign

The product's generic request-error handler labels a local instruction deadline as `PROVIDER_TRANSPORT_ERROR`. The actual timeout message, request ID and producing source are retained. A separate [posthoc deadline diagnostic](../scripts/diagnose_material_director_deadlines.py) identifies only source-confirmed local deadlines, binding its annotations to the original result, trace, job and product hashes. Its 13 focused tests passed. The [diagnostic sidecar](../.blender-test/material-director-benchmark/remedy-v3-paired-01/posthoc_deadline_diagnostics.json) preserves the original classification, failed delivery and skipped refinements; it does not alter campaign inputs or frozen files. Observed instruction duration includes tool work and is not reported as provider wait time. This corrects interpretation of the evidence; the frozen product's broad typed error label remains a disclosed limitation.

## Product changes

- Director can construct nine native surface families, exact geometric bands, assigned-face region masks, and constrained revisions. These compile to ordinary Open Painter folders, channels, masks and filters.
- Physical mapping is versioned. New metric materials carry an explicit meters-per-world-unit declaration and provenance; old materials retain their mapping. Unknown size remains unknown. Context reports under-resolved detail.
- Generated sources remain supported through the connected Codex account. Image-backed recoloring preserves the source and uses an editable bounded palette transfer. Source bundles retain channel interpretation and shared coordinates; independent generated images are explicitly uncalibrated.
- Candidate review uses disposable scene, mesh, collection and material copies, with the existing Eevee environment. Director receives front, back, assigned-face views and actual composed channel diagnostics before submitting the reviewed candidate for one atomic commit. One instruction allows three previews and four generated images in total.
- Candidate identities bind the proposal to source pixels, geometry and viewing conditions. Changed or superseded previews cannot approve a different material. Plan remains uncommitted; Explain cannot author candidates. Cancellation and failure discard disposable resources.
- Mask coverage is measured on mesh surface area at mask > 0.5. The coverage solver preserves the placement source and uses an editable monotonic Levels control; effect opacity remains separate.
- Provider failures retain structured causes. Benchmark execution, evidence and quality have separate states, including dependency-skipped refinements and unchanged failure-state evidence.

## Calibration and review policy

There are no supplied human reference materials or selected independent reviewer. Reviewer identity is **PENDING** and independent ratings remain **NOT_SCORED**. Neither Codex nor the implementation agent substitutes for that reviewer.

A separate deterministic authoring track builds calibration materials on the same five sterilized fixtures and fixed Eevee rig. These are engine diagnostics, not human-authored references or AI benchmark results. Calibration materials, authored command lists and evaluator annotations are never sent to Director.

The v3 fixtures declare nominal physical dimensions for the benchmark. They preserve the geometry and rig from v2. These declarations are explicit diagnostic assumptions, not verified measurements of the downloaded assets. The original v2 fixtures and results remain unchanged.

## Verification completed during implementation

- The latest full repository unit run completed 305 checks: 303 passed and two native PNG decoder checks skipped in Blender's bundled Python. Both native checks passed separately in the host runner environment. The eight supplied quality-gate regressions and 13 separate deadline-diagnostic checks passed. Actual Blender surface-control checks also passed.
- 34 existing Blender authoring/transaction checks passed with candidate review explicitly disabled; the enabled review path has separate real Eevee acceptance tests.
- Actual Eevee candidate tests exercised deliberately metallic rubber, returned images and numerical channels, correction to dielectric rubber, one commit, and exact candidate/final/reopened channel-buffer equality.
- Candidate lifecycle checks exercised failed-render cleanup, stale source pixels, changed viewing conditions, Plan/Cancel, Explain, foreign layer IDs, shared-mesh material slots and preview limits. Further actual Eevee tests passed for animation frame/subframe parity, remapped constraints, armature-data cleanup, and invalidation after changes to unmanaged scene geometry and view-layer visibility.
- Region tests verified assignment isolation from actual CAMERA-mode pixels, narrow-part framing, persistent editable membership and geometry-change rejection. Connected-member queries support world, object and assignment-normalized bounds with exact triangle/box intersection and optional normal limits. The visible test identified 23,112 member pixels in a 512 × 282 capture while preserving camera and artist state. Public coverage/channel inspection tests passed with both live scene fields and live edge masks, without changing artist images or documents.
- Metric material tests evaluated equivalent coordinate representations, nonuniform transforms, rotated connected members, scoped patches, imported/generated source interpretation and source-preserving recoloring. A resolved diagnostic weave was compared through native Eevee, CPU evaluation and production PNG export/reimport.
- Evidence-only recovery rendered a previously saved material without connecting Codex or changing the checkpoint. An earlier native Blender crash remains recorded as a separate failed recovery attempt.

These functional checks are not independent aesthetic ratings. Full model smoke runs and the frozen comparison are recorded separately below.

## Live validation

The initial five-case H diagnostic smoke is complete under `.blender-test/material-director-benchmark/remedy-v3-smoke-01`. Its immutable runtime predates the fixes listed below. Every case's images and traces were inspected as implementation diagnostics, without assigning aesthetic grades.

| Case | Create / Refine 1 / Refine 2 | Diagnostic observation |
| --- | --- | --- |
| T01 toolbox | APPLIED / APPLIED / APPLIED | Real generated hardwood source assigned; two actual candidate previews before Create committed. Petrol body and brass appear in the Eevee views; handle is visible from the back. Shader-budget rejections are retained. |
| T02 chair | NEEDS_INPUT / SKIPPED_DEPENDENCY / SKIPPED_DEPENDENCY | Director could not divide connected frame members with different grain directions. Added general geometric region queries; no material was manually repaired. |
| T03 boots | APPLIED / VALIDATION_FAILED / APPLIED | Drying required a native mud surface and a conventional fill splash layer to change together; the latter lacked typed revision support. Added constrained CUSTOM fill patches. Dark seam artifacts led to the UV gutter sampling fix. |
| T04 vase | VALIDATION_FAILED / SKIPPED_DEPENDENCY / SKIPPED_DEPENDENCY | Director saw broken blue bands at UV seams in its actual back candidate image and refused to commit. Fixed derived field gutter sampling; rejected candidates and unchanged failure state remain archived. |
| T05 rocks | APPLIED / APPLIED / VALIDATION_FAILED | Coverage inspection repeatedly read unset scratch scene fields. The read-only tool now waits for its disposable fields; its production regression passes. Pale stone and flat-looking green patches remain diagnostic visual concerns, not a quality pass. |

The old smoke runner labeled T03 and T05 `COMPLETED` at session level despite failed refinements. Those original records remain unchanged; the current runner derives unsuccessful delivery from typed stage outcomes. Failure-state evidence is not scored as a successful material.

Smoke 02 retained five setup-only `UNAVAILABLE` records: the cached access token could not cover the declared session cap. No Blender or AI request ran. The ordinary managed Codex login was subsequently refreshed through `account/read` with `refreshToken: true`; worker copies still receive only access credentials, never a refresh token. A read-only preflight now checks authentication before each wave, leaving later sessions unattempted and resumable if it expires. Smoke 03 then reached the first live tool call on T01 and T02, where new timing bookkeeping exposed an integer-key bug in the harness log redactor. Both owned workers were stopped; their traces and `operator_stop.json` preserve the infrastructure cause. T03–T05 were unattempted. An actual Blender replay of the numeric tool event passed after the fix, including forced terminal-write failures and cleanup. A fresh smoke was required after that correction. The subsequent API audit rejected three silent no-op control combinations: relative period on known-size surfaces, custom mapping frames on unknown-size surfaces, and procedural variation on image content without a detail source. Supported controls passed actual pixel checks. That stage used the now-historical product-only snapshot `improved-product-v3-02`, source SHA `2cd6369808ea02ee49bc2498b9a2521758e320810fb809c08a2244fcac172096`; the earlier `improved-product-v3` snapshot is also retained. The pending full comparison will use separately pinned baseline/improved builds, three independent repeats per case/mode/build, and a declared correction-disabled control.

The final partial-campaign status and delivery limits are recorded above. The user stopped execution before the full comparison, final cross-session audit and complete comparison packaging. Missing human ratings did not block implementation or any live attempt.

### Smoke 04: full live rerun on `2cd636…`

All five cases ran through the actual Codex account. The original records and operational image-inspection notes are retained in `remedy-v3-smoke-04`; none received independent ratings.

| Case | Recorded outcome | Finding |
| --- | --- | --- |
| T01 | Create applied; Refine 1 blocked before submission; Refine 2 skipped | The material's expanded context exceeded 256 KiB. Lossless provider-only factoring now prepares the actual saved Refine context at 249,226 bytes, preserving every value and the original file. |
| T02 | Explicit `VISUAL_REQUIREMENTS_UNMET`; no commit | The fixed atlas did not visibly resolve 1 mm weave, and complete cushion REGION masks leaked bright lines at UV edges. A separate replay of the unchanged proposal proved and removed the REGION sampling defect; it does not upgrade this failed AI run. |
| T03 | `NO_PROPOSAL`; no commit | Two candidate previews were delivered. Codex then completed normally without a proposal or structured inability report. Both dependent refinements were skipped. |
| T04 | All three stages applied | Actual close, back and circumference views show two continuous bands recolored and moved lower together. Input, evidence and scene-preservation checks passed; human quality and acceptance ratings remain pending. |
| T05 | All three stages applied | Actual views show olive recoloring and reduced moss patches. The production tool measured 4.4039% occupied area before reduction, independently of opacity. Appearance remains pale with weak moss contrast; human ratings remain pending. |

The REGION correction extends only disposable sampling buffers. The focused test and unchanged-proposal chair replay preserve raw membership, all owned texels, material documents and geometry. The context correction uses explicit shared defaults only for oversized provider contexts, with an independent lossless reconstruction check; it does not change saved materials or raise the cap.

Host evidence decoding now uses a pinned installed libpng when available, with the strict Python fallback retained. All 134 actual vase evidence PNGs decoded in 6.18 seconds without changing their files. This changes evidence processing time, not AI execution latency or material quality. The full unit run completed 305 checks: 303 passed and two native-decoder tests skipped in Blender's bundled Python; both native tests passed separately in the host runner environment.

### Smoke 05: final frozen product

All five actual Codex sessions are terminal and operationally inspected in [the smoke index](../.blender-test/material-director-benchmark/remedy-v3-smoke-05/smoke_index.json). No authored calibration was sent to Director. T01, T03, T04 and T05 completed Create and both refinements, with input integrity, evidence delivery and scene-preservation checks passing. T02 ended before tools with the provider's `serverOverloaded` / “Selected model is at capacity” error; both refinements were skipped and the unchanged failure state is retained.

- **Toolbox:** the live recolor passes the formerly oversized context. Paint-loss strength changes from 1 to 0.5 with unchanged source signature and source field within 5.96e-8. The appearance still shows repetitive dotted wear and weakly resolved wood grain.
- **Boots:** wet mud becomes lighter and rougher, preserving all three captured effect/source masks. The second revision halves both the native mud parent folder and separate splash layer. A child finish stays at opacity 1 inside its half-opacity parent; both intended components are included.
- **Vase:** the actual close and circumference views show two continuous bands recolored and translated lower together, retaining their widths, gap and gold rim.
- **Rocks:** recoloring preserves both captured masks. Final occupied moss area is 0.601473 of the initial area under the frozen estimator, with zero new support and unchanged opacity. Dampness remains exact. Stone remains very pale and moss lacks convincing surface detail.

These are implementation observations and numeric invariants, not independent appearance or environmental-placement grades. Host evidence normalization took approximately 6–10 seconds for the four completed cases. Hybrid mode allowed image generation; this smoke used native surfaces. Actual generation availability is established separately by the retained smoke-01 hardwood image and its producing Codex runtime/model.

## Pinned calibration evidence

### Current selection: attempt-09

[frozen_selection_09.json](../.blender-test/material-director-calibration/frozen_selection_09.json) selects all five attempt-09 cases on the current `improved-product-v3-04` product pin above. The selection records artifact hashes, source fixtures, protected-scene preservation and authored checkpoint evidence. Its status is `READY`; quality remains `NOT_SCORED` and reviewer `PENDING`.

The [no-edit-recapture-05 report](../.blender-test/material-director-calibration/no-edit-recapture-05/repeatability.json) reloaded each selected source file independently twice and forced channel and effect-mask capture. All five cases passed with **zero maximum channel difference and zero maximum mask difference**; source files remained unchanged. The frozen absolute channel/mask tolerance was `1e-6`. This establishes numeric no-edit repeatability, not autonomous texturing success or artistic quality.

The [offline browser-09 verification](../.blender-test/material-director-calibration/offline_browser_verification_09.json) passed in a fresh nonpersistent macOS WKWebView: **131 embedded images** decoded, with **no external resources**. The verified gallery source SHA-256 is `ce191ba7f79e30bc50ec58157ec7dcf67fbd7a8f84c21db385c37611548d8f44`. This is portable-evidence validation, not a material-quality grade.

### Historical calibration attempts

`material-director-calibration/attempt-04` completed all five deterministic cases on its then-pinned product snapshot. Original geometry, UVs, bakes and the Eevee rig remained unchanged. T03 includes wet/dry variants; T04 includes circumference views and T05 includes its underside. A separate T03 attempt-05 requested TRIPLANAR mapping on the legacy NOISE mask, which does not consume that parameter. Its mask was identical to attempt-04: this was an ineffective authoring change, not a test of world-space breakup. Both attempts remain available.

In attempt-05 the actual wet/dry effect-mask buffers and source signatures were identical. The 241,334 valid samples outside mud had zero color, roughness, metallic or height difference. In occupied mud, mean roughness changed from 0.22577 to 0.78823 and mean color from 0.03166 to 0.11510; height remained unchanged. These are controlled parameter/evaluation measurements, not independent quality ratings.

The chair calibration uses three explicit directional groups within the existing shader budget. Curved joints and shared upright tilt are disclosed approximations. The nominal 1 mm fabric period remains under-resolved at the fixed atlas resolution.

Reopening the five pinned calibration states twice produced zero maximum and mean differences across 48 channel comparisons, 13 effect masks and 13 source fields. A separate native paint-loss control changed strength from 1 to 0.5 while preserving its source signature and source field within 5.96e-8. The measurements distinguish this effect control from the coating layer's unchanged opacity.

Attempt-06 used the supported PROCEDURAL/FBM triplanar field for the boots; its close views show continuous lower-boot breakup instead of the broad chart-shaped transitions. Attempt-07 then authored all five cases on the then-pinned `2cd636…` product. That earlier calibration selection is retained with 213 artifact hashes in `material-director-calibration/frozen_selection.json`; attempt-09 is the current selection.

The attempt-07 boots' wet/dry mask buffers and source signatures are identical. All eight captured channels are unchanged outside the mud across 227,568 valid samples. Within mud, mean roughness changes from 0.30235 to 0.77689 and mean color from 0.03498 to 0.10540; height remains unchanged. The historical all-five `no-edit-recapture-03` check reopened each selected authored file twice and measured zero channel and mask differences.

The attempt-07 portable calibration gallery passed an actual offline WKWebView check: all 85 embedded 1024 × 1024 PNGs decoded with no external resources. Those checks established evidence integrity and repeatability, not aesthetic quality. Its close views showed limitations: the fine chair weave was under-resolved, the wood looked coarse, and the pale rock material had weak moss contrast. These historical observations remain diagnostic and ungraded.
