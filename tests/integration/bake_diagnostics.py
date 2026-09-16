"""Provenance, diagnostics and cancellation preserve authoring geometry."""
import sys
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, image_manager as images
from open_painter.baking.diagnostics import inspect
from open_painter.baking.bake_manager import register_map, bake_steps
from open_painter.geometry.provenance import record, status
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
assert status(ts,prop)=='Ready', 'moving the object does not change a curvature map'
obj.scale=(2,2,2); bpy.context.view_layer.update()
assert status(ts,prop).startswith('Stale'), 'resizing it does'
obj.scale=(1,1,1); obj.location.x-=1; bpy.context.view_layer.update()
assert status(ts,prop)=='Ready'
ao_image=images.create('Recorded AO',512,(1,1,1,1))
register_map(ts,'AO',ao_image)
ao_prop=next(m for m in ts.baked_maps if m.kind=='AO')
obj.location.x+=1; bpy.context.view_layer.update()
assert status(ts,ao_prop).startswith('Stale'), 'occlusion reads where the parts stand'
obj.location.x-=1; bpy.context.view_layer.update()
assert status(ts,ao_prop)=='Ready'
# Maps recorded before placement-aware records (every transform in full) stay current.
ao_prop.provenance=record(ts,'AO',legacy=True); prop.provenance=record(ts,'CURVATURE',legacy=True)
assert status(ts,prop)=='Ready' and status(ts,ao_prop)=='Ready'
ts.baked_maps.remove(next(i for i,m in enumerate(ts.baked_maps) if m.kind=='AO'))
prop=ts.baked_maps[0]
verts=tuple(v.co[:] for v in obj.data.vertices)
job=bake_steps(bpy.context,ts)
assert next(job)=='CURVATURE'
job.close()
assert prop.image==image and tuple(v.co[:] for v in obj.data.vertices)==verts
# Reject changes made while paused before the bake, without replacing a good map.
job=bake_steps(bpy.context,ts); next(job); obj.scale.z*=2; bpy.context.view_layer.update()
try:
    next(job)
    raise AssertionError('Stale inputs accepted')
except ValueError as exc:
    assert 'inputs changed' in str(exc)
assert prop.image==image
obj.scale.z/=2; bpy.context.view_layer.update()
# Exact UV overlap: the default cube's cross layout has none. A second object
# sharing the texture set, with copied UVs, stacks every island (deliberate on
# symmetric assets), one mirrored face is counted, and a shifted face overlaps
# partially. Faces map back to base meshes; selection is the only change.
from open_painter.baking import diagnostics
import numpy as np
overlap=lambda issues:[i for i in issues if 'overlap' in i.message.lower() or 'Stacked' in i.message]
assert not overlap(inspect(ts,evaluated=True))
bpy.ops.object.select_all(action='DESELECT')
obj.select_set(True); bpy.context.view_layer.objects.active=obj
bpy.ops.object.duplicate(linked=False)
twin=bpy.context.active_object
twin.name='Twin'; twin.location.x+=3
sets.assign(scene,ts,twin)
twin_uv=twin.data.uv_layers.active
loops=twin.data.polygons[1].loop_indices
for index in loops:  # mirror one face in U about its own centre
    u,v=twin_uv.data[index].uv; twin_uv.data[index].uv=(u,v)
centre=np.mean([twin_uv.data[i].uv[0] for i in loops])
for index in loops:
    u,v=twin_uv.data[index].uv; twin_uv.data[index].uv=(2*centre-u,v)
for index in twin.data.polygons[2].loop_indices:  # partial overlap: shift by a third of a face
    u,v=twin_uv.data[index].uv; twin_uv.data[index].uv=(u+.08,v)
