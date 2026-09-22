# Open Painter Material Director: capability and benchmark remedy

## Direction for the implementation agent

Work on `task/open-graph-0.4.0`. The reviewed baseline is commit `2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e`.

Improve the production Material Director and its existing five-asset benchmark. Do not build another benchmark framework, import another scene collection, switch renderer, or spend this task changing cameras and lights. Keep Eevee mandatory. Preserve the existing frozen fixtures, rendering infrastructure, transaction validation, protected-content checks, and evidence capture wherever they already work.

Implement the complete sequence below. Product improvements must run through the same public Material Director path in normal use and in the benchmark. Do not add benchmark-specific authoring, canned proposals, hidden masks, filename-based material choices, manually repaired AI outputs, or extra artistic hints from the runner. Do not declare success because a tool returned successfully, a material committed, PNG files exist, or tests produced a populated report.

Deliver working material capabilities, regression tests, and a new evidence-backed comparison. A final implementation report must distinguish implemented, tested, failed, and not run. Never invent reviews, generated textures, evidence frames, model IDs, or performance results.

## 1. What was actually inspected

Sources: the uploaded `gallery.html` and code fetched from the pinned branch above. Relevant source files are linked in section 12. This document is an implementation specification, not a claim that the remedy has been implemented or that live texturing has been rerun.

The HTML contains metadata and relative links, not the PNG image bytes. The linked `result.json`, provider traces, and saved Blender files were not supplied with it. Therefore the reported lack of realism is the user's visual observation. This review establishes code-level gaps and the recorded failure path, not a pixel-level diagnosis of unseen materials. The gallery does not embed the product commit, so its producing build must be established from the original manifest before comparing it with the reviewed branch.

### Recorded gallery state

| Case | Execution status | Stages | Recorded primary frames |
|---|---|---|---:|
| Toolbox | COMPLETED | Create, Refine 1, Refine 2 applied | 10/10 |
| Chair | COMPLETED | Create, Refine 1, Refine 2 applied | 10/10 |
| Boots | FAILED | Create RESPONSE_ONLY; refinements NOT_RUN | 1/10 |
| Vase | COMPLETED | Create, Refine 1, Refine 2 applied | 10/10 |
| Rocks | COMPLETED | Create, Refine 1, Refine 2 applied | 10/10 |

These are gallery records, not independently verified file availability or quality passes. The HTML reports 41 primary frames. None of those PNGs accompanied this upload.

The boots failure is explicit: `Create produced no material commit; see provider reply and status`. In `run_session.py`, `completed_turn()` ends the run when Create has no receipt, before the normal post-create capture. The gallery's generic `MISSING` label then covers both the failed creation and its never-executed dependent stages.

Do not assume the boots failed because of segmentation, image generation, authentication, or a shader exception. Read the original `T03/H/1/result.json`, `create_proposal.json`, `create_input.json`, `create_receipt.json`, and `provider_and_tool_trace.jsonl`. The current runner already archives much of this diagnostic information. Surface it instead of repeating the same failed run without a diagnosis.

### Confirmed code-level gaps

1. **No candidate visual-feedback loop.** `session.tool_call()` compiles and validates proposals but returns changes, not a render of the proposed material. `completed()` applies after the provider turn ends. The model can inspect the old state and validate a command list, but cannot inspect that candidate's resulting appearance before finishing that turn.
2. **Quality scores do not gate PASS.** `scoring.score_session()` checks whether the final score exists, but does not compare it with a quality threshold. With the other checks satisfied, a numeric score of zero can take the PASS branch. Earlier stage scores and per-stage acceptance values are not incorporated into that decision either.
3. **Physical scale is normalized away from normal authoring context.** `prepare_fixtures.normalize_model()` resizes the longest asset dimension to two world units. Its original-scale conversion remains in the preparation audit. `authoring.context.capture()` exposes current dimensions and scene unit scale, not that original conversion. Metric-sized material details cannot be trusted without reconciling these coordinate systems.
4. **Target isolation is object-level, not material-slot-level.** `viewport.isolated()` hides non-target objects. If one object contains several material slots, it does not isolate only the faces assigned to the selected texture set. The prompt describes an isolated editable-part view more precisely than this implementation guarantees. This is a general targeting defect, not a proven diagnosis of any particular unseen gallery image.
5. **The authoring interface lacks first-class physical surface controls.** It exposes layers, channels, masks, scalar projection controls and local recipes. It does not expose explicit material-space millimeter scale, a semantic-region authoring workflow, or a bounded candidate-preview tool. Additional node-graph features elsewhere in the application do not automatically become Director capabilities.
6. **The gallery discards useful distinctions.** Missing files, nonexistent checkpoints, dependency-skipped stages, response-only creation, and browser link failures are not presented with a unified cause-specific state model. `visible_complete` means the preview settled and capture completed, not that a reviewer judged the material realistic.

