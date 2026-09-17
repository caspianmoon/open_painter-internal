"""Utility maps use evaluated assigned geometry, render UVs and actual ray intersections."""

import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.baking.bake_manager import bake_selected, register_map
from open_painter.baking.maps.utility import bake_utility
from open_painter.core import image_manager as images, texture_set_manager as sets


def pixels(ts, kind):
    image = bake_utility(ts, kind)
    assert image.packed_file and image.colorspace_settings.name == "Non-Color"
    assert image["open_painter_mesh_map"] == kind
    return image, images.pixels(image)


open_painter.register()
mesh = bpy.data.meshes.new("Assigned utility surfaces")
mesh.from_pydata(
    [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0), (2, 1, 0),
     (100, 100, 0), (101, 100, 0), (101, 101, 0), (100, 101, 0)], [],
    [(0, 1, 2, 3), (1, 4, 5, 2), (6, 7, 8, 9)],
)
obj = bpy.data.objects.new("Utility panel", mesh)
bpy.context.collection.objects.link(obj)
for name in ("Original copper", "Original steel", "Unassigned material"):
    mesh.materials.append(bpy.data.materials.new(name))
for index, face in enumerate(mesh.polygons):
    face.material_index = index
render_uv = mesh.uv_layers.new(name="Render islands")
for face, x0, x1 in zip(mesh.polygons, (0.1, 0.55, 0.1), (0.45, 0.9, 0.9)):
    for loop, uv in zip(face.loop_indices, ((x0, .1), (x1, .1), (x1, .9), (x0, .9))):
        render_uv.data[loop].uv = uv
# The active editing UV map is deliberately wrong. The render map must win.
mesh.uv_layers.new(name="Wrong editing map")
for loop in mesh.uv_layers[1].data:
    loop.uv = (0, 0)
mesh.uv_layers.active_index = 1
mesh.uv_layers[0].active_render = True
obj.rotation_euler.x = math.pi / 2
obj.location = (3, 4, 5)
obj.scale = (2, 3, 4)
bpy.context.view_layer.update()
ts = sets.create(bpy.context.scene, "Utility data", 512)
sets.assign(bpy.context.scene, ts, obj, 0)
sets.assign(bpy.context.scene, ts, obj, 1)
node_count = len(ts.material.node_tree.nodes)

position_image, position = pixels(ts, "POSITION")
assert np.allclose(position_image["world_min"], (3, 4, 5), atol=1e-5)
assert np.allclose(position_image["world_max"], (7, 4, 8), atol=1e-5)
assert position[256, 140, 3] == 1 and position[256, 370, 3] == 1
assert position[0, 0, 3] == 0 and position[256, 256, 3] == 0
assert position[256, 140, 0] < position[256, 370, 0]
# Degenerate axes have a neutral .5 value, not divide-by-zero or unstable bounds.
assert np.allclose(position[..., 1][position[..., 3] > 0], .5, atol=1e-5)
_, world = pixels(ts, "WORLD_NORMAL")
_, local = pixels(ts, "OBJECT_NORMAL")
assert np.allclose(world[256, 140, :3], (.5, 0, .5), atol=1e-5)
assert np.allclose(local[256, 140, :3], (.5, .5, 1), atol=1e-5)
id_image, material_ids = pixels(ts, "MATERIAL_ID")
assert not np.allclose(material_ids[256, 140, :3], material_ids[256, 370, :3])
assert len(json.loads(id_image["id_palette"])) == 2
_, object_ids = pixels(ts, "OBJECT_ID")
assert np.array_equal(object_ids[256, 140, :3], object_ids[256, 370, :3])
_, island_ids = pixels(ts, "UV_ISLAND")
assert not np.allclose(island_ids[256, 140, :3], island_ids[256, 370, :3])
_, border = pixels(ts, "UV_BORDER")
assert border[256, 52, 0] > .5 and border[256, 140, 0] == 0
assert np.count_nonzero(border[..., 0]) < np.count_nonzero(border[..., 3]) * .15

