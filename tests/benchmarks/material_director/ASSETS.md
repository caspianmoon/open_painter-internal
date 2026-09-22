# Public source assets and fixed Eevee fixtures

The primary protocol is [the revised Eevee benchmark](../../../Open_Painter_Material_Director_Eevee_Benchmark.md). Its five fixtures supersede the earlier eight-case setup. Earlier `fixtures`, `fixtures-v2`, combined workshop, barrel, reference-chair and Cycles pilots remain historical setup evidence and must not enter the primary results.

## Acquire and verify

`asset_manifest.json` pins the exact official Poly Haven Blend/2K packages, metadata, creator, license, sizes, provider MD5 and independent SHA-256. Source pages and the CC0 license were checked on 22 September 2026. Source appearance files are retained only in the acquisition archive for reproducibility or evaluating a necessary source geometry modifier; none survives in the test scene or goes to Director.

```sh
python3 tests/benchmarks/material_director/assets.py --download --assets A01,A02,A05,A04,A06
```

Existing pinned files are verified without networking. Changed bytes fail verification. `--asset-manifest` and `--assets-root` override paths. No model account is used to download assets.

## Prepare the five fixtures

```sh
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
  --disable-autoexec --python-exit-code 1 \
  --python tests/benchmarks/material_director/prepare_fixtures.py -- \
  --assets A01,A02,A05,A04,A06 \
  --output .blender-test/material-director-benchmark/fixtures-eevee-v1
```

Existing `.blend` fixtures are immutable. Use a fresh output directory to change preparation. `--samples` is fixed at 32 for CPU AO. `--skip-bakes` performs setup diagnostics only and fails explicitly without creating a runnable fixture. Preparation never invokes a provider.

The first preparation creates `_infrastructure/benchmark_rig.blend` from a blank file and pins its checksum. Subsequent fixtures reload that exact file. Its only collections are `TEST_MODEL` and `BENCHMARK_LIGHTS`; the latter contains Key, Fill and Rim. Exactly one `BENCHMARK_CAMERA` belongs to the scene root. There is no floor, guard geometry, alternate light rig, helper empty or source scene infrastructure. Camera and light location/rotation/scale are UI-locked and unselectable; signatures additionally detect programmatic edits.

All fixtures use Eevee, 1024 × 1024 output, 64 viewport/render samples, opaque neutral World, AgX Medium High Contrast, exposure zero, gamma one, and disabled compositor/sequencer. Camera/light/world/color/render settings are model-independent. One camera has three predefined orthographic poses, `front`, `back`, and `close`. The evaluator temporarily applies a pose and restores the original; Director cannot operate this infrastructure.

## Geometry preparation and real material boundaries

| Asset | Retained source geometry | Fresh sets |
|---|---|---:|
| A01 toolbox | All six original parts | 1 shared source material |
| A02 chair | Original chair mesh and its occupied legs/pillow slots | 2 |
| A05 boots | Clean left and right LOD0 only; excludes source dirty/LOD1 duplicates | 1 shared source material |
| A04 vase | Original vase mesh | 1 |
| A06 rocks | All six original rocks together | 1 shared source material |

Source render modifiers are evaluated and applied before appearance removal. Geometry attributes, actual material indices, UVs, custom normals, sharp edges and creases survive the source-to-frozen invariant check. Source materials, textures, masks, appearance vertex colors/groups, shader groups, text blocks and unused camera/light/world datablocks are removed. No semantic groups, region masks or new material partitions are created for the primary fixtures.

A single uniform transform centers each entire model at `(0,0,1)` and makes its maximum extent two benchmark units. Relative object placement and orientation survive. The transform is applied to mesh coordinates so object scales are one. Reports retain original dimensions, original/evaluated geometry hashes and the exact conversion factor. **Open Painter's physical operators currently measure normalized world units, not original source meters.** The source-to-benchmark scale is disclosed in the setup audit rather than misrepresented as original physical scale.

