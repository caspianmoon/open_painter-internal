# Geometry effects: real prototypes, variation and materials

> **0.10.1 — pieces where the material shows them.** Every effect now takes a *Where*: **What the
> material shows** (the default; the aging outcome the effect materialises, or the mesh features
> that stand for it when the set is not aged), **Active layer mask**, **Painted spots** (one piece
> per blob, sized by the blob) or **Whole surface**. Pieces take the colour of the surface under
> them (*Match Surface*), can be the artist's own meshes (*Own Pieces*, a collection) and
> **follow the material**: change the age or the environment and the flakes, chips and moss
> regenerate once the edit settles. See [Placement from the material](#placement-from-the-material-0101).

The M8 geometry effects had placeholder prototypes: a box for a rust flake, a
six-sided cone for moss, an octahedron for snow, a pentagon for a splinter, two
quads for a peel fragment, all instanced at one size and one orientation and
wearing the painted surface's own material; the weld was a constant-radius tube.
This pass replaces the content while keeping the architecture: owned helper
objects parented to the source, bounded instance and triangle counts, freeze,
regenerate and remove, masks published from the layer stack.

Before/after renders (Cycles, same scene and camera) are in
[validation/geometry-effects](validation/geometry-effects/) — e.g.
[rust before](validation/geometry-effects/rust_flakes-before.png) /
[after](validation/geometry-effects/rust_flakes-after.png),
[moss after](validation/geometry-effects/moss-after.png),
[weld after](validation/geometry-effects/weld-after.png). The script that
produced them is `scripts/render_geometry_effects.py`.

## What each effect instances now

| Effect | Prototype (variants) | Material (owned, standard nodes; EEVEE and Cycles) |
| --- | --- | --- |
| Rust Flakes | 6 procedural plates: 18–28 point jagged outline with crumbled notches, 1.5–4.5 % thickness, curl and a lifted side, a `rim` attribute | Dark oxide ramp with per-instance noise offset, lighter dry dust at the rim, fine bump |
| Moss | 8 clusters of 10–14 CC0 Poly Haven `moss_01` scanned clump cards (UVs kept) | The scan's diffuse/alpha/normal/roughness textures, translucent mix, per-clump brightness variation, dithered alpha |
| Snow Clumps | 4 displaced icospheres (multi-frequency bumps), squashed to 24–36 % height, sunk 5 % | Matte white with strong subsurface and granular bump |
| Splinters | 5 shards: squarer fibrous profile, straight sides, one splintered break, slight bow | Stretched grain ramp along the shard, bump |
| Peel Fragments | 4 cracked chips attached along one edge, rolling up (1.6–3.6 rad) with a twist | Two-sided: painted face (sampled from the set's base colour output when available) over a primer underside |
| Weld Curve | Flattened bead profile sunk 30 %, resampled by length, crescent "stack-of-dimes" ripples displaced along the normal (phase shifted by lateral position, pinched, noise-broken) | Metallic steel with noise roughness and heat tint concentrated at the toes |

Placement per instance: alignment to the surface normal, random yaw (splinters
follow the object's local X like wood grain, ±14°), a small random lean, a
skewed size distribution (many small, few large), anisotropic footprints for
flat pieces, a variant index, and an `op_random` value that materials read
through an Instancer attribute so textures do not repeat per variant. Points
are thinned to a minimum spacing per kind (blue-noise style) and snow and moss
are weighted towards upward-facing surfaces; the published mask still rules.
Limits now apply to the instances that survive the mask and spacing, not to the
candidate points the mask rejected.

## Moss asset provenance

`assets/geometry/moss_01/` holds the twelve LOD0 cards of Poly Haven's
`moss_01` (Rob Tuytel, CC0 1.0) as `clumps.json` plus its 1k diffuse and alpha
textures unchanged and the normal and roughness maps converted from EXR to
8-bit PNG (maximum absolute difference 0.002). URLs, MD5 of the source blend and
conversion notes are in `provenance.json`. Textures are packed into the project
on first use and removed with the last helper that uses them.

## Defaults and limits

The dialog starts each kind at a sensible density and size (rust 1500/m² at 2 cm,
moss 3000/m² at 8 cm clusters, snow 800/m² at 7 cm, splinters 800/m² at 4 cm,
peel 600/m² at 3 cm, weld radius 1.5 cm). Card-based moss needs dense packing:
use it on masked patches rather than whole objects; the 500 000-triangle project
limit still applies. These are instanced textures and shapes, not simulation:
snow does not accumulate by physics and rust flakes are not derived from the
paint layers beneath them.

Tests: `tests/integration/geometry_effects.py` (determinism, six flake variants
with owned material and cleanup, per-point attributes and spacing, moss cluster
cards with packed textures released on removal, snow on upward faces only,
rippled sunk weld with owned material).

## Material → Geometry (0.7.0)

Why should height always be fake? The *Material → Geometry* row turns painted and
generated detail into real geometry — helpers owned like every other effect
(regenerate, freeze, remove, export) that you can keep or bake back.

- **Displace from Height** (or the active layer's mask): an owned copy of the
  evaluated source is subdivided (levels 0–4, simple or smooth), samples the
  published Height output through the set's UV map and offsets each vertex along
  its normal by `(h − midlevel) × amplitude` (default: the set's Height Distance).
  The source mesh is never touched, so live scene fields and raster caches keyed on
  it stay valid; **Regenerate** re-copies a moved or edited source, **Apply** freezes
  the helper into real mesh data, **Replace Source Mesh** hands that mesh to the
  source (explicitly destructive).
- **Extract Curves**: the active layer's mask is thinned to its centre lines
  (Zhang–Suen), traced into paths and loops, simplified and projected through the
  set's world-position raster into an editable CURVE object parented to the source,
  split where a path leaves the surface or jumps between UV islands.
  **Stitches on Curve** instances thread prototypes along it (spacing, size), aligned
  to the tangent and the nearest surface normal; **Seam Bead on Curve** is a low,
  smooth weld profile; **Weld Curve** remains the rippled bead.
- **Bolts, Scales, Rocks, Bark**: new scatter prototypes with owned materials
  (hex/round bolt heads on washers, downward-overlapping scale plates, angular
  flat-based rocks, ridged bark plates).
- **Bake Back to Normal / AO**: every generated helper of the set is staged as a
  high-poly source paired to its own object (instances realised for Cycles), the
  existing NORMAL/AO bake runs with a ray distance covering the relief, the maps
  are tagged with the helpers they came from and read *Stale* when a helper is
  regenerated; then keep, hide or remove the geometry. The set's bake settings are
  restored afterwards.

Files: `geometry_effects/displace.py`, `curves.py`, `stitch.py`,
`prototypes_detail.py`, `baking/bake_back.py`, `core/published_outputs.publish_channel`.
Tests: `tests/integration/geometry_displace.py`, `curve_geometry.py`,
`bake_back.py`, `tests/unit/test_curve_extraction.py`.

## Placement from the material (0.10.1)

The effects used to scatter inside whatever mask the active layer had. That made them random
generators: a rust flake had nothing to do with the rust under it, moss grew wherever a mask
said, bolts landed anywhere. `geometry_effects/sources.py` now builds a **placement** — a
density raster over the set's UV space, an optional facing direction, or one point per painted
spot — from what the material shows, and the manager scatters from it.

| Effect | *What the material shows* on an aged set (`OUTCOMES`) | …on a set without aging |
| --- | --- | --- |
| Rust Flakes | corrosion + ½ surface rust coverage × opacity, weighted to upward faces | curvature edges and AO cavities |
| Peel Fragments | the band just outside the intact paint (the paint layer's coverage, smoothed and thresholded), each chip's hinge turned toward the paint that holds; its painted face takes the paint colour beside it | curvature edges |
| Moss | biological growth, more in cavities, rarely on the underside | cavities, shade (faces that do not look up) and the ground (low position) |
| Splinters | wood checking and dents, more along edges | curvature edges |
| Snow | upward faces (`up^2.5`), deeper in hollows | the same |
| Bolts | *Painted spots* by default: one bolt per blob of the active layer's mask, its head sized by the blob's radius (Size is a multiplier); regenerate follows the mask | — |
| Scales, Bark | *Whole surface* by default | — |
| Rocks | the active layer's mask | — |

Fields come from `mask_field` (the layer's mask stack evaluated at ≤1024² through the CPU
reference path, no publishing), `features` (world normals and positions rasterised from the
assigned faces — `up`, `down`, `low` need no bake — plus `cavity` and `edges` from the AO and
Curvature maps when they exist), summed-area-table blurs, `boundary_band`, `direction_toward`
(the gradient of the smoothed coverage) and `label_runs` (run-length union-find connected
components for spots). A material placement is normalised to its 98th percentile so a faint
outcome still reaches the density the artist chose in its strongest places; the status line says
how many pieces went where ("312 pieces on corrosion and surface rust"), and an empty outcome is
refused with "Nothing to place" rather than scattered. For material placements the kind's own
upward-facing bias is off — the field already knows which faces it wants.

**Colour.** Every point carries `op_color`: the set's Base Color under it (composited at 512²,
`sources.channel_raster`), alpha = the helper's *Match Surface*. The owned materials mix their
base colour toward it through an Instancer attribute (`materials.match_surface`); the peel
material tints only its painted face, which is the aging paint layer's colour as it is now (the
paint that left), while its underside stays primer.
Defaults per kind: rust 0.2, peel 1.0, splinters 0.8, scales 0.5, bark 0.6, rocks 0.35; moss,
snow and bolts 0.

**Own pieces.** *Own Pieces* names a collection; its mesh objects are instanced instead of the
built-in shapes (picked per point like variants), each scaled so its largest dimension is one
unit — *Size* means the same for a bolt from the kit as for a procedural one. The objects stay
the artist's (not owned, not removed with the helper).

**Follow the material.** A helper remembers its source (`source`, `source_layer`, `prototypes`,
`match`, `follow`). *Regenerate* evaluates the placement again on the material as it is now
(a repainted spot mask moves the bolts); *Regenerate All* does every helper of the set. When
the Material Aging engine re-drives its folder it calls `manager.schedule_follow(ts)`: helpers
with *Follow the Material* regenerate 0.6 s after the last edit, so scrubbing the age grows
the flakes and the peel without regenerating on every slider step. A helper that does not
follow (or is frozen) keeps its pieces. A helper made from a published mask image before
0.10.1 still regenerates from that snapshot.

**Aging document changes that make this work.** The coating stack shares one delamination
pattern: the paint opens a grunge (fixed seed) with its coating loss, cracking and corrosion,
the primer opens the same pattern more strictly, and the corrosion layer rusts exactly where
the film above is gone (chips at the edges plus the delaminated flats). Cracks are hairline and
only where film remains; salt, soil, growth, patina and dust sit on a soft grunge the state
opens (levels matched to each procedural's range) with the cavities, the ground and upward faces
added when the maps exist — a plank without cavities still gathers them.

Render for review: `scripts/render_material_geometry.py` (a baked, aged steel plate with a boss
and a slot: rust flakes, peel, bolts on painted spots, snow; a tropical oak plank: moss,
splinters); four of its images are in [validation/material-geometry](validation/material-geometry/).
Tests: `tests/integration/geometry_sources.py`, `tests/integration/geometry_effects_ui.py` (GUI).
