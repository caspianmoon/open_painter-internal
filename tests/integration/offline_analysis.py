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
recipe={'name':'Declarative','layers':[dict(name='Base',color=[.2,.3,.4],roughness=.6,metallic=0,scale=8,detail=.1)]}
api.apply_recipe(ts,recipe)
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
