# Material Director region, coverage and calibration validation

## Production changes

- Exact assigned-face views use disposable evaluated meshes and explicit offscreen matrices. Material slots, artist geometry, UVs, camera settings, selection and visibility survive capture and failures. The camera-perspective regression checks actual Eevee pixels of narrow hardware behind unrelated faces.
- Opaque regions expose assignment/component/artist-selection membership, world bounds, triangle counts, UV-valid surface fraction and provenance. Height candidates explicitly disclose their geometric approximation. Native saved-face masks retain membership across selection changes and file reloads, reject topology drift, and support ordinary manual replacement from selected faces.
- `query_region` selects arbitrary parts of connected geometry using world/object/assignment-normalized bounds and optional geometric-normal limits. Centroid and exact triangle/box intersection methods both disclose whole-polygon precision. Request-scoped opaque IDs reject changed geometry, transforms and UVs before commit. Querying is read-only, including Explain mode; applying a region uses ordinary reviewed material commands. Saved query masks remain discoverable after reopening the file, with the full query and frozen membership in native authoring state.
- Surface coverage inspection uses triangle-area-weighted deterministic samples. Reductions add an ordinary final Levels mask, preserve existing source masks and opacity, and reject invalid UVs, near-zero coverage and impossible binary-mask requests. Compilation does not publish stale scene-field images or alter artist pixels.
- Geometry-derived scene fields now extend disposable sampling gutters before filtering. Occupied texels and stored source pixels remain unchanged. This prevents inverted masks from introducing false coverage at UV chart edges.
- Native REGION masks extend only disposable sampling buffers into UV gutters. Their exact membership raster and every occupied texel remain unchanged. A shared sampling identity versions CPU stacks, shader leaves, filtered prefixes, published mask references/anchors, and layer/folder cache provenance. This corrects neutral-material leakage when linear filtering straddles a UV-island boundary. Intentionally frozen artist caches retain their explicit refresh policy.

## Deterministic checks

Passed in Blender 5.2.1 LTS:

- `tests/integration/director_regions.py`: multiple slots on one mesh, multiple objects sharing a set, connected/selected regions, exact region views, cross-slot rejection, cleanup after capture failure, native persistence and topology rejection.
- The same test in visible mode: CAMERA perspective, 512×282 capture, 7,716 identifiable hardware pixels, substantial target framing, no unassigned red faces, unchanged camera/view/artist geometry.
- `tests/integration/director_coverage.py`: physical area versus unequal UV density, exact native-filter evaluation, repeated reductions, public `create_surface`/`patch_surface`, transaction undo, unchanged source/opacity, invalid/zero/binary rejection, read-only stale-field rejection.
- `tests/integration/director_scene_gutters.py`: an inverted upper-surface height field previously crossed the threshold at a chart boundary. Corrected sampling produces no false coverage; source pixels, authoring state, owned texels and image datablocks are unchanged.
- `tests/integration/director_region_gutters.py`: two exact disconnected regions with UV edges between texel centers. Selected boundary samples previously read 0.0632, 0.2168 and 0.1144; corrected CPU and actual shader-leaf samples are all 1. Excluded owned faces remain 0, source images and geometry are unchanged, and saved/reopened native state preserves membership and corrected sampling. A saved pre-fix blurred REGION leaf and a blurred published-mask reader both rebuild correctly; CPU stack and layer/folder cache identities change while the authored document stays identical.
- `tests/integration/director_region_queries.py`: a single connected bent member, exact intersection versus false bounding-box overlap, closed boundaries, object transforms and normal limits, normalized coordinates, cross-slot and foreign-registry rejection, read-only resource invariants, stale vertex/UV/transform rejection, public command undo and save/reopen. Visible mode captured the queried member as the expected 1:2 rectangle: 23,112 identifiable pixels at 512×282, with the rest of the connected mesh absent and camera/view/artist state unchanged.
- `tests/integration/director_coverage_tools.py`: actual Explain-mode `inspect_mask_coverage` and `inspect_material_channels` tools on SCENE/UP and live edge-wear masks. Disposable scene fields settle before measurement; returned mask hashes and surface areas match native evaluation. Every artist document, source pixel buffer, geometry/UV/assignment, selection and Blender ID set stays unchanged.

These checks establish implementation behavior. They are not live AI or artistic-quality evidence.

## Separate authored calibration

Command entry point: `scripts/material_director_calibration.py`.

Use `--product-root /path/to/frozen/product` (or `OP_MD_PRODUCT_ROOT`) for same-build calibration. New records include the product source SHA, commit, authoring-script SHA and whether the checkout carries a validated frozen-product pin. Product bytes are checked before and after each case. Historical unpinned attempts remain explicitly marked as development diagnostics in the gallery.

