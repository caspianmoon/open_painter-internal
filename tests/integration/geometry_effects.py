"""Bounded deterministic helpers preserve original topology and owned cleanup."""
import sys
from pathlib import Path
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, image_manager as images
from open_painter.geometry_effects import manager,limits,weld
open_painter.register()
scene=bpy.context.scene; obj=bpy.context.active_object
ts=sets.create(scene,'Flakes',512); sets.assign(scene,ts,obj)
mask=images.create('Density',64,(1,1,1,1))
vertices=tuple(v.co[:] for v in obj.data.vertices)
assert manager.create(ts,obj,mask,density=0) is None
first,normals=manager.scatter(ts,obj,mask,10,31)
second,_=manager.scatter(ts,obj,mask,10,31)
np.testing.assert_array_equal(first,second)
helper=manager.create(ts,obj,mask,'RUST',10,.02,31)
assert helper.parent==obj and helper['instance_count']==len(first)
bpy.context.view_layer.update()
graph=bpy.context.evaluated_depsgraph_get()
instances=[instance for instance in graph.object_instances if instance.is_instance and instance.parent.original==helper]
assert len(instances)==len(first), f'Expected actual instances: {len(instances)} versus {len(first)}'
helper_id=helper['effect_owner']
obj.location.x+=2; bpy.context.view_layer.update()
assert helper.matrix_world.translation.x==obj.matrix_world.translation.x
assert tuple(v.co[:] for v in obj.data.vertices)==vertices
try:
    limits.check(scene,limits.MAX_INSTANCES,limits.MAX_TRIANGLES)
    raise AssertionError('Project limits ignored')
except ValueError: pass
helper['frozen']=True
try:
    manager.regenerate(ts,helper)
    raise AssertionError('Frozen helper regenerated')
except ValueError: pass
helper['frozen']=False
replacement=manager.regenerate(ts,helper)
assert replacement and replacement['effect_owner']!=helper_id
assert not any(item.get('effect_owner')==helper_id for item in bpy.data.objects)
manager.remove(replacement)
assert obj.name in scene.objects and tuple(v.co[:] for v in obj.data.vertices)==vertices
moss=manager.create(ts,obj,mask,'MOSS',5,.02,31)
assert moss and moss['instance_count']>0
manager.remove(moss)
for kind in ('SNOW','SPLINTERS','PEEL'):
    effect=manager.create(ts,obj,mask,kind,2,.02,31)
    bpy.context.view_layer.update()
    graph=bpy.context.evaluated_depsgraph_get()
    assert sum(1 for inst in graph.object_instances if inst.is_instance and inst.parent.original==effect)==effect['instance_count']
    manager.remove(effect)
# Editable curve weld creates real evaluated mesh, while source remains a curve.
curve_data=bpy.data.curves.new('Authored weld','CURVE'); curve_data.dimensions='3D'
spline=curve_data.splines.new('POLY'); spline.points.add(1)
spline.points[0].co=(0,0,0,1); spline.points[1].co=(1,0,0,1)
curve=bpy.data.objects.new('Weld path',curve_data); scene.collection.objects.link(curve)
tube=weld.create(scene,curve)
bpy.context.view_layer.update(); evaluated=tube.evaluated_get(bpy.context.evaluated_depsgraph_get()); mesh=evaluated.to_mesh()
assert len(mesh.polygons)>0; evaluated.to_mesh_clear()
manager.remove(tube)
assert curve.type=='CURVE' and len(curve.data.splines[0].points)==2
masks=weld.coating_depth(np.array([0,.4,.9],np.float32))
np.testing.assert_array_equal(sum(masks),1)
open_painter.unregister()
assert obj.name in bpy.data.objects and curve.name in bpy.data.objects
print('GEOMETRY_EFFECTS_PASS',flush=True)
