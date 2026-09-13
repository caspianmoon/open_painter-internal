"""Provenance, diagnostics and cancellation preserve authoring geometry."""
import sys
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, image_manager as images
from open_painter.baking.diagnostics import inspect
from open_painter.baking.bake_manager import register_map, bake_steps
from open_painter.geometry.provenance import status
open_painter.register()
scene=bpy.context.scene
obj=bpy.context.active_object
ts=sets.create(scene,'Bake provenance',512)
sets.assign(scene,ts,obj)
ts.bake.selected_to_active=False; ts.bake.normal=False; ts.bake.ao=False
assert not [i for i in inspect(ts, evaluated=True) if i.severity=='ERROR']
image=images.create('Recorded map',512,(1,1,1,1))
register_map(ts,'CURVATURE',image)
prop=ts.baked_maps[0]
assert status(ts,prop)=='Ready'
ts.name='Material-only rename'
assert status(ts,prop)=='Ready'
obj.location.x+=1; bpy.context.view_layer.update()
assert status(ts,prop).startswith('Stale')
obj.location.x-=1; bpy.context.view_layer.update()
assert status(ts,prop)=='Ready'
verts=tuple(v.co[:] for v in obj.data.vertices)
job=bake_steps(bpy.context,ts)
assert next(job)=='CURVATURE'
job.close()
assert prop.image==image and tuple(v.co[:] for v in obj.data.vertices)==verts
# Reject changes made while paused before the bake, without replacing a good map.
job=bake_steps(bpy.context,ts); next(job); obj.location.z+=1; bpy.context.view_layer.update()
try:
    next(job)
    raise AssertionError('Stale inputs accepted')
except ValueError as exc:
    assert 'inputs changed' in str(exc)
assert prop.image==image
# Evaluated diagnostics return face identities and never alter UVs.
uv=obj.data.uv_layers.active
for index in obj.data.polygons[0].loop_indices:
    uv.data[index].uv=(2,2)
issues=inspect(ts,evaluated=True)
assert any(0 in i.faces and 'Degenerate' in i.message for i in issues)
assert any(0 in i.faces and 'outside' in i.message for i in issues)
open_painter.unregister()
print('BAKE_DIAGNOSTICS_PASS',flush=True)
