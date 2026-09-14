# Generated material quality — 0.2.5

All **50 existing smart materials** now use generated surface-detail inputs:
15 original bitmap sources are bound to 76 of the existing 229 editable layers.
The source files were generated with the built-in `image_gen` tool and integrated
into the texture evaluator. They are not rendered objects pasted into albedo.
The native Blender layout, existing recipe names, layer ordering, authored
channel values, masks, surface settings and version-1/version-2 material support
are preserved. Existing saved material instances are not overwritten; apply the
updated library recipe to use its new inputs.

Implementation is on `task/generated-material-quality`, based on
`ec349795d15fe8e1fe166a76bd37449850e3f0b0`. The completion message supplies the final
commit hash. The checkout was not reset and newer work was preserved.

## Sources and appearance

The library now includes brushing, machining scratches, casting pores, tarnish,
burn blisters, zinc spangles, marble veins, granite minerals, timber grain,
leather grain, canvas weave, concrete pores, rust texture, polymer micrograin
and handling smudges. The actual Blender render comparison caught lost marble
contrast; that image was refined with the image-generation edit tool before
shipping. No original generation files were deleted.

[Browse the rendered material gallery](generated-material-gallery.html).
It includes all 50 final library previews and nine before/after comparisons.
The comparison uses identical geometry, UVs, baked maps, lighting and rendering
settings: 1024px output textures, 512px Cycles CPU renders, 32 samples. The final
library thumbnails use the established 256px/16-sample fixture. These preview
settings do not change the user's texture-set resolution.

Prompts: [initial sources](research/generated-material-prompts.json),
[specialized sources](research/generated-material-prompts-specialized.json),
[marble refinement](research/generated-material-refinement.json).
Actual source files and hashes:
[`assets/generated/provenance.json`](../src/open_painter/assets/generated/provenance.json).
The tool returned **1254 × 1254, 8-bit RGB PNGs**, despite a 2048px request.
Original pixels are retained. Generated detail is artistic microstructure, not
measured PBR scans. Authored output resolution and float32 evaluation are unchanged.

