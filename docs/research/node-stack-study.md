# What Open Painter can take from the node-stack add-on (3.0.0)

Study date: 2026-09-14. Source: the supplied source archive (version 3.0.0, GPL-3.0-or-later; `bake_common.py`
verified byte-identical to the upstream repository's master). Compared against the current Open Painter
checkout (`src/open_painter`, 0.2.5/0.2.6 in progress on `task/generated-material-quality`).

Method: seven independent readers covered the 40 node-stack add-on modules by subsystem (~95 raw findings); the 16
highest-value adoptable findings were each checked by two adversarial verifiers (one against the node-stack add-on source, one
against Open Painter source and the plan constraints); a completeness critic then re-checked every citation in the
synthesized report and swept the operator/enum inventory for omissions. Sections 2-4 hold the verified findings with
the critic's corrections applied; section 5 holds the critic's additions, which were spot-checked against source but
not adversarially verified. Nothing here has been implemented or run in Blender.

## 1. Scope and license

- Both projects are `SPDX:GPL-3.0-or-later` (the node-stack add-on `blender_manifest.toml:16`, `COPYING`; OP `blender_manifest.toml:10`, `LICENSE`). Verbatim ports are license-compatible provided the copied code keeps its copyright notice and the OP `LICENSE`/CHANGELOG record the origin (file, commit) of each ported function.
- The node-stack add-on is a shader-node layer stack: every layer, mask and modifier is a node group rebuilt by `node_connections.py`/`node_arrangements.py`. OP evaluates layers on the CPU into float32 channel images displayed through a fixed material (`ARCHITECTURE.md:36-47, 64-71`). Node plumbing, `lib.blend` groups, halt/reconnect flags and neighbor-UV bump trees do not transfer; algorithms, workflows, edge cases and data-model ideas do.
- Hard constraints applied (`Open_Painter_Updated_Codex_Plan.md:27-40`): CPU evaluator stays the reference (3), fixed display material (4), format v2 preserved (5), catalog numerics are the baseline (6), no silent quality reduction (7), no package code (8), no threads (9), no network (11), no destructive user-data edits (12).

## 2. Ranked recommendations

### Port-algorithm

**R1. Signed mean-curvature vertex map** (M5, medium, high value)
- Take: `fill_curvature_vcol` (`bake_common.py:170-262`): weld vertices by rounding `co*1e5` (188-195), pair faces per welded edge by lexsort (197-214), signed dihedral angle weighted by edge length with convexity from `(c2-c1)·n1 < 0` (216-222), weld-only edges count only on the concave side (224-228), `k = acc/(4*varea)`, `signal = clip(k*distance,-1,1)` (230-240), one relaxation pass (242-251), `0.5+0.5*signal` (253). The node-stack add-on then multiplies a contact AO (`4580-4602`) and normalizes (`normalize_image_values`, 264-275, applied at 4914-4919).
- OP now: `baking/maps/curvature.py:13-35` keeps only convex crease edges above an angle threshold and rasterizes fixed-pixel-width lines (38-84), tagged `GEOMETRIC_CREASES_V1`; `README.md:162` and `CHANGELOG.md:60,67` state smooth/concave curvature is not implemented. Rasterization of a per-vertex scalar already exists: `geometry/uv_rasterizer.py:5-25` weights and `baking/maps/utility.py:133-137` (`weights @ values`); `geometry/snapshots.py:20-79` exposes positions/triangles on the evaluated copy.
- Constraints: pure NumPy on the `to_mesh()` copy, no temp color attribute on the user mesh (12), no shader bake. Must be a NEW kind appended to `properties/bake.py:23-27` (comment: append only) because `generators/edge_wear.py:7-9`, `evaluation/live_masks.py:129-130`, `smart_materials/composer.py:31-32` and 24 catalog rows assume black=flat for `CURVATURE` (6). Drop the contact-AO multiply: `edge_wear` and `DIRT` already multiply the baked AO. Record the normalization range in `geometry/provenance.py:35-40`.
- First step: add `MEAN_CURVATURE` to the enum plus `properties/mask.py:80,88`, `generators/base.py:13-22`, `bake_manager.py:65-66`; port lines 188-253 into `baking/maps/mean_curvature.py`; acceptance test on a beveled cube (sign outside vs cavity), a mirrored object and the unchanged `tests/integration/geometric_curvature.py` baseline. Note the limit: on a low-poly mesh with only hard edges (default cube) every vertex reads identical; the map is useful with evaluated bevel/subsurf.

**R2. UV-derived UDIM tile enumeration and detection** (M11, small, high value)
- Take: `get_tile_numbers` (`UDIM.py:88-151`): gather all loop UVs, drop loops within `UV_TOLERANCE/2 = 0.05` of an integer boundary (`UV_TOLERANCE = 0.1` at `UDIM.py:7`; filter at 118-126), floor, `unique`, `tile = 1001 + u + v*10` (137-142); `is_uvmap_udim` = `any(uv > 1.0 + tol/2)` (153-185), wired to auto-tick "Use UDIM Tiles" via `BaseOperator.py:141-157`.
- Fix two defects when porting: the clamp `u = min(max(u,0),10)` lets u=10 alias into the next row (1011); u must be 0..9. The boundary filter drops loops on either axis, so a quad exactly on integer boundaries contributes no tile; use triangle centroids (or OP's raster coverage) instead of vertex filtering.
- OP now: `baking/diagnostics.py:67-73` only warns "UVs outside the single supported tile"; `geometry/texel_density.py:21` counts outside triangles; no tile identity anywhere (`docs/udim-tile-access.md`). `snapshots.targets(ts)` already gives per-object evaluated UV arrays without the `mode_set` the node-stack add-on uses (12).
- Constraints: do NOT wire it to tiled-image creation yet; `core/image_manager.py:35-40` writes through `Image.pixels`, which reaches only tile 1001 (7, M11 gate `implementation-status.md:202`). Land it as an informational diagnostic listing needed tiles.
- First step: `geometry/udim.py: tile_numbers(ts)` over `targets(ts)['uv'][tri.loops]` for slot-filtered triangles; replace the generic warning with the enumerated list; test a two-tile plane and an on-boundary quad.

**R3. Multi-file PBR import by filename synonyms** (corrected: OP status is partial, not missing; no milestone, medium)
- Take: `open_images_to_single_layer` (`Layer.py:1645-1830`): synonym table (1679-1687), matcher for `_syn`/` syn`/`.x` suffixes, whitespace/underscore variants and 3-5 letter prefixes (1597-1643), GL preferred over DX only when both exist (1673-1677, 1710-1712), Invert for glossiness/smoothness (1780-1795), Non-Color for linear channels unless `is_dirty` (1760-1762), unmatched images removed (1827-1830). Correction: `normal_map_flip_y` (1585-1589) is declared and drawn but never applied; the node-stack add-on does not flip DX maps.
- OP now: single-file `open_painter.import_texture` (`ui/native.py:219-233`) with Blender's default colorspace; `core/surface_presets.py:138-149` already loads a multi-map set into one fill layer from an explicit JSON manifest; `INVERT` and `NORMAL_INVERT_Y` exist (`texture_engine/filters.py:301,345-350`); `CHANNEL_MIXER` (`properties/effect.py:55-56`) gives an RGB swizzle for channels; masks always read component 0 (`mask_eval.py:168-178`).
- Constraints: ImportHelper + per-channel `OP_Channel.image` is already the data model. Any source-component enum must default to current behaviour (RGB kept on channels, `buffers.py:1-4`; luminance for `detail_image`, `channel_eval.py:231-233`), not "R" (6). GPU kernels hard-read `.r` (`live_masks.py:112-115`, `live_gpu.py:125`), so non-default selections need kernel support or the explicit CPU fallback.
- First step: port `is_synonym_in_image_name` into `core/pbr_import.py` with a pure test on Poly Haven/AmbientCG names; operator creates one FILL layer, sets sRGB for BASE_COLOR and Non-Color otherwise as `surface_presets.py:146` does.

### Adopt-idea

**R4. Supersampled (2x) Cycles bakes with margin scaling** (M5, small)
- Node-stack add-on: `margin *= aa_level; width/height *= aa_level` before `prepare_bake_settings` (`bake_common.py:2597-2611`, `Bake.py:2137-2145`); post order dither → denoise → resize → FXAA (`Bake.py:2223-2240` for channel bakes, `bake_common.py:2408-2425` for bake targets); `bake_to_entity` doubles size for other-object/wireframe (`3958-3970`) but does not scale margin there. Correction: the `STRAIGHT_OVER` group in `resize_image` (6080-6289) un-premultiplies rather than averaging premultiplied colour; the alpha pass is 6216-6254.
- OP now: `baking/cycles_baker.py:33-35` allocates exactly `int(ts.resolution)`, margin copied unscaled (45), no resample; no AA property in `properties/bake.py:31-103`; `docs/bake-diagnostics.md:104-106` states no AA.
- Constraints: explicit opt-in, default 1x keeps numerics (6); output stays authored size (7). Correction to the reader's figure: 2x of a 4K set is 8192² RGBA float = 1 GiB, plus the NumPy working copy; 2x of 8K is 4 GiB. Gate hard on bytes, not "warn at 4K"; `evaluation/memory.py` does not govern Blender image RAM (plan line 361). Downscale in NumPy (2x2 box on decoded normals then renormalize); premultiply is a no-op on OP Cycles targets (alpha prefilled 1).
- First step: `aa_level` on `OP_BakeSettings`, added to `provenance.py:35` `shared_bake` and `ui/baking_panel.py:148`; parity test that edge-pixel variance drops on a two-island 512² fixture.

**R5. AO distance, inside/flip, and COMBINED-vs-AO pass choice** (M5, small)
- Node-stack add-on: no alpha in scene → COMBINED with an AO node into Emission because it denoises better (`bake_common.py:4143-4173, 4404-4419`); alpha → bake type AO with `use_fast_gi` and `world.light_settings.distance = ao_distance` (4394-4403); THICKNESS = 1 − AO(inside, only_local) (4421-4434); `hide_other_objs` unless only_local (4184); defaults `BakeToLayer.py:229-233, 445-448`. Nuance: the world-distance branch fires only when the node-stack add-on tree itself exposes alpha.
- OP now: `cycles_baker.py` bakes type AO with no distance; `staging.py:18-24` creates the staging scene with no World (a fresh World's `light_settings.distance` is 10.0; verified headless on 5.2.1); `ao_scope` SET/PAIR exists (`properties/bake.py:39-42`) but is an occluder-scope analog, not `only_local`; thickness exists as a CPU raycast (`maps/utility.py:155-232`), so the inside-AO variant is redundant.
- Constraints: owned World in the staging scene, removed in `close()` (12); `ao_distance` default 10.0 (7). Adding the key to the AO provenance tuple marks every existing AO map Stale after upgrade (exact string compare, `provenance.py:51-57`); either omit at default or normalize old records. For selected-to-active COMBINED bakes the source copies keep user materials (`render_geometry.py:78-84`), so owned emission materials are needed on sources too.
- First step: World + `ao_distance` only (skip COMBINED variant until R6 lands the emission-graph allowance).

**R6. Bevel-node edge mask and bevel normal** (M5, medium)
- Node-stack add-on: BEVEL_MASK = Geometry.Normal vs `ShaderNodeBevel(samples, radius)`, CROSS→length or DOT→1−dot, into Emission, EMIT bake (`bake_common.py:4355-4388, 4604-4631`); BEVEL_NORMAL = Bevel into Diffuse Normal, NORMAL bake in TANGENT space (4178-4179, 879-880); defaults `BakeToLayer.py:236-246, 476-489`; modifiers kept for surface-based types (4299-4300).
- OP now: nothing (`grep -i bevel` empty); `cycles_baker.py:17-18` restricts to NORMAL/AO; `docs/texture-workflow-v1.md:32` lists bevel-mask as out of scope. Crease curvature has a UV-texel width, not a world-space radius.
- Constraints: the Bevel/Emission nodes live only in the owned transient "OP Bake Target" material (`staging.py:39-47`, removed in 73-81); the display material is untouched (4). New kinds appended to the enum and to `mask.py:80,88`, `generators/base.py:21`, `provenance.py:39-43`, `live_masks.py:18`; force `use_selected_to_active=False` for these kinds.
- First step: `BEVEL_MASK` kind with radius/samples/method, EMIT bake through an owned emission graph; compare against `CURVATURE` on a hard-edged cube.

**R7. Per-channel mask participation + layer-level mask mute** (no milestone, medium)
- Node-stack add-on: `YLayerMaskChannel.enable` (`Mask.py:2127-2133`) per mask per root channel (`Mask.py:2354`); mix nodes removed when `not layer.enable_masks or not mask.enable or not c.enable` (`subtree.py:594-609`); rows dim when the layer channel is off (`ui.py:3676-3695`); `YLayer.enable_masks` (`Layer.py:6328-6333`).
- OP now: `OP_Mask.enabled` only (`properties/mask.py:23`); `evaluate_masks(ts, layer, ...)` is channel-agnostic (`mask_eval.py:100-112`) and multiplied into every channel (`evaluator.py:184-187`); `live_inputs.py:301-321`/`live_gpu.py:125` same.
- Constraints: defaults (all channels, `enable_masks=True`) keep the 50 materials/18 masks identical (6); new RNA fields serialize through `layer_stack.snapshot` on format v2 (`SMART_MATERIAL_FORMAT.md:30`). Fixed eight channels avoid the node-stack add-on's resize/paste-by-name bookkeeping (`channel_common.py:28-38`, `Layer.py:4404-4420`).
- First step: BoolVectorProperty(size=8) on OP_Mask; filter the mask tuple per channel in `compose()` and `_stack_signature`; empty vector from old files = all on; GPU plan honours the same filter.

**R8. Previous-Layers adjustment layer** (M7, medium)
- Node-stack add-on: `PREV_LAYERS` type (`common.py:320`) whose source is the group input (`common.py:622`, `node_connections.py:1068`), chained only within the same `parent_idx` (783-803), Adjustment submenu creating it with one modifier (`ui.py:6811-6834`, `Layer.py:1349`), channel enable is just `ch.enable` (`common.py:5799-5800`), no mapping UI (`common.py:3542`). Corrections: the min-with-group-alpha clamp is created (`subtree.py:723`) but only wired for GROUP (`node_connections.py:1397-1416, 1520-1523`); "cannot be baked" is only a missing UI button, not a guard. The node-stack add-on's own 3.0 migration replaced root-channel modifier stacks with this type (`versioning.py:1678-1688`).
- OP now: PAINT/FILL/FOLDER only (`properties/layer.py:31-37`); `format.py:78` rejects other types explicitly; `references.py:1-5` samples only pre-composition sources; adjustment layers listed as not established (`Codex_Plan.md:76`). `compose()` already carries rgb+coverage bottom-to-top (`evaluator.py:163-232`) and mask FILTER sources already operate on the accumulated result (`mask_eval.py:125-135`).
- Constraints: `ADJUST` appended to the enum; src = backdrop, run `apply_texture_effects`, blend with layer mode/opacity/mask; halo via `stack_footprint(prop.effects)` (`evaluator.py:98-111`). `live_inputs.compose` (~285) and `buffers.scalar_source` (32-38) treat unknown kinds as FILL, so an explicit branch or the `_fallbacks` CPU route (`live_preview.py:280-284`) is mandatory (7).

**R9. Non-destructive freeze of a single layer/mask with Rebake/Remove** (M7, medium)
- Node-stack add-on: `bake_entity_as_image` isolates the entity (enable, opacity 1, MIX, masks off or only the target mask active, `bake_common.py:5593-5651`), EMIT bakes, restores in reverse (5745-5802); the `set_image_to_entity` branch inside `bake_entity_as_image` stores bake info, creates `baked_source`, sets `use_baked` (5809-5896); node selection by `use_baked` (`node_connections.py:996-1004`); duplicate-as-image-mask (`BakeToLayer.py:1370-1414`); remove (1440-1506); UI `ui.py:2449-2472`. Corrections: each rebake creates a NEW datablock and `replace_image` re-points users (`common.py:1876-1925`); entity modifiers are excluded from the bake and stay live on top.
- OP now: `ui/properties_panel.py:37-47` BAKE is destructive (`layer.masks.clear()`, tested in `tests/integration/edge_wear_textures.py:60-63`); per-mask freeze exists only for SCENE fields at 64-256 analysis grid (`properties/influencer.py:23-27`, `generators/scene_fields.py:100-142`); `references.py:62-96` already evaluates an isolated sub-stack.
- Constraints: frozen image at `int(ts.resolution)` like `bake_mask_texture` (`mask_eval.py:197-214`) (7); optional `frozen_image`/`use_frozen` fields ride v2 as `detail_image` did (`smart_materials/format.py:31-33`); keep the destructive BAKE test; mark stale on resolution change.

**R10. Solo/isolation preview over a checker** (M3, medium)
- Node-stack add-on: `get_preview` selects an emission viewer group and remembers `ori_bsdf` only when the current link is not the viewer (`preview_mode.py:7-80`); `remove_preview` relinks (82-95); LAYER mode composites Color over a camera-space 10x checker by Alpha with a second "missing data" grid, Gamma 2.2 for sRGB channels, Mix blend type = the layer channel's blend (154-227); CHANNEL mode cycles outputs when re-selected (229-305). Correction: ALPHA and SPECIFIC_MASK use a plain Emission node (184-189); the checker exists only in LAYER mode; re-pressing the toggle turns preview off (399-426), cycling happens on channel re-selection.
- OP now: nothing; `core/material_manager.py:8-83` roles have no viewer; only the 2D editor shows raw paint images (`ui/native.py:250-259`).
- Constraints: a permanent 3-node role set (Viewer TexImage + Emission + Mix Shader) created once by `repair()` keeps the graph fixed-size (4); node-count tests are deltas (`tests/integration/catalog_materials.py:111-115`); solo raster is a disposable display image like `live_display` (7). Caveats: `evaluator._channel_steps` (96) and `live_inputs.Builder` (67) capture snapshots internally and need an injection hook; coverage must be exposed from `evaluator.py:164-225` for alpha modes; `repair().connect()` never removes links, so the BSDF→Output link needs a one-time managed removal; restore on `save_pre`/`render_pre` via `cache.py:357-361`.

**R11. Object-driven decal projection** (M10, medium)
- Node-stack add-on: `create_decal_empty` at cursor, parented with `matrix_parent_inverse` (`Decal.py:63-83`); TexCoord(object=empty) + Decal Process group, CLIP forced with original extension stored (107-136, restored 186-191); group = `Vector*Scale + (0.5,0.5,0)`, `Alpha = |z| < Distance` hard cutoff (dumped from `lib.blend`); Scale from image aspect (133-136); alpha multiplies every channel/mask (`node_connections.py:1155-1161, 1531-1533`); operators (197-260). Correction: the N/S/E/W `decal_alpha_*` nodes are never linked (dead).
- OP now: projection UV/TRIPLANAR only (`properties/channel.py:83-90`, `mask.py:41-48`); `material_stamp.decal()` (112-119) has no callers; tiling NONE already clips (`channel_eval.py:320-322`); `painting/projection.py:11-34` rasterizes world positions/normals; influencers already track `matrix_world` (`geometry/provenance.py:25-30`, `dependencies.py:80,101`).
- Constraints: append `DECAL` with an Object pointer and distance; `dependencies.uses_geometry` (20-22) must include it or the Empty is never tracked; explicit branches in `channel_eval._sample_fill` (409-414) and `mask_eval` (148-151) since they default to UV; `composer._validate_projection` (22-24) whitelist; `serializer.encode` has no Object case, so follow the SCENE-mask null/refuse precedent (52-58). Add a facing test the node-stack add-on lacks. GPU already falls back for non-UV (`live_inputs.py:105`).

**R12. Height Compare gate** (no milestone, large)
- Node-stack add-on: `height_blend_type COMPARE` (`common.py:113-118`) selects `~yPL Height Compare` whose Normal Alpha is `LESS_THAN(Prev Height, Height)` (hard step; the "Smooth" variants are neighbour-sampling, not softness); `compare_alpha` (`node_connections.py:1127-1133`) multiplies every other channel's alpha via Extra Alpha nodes (`subtree.py:2406-2434`, `node_connections.py:1899-1902`) and feeds transition AO (1814-1819).
- OP now: no cross-channel blend (`constants.py:4-32`); each channel is a separate job (`evaluator.py:53-56`); REFERENCE masks can read one layer's HEIGHT pre-composition but never the accumulated height below (`references.py:1-5`).
- Constraints: default-off optional field (6); softness is an OP addition; invalidation must extend `properties/channel.py:6-19 changed()`/`dependent_channels` so a HEIGHT edit below dirties all channels of gated layers (M1); the gate needs a per-layer HEIGHT sub-compose inside every gated channel job (cacheable), which is why effort is large; GPU must take the explicit fallback (`live_gpu.py:373`).

**R13. Data-image colorspace policy and mismatch diagnostic** (corrected; M1/M2, small)
- The node-stack add-on retags images assigned to masks/linear channels to Non-Color unless `is_dirty` (`Mask.py:210-211, 829-830`, `Layer.py:1466-1467, 1772-1773`), computes `1/GAMMA` for sRGB-tagged mask images (`common.py:6732-6747`), shows an alert with `YUseLinearColorSpace` (`ui.py:1340-1343`, `Layer.py:42-56`). Corrections: three reader citations were wrong; `gamma_space` (`Layer.py:5395-5400`) only gates non-image layers feeding sRGB channels, not image overrides; the policy is datablock-tag driven (`common.py:6609-6621`), not use-driven.
- OP now: decode gated solely on tag == 'sRGB' (`image_snapshots.py:27-30`); owned/bundled images get a policy (`image_manager.py:16`, `surface_presets.py:146`) but `import_texture` and shelf assignment (`ui/library_categories.py:120-135`) keep Blender's default, so an 8-bit roughness PNG is silently linearized (0.5→0.214).
- Constraints: retag implicitly only images OP owns or with `users <= 1` (12; the tag is global per datablock); arbitrary datablocks get a warning + explicit operator; tag is already in the resource key (`resource_keys.py:9-12`); the per-channel flag can be dropped. `tests/integration/generated_detail_inputs.py:113-135` is the home for the before/after test.

### Adapt-workflow

**R14. Explicit "sync tiles from UVs" and single↔tiled conversion** (M11, medium)
- Node-stack add-on: `YRefillUDIMTiles` re-runs `get_tile_numbers` per image and `fill_tile(..., empty_only=True)` with `image.yui.base_color` (`UDIM.py:536-591`, fill at 15-49); `refresh_udim_atlas` removes unreferenced tiles except 1001 (1023-1025); `YConvertImageTiled` is bidirectional (1160-1225). Correction: base colour is recorded by `initial_pack_udim` (340-342); new image masks use white/black per option (`Mask.py:789-817`), the 1187-1194 colours belong to the conversion path only.
- OP now: no tile records; M11 requires sparse allocation starting with 1001/1002 (`Codex_Plan.md:688-704`).
- Constraints: explicit operator only (12); memory preflight before allocation (plan line 363); paint-layer tiles fill transparent (`channel_paint.py:22-27`), fill/output tiles use `constants.DEFAULTS`; undo of tile edits is not established (`material_stamp.py:90`), so keep tile files as the recovery path. Not complete until two-tile acceptance passes (10).

**R15. UDIM persistence (`<UDIM>` path, temp dir, save→pack→cleanup)** (corrected: OP status is missing, not partial; M11)
- Node-stack add-on: `get_temp_udim_dir` = `<blend>/UDIM__` (`UDIM.py:187-192`), `initial_pack_udim` save_as → pack → delete files (303-342), save_pre `save_pack_all` checks empty tiles and lost packed flags (`image_ops.py:167-202`). Corrections: `is_image_filepath_unique` self-matches so the "not unique" branch always fires (`common.py:7998-8004`); `remove_tiles` deletes a user tile file without a temp-dir gate (529-531); "user files never deleted" is overstated.
- OP now: only `scripts/probe_udim.py` and the untracked `docs/udim-tile-access.md`; production has generic pack-on-save (`cache.py:344-347`, `image_manager.py:69-79`), no tiled images, no recovery checks.
- Corrections to the constraint check: Blender 5.2.1 has no `Image.is_using_temp_dir`, so the protection must be an OP-owned path prefix; PNG tile files quantize float paint (measured 0.123456789 → 0.123460747) and cannot hold >1 heights, so tile files must be OPEN_EXR (7); it cannot be dropped into `pack_image` without tile identity in the evaluator.

**Unverified, lower priority, worth a look** (from the readers; not adversarially verified): rest-pose armatures before normal bakes (`bake_common.py:2298-2316`; OP `render_geometry.py` copies keep Armature modifiers pointing at the posed armature); UV-duplicating modifier hazard rule, Mirror exempt only with mirror-UV/offset (`bake_common.py:46-61`); version-stamped ordered load migration keyed on add-on and `bpy.data.version` (`versioning.py:319-330, 1754-1825`; OP already has `Scene.open_painter.format_version` at `properties/project.py:11`, default 1, but nothing reads or bumps it — the gap is the ordered migration keyed on it); runtime resolution of sRGB/Non-Color names (`common.py:822-876`); one-click missing-data repair with explicit placeholders (`Root.py:2380-2513`; OP `core/validation.py` only reports). Caveat: the node-stack add-on's placeholder is a fixed 1024² byte image regardless of the original size (`Root.py:2380-2388`) and masks whose source is gone are deleted without confirmation (`2442-2446`); copying that verbatim would break constraints 7 and 12; compositor denoise post-pass (`bake_common.py:1445-1520`); `bpy.app.translations` localization (`Localization.py`).

## 3. Not to take, and corrected findings

**Do not take**
- Node-graph plumbing: `node_connections.py`/`node_arrangements.py`, `halt_update`/`halt_reconnect` RNA flags (stuck-flag repair button `ui.py:1315-1319` is a symptom), neighbor-UV smooth bump (`subtree.py:390-430`, empirical scales `common.py:4813-4821`), per-neighbor modifier trees, `lib.blend` revision upgrades. OP has no graph to rebuild.
- Tile access by file rename (`UDIM.py:344-412`, wrapped around every per-tile op, `bake_common.py:1410-1437, 5025-5045`): O(all tiles) disk I/O per tile and leaves the datablock inconsistent. Reference only; it confirms the M11 "one plain image per tile" design.
- Image atlas and UDIM atlas (`ImageAtlas.py`, `UDIM.py:623-1057`): solves sampler count in shader materials; OP samples on the CPU.
- Scene-mutating bake bookkeeping (`bake_common.py:313-436, 1060-1267`), plane-bake resize/FXAA (1803-1938, 6080-6289), render-to-disk blur/denoise round trips, silent GPU→CPU device switch (296-311). OP's staging scene is the better design.
- Updater and credits modules: `threading`, `ssl._create_unverified_context`, `requests`, writes into the add-on directory, executes fetched Python (`addon_updater.py:726-751, 942-1070`; `credits_ui.py:608-621`; manifest network permission). Violates 8, 9, 11.
- View/engine-dependent mask sources (BACKFACE, camera-space Hemi, EEVEE GTAO toggling `scene.eevee.use_gtao`, `Layer.py:6153-6181`): non-deterministic and mutate scene settings.
- Preview-time colour-management swap (`preview_mode.py:114-152`): export must never change scene colour settings (`ARCHITECTURE.md:112`).
- Decal shrinkwrap depsgraph handler (`Decal.py:292-334`): per-update `matrix_world` writes from a handler.
- User-defined channel model and rename/reorder cascades (`Root.py:1274-1882`); OP's fixed eight channels are a constraint.
- Paint-slot depsgraph hack and relink-to-recompile hack (`Root.py:4213-4250, 4075-4090`).

**Refuted or corrected by the verifiers** (use the corrected view)
- UDIM persistence: OP status corrected partial → missing; the reader's evidence was an untracked probe. The node-stack add-on's uniqueness check is self-defeating and its file deletion is not fully gated (see R15).
- PBR set import: OP status corrected missing → partial (surface presets, per-channel images, INVERT/NORMAL_INVERT_Y/CHANNEL_MIXER exist); "flip-G" is inert in the node-stack add-on; channel swizzle is developer-mode and non-override only (`node_connections.py:1469-1470`), masks only get full swizzle (`Mask.py:2211-2225`); default component must be current behaviour, not R.
- Colorspace policy: three wrong citations; `gamma_space` mischaracterized; policy is tag-driven (see R13).
- SSAA: memory estimate was 16x too low; `STRAIGHT_OVER` un-premultiplies, it does not average premultiplied.
- Mean curvature: the `4897-4901` citation is the GPU retry, normalization is 4914-4919; `BakeToLayer.py:462-464` is DUST defaults.
- Solo preview: checker only in LAYER mode; toggle turns off rather than cycling.
- Height compare: no softness exists in the node-stack add-on; per-channel link is 1899-1902.
- Adjustment layer: alpha clamp unwired for PREV_LAYERS; bake not guarded.
- Decal: N/S/E/W alpha nodes are dead code.
- Per-channel masks: `live_inputs.py:190-193` also needs the filter.

## 4. Open questions to test in Blender 5.2.1 before committing

1. Bevel node bake (R6): confirm an EMIT bake of Geometry.Normal vs `ShaderNodeBevel` through an owned emission material in the staging scene renders on the CPU device, and measure cost at 2K with samples 4/8/16; check that `use_selected_to_active=False` is honoured when the target has sources listed.
2. AO World (R5): verify that assigning an owned World to `bpy.data.scenes.new` staging scene and setting `light_settings.distance` changes the AO bake result versus the current no-World path (baseline distance 10.0 measured headless); confirm `world.light_settings.distance` still exists on the AO pass in 5.2.
3. SSAA memory (R4): allocate an 8192² float RGBA image in Blender, read via `foreach_get`, downscale in NumPy, write back; record peak RSS and whether Blender's own image buffer plus the NumPy copy exceed the machine budget; decide the byte gate from the measurement.
4. Solo viewer (R10): confirm that inserting a Mix Shader between the managed Principled and Material Output and toggling its factor does not force a shader recompile on every paint update (the repair path is tuned to avoid recompiles, `ARCHITECTURE.md:159-160`); confirm the emission viewer renders the disposable display image at `preview_policy` resolution in Material Preview and Rendered modes.
5. Mean curvature on real assets (R1): compare the vertex-domain result on a beveled/subdivided evaluated mesh against the node-stack add-on's baked output for the same mesh (sign, range after normalization), and confirm a mirrored (negative-scale) object does not flip sign through `snapshots.targets` normals.
6. Armature rest pose (unverified item): confirm whether `render_geometry` copies with an Armature modifier bake in the posed or rest state, and whether setting the original armature `pose_position='REST'` inside the staging context leaks to the user scene on cancel.
7. UDIM tile files (R2/R14/R15): rerun `scripts/probe_udim.py` with OPEN_EXR tile files and a float tiled image, then a Cycles bake with `use_clear=False`, save, reopen; confirm float round-trip exactness and that packed EXR tiles survive reopen as the PNG probe did.
8. Decal dependency (R11): confirm `depsgraph_update_post` reports `is_updated_transform` for a parented Empty when only the parent object moves, so the decal invalidates on either transform.
9. Colorspace retag (R13): confirm that changing `colorspace_settings.name` on a dirty image reloads and discards unsaved pixels on 5.2.1 (the reason for the node-stack add-on's `is_dirty` guard) and whether a packed image is affected the same way.
10. Adjustment layer live path (R8): verify the `_fallbacks` CPU route in `live_preview.py` produces the same frame for an `ADJUST` layer as the CPU evaluator, and that no GPU plan silently treats it as FILL.


## 5. Additions from the completeness review

The critic spot-checked these against source; they were not put through the two-lens verification above.

**T1. Transition Bump / Ramp / AO — mask-edge effects (omitted from the verified set; high value, M7 family).**
Its signature "layer thickness" feature. Data model: `Layer.py:5676-5800` (bump: alpha-sharpening value 3.0,
second edge, height range, chain count, flip, crease factor/power, falloff EMULATED_CURVE/CURVE), `5800-5838` (ramp:
own blend type, intensity, unlink from channel intensity), `5855-5911` (AO: power 4.0, intensity 0.5, colour, inside
intensity). Mechanism: `transition_common.py:20-59` places an `INTENSITY_MULTIPLIER_SHARPEN` (`lib.py:70-73`) on every
*other* channel so colour/roughness see `clamp(alpha*value)` (a hard edge) while the height channel keeps the soft
alpha, which reads as a bevelled rim; only one HEIGHT channel per layer may own the bump
(`transition_common.py:426-446`); falloff variants at 294-392. OP has nothing coverage-derived: the filter CATALOG has
no coverage-band operation, `generators/edge_wear.py` is mesh-map driven, and masks multiply every channel uniformly
(`evaluator.py:184-187`). Adoptable as a per-layer optional "edge band" = f(coverage) feeding a height offset, a
GRADIENT_MAP tint and an AO darkening, default off (constraint 6), optional fields on format v2. It composes with R7
(per-channel mask participation) and R12 (height gate).

**T2. Selection-to-mask and Color-ID authoring by face fill (small, high value; M6).**
`SELECTED_VERTICES` bake (`bake_common.py:4043-4092`, vertex vs face mode from `mesh_select_mode`, EMIT bake at
4634-4642); selection recorded per object in bake info for re-selection (`5293-5312`, `BakeToLayer.py:12-105`).
Color ID uses a dedicated `__yp_color_id` attribute (`common.py:48, 6574-6586`), uniqueness at 1/256 tolerance
(`common.py:244, 6588-6596`), face-fill operators `vcol_editor.py:450-640`, select-by-colour at 376. OP's ID-colour pick
already exists (`generators/base.py:94-97`, `properties/mask.py:40`) but there is no selection mask source. Port as a
NumPy rasterization of selected faces/vertices through `geometry/uv_rasterizer.py` (0/1 per vertex, as
`baking/maps/utility.py:133-137`), storing face indices for re-select. Do NOT copy the temp-attribute route
(`bake_common.py:4086-4092` writes `TEMP_VCOL` to the user mesh and runs `mode_set`; constraint 12).

**T3. Colour attribute as an RGB source at authored resolution (small).** `VCOL` is a layer, mask and override type
(`common.py:302-321, 323-341, 343-355`) with `divide_rgb_by_alpha` (`Layer.py:6144-6150`, auto for VCOL at 844). OP has
only the SCENE `ATTRIBUTE` field, luminance-only on a 64-256 grid (`generators/scene_fields.py:13-47`,
`properties/influencer.py:28`). The rasterizer exists; extend to a channel fill kind at `ts.resolution` with RGB.

**T4. Merge layer down/up and merge masks (medium).** `Bake.py:2777-2830` (direction, reference channel, apply
modifiers, forced MIX blending so nothing is lost), guards at 2966-2976, reconnect-two-then-bake at 3020-3040, undo
warning at 2884/2930-2932; `YMergeMask` at 3202. OP layer actions (`ui/layer_list.py:14-30`) have no merge;
`evaluation/references.py:62-96` already evaluates an isolated sub-stack, so the CPU port is a two-layer sub-compose into
a new PAINT layer.

**T5. Harvest an existing Principled material into a fill layer (medium; extends R3).** `Layer.py:1979-2019`
`search_for_image_node` walks links backwards from each Principled input, maps `Bump.Height` and the output
`Displacement` socket to the bump channel; 2130-2170 chooses READ_NODE vs filename synonyms; `asset_library_path`
imports Asset Browser materials and de-duplicates images (2020-2030, 2180-2200). OP has no `TEX_IMAGE` reader. Retagging
harvested images must respect R13's owned/single-user rule.

**T6. Per-layer/mask UV map and UV transfer (medium).** `Layer.py:6191-6203`, `Mask.py:2270-2281` (`uv_name`);
`YTransferLayerUV` (`Bake.py:557-692`) re-bakes an image from one UV map to another and updates every entity sharing the
image; batch variant 388-556. OP samples only the active-render UV (`geometry/snapshots.py:38`) and retargets only at
export (`export/atlas.py`). NumPy version: rasterize target-UV triangles, interpolate the source UV barycentrically,
`sample_uv` the source image; no Cycles needed.

**T7. Copy/paste layers between materials (small).** WindowManager clipboard (`Layer.py:4271-4310`, paste 4527-4600
with duplicate-packed / blank / rebake options; reused by `YOpenLayersFromMaterial` 2254, 2341-2344). OP has only
in-set DUPLICATE and whole-material save/import.

**T8. Dithered 8-bit export (small).** Toggle and intensity at `Bake.py:1742-1752`, applied only to clamped sRGB colour
channels (2223-2224), forcing a float bake (2189). The node-stack add-on's mechanism is a compositor render round trip
(`bake_common.py:1522-1600`; do not take). OP `export/image_writer.py:13-22` quantizes with `np.rint` and no dither;
an opt-in NumPy TPDF/ordered dither, default off, fits constraint 7.

**T9. Bake to vertex colour and richer channel packing (small).** `YBakeChannelToVcol` (`Bake.py:856-900`);
bake-target channels carry `subchannel_index`, `default_value`, `invert_value`, `normal_type`
(`BakeTarget.py:45-103`). OP `export/channel_pack.py:1-16` packs only component 0, ONE_MINUS_, ZERO/ONE.

**T10. Deferred/large or low:** flow map from a straight UV (`common.py:6781-6840`, niche); multires bakes and the
VDM sculpt round trip (`BakeToLayer.py:491-512`, `vector_displacement.py:1418-1720`, `vector_displacement_lib.py:24-700`;
OP has none — list as deferred); displacement setup with a managed Displacement role (`displacement_common.py:178-290`,
subdivision from a poly budget 46-118; the subsurf part mutates user modifiers, so explicit operator only); optional
gamma-space blending (`Root.py:3969-3974`; OP blends linear, default must stay linear); object/world-space normal
layers (`Layer.py:5438-5444`; needs per-texel TBN, low); HEMI's `hemi_use_prev_normal` (`Layer.py:6175-6180`,
`node_connections.py:967-992`) as a second consumer of the accumulated below-normal that R12 needs.

**T11. UX details worth copying:** eraser toggle pairs brushes and restores the original blend mode and pressure
flags (`vcol_editor.py:170-260`, `common.py:692-697`) where OP resets to MIX (`ui/brush_operators.py:45-46`);
expandable mask/override sub-rows in one UIList with `active_edit` selecting the paint target
(`ListItem.py:30-200, 291-400`); one-click "Add Simple UVs" on assign failure (`Root.py:1884`; OP raises at
`core/texture_set_manager.py:34-35`); RGB Curve and mask Curve modifiers (`modifier_common.py:8-36`,
`MaskModifier.py:7-11`) with no curve-mapping equivalent in OP's filter CATALOG; margin type EXTEND/ADJACENT_FACES
exposed (`bake_common.py:6380-6386`) where OP exposes margin width only (`properties/bake.py:89`).

**Already present in OP and correctly not re-recommended:** ID-colour mask (`generators/base.py:94-97`), MODIFIER mask
(= OP FILTER source), OBJECT_INDEX (= OP OBJECT_ID map), "Use Baked" display (= OP authoritative outputs), save-time
packing (`evaluation/cache.py:344-347`), paint-undo snapshots (`painting/material_stamp.py:90-98`), material-view
switch on entry (`ui/native.py:261`). PSD layer export (`image_ops.py:735-818`) is gated to an absent `psd_io` module
(a paid variant of the add-on) and is not an available node-stack add-on capability.

## 6. Suggested order

1. **R1 mean curvature** and **T2 selection mask**: pure NumPy on existing rasterizer paths, immediate benefit to the
   18 smart masks, no format risk.
2. **R13 colorspace policy**, **R2 tile enumeration diagnostic**, **R5 AO world/distance**, **R4 SSAA opt-in**: small,
   each with a clear test.
3. **R7 per-channel masks**, **T1 transition edge band**, **R8 adjustment layer**, **R9 non-destructive freeze**: format
   v2 additions with all-on/off defaults; these change how the stack composes and need the invalidation work first.
4. **R10 solo viewer**, **R11 decal projection**, **R6 bevel bakes**, **T4/T5/T6/T7**: workflow features.
5. **R12 height gate**, **R14/R15 UDIM workflow**: only after the evaluator carries the needed identities.

## 7. Fit with 0.2.7 shader-native authoring (added after Codex's change)

Version 0.2.7 compiles every layer, channel, mask and filter of a `LIVE` set into one
persistent `ShaderNodeTree` of Python-built math nodes bound to Value-node uniforms
(`evaluation/shader_graph.py`, `docs/shader-preview-architecture.md`). That is the node-stack add-on's
architecture for the preview, so the "node plumbing does not transfer" verdict in
section 1 no longer covers the preview path. What the node-stack add-on learned about running such
graphs now applies directly. Measurements below are from this checkout on Blender
5.2.1 (`scripts/benchmark_node_graph_scaling.py`, results in
[node-graph-scaling-0.2.7.json](../performance/node-graph-scaling-0.2.7.json), plus
probes on the catalog materials).

### What the compiled graph costs today

| Material | Layers | Nodes | Math / VectorMath / Value / TexImage | Unique images | First build |
| --- | ---: | ---: | --- | ---: | ---: |
| Worn Painted Metal | 6 | 1,474 | 985 / 246 / 156 / 29 | 10 | 4.5–5.5 s |
| Post-Apocalyptic Scrap Metal | 9 | 2,834 | 1,980 / 403 / 276 / 59 | 14 | 17–20 s |

Profiling the heavy build: `shader_math.node` 8.3 s (2,834 calls) and `shader_math.link`
5.4 s (4,898 calls) of 17.2 s. Blender's `nodes.new` and `links.new` re-validate the
whole tree on every call, so a flat tree grows quadratically even with unique names
(raw `nodes.new`: 500 → 0.24 s, 1,000 → 1.77 s, 2,000 → 13.8 s; with immediate renaming
2.5 s). Hiding one layer changes `structural_signature` and triggers a **full rebuild**
(4.05 s on the six-layer material; the heavy one would take the full build again),
while a numeric edit costs 0.26 ms. `gpu.capabilities.max_textures_frag_get()` is
**64** on this Metal GPU; the heavy material already binds 14 unique images and EEVEE
reserves samplers of its own.

### Same node count, three structures (3,000 chained Math nodes)

| Structure | Node-stack add-on equivalent | Build time |
| --- | --- | ---: |
| One flat tree | none (OP today) | 46.1 s |
| 10 separate group trees, each instanced once | per-layer `group_node` trees (`common.py:2349-2360`, `Layer.py:6057`) | 2.5 s |
| One library group built once, instanced 10× | `lib.blend` groups via `get_node_tree_lib` (`common.py:2163-2187`) | 0.17 s |

Blender inlines groups when it generates GLSL, so these layouts cost the GPU the same;
only the Python/RNA construction changes.

### Recommendations for the shader compiler (Codex's files; not implemented here)

1. **Per-layer group trees** (`node_connections.py:930 reconnect_layer_nodes`,
   `node_arrangements.py:600`): compile each layer (or folder) into its own group tree
   with channel sockets and instance it from a small top tree. Construction becomes
   Σ(size²) instead of (Σ size)², ~18× faster at 3,000 nodes with ten groups, and a
   `RebuildRequired` for one layer rebuilds one group instead of everything.
2. **Library groups for repeated arithmetic**: Levels/black-white-invert, mask mix,
   each blend mode, UV/triplanar coordinates, image coverage. Build them once per
   session (procedurally, no shipped `.blend` needed) and instance them; the top-level
   node count drops by an order of magnitude and construction by two. Unlinked group
   input sockets are uniforms in Blender's GPU codegen, so a group input can replace a
   Value node where binding identity is not needed.
3. **Visibility and enable flags as uniforms, not structure**: the node-stack add-on parks disabled
   layers in a trash group and mutes links (`common.py:2356-2358`) rather than deleting
   nodes. OP can multiply the layer amount by a 0/1 uniform so toggling a layer costs
   0.26 ms instead of a rebuild; only order/parent/type changes need topology work.
4. **Sampler budget diagnostic**: report unique images and sampler states against
   `max_textures_frag_get()` in Developer Mode. If boundary resources per legacy source
   push a set toward the limit, the node-stack add-on's image atlas (`ImageAtlas.py`; on by default
   for baked layers, `BakeToLayer.py:379,636`) applies to those cached rasters, which
   OP owns and can pack into one texture.
5. **User-facing "Use Baked" freeze** (`Root.py:3907-3912`): OP's explicit CPU outputs
   already play this role for a whole set; a per-layer/folder freeze into an image
   boundary (R9) is the shader-simplification tool node-stack add-on users reach for on heavy
   stacks, and it removes that layer's nodes from the compiled graph.
6. **Solo viewer** (R10) and **decal projection** (R11) are now cheap in the preview:
   an emission viewer output and an Object-space texture coordinate node are exactly
   what the node-stack add-on uses (`preview_mode.py:7-95`, `Decal.py:107-136`); the CPU reference
   implementations are still required for parity.

### What was integrated in this pass (outside the compiler)

See [node-stack-integration.md](../node-stack-integration.md): the signed mean-curvature
map (R1), the selection map and reselect action (T2), UDIM tile enumeration in the bake
diagnostics (R2), the explicit AO distance through an owned staging World (R5) and
opt-in 2× supersampled Cycles bakes with a byte preflight (R4).