Source defects are fixed before any AI session. Coincident vertices are welded at a deterministic geometry-relative tolerance; inconsistent face windings are recalculated only on affected objects. Tiny dangling triangular scan flaps are removed only when two edges are boundaries, the third is nonmanifold, a vertex has no other face, and area is below the frozen threshold; the rock source contains nine such flaps, including the one whose normal kept flipping. Actual material slots remain unchanged. Invalid/overlapping/out-of-tile UVs are packed jointly across each actual texture set without rotation. When a small number of source faces still have folded/overlapping UVs, only those exact diagnostic faces are separated into a reserved atlas strip; the remaining packed charts retain their shape under one uniform scale. Separated invalid faces receive nonoverlapping convex UV polygons. The repaired boots isolate 76 of 14,032 faces and the six rocks isolate 91 of 63,118 triangles; this is geometry repair, with no semantic labels. A source requiring more than five percent of faces or 200 faces (whichever is larger) is rejected for manual source replacement. The final setup also requires at least 15% sampled atlas occupancy per set, preventing nominally valid but unusably sparse layouts. All changes and triggering diagnostics are recorded. There is no semantic targeting in this process. A strict full geometry/UV check follows; remaining warnings/errors for geometry/UVs are a setup **FAIL**, never an AI result. Merely unmarked UV seams are informational.

## Fresh maps, Eevee only

Each occupied source material receives a fresh **1024** texture set with geometry-only AO, four curvature widths, position, world normal and thickness. No original PBR map is reused. No Cycles renderer or Cycles bake is invoked. The native CPU geometry evaluators produce curvature/utility maps; `cpu_maps.py` casts 32 deterministic cosine-hemisphere BVH rays at every covered AO texel, distance 0.2 normalized units. All model meshes participate in occlusion, including cross-material contact. Maps are packed and registered with ordinary Open Painter freshness provenance. AO metadata records method, sample count, distance and genuine ray count. Tangent normal is omitted because the revised context does not require it and there is no separate high-poly source.

Each set contains a disabled locked artist adjustment for structural preservation checks. It adds no visible material or geometric hint. Scene infrastructure is protected through the fixed rig signature.

## APIs and evidence contract

```python
prepare_fixture([asset_id], manifest_path, asset_root, output_path, samples=32, bake=True)
fixed_rig_record(scene, config)  # JSON-safe immutable infrastructure snapshot
rig_signature(scene, config)    # SHA-256 of the canonical record
```

The adjacent fixture JSON records `READY` or `FAIL`, `.blend` SHA-256, source checksums, complete repair log, normalization, geometry/UV/material-partition hashes, diagnostics, map hashes, freshness, and the exact rig record. `READY` means setup passed; it says nothing about AI quality. `fixture_index.json` lists all ready fixtures and their identical rig signature.

`scene['md_benchmark_config']` contains only evaluator infrastructure and source-boundary metadata:

- `target_set_ids`, `fixture_id`, `asset_ids`.
- `camera_name`/`input_camera_name`: `BENCHMARK_CAMERA`; `camera_names`: that one camera.
- `camera_poses`: front/back/close matrices, ORTHO type, scale and lens; `input_camera_pose`: front.
- `fixed_light_names`: Key/Fill/Rim; no relight collections or guard objects.
- `render_engine`: `BLENDER_EEVEE`; `render_size`: `[1024,1024]`.
- `primary_evidence`: true; `studio_version`: `fixed-eevee-v1`; `rig_signature`.

Current camera pose and output filepath/format are excluded from the immutable rig digest so the harness can capture its declared views. The declared pose definitions remain inside the digest. Evidence separately checks/restores the current pose and guards all changes during a Director turn. The source audit and any evaluator annotations are never Director attachments.

## Independent read-only audit

```sh
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
  --disable-autoexec --python-exit-code 1 \
  --python tests/benchmarks/material_director/verify_fixtures.py -- \
  --fixtures .blender-test/material-director-benchmark/fixtures-eevee-v1
```

The audit opens each saved fixture, verifies its checksum, fixed rig, five-map families, packed decoded map hashes and freshness, exact source object/material selection, source appearance absence and strict geometry/UV checks. It independently imports the pinned original source and replays the documented normalization/repair pipeline, then compares geometry, UVs, material indices, custom normals, sharp edges and transforms with the saved result. All five must share one rig signature. `source_fixture_audit.json` records every check. Original source `.blend` files are never saved or edited.


## Optional evaluator-only placement annotations

Annotations are a separate, hash-pinned bundle outside every `.blend`, prompt, normal Director context and reference attachment. A human must author them against the **final cleaned fixture hash and UV layout**, before viewing scored AI outputs. Old masks for unrepaired source UVs are invalid. Missing annotations remain `NOT_TESTED`; the harness does not invent segmentation or artistic scores.

