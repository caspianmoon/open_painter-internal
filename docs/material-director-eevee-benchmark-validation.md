# Material Director: live Eevee benchmark results

## Outcome

Implemented and ran the five-model benchmark with the actual signed-in Codex account, `gpt-6-astra`, and native image generation on 2026-09-22.

**Four of five primary sessions completed Create and both refinements. Several material requirements were visibly unmet.** The primary boots request failed at the provider before any tool call or material change. A separate, explicitly unscored boots diagnostic subsequently completed all three operations; it does not replace that failure.

- [Interactive primary gallery](../.blender-test/material-director-benchmark/eevee-comparison-01/gallery.html)
- [Machine report](../.blender-test/material-director-benchmark/eevee-comparison-01/report.md) and [frozen manifest](../.blender-test/material-director-benchmark/eevee-comparison-01/manifest.json)
- [Separate boots diagnostic gallery](../.blender-test/material-director-benchmark/diagnostic-eevee-t03-h-01/gallery.html)
- [Blinded human review packets](../.blender-test/material-director-benchmark/eevee-comparison-01/review_packets/)
- [Revised protocol](../Open_Painter_Material_Director_Eevee_Benchmark.md) and [reproduction instructions](../tests/benchmarks/material_director/README.md)

**Human artistic scores remain pending.** Completed operations and intact evidence establish delivery, not satisfaction of the material brief. No human corrections were made to Director's outputs; the three predefined prompts are the test instructions. Zero corrections does not establish a successful material.

The linked renders, Blender files and raw evidence are local artifacts under the Git-ignored `.blender-test/` directory; they are not included in the source commit. The tested working tree also contained pre-existing stability changes. The retained source archive and checksums identify the exact executed bytes independently of Git HEAD.

## Primary results

| Case | Execution | Session time | Native generated inputs | Observed result |
|---|---|---:|---:|---|
| Toolbox | 3 operations applied | 275.1 s | 1 | Paint and wear applied. Hardwood and brass layers explicitly hidden after part-selection API rejection. |
| Chair | 3 operations applied | 401.5 s | 3 | Wood remains unchanged through both revisions. Upholstery recolor clips most texels near black. |
| Boots | Provider failed before Create applied | 16.8 s | 0 | Correct inputs delivered; no proposals, tools or material changes. Two refinements unattempted. |
| Vase | 3 operations applied | 237.4 s | 1 | Two bands persist through revisions, but initial height bounds misplace them and cover much of the body in gold. |
| Six rocks | 3 operations applied | 301.5 s | 2 | Moss revisions preserve sources and environmental controls. Actual coverage change requires a defined measurement. |

Primary totals: **12 applied operations**, seven generated image inputs assigned, and 20 min 32 s of summed session wall time. This excludes fixture preparation, pilots and the later diagnostic. The toolbox image is assigned to a hidden wood layer; assignment alone does not prove visible use. Monetary cost and active human operating time were not exposed/measured.

## What the harness enforces

- Five clean mesh-only imports with original source appearance removed, useful object/material boundaries preserved, documented geometry/UV repairs, and fresh texture sets.
- One prebuilt, locked Eevee rig shared by every fixture: one camera, three neutral lights, fixed World/color management, 64 samples and 1024 × 1024 renders. Models are normalized into its volume.
- Fresh 1024 AO, curvature, world-normal, position and thickness maps. AO and geometry maps are baked by deterministic CPU code; the benchmark does not use Cycles.
- Actual model screenshots and map-image bytes sent through the normal Material Director integration. No benchmark scene construction or rendering task is delegated to the tested model.
- Exact Create/Refine prompts, bounded sessions, immutable attempt records, pinned model/runtime/source/library/fixture hashes, and preservation of failed attempts.
- The ten requested Eevee filenames, evaluated channel buffers, material-state dumps, change receipts, readable native layer/mask controls, and packed final `.blend` files for completed cases.
- Missing/nonfinite evidence blocks a completion claim. Fixed infrastructure, geometry, assignments, maps and protected layers are checked across checkpoints.
- Five weighted human score categories and an explicit artist-intervention count. No invented numerical pass threshold or AI-generated artistic rating.

The primary run used Blender **5.2.1 LTS**, Codex runtime **0.154.0-alpha.6.2**, and source SHA-256 `68f0e5bd0d1a315046a8688ef8736e95cc5e2b7417185b7e745388da40463390`. The exact 281 fingerprinted source files are retained in [frozen-source.zip](../.blender-test/material-director-benchmark/eevee-comparison-01/frozen-source.zip), with [file checksums](../.blender-test/material-director-benchmark/eevee-comparison-01/frozen-source.json). Subsequent report wording changes do not change the tested implementation or recorded outcomes.

## Measured revision findings