### Existing code to retain

The branch already has frozen Eevee rig validation, ordinary Director operator invocation, provider/tool traces, actual-generation records, state snapshots, channel captures, source checksums, protected-content checks and finalization checks. `cpu_maps.py` also provides a renderer-independent geometric AO preparation path. These are useful foundations. Do not rewrite them merely to rename the benchmark.

The production prompt already warns against excessive noise, incorrect PBR channels, universal grunge and unsupported visual claims. More adjectives in that prompt will not replace missing capabilities.

## 2. Non-negotiable boundaries

- Eevee remains the renderer for input views, candidate previews and result evidence. No Cycles switch or fallback. Preserve the current non-Cycles geometry-map preparation path.
- The harness owns immutable lighting, cameras and color management. Director gets named read-only views, not scene-management tools or arbitrary Python execution.
- Keep the existing five assets. Small generated diagnostic meshes are unit fixtures, not replacements for the real-model benchmark.
- Save the original v2 records unchanged. Revised prompts, units, scoring or view inventories require a new protocol version and comparison ID.
- Normal Material Director use must receive the same capabilities as the benchmark. No special product behavior selected by `T01`, asset filename, object name, or evaluation annotations.
- Preserve existing artist content, geometry, UVs, material-slot assignments, images and transaction safety. Any new semantic selections are editable material masks, not silent mesh edits.
- Never hide a failed attempt by overwriting its records. Diagnostic rerenders must use the saved material state and be marked as recovered evidence, not new AI successes.

## 3. Work package A: trustworthy delivery, evidence and quality gates

### A1. Separate execution, evidence and quality

Represent them as separate fields, not one overloaded status.

Execution examples: `APPLIED`, `RESPONSE_ONLY`, `NEEDS_INPUT`, `VALIDATION_FAILED`, `PROVIDER_FAILED`, `CANCELLED`, `SKIPPED_DEPENDENCY`.

Evidence examples: `CAPTURED_VERIFIED`, `CAPTURE_FAILED`, `ARTIFACT_MISSING`, `HASH_MISMATCH`, `NOT_SCHEDULED`, `NOT_APPLICABLE`.

Quality examples: `NOT_REVIEWED`, `PASS`, `FAIL`, `NOT_ASSESSABLE`.

A no-commit creation can have a successfully captured failure-state image. That is not a successfully textured model. A never-run refinement must not be called a missing successful image. A browser failure to load a previously verified relative PNG must not be reported as a model failure.

Keep old records readable through an explicit versioned adapter. New records must have typed cause codes, rather than deriving control flow by searching human-readable status strings.

### A2. Make every submitted attempt diagnosable

Extend the current state machine rather than introducing a second runner:

1. Persist the neutral checkpoint and exact outgoing request.
2. Persist provider response, proposed commands, validation errors, generation outcomes and receipt IDs as they arrive.
3. On any terminal outcome, save a final attempt record before shutdown.
4. When the Blender context is still healthy, capture the current state and save `failure.blend` for failed creation. Label the image `FAILURE_STATE` or `UNCHANGED_AFTER_ATTEMPT`, not `after_create_success`.
5. Mark dependent refinements `SKIPPED_DEPENDENCY` with the failed stage's ID.
6. If capture itself fails, persist that exception and the last valid checkpoint. Do not replace the original failure with the capture failure.
7. A deterministic evidence-only command may retry capture from a saved checkpoint. It must not reconnect the provider, regenerate a texture, or change the material.

Retain the current behavior of not evaluating revisions on a material that was never created. Do not execute fake refinement prompts just to fill a ten-image grid.

For response-only creation, distinguish an explicit question from an invalid proposal, unavailable capability, provider interruption, or plain non-delivery. A supported request should end with a valid proposal or a structured failure. Do not force an arbitrary successful-looking material when a needed region genuinely cannot be located.

### A3. Make the gallery portable and diagnostic

Extend `collect_run()` to consume execution state before assigning frame state. Display the provider's final response, first failing validation, proposed/committed target counts, and a link to full traces. Show requested generation, actual generated images, assigned sources and reused sources separately. The backend already records actual-generation information; the gallery should expose it.

Provide two explicit export formats:

- A portable review HTML containing embedded preview PNG bytes and essential metadata. Original image checksums remain available.
- A full evidence ZIP containing the original HTML-relative directory structure, manifest, records, PNGs and saved states. Large `.blend` files may be a separately declared companion archive, never a silently broken link.

