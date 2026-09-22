"""Physical occlusion and real-map registration smoke; no provider or Cycles."""
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2] / "src")]
import open_painter
from cpu_maps import sample_accessibility, bake_ao
from open_painter.core import texture_set_manager
from open_painter.baking.bake_manager import register_map
from open_painter.geometry.provenance import status

open_painter.register()
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
points = np.array([[0, 0, 0], [.2, .2, 0], [-.2, 0, 0]], float)
normals = np.repeat([[0, 0, 1]], 3, axis=0)
floor = [(-2,-2,0), (2,-2,0), (2,2,0), (-2,2,0)]
faces = [(0,1,2), (0,2,3)]
tree = BVHTree.FromPolygons(floor, faces, all_triangles=True)
assert np.array_equal(sample_accessibility(tree, points, normals), np.ones(3)), "Unoccluded plane falsely self-shadows"
ceiling = [(x,y,.005) for x,y,z in floor]
blocked = BVHTree.FromPolygons(floor + ceiling, faces + [(4,6,5),(4,7,6)], all_triangles=True)
closed = sample_accessibility(blocked, points, normals)
assert np.array_equal(closed, np.zeros(3)), "Overhead geometry did not occlude actual hemisphere rays"
assert np.array_equal(closed, sample_accessibility(blocked, points, normals)), "Sampling is nondeterministic"

collection = bpy.data.collections.new("TEST_MODEL")
scene.collection.children.link(collection)
mesh = bpy.data.meshes.new("AO test geometry")
mesh.from_pydata(floor, [], [(0,1,2,3)])
mesh.update()
obj = bpy.data.objects.new("AO target", mesh)
collection.objects.link(obj)
uv = mesh.uv_layers.new(name="UVMap")
for index, co in enumerate(((0,0),(1,0),(1,1),(0,1))):
    uv.data[index].uv = co
ts = texture_set_manager.create(scene, "AO probe")
ts.resolution = "512"
texture_set_manager.assign(scene, ts, obj)
image = bake_ao(ts, sampling_resolution=128, samples=8)
register_map(ts, "AO", image)
pixels = np.asarray(image.pixels[:]).reshape(512,512,4)
assert np.all(pixels[..., :3] == 1), "Real plane UV bake must have fully open accessibility"
assert np.all(pixels[..., 3] == 1), "Real plane UV bake coverage incomplete"
assert status(ts, ts.baked_maps[-1]) == "Ready"
assert image.packed_file and image["ray_count"] == 128 * 128 * 8
assert scene.render.engine == "BLENDER_EEVEE"
assert not any(s.render.engine == "CYCLES" for s in bpy.data.scenes)
print("BENCHMARK_CPU_AO_SMOKE_PASS", flush=True)