The five v3 neutral copies live in `.blender-test/material-director-benchmark/fixtures-eevee-v3`. They retain the original geometry, assignments, UVs, maps and fixed Eevee infrastructure. Nominal longest dimensions are 0.55 m, 0.90 m, 0.40 m, 0.40 m and 2 m. Every declaration says it is a diagnostic assumption, not measured source size. Original v2 fixture hashes remain unchanged.

A separate, deterministic script authored actual materials through public commands. It made no provider calls and supplied no calibration materials to Director. Results are explicitly `NOT_SCORED`, reviewer `PENDING`, and unfrozen development diagnostics:

| Subject | Captured calibration | Limitation |
| --- | --- | --- |
| Toolbox | `attempt-03/T01` | First two attempts retained: independent material inputs exceeded the unchanged 12-input budget. Final construction shares brass and uses baked AO grime. |
| Chair | `attempt-01/T02` | Nominal 1 mm weave may be under-resolved in the existing 1024 atlas. |
| Boots | `attempt-01/T03` | Sole is a disclosed geometric height approximation, not a verified semantic region. Wet and dry variants captured. |
| Vase | `attempt-01/T04` | Two narrow upper bands and a narrow gold mouth rim are visible; no independent aesthetic grade. |
| Rocks | `attempt-01/T05` | Diagnostic renders are very pale with weak moss contrast. No quality pass. |

Local portable review: `.blender-test/material-director-calibration/review.html`. Images are embedded; editable `.blend` files, channel buffers, statistics, commands and records remain in the accompanying local directories.

The initial development review contained 23 embedded PNGs. The 54- and 85-image reviews passed both complete PNG decoding and actual offline WKWebView checks after copying the HTML into an otherwise empty temporary directory. The current review includes every attempt through `attempt-09`: all **131 embedded PNGs** decode at 1024×1024 with zero external image/script dependencies. `portable_verification.json` pins gallery SHA `ce191ba7f79e30bc50ec58157ec7dcf67fbd7a8f84c21db385c37611548d8f44` and the exact libpng decoder binary. The final actual offline WKWebView check also passed: all 131 images loaded at 1024×1024 with zero external resources. `offline_browser_verification_09.json` records this result and the same source SHA; earlier checks remain preserved.

Boot wet-to-dry measurements show identical mud/sole mask buffers and source signatures. Outside the mud, maximum base-color, roughness, metallic and height differences are zero. Within occupied mud samples, mean roughness changed from 0.22585 to 0.78824 and mean color from 0.03164 to 0.11507; height remained unchanged.

## Pinned authored calibration

The final selected calibration is **all five cases in `attempt-09`**, on product source SHA `ef159e797072e8860afeade84515976b2a5fa36db5ede66a57e6a6db23094203` and commit `2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e`. `frozen_selection_09.json` pins each original calibration record, fixture, editable `.blend`, Eevee view, channel buffer, effect mask, geometric sample array, command and statistic file: 213 artifacts in total. Its SHA is `50e00193b9e0fc538fb32252a5ca30b3160d2dd0317f4dfd1093eae98a448f94`. Every case completed as `AUTHORED_AND_CAPTURED`; protected infrastructure and original fixture bytes remained unchanged. This is a separate authored engine diagnostic, with zero provider calls, reviewer `PENDING` and rating `NOT_SCORED`.

Selection07 remains byte-for-byte preserved at both its original `frozen_selection.json` path and `frozen_selection_07.json`. Selection08 remains in `frozen_selection_08.json`; its product lacked the subsequent filtered-prefix cache invalidation and is not substituted into the final selection.

The final boots calibration uses the supported PROCEDURAL/FBM source with TRIPLANAR projection. Its lower mud appears continuously mottled across the formerly broad chart-shaped transitions. Actual wet/dry mud and sole buffers are identical, as are their source signatures and opacity. All 227,568 valid samples outside mud have zero channel delta. In 92,531 occupied mud samples, mean roughness changes from 0.30235 to 0.77689 and mean color from 0.03498 to 0.10540; height remains unchanged. Evidence: `attempt-09/T03/wet_to_dry_invariants.json`. This confirms the constrained authored appearance edit, without assigning an artistic grade.

Earlier all-five `attempt-04` used product source SHA `8f283bb7881c3154ffd9d4324ab0b0674a203c2123655e0af0e08405f733f44d`. It and all intervening attempts are preserved rather than substituted into the final selection.

The chair now uses three explicit world direction groups within the normal texture-input budget: rails/arms, query-selected uprights, and the crosswise stretcher. Upright direction is visible in the captures. Shared upright tilt and whole-face boundaries at curved junctions remain disclosed approximations; the grain is coarse and the nominal 1 mm weave remains difficult to resolve.

