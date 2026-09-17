"""Bounded offline analysis, explicit face marking and data-only extension API."""
import sys
from pathlib import Path
from unittest.mock import patch
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import open_painter
from open_painter import registration,api
from open_painter.core import texture_set_manager as sets,layer_stack as stack
from open_painter.core.material_inheritance import preview
from open_painter.analysis.offline import strain,mark_faces,contact_history
from open_painter.analysis.analyzer import report
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.utils.ids import new_id
# Partial initialization rolls back all callbacks/classes, without a new layout.
from open_painter.ui import editor
original=editor.register
def fail():
    original(); raise RuntimeError('Injected partial registration failure')
with patch.object(editor,'register',side_effect=fail):
    try:
        open_painter.register(); raise AssertionError('Expected injected failure')
    except RuntimeError as exc:
        assert 'Injected' in str(exc)
assert not registration._registered_classes and not hasattr(bpy.types.Scene,'open_painter')
open_painter.register(); open_painter.register()
assert 'rest_object' in bpy.ops.open_painter.offline_analysis.get_rna_type().properties
assert bpy.ops.open_painter.offline_analysis.get_rna_type().properties['rest_object'].type=='STRING'
scene=bpy.context.scene; obj=bpy.context.active_object
ts=sets.create(scene,'Offline',512); sets.assign(scene,ts,obj)
ts.preview_backend='CPU'
recipe={'name':'Declarative','layers':[dict(name='Base',color=[.2,.3,.4],roughness=.6,metallic=0,scale=8,detail=.1)]}
api.apply_recipe(ts,recipe)
LayerEvaluator(scene).evaluate_texture_set(ts.id)  # Account for explicitly allocated raster buffers.
count=len(ts.layers)
try:
    api.apply_recipe(ts,dict(recipe,script='not executable'))
    raise AssertionError('Executable field accepted')