Validate actual PNG decoding and expected dimensions, not just an eight-byte signature. Verify all advertised local paths and hashes before marking an evidence package complete. Test the portable review by copying it to an otherwise empty directory and opening it offline.

Display execution and quality separately. Use `APPLIED / NOT REVIEWED`, not a green quality-success interpretation of `COMPLETED`. Show Create and final results side by side instead of defaulting exclusively to the final refinement. Include source commit, protocol version, fixture hash and selected provider model in the review header.

### A4. Enforce quality gates

Keep the existing 25/25/20/15/15 scoring dimensions initially. Add a versioned, frozen quality policy. Suggested starting development gate:

- Final overall quality at least 80/100.
- Final material appearance at least 4/5.
- No final dimension below 3/5.
- Every required stage has its own review, passes its explicit acceptance checks, and has material appearance at least 3/5.
- An overall workflow PASS requires successful Create and both requested revisions, complete required evidence, no preservation failure, and no undisclosed artistic assistance.

These are proposed product gates, not universal standards. Calibrate once against a fixed human-authored reference before opening the comparison. Do not lower them after seeing results. A final-only success can be reported separately but cannot erase poor initial creation.

A missing score is NOT_REVIEWED, not zero. A delivered ugly result can receive a low score. A no-output attempt fails end-to-end delivery but has no assessable aesthetic result. Missing evidence must not turn an otherwise measured product failure into an unqualified setup-only exclusion.

For development, one named human reviewer is useful and should be disclosed as a single-reviewer result. The generating AI's own critique is diagnostic input, never the independent final grade. Preserve reviewer disagreement and raw dimension scores.

Report two denominators: all planned/attempted end-to-end runs, and the subset with assessable appearance. Show blocked setup, provider failures and capture failures separately. Do not publish only the surviving attractive outputs.

### A5. Required regression cases

The supplied `test_director_quality_gate_regressions.py` imports the checkout's actual scoring module and exercises synthetic records. It specifies failures for zero final quality, zero appearance despite other high scores, bad Create hidden by good refinements, failed per-stage acceptance, and missing Create review. It also includes positive and preservation-failure controls. It is syntax-checked, not executed against this checkout in the review environment.

Add gallery tests for response-only Create, skipped refinements, render exception, moved artifact, mismatched hash, corrupt PNG, incomplete archive and portable offline display. A failure-state capture must never satisfy the successful-stage acceptance condition.

**A acceptance:** The boots record has an explicit non-delivery reason and dependency skips; a zero-quality synthetic result cannot pass; advertised portable images actually load. No claim of improved texturing quality follows from this package alone.

## 4. Work package B: improve material construction, not just instructions

### B1. Restore a coherent physical material space

Do not rebuild the camera rig. Preserve the normalized fixture coordinates, but carry a verified `meters_per_world_unit` conversion from source preparation into product authoring context and all new physical controls. Include source-dimension provenance and an explicit known/unknown status. The existing audit field assumes a source-unit convention; verify it for these fixtures rather than relabeling that assumption as measurement.

Represent reusable surface controls in physical units: pattern period, grain scale, chip radius, height amplitude and effect distance. Convert them consistently to world and texture coordinates. Scene unit settings and benchmark normalization must not each apply an extra scale factor. An unknown real size must be disclosed; the system must not confidently promise millimeter accuracy from an arbitrary two-unit mesh.

Use a versioned mapping specification. Do not retroactively rescale existing saved materials. Existing documents retain their current appearance unless an explicit migration is requested.

Tests: two coordinate representations of the same physical object, non-unit object transforms, multiple objects with different extents, and independently rotated frame members. A nominal 1 mm pattern must remain 1 mm in all physically equivalent representations. Validate the evaluated pattern as well as its stored parameter.

### B2. Give Director usable region information

Fix target capture to honor each `(object, slot_index)` assignment. Show only the assigned faces in an isolated view, or highlight them over a dimmed context view. Never call a whole multi-slot object an exact view of only one slot.

Include per-assignment bounds, triangle counts, valid material-space coverage and stable opaque region IDs. Existing slots and explicit artist selections can be reliable regions. Geometry-derived candidate regions can use connected components, normals, height ranges and declared attributes, but must expose their provenance and uncertainty rather than asserting semantic truth.

For parts mixed inside a single slot, provide a bounded production workflow to propose an editable mask, preview that mask on the object and confirm its coverage before applying a finish. The mask must survive saving, undo and ordinary manual editing. Evaluator-only semantic labels must not enter this workflow.