before_uv={o.name:[tuple(d.uv) for d in o.data.uv_layers.active.data] for o in (obj,twin)}
issues=diagnostics.run(ts)
assert diagnostics.last(ts)==issues
stacked=[i for i in issues if i.message.startswith('Stacked')]
partial=[i for i in issues if i.message.startswith('Partially')]
assert {i.object_name for i in stacked}=={obj.name,'Twin'} and all('also overlaps' in i.message for i in stacked)
assert all('1 mirrored face(s)' in i.message for i in stacked), [i.message for i in stacked]
assert all(i.severity=='INFO' for i in stacked) and all(i.severity=='WARNING' for i in partial)
twin_stacked=next(i for i in stacked if i.object_name=='Twin')
assert 2 not in twin_stacked.faces and 1 in twin_stacked.faces and len(twin_stacked.faces)==5
twin_partial=next(i for i in partial if i.object_name=='Twin')
assert 2 in twin_partial.faces
assert {o.name:[tuple(d.uv) for d in o.data.uv_layers.active.data] for o in (obj,twin)}==before_uv
counts,skipped=diagnostics.select_faces(ts,issues)
assert counts=={obj.name:6,'Twin':6} and not skipped, (counts,skipped)
assert [p.select for p in twin.data.polygons]==[True]*6
# Selecting only the partial finding leaves the other faces unselected.
assert diagnostics.select_faces(ts,partial)[0]=={'Twin':1,obj.name:1}
assert [p.select for p in twin.data.polygons]==[False,False,True,False,False,False]
# Topology-changing modifiers make evaluated face indices unmappable: reported, not guessed.
modifier=twin.modifiers.new('Subdivide','SUBSURF'); modifier.levels=1
issues=diagnostics.run(ts)
counts,skipped=diagnostics.select_faces(ts,issues)
assert 'Twin' in skipped and obj.name in counts
twin.modifiers.remove(modifier)
# Enum-flag modifier settings (Decimate's delimit is a set) record as sorted lists: the
# provenance JSON and the geometry key must accept them.
from open_painter.geometry.provenance import parameters
from open_painter.evaluation.dependencies import modifier_signature
decimate=twin.modifiers.new('Decimate','DECIMATE'); decimate.decimate_type='DISSOLVE'; decimate.delimit={'NORMAL','SEAM'}
assert parameters(decimate)['delimit']==['NORMAL','SEAM']
assert '"delimit":["NORMAL","SEAM"]' in record(ts,'NORMAL')
assert hash(modifier_signature(twin)) is not None
twin.modifiers.remove(decimate)
# Native operator path: inspection reports, selection selects, results retained for the panel.
bpy.context.view_layer.objects.active=obj
assert bpy.ops.open_painter.bake(action='INSPECT')=={'FINISHED'}
assert diagnostics.last(ts)
assert bpy.ops.open_painter.bake(action='SELECT_ISSUES')=={'FINISHED'}
sets.unassign(ts,next(i for i,ref in enumerate(ts.assignments) if ref.object==twin))
bpy.data.objects.remove(twin,do_unlink=True)
diagnostics.forget(ts)
assert diagnostics.last(ts)==()
# Sampled cage-ray coverage: an enclosing high mesh is hit everywhere; a high
# plane over the top face only leaves misses on the other five faces; a custom
# cage with matching topology casts from the cage instead. Reported as a
# sampled fraction with faces, never as exact coverage.
def add_high(name, scale=(1.02,1.02,1.02), location=(0,0,0), plane=False):
    location=tuple(obj.matrix_world.translation)+np.asarray(location)  # the cube moved earlier in this test
    (bpy.ops.mesh.primitive_plane_add if plane else bpy.ops.mesh.primitive_cube_add)(size=2, location=location)
    high=bpy.context.active_object; high.name=name; high.scale=scale
    bpy.context.view_layer.update()
    ref=ts.bake.high_objects.add(); ref.object=high; ref.name=high.name
    return high