These are post-hoc diagnostics, not independent human scores. See the [revision audit](../.blender-test/material-director-benchmark/eevee-comparison-01/diagnostics/md_revision_audit.json).

**Chair.** Both refinements preserve all wood channel hashes and source images. Refine 1 adds only an upholstery HSL filter and renames its layer. The negative lightness adjustment leaves 72.50% of valid upholstery texels with maximum linear RGB below 0.01. Refine 2 doubles only upholstery color/height UV scale from 32 to 64; it preserves the same generated textures, including the dark recolor.

**Vase.** Director received the actual 2-unit height but left every relative-height field at bounds `[0, 1]`. Its band thresholds therefore select approximately 35–38% of model height; gold begins near 49.4%. Refine 1 changes only the two band colors. Refine 2 subtracts 0.03 from all eight existing boundary thresholds, preserving band widths and spacing. It does not correct the initial range error.

**Toolbox.** Attempts to use assigned handle/latch meshes as proximity references are rejected by the authoring API. Director reports the limitation, removes those masks, and hides the wood/brass layers. Later wear edits retain layer/mask identities and random seeds. The final revision changes the intact-paint mask strength from 1 to 0.5 and halves grime opacity. Whole-set mean metallic contribution falls to 51.42% of its previous value; that is not a surface-area measurement.

**Rocks.** Recoloring changes only moss color. The final edit changes existing breakup thresholds from 0.32/0.62 to 0.43/0.73, preserving seed 71, scale 16, and upward-facing/concavity controls. A separate [native mask measurement](../.blender-test/material-director-benchmark/eevee-comparison-01/diagnostics/md_moss_coverage_audit.json) reproduces archived roughness outputs exactly and integrates the actual mask over all 63,118 triangles. Surface-area-weighted mean mask strength falls **54.32%**. Binary covered-area reduction is **23.79%, 50.71%, 65.82%, or 77.15%** at mask thresholds 0.01, 0.10, 0.25, or 0.50 respectively. No new covered sample locations appear. A single universal visual “coverage percentage” cannot be inferred from these different measurements.

## Separate boots diagnostic

The same frozen build and clean fixture completed all three operations in **246.5 s**, generating one rubber texture. Correct explicit world-height bounds `[0, 2]` were used. Drying changes only the two mud colors and roughness values; all masks and authored placement remain identical. The final edit halves both mud-layer opacities, preserving the pattern and reducing contribution rather than measuring half the covered surface area. See the [separate audit](../.blender-test/material-director-benchmark/eevee-comparison-01/diagnostics/md_boots_diagnostic_audit.json).

The rendered rubber also has unintended pale shaft bands. A [buffer diagnostic](../.blender-test/material-director-benchmark/eevee-comparison-01/diagnostics/md_boots_surface_diagnostic.json) finds those bands already in the native CPU base-color output, with approximately constant roughness across them. The generated input is a mustard surface and no band layer was authored. The exact sampling/compositing cause remains unresolved; this requires a material-pipeline investigation.

The original failure's cause remains unknown: the provider emitted a failed completion without a surfaced error explanation. The successful diagnostic establishes that failure did not repeat in this attempt. It remains excluded from primary scores and success counts.

## Follow-up work supported by the results

1. Add explicit object/material-part selection masks for meshes sharing one texture set; proximity to external influencers cannot perform that job.
2. Make object-relative normalized height explicit in the authoring API and instructions, with actual bounds supplied automatically or required in proposals.
3. Expose color-adjustment preview statistics and detect unintended clipping before committing a requested recolor.
4. Isolate material-slot faces in target screenshots. The chair's separate slots currently receive object-level views showing the whole chair.
5. Define surface-area coverage and its threshold before the next comparison, then measure percentage requests instead of estimating parameter changes.
6. Surface structured provider failure details and distinguish applied partial work from completion of every requested component.
7. Investigate the boots' unintended base-color bands using the retained generated image, projection settings and CPU buffers.

## Verification and limits

All five fixtures passed **291 preparation checks**. The harness has **31 passing focused tests**, plus real Blender checks for CPU AO, immutable rig capture, nonfinite evidence handling and native inspector paging. The live audit verified 41 primary render records, 219 UI screenshots, 189 decoded buffer records and 120 outgoing image records. Completed cases preserve the fixed scene and source geometry/maps; reopening the failed boots file confirms the initial state remained unchanged. The separate boots diagnostic passed the same delivery/infrastructure checks.

This first comparison has one H-enabled sample per model. It does not establish statistical reliability, a causal improvement from mesh maps, game-engine/export compatibility, or independent artistic quality. Optional P/H repeats, reference-image cases, export/4K checks and artist baselines were not part of this primary run.

Regenerate the primary gallery without modifying its images:

```sh
python3 scripts/benchmark_material_director_gallery.py \
  .blender-test/material-director-benchmark/eevee-comparison-01
```