Do not solve boots by silently supplying a sole mask in the benchmark fixture. Solve sole targeting as a normal capability, or truthfully report that the unsupported case remains unresolved.

Add a CAMERA-perspective framing regression: the harness uses camera view while current isolated capture changes `view_location` and `view_distance`. Verify small-part framing from actual captured pixels. Do not assume these controls reliably reframe that camera-mode view.

Tests: one mesh with two slots; two objects sharing one set; one object shared by two sets; narrow brass hardware; overlapping front and back surfaces; and a single-slot mixed-material object. Confirm isolation respects assignments without modifying the original scene.

### B3. Add editable surface-level controls backed by the existing graph

Director should express an ordinary material decision without manually reconstructing dozens of fragile low-level edits. Introduce a small set of typed surface recipes and effect controls that compile into the existing Open Painter layers, channels, masks and anchors. Keep that native graph as the source of truth. Do not create a hidden second material system.

Begin with the surfaces required by the current benchmark: painted metal, bare metal, wood, woven fabric, rubber, ceramic glaze, mud, stone and moss. This is a quality-tested set of building blocks, not a request for hundreds of preset names.

Each surface/effect exposes stable controls and identity: region, palette, roughness range, physical detail scale, height/normal amplitude, source IDs, pattern seed, mapping frame, opacity and separately defined coverage. Controls must be visible in the normal UI, serialized, reversible and exportable.

Retain causal separation:

| Surface | Required construction behavior |
|---|---|
| Painted metal | A substrate and dielectric coating; a shared paint-loss mask exposes the metal rather than painting bright speckles over intact coating. |
| Wood | Directional structure in a member-specific material frame; broad color variation and fine pores at different scales; low relief unless the brief asks otherwise. |
| Fabric | A reusable thread/weave structure with controllable physical period; color independent of that structure; no large random rocky bumps. |
| Rubber | Controlled broad shading response and subtle surface detail; mud is a separate spatially masked material effect. |
| Ceramic | A quiet dominant glaze surface; decorative bands defined geometrically rather than as accidental noise. |
| Mud | Appearance parameters independent of coverage; wet-to-dry changes must not require generating a new mask. |
| Stone and moss | Distinct material identities; placement derived from geometry constraints plus bounded variation, not green color noise everywhere. |

Do not begin by adding renderer-specific shading features that the entire pipeline cannot preserve. First prove the existing base-color, roughness, metallic, normal and height paths can reproduce a competent reference material. Any additional shading control needs actual Eevee support checks and matching authoring, preview, persistence and export behavior.

### B4. Use coordinated sources instead of unrelated images

Extend source metadata to record each source's purpose, channels, mapping frame, physical extent, generation/import provenance and revision identity. For an imported coherent PBR bundle, preserve its shared UV transform and channel interpretation. For a procedural surface, share the structural field where appropriate while allowing distinct channel transfer functions. A normal derived from a height field must use that field's coordinate system and physical amplitude.

For generated sources, do not claim independently generated color, roughness and normal images form a physically calibrated bundle. Generated color-only detail can still be useful; label what is approximate. Do not derive roughness or height by blindly reusing color brightness. Do not make every channel use identical noise merely to appear correlated.

Validate generated-source dimensions, actual assignment, texture scale, repeated seams, obvious baked illumination and whether the source improves the material rather than just adding contrast. Automated seam or variance checks are diagnostics, not realism scores.

A material may legitimately use the existing library or procedural controls rather than generation. H means generation is allowed, not that every successful H run proves image generation was used. Preserve the actual-source report. A separately declared generation-specific test can require a genuine generated and assigned source; do not manufacture this evidence for simple analytic bands.

### B5. Make revisions constrained patches

Give surfaces stable IDs and explicit editable parameters. For each requested revision, determine both the allowed changes and invariants.

Examples:

- Upholstery recolor: only its color mapping can change. Weave source, scale, masks, roughness and wood must remain unchanged.
- Twice-as-fine weave: halve the physical period. Preserve color and material assignment.
- Dry mud: preserve the coverage mask and placement; change mud appearance parameters only.
- Half opacity: multiply effect opacity by 0.5, leaving source and coverage fixed.
- Less area covered: solve for a smaller spatial support or threshold with an explicit coverage definition. This is not opacity.
- Move vase bands: translate one shared region-space parameter while preserving widths and gap.

Enforce explicit invariants in proposal validation and commit checks, not only in the model's prompt. Do not treat identical layer names as identity. A revision that replaces a generated source must record that replacement and must fail a preserve-source requirement.