except ValueError: pass
assert len(ts.layers)==count
rest=np.array([(0,0,0),(1,0,0),(2,0,0)],np.float32)
np.testing.assert_allclose(strain(rest,rest*2,[(0,1),(1,2)]),1)
np.testing.assert_array_equal(strain(rest,rest,[(0,1),(1,2)]),0)
mark_faces(obj,[0,2]); assert obj.data.attributes['open_painter_cut_surface'].data[0].value==1
assert obj.data.attributes['open_painter_cut_surface'].data[1].value==0
# Marking from Edit Mode (where faces are selected) writes the BMesh layer.
bpy.ops.object.mode_set(mode='EDIT'); obj.update_from_editmode()
marked=mark_faces(obj,[1,3]); assert marked is not None and marked.domain=='FACE'
bpy.ops.object.mode_set(mode='OBJECT')
values=[v.value for v in obj.data.attributes['open_painter_cut_surface'].data]
assert values==[0,1,0,1,0,0],values
mark_faces(obj,[5]); assert [v.value for v in obj.data.attributes['open_painter_cut_surface'].data]==[0,0,0,0,0,1]
base={'layers':[{'id':'a','opacity':1.,'paint':'unchanged'}]}
parent={'layers':[{'id':'a','opacity':.5,'paint':'unchanged'}]}
local={'layers':[{'id':'a','opacity':.7,'paint':'artist edit'}]}
merged,conflicts=preview(base,parent,local)
assert merged['layers'][0]['paint']=='artist edit' and merged['layers'][0]['opacity']==.7 and conflicts
layer=stack.add(ts,'FILL'); mask=layer.masks.add(); mask.id=new_id(); mask.source='SCENE'
field=mask.influence; field.kind='CONTACT'; field.distance=.5
bpy.ops.mesh.primitive_uv_sphere_add(segments=8,ring_count=4,radius=.1,location=(0,0,1.05))
part=bpy.context.object; field.object=part
part.keyframe_insert('location',frame=1); part.location.x=3; part.keyframe_insert('location',frame=3)
scene.frame_set(2); saved_frame=scene.frame_current
result=contact_history(ts,field,1,3)
assert result['frames']==[1,2,3] and result['relative_origin_travel']>2
assert scene.frame_current==saved_frame
assert np.any(result['contact_fraction']>0)
# Re-running Sample Contact History replaces the frozen raster instead of stacking fake-user images:
# the last raster loses its fake user (the datablock stays for an undo of the run and is purged on save).
def contact_images(): return sorted(i.name for i in bpy.data.images if i.name.startswith('Offline Contact Duration') and (i.users or i.use_fake_user))
def by_id(identifier): return next(i for i,item in enumerate(ts.layers) if item.id==identifier)
layer_id=layer.id; ts.active_layer=by_id(layer_id); layer.active_mask=len(layer.masks)-1
bpy.ops.open_painter.offline_analysis(action='CONTACT',first_frame=1,last_frame=3,step=1)
first=field.image.name; assert field.mode=='FROZEN' and contact_images()==[first],contact_images()
bpy.ops.open_painter.offline_analysis(action='CONTACT',first_frame=1,last_frame=2,step=1)
assert field.image is not None and field.image.name!=first
assert contact_images()==[field.image.name],contact_images()
assert first in bpy.data.images and not bpy.data.images[first].use_fake_user and bpy.data.images[first].users==0
# Only one superseded raster stays resident per mask: the field remembers the released predecessor
# and a newer run removes the one before it (two undo steps away, bound by nothing).
from open_painter.ui.offline_analysis import SUPERSEDED
second=field.image.name; assert field[SUPERSEDED]==first
bpy.ops.open_painter.offline_analysis(action='CONTACT',first_frame=2,last_frame=3,step=1)
assert first not in bpy.data.images and second in bpy.data.images and field[SUPERSEDED]==second
assert bpy.data.images[second].users==0 and not bpy.data.images[second].use_fake_user
assert contact_images()==[field.image.name],contact_images()
# A raster still referenced elsewhere (a duplicated set's field) survives the swap.
shared=field.image.name
other=stack.add(ts,'FILL'); other_mask=other.masks.add(); other_mask.id=new_id(); other_mask.source='SCENE'
other_mask.influence.image=bpy.data.images[shared]
layer=ts.layers[by_id(layer_id)]; mask=layer.masks[layer.active_mask]; field=mask.influence  # stack.add invalidated the RNA references
ts.active_layer=by_id(layer_id)
bpy.ops.open_painter.offline_analysis(action='CONTACT',first_frame=1,last_frame=3,step=1)
other=next(item for item in ts.layers if item.id==other.id)
assert field.image.name!=shared and other.masks[0].influence.image.name==shared
assert contact_images()==sorted([shared,field.image.name]),contact_images()
# The shared predecessor is not this mask's to remove later, so it is not remembered; the
# superseded raster before it went when the shared one was released.
assert SUPERSEDED not in field and second not in bpy.data.images
# The next run's predecessor (bound by this field alone) is remembered again, and a remembered
# raster something re-adopted meanwhile (a user, or a fake user) is left alone.
bpy.ops.open_painter.offline_analysis(action='CONTACT',first_frame=1,last_frame=2,step=1)
third=field[SUPERSEDED]; assert third in bpy.data.images and bpy.data.images[third].users==0
bpy.data.images[third].use_fake_user=True
bpy.ops.open_painter.offline_analysis(action='CONTACT',first_frame=1,last_frame=3,step=1)
assert third in bpy.data.images and bpy.data.images[third].use_fake_user and field[SUPERSEDED]!=third
bpy.data.images[third].use_fake_user=False
assert report(ts)['estimated_image_buffer_bytes']>0
from open_painter.analysis.lod import create
original_mesh=part.data
lod=create(bpy.context,part,.5)
bpy.context.view_layer.update()
assert lod.data!=original_mesh and part.data==original_mesh
evaluated=lod.evaluated_get(bpy.context.evaluated_depsgraph_get()); mesh=evaluated.to_mesh()
assert 0<len(mesh.polygons)<len(original_mesh.polygons)
evaluated.to_mesh_clear()
open_painter.unregister(); open_painter.unregister()
print('OFFLINE_ANALYSIS_PASS',flush=True)
