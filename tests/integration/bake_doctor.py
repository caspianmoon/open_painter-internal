"""Bake Doctor (0.9.1): every check finds its planted defect and selects the offending geometry."""
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.baking import doctor
from open_painter.core import texture_set_manager as sets


def findings(report, key):
    return [f for f in report.findings if f.check == key]


def make_mesh(name, size=1.0, subdivisions=0, uv_box=(0., 0., 1., 1.), torus=False):
    """A cube (or torus) with smart-projected UVs packed into ``uv_box`` (u0, v0, u1, v1)."""
    if torus:
        bpy.ops.mesh.primitive_torus_add(major_radius=size, minor_radius=size * .4, major_segments=24, minor_segments=12)
    else:
        bpy.ops.mesh.primitive_cube_add(size=size)
    obj = bpy.context.object
    obj.name = name
    if subdivisions:
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.subdivide(number_cuts=subdivisions)
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(island_margin=.02)
    bpy.ops.object.mode_set(mode='OBJECT')
    uv = obj.data.uv_layers.active
    coords = np.empty((len(obj.data.loops), 2), np.float32)
    uv.data.foreach_get('uv', coords.ravel())
    u0, v0, u1, v1 = uv_box
    coords[:, 0] = u0 + coords[:, 0] * (u1 - u0)
    coords[:, 1] = v0 + coords[:, 1] * (v1 - v0)
    uv.data.foreach_set('uv', coords.ravel())
    return obj


open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

low = make_mesh('Body_low', 2.0, 1, (0., 0., .7, .7))
high = make_mesh('Body_high', 2.0, 3)
near = make_mesh('Body_Hi', 2.0, 1)
bolt_low = make_mesh('Bolt_low', .5, 0, (.75, .75, 1., 1.))
ring_low = make_mesh('Ring_low', 1., 0, (.75, 0., 1., .7), torus=True)
ring_low.location.y = 4
bolt_low.location.x = 3
bolt_low.scale = (1.5, 1.5, 1.5)
ts = sets.create(scene, 'Doctor', 512)
sets.assign(scene, ts, low)
sets.assign(scene, ts, bolt_low)
sets.assign(scene, ts, ring_low)
for obj in (high, near):
    ref = ts.bake.high_objects.add()
    ref.object, ref.name = obj, obj.name
ts.bake.selected_to_active = True
ts.bake.source_mode = 'ALL'
ts.bake.cage_mode = 'AUTO'
ts.bake.ray_distance = .1

# A clean setup passes every exact check (the near-miss source is the one planted naming warning).
clean = doctor.examine(ts, 'PRE_BAKE')
assert clean.status('uvs') == 'PASS' and clean.status('normals') == 'PASS' and clean.status('doubles') == 'PASS', clean.summary()
assert clean.status('cage') == 'PASS' and clean.status('modifiers') == 'PASS', clean.summary()
assert clean.status('coverage') == 'SKIP'
assert clean.status('scale') == 'WARN' and 'Bolt_low' in [f.object_name for f in findings(clean, 'scale')]
naming = findings(clean, 'naming')
assert any(f.object_name == 'Body_Hi' and "'_Hi'" in f.message for f in naming), naming
assert all(f.severity != 'ERROR' for f in clean.findings), clean.findings
print('BAKE_DOCTOR_CLEAN', clean.summary(), flush=True)

# Plant defects.
bm = bmesh.new(); bm.from_mesh(low.data); bm.faces.ensure_lookup_table()
flipped_faces = [bm.faces[i] for i in (0, 5)]
bmesh.ops.reverse_faces(bm, faces=flipped_faces)
bm.to_mesh(low.data); bm.free()
bm = bmesh.new(); bm.from_mesh(high.data)
bmesh.ops.reverse_faces(bm, faces=bm.faces)          # inside-out source
bm.to_mesh(high.data); bm.free()
bm = bmesh.new(); bm.from_mesh(bolt_low.data); bm.verts.ensure_lookup_table()
duplicate = bm.verts.new(bm.verts[0].co.copy())      # a coincident vertex
bm.verts.index_update()
bm.to_mesh(bolt_low.data); bm.free()
modifier = low.modifiers.new('Viewport only', 'SUBSURF')
modifier.show_render = False
ts.bake.ray_distance = 1.5                           # the torus' inner-ring cage crosses its hole

