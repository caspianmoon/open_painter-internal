"""Inverted geometric masks do not acquire UV-seam stripes; source stays intact."""
from pathlib import Path
import sys
import bpy
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
import open_painter
from open_painter.core import image_manager,layer_stack,texture_set_manager as sets
from open_painter.evaluation import cache
from open_painter.evaluation.blend_modes import sample_uv
from open_painter.evaluation.uv_gutters import padded
from open_painter.export.padding import ownership
from open_painter.generators import scene_fields
from open_painter.authoring import document
open_painter.register()
if bpy.app.timers.is_registered(cache.tick):bpy.app.timers.unregister(cache.tick)
scene=bpy.data.scenes.new('Gutter regression');bpy.context.window.scene=scene
mesh=bpy.data.meshes.new('Upper physical plane with chart gutters')
mesh.from_pydata([(0,0,.8),(1,0,.8),(1,1,.8),(0,1,.8)],[],[(0,1,2,3)])
uv=mesh.uv_layers.new()
# Exact geometric edge lies between covered and unowned pixel centers.
for loop,point in zip(mesh.polygons[0].loop_indices,((.251, .25),(.749,.25),(.749,.75),(.251,.75))):uv.data[loop].uv=point
obj=bpy.data.objects.new('Target',mesh);scene.collection.objects.link(obj)
ts=sets.create(scene,'Mask gutters',512);sets.assign(scene,ts,obj)
layer=layer_stack.add(ts,'FILL','Low-only effect')
mask=layer.masks.add();mask.id='height-mask';mask.source='SCENE';mask.invert=True
mask.black=.4;mask.white=.7
field=mask.influence;field.kind='WORLD_HEIGHT';field.bounds=(0,1);field.resolution='64';field.mode='LIVE'
bpy.context.view_layer.update();scene_fields.update(ts,mask)
source=image_manager.pixels(field.image).copy();before=document.canonical(document.snapshot(ts));images={i.as_pointer() for i in bpy.data.images}
# Source zero background used to cross threshold after filtering despite every
# surface point being above it: false black-sole stripes on a tall rubber shaft.
u=np.array([.2511,.252,.253,.255],np.float32);v=np.full_like(u,.5)
old=sample_uv(source,u,v)[...,0]
new=scene_fields.evaluate(ts,mask,u,v,{})
assert old.min()<.7,old
np.testing.assert_allclose(new,.8,atol=1e-6)
inverted=1-np.clip((new-.4)/(.7-.4),0,1)
assert inverted.max()==0,inverted
coverage=ownership(ts,len(source)).covered
result=padded(ts,source)
np.testing.assert_array_equal(result[coverage],source[coverage])
np.testing.assert_array_equal(image_manager.pixels(field.image),source)
assert document.canonical(document.snapshot(ts))==before
assert {i.as_pointer() for i in bpy.data.images}==images
# CPU evaluation gives the same fixed samples as the shader boundary's producer.
field.mode='FROZEN';freeze=document.canonical(document.snapshot(ts))
np.testing.assert_allclose(scene_fields.evaluate(ts,mask,u,v,{}),new,atol=0)
assert document.canonical(document.snapshot(ts))==freeze
print('DIRECTOR_SCENE_GUTTERS_SOURCE_PRESERVATION_OK',flush=True)
