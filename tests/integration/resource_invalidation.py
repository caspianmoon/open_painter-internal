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

# Real depsgraph updates: unrelated movement preserves geometry; assigned movement,
# UVs, vertex edits, modifier settings, frame and assignment changes retire it.
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
    np.testing.assert_array_equal(images.pixels(images.output(ts, 'ROUGHNESS').image), actual)

changed_geometry(lambda: setattr(obj.location, 'x', obj.location.x+.31), 'transform')
def edit_uv():
    obj.data.uv_layers.active.data[0].uv.x += .1
    obj.data.update()
changed_geometry(edit_uv, 'UV')
def edit_mesh():
    obj.data.vertices[0].co.z += .2
    obj.data.update()
changed_geometry(edit_mesh, 'mesh')
modifier = obj.modifiers.new('Lifetime bevel', 'BEVEL')
changed_geometry(lambda: setattr(modifier, 'width', .15), 'modifier', False)
changed_geometry(lambda: scene.frame_set(scene.frame_current+1), 'frame', False)
changed_geometry(lambda: setattr(ref, 'slot_index', 1), 'assignment')
changed_geometry(lambda: setattr(ref, 'slot_index', 0), 'assignment restore')
def render_uv():
    uv = obj.data.uv_layers.new(name='Render lifetime UV')
    uv.active_render = True
    uv.data[0].uv.x += .1
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