# Evaluated modifiers affect the map while the original mesh remains untouched.
modifier = obj.modifiers.new("Utility solidify", "SOLIDIFY")
modifier.thickness, modifier.offset = .5, 1
bpy.context.view_layer.update()
modified, _ = pixels(ts, "POSITION")
assert abs(modified["world_min"][1] - 2) < 1e-5
obj.modifiers.remove(modifier)
bpy.context.view_layer.update()

# Triangulating a UV island cannot introduce internal UV-border lines.
bm = bmesh.new()
bm.from_mesh(mesh)
bmesh.ops.triangulate(bm, faces=list(bm.faces))
bm.to_mesh(mesh)
bm.free()
bpy.context.view_layer.update()
_, triangulated_border = pixels(ts, "UV_BORDER")
assert np.allclose(triangulated_border, border, atol=1e-4)


def quads(name, islands, resolution):
    """Disconnected quads, each its own UV island, assigned to a fresh set."""
    data = bpy.data.meshes.new(name)
    data.from_pydata([(x, y, 0) for index in range(len(islands)) for x, y in ((index, 0), (index + 1, 0), (index + 1, 1), (index, 1))],
                     [], [(4 * index, 4 * index + 1, 4 * index + 2, 4 * index + 3) for index in range(len(islands))])
    layer = data.uv_layers.new(name="Render")
    for face, corners in zip(data.polygons, islands):
        for loop, uv in zip(face.loop_indices, corners):
            layer.data[loop].uv = uv
    holder = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(holder)
    bpy.context.view_layer.update()
    holder_ts = sets.create(bpy.context.scene, name, resolution)
    sets.assign(bpy.context.scene, holder_ts, holder)
    return holder, holder_ts


# Diagonal borders: the falloff equals the plain per-texel distance to the
# nearest border edge (islands far enough apart that only their own edges count).
diamond = ((.25, .05), (.45, .25), (.25, .45), (.05, .25))
sliver = ((.55, .55), (.95, .6), (.94, .7), (.56, .62))
_, diagonal_ts = quads("Diagonal islands", (diamond, sliver), 512)
diagonal_ts.bake.uv_border_width = 3
_, diagonal = pixels(diagonal_ts, "UV_BORDER")
covered_rows, covered_cols = np.nonzero(diagonal[..., 3] > 0)
points = np.stack((covered_cols, covered_rows), axis=1).astype(np.float64)
nearest = np.full(len(points), np.inf)
for corners in (diamond, sliver):
    for a, b in zip(corners, corners[1:] + corners[:1]):
        a, b = np.array(a) * 512 - .5, np.array(b) * 512 - .5
        t = np.clip(((points - a) @ (b - a)) / ((b - a) @ (b - a)), 0, 1)
        nearest = np.minimum(nearest, np.linalg.norm(points - a - t[:, None] * (b - a), axis=1))
expected = np.clip(1 - nearest / 3, 0, 1)
assert np.allclose(diagonal[covered_rows, covered_cols, 0], expected, atol=1e-4)
assert (expected > 0).sum() > 200 and (expected == 0).sum() > 200
# Edges spanning most of a large tile only touch the texels near them: six
# full-width strips at 2048 (twelve long edges) stay well under the square
# per-edge grids that used to take a GB here and swap or fail at 4k and 8k.
import time
import tracemalloc
crate = tuple(((.05, .05 + .15 * index), (.95, .05 + .15 * index), (.95, .17 + .15 * index), (.05, .17 + .15 * index))
              for index in range(6))
