# Generated masks and material relief — 0.2.6

The previous revision was rejected for bright procedural scratch ticks,
striped paint fragments and weak surface depth. This revision replaces those
patterns with generated bitmap masks and changes the actual editable material
layers. The results can be inspected in the [50-material before/after review](material-surface-review.html),
including the evaluated height and combined normal maps.

## New source images

Thirteen new inputs were generated with the built-in `image_gen` tool: abrasion,
brushed metal, paint loss, rust relief, timber, carbonized timber, leather,
denim twill, cement, weld beads, heat blisters, rubber fissures and grease.
The rubber crack mask was refined with the same image-generation edit tool to
make crack centers encode positive coverage consistently.

All selected original PNG pixels are preserved under
[`src/open_painter/assets/generated`](../src/open_painter/assets/generated/).
The tool returned **1254 × 1254, 8-bit RGB** images despite 2048px requests.
The material outputs are composed at **2048 × 2048** for this review; that does
not imply that the source images contain native 2K detail. Earlier sources
remain available for saved projects, giving 28 registered inputs in total.

[Source index and exact prompts](research/material-relief-source-index.md).
The [provenance manifest](../src/open_painter/assets/generated/provenance.json)
records every file's hash, dimensions and provenance. These are authored
grayscale inputs, not measured physical scans. No third-party material assets
were used for this revision. Opposite-edge continuity was requested but is not
guaranteed by generation.

## Material changes

Scratches use irregular image coverage shared by roughness, recessed height
and, where a cut exposes a substrate, color/metalness. Clean metal, scratched
metal and coating scuffs use different strengths. The first replacement's
overly dense bright aluminum wear was reduced after rendering it.

Chipped coatings have separate substrate, primer and raised paint levels.
The same generated loss mask controls their boundaries. Narrow levels keep
the primer transition from turning large rust areas into translucent stains.
Isotropic masks use triplanar projection to avoid the former latitude/longitude
stripe and pole artifacts. Grain and weave keep their deliberate UV direction.

Corrosion, hide, cement, cloth and wood have material-specific relief.
Cloth heights and scale were revised after the first render made the yarns
too large. Weld, blister, rubber and grease sources were added after the full
review showed that their named features were still too weak or synthetic.
Smooth chrome, automotive paint and polished stone intentionally retain smooth
finishes rather than acquiring roughness appropriate to rust or concrete.

The catalog still contains the same 50 material identities. Its layers, masks,
source mappings, values and filters remain editable. A raised `height_range`
is stored as an ordinary Height-channel Levels effect, with editable output
endpoints. Reapplying a preset uses the new recipe; installing it does not
rewrite existing painted stacks.

Individual observed results: [metals and coatings](material-relief-metal-review.md),
[industrial surfaces and polymers](material-relief-industrial-review.md),
[natural materials](material-relief-natural-review.md).

## Height and normals

The preview already connected Height into Blender's Bump node. The old recipes
often used tiny offsets, then attenuated them again with opacity. The revised
recipes have usable grain and coating height separation.

Export now converts the evaluated height into tangent-space normal detail,
accounting for assigned geometry, UV scale/shear, mirrored mapping, world
transforms and connected UV charts. The result perturbs authored normals using
the same surface-gradient behavior as Blender's Bump shader. Actual Cycles
render comparisons cover neutral and tilted authored normals.

The authoring Normal channel remains separate so the preview applies bump
once. Exported Normal includes the final height relief. Use the separate
Height export for displacement; applying it through a second bump node on
the combined normal would repeat that relief. Height distance now survives
material archive round trips and texture-set duplication.

A live-preview cache defect was also fixed: triplanar image masks must include
the geometry revision in their cache identity. Moving an object now updates
both its generated detail and mask coverage consistently.

## Review and validation

Final overview renders use Blender 5.2.1 LTS, the curved sample, 2048px textures,
512px Cycles CPU output and 32 samples. These match the recorded resolution,
sample count and fixture of the rejected revision. Additional 768px closeups
were used to inspect scratch shape and coating relief. The gallery lists
recorded conditions honestly; older captures lack some of the new hash fields.

Height and combined normal files are **16-bit linear PNGs**, without a contrast
remap. The review links to these full-resolution local artifacts. Library
thumbnails are taken from the final 512px actual material renders.

Validation includes all 50 distinct color/height outputs, finite normalized
normal maps, packed image/source integrity, portability, rollback, live GPU
parity, native UI workflows and a real extension installation. Full-suite logs
and final recipe/source checks are indexed in
[`validation/material-relief-validation.json`](validation/material-relief-validation.json).
The local release record adds the package checksum and installation result:
[`validation/material-relief-release.json`](validation/material-relief-release.json).
The review establishes visible improvements and functioning relief; it does
not establish physical scan accuracy, exact seamlessness or a frame-rate claim.

The large local review renders/maps live under `.blender-test/material-relief/`.
They are review artifacts, separate from the extension's generated source
assets and editable recipes.