ts.bake.selected_to_active=True; ts.bake.normal=True; ts.bake.cage_mode='AUTO'; ts.bake.ray_distance=.1
shell=add_high('Shell')
coverage=diagnostics.ray_coverage(ts, samples=4000)
assert len(coverage)==1 and coverage[0].severity=='INFO' and coverage[0].faces==() and 'sampled estimate' in coverage[0].message, coverage
assert coverage[0].message.startswith('0 of 4000')
ts.bake.ray_distance=.01  # shell at 0.02 lies beyond the maximum ray distance from a 0.01 extrusion
short=diagnostics.ray_coverage(ts, samples=4000)
assert short[0].severity=='WARNING' and short[0].message.startswith('4000 of 4000') and len(short[0].faces)==6
ts.bake.ray_distance=0  # zero means unlimited
assert diagnostics.ray_coverage(ts, samples=4000)[0].message.startswith('0 of 4000')
ts.bake.ray_distance=.1
ts.bake.high_objects.clear(); bpy.data.objects.remove(shell, do_unlink=True)
lid=add_high('Lid', scale=(1.5,1.5,1), location=(0,0,1.05), plane=True)
partial_cover=diagnostics.ray_coverage(ts, samples=6000)[0]
top=next(p.index for p in obj.data.polygons if p.normal.z>.9)
assert partial_cover.severity=='WARNING' and top not in partial_cover.faces and len(partial_cover.faces)==5, partial_cover
missed=int(partial_cover.message.split(' of ')[0])
assert 4500 < missed < 5500, missed  # about five of six faces by area
# Findings from run() include the sampled tier and remain selectable.
issues=diagnostics.run(ts)
assert any('sampled estimate' in i.message for i in issues)
counts,skipped=diagnostics.select_faces(ts,[partial_cover])
assert counts=={obj.name:5} and not skipped and not obj.data.polygons[top].select
# Custom cage: a scaled copy of the low mesh, rays start on the cage.
bpy.ops.object.select_all(action='DESELECT'); obj.select_set(True); bpy.context.view_layer.objects.active=obj
bpy.ops.object.duplicate(linked=False)
cage=bpy.context.active_object; cage.name='Cage'; cage.scale=(1.2,1.2,1.2); bpy.context.view_layer.update()
ts.bake.cage_mode='CUSTOM'; ts.bake.cage_object=cage; ts.bake.ray_distance=0
unlimited=diagnostics.ray_coverage(ts, samples=6000)[0]
bottom=next(p.index for p in obj.data.polygons if p.normal.z<-.9)
# Unlimited rays from the bottom cage face pass through the cube and reach the lid.
assert 'custom cage' in unlimited.message and bottom not in unlimited.faces and top not in unlimited.faces and len(unlimited.faces)==4, unlimited
ts.bake.ray_distance=.5
caged=diagnostics.ray_coverage(ts, samples=6000)[0]
assert len(caged.faces)==5 and top not in caged.faces, caged
ts.bake.cage_mode='AUTO'; ts.bake.cage_object=None; ts.bake.selected_to_active=False; ts.bake.normal=False
ts.bake.high_objects.clear()
for extra in (lid, cage):
    bpy.data.objects.remove(extra, do_unlink=True)
bpy.context.view_layer.objects.active=obj
# Evaluated diagnostics return face identities and never alter UVs.
uv=obj.data.uv_layers.active
for index in obj.data.polygons[0].loop_indices:
    uv.data[index].uv=(2,2)
issues=inspect(ts,evaluated=True)
assert any(0 in i.faces and 'Degenerate' in i.message for i in issues)
assert any(0 in i.faces and 'outside' in i.message for i in issues)
# A face moved into tile 1002 is enumerated by tile number; degenerate faces are excluded from the count.
for index in obj.data.polygons[1].loop_indices:
    uv.data[index].uv=(uv.data[index].uv[0]+1, uv.data[index].uv[1])
issues=inspect(ts,evaluated=True)
spread=next(i for i in issues if 'outside' in i.message)
assert 'UDIM tile(s) 1002' in spread.message and 1 in spread.faces, spread.message
from open_painter.geometry.udim import layout
tiles,invalid,outside=layout(ts)[obj.name]
assert tiles==[1001,1002] and outside==[1] and invalid==[] , (tiles,invalid,outside)  # face 0 is degenerate
open_painter.unregister()
print('BAKE_DIAGNOSTICS_PASS',flush=True)
