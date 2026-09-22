# Open Painter Material Director: use-case-based AI texturing benchmark

**Prepared:** 22 September 2026  
**Repository inspected:** `caspianmoon/open_painter`, `main`, `8a62636f5ae58512ba3fb43a2a2581b9d1cbb9fa`  
**Execution status:** NOT RUN. This is a complete test specification, not a results report. The source asset pages and relevant code were inspected. Model files have not been imported into Blender, UV-validated, or packaged here. No live AI generation has been run.

## 1. The question this benchmark must answer

Can an artist give Material Director a normal art-direction brief and obtain a convincing, correctly placed, editable PBR material on a real model, then revise it without breaking work that should stay unchanged?

A successful tool call, valid proposal, populated layer stack, generated image, or persuasive explanation is not sufficient. The material on the actual mesh must satisfy the brief in multiple views, remain editable, survive reopening and export, and offer value over a simple preset workflow.

The scope is hard-surface props, furniture, patterned products, environmental dirt and natural assets. Passing these cases is not evidence of skin, hair, subsurface scattering, transparent materials, character UDIMs, or every possible game-art workflow.

## 2. What the current code actually exposes

The checked implementation captures texture-set or scene context, optional model-view images, mesh-map previews and a reference image. It provides library search and editable proposal tools. Its writable schema includes layers/folders, channel settings, masks, scene fields, filters, anchors, library assets, and generated-image assignment. It is not an unrestricted modeling or UV-editing API. [C1][C2][C3]

Create/Refine can commit a validated draft after the turn; Plan leaves a draft for review, and Explain cannot propose material changes. Scene proposals inspect their target materials and compile a coordinated change. The code also includes target-state checks and request cancellation. These are implementation observations, not proof that every path works on artist assets. [C1][C2]

The integration test excerpt inspected sets up a cube, constructs known proposals and uses synthetic generated-image data. That is valuable transaction/orchestration coverage, but it does not establish whether a live AI can produce good textures from a brief. Keep those tests and add this benchmark rather than replacing them. [C4]

Do not give credit for imagined capabilities. Bake/setup/export steps below are evaluator operations unless the tested UI demonstrably exposes them to the Director. Do not claim an autonomous render-and-refine loop merely because an operator sends another prompt with a fresh view.

## 3. Free model set

All six selected model pages offer Blender downloads and list CC0. Poly Haven explicitly permits commercial use and redistribution of its CC0 assets. Its website renders, copy and branding are a separate category, so distribute asset files or your own renders rather than copying website presentation images. [L1]

Download the **Blend / 2K** package from each exact asset page below. The 2K choice is a download convenience, not permission to reuse the supplied material. The benchmark itself uses new 2048 x 2048 output maps. The displayed polygon figures are approximate site values, not measured import results. Record actual mesh statistics during preparation.

