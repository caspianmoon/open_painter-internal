# Node-stack add-on features integrated on 2026-09-14

Source study: [research/node-stack-study.md](research/node-stack-study.md). The node-stack add-on is
GPL-3.0-or-later like Open Painter; ported code keeps its attribution in the module
docstring. Everything below is implemented outside the shader compiler and the
smart-material files that were being edited concurrently; the compiler findings are
recommendations in section 7 of the study, not changes.

## Curvature V2: smooth edges for every recipe (`CURVATURE`) and a signed map (`MEAN_CURVATURE`)

**Problem observed on the supplied project** (`open painter test.blend`, 39,637-face smooth
shell): the crease-only `CURVATURE` map had 0.018 % of its texels above 0.01, so the
CHIPS/EDGE_WEAR masks of every material were empty and the only visible wear was the
ungated scratch image — "the flat material applied everywhere".

`baking/maps/mean_curvature.py` keeps the node-stack add-on's welded edge pairing and signed dihedral
weighting (`bake_common.py:170-262`) but estimates curvature per **texel** as a kernel
ratio: turning (¼ · angle · length per adjacent face) within Curvature Radius of the
texel's world position, divided by surface area within it — the integral of mean
curvature over the kernel. It is exact for cylinders, identical on every face of a
sphere, insensitive to tessellation density and sliver triangles, and gives edge bands
of physical width. The surface's median turning is subtracted (a sphere or the body of a
rounded object is the flat baseline), and turning below a 0.1 rad dead zone counts as
flat. Faces are sampled at half the radius; candidates are gathered per radius-sized
block, so cost scales with covered area, not face count.

- `CURVATURE` (`CYCLES_BEVEL`) = the transient Bevel-normal/cross-product/length/
  smooth-step shader baked in Cycles, one reach per Bevel radius multiple (thin,
  normal, wide, extreme). Every recipe that reads Curvature gets edge wear from
  the same shader the viewport could evaluate. Maps baked with older methods
  report stale and rebake into the set.
- `MEAN_CURVATURE` = signed estimate (0.5 flat, convex above, concave below), stretched so
  the strongest feature saturates, with convex/concave crease lines drawn on top so sharp
  lips on coarse meshes stay crisp. A `BAKED` mask source; use levels/invert to pick
  convex or concave.
- Curvature Radius (default 0.02 m) is the wear width; it is part of both maps'
  provenance.

Earlier attempts and why they were rejected: a per-vertex port bled across large faces
and read a sphere body as convex; a fixed-radius edge kernel rippled when the radius was
smaller than the edge spacing; a per-face estimate exploded on sliver triangles.

Measured on the project at 2048: rebake 31.7 s (crease-only was ~5 s); texels above 0.01
went from 0.018 % to 12.65 %; the Blue paint coat CHIPS mask went from no coverage to
1.6 % concentrated on panel rims. Renders with the unchanged recipe:
[before](validation/curvature-v2-before.png), [after](validation/curvature-v2-after.png),
[the map](validation/curvature-v2-map.png). Tests: `geometric_curvature.py` (12 cube
creases unchanged, dense sphere and flat triangulation stay black, a bevel under the
crease angle registers), `utility_maps.py` (bevelled cube convex only, mirrored
non-uniform scale, inset pocket concave floor and convex lips, queue bake, CPU mask and
shader-graph sampling).

Still recipe-level, not fixed here: the scratch image masks are not gated by exposure,
and cavity dirt reads only AO (the concave part of the new map is a better recess
signal). Those live in the smart-material recipes.

## Selection map (`SELECTION`) and Reselect Baked Faces

`baking/maps/selection.py` rasterizes the current face selection of the assigned
meshes (1 selected, 0 otherwise, alpha = assigned-UV coverage), following the node-stack add-on's
`SELECTED_VERTICES` bake (`bake_common.py:4043-4092`) without its temporary vertex
color. Face flags are authoritative; a modifier that changes the face count is
refused with an explanation instead of guessed. Selected face indices are recorded on
the image (`selected_faces`); **Reselect Baked Faces** in the bake panel restores them
through the same base-mesh mapping as Select Problem Faces. The map is a snapshot of
an explicit action and carries no geometry provenance.

## UDIM tile enumeration in bake diagnostics

`geometry/udim.py` adapts `get_tile_numbers` / `is_uvmap_udim` (`UDIM.py:88-185`) with
two fixes: tiles are decided by triangle centroids (a face exactly on integer
boundaries still counts) and the column is bounded to 0–9 (no aliasing of u=10 into the
next row). Inspect Bake Setup now names the tiles a layout covers ("Faces also cover
UDIM tile(s) 1002; only 1001 is painted, baked and exported") and flags faces outside
the 1001–1100 range. No tiled image is created; see
[udim-tile-access.md](udim-tile-access.md) for the gate.

## AO Distance through an owned staging World

The staging scene had no World, so Cycles used its implicit 10 m occlusion reach. The
bake now creates an owned World (removed in `close()`) with
`light_settings.distance = AO Distance` (`bake_common.py:4394-4403` in the node-stack add-on).
Default 10.0 reproduces earlier bakes exactly (asserted with `atol=1e-5`); a shorter
reach removes occlusion from a roof 0.15 above the floor (mean AO 0.025 → 1.0). The
distance enters AO provenance only when it differs from the default, so maps baked
before the setting existed stay Ready.

## 2× supersampled Cycles bakes (opt-in)

`Anti-Aliasing: 2x Supersample` bakes Normal/AO at twice the authored size with the
margin scaled to match (`bake_common.py:2597-2611`), then box-filters to the authored
size in NumPy; normals are averaged as unit vectors and renormalized, alpha keeps mean
coverage. `supersample_preflight` refuses jobs whose working image plus NumPy copy
exceed 2 GiB (8K × 2 is refused; 4K × 2 = 1 GiB is allowed), instead of a silent
allocation. Off is the default and byte-identical to before; the factor is recorded in
provenance and on the image.

## Commands

```sh
BLENDER_USER_RESOURCES=.blender-test/user /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python tests/integration/utility_maps.py
BLENDER_USER_RESOURCES=.blender-test/user /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python tests/integration/bake_isolation.py
BLENDER_USER_RESOURCES=.blender-test/user /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python tests/integration/bake_diagnostics.py
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 scripts/run_pure_tests.py
BLENDER_USER_RESOURCES=.blender-test/user /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python scripts/benchmark_node_graph_scaling.py
```
