# Material Director AI Texturing Benchmark

This is the revised benchmark requested by the user. It supersedes the eight-case
rendering setup in `Open_Painter_Material_Director_Test_Plan.md`.

## 1. Fixed Eevee harness

Every test starts from a prebuilt benchmark `.blend`, not the downloaded model's original scene.

```text
BENCHMARK_SCENE
├── TEST_MODEL
│   └── mesh objects only
├── BENCHMARK_LIGHTS
│   ├── Key
│   ├── Fill
│   └── Rim
└── BENCHMARK_CAMERA
```

Use Eevee only. The camera, lights, World, color management, Eevee settings and
render settings are created once and locked. They are infrastructure.

Material Director must never be instructed to create, delete, modify, inspect,
troubleshoot or improve them. Its job begins at: “Here is an Open Painter texture
set assigned to clean geometry. Texture it.” It ends at material changes applied.
Nothing involving scene construction.

## 2. Sterilize every downloaded model first

Never open a downloaded `.blend` and tell Codex to work with it. Create a new
benchmark file and bring over mesh data only. Remove all source cameras, lights,
worlds, textures, shader nodes, compositor configuration, render configuration,
helper empties and unrelated collections.

Keep only useful geometry, UVs, material boundaries, object separation,
vertex colors/attributes genuinely part of the mesh, and necessary modifiers,
preferably applied beforehand. Then create fresh Open Painter texture sets.

If a model needs cleanup, it fails fixture preparation, not the AI test. Fix it
before testing or choose another asset.

## 3. One standard Eevee environment

Every model uses exactly the same evaluation environment:

- Eevee renderer, 1024 × 1024 output.
- Fixed samples and benchmark camera rig.
- Neutral three-point studio lighting and fixed neutral World strength.
- Fixed exposure and view transform.
- Transparent background off; no post-processing or compositor.
- No per-model lighting improvements.

For different-sized objects, move/scale the model into the benchmark volume,
not the lights.

## 4. Texturing-relevant context only

Give Material Director the Eevee viewport image, isolated target image, clean
geometry, UVs, Open Painter texture set, baked AO, curvature, world normal,
position, thickness where useful, object dimensions and the texturing prompt.

Do not turn camera setup, Eevee configuration, lighting, World configuration,
object importing, cleanup, UV repair, render troubleshooting or scene organization
into tasks. They may exist in the file but are immutable infrastructure.

## 5. Five models, five capabilities

| Model | Capability |
|---|---|
| Metal toolbox | Multi-material reasoning and physically located wear |
| Chair | Clean wood/fabric creation, scale and direction |
| Rubber boots | Geometry-aware dirt placement |
| Ceramic vase | Precise graphic placement |
| Rock set | Geometry-aware natural distribution |

## 6. Toolbox: physical reasoning

### Create

Texture this toolbox as a used but maintained workshop toolbox. Make the body dark petrol-blue painted steel, the handle aged hardwood and the latches brass. Add restrained paint loss at plausible exposed edges and repeated contact areas, revealing steel underneath. Add subtle accumulated grime in protected recesses. Keep broad painted surfaces mostly clean. Build everything as editable Open Painter material components.

### Refine 1

Make it look approximately five years more worn. Preserve the existing material identity and wear logic.

### Refine 2

Reduce the wear by 50%. Keep the existing wear locations and patterns. Do not regenerate or replace them.

This tests persistent material reasoning. If the chips move during the last
revision, Director fails even if the new result looks good.

## 7. Chair: resist grunge

### Create

Texture this as a nearly new premium contemporary chair. Use dark walnut for the wooden frame and burnt-orange woven upholstery. The walnut should have directional grain appropriate to each wooden member. The upholstery should have subtle woven structure at believable physical scale. Do not add dirt, damage, scratches, edge wear or artificial aging.

### Refine 1

Change only the upholstery to deep forest green.

### Refine 2

Make only the upholstery weave twice as fine. Preserve its color and all wooden surfaces.

Random scratches or aging violate the clean-material brief.

## 8. Boots: geometry awareness

### Create

Texture these as mustard-yellow rubber work boots with matte black soles. Add wet dark mud primarily to the soles and lower 25% of the boots, with sparse upward splashes. Keep the upper shafts mostly clean. The mud must remain an independently editable effect.

### Refine 1

The mud has dried overnight. Preserve its exact spatial distribution. Make only the mud lighter, rougher and visually dry.

### Refine 2

Reduce the amount of mud by half while preserving the character of its distribution.

This separates material appearance from material placement.

## 9. Vase: precision

### Create

Create a warm ivory glazed ceramic finish. Add exactly two thin cobalt-blue horizontal bands around the upper third of the vase, with equal width and a clean gap between them. Add a narrow metallic gold rim around the mouth. Keep each component independently editable.

### Refine 1

Change only the two cobalt bands to dark red.

### Refine 2

Move both bands slightly lower while preserving their width, spacing and relationship to each other.

This tests specific controllable placement rather than generic grunge.

## 10. Rocks: environmental reasoning

### Create

Texture these rocks as pale weathered sandstone. Add moss primarily to upward-facing sheltered surfaces and cracks. Keep steep exposed vertical faces mostly clear and undersides almost completely free of moss. Add subtle damp darkening in deep recesses. Maintain consistent material scale across every rock.

### Refine 1

Make the moss warmer and more olive without changing where it grows.

### Refine 2

Reduce moss coverage by approximately 40% while preserving its environmental placement logic.

This tests whether geometry maps improve reasoning, not merely whether they are available.

## 11. Automatic Eevee evidence

After every operation a deterministic Blender script produces the same views:

```text
00_before.png
01_create_front.png
02_create_back.png
03_create_close.png
04_refine1_front.png
05_refine1_back.png
06_refine1_close.png
07_refine2_front.png
08_refine2_back.png
09_refine2_close.png
```

The benchmark harness prepares models, fixes Eevee/camera/lights, bakes maps,
then invokes Material Director for texturing only. The harness subsequently
captures Eevee renders, screenshots, material state and metrics. Codex does not
render, repair the environment, or evaluate its own result.

## 12. Score the AI texturing

| Capability | Weight |
|---|---:|
| Material appearance | 25% |
| Semantic/spatial placement | 25% |
| Prompt compliance | 20% |
| Editability | 15% |
| Revision fidelity | 15% |

No points for importing, fixing lights, camera setup, render configuration,
shader conversion or scene cleanup. Those are prerequisites.

Record **artist intervention count**: every human correction of Director.

- 0: autonomous success, provided the actual texturing requirements pass.
- 1: minor assistance.
- 2: significant assistance.
- 3 or more: failed autonomous workflow.

## Most important rule

Codex is not the benchmark operator. The tested model receives only texturing
prompts through Material Director. A deterministic Blender script supplies the
clean fixture and Eevee evidence. Humans evaluate blinded results.
