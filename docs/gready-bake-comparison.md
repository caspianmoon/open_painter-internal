# G-Ready bake comparison and integration

Audited the user-supplied `G-Ready_Source` directory on 2026-09-13, against
Open Painter task source `0f44580da6425eec21dc00ff74a93f0b7d6cacf0`.
G-Ready identifies itself as version 2.1 by Farid Mammadov.

## Finding

G-Ready has useful bake-workflow features missing from Open Painter. It does
not supply a different bake engine: its Closest Hit operation calls ordinary
`bpy.ops.object.bake(type=...)` (`operators_baking.py:1883`). This audit does
not establish faster rendering or universally better pixels from G-Ready.

| G-Ready feature / evidence | Open Painter integration |
| --- | --- |
| Per-low `_high(.NNN)` / `_cage` matching (`operators_baking.py:1803–1819`) | Explicit source→target pointers, optional strict name matching, multiple highs per target, scene-local discovery, ambiguity/missing-pair errors. Existing All Sources mode retained. |
| Matrix separation for non-AO; assembly positions retained for AO (`operators_matrix_bake.py:432–468`) | Temporary evaluated bake scene. Individual normals/pair AO use only the relevant parts; assembly AO uses listed parts at their authored positions. Unrelated scene geometry is excluded. Originals are never moved or joined. |
| Cage creation and independent ray controls (`operators_baking.py:1267–1450`, `ui.py:1139–1163`) | Editable cages from render-configured geometry, world-space normalized offsets, per-target cages, ordered evaluated topology checks and separate extrusion/ray-distance controls. |
| CPU/GPU selection (`__init__.py:324`) | CPU or existing Scene Device selection, applied only to the bake scene. Artist device preferences remain unchanged. GPU hardware speed is not claimed. |
| Explicit normal convention (`__init__.py:318`) | Internal normal maps always use +X/+Y/+Z, independent of the artist's scene bake settings. Existing export convention conversion remains authoritative. |

Open Painter additionally keeps float32 mesh-map buffers, renders into owned
image targets, and discards failed maps while retaining prior completed maps.
Bake source/cage settings persist in `.blend`; changing routing or cage geometry
marks affected map provenance stale.

## What was not imported

The integration is original Blender API code; it does not import or redistribute
the G-Ready source directory or icons. No package-wide or bake-module license
notice was present in the supplied folder; the separate auto-UV file has its own
GPL notice. The source folder remains unchanged and untracked.

The standard G-Ready baker modifies source material nodes and scene render/color
settings. Its matrix placement writes the same local location to each trio,
which can lose relative offsets. Cage-only Edge Split can break correspondence.
Its experimental compositor AA clears the user's compositor, and some output
paths use byte images/8-bit intermediate PNGs. Those implementations were not
ported. Nor were unrelated modeling, export presets, auto-renaming or UV tools.
Open Painter uses its existing render UV map so bake placement agrees with its
painting/display pipeline; no independent target-UV override was added.

## Pixel and ownership evidence

Real Blender 5.2.1 LTS `9e2066aef7ef`, CPU, 512² float32 images, 8 samples,
small deterministic two-part plane fixtures:

- Legacy all-high routing projected a neighbouring part's opposite slope onto
  the wrong target: max absolute encoded RGB error **0.0449545383** versus a
  one-target/one-source control. The initial old baker failed this assertion.
- Matched routing agrees with that control: max absolute error **0.0**.
- Unrelated-object and isolated-pair AO averages are **1.0** on the unobstructed
  interior; assembly contact AO is **0.0249583330** with the fixture roof included.
  These are fixture pixel values, not a general image-quality score.
- Render subdivision output matches when viewport subdivision is reduced or
  disabled. A cage that matches base topology but not render topology rejects
  before Cycles is called.
- Source Edit Mode, selection, transforms, visibility, render settings,
  materials, active nodes, image pixels and device preferences remain intact.
- OBJECT-linked material overrides and existing active image nodes are protected.
  An injected second-target failure leaves no staging datablocks or partial map.
- Cage/source mapping survives save/reopen; cage edits and routing changes
  invalidate map provenance. AO scope does not invalidate a Normal map.

The existing beveled-cube bake caught an evaluated-ID ownership error during
development. Copying original material IDs instead of keeping depsgraph-owned
IDs fixed it. The existing fixture and new override regression cover this path.

## Using it

In **Bake Mesh Maps**, add your high-poly sources and choose **By Target / Name**.
Set each source row's **Low Poly Target**, or use **Match Sources by Name** with
`part_low`, `part_high`, `part_high.001` and optionally `part_cage`.
Matching does not rename objects or silently guess an ambiguous pair.

Choose **AO Occluders → Texture Set Assembly** for contact between listed parts,
or **Individual Parts** to suppress that contact. **Create Editable Cages** creates
wire cages without changing source geometry; multiple targets store their cages
on source rows. **Separate Cage Extrusion** decouples the ray origin offset from
maximum ray distance. Existing projects retain their old distance coupling until
this option is enabled.

**Bake Device → Scene Device** uses the existing Cycles device configuration.
Open Painter does not enable or reconfigure hardware devices on the user's behalf.

## Validation commands and limits

```sh
python3 scripts/build_extension.py --output /private/tmp/open-painter-gready-bake.zip
python3 scripts/run_tests.py --blender /Applications/Blender.app/Contents/MacOS/Blender --package /private/tmp/open-painter-gready-bake.zip
python3 scripts/run_tests.py --blender /Applications/Blender.app/Contents/MacOS/Blender --gui-only
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 scripts/run_pure_tests.py
BLENDER_USER_RESOURCES=/private/tmp/op-gready-persist-user /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python tests/integration/bake_pairing.py
BLENDER_USER_RESOURCES=/private/tmp/op-bake-object-material-user /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python tests/integration/bake_isolation.py
python3 -m compileall -q src/open_painter scripts tests
git diff --check
```

The main runner sets isolated `BLENDER_USER_RESOURCES`; it restores bundled
fixtures after testing. The artist profile and published extension ZIP are not
replaced. The candidate excludes the supplied G-Ready source.

A single Cycles map remains synchronous, with cancellation between maps. Staging
copies require additional temporary memory; there is no bake-speed or large-mesh
memory-reduction claim. GPU configuration routing was checked, but actual GPU
baking was not benchmarked. Complex Geometry Nodes external dependencies,
adaptive subdivision and shader-driven normal fidelity need broader fixtures;
the current render-geometry tests cover subdivision, visibility, bevel geometry,
transforms, UVs, material ownership and cage correspondence. Existing single-tile
and utility-map limitations remain.

Final results: **50 integration scripts, 32 Blender unit tests, 27 pure tests,
three native GUI scripts, extension validation and isolated installation PASS**.
Compilation and whitespace checks pass. The [validation record](performance/gready-bake-validation.json)
contains tested source/package hashes, exact commands, changed files and fixture
values. No historical Worn Painted Metal timing is presented as a bake result.