report = doctor.examine(ts, 'PRE_BAKE')
print('BAKE_DOCTOR_PLANTED', report.summary(), flush=True)
normals = findings(report, 'normals')
low_normals = next(f for f in normals if f.object_name == 'Body_low')
assert set(low_normals.faces) == {0, 5} and 'inconsistent' in low_normals.message, low_normals
high_normals = next(f for f in normals if f.object_name == 'Body_high')
assert 'flipped' in high_normals.message and len(high_normals.faces) == len(high.data.polygons), high_normals
doubles = findings(report, 'doubles')
assert doubles and doubles[0].object_name == 'Bolt_low' and set(doubles[0].vertices) == {0, len(bolt_low.data.vertices) - 1}, doubles
mods = findings(report, 'modifiers')
assert mods and 'viewport-only' in mods[0].message and mods[0].objects == ('Body_low',), mods
cage = findings(report, 'cage')
assert cage and cage[0].object_name == 'Ring_low' and cage[0].faces and cage[0].vertices, cage
assert report.status('cage') == 'WARN' and report.status('normals') == 'WARN'
ts.bake.ray_distance = .1

# Selection: faces, vertices, objects.
message = doctor.select(ts, low_normals)
assert 'faces' in message
flags = np.empty(len(low.data.polygons), bool); low.data.polygons.foreach_get('select', flags)
assert set(np.flatnonzero(flags)) == {0, 5} and bpy.context.view_layer.objects.active == low
message = doctor.select(ts, doubles[0])
flags = np.empty(len(bolt_low.data.vertices), bool); bolt_low.data.vertices.foreach_get('select', flags)
assert set(np.flatnonzero(flags)) == set(doubles[0].vertices) and scene.tool_settings.mesh_select_mode[0]
message = doctor.select(ts, findings(report, 'scale')[0])
assert bolt_low.select_get() and not low.select_get() and bpy.context.view_layer.objects.active == bolt_low
# Edit-mode selection uses bmesh.
bpy.context.view_layer.objects.active = low
bpy.ops.object.mode_set(mode='EDIT')
doctor.select(ts, low_normals)
bm = bmesh.from_edit_mesh(low.data); bm.faces.ensure_lookup_table()
assert {f.index for f in bm.faces if f.select} == {0, 5}
bpy.ops.object.mode_set(mode='OBJECT')
doctor.select_all(ts, report)
print('BAKE_DOCTOR_SELECT_OK', flush=True)

# Seams: island borders without seam flags are reported as INFO with edges.
seams = findings(report, 'seams')
assert seams and seams[0].severity == 'INFO' and seams[0].edges, seams
doctor.select(ts, seams[0])
flags = np.empty(len(low.data.edges), bool); low.data.edges.foreach_get('select', flags)
assert set(np.flatnonzero(flags)) == set(seams[0].edges)
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT'); bpy.ops.uv.seams_from_islands()
bpy.ops.object.mode_set(mode='OBJECT')
assert not [f for f in doctor.examine(ts).findings if f.check == 'seams' and f.object_name == 'Body_low']

# By-name matching: the near-miss source becomes a bake error that stops the bake.
ts.bake.source_mode = 'MATCHED'
matched = doctor.examine(ts, 'PRE_BAKE')
assert matched.status('naming') == 'FAIL' and matched.status('normals') == 'WARN', matched.summary()
ts.bake.source_mode = 'ALL'
# Operators and the pre-bake gate.
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.bake_doctor(action='CHECK') == {'FINISHED'}
    index = next(i for i, f in enumerate(doctor.last(ts).findings) if f.check == 'doubles')
    assert bpy.ops.open_painter.bake_doctor(action='SELECT', index=index) == {'FINISHED'}
assert 'selected' in scene.open_painter.status
# An ERROR stops the bake with the report retained.
ts.bake.source_mode = 'MATCHED'
from open_painter.baking.bake_manager import bake_steps
try:
    next(bake_steps(bpy.context, ts))
except ValueError as exc:
    assert 'Bake check failed' in str(exc), exc
else:
    raise AssertionError('an unmatched MATCHED source must stop the bake')
assert doctor.last(ts).errors
# Full tier adds sampled ray coverage.
ts.bake.source_mode = 'ALL'
full = doctor.examine(ts, 'FULL')
assert full.status('coverage') != 'SKIP', full.summary()