`attempt-05/T03` separately requested triplanar projection on the legacy NOISE breakup source, preserving attempt-04 and using the same frozen product. Cross-attempt array comparison later proved this request was a no-op: legacy NOISE evaluates in UV coordinates and ignores projection. The original assumption describes intent, not achieved world mapping; `attempt-05/T03/mapping_diagnostic.json` records that failure. The supported alternative is a native PROCEDURAL/FBM mask with TRIPLANAR projection.

Actual wet/dry mud and sole arrays in attempt-05 are identical, including source signatures. All 241,334 valid samples outside mud have zero color, roughness, metallic and height delta. Occupied mud roughness changes from 0.22577 to 0.78823, mean color from 0.03166 to 0.11510, and height stays unchanged. Evidence: `attempt-05/T03/wet_to_dry_invariants.json`.

The ineffective attempt-05 projection request cannot establish the cause of the broad ankle transitions. A separate `attempt-06/T03` used PROCEDURAL/FBM/TRIPLANAR on the old product, then all five cases were rerun as attempts07, 08 and 09 on their recorded product pins. The narrow dark chart-edge fissures diagnosed below are absent. Rocks still appear very pale with weak moss contrast. These limitations remain visible; neither result receives an artistic pass.

Historical `no-edit-recapture-01/repeatability.json` records two independent reloads of every selected old-product case, followed by forced native CPU channel and effect-mask evaluation. All five cases have zero measured maximum and mean channel/mask deltas, including the newly separated pre-strength paint-loss field. `no-edit-recapture-02` separately recaptures the corrected attempt-06 boots. Valid pixels, geometric area samples, source signatures, opacities and original saved files remain unchanged. The declared absolute tolerances are 1e-6; implementation and source-product hashes are recorded. These are numeric repeatability passes, not artistic grades or spatial-sampling convergence studies.

Historical `no-edit-recapture-03` and `no-edit-recapture-04` preserve the all-five passes for attempts07 and 08. The final `no-edit-recapture-05/repeatability.json` independently reopens each attempt-09 authored state twice on product `ef159e…`. All five cases pass with **zero measured channel and mask deltas**; original saved files are unchanged. The report SHA is `870460f63682d62fa87e06129154a645e22a2c9b3f25864071288386c118191d`. It records exact capture and comparison implementation hashes, actual recaptured arrays and the 1e-6 frozen tolerances. This establishes repeatability for the final selected calibration, without claiming aesthetic quality or convergence of the geometric sampling approximation.

## Real-model mask-seam diagnosis

The old boots diagnostic had broad pale horizontal color bands. Those are absent in the new authored calibration, but dark chart-edge fissures remained. Separate disposable diagnostics established:

1. Disabling height and normal did **not** remove the fissures.
2. Hiding sole and mud while retaining physical rubber and relief **did** remove them.
3. Correcting scene-field gutter sampling removed the fissures from the unchanged saved calibration. Stored `.blend` checksum stayed unchanged.

Evidence: `.blender-test/material-director-calibration/seam-diagnostic-fixed-sampling-v2/03_create_close.png`. Earlier diagnostic attempts are retained. These are labeled engine isolation diagnostics, not repaired AI results.

### Exact-region chair boundaries

The live smoke04 chair attempt exposed a separate defect: adding exact connected-component masks around the seat and back introduced bright broken cushion-edge lines. An isolated diagnostic replayed its **unchanged final proposal** on frozen product `2cd636…`, saved the candidate, then reopened that same file with only `mesh_masks.py` and `shader_graph.py` changed. Actual 1024×1024 Eevee close renders reproduce the lines before the fix and remove them afterward.

All seven raw region-mask hashes, the full authored document hash and proposal hash match. Maximum occupied-texel delta is zero; protected scene state and original source files remain unchanged. The existing 1 mm weave still appears unresolved, so this correction does not make the original failed AI attempt successful. Original smoke evidence remains untouched.

Evidence: `.blender-test/material-director-region-gutter-diagnostic/comparison.json`, with original views and records under `before-02/` and `after/`. The first diagnostic script's statistics failure and its completed views are retained under `before/`. Snapshot03 and its calibration08/recapture04 are retained. Snapshot04 additionally completes the REGION cache dependency audit and filtered-prefix regression; calibration09/recapture05 match that audited product.

## Remaining limits

The final live comparison must match the pinned calibration's product SHA; a product change requires another separately recorded calibration. No-edit recapture tolerance checks are a separate measurement diagnostic. Historical development calibration is not a pinned baseline, a human-authored reference, a reviewer score, or proof of broad generalization. Candidate/commit/save/export parity, complete live use cases and independent artistic review belong to their separate task records.