`annotations.py` accepts schema version 1, an identified `annotator`, `declared_before_ai_outputs: true`, and `fixtures`. Each fixture identifies its SHA-256, explicit `regions`, `measurements`, and optional `editability_probe_requirements`. A region identifies an actual frozen `texture_set_id` plus one of:

- `kind: "uv_mask"`: `path` to a nonempty boolean H×W `.npy`, at the captured channel resolution, bottom-left origin.
- `kind: "surface_samples"`: `.npz` containing `uv[N,2]`, positive surface-area `weight[N]`, boolean `member[N]`; optional boolean `valid[N]` excludes declared boundaries, and `height[N]` records normalized physical height. These samples support surface-area comparisons independent of UV stretching.

Measurements reference a region and one captured channel:

- `coverage`: an explicit `stage` and linear per-channel `selector.minimum`/`selector.maximum`; reports membership coverage, spill and missed area. This does not recognize materials by name.
- `preservation`: declared `before`/`after` checkpoint labels, numerical `tolerance` and `minimum_fraction`; compares archived channel values inside the annotated region.
- `bands`: declared height samples plus frozen selector, bin count and occupancy fraction; reports observed height intervals. It cannot establish visual straightness or artistic quality.

Supported stage labels are `before`, `after_create`, `after_refine_1`, and `after_refine_2`. A small schema example, replacing the placeholders and supplying real manual arrays:

```json
{
  "schema_version": 1,
  "annotator": "reviewer-name",
  "declared_before_ai_outputs": true,
  "fixtures": [{
    "fixture_sha256": "<exact 64-character fixture hash>",
    "regions": [{"id": "protected-region", "texture_set_id": "<actual set ID>",
                 "kind": "uv_mask", "path": "protected.npy", "origin": "bottom-left"}],
    "measurements": [{"id": "unchanged-color", "kind": "preservation",
                      "region_id": "protected-region", "channel": "BASE_COLOR",
                      "before": "after_create", "after": "after_refine_1",
                      "tolerance": 0.0039215686, "minimum_fraction": 0.995}],
    "editability_probe_requirements": [{"id": "roughness-control", "component": "ceramic glaze",
                                        "channel": "ROUGHNESS", "field": "value", "value": 0.6}]
  }]
}
```

Use a Python environment with NumPy, such as the installed Blender bundled Python:

```sh
"/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13" \
  tests/benchmarks/material_director/annotations.py validate evaluator/spec.json
"/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13" \
  tests/benchmarks/material_director/annotations.py freeze evaluator/spec.json \
  --output evaluator/frozen-v1
```

Freezing copies every mask/sample array into an immutable new directory and records all checksums. Supply the resulting manifest path and hash in an external evidence configuration before freezing the benchmark:

```json
{"annotations_manifest": "/absolute/evaluator/frozen-v1/annotations.json",
 "annotations_sha256": "<manifest SHA-256 returned by freeze>"}
```

The benchmark `freeze --evidence-config` option pins this configuration. `verify_frozen(path, expected_sha256=None, fixture_sha256=None)` checks the manifest and dependencies using standard Python; numeric validation/evaluation requires NumPy. Exact fixture hashes are checked before every run. The evaluator hook measures captured channels only after they are archived, verifying decoded channel and valid-sample hashes. The primary human appearance/placement/prompt/editability/revision scores still require blind review.

Archived measurements can be rerun without AI:

```sh
"/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13" \
  tests/benchmarks/material_director/annotations.py measure \
  --annotations evaluator/frozen-v1/annotations.json \
  --fixture-sha256 <fixture-sha256> \
  --checkpoint run/after_refine_1_state.json --output run/placement-review.json
```

Output component IDs are unknown before creation. Freeze semantic editability requirements first, then manually bind each requirement exactly once to a real output layer/mask ID using a `bindings` JSON list with `requirement_id`, `texture_set_id`, `layer_id`, optional `mask_id` and `protected_set_ids`. Run `annotations.py map-probes` with the same annotation/fixture/checkpoint arguments plus `--bindings` and `--output`. It validates IDs and produces `editability_probes` for the evaluator. A reviewer must confirm semantic correspondence; a matching ID alone proves no artistic claim. This post-output binding never changes the predeclared operation or value.
