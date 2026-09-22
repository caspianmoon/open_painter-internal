# Open Painter: two-weapon texturing test

## Goal
Can Material Director turn a short art-direction prompt into a convincing,
editable game material on two different weapons?

## Models
Use the included assault rifle and SMG separately. Both are GGBotNet game-art
assets, listed as untextured and CC0 by their source pages. Original model files
are included after this pack has been built; they are not prepared test scenes.
Use SOURCES.md for provenance and SOURCE_MANIFEST.json for downloaded checksums.

## Prepare once, outside the AI test
Import only each weapon's mesh into your existing fixed Eevee test environment.
Keep geometry and genuine material boundaries. Use neutral materials and neutral
part/slot labels. Verify the UVs before testing; repair or unwrap once during
preparation only if needed, record the change, and freeze that prepared copy.
Do not pretend that downloading a model proves its UVs are usable.

Use Eevee only. Reuse the same lighting and exposure for both weapons. Freeze
camera views for each weapon before its first prompt. Preserve physical scale;
frame the camera rather than rescaling the weapon. No Cycles, scene repair or
lighting changes during testing. Disable auto-running scripts in downloaded
Blender files. Do not bring source cameras, lights or World settings into the rig.

## Run the same three prompts on each weapon
Start each weapon in a fresh Material Director conversation from its neutral
fixture. Keep the same AI model and texture-generation setting for both.
Use the ordinary Material Director interface and normal visual context.

### 1. Create
> Texture this like it belongs in a realistic post-apocalyptic game.

### 2. Refine
> This doesn't look realistic enough. Improve it.

### 3. Refine again
> Make it look like it was textured by a professional game artist. Remove anything that looks obviously procedural or artificial.

Do not add material recipes, explain where wear belongs, or manually repair the
AI's material between prompts. The AI must interpret the short art direction.
These fixed follow-ups are a refinement stress test, not a claim that every
initial result is necessarily poor.

## Save evidence
Save the neutral state and every applied result as a .blend. At each state,
capture the same left, right, front, rear and close-up Eevee views. Put screenshots
inside the delivered review ZIP or embed them in its HTML, not external-only links.

If a prompt fails, retain its response/error and the actual failure state.
Label unapplied changes as failed and later skipped prompts as not run. Do not
replace missing evidence with another attempt or silently rerun until it passes.

## Judge the actual result
Compare both weapons side by side, then compare each refinement with its previous
state. Answer: Does it look believable? Does it fit the apocalypse direction?
Do materials and wear respond to each weapon's geometry? Did the revisions improve
it without destroying good work? Can you still edit its materials and effects?

Finish with: **Would I use this in a realistic game without retexturing it?**
Record yes/no and the three most important problems. Shared palette/style is fine;
unrelated copied wear patterns are not. No named reviewer or reference .blend is
required to execute this development test. Do not call it independent human
validation unless a real independent human has reviewed it.

Source meshes are low-poly. Judge texturing separately from silhouette/detail
limitations. This pack downloads assets; it does not execute the AI tests.
