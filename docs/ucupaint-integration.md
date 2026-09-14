# Ucupaint-derived features integrated on 2026-09-14

Source study: [research/ucupaint-study.md](research/ucupaint-study.md). Ucupaint is
GPL-3.0-or-later like Open Painter; ported code keeps its attribution in the module
docstring. Everything below is implemented outside the shader compiler and the
smart-material files that were being edited concurrently; the compiler findings are
recommendations in section 7 of the study, not changes.

## Signed mean-curvature map (`MEAN_CURVATURE`)

`baking/maps/mean_curvature.py` ports Ucupaint's `fill_curvature_vcol`
(`bake_common.py:170-262`): weld coincident vertices, pair the faces at every welded
edge, accumulate the signed dihedral angle weighted by edge length, normalize by the
vertex area share, saturate at Curvature Distance, relax once, then rasterize through
the existing UV rasterizer. Differences from the port source: it runs on the evaluated
world-space snapshot (`geometry/snapshots.targets`) so non-uniform and mirrored scale
behave like the other utility maps, writes no attribute to the user mesh, and
replaces Ucupaint's post-bake min/max normalization with one symmetric stretch per
texture set (flat stays exactly 0.5; the strongest feature saturates). The effective
radius is stored on the image (`mean_curvature_effective_distance`) and the distance
is part of the map's provenance.

The existing `CURVATURE` crease map and every recipe that consumes it are untouched.
The new map is a `BAKED` mask source (`Map: Mean Curvature`); use Black/White levels or
Invert to select convex or concave regions. Limits: vertex-domain, so flat faces
between bevel rings read slightly above 0.5; it describes the target's own evaluated
geometry, not high-poly detail.

Test (`tests/integration/utility_maps.py`): bevelled cube reads convex, no concave
values, stretch records the effective radius; mirrored non-uniform scale keeps the
sign; an inset pocket reads below 0.2 on its floor edges and above 0.9 on lips; the
map bakes through the queue, evaluates as a BAKED mask on the CPU, and the shader
graph samples it through the generic `mesh_map` path.

## Selection map (`SELECTION`) and Reselect Baked Faces

`baking/maps/selection.py` rasterizes the current face selection of the assigned
meshes (1 selected, 0 otherwise, alpha = assigned-UV coverage), following Ucupaint's
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
`light_settings.distance = AO Distance` (`bake_common.py:4394-4403` in Ucupaint).
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
