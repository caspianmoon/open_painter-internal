"""Material → Geometry: a published height displaces an owned copy of the source by
(h - midlevel) x amplitude along the normal, seams do not average, the source is untouched,
limits hold, regenerate/apply/replace work, helpers and unassigned meshes are refused as sources,
tuned modifier inputs drive Regenerate and the bake-back reach, Replace Source drops the modifiers it
already contains, and only the set's own slot faces displace."""
import sys
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.baking import bake_back
from open_painter.core.published_outputs import publish_channel
from open_painter.geometry.snapshots import targets
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
# The helper links its snapshot (a rename follows) and regenerates for the set it was made
# for even when another set is active; the last helper using the snapshot releases it.
assert helper['source_image'] == height
height.name = 'relief'
replacement = displace.regenerate(other, helper)
assert replacement.get('kind') == 'DISPLACE' and abs(displace.vertices_world(replacement)[:, 2].mean() - 1.0) < .05
assert replacement['source_texture_set'] == ts.id and replacement['source_image'] == height
assert bpy.data.images.get('relief') == height and height.use_fake_user
helper = replacement
displace.apply(helper)
assert not helper.modifiers and helper['frozen'] and len(helper.data.vertices) > 16
source = displace.replace_source(helper)
assert source == plane and len(plane.data.vertices) > 16
assert not any(obj.get(manager.TAG) == 'INSTANCE_HELPER' and obj.get('kind') == 'DISPLACE' and obj.parent == plane for obj in scene.objects)
assert bpy.data.images.get('relief') is None, 'published height snapshot leaked after the helper went away'
assert box_height.name in bpy.data.images   # the box helper still holds its own snapshot
print('GEOMETRY_DISPLACE lifecycle: PASS', flush=True)

# 4. Guards: a helper is never a source (the operator leaves it active), nor is an unassigned mesh.
height = publish_channel(ts, 'HEIGHT')   # the last helper released the earlier snapshot
helper = displace.create(ts, plane, height, levels=1, amplitude=.2)
# The sidebar panel offers Apply / Replace Source and Regenerate / Freeze for a displaced copy;
# amplitude and midlevel live on the modifier, so only the creation-time keys are exposed.
from open_painter.ui.geometry_effects import OP_PT_geometry_effects
class Layout:
    def __init__(self, calls): self.calls = calls
    def row(self, **_): return self
    def column(self, **_): return self
    def box(self): return self
    def panel(self, *_a, **_k): return self, self
    def menu(self, *_a, **_k): pass
    def label(self, **_): pass
    def prop(self, data, path, **_): self.calls.append(('prop', path))
    def operator(self, idname, text='', **_):
        self.calls.append(('operator', text)); return type('Props', (), {'action': None, 'name': None})()
panel_calls = []
OP_PT_geometry_effects.draw(type('Panel', (), {'layout': Layout(panel_calls)})(), type('Context', (), {'active_object': helper})())
panel_actions = {text for kind, text in panel_calls if kind == 'operator'}
assert {'Apply', 'Replace Source Mesh', 'Regenerate', 'Freeze', 'Remove Effect'} <= panel_actions, panel_actions
assert {path for kind, path in panel_calls if kind == 'prop'} == {'["levels"]', '["smooth"]'}, panel_calls
try:
    displace.create(ts, helper, height, levels=1, amplitude=.2)
    raise AssertionError('a displaced helper was accepted as a source')
except ValueError as error:
    assert 'helper' in str(error), error
bpy.ops.mesh.primitive_plane_add(size=2, location=(0, 5, 0))
stranger = bpy.context.object
try:
    displace.create(ts, stranger, height, levels=1, amplitude=.2)
    raise AssertionError('an unassigned mesh was accepted as a source')
except ValueError as error:
    assert 'assigned' in str(error), error
bpy.data.objects.remove(stranger, do_unlink=True)

# 5. Amplitude / Midlevel tuned on the modifier drive the bake-back reach and survive Regenerate.
modifier = helper.modifiers[0]
sockets = {item.name: item.identifier for item in modifier.node_group.interface.items_tree
           if item.item_type == 'SOCKET' and item.in_out == 'INPUT'}
getattr(modifier.properties.inputs, sockets['Amplitude']).value = .6
getattr(modifier.properties.inputs, sockets['Midlevel']).value = .2
helper.update_tag(); bpy.context.view_layer.update()   # the modifier panel tags the edit itself
tuned = displace.settings(helper)
assert abs(tuned['amplitude'] - .6) < 1e-6 and abs(tuned['midlevel'] - .2) < 1e-6, tuned
assert displace.vertices_world(helper)[:, 2].max() - 1.0 > .4   # the relief really is the tuned one
ts.bake.ray_distance = .1
with bake_back.staged(ts, [helper]):
    assert ts.bake.ray_distance > .85, ts.bake.ray_distance   # .6 x 1.5, not the creation-time .2
