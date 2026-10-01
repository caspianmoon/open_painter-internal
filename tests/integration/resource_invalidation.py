"""Material-only edits retain geometry and raw triplanar sources."""
import sys
from pathlib import Path
from unittest.mock import patch
import bpy
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.painting import projection
from open_painter.texture_engine import procedurals

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Geometry lifetime', 512)
ref = ts.assignments.add()
ref.object = bpy.context.object
# Both assignment targets must contain real faces in the evaluated mesh.
ref.object.data.materials.append(bpy.data.materials.new("Alternate slot"))
ref.object.data.polygons[0].material_index = 1
ref.object.data.update()
layer = stack.add(ts, 'FILL')
rough = stack.channel_for(layer, 'ROUGHNESS')
rough.procedural, rough.projection = 'FBM', 'TRIPLANAR'
rough.procedural_scale = 3.71
LayerEvaluator(scene).evaluate_texture_set(ts.id)
before = images.pixels(images.output(ts, 'ROUGHNESS').image)
rough.value = .73
with patch.object(projection, 'geometry', wraps=projection.geometry) as geometry, \
     patch.object(procedurals, 'evaluate', wraps=procedurals.evaluate) as noise:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    assert geometry.call_count == 0, 'Roughness edit rebuilt unchanged geometry'
    assert noise.call_count == 0, 'Roughness edit regenerated unchanged triplanar source'
after = images.pixels(images.output(ts, 'ROUGHNESS').image)
assert np.max(abs(after-before)) > .01
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
np.testing.assert_array_equal(images.pixels(images.output(ts, 'ROUGHNESS').image), after)

# Real depsgraph updates: unrelated movement and the frame of a static prop preserve
# geometry; assigned movement, UVs, vertex edits, modifier settings, assignment changes
# and the frame of a deformed prop retire it.
obj = ref.object
other = scene.objects.get('Camera')
bpy.context.view_layer.update()
LayerEvaluator(scene).evaluate_texture_set(ts.id)
other.location.x += 1
bpy.context.view_layer.update()
rough.value -= .02
with patch.object(projection, 'geometry', wraps=projection.geometry) as geometry:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    assert geometry.call_count == 0, 'Unrelated object movement rebuilt geometry'

# Exact until the prop gets a bevel (below): from then on a forced evaluation may differ
# by Blender's bevel UV rounding.
reevaluated_atol = 0


def changed_geometry(mutate, label, changes_pixels=True):
    before = images.pixels(images.output(ts, 'ROUGHNESS').image)
    mutate()
    bpy.context.view_layer.update()
    with patch.object(projection, 'geometry', wraps=projection.geometry) as geometry:
        LayerEvaluator(scene).evaluate_texture_set(ts.id)
        assert geometry.call_count == 1, label + ' did not rebuild geometry'
    actual = images.pixels(images.output(ts, 'ROUGHNESS').image)
    if changes_pixels:
        assert np.max(abs(before-actual)) > 1e-5, label + ' had no effect'
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    forced = images.pixels(images.output(ts, 'ROUGHNESS').image)
    if reevaluated_atol:
        # The forced pass evaluates the mesh again; see the bevel note below.
        np.testing.assert_allclose(forced, actual, rtol=0, atol=reevaluated_atol)
    else:
        np.testing.assert_array_equal(forced, actual)

changed_geometry(lambda: setattr(obj.location, 'x', obj.location.x+.31), 'transform')
def edit_uv():
    obj.data.uv_layers.active.data[obj.data.polygons[1].loop_start].uv.x += .1
    obj.data.update()
changed_geometry(edit_uv, 'UV')
def edit_mesh():
    obj.data.vertices[0].co.z += .2
    obj.data.update()