_, crate_ts = quads("Crate islands", crate, 2048)
tracemalloc.start()
started = time.perf_counter()
_, crate_border = pixels(crate_ts, "UV_BORDER")
crate_seconds = time.perf_counter() - started
crate_peak = tracemalloc.get_traced_memory()[1]
tracemalloc.stop()
assert crate_peak < 600 * 2 ** 20, f"UV border bake peaked at {crate_peak / 2 ** 20:.0f} MB"
assert crate_border[int(.11 * 2048), int(.05 * 2048), 0] > .5 and crate_border[int(.11 * 2048), 1024, 0] == 0
print(f"crate border at 2048: {crate_seconds:.2f}s, {crate_peak / 2 ** 20:.0f} MB traced")
# UV island IDs: one colour per island, gathered rather than compared per triangle.
side = 40
many = tuple(((x / side + .002, y / side + .002), ((x + 1) / side - .002, y / side + .002),
              ((x + 1) / side - .002, (y + 1) / side - .002), (x / side + .002, (y + 1) / side - .002))
             for y in range(side) for x in range(side))
many_object, many_ts = quads("Many islands", many, 512)
started = time.perf_counter()
many_image, many_ids = pixels(many_ts, "UV_ISLAND")
many_seconds = time.perf_counter() - started
many_palette = json.loads(many_image["id_palette"])
assert len(many_palette) == side * side
assert len({tuple(color) for color in many_palette.values()}) == side * side
from open_painter.geometry.snapshots import _object_key
for polygon in list(many_object.data.polygons)[::97]:
    row, col = int((polygon.index // side + .5) * 512 / side), int((polygon.index % side + .5) * 512 / side)
    assert many_ids[row, col, 3] == 1
    assert np.allclose(many_ids[row, col, :3], many_palette[f"{_object_key(many_object)}|island:{polygon.index}"], atol=1e-6)
print(f"{side * side} island IDs at 512: {many_seconds:.2f}s")

# Open sheets have no valid opposing surface: do not replace thickness with AO/noise.
ts.bake.thickness_resolution = "64"
_, open_thickness = pixels(ts, "THICKNESS")
assert not open_thickness[..., 3].any()
bpy.ops.mesh.primitive_cube_add()
cube = bpy.context.object
cube_ts = sets.create(bpy.context.scene, "True thickness", 512)
sets.assign(bpy.context.scene, cube_ts, cube)
cube_ts.bake.thickness_resolution = "64"
thickness_image, thickness = pixels(cube_ts, "THICKNESS")
valid = thickness[..., 3] > 0
assert valid.any()
distances = thickness[..., 0][valid] * thickness_image["thickness_distance_scale"]
assert np.allclose(distances, 2, atol=2e-4), (distances.min(), distances.max())
assert thickness_image["thickness_ray_count"] <= 64 * 64 + 1024  # shared triangle edge pixels
cube_ts.bake.thickness_samples = "4"
_, cone = pixels(cube_ts, "THICKNESS")
assert np.isfinite(cone).all() and cone[..., 3].any()
cube_ts.bake.thickness_max_distance = 1
cube_ts.bake.thickness_samples = "1"
_, missed = pixels(cube_ts, "THICKNESS")
assert not missed[..., 3].any()

# A surface's opposite side may use an unassigned material. Its true thickness
# must still be found even when deeper than the assigned face's bounding box.
cube.scale.z = 4
cube.data.materials.append(bpy.data.materials.new("Other side of shell"))
for face in cube.data.polygons:
    face.material_index = 0 if face.normal.z > .9 else 1
cube_ts.bake.thickness_max_distance = 0
bpy.context.view_layer.update()
deep_image, deep = pixels(cube_ts, "THICKNESS")
assert deep[..., 3].any()
assert np.allclose(deep[..., 0][deep[..., 3] > 0] * deep_image["thickness_distance_scale"], 8, atol=1e-4)

# Utility-only baking is independent of an empty high-poly source list.
cube_ts.bake.normal = cube_ts.bake.ao = cube_ts.bake.curvature = False
cube_ts.bake.position = cube_ts.bake.world_normal = True
cube_ts.bake.cage_mode = "CUSTOM"  # Unrelated high-poly cage settings must not block utilities.
bake_selected(bpy.context, cube_ts)
assert {item.kind for item in cube_ts.baked_maps} == {"POSITION", "WORLD_NORMAL"}
assert not cube_ts.bake.thickness
# Adding sets reallocated the collection: re-resolve the first set.
ts = next(item for item in bpy.context.scene.open_painter.texture_sets if item.name == "Utility data")
assert len(ts.material.node_tree.nodes) == node_count
register_map(cube_ts, "THICKNESS", thickness_image)

# Signed mean curvature (ported from the node-stack add-on): a beveled cube reads convex on
# its rounded edges, flat (0.5) on face centres, and a hole punched inward reads
# concave along its rim. Coverage alpha matches the other utility maps.
cube = next(ref.object for ref in cube_ts.assignments if ref.object)
bevel = cube.modifiers.new("Bevel", "BEVEL")
bevel.width, bevel.segments = .15, 3
bpy.context.view_layer.update()
cube_ts.bake.mean_curvature_distance = .05
curvature_image, curvature = pixels(cube_ts, "MEAN_CURVATURE")
assert curvature_image["open_painter_mesh_map"] == "MEAN_CURVATURE"
covered = curvature[..., 3] > 0
assert covered.any() and np.isfinite(curvature).all()
values = curvature[..., 0][covered]
# A 0.15 bevel does not reach the 0.05 saturation radius, so the set is
# stretched until its strongest feature saturates; the effective radius is recorded.
assert values.max() > .95, values.max()
assert curvature_image["mean_curvature_effective_distance"] > .05
assert np.isclose(curvature_image["mean_curvature_distance"], .05)
# Face interiors interpolate the bevel-ring vertices (vertex domain), so flat
# areas sit above 0.5 rather than exactly on it; nothing reads concave.
assert .5 <= np.median(values) < .75, np.median(values)
assert values.min() >= .5 - 1e-4
# Non-uniform scale and mirroring must not flip or break the sign.
cube.scale = (-1.5, 1, .5)
bpy.context.view_layer.update()
mirrored = pixels(cube_ts, "MEAN_CURVATURE")[1]
assert mirrored[..., 0][mirrored[..., 3] > 0].max() > .9 and mirrored[..., 0][mirrored[..., 3] > 0].min() >= .5 - 1e-4
cube.scale = (1, 1, 1)
# A concave feature: a recessed pocket in the top face. Its floor edges are
# concave (dark) while the pocket lip and outer edges stay convex (bright).
cube.modifiers.remove(bevel)
pocket = bpy.data.objects.new("pocket", bpy.data.meshes.new("pocket"))
bm = bmesh.new(); bmesh.ops.create_cube(bm, size=2)
top = max(bm.faces, key=lambda f: f.calc_center_median().z)
inset = bmesh.ops.inset_region(bm, faces=[top], thickness=.6, depth=0)
bmesh.ops.translate(bm, verts=top.verts, vec=(0, 0, -.8))
bm.to_mesh(pocket.data); bm.free()
bpy.context.collection.objects.link(pocket)
bpy.context.view_layer.objects.active = pocket
bpy.ops.object.select_all(action="DESELECT"); pocket.select_set(True)
bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(island_margin=.02); bpy.ops.object.mode_set(mode="OBJECT")
pocket_ts = sets.create(bpy.context.scene, "Pocket", 512)
sets.assign(bpy.context.scene, pocket_ts, pocket)
pocket_ts.bake.mean_curvature_distance = .05
recess = pixels(pocket_ts, "MEAN_CURVATURE")[1]
recess_values = recess[..., 0][recess[..., 3] > 0]
assert recess_values.min() < .2 and recess_values.max() > .9, (recess_values.min(), recess_values.max())
bpy.context.view_layer.objects.active = cube

# Selection map: chosen faces are 1, others 0; the face list is recorded; a
# topology-changing modifier is refused instead of guessed.
for face in cube.data.polygons:
    face.select = face.normal.z > .9
selection_image, selection = pixels(cube_ts, "SELECTION")
assert json.loads(selection_image["selected_faces"]) == {cube.name: [f.index for f in cube.data.polygons if f.select]}
# Only the top face belongs to this set's slot, so its texels are the covered ones.
sel = selection[..., 0][selection[..., 3] > 0]
assert sel.size and np.all(sel == 1), np.unique(sel)[:8]
for face in cube.data.polygons:
    face.select = False
cleared = pixels(cube_ts, "SELECTION")[1]
assert np.all(cleared[..., 0][cleared[..., 3] > 0] == 0)
for face in cube.data.polygons:
    face.select = face.normal.z > .9
subdivide = cube.modifiers.new("Subdivide", "SUBSURF")
try:
    bake_utility(cube_ts, "SELECTION")
    raise AssertionError("Selection mapped through a topology-changing modifier")
except ValueError as exc:
    assert "face count" in str(exc)
cube.modifiers.remove(subdivide)
for face in cube.data.polygons:
    face.select = face.normal.z > .9
# Both kinds are selectable baked-mask sources and bake through the queue.
cube_ts.bake.position = cube_ts.bake.world_normal = False
cube_ts.bake.mean_curvature = cube_ts.bake.selection = True
bake_selected(bpy.context, cube_ts)
assert {item.kind for item in cube_ts.baked_maps} >= {"MEAN_CURVATURE", "SELECTION"}
from open_painter.core import layer_stack as stack
probe_layer = stack.add(cube_ts, "FILL", "Curvature probe")
probe_mask = probe_layer.masks.add(); probe_mask.source, probe_mask.map_type = "BAKED", "MEAN_CURVATURE"
from open_painter.evaluation.mask_eval import evaluate_masks
masked = evaluate_masks(cube_ts, probe_layer, 64, 0, 64, {}, {})
assert masked.shape[:2] == (64, 64) and np.isfinite(masked).all()
# The persistent shader graph (0.2.7 native authoring, when present) samples any
# baked map kind through the same generic path.
try:
    from open_painter.evaluation import shader_preview
except ImportError:
    shader_preview = None
if shader_preview is not None:
    graph = shader_preview.ensure(bpy.context.scene, cube_ts)
    assert graph is not None and graph.tree is not None and not graph.pending_resources
    curvature_prop = next(m for m in cube_ts.baked_maps if m.kind == "MEAN_CURVATURE")
    assert any(node.bl_idname == "ShaderNodeTexImage" and node.image == curvature_prop.image for node in graph.nodes())
# Recorded faces can be restored on the base mesh.
for face in cube.data.polygons:
    face.select = False
bpy.context.view_layer.objects.active = cube
project = bpy.context.scene.open_painter
project.active_texture_set = next(i for i, item in enumerate(project.texture_sets) if item.id == cube_ts.id)
assert bpy.ops.open_painter.bake(action="RESELECT_MAP") == {"FINISHED"}
assert [face.select for face in cube.data.polygons] == [face.normal.z > .9 for face in cube.data.polygons]
filename = root / ".blender-test" / "utility-maps.blend"
filename.parent.mkdir(exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(filename))
bpy.ops.wm.open_mainfile(filepath=str(filename))
loaded = next(item for item in bpy.context.scene.open_painter.texture_sets if item.name == "True thickness")
saved_thickness = next(item.image for item in loaded.baked_maps if item.kind == "THICKNESS")
assert saved_thickness.packed_file and saved_thickness["thickness_valid_hits"] > 0
assert np.allclose(images.pixels(saved_thickness), thickness)
open_painter.unregister()
print("UTILITY_MAPS_PASS: assigned bounds; render UVs; world/object normals; stable IDs; islands/borders; evaluated modifiers; true closed/open thickness; signed mean curvature; selection mask; packed save/reopen; no shader effect nodes")
