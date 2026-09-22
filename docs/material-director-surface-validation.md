# Material Director surface validation

These are deterministic engine checks run in Blender 5.2.1 LTS during implementation. They use diagnostic geometry and synthetic images, make no provider calls, and assign no artistic scores. They do not establish live Codex success on the five benchmark assets. The earlier artifacts do not record a product hash; do not describe them as a complete rerun of the final frozen build.

## Checks actually run

| Check | Observed result | Evidence |
| --- | --- | --- |
| [Native surfaces](../tests/integration/material_surfaces.py) | PASS: evaluated 1 mm repeat; equivalent world-scale representations; nonuniform transforms; nine native recipes; member frames; scoped revisions and undo; paired-band translation preserving widths and gap; multiple objects sharing one atlas; save/reopen preservation. Coordinated native mud and ordinary fill-layer drying changed appearance while retaining masks. | [run log](../.blender-test/material-director-remedy-validation/logs/material-surfaces-test.log); [saved material file](../.blender-test/material-surfaces-save.blend) |
| [Source integration](../tests/integration/material_surface_sources.py) | PASS: image-content recolor changes actual pixels while retaining structure; bundle color-space copies preserve original images; shared metric mapping; retained member frame and untouched height channel; explicit WORLD wood direction on perpendicular faces, including generated-source assignment. The generated-source boundary used a synthetic PNG, not image generation. | [run log](../.blender-test/material-director-remedy-validation/logs/material-surface-sources.log) |
| [Eevee/CPU/export parity](../tests/integration/material_surface_parity.py) | PASS: actual Eevee channel renders compared with CPU buffers and production 16-bit PNG exports. | [numeric report](../.blender-test/material-remedy-parity/result.json); adjacent EXRs and `maps/` |
| [Metric masks](../tests/integration/material_metric_masks.py) | PASS: circular chip radius, scale-equivalent representations, Eevee/CPU/export agreement, metric field distance/bounds, and native paint-loss group composition. | [run log](../.blender-test/material-director-remedy-validation/logs/material-metric-masks.log); [numeric report](../.blender-test/material-metric-masks/result.json); adjacent EXRs and `maps/` |
| [Control contracts](../tests/integration/material_surface_control_contracts.py) | PASS: ineffective requests fail without changing the live document; supported relative scale, procedural variation and image palette edits change evaluated pixels. Registered in `scripts/run_tests.py`. | [run log](../.blender-test/material-director-remedy-validation/logs/material-surface-control-contracts.log) |

The logs above are durable workspace copies. Their original paths and SHA-256 hashes are recorded in the [log inventory](../.blender-test/material-director-remedy-validation/log_inventory.json). Source tests and the listed artifacts identify what was exercised.

## Measured parity

The weave diagnostic uses a 16 mm plane at 512 × 512, with a declared 1 mm repeat and 32 samples per repeat. MAE means mean absolute error. Channel comparisons use equivalent image sampling; raw texel differences are separately retained in the report.

| Channel | CPU–Eevee MAE | Export–Eevee MAE | Maximum error, either comparison |
| --- | ---: | ---: | ---: |
| Base color | 0.00005425 | 0.00004854 | 0.00024414 |
| Roughness | 0.00022812 | 0.00022618 | 0.00048828 |
| Height | 0.00017036 | 0.00017036 | 0.00048828 |

The channel MAE acceptance limit was 0.001. Native bump versus exported normal relief had MAE **0.00387724**, maximum **0.02929688**, against a mean-error limit of **0.02**. Eevee screen derivatives and texture-space export derivatives differ at thread peaks; this was not exact normal equivalence.

The circular-chip diagnostic declared a **0.280 mm** radius and measured **0.28125 mm**. CPU–Eevee MAE was **0.00002750**; export–Eevee MAE was **0.00002527**. Its scene-field distance was **4 mm**. This tests the regular circular primitive before geometry clipping, not irregular chip realism.

## Rejected controls and remaining limits

- A known physical scale requires `period_m`; `relative_period` is rejected instead of silently ignored. Unknown-size surfaces retain relative world mapping. Nondefault object/member frames, origins or axes require a physical declaration; metadata explicitly reports `LEGACY_RELATIVE_WORLD` for the fallback, including wood pores.
- `color_variation` requires a procedural or scalar-detail source. Image-content and constant-color requests without that source are rejected. Image-content `COLOR_ONLY` remains supported through an editable palette filter.
- Physical size is never inferred from display units or normalized geometry. Metric controls require an explicit conversion and provenance. A nominal size declaration is an assumption, not measured source dimensions.
- The 1 mm result above is a resolved diagnostic. It does not prove that 1 mm detail survives a whole asset's atlas, including a 2K atlas. Under-resolution reporting uses an area-average estimate; stretched UVs can be worse, and resolution is not increased automatically.
- Imported/generated bundles enforce shared mapping, not physically calibrated channel content. Generated source metadata remains an approximation. Metric projected tangent-normal bundles are rejected: use shared UV mapping for tangent normals or metric height for projected relief. Generated fill-mask images also reject `physical_extent_m`; that control currently applies to channel images.
- Editable native graphs remain the material state. These checks cover the named cases; they do not guarantee every artist-modified graph, UV layout or source image. Live AI completion, semantic placement and visual quality require their separate benchmark evidence and review.

The final control-contract run passed after the validation-only changes at product SHA-256 `2cd6369808ea02ee49bc2498b9a2521758e320810fb809c08a2244fcac172096`. No new tests were run to write this document.