changed_geometry(edit_mesh, 'mesh')
modifier = obj.modifiers.new('Lifetime bevel', 'BEVEL')
# Blender's bevel can round a few corner UVs one ulp differently from one evaluation to
# the next (seen on Windows, even single-threaded): the forced pass then samples the
# procedural a hair away at those texels: 1.3% of texels moved, by up to 1.8e-7.
reevaluated_atol = 1e-6
changed_geometry(lambda: setattr(modifier, 'width', .15), 'modifier', False)
# A static prop's key leaves the frame out: scrubbing the timeline keeps its geometry and
# every raster keyed on it. An armature-deformed prop follows the pose, so its key does not.
from open_painter.evaluation import dependencies
assert not dependencies.animated(ts), 'a bevelled static prop counted as animated'
static_key = dependencies.geometry_key(scene, ts)
scene.frame_set(scene.frame_current+1)
bpy.context.view_layer.update()
assert dependencies.geometry_key(scene, ts) == static_key, 'a frame change re-keyed a static prop'
with patch.object(projection, 'geometry', wraps=projection.geometry) as geometry:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    assert geometry.call_count == 0, 'a frame change rebuilt the geometry of a static prop'
rig = bpy.data.objects.new('Lifetime rig', bpy.data.armatures.new('Lifetime rig'))
scene.collection.objects.link(rig)
deform = obj.modifiers.new('Lifetime armature', 'ARMATURE')
deform.object = rig
bpy.context.view_layer.update()
assert dependencies.animated(ts), 'an armature-deformed prop counted as static'
deformed_key = dependencies.geometry_key(scene, ts)
assert deformed_key != static_key and deformed_key[1] == (scene.frame_current, scene.frame_subframe)
LayerEvaluator(scene).evaluate_texture_set(ts.id)
changed_geometry(lambda: scene.frame_set(scene.frame_current+1), 'frame', False)
assert dependencies.geometry_key(scene, ts)[1] == (scene.frame_current, scene.frame_subframe)
obj.modifiers.remove(deform)
bpy.data.objects.remove(rig)
bpy.context.view_layer.update()
assert not dependencies.animated(ts)
# What a modifier reads is walked too: a static Boolean cutter keeps the prop static, a
# keyed one (or a cutter deformed by its own rig, or an animated object up the cutter's
# parent chain) carries the frame into the key; a cutter reading its owner back
# terminates. Frame-driven kinds the set once missed (a .mdd Mesh Cache) count on their own.
cutter = bpy.data.objects.new('Lifetime cutter', bpy.data.meshes.new('Lifetime cutter'))
scene.collection.objects.link(cutter)
cut = obj.modifiers.new('Lifetime cut', 'BOOLEAN')
cut.object = cutter
bpy.context.view_layer.update()
assert not dependencies.animated(ts), 'a static Boolean cutter counted as animated'
assert dependencies.geometry_key(scene, ts)[1] is None
cutter.keyframe_insert('location', frame=1)
cutter.location.x += .5
cutter.keyframe_insert('location', frame=10)
bpy.context.view_layer.update()
assert dependencies.animated(ts), 'an animated Boolean cutter counted as static'
assert dependencies.geometry_key(scene, ts)[1] == (scene.frame_current, scene.frame_subframe)
cutter.animation_data.action = None   # animation_data_clear() crashes 5.2.1 headless
assert not dependencies.animated(ts)
cutter_rig = bpy.data.objects.new('Lifetime cutter rig', bpy.data.armatures.new('Lifetime cutter rig'))
scene.collection.objects.link(cutter_rig)
cutter.modifiers.new('Lifetime cutter armature', 'ARMATURE').object = cutter_rig
assert dependencies.animated(ts), 'a rigged Boolean cutter counted as static'
cutter.modifiers.clear()
assert not dependencies.animated(ts)
anchor = bpy.data.objects.new('Lifetime cutter anchor', None)
scene.collection.objects.link(anchor)
cutter.parent = anchor
anchor.keyframe_insert('rotation_euler', frame=1)
assert dependencies.animated(ts), "an animated parent of a Boolean cutter counted as static"
cutter.parent = None
anchor.animation_data.action = None
assert not dependencies.animated(ts)
cutter.modifiers.new('Lifetime cut back', 'BOOLEAN').object = obj   # cycle: must terminate
assert not dependencies.animated(ts)
cutter.modifiers.clear()
for kind in ('MESH_CACHE', 'FLUID', 'DYNAMIC_PAINT', 'EXPLODE', 'PARTICLE_INSTANCE'):
    assert kind in dependencies._TIME_MODIFIERS, kind + ' is frame-driven but left the frame out of the key'
cache = obj.modifiers.new('Lifetime mesh cache', 'MESH_CACHE')
assert dependencies.animated(ts), 'a Mesh Cache prop counted as static'
obj.modifiers.remove(cache)
obj.modifiers.remove(cut)
bpy.data.objects.remove(cutter)
bpy.data.objects.remove(cutter_rig)
bpy.data.objects.remove(anchor)
bpy.context.view_layer.update()
assert not dependencies.animated(ts)
LayerEvaluator(scene).evaluate_texture_set(ts.id)
changed_geometry(lambda: setattr(ref, 'slot_index', 1), 'assignment')
changed_geometry(lambda: setattr(ref, 'slot_index', 0), 'assignment restore')
def render_uv():
    uv = obj.data.uv_layers.new(name='Render lifetime UV')
    uv.active_render = True
    uv.data[obj.data.polygons[1].loop_start].uv.x += .1
    obj.data.update()
changed_geometry(render_uv, 'render UV')

# A managed map write invalidates only dependent sources. AO replacement must
# leave a UV procedural mask cached; the final result still matches force.
layer = stack.add(ts, 'FILL')
mask = layer.masks.add(); mask.source = 'GENERATOR'; mask.generator = 'CAVITY_DIRT'
entry = ts.baked_maps.add(); entry.kind = 'AO'; entry.image = images.create('Revision AO',32,(1,1,1,1))
other_layer = stack.add(ts, 'FILL')
noise_mask = other_layer.masks.add(); noise_mask.source='PROCEDURAL'; noise_mask.procedural='FBM'
LayerEvaluator(scene).evaluate_texture_set(ts.id)
from open_painter.evaluation import mask_eval
old_revision = entry.image.get('open_painter_pixel_revision',0)
images.write(entry.image,np.tile(np.array([.3,.3,.3,1],np.float32),(32,32,1)),pack=False)
assert entry.image['open_painter_pixel_revision'] == old_revision+1 and ts.dirty
with patch.object(mask_eval,'_evaluate_masks',wraps=mask_eval._evaluate_masks) as calls:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    assert calls.call_count > 0
    assert {call.args[1].id for call in calls.call_args_list} == {layer.id}, 'AO write invalidated an unrelated mask'
actual=images.pixels(images.output(ts,'BASE_COLOR').image)
LayerEvaluator(scene).evaluate_texture_set(ts.id,force=True)
np.testing.assert_array_equal(images.pixels(images.output(ts,'BASE_COLOR').image),actual)
open_painter.unregister()
print('RESOURCE_INVALIDATION_PASS', flush=True)
