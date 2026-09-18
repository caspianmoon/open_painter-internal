# Engine validation (quality remedy, Phase 3)

Real-engine validation of the candidate add-on's exports: the exact files the add-on wrote
are imported into an Unreal Engine 5 project (metallic/roughness) and a Unity URP project
(Lit, metallic workflow, smoothness in the metallic map's alpha), with recorded import
settings, hashes of every imported file, the five required scenes and numerical checks.

## Layout

- `scripts/engine_validation.py` builds the fixtures with background Blender into
  `.blender-test/quality/engine-fixtures/<target>/` and writes `fixtures.json` (package
  checksum, Blender build, fixture list, sha256 of every exported file), then runs each
  engine's script when its editor is available and reports `NOT RUN` otherwise.
- `unreal/import_and_validate.py` runs inside the Unreal Editor (Python plugin):
  `UnrealEditor-Cmd.exe <project>.uproject -run=pythonscript -script=<this file>` or
  `-ExecutePythonScript=<this file>` with `OP_FIXTURES=<engine-fixtures/unreal>`.
- `unity/Editor/OpenPainterValidation.cs` runs inside the Unity Editor (URP project):
  `Unity -batchmode -projectPath <project> -executeMethod OpenPainter.Validation.Run
  -opFixtures <engine-fixtures/unity_urp> -opReport <report.json>`.

## Fixtures

| Fixture | Scene it serves | Notes |
| --- | --- | --- |
| `channel_plate` | Unlit channel reference | Known patches per channel; expected encoded values in `fixtures.json` |
| `seam_object` (height 0) and `seam_object_height` | Rotating lit seam object | Continuous analytic colour/roughness across UV cuts; the second carries height in the normal |
| `difficult_atlas` | Distance and grazing-angle sweep | Well-separated islands pass; the close pair is the characterised diagnostic |
| `transparency_card` | Cutout and soft alpha | glTF/URP base map with opacity in alpha; no halo or inflation expected |
| `production_prop` | Production prop | Three sets, 25 layers, painted content, FBX mesh with slots |

## Import policy the scripts apply and record

Unreal: BaseColor `sRGB` on, `TC_Default`; Normal `TC_Normalmap`, `sRGB` off, Flip Green
Channel **off** (the export is already DirectX); ORM `TC_Masks`, `sRGB` off; mip generation
`TMGS_FromTextureGroup`; the material samples ORM R→AO, G→Roughness, B→Metallic. Mesh
FBX with "Import Normals and Tangents" recorded; a second import with "Compute Normals"
records whether a tangent-basis recompute introduces seams.

Unity URP: BaseMap `sRGB`; Normal `TextureImporterType.NormalMap`, no flip; MetallicGlossMap
linear (`sRGBTexture=false`), `alphaIsTransparency=false` (alpha is smoothness);
OcclusionMap linear; Lit shader `_MetallicGlossMap`, `_SmoothnessTextureChannel=0` (metallic
alpha). Mesh FBX with `importNormals=Import`, `importTangents=Import` recorded.

## Status

The engines were **NOT RUN** on the reference machine (no Unreal or Unity installation is
present, and the plan forbids purchasing hosting or deploying to accounts). The scripts,
fixtures, hashes and scene definitions are delivered so the run can be executed on a
machine with the engines; see the completion report for the exact status line.