# A finding clicked during a correction brush session ends the session first: the face
# selection that masks strokes to the assigned slot must not become the finding's faces.
from open_painter.baking import corrections
project = scene.open_painter
bpy.context.view_layer.objects.active = low
corrections.begin_brush(bpy.context, ts, 'OFFSET')
assert project.paint_correction == 'OFFSET' and low.mode == 'TEXTURE_PAINT' and low.data.use_paint_mask
assert doctor.select(ts, low_normals).startswith('selected 2 faces')
assert project.paint_image is None and not project.paint_correction and low.mode == 'OBJECT'
assert not low.data.use_paint_mask
flags = np.empty(len(low.data.polygons), bool); low.data.polygons.foreach_get('select', flags)
assert set(np.flatnonzero(flags)) == {0, 5}
corrections.begin_brush(bpy.context, ts, 'OFFSET')
doctor.select(ts, findings(report, 'scale')[0])
assert project.paint_image is None and low.mode == 'OBJECT' and bolt_low.select_get()
print('BAKE_DOCTOR_SESSION_OK', flush=True)

# Evaluated polygon indices only address base faces while no modifier changes the face count:
# a mirrored target stacks its UVs, and its findings say so instead of offering wrong faces.
mirror = bolt_low.modifiers.new('Mirror', 'MIRROR')
mirrored = doctor.examine(ts, 'FULL')
gated = [f for f in mirrored.findings if f.object_name == 'Bolt_low' and f.check in {'uvs', 'coverage'}]
assert any('Stacked' in f.message and f.check == 'uvs' for f in gated), gated
assert all(not f.faces and 'not selectable' in f.message for f in gated), gated
doctor.select_all(ts, mirrored)                     # nothing left on Bolt_low to mis-select
bolt_low.modifiers.remove(mirror)
plain = doctor.examine(ts, 'FULL')
assert all(f.faces for f in plain.findings if f.object_name == 'Bolt_low' and f.check == 'coverage'), plain.findings
print('BAKE_DOCTOR_TOPOLOGY_OK', flush=True)

# The doctor reads the layer the CPU reference evaluates the targets in (0.9.8). A layout/
# texturing split excludes the bolt from the first view layer; the artist works in the second.
# The first layer evaluated the excluded bolt to its base mesh, so the mirrored target looked
# unmodified and evaluated face indices were offered as base faces.
from open_painter.baking import diagnostics
props = bpy.data.collections.new('Props')
scene.collection.children.link(props)
for owner in list(bolt_low.users_collection):
    owner.objects.unlink(bolt_low)
props.objects.link(bolt_low)
texturing = scene.view_layers.new('Texturing')
scene.view_layers[0].layer_collection.children['Props'].exclude = True
bpy.context.window.view_layer = texturing
mirror = bolt_low.modifiers.new('Mirror', 'MIRROR')
assert doctor._base_topology(ts)['Bolt_low'] is False, 'the mirrored bolt evaluates in the artist\'s layer'
split = doctor.examine(ts, 'FULL')
gated = [f for f in split.findings if f.object_name == 'Bolt_low' and f.check in {'uvs', 'coverage'}]
assert any('Stacked' in f.message and f.check == 'uvs' for f in gated), gated
assert all(not f.faces and 'not selectable' in f.message for f in gated), gated
assert not any('skipped' in f.message for f in split.findings), split.findings
# The diagnostics' own face selection refuses those indices the same way.
counts, skipped = diagnostics.select_faces(ts, [diagnostics.Issue('INFO', 'planted', 'Bolt_low', (0, 1))])
assert skipped == ['Bolt_low'] and not counts, (counts, skipped)
# The artist's layer excludes the bolt but the other one evaluates it: that layer serves both.
scene.view_layers[0].layer_collection.children['Props'].exclude = False
texturing.layer_collection.children['Props'].exclude = True
assert doctor._base_topology(ts)['Bolt_low'] is False
bolt_low.modifiers.remove(mirror)
assert doctor._base_topology(ts)['Bolt_low'] is True
# ``evaluable`` keeps an object some layer gives an enabled base and drops one no layer does
# (excluded everywhere, or disabled in viewports), walking each layer's collection tree
# once rather than once per object: the bake stages and validation call it per texture set.
from open_painter.geometry import snapshots
walks = []                                          # root walks; the recursion re-enters the global
tree_walk = snapshots._enabled_collections
snapshots._enabled_collections = lambda layer_collection, found: (
    layer_collection.collection == scene.collection and walks.append(layer_collection)) or tree_walk(layer_collection, found)
