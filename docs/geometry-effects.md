# Geometry effects: real prototypes, variation and materials

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