**B acceptance:** deterministic unit fixtures show correct physical scale, true assignment isolation, coherent material controls and invariant-preserving revisions in the existing graph. Demonstrate live-preview and exported-channel parity for the new controls. No asset-specific benchmark branches are allowed.

## 5. Work package C: a bounded Eevee preview-and-correct capability

Add a production candidate-review operation. A proposed tool name such as `preview_proposal` is a new API to implement, not an existing capability.

### Required lifecycle

1. Read the actual material, assigned regions, physical scale and current visual context.
2. Author a complete candidate through the existing constrained API.
3. Validate its schema, scope, protection rules, resource budget and image ownership.
4. Build an isolated temporary candidate using the same Open Painter graph/evaluator path as the final material.
5. Capture fixed Eevee views of the candidate on the actual object, including relevant close-ups and a back view. Return those images and diagnostics to Director.
6. Let Director identify concrete discrepancies with the brief, then issue a bounded correction to that candidate.
7. Repeat at most twice within the same user instruction, with total provider, image, shader and time budgets frozen in advance.
8. Commit the final validated candidate as one user-visible operation with an ordinary undo path. Save the first candidate as evidence as well as the final chosen candidate.

The internal assessment must address material identity, region assignment, physical scale, wear placement, seams, channel misuse and preserved constraints. It cannot certify its own final benchmark quality.

### Isolation and safety

Scratch previews must not overwrite live artist materials. Use temporary bindings and copied object data where rebinding material slots would otherwise modify originals. Read original geometry and current map provenance without allowing the provider to alter them. Keep scratch data out of normal project saves and clean it up on completion, cancellation, timeout and exceptions.

Bind each preview to a candidate digest, geometry digest, source-image digests, view ID and renderer configuration hash. Return those IDs with images. Reject stale preview claims and never apply a candidate validated against an outdated artist state.

Preserve scene-wide atomicity, protected paths and the current stale-draft defenses. If an artist edits the source during review, abort/rebase explicitly instead of overwriting that work. Failure in one scene target must not partially commit the others.

Keep native generated-image budgets aggregated over the entire instruction, including corrections. Reuse sources for slider-only corrections. Do not multiply the four-image limit into a fresh allowance for every internal step. Keep every attempt in the trace; corrections are product behavior, not hidden benchmark retries.

Plan mode can review scratch candidates but still must not commit them. Explain mode remains read-only. Unsupported requests require an honest structured outcome. An exhausted visual-review budget must not produce a false claim that the material was verified realistic.

### Read-only analysis tools

Expose narrowly scoped operations for fixed views, material-channel inspection, effect-mask coverage and candidate comparison. These may return measurements, not arbitrary code execution. The provider must not receive paths to write or tools to change render engine, lights, cameras or unrelated scenes.

The runner currently allowlists seven material tools. Update that boundary for the explicitly implemented production inspection/preview tools and add tests proving scene-management tools remain unavailable. Do not keep the old allowlist by running unlogged side-channel assistance outside Director.

### Required tests

- A scripted provider proposes an obviously wrong metallic rubber surface, receives the actual candidate channels/view, corrects it, and commits once. This is a control-flow integration test, not evidence of live AI intelligence.
- An explicit non-target edit is rejected before preview and before commit.
- Two proposed candidates cannot leak stale images or generated source IDs into one another.
- Cancellation and stale artist state preserve the original document and dispose of scratch resources.
- All targets preview correctly when several texture sets share an object.
- The exact selected candidate renders consistently before and after commit, save and reopen, subject to a declared rendering tolerance.
- A real provider subsequently demonstrates the same workflow without scripted proposals.

**C acceptance:** a real production instruction can see and correct its proposed result before its final commit, with Eevee fixed, ordinary undo intact and all extra work logged. A better-looking unrelated sample image is not evidence of success.

## 6. Work package D: revised use cases and measurable revisions

Create protocol `five_asset_eevee_v3`. Retain the v2 prompts and results as an archive. Do not quietly rewrite their acceptance meaning.

Retain the existing five Create subjects. Remove non-measurable acceptance language from control tests. In particular, 'five years more worn' is not a calibrated physical target, and 'half the amount' cannot simultaneously mean half opacity, half occupied area and half visual contrast.

### T01: toolbox, distinct surfaces and reversible paint edits

Create:

> Texture this toolbox as a used but maintained workshop prop: petrol-blue painted steel body, aged hardwood handle and brass latches. Add restrained paint loss at plausible exposed edges and contact areas, exposing steel beneath. Keep broad panels mostly clean and place subtle grime in recesses. Keep the physical materials and wear effects separately editable.

Refine 1:

