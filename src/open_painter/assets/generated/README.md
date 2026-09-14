# Generated surface detail

The initial 0.2.5 release added fifteen bitmap inputs using the built-in
`image_gen` tool on 2026-09-14. No third-party reference images, trademarks or
Substance assets were supplied. These sources follow the project's
**GPL-3.0-or-later** license; they are not CC0 downloads. Commercial distribution
of the add-on remains subject to that license.

The [provenance manifest](provenance.json) records every source's SHA-256, actual
dimensions, bit depth and original generated filename. The full prompt set is
in `docs/research/generated-material-prompts.json` and
`docs/research/generated-material-prompts-specialized.json` in the source repository.
OpenAI's [terms, Content section](https://openai.com/policies/terms-of-use/)
assign its rights in output to the user to the extent permitted by law, while
not promising uniqueness. This is provenance, not a claim of exclusive rights
or a guarantee concerning third-party rights.

All source PNGs are **1254 × 1254, 8-bit RGB**, loaded as Non-Color scalar data.
The generation prompt requested 2048 pixels; the tool returned 1254 pixels.
Those original pixels are retained. Texture-set output resolution and float32
evaluation are independent and unchanged.

These are artistic microstructure inputs, not measured albedo/roughness/normal
scans. The evaluator modulates editable material values with restrained amounts.
Height feeds the existing bump response. Curvature, AO, position and normals
still control mesh-dependent wear, dirt and other masks. Broad lighting and
shaded material previews are not used as base color textures.

The prompts request repeatability, but generated opposite borders are not
mathematically guaranteed to match. Source border diagnostics and actual
render comparisons are documented in `docs/generated-material-quality.md`.
Use the native Surface Detail picker and mapping controls to replace or adjust
the source for a project's UV density and physical scale.
## Material relief revision

The 0.2.6 library adds thirteen generated inputs with explicit wear/relief roles:
`abrasion_mask_v2`, `paint_loss_v2`, `brushed_relief_v2`, `rust_relief_v2`,
`timber_relief_v2`, `leather_relief_v2`, `denim_twill_v2`, `cement_relief_v2`
`charred_timber_v2`, `weld_bead_v2`, `burn_relief_v2`, `rubber_cracks_v2`
and `grease_deposits_v2`. Original 1254 × 1254 PNG pixels are retained; requested
2048 × 2048 generation was not returned by the tool. These are authored sources,
not measured scans. The 2048px material outputs are evaluated from these inputs.

Exact prompts are in `docs/research/coating-relief-prompts.json`,
`docs/research/abrasion-relief-prompts.json`, and
`docs/research/natural-relief-prompts.json`, `docs/research/industrial-relief-prompts.json`
and `docs/research/supplemental-relief-prompts.json`. `provenance.json` records source
hashes and dimensions. Earlier source files remain available for existing saved
materials. Grayscale source inputs are loaded as Non-Color data and shared.