| ID | Exact model and source | Approx. triangles shown by source | Test value |
|---|---|---:|---|
| A01 | [Metal Toolbox](https://polyhaven.com/a/metal_toolbox) | 14K | Coated metal, exposed metal, wood, small hardware, localized wear. |
| A02 | [Modern Arm Chair 01](https://polyhaven.com/a/modern_arm_chair_01) | 9K | Wood versus upholstery; multiple texture sets; directional grain; selective revisions. |
| A03 | [Wine Barrel 01](https://polyhaven.com/a/wine_barrel_01) | 11K | Stylized art direction, wood grain versus hoops, end grain and UV seams. |
| A04 | [Antique Ceramic Vase 01](https://polyhaven.com/a/antique_ceramic_vase_01) | 9K | Clean surfaces, exact band count and placement, glaze, metallic trim, continuous wrap. |
| A05 | [Rubber Boots](https://polyhaven.com/a/rubber_boots) | 35K | Rubber versus mud; lower-region placement; wet-to-dry revisions without repainting. |
| A06 | [Rock Moss Set 01](https://polyhaven.com/a/rock_moss_set_01) | 63K for set | Irregular geometry, orientation-aware masks, stone scale and cross-object consistency. |

**Reference asset:** [Fabric Pattern 05](https://polyhaven.com/a/fabric_pattern_05), also CC0. Download the **Col 01 color texture**, use a fixed 1K copy named `R01_fabric_reference.png`, and record its checksum. Attach it only as a visual reference in MD-08. Do not use the website's example render or directly import its complete PBR set into the output.

Keep the original asset name, creator credit, source URL, download date, license, exact files and SHA-256 checksums in the asset manifest. Even where attribution is not required, retain provenance for a reproducible public benchmark. These are source-page selections, not a claim that their downloaded Blender files have already passed Open Painter's importer.

## 4. Fixture preparation: prevent the asset from doing the AI's work

### Neutral starting state

Prepare one immutable `.blend` fixture for each asset and one for the combined scene. Keep the downloaded originals elsewhere and never open them in a scored AI session.

1. Import/append only the intended geometry with automatic script execution disabled. Keep useful modeled geometry and the existing UV layout. Remove source cameras, lights and irrelevant objects. Record and freeze the intended modifier result; resolve texture-driven displacement without leaving hidden source images behind.
2. Remove **all original appearance data** from the scored file: material node trees, image textures, normal/height maps, roughness/metalness maps, vertex colors used for appearance, source dirt masks, and packed source images. Replace material appearance with neutral gray. Preserve source material-slot assignments, but record exactly what boundaries and names they reveal.
3. Verify normals, UV presence, overlaps, mirrored islands, material-slot assignments, transforms, physical dimensions and texel density. Fix setup defects once, before freezing fixtures, and save the preparation script/log. Never silently repair the asset differently for different AI runs. A broken fixture is a setup defect, not automatically an AI failure.
4. Register the intended Open Painter texture sets. Bake the geometry maps needed by the test from the **neutral benchmark geometry**, not the original PBR textures. Record which maps are fresh, absent or unsupported. Do not manufacture a high-poly source or reuse the original artist's fine-detail normal map as AI work.
5. Use consistent neutral studio lighting, a fixed camera set and fixed color management. Save front, back, left, right, top/underside where visible, and a three-quarter view. Use a second fixed light arrangement only for relighting checks. Do not optimize lighting per result.
6. Add non-target guard geometry/materials for set-scoped runs. Include a small pre-existing artist-authored layer or protected component for preservation checks. In the combined SCENE fixture, the background/floor are ordinary Blender materials outside the registered editable sets; every captured Open Painter set is an intended target.
7. Save checksums of geometry, UVs, transforms, target and non-target material state, decoded image buffers and the `.blend` fixture. Use semantic snapshots for material comparisons; ignore volatile UI metadata, history IDs and timestamps.

**Important source-specific contamination traps:** the vase page supplies masks; the boots page supplies a dirty mask and dirty PBR maps. Remove these just as thoroughly as base color. They would otherwise answer the hardest part of the test in advance. [A4][A5]

### Do not hide semantic-targeting limitations

For the primary run, preserve only the source's actual material boundaries. Do not paint wood/metal/mud masks or add named semantic vertex groups for the AI. A texture set is not automatically the same thing as a semantic region.

When the source has one combined set, test whether Material Director can produce the requested regional treatment with the information and tools it really has. An honest limitation is safer than an incorrect all-over material, but it still means the requested capability was not delivered.

After scoring a targeting failure, an evaluator may run a separately labeled **ASSISTED diagnostic** with named region groups or manually prepared masks. Keep it out of the headline success rate. Report the difference as: “works on prepared regions” versus “understands and textures the asset without those regions.”

## 5. Experimental design

### Two separate modes

**P: Native editable authoring.** Turn Generate Surface Textures off. Use the normal Director workflow and editable Open Painter materials, masks, generators and local recipes. Existing library resources are allowed and must be recorded; this is not a procedural-purity contest.

**H: Hybrid allowed.** Turn Generate Surface Textures on. Generated raster inputs are allowed inside editable material components. Keep the same brief, physical scale and protected regions. Record whether generation actually occurred; the toggle being on is not evidence that an image was generated. Report the subset with actual generated inputs separately. When generation is unavailable, label the generative capability BLOCKED rather than crediting a procedural fallback as successful image generation.

A hybrid result is not failed merely for containing images. It fails editability when requested components cannot be revised independently without replacing unrelated work. Do not average P and H together to hide a weak mode.

Run **8 use cases x 2 modes x 3 independent repeats = 48 core sessions**. Each session has the exact Create prompt and two predefined Refine prompts, so at most 144 user instruction turns. Follow-up edits are part of one session, not independent repetitions.

Use a fresh fixture and cleared Director conversation for every session. Run the same exact selected AI model/version throughout a comparison and log its returned identifier, provider/runtime version, exposed seed settings, texture-generation availability, hardware, Blender version, Open Painter build, render/evaluator backend and material-library version. Do not use a drifting “account default” without logging what it resolved to. Where randomness cannot be controlled, record it as uncontrolled.

Randomize the case order within each mode and alternate modes across repeats. Keep the run order in the manifest. Do not pick the best of three; keep and score all three.

### Fair intervention rules

Use the public UI and live provider path. Do not handwrite the Director's proposal, substitute generated images, patch its commands, or texture the model through a separate script. Fixture setup and read-only evidence capture can be automated.

Use Create, then the two exact Refine prompts. Enable Send Current 3D View and Send Mesh Maps; fix and record the input camera at each turn. Refresh the view after the prior result settles. Attach the reference only for MD-08. The AI may repair its own validation errors within its normal turn; record those attempts.

No manual artistic rescue or unrecorded extra prompting is allowed in a core session. If the first turn fails, preserve that failure and continue only where a meaningful current state remains. Never insert a successful hidden baseline between turns. A valid proposal that never becomes a visible, completed material is a delivery failure.

Predeclare operational limits: suggested wall-clock caps are 10 minutes per single-asset turn and 15 minutes per multi-set/scene turn, with a 30-minute total cap per session. These are benchmark stopping rules, not promised product speeds. Record provider wait, local application, preview settling, baking/export and active operator time separately. Confirm the caps on a non-scored pilot before the run matrix and freeze them; never adjust limits to rescue a specific failure.

### Controls

For every case, prepare a no-AI baseline using the same clean fixture and installed library: an artist selects sensible existing presets and makes ordinary basic adjustments. Give that baseline the same art brief, source disclosures and reference restrictions. Record its output, active time and elapsed time.

Also produce an artist-directed benchmark using normal Open Painter controls, with a predeclared active-work budget such as 20 minutes per case. The original downloaded texture is **not** ground truth because these briefs deliberately request different finishes. If a qualified artist baseline cannot be obtained, say so and limit conclusions to the preset comparison.

The live runs may not depend on a passing score from Open Painter's own unit tests. Unit tests, mocked-provider integration tests, the no-AI baseline and live visual results are separate evidence categories.

## 6. The eight use cases

Append this common instruction to each Create prompt:

> Use native editable Open Painter layers, masks, generators and channel controls. Do not replace the model with an image or change geometry, UVs, lighting or cameras. Apply the material changes; a written description alone is not a result.

The initial material and both refinements must be captured separately. The requirements below are test expectations, not claims that the current implementation can satisfy them.

### MD-01: A convincing, editable hero prop

**Asset:** A01. **Scope:** SET, or asset-only SCENE when the source has multiple registered sets.  
**Fixture to prepare:** `A01_toolbox_neutral.blend`  
**Use case:** An artist needs a close-up workshop prop, not a generic grunge material.

**Create prompt**

> Texture this toolbox as a used but well-maintained workshop prop. Give the body satin petrol-blue painted steel, the handle warm oak, and the latches brass. Put small paint chips on exposed body edges and around the latch contact points, exposing steel beneath. Keep most broad panels clean. Add restrained dirt in recesses. Keep paint, exposed steel, wood, brass, dirt and wear independently editable. Do not change the mesh, UVs, camera or lighting.

**Refinement 1**

> Change only the painted body from petrol blue to burgundy. Preserve the wood, brass, exposed steel, roughness, and the exact locations of chips and dirt.

**Refinement 2**

> Reduce the visible rust or oxidation amount by half without moving the existing wear pattern or changing the paint, wood, brass or chip shapes. Do not add rust to satisfy this request when none was created.

**Acceptance checks**

1. The body, handle and latches have distinct materials in the correct places.
2. Intact paint does not inherit the exposed metal response; exposed steel and brass remain visually distinct.
3. Wear is concentrated on requested edges/contact regions, not uniformly distributed over every surface.
4. The burgundy revision changes only paint color; protected material buffers and chip placement remain unchanged.
5. Turning off the wear/dirt components reveals a coherent clean material, not missing color or geometry.

**Typical failure:** One noisy material everywhere; metal-looking wood; a recolor that regenerates the complete surface; convincing prose but no applied material.

**Editability proof:** Toggle dirt and wear separately, change paint color, then restore the saved final state.

**Fixture/diagnostic note:** After recording an unassisted targeting failure, repeat separately with evaluator-authored region groups. Label that repeat ASSISTED; it cannot replace the original score.

### MD-02: Furniture with clearly different wood and textile surfaces

**Asset:** A02. **Scope:** SCENE in an asset-only scene.  
**Fixture to prepare:** `A02_chair_neutral.blend`  
**Use case:** An interior/game artist needs coordinated frame and cushion materials plus safe client revisions.

**Create prompt**

> Create a premium contemporary chair with a dark walnut frame and burnt-orange woven upholstery on the cushions. Align the wood grain with the length of each frame member. Make the upholstery soft and matte, with a restrained woven structure that does not sparkle or turn into stone-like bumps. Keep it clean and almost new. Treat every editable part of this chair. Do not change its geometry, UVs, camera or lighting.

**Refinement 1**

> Change only the upholstery to deep forest green. Keep its weave, scale and roughness exactly as they are. Leave every frame material unchanged.

**Refinement 2**

> Make the upholstery weave twice as fine without changing its color, overall roughness or the walnut frame. Keep the result free of visible aliasing at the normal viewing distance.

**Acceptance checks**

1. Every chair texture set is treated, without silently leaving one neutral.
2. Fabric and wood are distinguishable without relying only on hue.
3. Wood direction is appropriate for individual structural members.
4. The color-only revision preserves the frame and the textile pattern.
5. The scale revision changes repetition/detail scale rather than replacing the fabric with a different material; subpixel fibers are not required to be visibly resolved.

**Typical failure:** Leather/plastic instead of woven cloth; wood grain covering cushions; one frame member left gray; dramatic new lighting used to sell the result.

**Editability proof:** Independently change upholstery color and scale, and confirm all frame channels are untouched.

**Fixture/diagnostic note:** Preserve source material-slot boundaries. Do not add new cushion masks before the primary run.

### MD-03: Stylized PBR rather than automatic photorealistic grunge

**Asset:** A03. **Scope:** SET, or asset-only SCENE when required.  
**Fixture to prepare:** `A03_barrel_neutral.blend`  
**Use case:** A game artist needs a stylized coastal-adventure prop with deliberately restrained detail.

**Create prompt**

> Texture this barrel for a stylized coastal adventure game. Use warm pale wood with broad, readable grain and muted dark-blue painted metal hoops. Keep the surface clean, with sparse, broad hand-painted-looking color variation rather than photorealistic dirt or fine noise. Run the side grain along the staves and use a different end-grain treatment on visible end faces. Keep the wood and hoops separate. Do not bake a directional light or cast shadow into the base color. Keep the mesh and UVs unchanged.

**Refinement 1**

> Make the wood color variation about half as strong and keep the broad grain readable. Preserve the hoops, material boundaries and grain direction.

**Refinement 2**

> Change only the painted hoops to muted terracotta. Keep the wood, roughness structure and all existing pattern locations unchanged.

**Acceptance checks**

1. The result reads as the requested stylized art direction rather than the original scanned appearance.
2. Wood and hoops remain separated on the front, rear and end views.
3. Side grain and end-face treatment differ appropriately.
4. The first revision reduces visual noise/contrast without flattening everything to a solid color.
5. The hoop recolor leaves all wood buffers unchanged.

**Typical failure:** Default rust/grunge despite the clean brief; giant mismatched triplanar patches; directionally baked highlights; wood grain on metal.

**Editability proof:** Expose or locate the parameters for wood variation and hoop color; modify each independently.

**Fixture/diagnostic note:** Use this case to separate useful library composition from a single default preset. Do not demand novelty for its own sake.

### MD-04: A precise design that must wrap around a curved object

**Asset:** A04. **Scope:** SET, or asset-only SCENE when required.  
**Fixture to prepare:** `A04_vase_neutral.blend`  
**Use case:** A product artist needs a clean ceramic finish and an exact, revisable decorative layout.

**Create prompt**

> Give this vase warm ivory ceramic glaze. Add exactly two thin cobalt-blue horizontal bands around the upper third of the body, with a clear gap between them. Add a narrow metallic gold rim at the mouth. Keep the interior ivory and the bottom contact ring unglazed terracotta. The bands must continue around the entire vase, including the back, without a visible seam. Keep the glaze, bands, gold and unglazed foot independently editable. Do not change geometry or UVs.

**Refinement 1**

> Change only the two blue bands to deep brick red. Preserve their widths, spacing, positions, the gold rim, and the ivory glaze.

**Refinement 2**

> Move both decorative bands downward by five percent of the vase height, maintaining their exact widths and spacing. Do not move the gold rim or change any other surface.

**Acceptance checks**

1. Exactly two continuous bands are present at the requested initial height.
2. The bands wrap correctly on the rear and across UV boundaries.
3. Gold is confined to the narrow rim; the interior and contact ring follow the brief.
4. The recolor does not redraw or move the bands.
5. The placement revision moves the band centers by 5% of measured vase height, with a proposed tolerance of 1% of height; band widths and gap remain stable.

**Typical failure:** Three bands; front-only decoration; mirrored or drifting pattern; entire vase metallic; band edits flattening the glaze stack.

**Editability proof:** Independently adjust band color/position and rim appearance, then restore the final checkpoint.

**Fixture/diagnostic note:** The source asset supplies mask textures. None of those masks may be present in the primary test fixture.

### MD-05: Mud is a material, not just a brown stain

**Asset:** A05. **Scope:** SET, or asset-only SCENE when required.  
**Fixture to prepare:** `A05_boots_neutral.blend`  
**Use case:** A game artist needs localized environmental wear and a wet/dry variant without losing placement.

**Create prompt**

> Make these boots mustard-yellow rubber with matte black soles and black top rims. Add dark wet mud on the soles and irregular splashes restricted mainly to the lower third of each boot. Keep the upper shafts clean. The rubber must remain recognizably rubber and the mud must have its own color, roughness and subtle surface relief. Do not add leather grain, rust or metallic effects. Keep the material regions and mud independently editable. Do not change geometry or UVs.

**Refinement 1**

> Make only the mud look dry: lighter, less glossy and slightly chalky. Preserve its current coverage and splash shapes exactly. Leave the rubber and black parts untouched.

**Refinement 2**

> Reduce the mud layer opacity to half its current value without changing the mud mask, color, roughness or the boot materials.

**Acceptance checks**

1. Yellow rubber, black components and mud remain visibly distinct.
2. Mud coverage is concentrated in the lower region and does not spread across the clean shafts.
3. Wet-to-dry changes surface response as well as color, while preserving the mask.
4. The opacity revision halves the relevant layer opacity or equivalent compositing control without rebuilding the mask.
5. Both boots are treated, with plausible variation rather than accidental identical mirrored stains.

**Typical failure:** The original downloaded dirt mask reappears; mud exists only in base color; dry-mud instruction changes the whole boot; glossy upper shafts become metallic.

**Editability proof:** Toggle mud off and inspect the intact rubber; adjust mud roughness without changing coverage.

**Fixture/diagnostic note:** The source includes dirty textures and masks. Remove dirty_diff, dirty_mask, dirty_arm, dirty normal/roughness, and all other original images before testing.

### MD-06: Natural surfaces with geometry-aware distribution

**Asset:** A06. **Scope:** SET, or asset-only SCENE when required.  
**Fixture to prepare:** `A06_rocks_neutral.blend`  
**Use case:** An environment artist needs a coherent rock set that is not covered by one tiled image.

**Create prompt**

> Texture these rocks as pale weathered sandstone with restrained mineral variation. Put dark green moss mainly on upward-facing ledges and in sheltered cracks. Keep most steep vertical faces bare and the undersides free of moss. Make recessed damp areas subtly darker and less rough than the dry stone. Use a consistent real-world scale across the rocks but avoid identical repeated patches. Preserve geometry and UVs. Keep stone, moss and dampness independently editable.

**Refinement 1**

> Change only the moss to a warmer olive green. Preserve its exact coverage and the stone and dampness materials.

**Refinement 2**

> Reduce only the moss opacity by half. Preserve its mask and the underlying stone, dampness and detail scales.

**Acceptance checks**

1. Stone, moss and damp regions are independently represented.
2. Distribution follows the specified surfaces in top, side and underside inspections.
3. The collection has coherent material scale without conspicuous cloned patterns.
4. The recolor preserves the moss mask and all non-moss components.
5. The opacity edit does not regenerate moss coverage or damage the underlying rock material.

**Typical failure:** Green sprayed evenly everywhere; moss on the underside; giant noise unrelated to object size; seams hidden only from the input camera.

**Editability proof:** Toggle moss and dampness independently; check that their masks and parameters remain separate.

**Fixture/diagnostic note:** Use all six rocks for the standard set test. Verify the source orientation and ground convention during fixture preparation; do not rotate rocks between repeats.

### MD-07: Coordinated scene art direction without making everything the same

**Asset:** A01, A02, A04. **Scope:** SCENE.  
**Fixture to prepare:** `A07_workshop_scene_neutral.blend`  
**Use case:** An artist needs several props to belong to one environment while retaining their individual materials.

**Create prompt**

> Texture all editable props in this scene as a coordinated, realistically aged coastal workshop. The toolbox has faded teal painted steel, a wood handle and brass latches. The chair has dark brown wood and faded tan fabric cushions. The vase has dirty ivory ceramic glaze. Use restrained dust and wear appropriate to each material, with a coherent age and saturation level across the scene. Do not make them share one universal grunge material. Preserve the floor, background, camera, lighting, geometry and UVs.

**Refinement 1**

> Make the entire editable prop collection feel better maintained by reducing dust and dirt opacity by half. Preserve all base colors, material identities, wear masks and noneditable scene objects.

**Refinement 2**

> Change only the toolbox paint to muted burgundy. Preserve the chair, vase, wood handle, brass, all wear patterns, and the floor and lighting.

**Acceptance checks**

1. Every registered target material receives an appropriate result, including all chair parts.
2. The objects look coordinated without losing distinct metal, wood, cloth and ceramic identities.
3. The scene-wide dirt edit is consistent but does not recolor base materials.
4. The toolbox-only revision leaves every chair and vase material buffer unchanged.
5. No partial scene application is accepted as success; errors and stale targets must be reported without corrupting the previously committed state.

**Typical failure:** Only active object textured; all objects share identical noise; hidden set left gray; incorrect props changed by the second revision.

**Editability proof:** Inspect each set and independently toggle its dirt controls; check scope-limited recolor preservation.

**Fixture/diagnostic note:** The floor and background use ordinary Blender materials and are not registered as editable Open Painter texture sets. All captured editable sets are intended targets for Scene Create.

### MD-08: Reference-driven reconstruction with editable color and pattern

**Asset:** A02. **Scope:** SCENE in an asset-only scene.  
**Fixture to prepare:** `A08_chair_reference_neutral.blend`  
**Use case:** An artist provides an appearance reference and later changes its pattern without repainting.

**Create prompt**

> Use the attached fabric image as an appearance reference for this chair upholstery. Recreate its main palette, windowpane grid and woven character as an editable material; do not paste or import the reference as the final texture. Make the large grid cells approximately 30 millimeters wide on the seat, with consistent scale on the back cushion. Give the frame dark walnut. Keep pattern color, background color and grid scale independently editable. Do not change geometry, UVs, lighting or the camera.

**Refinement 1**

> Change only the grid-line color to cream. Keep the fabric background, weave, grid spacing, walnut frame and all roughness unchanged.

**Refinement 2**

> Double the grid-cell width to approximately 60 millimeters without changing either fabric color, the weave or the frame.

**Acceptance checks**

1. The result follows the actual supplied reference, not merely the word fabric.
2. The initial large grid cells are about 30 mm and become about 60 mm; proposed size tolerance is 20% at fixed physical scale.
3. Grid lines, background and weave can be controlled separately.
4. The reference bitmap is not silently assigned or copied as final surface content.
5. The color and scale revisions preserve the other components and all frame materials.

**Typical failure:** A plausible but unrelated textile; direct reference reuse; meaningless color knobs on a flattened bitmap; changing line color changes the entire cushion.

**Editability proof:** Change grid-line color, background color and pattern scale independently with normal Open Painter controls.

**Fixture/diagnostic note:** Use a downloaded CC0 color texture as the reference, not a copyrighted website example render. Keep that reference outside the local asset library.

## 7. Evaluation after every session

### What to capture

Save the initial gray view, each instruction, settings/sharing summary, actual model ID, input-image manifest, tool-call log, validation errors, proposal, committed change receipt, timings, and all visible status messages. Preserve failed attempts and generated-but-unused images where the user's sharing policy permits. Redact account credentials, tokens and personal paths before publishing logs.

At Create, Refine 1 and Refine 2, save the actual material state, normal Open Painter layer/mask views, base color, roughness, metallic, normal and any other used channels, plus standardized multi-view renders. Give final renders anonymous IDs for scoring. A layer-list screenshot is evidence of structure, not evidence of quality.

Capture one normal-use viewing distance and one close-up at the target resolution. Do not require millimeter-scale fibers to be individually visible when the fixture's texel density cannot represent them. Assess material scale and aliasing rather than rewarding excessive bump detail.

### Editability, undo and persistence

After saving the scored final checkpoint, perform the listed editability probe with ordinary controls. Prove that wear, dirt, decoration or pattern color/scale are separable as required. Record the controls used and the component buffers affected. Restore the checkpoint before subsequent evidence capture.

Use Undo Last AI Change after the second refinement. Check that semantic material state and decoded image pixels return to the first-refinement checkpoint, not merely something visually similar. For a scene transaction, inspect every affected set. Reopen the saved final checkpoint afterwards; this benchmark does not depend on the add-on exposing an AI-specific redo command.

Save the final `.blend`, close it, disconnect the provider, and reopen it in Blender. Verify the same material, packed generated inputs, layer/mask controls and standard views without regeneration or access to temporary request-cache files. Do not delete a user's real provider cache to manufacture this test; use an isolated benchmark profile.

### Export and independent reconstruction

Export the channels used by the final material with a documented game-ready PBR preset. Reimport them onto a copy of the neutral geometry in a new Blender file using a plain PBR shader, with **no Open Painter graph, temporary image cache or procedural dependency**. Match color management, tangent convention, channel packing and normal handedness to the recorded export settings.

Compare this reconstruction to the settled Open Painter result under the same fixed lights and cameras, then under the second light arrangement. Inspect seams, tiny islands, normal orientation, roughness and metallic behavior, and distant/mipmapped views. Do not expect arbitrary node graphs to survive export; require the appearance intended for the chosen output to be baked faithfully, or require an explicit unsupported-feature report.

Run the same final stack at 4K once per use-case/mode on a declared fixed repeat (repeat 1), **without rerolling AI**. Record memory, sampler/evaluator failures, preview time and export changes. This is a resolution robustness check, not another 16 independent generative successes.

A clean Blender reconstruction verifies a baked handoff, not Unreal or Unity compatibility. Only claim a particular engine is validated after the output is imported and inspected in that engine; otherwise mark that engine NOT TESTED.

### Preservation and placement measurements

Compare material state and decoded channel buffers, not whole rendered images, for “leave this unchanged” requirements. Lighting bounce and anti-aliasing can alter renders even when a protected object's material is identical. Whole `.blend` hashes also change for harmless metadata.

For protected sets/components, require identical semantic values and image buffers where an exact change was forbidden. For evaluated output comparisons, use the same evaluator, coordinates and precision. A proposed tolerance is 1/255 per normalized channel on at least 99.5% of the protected valid samples, excluding predeclared UV-padding/antialias boundary bands. Record the exceptions. A clearly visible unauthorized edit still fails even when it occupies less than 0.5% of the object.

Use evaluator-only region annotations to quantify semantic placement. Keep those annotations out of the AI context. Prefer surface-area-weighted samples across the mesh rather than allowing unusually large UV islands to dominate. Record spill outside the intended region, missed region coverage, seam defects, band counts and measured band/grid positions. The exact scoring regions must be frozen before AI results are viewed.

## 8. Safety and workflow scenarios

Run these eight scenarios once in each mode, separately from the 48 aesthetic sessions: **16 scripted robustness scenarios**. Some contain multiple actions; they are not additional independent texture-quality samples. Use the same real runtime, except deliberate fault injection where explicitly noted and separately labeled.

| ID | Action | Expected behavior |
|---|---|---|
| R-01 | Remove the active UV map in a disposable copy, then request a material. | Explain the missing prerequisite or use a genuinely supported safe path. No fake successful UV-textured result, damaged mesh or unrelated edits. |
| R-02 | Remove a required map, or modify geometry so a map becomes stale, then request edge wear. | Diagnose freshness/availability, use a valid live alternative, or report the limitation. Do not silently present stale placement as current. |
| R-03 | Edit the target material while a request is in flight, or after a Plan draft is ready. | Reject the stale draft or require a rerun. Preserve the user's intervening change. |
| R-04 | Switch the active texture set while a request is in flight, then attempt to apply. | Do not redirect edits to the newly active set. Require returning to the original target or rerunning where appropriate. |
| R-05 | Stop a request during optional image generation and then start a new one. | The stopped turn cannot apply late; the next request is not contaminated by old images or responses. No orphaned material changes. |
| R-06 | Lose the provider connection before completion; separately inject a compile/apply failure in an isolated harness. | Distinguish provider and local errors; preserve the last committed state; no partial scene transaction. Label the injected branch as a robustness test, never a live AI quality run. |
| R-07 | Restrict scope to one layer/folder containing an artist change and protected siblings, then request a change. | Affect only allowed components and preserve the protected artist work. An honest refusal is better than silently escaping scope. |
| R-08 | Run Plan, inspect without applying, then discard; run Explain on the same state. | Neither operation changes the material. Plan is explicitly unapplied; Explain does not claim to have textured the asset. |

A safe refusal can pass a safety scenario while failing delivery of an ambitious art task. Keep those meanings separate. Do not treat an unsupported feature as a successful texture, and do not report provider outages as visual-quality scores.

## 9. Scoring and decision rules

The weights and thresholds here are **proposed product acceptance criteria**, not validated industry standards. Freeze them before testing.

Use three independent reviewers where practical: at least one experienced material/texture artist and one person assessing game-asset usability. Randomize and anonymize multi-view render sets. The AI that created the result cannot be the final visual judge. With only one reviewer, identify that limitation and avoid presenting the score as objective consensus.

Score each dimension from 0 to 5. Multiply by its weight divided by 5. Use the median of reviewer scores for subjective dimensions; structural checks are evidence-based. Rate first-pass visual/delivery quality separately from the final workflow score so a late refinement does not erase a weak first attempt.

| Dimension | Weight | What earns the points |
|---|---:|---|
| Material appearance | 25 | Requested surfaces read convincingly under fixed lighting at normal and close viewing distances; no default-noise substitute for material design. |
| Brief compliance | 20 | Correct colors, style, counts, scale and explicit exclusions, with all requested regions covered. |
| Spatial placement | 20 | Correct semantic regions, wear locations, orientation, boundaries and complete multi-view coverage. |
| Editable structure | 15 | Required components and their color/scale/coverage controls can be changed independently without replacing unrelated work. |
| Refinement fidelity | 10 | Both follow-up instructions are applied precisely while preserving all specified existing work. |
| Handoff integrity | 10 | Undo, save/reopen, offline use and the tested export/reimport preserve the intended material. |

**Anchors:** 0 = absent/unusable; 1 = fundamentally wrong; 2 = major rework needed; 3 = usable only after noticeable cleanup; 4 = production-useful with minor cleanup; 5 = convincingly satisfies the brief and inspections.

A session passes when the final score is **at least 80/100**, no dimension is below 3/5, all of its binary acceptance checks pass, and no hard failure occurs. A crash, unauthorized edit, lost artist work, corrupted save, missing final material after a claimed successful apply, or falsely claimed apply is a hard failure. An explicitly reported provider outage is an availability/delivery failure, not evidence of destructive behavior or a fabricated visual-quality score. A fully flattened result is an editability failure when it cannot satisfy the required independent edits; the mere presence of a raster input is not a hard failure.

For each mode separately, a proposed “useful beta on these use cases” gate is:

- At least **20 of 24 core sessions pass**, with at least **2 of 3 passing for every use case**. No averaging away a consistently broken material class.
- No destructive/cross-target hard failure in any attempted session or robustness scenario. A failed robustness gate blocks the dependable-workflow claim regardless of render quality.
- Mean final scores across the three repeats beat the matched no-AI preset baseline by at least 10/100 on **6 of 8 cases**, or demonstrate comparable quality within 5 points with at least 50% less active operator time on those cases. Declare which value claim is being tested in advance, not after seeing the results.

Report numerator/denominator, all individual scores and the median/range by case and mode. The 20/24 delivery gate uses all scheduled core sessions in its denominator, including unavailable or blocked sessions; additionally report visual quality conditional on completed outputs. Do not manufacture low aesthetic scores for nonexistent outputs, or silently remove unavailable attempts to improve delivery rates. Three repeats per case are a practical screening sample, not a precise reliability estimate. No “best result” marketing conclusions from this matrix.

Keep these metrics separate: first-pass acceptance; acceptance after each refinement; semantic-region delivery; preservation success; offline/export success; availability; provider/local latency; active human time; generation/tool call counts; and actual measurable monetary cost. Where the provider does not expose per-run cost, report it as unknown rather than inventing a dollar figure.

## 10. Failure diagnosis, not just pass/fail

Classify each failure at the narrowest evidenced point:

| Category | Evidence to look for |
|---|---|
| Missing capability | Requested operation is not exposed by the actual tool/schema or requires segmentation the input does not provide. |
| Perception/context | Wrong target, absent or stale map, misleading whole-scene view, unreadable reference, missing material-set information. |
| Planning/instruction following | Input is available but the model selects wrong materials, ignores a protected region, or proposes unsuitable operations. |
| Surface-generation quality | Generated inputs contain baked lighting, mismatched channels, repetition or inappropriate scale. |
| Material construction/evaluation | A plausible proposal creates ineffective masks, wrong compositing, shader/evaluator problems or stalled previews. |
| Transaction/persistence | Wrong scope, partial application, stale-write acceptance, broken undo, missing packed images or changed results after reopen. |
| Export | The in-app result is correct but map conversion, padding, normals or baked handoff is wrong. |
| Infrastructure | Provider/auth/network outage, unsupported image modality, local device limits or benchmark setup error. |

Do not assert a root cause from appearance alone. Link the classification to the relevant prompt, proposal, evaluated buffers, trace and screenshot. After the frozen benchmark finishes, diagnose and fix issues on a separate build, then rerun the same cases. Do not silently patch halfway through and pool those runs into the original result.

## 11. Evidence package and implementation directions

The companion `test_cases.json` is a declarative case manifest, **not an executable runner**. Build the runner around the existing normal UI/runtime and read-only evidence collection. Reuse the existing integration-test helpers for transactions and persistence only where they do not replace the live AI path.

Suggested repository destinations, all new benchmark artifacts rather than claims of existing files:

```text
tests/benchmarks/material_director/
  test_plan.md
  test_cases.json
  asset_manifest.json
  prepare_fixtures.py
  capture_evidence.py
  results/<build>/<case>/<mode>/<repeat>/
    environment.json
    input_manifest.json
    prompts.json
    provider_and_tool_trace.jsonl
    proposal.json
    receipt.json
    before_state.json
    after_create_state.json
    after_refine_1_state.json
    after_refine_2_state.json
    preservation_checks.json
    final.blend
    views/
    channels/
    export/
    export_reimport.blend
    scores.json
    report.md
```

The evaluator can automate asset hashing, geometry/UV checks, camera setup, screenshot/render collection, buffer diffs, export and report assembly. Human visual judgment remains required. Keep real provider calls opt-in and outside routine unattended CI to avoid surprise account usage. Do not send personal files, proprietary client meshes or credentials; this plan uses public CC0 test assets.

The final report must answer, by material class and mode: What did it deliver on the first attempt? What improved after refinement? What still required artist help? Did it understand regions or merely use supplied segmentation? Does it beat basic presets? Are the results editable, stable and usable after export? What is still untested?

**Completion criterion:** deliver every run's evidence and a capability matrix, including failures and unavailable runs. Do not substitute a demo video, a successful unit-test count, or a curated gallery for this report.

## Sources

Source pages and code below were checked while preparing the plan on 22 September 2026. Code citations are pinned; web assets can subsequently change. Implementation descriptions above are based on code inspection rather than execution.

- [C1] https://github.com/caspianmoon/open_painter/blob/8a62636f5ae58512ba3fb43a2a2581b9d1cbb9fa/src/open_painter/ai/session.py
- [C2] https://github.com/caspianmoon/open_painter/blob/8a62636f5ae58512ba3fb43a2a2581b9d1cbb9fa/src/open_painter/authoring/context.py
- [C3] https://github.com/caspianmoon/open_painter/blob/8a62636f5ae58512ba3fb43a2a2581b9d1cbb9fa/src/open_painter/authoring/schema.py
- [C4] https://github.com/caspianmoon/open_painter/blob/8a62636f5ae58512ba3fb43a2a2581b9d1cbb9fa/tests/integration/material_director.py
- [L1] https://polyhaven.com/license
- [A1] https://polyhaven.com/a/metal_toolbox
- [A2] https://polyhaven.com/a/modern_arm_chair_01
- [A3] https://polyhaven.com/a/wine_barrel_01
- [A4] https://polyhaven.com/a/antique_ceramic_vase_01
- [A5] https://polyhaven.com/a/rubber_boots
- [A6] https://polyhaven.com/a/rock_moss_set_01
- [R1] https://polyhaven.com/a/fabric_pattern_05