try:
    assert snapshots.evaluable(scene, [low, bolt_low, ring_low, high]) == [low, bolt_low, ring_low, high]
    assert len(walks) == len(scene.view_layers) == 2, len(walks)
    scene.view_layers[0].layer_collection.children['Props'].exclude = True
    high.hide_viewport = True
    assert snapshots.evaluable(scene, [low, bolt_low, ring_low, high]) == [low, ring_low]
    assert snapshots._enabled_in(texturing, [low, bolt_low]) == bolt_low
    scene.view_layers[0].layer_collection.children['Props'].exclude = False
    high.hide_viewport = False
    assert snapshots.evaluable(scene, [bolt_low, high]) == [bolt_low, high]
finally:
    snapshots._enabled_collections = tree_walk
texturing.layer_collection.children['Props'].exclude = False
bpy.context.window.view_layer = scene.view_layers[0]
scene.view_layers.remove(texturing)
props.objects.unlink(bolt_low)
scene.collection.objects.link(bolt_low)
scene.collection.children.unlink(props)
bpy.data.collections.remove(props)
print('BAKE_DOCTOR_VIEW_LAYER_OK', flush=True)

# The cage check is bounded like the other per-face checks: above LARGE_MESH faces at render
# level the pre-bake tier defers it to Full Check, from the base-mesh estimate or the
# evaluated count, instead of casting one Python ray per render-subdivided vertex.
ts.bake.ray_distance = 1.5
doctor.LARGE_MESH = 100
bounded = doctor.examine(ts, 'PRE_BAKE')
ring_cage = [f for f in findings(bounded, 'cage') if f.object_name == 'Ring_low']
assert ring_cage and ring_cage[0].severity == 'INFO' and 'Full Check' in ring_cage[0].message, findings(bounded, 'cage')
doctor.LARGE_MESH = 300                              # the torus (288 faces) passes the estimate...
array = ring_low.modifiers.new('Array', 'ARRAY')     # ...and the evaluated count catches the doubled mesh
array.count = 2
bounded = doctor.examine(ts, 'PRE_BAKE')
ring_cage = [f for f in findings(bounded, 'cage') if f.object_name == 'Ring_low']
assert ring_cage and ring_cage[0].severity == 'INFO' and 'evaluated faces' in ring_cage[0].message, findings(bounded, 'cage')
ring_low.modifiers.remove(array)
assert doctor.examine(ts, 'FULL').status('cage') == 'WARN'
doctor.LARGE_MESH = 250_000
ts.bake.ray_distance = .1
print('BAKE_DOCTOR_CAGE_BOUND_OK', flush=True)

# Edit Mode: the checks see the edit BMesh (seams just marked, faces just flipped), and a
# stale finding is refused against the edit mesh instead of indexing past it.
bpy.context.view_layer.objects.active = bolt_low
bpy.ops.object.mode_set(mode='EDIT')
edit = doctor.examine(ts, 'PRE_BAKE')
bolt_seams = [f for f in findings(edit, 'seams') if f.object_name == 'Bolt_low']
assert bolt_seams and bolt_seams[0].edges, findings(edit, 'seams')
assert not any('skipped' in f.message for f in edit.findings), edit.findings
bpy.ops.mesh.select_all(action='SELECT'); bpy.ops.uv.seams_from_islands()
bm = bmesh.from_edit_mesh(bolt_low.data); bm.faces.ensure_lookup_table()
bmesh.ops.reverse_faces(bm, faces=[bm.faces[2]])
bmesh.update_edit_mesh(bolt_low.data)
edit = doctor.examine(ts, 'PRE_BAKE')
assert not [f for f in findings(edit, 'seams') if f.object_name == 'Bolt_low'], findings(edit, 'seams')
bolt_normals = [f for f in findings(edit, 'normals') if f.object_name == 'Bolt_low']
assert bolt_normals and set(bolt_normals[0].faces) == {2}, bolt_normals
doctor.select(ts, bolt_normals[0])
bm = bmesh.from_edit_mesh(bolt_low.data); bm.faces.ensure_lookup_table()
assert {f.index for f in bm.faces if f.select} == {2}
bmesh.ops.delete(bm, geom=[bm.faces[i] for i in (2, 3, 4, 5)], context='FACES')
bmesh.update_edit_mesh(bolt_low.data)
try:
    doctor.select(ts, bolt_seams[0])
except ValueError as exc:
    assert 'changed since' in str(exc), exc
else:
    raise AssertionError('a finding from before the edit must be refused')
bpy.ops.object.mode_set(mode='OBJECT')
print('BAKE_DOCTOR_EDIT_MODE_OK', flush=True)
print('BAKE_DOCTOR_PASS', flush=True)
open_painter.unregister()
