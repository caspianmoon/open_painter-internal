"""UV seam coverage, material-slot protection and coordinated channel writes."""
import sys,time
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
# Culling walks the Target arrays, not one RNA triangle per face: a small stamp
# on a dense asset rasterises only the triangles inside its bounding box.
from open_painter.painting import material_stamp
from open_painter.geometry import snapshots
scene=bpy.context.scene
bpy.ops.mesh.primitive_uv_sphere_add(segments=320,ring_count=320)
dense=bpy.context.active_object
dense_set=sets.create(scene,'Dense stamp',512); sets.assign(scene,dense_set,dense)
with targets(dense_set) as entries: total=len(entries[0]['tri_vertices'])
assert total>150000,total
rasterised=[]
def counting_tiles(uv,n,*args,**kwargs):
    rasterised.append(uv); return tiles(uv,n,*args,**kwargs)
def forbid_rna(self,key):
    raise AssertionError(f'stamp materialised {key!r} through RNA')
material_stamp.tiles=counting_tiles; snapshots.Target.__missing__=forbid_rna
try:
    started=time.perf_counter()
    dense_mask=material_stamp.coverage(dense_set,dense,(0,0,1),(0,0,1),.02)
    elapsed=time.perf_counter()-started
finally:
    material_stamp.tiles=tiles; del snapshots.Target.__missing__
assert np.count_nonzero(dense_mask)>0
assert len(rasterised)<total*.01,(len(rasterised),total)
print(f'dense stamp: {len(rasterised)} of {total} triangles rasterised in {elapsed:.2f}s',flush=True)
# Occlusion still sees every evaluated triangle: an unassigned slot in front of
# the assigned surface blocks the stamp instead of letting it paint through.
shells=bpy.data.meshes.new('Shells')
shells.from_pydata([(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0),(-1,-1,.05),(1,-1,.05),(1,1,.05),(-1,1,.05)],[],[(0,1,2,3),(4,5,6,7)])
shells.materials.append(bpy.data.materials.new('Painted')); shells.materials.append(bpy.data.materials.new('Cover'))
shells.polygons[1].material_index=1
shells.uv_layers.new(name='UVMap')
for loop in shells.polygons[0].loop_indices: shells.uv_layers[0].data[loop].uv=[(0,0),(.5,0),(.5,.5),(0,.5)][loop%4]
shell_obj=bpy.data.objects.new('Shells',shells); scene.collection.objects.link(shell_obj)
shell_set=sets.create(scene,'Shell stamp',512); sets.assign(scene,shell_set,shell_obj)
assert shell_set.assignments[0].slot_index==0
bpy.context.view_layer.update()
covered=material_stamp.coverage(shell_set,shell_obj,(0,0,.05),(0,0,1),.4)
assert not np.any(covered),np.count_nonzero(covered)
shells.polygons[1].material_index=0; shells.update()
bpy.context.view_layer.update()
uncovered=material_stamp.coverage(shell_set,shell_obj,(0,0,.05),(0,0,1),.4)
assert np.count_nonzero(uncovered)>0
open_painter.unregister()
print('MATERIAL_STAMPS_PASS',flush=True)
