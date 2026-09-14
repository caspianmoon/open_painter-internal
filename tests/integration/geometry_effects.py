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
first,normals=manager.scatter(ts,obj,mask,10,31,kind='RUST',size=.02)
second,_=manager.scatter(ts,obj,mask,10,31,kind='RUST',size=.02)
np.testing.assert_array_equal(first,second)
helper=manager.create(ts,obj,mask,'RUST',10,.02,31)
assert helper.parent==obj and helper['instance_count']==len(first)
bpy.context.view_layer.update()
graph=bpy.context.evaluated_depsgraph_get()
instances=[(instance.object.original.name,instance.matrix_world.copy()) for instance in graph.object_instances if instance.is_instance and instance.parent.original==helper]
assert len(instances)==len(first), f'Expected actual instances: {len(instances)} versus {len(first)}'
# Several jagged flake variants, each with its own rotation, scale and random value.
group=helper.modifiers[0].node_group
prototypes=[n.inputs['Object'].default_value for n in group.nodes if n.bl_idname=='GeometryNodeObjectInfo']
assert helper['variants']==6 and len(prototypes)==6 and len({p.name for p in prototypes})==6
points=helper.data
used={a.value for a in points.attributes['op_variant'].data}
assert len(used)>=3 and used<=set(range(6)), used
assert {a.name for a in points.attributes}>={'op_rotation','op_scale','op_variant','op_random'}
scales=np.array([a.vector[:] for a in points.attributes['op_scale'].data])
low,high=manager.KINDS['RUST']['scale']
assert scales.min()>=.02*low/1.3-1e-6 and scales.max()<=.02*high*1.3+1e-6 and np.ptp(scales[:,2])>.003
coords=np.array([v.co[:] for v in points.vertices])
if len(coords)>1:
    pairwise=np.linalg.norm(coords[:,None]-coords[None],axis=-1)+np.eye(len(coords))*9
    assert pairwise.min()>=manager.KINDS['RUST']['spacing']*.02-1e-6, 'Flakes overlap closer than the spacing'
prototype=prototypes[0]
assert prototype.data.materials[0].name.startswith('OP Rust Flake') and prototype.data.materials[0].get(manager.TAG)=='OWNED_RESOURCE'
assert len(prototype.data.polygons)>=30, 'Flake prototype is still a box'
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
assert not [m for m in bpy.data.materials if m.get('effect_owner')==helper_id], 'Owned material leaked'
# Moss instances the twelve CC0 Poly Haven clump cards with their textures.
moss=manager.create(ts,obj,mask,'MOSS',5,.02,31)
assert moss and moss['instance_count']>0 and moss['variants']==8  # eight clusters of ten to fourteen scanned cards
bpy.context.view_layer.update(); graph=bpy.context.evaluated_depsgraph_get()
clump=next(n.inputs['Object'].default_value for n in moss.modifiers[0].node_group.nodes if n.bl_idname=='GeometryNodeObjectInfo')
assert clump.data.uv_layers and clump.data.materials[0].name.startswith('OP Moss')
moss_images={n.image.get('open_painter_geometry_texture') for n in clump.data.materials[0].node_tree.nodes if n.bl_idname=='ShaderNodeTexImage'}
assert moss_images=={'moss_01_diff_1k.jpg','moss_01_alpha_1k.png','moss_01_nor_gl_1k.png','moss_01_rough_1k.png'}, moss_images
assert all(bpy.data.images[i].packed_file for i in (img.name for img in bpy.data.images if img.get('open_painter_geometry_texture')))
manager.remove(moss)
assert not any(img.get('open_painter_geometry_texture') for img in bpy.data.images), 'Moss textures leaked after removal'
for kind in ('SNOW','SPLINTERS','PEEL'):
    effect=manager.create(ts,obj,mask,kind,2,.02,31)
    bpy.context.view_layer.update()
    graph=bpy.context.evaluated_depsgraph_get()
    assert sum(1 for inst in graph.object_instances if inst.is_instance and inst.parent.original==effect)==effect['instance_count']
    if kind=='SNOW':
        # Snow settles on faces that look up: the cube's top only.
        z=np.array([v.co.z for v in effect.data.vertices])
        assert len(z) and np.all(z>.99), z.min()
    manager.remove(effect)
# Editable curve weld creates real evaluated mesh, while source remains a curve.
curve_data=bpy.data.curves.new('Authored weld','CURVE'); curve_data.dimensions='3D'
spline=curve_data.splines.new('POLY'); spline.points.add(1)
spline.points[0].co=(0,0,0,1); spline.points[1].co=(1,0,0,1)
curve=bpy.data.objects.new('Weld path',curve_data); scene.collection.objects.link(curve)
tube=weld.create(scene,curve,radius=.02)
bpy.context.view_layer.update(); evaluated=tube.evaluated_get(bpy.context.evaluated_depsgraph_get()); mesh=evaluated.to_mesh()
assert len(mesh.polygons)>0
# A rippled, flattened bead: height varies along the seam and stays below a full circle.
bead=np.array([v.co[:] for v in mesh.vertices])
tops=[bead[np.abs(bead[:,0]-x)<.004][:,2].max() for x in np.linspace(.1,.9,30)]
assert np.ptp(tops)>.02*.08, np.ptp(tops)   # stack-of-dimes ripple along the length
assert max(tops)<.02*.9 and bead[:,2].min()<0   # flattened profile sunk into the surface
assert mesh.materials[0].name.startswith('OP Weld Bead') and mesh.polygons[0].use_smooth
evaluated.to_mesh_clear()
weld_owner=tube['effect_owner']
manager.remove(tube)
assert not [m for m in bpy.data.materials if m.get('effect_owner')==weld_owner]
assert curve.type=='CURVE' and len(curve.data.splines[0].points)==2
masks=weld.coating_depth(np.array([0,.4,.9],np.float32))
np.testing.assert_array_equal(sum(masks),1)
open_painter.unregister()
assert obj.name in bpy.data.objects and curve.name in bpy.data.objects
print('GEOMETRY_EFFECTS_PASS',flush=True)
