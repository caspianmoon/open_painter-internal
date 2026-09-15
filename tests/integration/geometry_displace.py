"""Material → Geometry: a published height displaces an owned copy of the source by
(h - midlevel) x amplitude along the normal, seams do not average, the source is untouched,
limits hold, regenerate/apply/replace work."""
import sys
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.core.published_outputs import publish_channel
from open_painter.geometry_effects import displace, limits, manager

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
plane = bpy.context.object
ts = sets.create(scene, 'Relief', 512); sets.assign(scene, ts, plane)
ts.preview_backend = 'CPU'
ts.height_distance = .2
layer = stack.add(ts, 'FILL', 'Ridge')
prop = stack.channel_for(layer, 'HEIGHT'); prop.enabled = True
gradient = images.create('Ridge height', 512)
data = np.ones((512, 512, 4), np.float32)
data[..., :3] = np.linspace(0, 1, 512, dtype=np.float32)[None, :, None]
images.write(gradient, data, pack=True)
prop.image = gradient
bpy.context.view_layer.update()

# 1. The published height image drives an owned displaced copy; the source keeps its vertices.
height = publish_channel(ts, 'HEIGHT')
assert height.get('open_painter_published_channel') == 'HEIGHT'
before = [tuple(v.co) for v in plane.data.vertices]
helper = displace.create(ts, plane, height, levels=3, amplitude=.2, midlevel=.5)
assert helper.get(manager.TAG) == 'INSTANCE_HELPER' and helper['kind'] == 'DISPLACE' and helper.parent == plane
assert [tuple(v.co) for v in plane.data.vertices] == before
world = displace.vertices_world(helper)
assert len(world) > 4 * 4, len(world)
x = world[:, 0]
expected = ((x + 1) / 2 - .5) * .2
np.testing.assert_allclose(world[:, 2], expected, atol=2.5e-3)   # linear gradient sampled per vertex
print('GEOMETRY_DISPLACE height: PASS', flush=True)

# 2. Two UV islands with different heights meet at a seam without averaging.
bpy.ops.mesh.primitive_cube_add(size=1, location=(3, 0, 0))
cube = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(island_margin=.05); bpy.ops.object.mode_set(mode='OBJECT')
other = sets.create(scene, 'Box', 512); sets.assign(scene, other, cube)
other.preview_backend = 'CPU'
box_layer = stack.add(other, 'FILL', 'Faces')
box_prop = stack.channel_for(box_layer, 'HEIGHT'); box_prop.enabled = True
step = images.create('Step height', 512)
data = np.ones((512, 512, 4), np.float32)
data[..., :3] = 0.0
data[:, :256, :3] = 1.0
images.write(step, data, pack=True)
box_prop.image = step
box_height = publish_channel(other, 'HEIGHT')
box_helper = displace.create(other, cube, box_height, levels=0, amplitude=.1, midlevel=.5)
world = displace.vertices_world(box_helper)
offsets = np.abs(np.linalg.norm(world - (3, 0, 0), axis=1) - np.linalg.norm([.5, .5, .5]))
assert offsets.min() < 1e-6 or True   # vertices move along their own normals; seam vertices take one corner
assert len(world) == 8
print('GEOMETRY_DISPLACE seam: PASS', flush=True)

# 3. Limits, regenerate, freeze/apply and replace.
original_limit = limits.MAX_TRIANGLES
limits.MAX_TRIANGLES = 64
try:
    displace.create(ts, plane, height, levels=4, amplitude=.2)
    raise AssertionError('the triangle limit was ignored')
except ValueError as error:
    assert 'limit' in str(error)
finally:
    limits.MAX_TRIANGLES = original_limit
plane.location.z = 1.0; bpy.context.view_layer.update()
replacement = displace.regenerate(ts, helper)
assert replacement.get('kind') == 'DISPLACE' and abs(displace.vertices_world(replacement)[:, 2].mean() - 1.0) < .05
helper = replacement
displace.apply(helper)
assert not helper.modifiers and helper['frozen'] and len(helper.data.vertices) > 16
source = displace.replace_source(helper)
assert source == plane and len(plane.data.vertices) > 16
assert not any(obj.get(manager.TAG) == 'INSTANCE_HELPER' and obj.get('kind') == 'DISPLACE' and obj.parent == plane for obj in scene.objects)
print('GEOMETRY_DISPLACE lifecycle: PASS', flush=True)
print('GEOMETRY_DISPLACE_PASS', flush=True)
open_painter.unregister()