> Change only the body's blue paint to burgundy. Preserve the wood, brass, exposed steel, roughness, paint-loss pattern and dirt.

Refine 2:

> Halve the paint-loss effect's opacity relative to its current value. Preserve its source mask, location, pattern and scale. Change nothing else.

Check material identity in the actual Eevee views. Confirm the uncoated metal is exposed through the coating rather than merely painted as a bright decal. Inspect high-contact regions, quiet broad faces and close-up scale. Record source-mask identity and evaluated mask equality across both refinements. Check opacity ratio separately from mask coverage.

### T02: chair, quiet realism and physical weave control

Create:

> Texture this nearly new premium chair with dark walnut framing and burnt-orange woven upholstery. Keep wood grain aligned with the length of each wooden member. Give the fabric a subtle approximately 1 mm repeat, consistent across the seat and back. Do not add dirt, damage, scratches or artificial aging.

Refine 1:

> Change only the upholstery color to deep forest green. Preserve its weave, roughness and all wooden surfaces.

Refine 2:

> Make the upholstery weave twice as fine, changing the physical repeat from 1 mm to 0.5 mm. Preserve its color, roughness and the entire frame.

The metric-size requirement applies only after the fixture has verified physical-scale metadata. Evaluate the normal-distance silhouette and several fixed close-ups. Wood should not resemble flat brown plastic, and fabric should not resemble stone or inflated corrugation. Validate pattern period from material parameters and evaluated surface sampling, not just a changed number in a receipt. Use member-specific frames for grain direction.

### T03: boots, successful delivery and material-state change

Create:

> Texture these work boots as mustard-yellow rubber with matte black soles. Add dark wet mud mainly on the soles and lower quarter of each boot, with sparse upward splashes. Keep the upper shafts mostly clean. Make mud separately editable from the rubber.

Refine 1:

> Dry the existing mud: make it lighter and rougher without changing the mud mask, any splash locations, or the rubber.

Refine 2:

> Halve only the mud effect's opacity. Preserve its source mask, spatial coverage and pattern. Do not generate replacement textures.

First prove a real receipt and correctly assigned materials exist. Then check lower-region concentration against hidden evaluator annotations. The dry-mud turn must preserve its evaluated coverage mask exactly within evaluator tolerance while changing the intended appearance controls. A new attractive but spatially different mud pattern fails the revision test.

The archived boots failure remains a failure. A successful diagnostic or retry gets a new run ID and cannot overwrite it.

### T04: vase, precise bands plus a believable quiet surface

Create:

> Give this vase a warm ivory glazed ceramic surface. Add exactly two narrow cobalt-blue horizontal bands around the full body, centered at 70% and 78% of its height, each 1.5% of its height wide. Add a thin gold-colored metallic rim at the mouth. Keep glaze, bands and rim independently editable.

Refine 1:

> Change only both blue bands to dark red. Preserve their geometry, the glaze and the rim.

Refine 2:

> Move both bands down by 5% of the vase's height. Preserve their width, spacing, color and the other materials.

Measure band position and width in the declared upright object frame. Suggested geometric tolerance: 0.5% of vase height, frozen before testing. Inspect the entire circumference, not just the front. Keep center translation distinct from changing scale. Judge the glaze's reflectance and surface restraint against a reference; do not require gratuitous noise for a clean ceramic finish.

### T05: rocks, geometry-aware area coverage

Create:

> Texture these rocks as pale weathered sandstone with moss mostly on upward-facing sheltered regions and cracks. Leave steep exposed faces mostly clear and undersides almost completely clear. Add subtle damp darkening in deep recesses. Keep stone, moss and dampness independently editable, with consistent physical texture scale across the rocks.

Refine 1:

> Make only the moss color warmer and more olive. Do not change where the moss grows, its roughness, or the stone.

Refine 2:

> Reduce the moss-covered surface area by approximately 40%, keeping the remaining moss inside its existing occupied regions. Preserve the moss color, physical detail scale and environmental placement logic. Do not merely reduce opacity.

Define occupied moss as its evaluated effect mask exceeding a frozen threshold, for example 0.5. Measure surface-area-weighted coverage, not the number of UV pixels. Target final/initial occupied area 0.60 with a preregistered tolerance such as +/-0.06, and permit only a small antialiasing tolerance for new support outside the old mask. Near-zero starting moss coverage must be flagged as invalid for the reduction test, not used to create an unstable ratio.

The color-only edit must preserve the evaluated mask. The coverage edit may change mask thresholds but not the seed or coordinate frame. It must retain the prescribed placement restrictions.

## 7. Evaluation that separates the cause of failure

