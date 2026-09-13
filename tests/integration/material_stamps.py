"""UV seam coverage, material-slot protection and coordinated channel writes."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets,layer_stack as stack,image_manager as images
from open_painter.painting.material_stamp import apply,coverage
open_painter.register()
scene=bpy.context.scene; obj=bpy.context.active_object
ts=sets.create(scene,'Seam stamp',512); sets.assign(scene,ts,obj)
layer=stack.add(ts,'PAINT','Coordinated stamp')
center=(1,0,1); normal=(.70710678,0,.70710678)
mask=coverage(ts,obj,center,normal,.4,.5)
assert mask.max()<=.5 and np.count_nonzero(mask)>50
from open_painter.geometry.snapshots import targets
from open_painter.geometry.uv_rasterizer import tiles
with targets(ts) as entries:
    hit_faces=set()
    for triangle in entries[0]['triangles']:
        for region,inside,_,_,_ in tiles(entries[0]['uv'][list(triangle.loops)],512):
            if np.any(mask[region][inside]>.01): hit_faces.add(triangle.polygon_index)
assert len(hit_faces)==2,hit_faces
values=dict(BASE_COLOR=(.6,.2,.1),ROUGHNESS=.8,METALLIC=.7,HEIGHT=.55)
assert apply(ts,layer,obj,center,normal,.4,values,.5)
for key,value in values.items():
    data=images.pixels(stack.channel_for(layer,key).image)
    np.testing.assert_array_equal(data[...,3],mask)
    np.testing.assert_allclose(data[...,:3],mask[...,None]*np.broadcast_to(value,(3,)),atol=1e-7)
    assert np.all(data[mask==0]==0)
other=bpy.data.materials.new('Protected'); obj.data.materials.append(other)
protected=next(iter(hit_faces)); obj.data.polygons[protected].material_index=1
bpy.context.view_layer.update()
protected_mask=coverage(ts,obj,center,normal,.4)
assert np.count_nonzero(protected_mask)<np.count_nonzero(mask)
with TemporaryDirectory(prefix='op-stamp-save-') as directory:
    identifier=ts.id
    bpy.ops.wm.save_as_mainfile(filepath=directory+'/stamp.blend')
    bpy.ops.wm.open_mainfile(filepath=directory+'/stamp.blend')
    ts=next(t for t in bpy.context.scene.open_painter.texture_sets if t.id==identifier)
    for key in values:
        data=images.pixels(stack.channel_for(ts.layers[0],key).image)
        np.testing.assert_array_equal(data[...,3],mask)
open_painter.unregister()
print('MATERIAL_STAMPS_PASS',flush=True)
