"""Panel/bolt fields react to explicit inputs, preserve maps and survive reload."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.generators import scene_fields
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation import dependencies
from open_painter.evaluation.metrics import Metrics
from open_painter.utils.ids import new_id
open_painter.register()
scene=bpy.context.scene
bpy.ops.mesh.primitive_plane_add(size=2)
panel=bpy.context.object
ts=sets.create(scene,'Panel',512); sets.assign(scene,ts,panel)
layer=stack.add(ts,'FILL'); rough=stack.channel_for(layer,'ROUGHNESS'); rough.value=.1
mask=layer.masks.add(); mask.id=new_id(); mask.source='SCENE'
field=mask.influence; field.kind='CONTACT'; field.distance=.5; field.mode='LIVE'
bpy.ops.mesh.primitive_uv_sphere_add(segments=12,ring_count=8,radius=.15,location=(0,0,.1))
bolt=bpy.context.object; field.object=bolt
bpy.context.view_layer.update()
LayerEvaluator(scene).evaluate_texture_set(ts.id)
first=images.pixels(field.image).copy()
assert first[32,32,0]>.7 and first[0,0,0]==0
assert not ts.baked_maps
rough.value=.2
with Metrics() as counters:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
assert counters.phases.get('scene_analysis',{}).get('calls',0)==0
assert counters.counts['scene_query_samples']==0
bolt.location.x=.5; bpy.context.view_layer.update(); dependencies.refresh(scene)
assert ts.dirty
LayerEvaluator(scene).evaluate_texture_set(ts.id)
second=images.pixels(field.image)
assert not np.array_equal(first,second) and second[32,48,0]>.7
assert not ts.baked_maps
field.kind='UP'
LayerEvaluator(scene).evaluate_texture_set(ts.id)
np.testing.assert_allclose(images.pixels(field.image)[...,0],1,atol=1e-6)
panel.rotation_euler.x=np.pi; bpy.context.view_layer.update(); dependencies.refresh(scene)
LayerEvaluator(scene).evaluate_texture_set(ts.id)
np.testing.assert_allclose(images.pixels(field.image)[...,0],0,atol=1e-6)
# Missing references produce failure without destroying last good analysis.
field.kind='CONTACT'; field.object=bolt
LayerEvaluator(scene).evaluate_texture_set(ts.id)
last=field.image; last_pixels=images.pixels(last)
bpy.data.objects.remove(bolt,do_unlink=True); bpy.context.view_layer.update(); stack.invalidate(ts)
try:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    raise AssertionError('Deleted reference silently accepted')
except ValueError as exc:
    assert 'existing external mesh' in str(exc)
assert field.image==last
np.testing.assert_array_equal(images.pixels(last),last_pixels)
field.mode='FROZEN'
LayerEvaluator(scene).evaluate_texture_set(ts.id)
with TemporaryDirectory(prefix='op-scene-field-') as directory:
    from open_painter.smart_materials.serializer import save
    from open_painter.smart_materials.loader import apply
    save(ts,Path(directory)/'frozen.openpaintmaterial')
    other=sets.create(scene,'Portable snapshot',512)
    apply(other,Path(directory)/'frozen.openpaintmaterial')
    imported=other.layers[0].masks[0].influence
    assert imported.mode=='FROZEN' and imported.object is None
    np.testing.assert_allclose(images.pixels(imported.image),last_pixels,atol=1e-6)
    identifier=ts.id
    bpy.ops.wm.save_as_mainfile(filepath=directory+'/scene.blend')
    bpy.ops.wm.open_mainfile(filepath=directory+'/scene.blend')
    ts=next(t for t in bpy.context.scene.open_painter.texture_sets if t.id==identifier)
    assert ts.layers[0].masks[0].influence.image.packed_file
open_painter.unregister()
print('SCENE_MASKS_PASS',flush=True)