Generated sources follow this repository's GPL-3.0-or-later license, allowing
commercial distribution subject to that license. They are not labeled CC0.
No third-party reference images or Substance library assets were supplied.
OpenAI assigns its output rights to the user to the extent permitted by law,
without promising uniqueness. [OpenAI terms, Content](https://openai.com/policies/terms-of-use/)
and the [asset notice](../src/open_painter/assets/generated/README.md) record the
basis and limits of that provenance.

## Runtime behavior

Each channel may reference an optional packed `detail_image`. Its scalar pixels
replace the procedural detail field while retaining editable base color/value,
variation and signed height amplitude. UV scale, rotation, offset, wrapping,
filtering and triplanar mapping use the existing sampling functions. Curvature,
AO, position and world normals still control the mesh-aware masks. No generated
source pretends to know the model's edges or cavities.

One packed source is shared across bindings. The CPU cache retains one float32
luminance plane per source instead of an RGBA duplicate: about 6 MiB rather than
24 MiB at 1254². Scalar sampling is cached independently of color/roughness/height
values. The existing live GPU compositor reuses those scalar leaves; warm value
edits do not regenerate, transfer or pack source images. The fixed display
material remains intact.

Save/export still evaluates the latest complete edits through CPU authority.
Portable version-2 archives encode each shared source once and restore shared,
independent packed images. Version-1 files need no new fields. Missing image
references fail explicitly. Managed source writes now also invalidate channels
that consume the changed channel through a published input.

## Fresh performance context

These measurements were made in this task on Blender **5.2.1 LTS**, build
`9e2066aef7ef`, on the user's M3 Pro Mac. They are not historical numbers.
The Worn Painted Metal fixture uses 2048² float32 outputs, six layer records
including its folder, and synthetic 32² AO/curvature maps. It excludes mesh
baking and viewport rendering. The before variant strips only generated-source
metadata to reproduce the preserved procedural recipe; the after variant uses
the new material detail. Thus they compare different surface inputs, not an
identical-image backend speedup.

| CPU operation | Preserved procedural | Generated detail |
|---|---:|---:|
| Initial complete texture set, wall | 30.887 s | 36.566 s |
| Warm roughness edit, wall | 0.244 s | 0.272 s |
| Initial process CPU time | 20.860 s | 23.845 s |
| Warm roughness CPU time | 0.216 s | 0.243 s |
| Retained scalar/cache arrays | 208.03 MiB | 210.03 MiB |
| Process peak RSS | 854.80 MiB | 848.56 MiB |

Both warm CPU edits had zero source-generation, masking, filter, geometry,
image-read and packing calls; one authoritative output image write is expected.
The generated edit reused 384 scalar strips. A development run retaining RGBA
detail images caused cache eviction and a 4.161 s warm edit; it was corrected
before packaging. The final run retains float32 scalar data under the unchanged
256 MiB cache allowance. This is a quality update, not a claim that CPU export
became faster. Cold evaluation and complex stacks still cost time.

Raw measurements: [before](validation/generated-material-cpu-before.json),
[after](validation/generated-material-cpu-after.json).
The real-window 512px live test matched all eight CPU channels within
`1.103e-6` maximum absolute error. Warm live edits performed no source generation,
masking, image writes/packing, GPU uploads or shader compilation. Its recorded
prepare/submit times are not displayed-frame latency or an FPS guarantee.
[Live verification](validation/generated-material-live-gpu-512.json).

A separate full-2K live run also passed: maximum error `2.861023e-6` across
all eight channels, with zero source generation, masking, image writes/packing,
GPU uploads or shader compilation for the three warm roughness edits.
[Full-2K live verification](validation/generated-material-live-gpu-2k.json).
Its 3.002 s cold preparation and 5.73–22.51 ms warm prepare/submit times exclude
GPU completion and native frame presentation; they must not be presented as FPS.

## Validation commands

All Blender acceptance runs used isolated user resources and factory startup.
Blender's Metal detection crashed before Python under the filesystem sandbox;
the recorded successful runs used approved unsandboxed Blender execution with
isolated profiles. No tests were claimed from that failed startup.

```sh
python3 scripts/run_tests.py
python3 scripts/run_tests.py --gpu-only
python3 scripts/run_tests.py --gui-only
```

The final full suite passed **55 background integration scripts and 49 unit
tests**. Five real-window GPU suites and four native GUI workflows passed.
New regressions cover generated asset hashes/dimensions/packing, all 50 recipes,
shared source identity, correct native edits, UV/triplanar CPU parity, scene
identity, failed-job state, geometry reuse, scalar memory, dependency invalidation,
portable image deduplication, missing inputs and version-1/version-2 round trips.

```sh
BLENDER_USER_RESOURCES="$PWD/.blender-test/generated-quality-user" \
  /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
  --python-exit-code 1 --python scripts/benchmark_texture_material.py -- \
  --resolution 2048 --legacy-detail \
  --report .blender-test/generated-quality-benchmark-before.json

BLENDER_USER_RESOURCES="$PWD/.blender-test/generated-quality-user" \
  /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
  --python-exit-code 1 --python scripts/benchmark_texture_material.py -- \
  --resolution 2048 --report .blender-test/generated-quality-benchmark-after.json

BLENDER_USER_RESOURCES="$PWD/.blender-test/generated-quality-user" \
  /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
  --python-exit-code 1 --python scripts/render_catalog_previews.py
```

The comparison render runs used `--size 512 --samples 32`, separate `--output`
directories, and repeated `--material` arguments for Clean Brushed Steel, Fresh
Oak, Worn Leather, Rough Concrete, Canvas Fabric, Heavy Rust, Galvanized Steel,
Polished Marble and Granite. The before run additionally used `--legacy-detail`.
Polished Marble was rerendered after source refinement.

```sh
BLENDER_USER_RESOURCES="$PWD/.blender-test/generated-quality-user" \
  OP_LIVE_BENCHMARK=1 \
  OP_LIVE_REPORT="$PWD/.blender-test/generated-quality-live-gpu-2k.json" \
  /Applications/Blender.app/Contents/MacOS/Blender --factory-startup \
  --python tests/integration/live_preview.py -- -ApplePersistenceIgnoreState YES

python3 scripts/build_material_gallery.py
python3 scripts/build_extension.py
BLENDER_USER_RESOURCES="$PWD/.blender-test/generated-quality-package-user" \
  /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
  --command extension validate dist/open_painter-0.2.5.zip
BLENDER_USER_RESOURCES="$PWD/.blender-test/generated-quality-package-user" \
  OPEN_PAINTER_TEST_ZIP="$PWD/dist/open_painter-0.2.5.zip" \
  /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
  --python-exit-code 1 --python tests/integration/install_extension.py
```

Release installation uses `scripts/install_local.py` in the normal Blender
profile, without factory startup, and makes a backup of the extension and user
preferences. A fresh normal-profile process verifies the enabled version and
installed files. Installation does not rewrite existing `.blend` projects;
a Blender process already running needs a restart to load the updated modules.

## Limits

Source images are 1254px artistic inputs, not 2K scans. Grayscale detail does not
provide measured independent albedo/roughness/normal data. Tiling prompts do not
guarantee identical opposing borders; [measured border diagnostics](validation/generated-material-assets.json)
are recorded without claiming seamlessness from a loose numeric threshold.
UV density, mapping, material scale and lighting need project-specific judgment.
Existing mask algorithms and legacy single-fill presets were not replaced.
No claim of complete Substance Painter parity or universally realistic output
follows from these tests.