### Three layers of testing

**Deterministic material tests:** simple meshes and known native material parameters establish that units, masks, normal/height scale, projection and export work. These do not call the model or claim AI success.

**Live artist use cases:** the five actual assets, ordinary text prompts, production Director, no hidden artistic intervention. These measure delivery, appearance and revision behavior.

**Generalization checks:** reuse the same capabilities on an unseen mesh or part arrangement, such as a changed chair-member orientation or another single-slot mixed-material object. Keep evaluation-only region annotations hidden. Label transformed versions of development assets as robustness checks, not genuinely new-asset generalization.

### Calibrate the rendering and material pipeline, without rebuilding it

For each family, have a competent artist author one reference using the same available Open Painter features and the fixed Eevee setup. If the reference itself looks poor, diagnose the evaluator, shader, geometry or display pipeline before blaming the model. If it looks good and the AI result looks poor under the same settings, focus on material decisions and source construction.

Keep source asset textures out of AI context. They may be shown separately as a labeled aesthetic reference, but are not a ground-truth recreation target when the brief asks for a different appearance. A copied source material is not an AI-texturing result.

Do not score quality through image variance, noise quantity, number of layers or the number of generated images. A smooth glaze can be correct, and a very detailed surface can still be wrong.

### Numeric measurements

Use decoded channel buffers and stable document/source identities for revision comparisons. Preserve the existing channel capture and add effect-mask capture where required. Use tolerances calibrated against a no-edit capture of the same state. Do not compare compressed PNG hashes as a substitute for numeric equivalence of two separately rendered views.

For coverage, sample mesh triangles with deterministic area weights and map those samples to evaluated mask coordinates:

`coverage = sum(weight * (mask > threshold)) / sum(weight)`

Declare treatment of invalid UVs and seam boundaries before testing. Do not silently drop difficult faces from the denominator. Image-space segmentation should not be the only evidence for three-dimensional placement.

Track the exact before/after values and source IDs, not just an LLM assertion that a region was preserved. Keep measured invariants distinct from reviewer judgments.

### Preview/evaluation/export parity

The product uses shader-native live authoring, while evidence code also evaluates CPU channel buffers. Do not assume those paths are equivalent for new material controls. Compare live Eevee material appearance, exported/reimported textures in the same Eevee setup, and decoded channel outputs on representative fixtures. Record the actual backend rather than relying on a hardcoded metadata label.

The previous front/back/close views cannot establish an underside claim. Extend the frozen v3 view inventory with an underside view for boots and rocks and a full-circumference check for the vase. These are deterministic read-only evidence views, not permission for Director to redesign the scene. Keep the original standard views for comparison.

## 8. Comparison design and budgets

Do not launch a large live campaign until the failing deterministic regression cases are resolved and one five-case smoke run has been inspected. The smoke run is diagnostic, not the published benchmark.

For the complete comparison, run the pinned baseline and improved build on identical v3 fixtures and prompts:

`5 cases x 2 builds x 2 generation modes x 3 independent repeats = 60 sessions`

Each session contains Create and the two predefined user revisions. Internal preview/correction iterations are additionally counted and archived. Native mode disallows image generation; hybrid mode allows it. Show actual source use rather than assuming mode labels establish generation.

Use a fresh fixture and conversation for each independent session. Pin provider model ID, runtime, product build, library contents, protocol, input manifests, fixture hashes and source-generation configuration. Balance execution order across builds. Keep all repeats, not only the best. Do not expose previous benchmark answers to the model as demonstrations.

Where possible, keep total wall-clock and image-generation limits equal across builds. Report actual extra tool calls and latency from visual correction. A better result that uses more time can still be useful, but that cost must be visible. Add a controlled correction-enabled/disabled comparison on the improved build to determine whether gains come from its feedback loop rather than merely changed recipes.

Old v2 results are not a valid numerical baseline for revised v3 prompts or acceptance criteria. Rerun the old product under v3, or label the comparison unavailable. Do not silently backport new authoring intelligence into the baseline.

## 9. Failure routing

| Observation | First place to investigate | Do not do |
|---|---|---|
| No material commit | Archived provider reply, proposal attempts, validation results and receipt log | Repeat blindly or invent a success screenshot |
| Whole object has one finish | Assignment-aware context and semantic-region masks | Change the benchmark to pre-mask the answer |
| Wood/fabric detail has implausible size | Physical conversion, projection frame and evaluated pattern period | Increase image resolution indiscriminately |
| Different parts look like the same plastic | Surface controls, roughness/normal behavior and chosen sources | Add universal grunge or flattering lights |
| Color edit moves scratches | Source IDs, seeds, mappings and revision constraints | Accept a new pattern because it looks better |
| Preview looks right, export looks wrong | Shader/CPU/export parity | Grade only the good viewport |
| Material applied, evidence capture failed | Saved state, capture logs and evidence-only recovery | Mark the model's texture as aesthetically bad or discard the product outcome |
| Evidence is complete but appearance poor | Candidate construction and visual-feedback loop | Improve gallery styling and call it a capability fix |

