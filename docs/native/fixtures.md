# Versioned Gate 0 fixtures

Definitions are in `scripts/native_fixtures.py`; runner:
`scripts/benchmark_windows_viewport.py`. Existing timing protocol is retained.

| CLI fixture | Identity | Scene/source | Edited coverage |
| --- | --- | --- | --- |
| plain | two_fills_plain_v1 | Plane, two uniform fills | coat opacity |
| masked | two_fills_cellular_mask_v1 | Plane, two fills, CELLULAR mask | mask strength |
| image | nonuniform_image_fill_v1 | Cube, two fills, UV image with red/green gradients | coat opacity |
| paint | paint_layer_v1 | Cube, two fills plus paint layer with persisted three-sample stroke | coat opacity |

The image is `(u, v, 0.2, 1)` at texel centers in Non-Color space. The stroke
uses top-face points `(-.2,.1,1), (0,.1,1), (.2,.1,1)`, normal `(0,0,1)`,
pressures `.4,.8,1`, radius `.2`, hardness `.6`, flow `1`, opacity `.9`,
MAX accumulation and BASE_COLOR/ROUGHNESS channels. These samples are stored
in the existing project stroke representation and evaluated by the legacy
raster; they do not claim native brush support or OS input simulation.

New fixtures require `--layers 2` (paint adds its own third layer). Validation
requires a spatial BASE_COLOR red range above `1e-4`. All fixtures exercise
roughness edits, coverage edits, opacity and unchanged republishing. Cold
initialization is separate. Mips and actual viewport sampling are tested by
the separate Gate 1 protocol, not established by this component benchmark.

`two_fills_cellular_mask_v1` supplies the controlled procedural mask case;
no duplicate `procedural_mask_v1` alias is introduced.

The Route C diagnostic fixture additionally applies a smooth UV coverage ramp
and shader dab to a C-produced two-fill plan. This is a distinct experimental
fixture, not equivalent to the image/stroke legacy workloads and not suitable
for a performance A/B claim. Its corrected background mip validation passes; it remains a component test.
