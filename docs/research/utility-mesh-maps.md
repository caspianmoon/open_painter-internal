# Baked target-mesh utility maps

Open Painter evaluates the assigned mesh through Blender's dependency graph and
rasterizes its render UV map into managed, packed Non-Color images. The temporary
evaluated meshes are released after baking. No shader-effect nodes are created.
Blender documents [corner normals](https://docs.blender.org/api/5.2/bpy.types.Mesh.html)
as incorporating smooth/sharp and custom normals; these are the source of the
object and world normal maps. Thickness uses the actual intersections returned by
[BVHTree.ray_cast](https://docs.blender.org/api/main/mathutils.bvhtree.html).

`baking.maps.utility.bake_utility(texture_set, kind)` returns a packed image.
`baking.bake_manager.register_map(texture_set, kind, image)` attaches it to the set.
All new bake toggles start disabled. They do not require a high-poly source or cage.

| Map kind | RGB encoding | Meaning |
| --- | --- | --- |
| `POSITION` | `(world_position - world_min) / (world_max - world_min)` | Shared world bounds include only vertices used by assigned evaluated faces; effectively zero-width axes encode 0.5. |
| `WORLD_NORMAL` | `unit_world_normal * 0.5 + 0.5` | XYZ transformed with the inverse-transpose world matrix and normalized after interpolation. World Z is the blue component. |
| `OBJECT_NORMAL` | `unit_object_normal * 0.5 + 0.5` | Local evaluated XYZ; not a tangent-space normal texture. |
| `MATERIAL_ID` | Deterministic flat RGB | Original materials preserved in the explicit slot assignments, before Open Painter replaced the preview material. |
| `OBJECT_ID` | Deterministic flat RGB | Source object identity by library path and object name. |
| `UV_ISLAND` | Deterministic flat RGB | Connected mesh faces with matching shared UV endpoints; tessellation edges do not split islands. |
| `UV_BORDER` | White at an island border, falling to black over Border Width | Computed in texture pixels from island boundaries; triangulation does not create borders. |
| `THICKNESS` | `inward_distance / thickness_distance_scale` | Actual intersections with the opposing exit-facing surface of the same evaluated target object. |

Alpha is zero outside assigned UV coverage and one inside. Thickness additionally
has alpha zero where an opposing exit-facing surface was not found. Consumers must
respect validity alpha, particularly before inverting masks. IDs are data colors:
do not apply color management to compare them. The `id_palette` image property
contains a JSON mapping of source identity to RGB. Object/material names changing
changes those IDs; UV island identity is topology dependent.

Every image stores `open_painter_mesh_map`, `open_painter_utility_method`,
`world_min`, `world_max`, and `alpha_encoding`. The method tag is
`EVALUATED_TARGET_UV_V1`. The bounds are three-element world XYZ lists. Thickness
also stores `thickness_sampling_resolution`, `thickness_samples`,
`thickness_ray_count`, `thickness_valid_hits`, and `thickness_distance_scale`.
Multiplying thickness RGB by the distance scale recovers Blender world-unit distance.

Thickness samples a user-selected 64, 128, or 256 square grid with 1, 4, or 8 rays
per covered texel. The strict maximum is grid size squared times rays per texel,
including overlapping UVs. One ray follows the inward interpolated normal; extra
rays sample a narrow cone and their hit distances are projected onto that normal
before averaging. Maximum Distance is both the search limit and white-value
normalization; zero uses the largest complete target-object world-bounds diagonal,
including unassigned faces that can form the opposing surface. The sampled values
are stored at the texture-set resolution using nearest resampling and exact UV
coverage clipping. This preserves measurements rather than interpolating values
between unrelated UV islands, but small features require increasing the sample grid.

These maps describe the evaluated target's viewport geometry, including modifiers.
They do not transfer high-poly details, raycast between separate objects, infer
transmission, or replace thickness with AO. Open/nonmanifold or self-intersecting
geometry can produce invalid or ambiguous thickness. Only the render UV map's 0–1
tile is baked. Overlapping UVs have deterministic last-surface-wins behavior, so
separate surfaces requiring different data should use separate UV coverage.

`tests/integration/utility_maps.py` verifies rotated/nonuniformly scaled normals,
assigned-slot bounds, different editing/render UVs, distinct original material IDs,
UV borders before/after triangulation, an evaluated Solidify modifier, exact cube
thickness, invalid open-sheet thickness, distance limits, packed save/reopen, and
the absence of new material nodes.