## 10. Exact implementation sequence

1. Archive existing runs and establish their producing build. Add scoring and gallery regression tests first. Fix truthful terminal states and quality gating.
2. Correct physical-unit metadata flow and material-slot-aware target capture. Add the corresponding deterministic tests without changing artist-owned geometry.
3. Implement surface-level controls and constrained revisions on the existing editable graph. Prove unit and preview/export parity.
4. Implement the bounded production candidate-preview-and-correct loop, including cancellation, stale-state protection and one-undo commit behavior.
5. Freeze v3 prompts, annotations, views, budgets and review policy. Run the five-case smoke test, inspect every result, then execute the preregistered comparison.
6. Ship the complete portable review and evidence inventory with separate execution, evidence and quality results. Report failures and untested claims explicitly.

Complete all six steps as one remedy effort. Do not substitute another plan document for implementation, and do not declare the task finished after repairing the missing-image labels.

## 11. Definition of done

The implementation is complete only when:

- Eevee remains mandatory and Director has no scene-management authority.
- Response-only or failed creation is reported truthfully, with its cause and dependent stage outcomes visible.
- No completed-stage evidence link is silently broken, and portable review images load offline.
- Low scores and failed stage acceptance can no longer yield a full workflow PASS.
- Physical pattern size and assignment-specific targeting are established by deterministic tests.
- The actual product can inspect and correct candidate materials before final application.
- Artist constraints survive recoloring, pattern resizing, state changes and spatial edits.
- Human reviewers can compare baseline and improved results under identical conditions, including initial outputs rather than only final edits.
- Every claimed quality improvement points to actual recorded material results and reviewer evidence.

Do not claim guaranteed photorealism or broad generalization from five assets. The target is demonstrated, repeatable improvement on these defined workflows, with a clear record of remaining failures.

## 12. Source map

All repository links below are pinned to the reviewed commit. Statements about the gallery derive from its embedded JSON and JavaScript, particularly the T03 record and its `render()` function. The associated metadata audit is included in the remedy bundle.

- [Production session, proposal validation and post-turn commit](https://github.com/caspianmoon/open_painter/blob/2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e/src/open_painter/ai/session.py)
- [Director context and offered tools](https://github.com/caspianmoon/open_painter/blob/2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e/src/open_painter/authoring/context.py)
- [Writable authoring schema](https://github.com/caspianmoon/open_painter/blob/2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e/src/open_painter/authoring/schema.py)
- [Current viewport isolation and framing](https://github.com/caspianmoon/open_painter/blob/2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e/src/open_painter/ai/viewport.py)
- [Existing production prompt](https://github.com/caspianmoon/open_painter/blob/2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e/src/open_painter/authoring/material_director_prompt.md)
- [Existing recipe retrieval and expansion](https://github.com/caspianmoon/open_painter/blob/2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e/src/open_painter/authoring/assets.py)
- [Shader-native authoring lifecycle](https://github.com/caspianmoon/open_painter/blob/2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e/src/open_painter/evaluation/shader_preview.py)
- [Live runner and no-commit termination](https://github.com/caspianmoon/open_painter/blob/2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e/tests/benchmarks/material_director/run_session.py)
- [Runner input and delivery checks](https://github.com/caspianmoon/open_painter/blob/2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e/tests/benchmarks/material_director/runner.py)
- [Scoring and verdict logic](https://github.com/caspianmoon/open_painter/blob/2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e/tests/benchmarks/material_director/scoring.py)
- [Fixture cleanup, partitions and normalization](https://github.com/caspianmoon/open_painter/blob/2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e/tests/benchmarks/material_director/prepare_fixtures.py)
- [Non-Cycles geometric AO preparation](https://github.com/caspianmoon/open_painter/blob/2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e/tests/benchmarks/material_director/cpu_maps.py)
- [Fixed Eevee rendering, snapshots and channel capture](https://github.com/caspianmoon/open_painter/blob/2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e/tests/benchmarks/material_director/capture_evidence.py)
- [Gallery collection and generic missing-frame classification](https://github.com/caspianmoon/open_painter/blob/2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e/scripts/benchmark_material_director_gallery.py)
