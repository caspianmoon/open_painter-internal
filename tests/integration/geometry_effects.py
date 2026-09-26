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
# The triangle estimate follows the resampled arc length, not the control-point count: an
# extracted 1 m seam with 200 points is a few thousand triangles, so it fits the budget with room
# for a second bead, and its record matches a 2-point line of the same length.
dense_data=bpy.data.curves.new('Extracted seam','CURVE'); dense_data.dimensions='3D'
dense_spline=dense_data.splines.new('POLY'); dense_spline.points.add(199)
for index,point in enumerate(dense_spline.points): point.co=(index/199,0,0,1)
dense=bpy.data.objects.new('Dense seam',dense_data); scene.collection.objects.link(dense)
seam=weld.create(scene,dense,radius=.008,ripple=.05,period=8.0)
bpy.context.view_layer.update(); mesh=seam.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()
actual=sum(len(polygon.vertices)-2 for polygon in mesh.polygons)
assert 1000<actual<20000 and .5<seam['estimated_triangles']/actual<2, (seam['estimated_triangles'],actual)
sparse=weld.create(scene,curve,radius=.008,ripple=.05,period=8.0)
assert sparse['estimated_triangles']==seam['estimated_triangles'], (sparse['estimated_triangles'],seam['estimated_triangles'])
assert limits.headroom(scene)[1]>limits.MAX_TRIANGLES-4*actual
manager.remove(sparse); manager.remove(seam); bpy.data.objects.remove(dense); bpy.data.curves.remove(dense_data)
# A scaled, rotated source: instances keep their metric size and stay orthogonal (no shear),
# the helper's world matrix is the identity and it still follows the source afterwards.
from mathutils import Matrix
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 6, 0))
scaled = bpy.context.object; scaled.scale = (3, 1, .5); scaled.rotation_euler = (0, 0, .4)
bpy.context.view_layer.update()
scaled_set = sets.create(scene, 'Scaled', 512); sets.assign(scene, scaled_set, scaled)
scaled_helper = manager.create(scaled_set, scaled, mask, 'SPLINTERS', 40, .05, 7)
assert scaled_helper and scaled_helper['instance_count'] > 0
assert all(abs(a - b) < 1e-5 for ra, rb in zip(scaled_helper.matrix_world, Matrix.Identity(4)) for a, b in zip(ra, rb))
bpy.context.view_layer.update(); graph = bpy.context.evaluated_depsgraph_get()
matrices = [inst.matrix_world.copy() for inst in graph.object_instances if inst.is_instance and inst.parent.original == scaled_helper]
assert len(matrices) == scaled_helper['instance_count']
low, high = manager.KINDS['SPLINTERS']['scale']
for m in matrices:
    extents = m.to_scale()
    assert .05 * low * .75 - 1e-6 <= min(extents) and max(extents) <= .05 * high / .75 + 1e-6, tuple(extents)   # aspect .75..1.3
    axes = [m.col[i].xyz.normalized() for i in range(3)]
    assert all(abs(axes[i].dot(axes[j])) < 1e-3 for i in range(3) for j in range(i + 1, 3)), 'sheared instance'
centres = np.array([m.translation[:] for m in matrices])
pairwise = np.linalg.norm(centres[:, None] - centres[None], axis=-1) + np.eye(len(centres)) * 9
assert pairwise.min() >= manager.KINDS['SPLINTERS']['spacing'] * .05 - 1e-6, 'instances closer than the spacing in world space'
scaled.location.x += 1; bpy.context.view_layer.update()
assert abs(scaled_helper.matrix_world.translation.x - 1) < 1e-6
# Deleting a helper with X (never through remove) leaves prototype data an orphan purge can reap.
prototype_objects = [n.inputs['Object'].default_value for n in scaled_helper.modifiers[0].node_group.nodes if n.bl_idname == 'GeometryNodeObjectInfo']
assert prototype_objects and all(not p.use_fake_user and p.users == 1 for p in prototype_objects)
scaled_owner = scaled_helper['effect_owner']
bpy.data.objects.remove(scaled_helper, do_unlink=True)
bpy.data.orphans_purge(do_recursive=True)
assert not any(item.get('effect_owner') == scaled_owner for collection in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.node_groups) for item in collection), 'X-deleted helper leaked its prototypes'
# Prototypes from files saved while they carried a fake user survive a purge; the next remove() reaps them.
legacy = manager.create(scaled_set, scaled, mask, 'PEEL', 20, .05, 9)
legacy_owner = legacy['effect_owner']
for node in legacy.modifiers[0].node_group.nodes:
    if node.bl_idname == 'GeometryNodeObjectInfo': node.inputs['Object'].default_value.use_fake_user = True
