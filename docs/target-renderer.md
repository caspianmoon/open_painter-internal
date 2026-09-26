# Target renderer — 0.5.1

Choose where the asset is going once, at the top of the Painter view: **Blender,
Unreal Engine 5, Unity HDRP, Unity URP, Godot 4** or **Custom**. The target
configures the export conventions of every texture set that follows it, drives
the **Engine Preview**, and turns export into one click: **Export → Unreal 5**.

| Target | Tangent normal | Gloss | Packing (export preset) | Naming | Mesh | AO |
| --- | --- | --- | --- | --- | --- | --- |
| Blender | OpenGL (+Y) | roughness | separate maps | `{set}_{map}` | — | multiplied into base colour (viewport) |
| Unreal Engine 5 | DirectX (−Y) | roughness | BaseColor, Normal, ORM (R=AO G=Rough B=Metal) | `T_{set}_{BC|N|ORM}` | FBX, cm, Z up, −Y forward | indirect only |
| Unity HDRP | OpenGL | smoothness | BaseColorMap, NormalMap, MaskMap (R=Metal G=AO B=Detail A=Smooth), EmissiveColorMap | `{set}_{map}` | FBX | indirect only |
| Unity URP | OpenGL | smoothness (Metallic alpha) | BaseMap, Normal, MetallicGlossMap, OcclusionMap, EmissionMap | `{set}_{map}` | FBX | indirect only |
| Godot 4 | OpenGL | roughness | albedo, normal, orm, emission | `{set}_{map}` | glTF (.glb) | indirect only |
| Custom | per set | per set | the set's own preset | the set's prefix/suffix | — | — |

Colour spaces: base colour and emission sRGB, everything else Non-Color; every
target expects specular F0 = 0.04 (Principled *Specular IOR Level* 0.5). The
manifest written next to the textures (`<set>_manifest.json`) records all of
this per file — convention, colour space, the compression the engine will
typically apply (BC1/BC5/BC7) — for importers and reviewers.

## Engine Preview

`Engine Preview` (toolbar toggle) reconfigures the display material to what the
engine computes from the *exported* files rather than from the live float graph:

- colour, roughness and metallic quantised to 8 bits (colour through sRGB, as the
  file stores it) — `8-bit Textures`;
- the normal goes through the target's convention round trip (stored as 1−G for
  DirectX, decoded back) and, with `Reconstruct Normal Z`, drops its blue channel
  and rebuilds Z from X and Y the way BC5 / Unity's *Normal map* import does;
- roughness goes through the smoothness round trip for Unity targets;
- `AO Indirect Only`: engines apply the AO map to indirect light, so the directly
  lit viewport shows none of it (the Texture Set node's AO in the preview is set to 0);
- the scene's view transform switches to Standard (Unreal's ACES tone map is not
  reproduced; the choice is restored when the preview is turned off).

The adapters are role-tagged nodes (`Engine · …`) inside the Open Painter Texture
Set node's group, between its samplers and its outputs. The material stays the node, a
Principled BSDF and a Material Output. Toggling the preview never rebuilds the stack.
`tests/integration/engine_preview.py` bakes the adapter outputs and checks them against
NumPy to 2/255.

## Export → Target

`Export → <Target>` (toolbar, and the Export panel's Target box) exports the
active set — or, with *Selected Objects*, every set assigned to the selected
objects — into the project's export directory with the target's packing,
naming, format and resolution cap, writes the manifest, and exports the
assigned objects as FBX or glTF with the target's axis and scale options. The
set's own export settings are restored afterwards; sets with *Follow Project
Target* off keep their preset for the Export tab. Existing
files are never overwritten.

Files: `targets/__init__.py` (registry, `apply`, `filename`, `manifest`),
`targets/preview.py` (adapters), `ui/target_panel.py`, `properties/project.py`.
Tests: `tests/unit/test_targets.py`, `tests/integration/engine_preview.py`,
`export_target.py`.