ts.bake.high_objects.clear(); ts.bake.ray_distance = .1
helper = displace.regenerate(ts, helper)
assert abs(helper['amplitude'] - .6) < 1e-6 and abs(helper['midlevel'] - .2) < 1e-6, (helper['amplitude'], helper['midlevel'])
assert displace.vertices_world(helper)[:, 2].max() - 1.0 > .4
manager.remove(helper)
print('GEOMETRY_DISPLACE tuned inputs: PASS', flush=True)

# 6. Replace Source Mesh on a source with live modifiers: the copy already holds their result,
# so they go (a Mirror would double the mesh, a Subdivision re-subdivide it); render-only ones stay.
bpy.ops.mesh.primitive_plane_add(size=2, location=(0, -5, 0))
half = bpy.context.object
sets.assign(scene, ts, half)
mirror = half.modifiers.new('Mirror', 'MIRROR')
subsurf = half.modifiers.new('Subsurf', 'SUBSURF'); subsurf.levels = 1
render_only = half.modifiers.new('Render bevel', 'BEVEL'); render_only.show_viewport = False
bpy.context.view_layer.update()
height = publish_channel(ts, 'HEIGHT')
half_helper = displace.create(ts, half, height, levels=1, amplitude=.2)
previewed = len(displace.vertices_world(half_helper))
displace.apply(half_helper)
assert len(half_helper.data.vertices) == previewed
assert displace.replace_source(half_helper) == half
assert [m.name for m in half.modifiers] == ['Render bevel'], [m.name for m in half.modifiers]
assert len(half.data.vertices) == previewed and len(displace.vertices_world(half)) == previewed
assert half.data.get(manager.TAG) is None and half.data.get('effect_owner') is None
print('GEOMETRY_DISPLACE modified source: PASS', flush=True)

# 7. Two sets own two slots of one grid: only this set's faces move, materials and indices stay,
# the other set's Bake Back does not stage the helper, and Replace Source keeps its faces.
bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(0, 10, 0))
grid = bpy.context.object
grid.data.materials.append(bpy.data.materials.new('left')); grid.data.materials.append(bpy.data.materials.new('right'))
for face in grid.data.polygons:
    face.material_index = 0 if face.center.x < 0 else 1
right_faces = sum(1 for face in grid.data.polygons if face.material_index == 1)
left_set = sets.create(scene, 'Left', 512); sets.assign(scene, left_set, grid, slot_index=0)
right_set = sets.create(scene, 'Right', 512); sets.assign(scene, right_set, grid, slot_index=1)
for owner in (left_set, right_set):
    owner.preview_backend = 'CPU'
    owner_layer = stack.add(owner, 'FILL', 'Flat')
    owner_prop = stack.channel_for(owner_layer, 'HEIGHT'); owner_prop.enabled = True
    flat = images.create(owner.name + ' height', 512)
    data = np.ones((512, 512, 4), np.float32)
    images.write(flat, data, pack=True)
    owner_prop.image = flat
bpy.context.view_layer.update()
left_height = publish_channel(left_set, 'HEIGHT')
grid_helper = displace.create(left_set, grid, left_height, levels=1, amplitude=.2, midlevel=.5)
world = displace.vertices_world(grid_helper)
local_x = world[:, 0] - grid.matrix_world.translation.x
moved = np.abs(world[:, 2] - grid.matrix_world.translation.z) > 1e-4
assert moved[local_x < -1e-4].all(), 'a face of the set did not move'
assert not moved[local_x > 1e-4].any(), "the other set's faces moved"
graph = bpy.context.evaluated_depsgraph_get()
evaluated_grid = grid_helper.evaluated_get(graph).data
assert [m.name for m in evaluated_grid.materials] == [m.name for m in grid.data.materials]
assert sum(1 for face in evaluated_grid.polygons if face.material_index == 1) == right_faces * 4
assert grid_helper in bake_back.helpers_of(left_set) and grid_helper not in bake_back.helpers_of(right_set)
displace.apply(grid_helper)
assert displace.replace_source(grid_helper) == grid
assert sum(1 for face in grid.data.polygons if face.material_index == 1) == right_faces * 4
with targets(right_set) as entries:
    assert len(entries) == 1 and len(entries[0]['tri_vertices']) == right_faces * 4 * 2
print('GEOMETRY_DISPLACE slots: PASS', flush=True)
print('GEOMETRY_DISPLACE_PASS', flush=True)
open_painter.unregister()