bpy.data.objects.remove(legacy, do_unlink=True)
bpy.data.orphans_purge(do_recursive=True)
assert any(o.get('effect_owner') == legacy_owner for o in bpy.data.objects)
other = manager.create(scaled_set, scaled, mask, 'PEEL', 20, .05, 10)
manager.remove(other)
assert not any(item.get('effect_owner') == legacy_owner for collection in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.node_groups) for item in collection), 'legacy prototypes were not reaped'
# Published mask snapshots: the helper links the image (renames follow), a regenerate reuses it,
# a legacy name record still resolves, and the last helper using it releases it.
from open_painter.core import layer_stack as stack
from open_painter.core.published_outputs import publish_mask
from open_painter.utils.ids import new_id
ts.preview_backend = 'CPU'
layer = stack.add(ts, 'FILL', 'Rusty')
paint = layer.masks.add(); paint.id, paint.source = new_id(), 'PAINT'
paint.image = images.create('Rust spots', 512, (1, 1, 1, 1))
bpy.context.view_layer.update()
published = publish_mask(ts, layer)
rust = manager.create(ts, obj, published, 'RUST', 10, .02, 3)
twin = manager.create(ts, obj, published, 'RUST', 10, .02, 4)
assert rust['mask_image'] == published and published.use_fake_user
published.name = 'rust density'
rust = manager.regenerate(ts, rust)
assert rust is not None and bpy.data.images.get('rust density') == published
twin['mask_image'] = published.name   # helpers from older files hold the name
twin = manager.regenerate(ts, twin)
assert twin is not None and twin['mask_image'] == published
manager.remove(rust)
assert bpy.data.images.get('rust density') == published, 'a snapshot another helper still uses was released'
manager.remove(twin)
assert not any(img.get('open_painter_published_layer') for img in bpy.data.images), 'published mask snapshot leaked after removal'
# Regenerate stays on the faces of the set the helper was generated for, whichever set is active.
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, -6, 0))
split = bpy.context.object
split.data.materials.append(bpy.data.materials.new('Body')); split.data.materials.append(bpy.data.materials.new('Straps'))
for face in split.data.polygons: face.material_index = 0 if face.index < 3 else 1
body = sets.create(scene, 'Body', 512); sets.assign(scene, body, split, 0)
straps = sets.create(scene, 'Straps', 512); sets.assign(scene, straps, split, 1)
def slots_of(helper):
    inverse = split.matrix_world.inverted()
    return {split.data.polygons[split.closest_point_on_mesh(inverse @ (helper.matrix_world @ v.co))[3]].material_index for v in helper.data.vertices}
body_helper = manager.create(body, split, mask, 'BOLT', 20, .05, 2)
assert body_helper and slots_of(body_helper) == {0}, slots_of(body_helper)
# The sidebar panel draws for any active object and offers Regenerate / Freeze for every
# helper kind the operator can regenerate (not only the five original ones); weld has no path.
from open_painter.ui.geometry_effects import OP_PT_geometry_effects
class Layout:
    def __init__(self, calls): self.calls = calls
    def row(self, **_): return self
    def column(self, **_): return self
    def box(self): return self
    def panel(self, *_a, **_k): return self, self
    def menu(self, *_a, **_k): pass
    def label(self, **_): pass
    def prop(self, data, path, **_): self.calls.append(('prop', path))
    def operator(self, idname, text='', **_):
        self.calls.append(('operator', text)); return type('Props', (), {'action': None, 'name': None})()
def panel_actions(active):
    calls = []
    OP_PT_geometry_effects.draw(type('Panel', (), {'layout': Layout(calls)})(), type('Context', (), {'active_object': active})())
    return {text for kind, text in calls if kind == 'operator'}, {path for kind, path in calls if kind == 'prop'}
assert 'Regenerate' not in panel_actions(None)[0] and 'Regenerate' not in panel_actions(split)[0]
for regenerable in (body_helper, manager.create(ts, obj, mask, 'RUST', 10, .02, 5)):
    actions, props = panel_actions(regenerable)
    assert {'Regenerate', 'Freeze', 'Remove Effect'} <= actions and 'Unfreeze' not in actions, (regenerable['kind'], actions)
    assert props == {'["follow"]', '["density"]', '["size"]', '["seed"]', '["lift"]', '["match"]'}, props
    regenerable['frozen'] = True
    assert 'Unfreeze' in panel_actions(regenerable)[0]
    if regenerable is not body_helper: manager.remove(regenerable)
body_helper['frozen'] = False
bead = weld.create(scene, curve, radius=.02)
actions, props = panel_actions(bead)
assert 'Remove Effect' in actions and not ({'Regenerate', 'Freeze'} & actions) and not props, (actions, props)
manager.remove(bead)
body_helper = manager.regenerate(straps, body_helper)
assert body_helper['source_texture_set'] == body.id and slots_of(body_helper) == {0}, slots_of(body_helper)
body_helper['source_texture_set'] = 'gone'
try:
    manager.regenerate(straps, body_helper)
    raise AssertionError('regenerated against a set that no longer exists')
except ValueError as error:
    assert 'no longer exists' in str(error), error
body_helper['source_texture_set'] = body.id
manager.remove(body_helper)
# An over-limit scatter takes what the project can still hold, without walking every candidate;
# once the budget is used up the next one is refused.
import time
bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 30, 0))   # 400 m² at the moss preset: 1.2M candidates
slab = bpy.context.object
slab_set = sets.create(scene, 'Slab', 512); sets.assign(scene, slab_set, slab)
started = time.perf_counter()
capped = manager.create(slab_set, slab, mask, 'MOSS', 3000, .08, 1)
elapsed = time.perf_counter() - started
assert capped is not None and capped['capped'], 'an over-limit scatter was not capped'
instances_left, triangles_left = limits.headroom(scene)
assert min(instances_left, triangles_left // manager.KINDS['MOSS']['triangles']) == 0, (instances_left, triangles_left)
assert elapsed < 8, f'capping an over-limit scatter stalled for {elapsed:.1f} s'
try:
    manager.create(slab_set, slab, mask, 'MOSS', 3000, .08, 2)
    raise AssertionError('project limits ignored once the budget is used up')
except ValueError as error:
    assert 'limit' in str(error), error
manager.remove(capped)
keep, was_capped = manager._thin(np.random.default_rng(0).random((5000, 3)), .01, np.random.default_rng(1), limit=20)
assert keep.sum() == 20 and was_capped
open_painter.unregister()
assert obj.name in bpy.data.objects and curve.name in bpy.data.objects
print('GEOMETRY_EFFECTS_PASS',flush=True)
