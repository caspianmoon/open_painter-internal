"""Bake Correction Brush (0.9.1): painted projection distance, source exclusion and region rebakes."""
import math
import sys
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.baking import corrections
from open_painter.baking.bake_manager import register_map
from open_painter.baking.cycles_baker import bake
from open_painter.core import image_manager as images, texture_set_manager as sets
from open_painter.geometry.provenance import status


def cube(name, size, subdivisions=0):
    bpy.ops.mesh.primitive_cube_add(size=size)
    obj = bpy.context.object
    obj.name = name
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    if subdivisions:
        bpy.ops.mesh.subdivide(number_cuts=subdivisions)
    bpy.ops.uv.smart_project(island_margin=.03)
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
low = cube('Body_low', 2.0, 2)   # interior top vertices belong to the top island only
high = cube('Body_high', 2.0, 2)
bpy.ops.mesh.primitive_cube_add(size=1.0)
plate = bpy.context.object
plate.name = 'Plate_high'
plate.scale = (.5, .5, .02)
plate.location = (0, 0, 1.3)   # entirely above the 0.1 cage, below a 0.5 one
plate.rotation_euler = (math.radians(25), 0, 0)
bpy.ops.object.transform_apply(scale=True, rotation=True)
sets.create(scene, 'Other set', 512)   # selected in the dock later, while the session stays on ts
ts = sets.create(scene, 'Corrections', 512)
sets.assign(scene, ts, low)
for obj in (high, plate):
    ref = ts.bake.high_objects.add()
    ref.object, ref.name = obj, obj.name
settings = ts.bake
settings.selected_to_active = True
settings.cage_mode = 'AUTO'
settings.separate_cage_distance = True
settings.cage_extrusion = .1
settings.ray_distance = 1.0
settings.samples = 1
settings.margin = 2
settings.normal, settings.ao, settings.curvature = True, False, False
scene.render.engine = 'CYCLES'
n = int(ts.resolution)

# The top island's UV footprint is where the plate would project.
tops = [p for p in low.data.polygons if p.normal.z > .9]
uv = low.data.uv_layers.active
corners = np.asarray([uv.data[i].uv[:] for p in tops for i in p.loop_indices], np.float32)
u0, v0 = corners.min(0); u1, v1 = corners.max(0)
rows = slice(int(v0 * n) + 2, int(v1 * n) - 2)
cols = slice(int(u0 * n) + 2, int(u1 * n) - 2)
inside = np.zeros((n, n), bool); inside[rows, cols] = True

baseline = images.pixels(bake(bpy.context, ts, 'NORMAL'))[..., :3].copy()
assert not corrections.active_keys(ts)
flat = np.abs(baseline[inside] - (.5, .5, 1.)).max()
assert flat < .02, f'without corrections the top face bakes the flat cube top ({flat})'

# Projection Distance painted white over the top face lifts the cage above the plate.
ts.bake.corrections.offset_range = .4
offset = corrections.image_of(ts, 'OFFSET', create_missing=True)
data = images.pixels(offset)
data[rows, cols, :3] = 1.0
images.write(offset, data, pack=False)
assert corrections.refresh_used(offset) and corrections.active_keys(ts) == ['OFFSET']
weights = corrections.vertex_weights(low.data, {'OFFSET': data[..., 0]})['OFFSET']
top_vertices = {v for p in tops for v in p.vertices}
lifted = [v for v in top_vertices if abs(low.data.vertices[v].co.x) < .99 and abs(low.data.vertices[v].co.y) < .99]
others = [v for v in range(len(low.data.vertices)) if v not in top_vertices]
assert lifted and all(weights[v] > .9 for v in lifted), weights[lifted]
assert weights[others].max() < .51, weights[others].max()
corrected = images.pixels(bake(bpy.context, ts, 'NORMAL'))[..., :3].copy()
changed = np.abs(corrected - baseline).max(-1) > .05
# The plate covers 0.5 x 0.46 of the 2 x 2 face: about 6 % of the island.
assert .04 < changed[inside].mean() < .12, f'the plate must appear where the distance was painted ({changed[inside].mean():.2f})'
outside = ~corrections.dilate(inside, settings.margin + 2)
assert not changed[outside].any(), f'{changed[outside].sum()} texels changed outside the painted region'
print('BAKE_CORRECTIONS_OFFSET_OK', flush=True)

# The corrected cage is what a hand-made cage would be: top vertices raised by extrusion + range.
from open_painter.baking.render_geometry import scope
with scope(bpy.context, [low]) as frozen:
    cage = corrections.corrected_cage(ts, frozen[low], corrections.world_rasters(ts))
    positions = np.empty((len(cage.data.vertices), 3), np.float32); cage.data.vertices.foreach_get('co', positions.ravel())
    for v in lifted:
        assert positions[v][2] > 1.0 + .1 + .3, positions[v]
    bpy.data.meshes.remove(cage.data)

# Region rebake: only the faces whose corrections changed are rebaked, into the same image.
prop = None
register_map(ts, 'NORMAL', bake(bpy.context, ts, 'NORMAL'))
prop = next(m for m in ts.baked_maps if m.kind == 'NORMAL')
assert status(ts, prop) == 'Ready'
before_image = prop.image
data = images.pixels(offset)
data[rows, cols, :3] = .5     # paint the correction away again
images.write(offset, data, pack=False)
corrections.refresh_used(offset)
assert status(ts, prop).startswith('Stale'), 'painting a correction marks the map stale'
result = corrections.rebake_region(bpy.context, ts, ['NORMAL'])
assert result['NORMAL'] > 0 and prop.image is before_image, result
rebaked = images.pixels(prop.image)[..., :3]
assert np.abs(rebaked - baseline).max() < .02, float(np.abs(rebaked - baseline).max())
assert status(ts, prop) == 'Ready'
assert corrections.rebake_region(bpy.context, ts, ['NORMAL']) == {'NORMAL': 0}
print('BAKE_CORRECTIONS_REGION_OK', flush=True)

# Ignore High Poly: where painted, the ignorable plate leaves the projection.
data[rows, cols, :3] = 1.0
images.write(offset, data, pack=False); corrections.refresh_used(offset)
ts.bake.high_objects[1].ignorable = True
ignore = corrections.image_of(ts, 'IGNORE', create_missing=True)
mask = images.pixels(ignore)
half = slice(cols.start, (cols.start + cols.stop) // 2)
mask[rows, half, :3] = 1.0
images.write(ignore, mask, pack=False); corrections.refresh_used(ignore)
ignored = images.pixels(bake(bpy.context, ts, 'NORMAL'))[..., :3]
left = np.zeros((n, n), bool); left[rows, half] = True
right = inside & ~left
assert np.abs(ignored[left] - baseline[left]).max() < .02, 'ignored region must match the bake without the plate'
assert np.abs(ignored[right] - corrected[right]).max() < .02, 'unpainted region keeps the plate'
print('BAKE_CORRECTIONS_IGNORE_OK', flush=True)

# Brush session: targets the correction image, survives synchronize, ends cleanly.
from open_painter.painting import paint_session
project = scene.open_painter
image = corrections.begin_brush(bpy.context, ts, 'OFFSET')
assert image is offset and project.paint_correction == 'OFFSET' and project.paint_image is offset
assert low.mode == 'TEXTURE_PAINT' and scene.tool_settings.image_paint.canvas is offset
assert paint_session.synchronize(bpy.context)
ts.bake.corrections.value = .1
assert abs(ts.bake.corrections.value - .1) < 1e-4
from open_painter.evaluation import shader_preview
assert shader_preview.native_paint_input(scene) is True
data = images.pixels(offset); data[0:8, 0:8, :3] = .9
offset.pixels.foreach_set(data.ravel()); offset.update()
assert corrections.stroke_finished(scene) and offset['open_painter_pixel_revision'] >= 1
assert corrections.pending_rebake(scene) is None
import time; time.sleep(corrections.SETTLE + .05)
assert corrections.pending_rebake(scene).id == ts.id
# The live rebake targets the set that owns the canvas, not the one selected in
# the dock: the session survives selecting another set to inspect it (0.9.7).
from unittest.mock import patch
from open_painter.evaluation import cache
project.active_texture_set = 0
assert paint_session.synchronize(bpy.context) and project.paint_set_id == ts.id
rebaked = []
with patch.object(corrections, 'rebake_region', lambda context, target, kinds=None: rebaked.append(target.id) or {}):
    assert cache._rebake_pending(scene), 'the settled stroke must run its rebake'
assert rebaked == [ts.id], rebaked
assert corrections.pending_rebake(scene) is None and scene.as_pointer() not in corrections._pending
# The operator and the brush panel follow the session too, while the dock shows the other set.
from open_painter.ui import bake_corrections as correction_ui
other = project.texture_sets[0]
assert correction_ui.session_set(bpy.context) is not None and correction_ui.session_set(bpy.context).id == ts.id
rebaked.clear()
with patch.object(corrections, 'rebake_region',
                  lambda context, target, kinds=None, flush=False: rebaked.append((target.id, flush)) or {}), \
        bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.bake_correction(action='REBAKE_REGION') == {'FINISHED'}
    # The operator flushes the edit meshes it stages (a user action); the timer above did not.
    assert rebaked == [(ts.id, True)], f'Rebake Region ran on the set selected in the dock ({rebaked})'
    assert bpy.ops.open_painter.bake_correction(action='REBAKE_REGION', set_id=other.id) == {'FINISHED'}
    assert rebaked == [(ts.id, True), (other.id, True)], 'a button drawn in a set\'s panel acts on that set'
other.bake.corrections.active = 'SKEW'   # the other set's selector must not re-target the session
assert project.paint_correction == 'OFFSET' and project.paint_set_id == ts.id and project.paint_image is offset
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.bake_correction(action='CLEAR', kind='OFFSET', set_id=other.id) == {'FINISHED'}
    assert bpy.ops.open_painter.bake_correction(action='CLEAR_ALL', set_id=other.id) == {'FINISHED'}
assert project.paint_correction == 'OFFSET' and project.paint_image is offset and low.mode == 'TEXTURE_PAINT', \
    'clearing another set\'s corrections ended the session'
assert corrections.image_of(ts, 'OFFSET') is offset
project.active_texture_set = 1
overlays = [o for o in scene.objects if o.get(corrections.OVERLAY)]
assert overlays and overlays[0].hide_select
ts.bake.corrections.active = 'SKEW'
assert project.paint_correction == 'SKEW' and project.paint_image is corrections.image_of(ts, 'SKEW')
ts.bake.corrections.active = 'DIRECTION'
# Re-targeting the session replaces its canvas snapshot: one per scene, never one per kind visited.
assert list(corrections._snapshots) == [scene.as_pointer()]
assert corrections._snapshots[scene.as_pointer()][0] == project.paint_image.as_pointer()
corrections.end_brush(bpy.context)
assert not project.paint_correction and project.paint_image is None and low.mode == 'OBJECT'
assert not corrections._snapshots, 'ending the session drops the canvas snapshot'
assert not [o for o in scene.objects if o.get(corrections.OVERLAY)]
# Paint on a layer while the correction brush runs: the layer session replaces it outright
# (the stale key would pin the brush to the correction path: no retargeting, live rebakes).
from open_painter.core import layer_stack as stack
marks = stack.add(ts, 'PAINT', 'Marks')
ts.active_layer = stack.index_of(ts, marks.id)
corrections.begin_brush(bpy.context, ts, 'OFFSET')
assert project.paint_correction == 'OFFSET' and corrections._snapshots
layer_image = paint_session.begin(bpy.context, ts, 'ROUGHNESS')
assert not project.paint_correction and project.paint_image is layer_image
assert project.paint_layer_id == marks.id and low.mode == 'TEXTURE_PAINT'
assert not corrections._snapshots and not [o for o in scene.objects if o.get(corrections.OVERLAY)]
data = images.pixels(layer_image); data[0:8, 0:8, :3] = .9
layer_image.pixels.foreach_set(data.ravel()); layer_image.update()
assert paint_session.detect(scene, force=True)
assert not corrections.stroke_finished(scene) and corrections.pending_rebake(scene) is None
paint_session.finish(bpy.context, evaluate=False)
stack.remove(ts, stack.index_of(ts, marks.id))
print('BAKE_CORRECTIONS_HANDOVER_OK', flush=True)
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.bake_correction(action='CLEAR_ALL') == {'FINISHED'}
assert not corrections.active_keys(ts) and ts.bake.corrections.offset_image is None
print('BAKE_CORRECTIONS_SESSION_OK', flush=True)


def nonneutral(values, region):
    return int((np.abs(values[region] - (.5, .5, 1.)).max(-1) > .05).sum())


def paint(key, value, region_rows, region_cols):
    image = corrections.image_of(ts, key, create_missing=True)
    values = images.pixels(image)
    values[region_rows, region_cols, :3] = value
    images.write(image, values, pack=False)
    corrections.refresh_used(image)
    return image


# A low bump on the top face: high-poly detail at the surface that the default projection captures.
bpy.ops.mesh.primitive_uv_sphere_add(radius=.5, location=(0, 0, 1.0))
bump = bpy.context.object
bump.name = 'Bump_high'
bump.scale = (1, 1, .1)
bpy.ops.object.transform_apply(scale=True)
ref = ts.bake.high_objects.add(); ref.object, ref.name = bump, bump.name

# Shared ray distance (the defaults): a painted lift must not move the rays' reach off the surface.
settings.separate_cage_distance, settings.ray_distance = False, .1
ts.bake.corrections.offset_range = .05
baseline = images.pixels(bake(bpy.context, ts, 'NORMAL'))[..., :3].copy()
detail = nonneutral(baseline, inside)
assert detail > 500, detail
paint('OFFSET', 1.0, rows, cols)
lifted_map = images.pixels(bake(bpy.context, ts, 'NORMAL'))[..., :3]
kept = nonneutral(lifted_map, inside)
assert kept > detail * .9, f'lifting the cage by the painted distance lost the bump ({kept} of {detail} texels)'
with scope(bpy.context, [low]) as frozen:
    cage = corrections.corrected_cage(ts, frozen[low], corrections.world_rasters(ts))
    assert abs(cage[corrections.LIFT] - .05) < 1e-3, cage[corrections.LIFT]
    bpy.data.meshes.remove(cage.data)
print('BAKE_CORRECTIONS_REACH_OK', flush=True)

# Distance Range scales every painted texel: changing it stales the map and dirties the stroke.
register_map(ts, 'NORMAL', bake(bpy.context, ts, 'NORMAL'))
prop = next(m for m in ts.baked_maps if m.kind == 'NORMAL')
assert status(ts, prop) == 'Ready'
ts.bake.corrections.direction_vector = (1., 0., 0.)   # no Projection Direction painted: not a bake input yet
assert status(ts, prop) == 'Ready' and not corrections.changed_texels(ts, 'NORMAL').any()
# Undo keeps the record the region diff needs (0.9.7): without it the next
# stroke would fall back to a full bake of every enabled kind.
cache.on_undo(None)
assert corrections.changed_texels(ts, 'NORMAL') is not None, 'undo forgot the last bake\'s corrections'
assert not corrections.changed_texels(ts, 'NORMAL').any()
ts.bake.corrections.offset_range = .06
assert status(ts, prop).startswith('Stale'), 'a new distance range is a bake input of the painted map'
dirty = corrections.changed_texels(ts, 'NORMAL')
assert dirty[rows, cols].all() and not dirty[~inside].any()
result = corrections.rebake_region(bpy.context, ts, ['NORMAL'])
assert result['NORMAL'] > 0 and status(ts, prop) == 'Ready', (result, status(ts, prop))
print('BAKE_CORRECTIONS_RANGE_OK', flush=True)

# Region rebakes keep the neighbours' texels: their faces went to scratch in the restricted bake.
settings.margin = 16
corrections.clear(ts)
# Mode switches re-allocated the mesh: resolve the top face and its UVs afresh.
centre = next(p for p in low.data.polygons if p.normal.z > .9 and abs(p.center.x) < .1 and abs(p.center.y) < .1)
cu, cv = np.asarray([low.data.uv_layers.active.data[i].uv[:] for i in centre.loop_indices], np.float32).mean(0)
dab_rows, dab_cols = slice(int(cv * n) - 1, int(cv * n) + 1), slice(int(cu * n) - 1, int(cu * n) + 1)
paint('OFFSET', .5 + .002, dab_rows, dab_cols)   # a used canvas: the next dab dirties only its face
register_map(ts, 'NORMAL', bake(bpy.context, ts, 'NORMAL'))
prop = next(m for m in ts.baked_maps if m.kind == 'NORMAL')
truth = images.pixels(prop.image)[..., :3].copy()
paint('OFFSET', .5 + .004, dab_rows, dab_cols)   # dirties the centre face; the cage barely moves
result = corrections.rebake_region(bpy.context, ts, ['NORMAL'])
assert 0 < result['NORMAL'] <= 4, result
after = images.pixels(prop.image)[..., :3]
worst = float(np.abs(after - truth).max())
assert worst < .05, f'the rebaked face\'s margin overwrote its neighbours ({worst})'
print('BAKE_CORRECTIONS_HALO_OK', flush=True)

# Render subdivision differs from the viewport: the region's face ids must name the staged faces.
settings.margin = 2
subsurf = low.modifiers.new('Subdivision', 'SUBSURF')
subsurf.subdivision_type = 'SIMPLE'
subsurf.levels, subsurf.render_levels = 1, 2
register_map(ts, 'NORMAL', bake(bpy.context, ts, 'NORMAL'))
prop = next(m for m in ts.baked_maps if m.kind == 'NORMAL')
truth = images.pixels(prop.image)[..., :3].copy()
paint('OFFSET', .5 + .006, dab_rows, dab_cols)
result = corrections.rebake_region(bpy.context, ts, ['NORMAL'])
assert 0 < result['NORMAL'] <= 16, result   # the centre face is four render faces
worst = float(np.abs(images.pixels(prop.image)[..., :3] - truth).max())
assert worst < .05, f'region faces were taken from the viewport evaluation ({worst})'
subsurf.levels, subsurf.render_levels = 2, 1
paint('OFFSET', .5 + .008, dab_rows, dab_cols)
result = corrections.rebake_region(bpy.context, ts, ['NORMAL'])   # must not index past the render mesh
assert 0 < result['NORMAL'] <= 4, result
assert float(np.abs(images.pixels(prop.image)[..., :3] - truth).max()) < .05
low.modifiers.remove(subsurf)
print('BAKE_CORRECTIONS_RENDER_FACES_OK', flush=True)

# ---- Edit Mode: bakes and CPU rasters read the mesh the artist is editing (0.9.8) -----------
# Edit Mode keeps the geometry in the edit BMesh; the datablock every bake stage copies (and
# ``to_mesh`` with all data layers evaluates) lags behind until the mode ends. A region rebake
# started from Edit Mode, a custom-graph raster and the CPU reference must all read the moved
# vertex, not the mesh as it was on entering the mode; only the doctor's checks used to.
import bmesh
from types import SimpleNamespace
from open_painter.baking.maps.utility import bake_utility
from open_painter.evaluation import custom_graph
saved_reach = settings.separate_cage_distance, settings.cage_extrusion, settings.ray_distance
settings.separate_cage_distance, settings.cage_extrusion, settings.ray_distance = True, .1, 1.0
register_map(ts, 'NORMAL', bake(bpy.context, ts, 'NORMAL'))
prop = next(m for m in ts.baked_maps if m.kind == 'NORMAL')
before = images.pixels(prop.image)[..., :3].copy()
ids, _ = corrections.face_map(bpy.context, ts)[low]
centre_face = int(ids[int(cv * n), int(cu * n)])
region = ids == centre_face
interior = region & ~corrections.dilate(~region, 2)
bpy.context.view_layer.objects.active = low
bpy.ops.object.mode_set(mode='EDIT')
bm = bmesh.from_edit_mesh(low.data); bm.verts.ensure_lookup_table()
corner = next(v for v in bm.verts if abs(v.co.z - 1.) < 1e-6 and abs(v.co.x - 1 / 3) < .01 and abs(v.co.y - 1 / 3) < .01)
corner_index, corner.co.z = corner.index, 1.3   # a corner of the centre face
bmesh.update_edit_mesh(low.data)
assert abs(low.data.vertices[corner_index].co.z - 1.) < 1e-6, 'the datablock lags behind the edit mesh (the premise)'
paint('OFFSET', .5 + .01, dab_rows, dab_cols)
result = corrections.rebake_region(bpy.context, ts, ['NORMAL'], flush=True)   # the operator's call
assert low.mode == 'EDIT' and 0 < result['NORMAL'] <= 4, (low.mode, result)
assert abs(low.data.vertices[corner_index].co.z - 1.3) < 1e-6, 'the bake staged the datablock without flushing the edit'
tilted = (np.abs(images.pixels(prop.image)[..., :3] - before).max(-1) > .05)[interior].mean()
assert tilted > .3, f'the rebaked face bakes flat: the staged mesh is the one from before the edit ({tilted:.2f})'
# The custom-graph raster (Cycles, staged copies) and the CPU reference (``snapshots.targets``)
# agree on the raised geometry: world height baked through a Position input against POSITION.
tree = bpy.data.node_groups.new('OP Height', 'ShaderNodeTree')
tree.interface.new_socket(name='Position', in_out='INPUT', socket_type='NodeSocketVector')
tree.interface.new_socket(name='Height', in_out='OUTPUT', socket_type='NodeSocketFloat')
group_in, group_out = tree.nodes.new('NodeGroupInput'), tree.nodes.new('NodeGroupOutput')
split = tree.nodes.new('ShaderNodeSeparateXYZ')
tree.links.new(group_in.outputs['Position'], split.inputs['Vector'])
tree.links.new(split.outputs['Z'], group_out.inputs['Height'])
height = custom_graph.raster(ts, SimpleNamespace(node_group=tree, name='Height'), n)
position = bake_utility(ts, 'POSITION')
lo, hi = position['world_min'][2], position['world_max'][2]
reference = images.pixels(position)[..., 2] * (hi - lo) + lo
assert abs(hi - 1.3) < 1e-4, f'the CPU reference bounds stop at {hi}: it read the mesh from before the edit'
assert abs(height[interior].max() - 1.3) < .03 and abs(reference[interior].max() - 1.3) < .03, \
    (height[interior].max(), reference[interior].max())
worst = float(np.abs(height - reference)[interior].max())
assert worst < .03, f'the custom-graph bake and the CPU raster disagree on the edited mesh ({worst})'
bpy.data.images.remove(position)
# Only operator-driven stages flush. The custom-graph raster runs from the evaluation tick
# and layer-cache jobs, and the stroke-driven region rebake from the scheduler's timer:
# ``update_from_editmode`` tags the mesh as changed, so a timer flushing it would repeat
# the full-mesh flush every tick and fight the edit session. Those stage the datablock as
# it is (the CPU reference reads the same datablock, so the two still agree); the
# operator's ``flush=True`` is what loads a later edit.
bm = bmesh.from_edit_mesh(low.data); bm.verts.ensure_lookup_table()
bm.verts[corner_index].co.z = 1.6
bmesh.update_edit_mesh(low.data)
height = custom_graph.raster(ts, SimpleNamespace(node_group=tree, name='Height'), n)
assert abs(low.data.vertices[corner_index].co.z - 1.3) < 1e-6, 'a scheduler-driven raster flushed the edit mesh'
assert abs(height[interior].max() - 1.3) < .03, height[interior].max()
result = corrections.rebake_region(bpy.context, ts, ['NORMAL'])   # the timer's call
assert result == {'NORMAL': 0}, result
assert abs(low.data.vertices[corner_index].co.z - 1.3) < 1e-6, 'the timer-driven region rebake flushed the edit mesh'
corrections.rebake_region(bpy.context, ts, ['NORMAL'], flush=True)
assert abs(low.data.vertices[corner_index].co.z - 1.6) < 1e-6, 'the operator-driven region rebake must flush'
bpy.data.node_groups.remove(tree)
bm = bmesh.from_edit_mesh(low.data); bm.verts.ensure_lookup_table()
bm.verts[corner_index].co.z = 1.0
bmesh.update_edit_mesh(low.data)
bpy.ops.object.mode_set(mode='OBJECT')
assert abs(low.data.vertices[corner_index].co.z - 1.) < 1e-6
settings.separate_cage_distance, settings.cage_extrusion, settings.ray_distance = saved_reach
print('BAKE_CORRECTIONS_EDIT_MODE_OK', flush=True)

# Changing the set resolution keeps the painted corrections (rescaled) and their map registered.
corrections.clear(ts)
offset = paint('OFFSET', 1.0, rows, cols)
register_map(ts, 'NORMAL', bake(bpy.context, ts, 'NORMAL'))
prop = next(m for m in ts.baked_maps if m.kind == 'NORMAL')
ts.resolution = '1024'
assert corrections.active_keys(ts) == ['OFFSET']
assert corrections.image_of(ts, 'OFFSET') is offset and tuple(offset.size) == (1024, 1024)
assert corrections.raster(ts, 'OFFSET').shape == (1024, 1024) and corrections.raster(ts, 'OFFSET').max() > .99
assert corrections.rebake_region(bpy.context, ts, ['NORMAL']) == {'NORMAL': -1}, 'a map of another size is baked fully'
assert tuple(prop.image.size) == (1024, 1024) and status(ts, prop) == 'Ready'
assert len([i for i in bpy.data.images if i.get(corrections.TAG) == 'OFFSET']) == 1, 'no orphaned canvas'
ts.resolution = '512'
assert corrections.image_of(ts, 'OFFSET') is offset and tuple(offset.size) == (512, 512)
# The panel header asks on every redraw: the answer comes from the flag, not the canvas.
read = corrections.pixels
corrections.pixels = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('active_keys read a canvas'))
try:
    assert corrections.active_keys(ts) == ['OFFSET']

    class Header:   # a collapsed section: the header's count is all the redraw computes
        def panel(self, *args, **kwargs):
            return self, None

        def label(self, text='', **kwargs):
            self.text = text

    header = Header()
    correction_ui.draw_corrections(header, bpy.context, ts)
    assert header.text == 'Bake Corrections · 1 painted', header.text
finally:
    corrections.pixels = read
print('BAKE_CORRECTIONS_RESOLUTION_OK', flush=True)

# Cage: None (a surface bake) keeps baking under a Skew stroke: the cage sits a hair off the surface.
corrections.clear(ts)
bpy.data.objects.remove(bump, do_unlink=True)
ts.bake.high_objects.remove(len(ts.bake.high_objects) - 1)
bevel = high.modifiers.new('Bevel', 'BEVEL')
bevel.width, bevel.segments = .3, 4
settings.cage_mode, settings.ray_distance = 'NONE', 0.0
surface = images.pixels(bake(bpy.context, ts, 'NORMAL'))[..., :3]
detail = nonneutral(surface, np.ones((n, n), bool))
assert detail > 1000, detail
paint('SKEW', 1.0, dab_rows, dab_cols)
skewed = images.pixels(bake(bpy.context, ts, 'NORMAL'))[..., :3]
kept = nonneutral(skewed, np.ones((n, n), bool))
assert kept > detail * .8, f'a correction under Cage: None wiped the map ({kept} of {detail} texels)'
print('BAKE_CORRECTIONS_SURFACE_CAGE_OK', flush=True)

# Duplicating the set replays its bake settings. The brush value reads and writes the paint
# colour, so replaying it must not collapse the artist's RGB to a grey of its red channel.
from open_painter.painting.brush_bridge import color_owner
ts_id = ts.id
owner = color_owner(scene)
assert owner is not None, 'the brush session left a paint brush behind'
owner.color = (1., .1, .1)
copy = sets.duplicate(scene, next(i for i, t in enumerate(project.texture_sets) if t.id == ts_id))
copy_id = copy.id
assert np.allclose(tuple(owner.color), (1., .1, .1), atol=1e-4), tuple(owner.color)
assert copy.bake.corrections.offset_range == ts.bake.corrections.offset_range
sets.remove(scene, next(i for i, t in enumerate(project.texture_sets) if t.id == copy_id))
ts = next(t for t in project.texture_sets if t.id == ts_id)
# Deleting the set that owns a running correction session ends the session with it: the mesh
# leaves Texture Paint, its face mask and selection come back, no canvas snapshot lingers.
corrections.begin_brush(bpy.context, ts, 'OFFSET')
assert project.paint_correction == 'OFFSET' and low.mode == 'TEXTURE_PAINT' and low.data.use_paint_mask
baked_names = {m.image.name for m in ts.baked_maps if m.image}
sets.remove(scene, next(i for i, t in enumerate(project.texture_sets) if t.id == ts_id))
assert not project.paint_correction and project.paint_image is None and not project.paint_set_id
assert low.mode == 'OBJECT' and not low.data.use_paint_mask
assert scene.tool_settings.image_paint.canvas is None
assert not corrections._snapshots and not [o for o in scene.objects if o.get(corrections.OVERLAY)]
assert not [i for i in bpy.data.images if i.get(corrections.TAG)]
# The set's maps and outputs are released for the next save to purge (no fake user, nothing
# binding them), not removed: an undo of the deletion still finds their pixels.
kept = {i.name for i in bpy.data.images if i.users or i.use_fake_user}
assert not baked_names & kept and not [i for i in bpy.data.images if i.get('texture_set_id') == ts_id and (i.users or i.use_fake_user)]
print('BAKE_CORRECTIONS_SET_REMOVED_OK', flush=True)

# ---- the face map reads the layer the CPU reference evaluates the target in (0.9.8) ----------
# A layout/texturing split: the prop's collection is excluded from the first view layer and the
# artist works in the second. Region rebakes keep the staged faces the face map names; the dirty
# texels were painted against the evaluation ``snapshots.targets`` reads. Both must come from the
# same view layer, or the rebaked faces would not be the painted ones.
from open_painter.core.validation import validate
from open_painter.geometry.snapshots import targets
from open_painter.geometry.uv_rasterizer import rasterize
props = bpy.data.collections.new('Props')
scene.collection.children.link(props)
prop = cube('Prop', 2.0, 1)
for owner in list(prop.users_collection):        # cube() links into the active collection
    owner.objects.unlink(prop)
props.objects.link(prop)
prop.location.x = 8
subsurf = prop.modifiers.new('Subdivision', 'SUBSURF')
subsurf.subdivision_type = 'SIMPLE'
subsurf.levels = subsurf.render_levels = 1       # 24 base faces, 96 evaluated ones
texturing = scene.view_layers.new('Texturing')
scene.view_layers[0].layer_collection.children['Props'].exclude = True
bpy.context.window.view_layer = texturing
prop_ts = sets.create(scene, 'Prop', 512)
sets.assign(scene, prop_ts, prop)


def reference_ids():
    """Face ids at the texels of the evaluation the CPU reference reads."""
    with targets(prop_ts) as meshes:
        target = meshes[0]
        ids = np.full((n, n), -1, np.int64)
        rows, cols, tris, _ = rasterize(target['uv'][target['tri_loops']], n)
        ids[rows, cols] = target['tri_polygons'][tris]
        return ids, len(target['mesh'].polygons)


for excluded, serving in ((scene.view_layers[0], texturing), (texturing, scene.view_layers[0])):
    # First the artist's own layer serves; then the artist's layer excludes the prop and the
    # other one evaluates it: both readers follow the same choice.
    excluded.layer_collection.children['Props'].exclude = True
    serving.layer_collection.children['Props'].exclude = False
    ids, count = reference_ids()
    staged, staged_count = corrections.face_map(bpy.context, prop_ts)[prop]
    assert count == staged_count == 96, (count, staged_count)
    assert np.array_equal(staged, ids), f'{(staged != ids).mean():.4f} of the texels name other faces'
    assert not [issue for issue in validate(prop_ts) if 'disabled' in issue], validate(prop_ts)
# Disabled in viewports: no layer evaluates it, so evaluation refuses it and validation says why.
prop.hide_viewport = True
issues = validate(prop_ts)
assert any('Prop is disabled in viewports: enable it or unassign it' in issue for issue in issues), issues
for reader in (reference_ids, lambda: corrections.face_map(bpy.context, prop_ts)):
    try:
        reader()
        raise AssertionError('a target disabled in viewports must not be read from its base mesh')
    except ValueError as exc:
        assert 'Prop' in str(exc) and 'view layer' in str(exc), exc
prop.hide_viewport = False
assert not [issue for issue in validate(prop_ts) if 'disabled' in issue]
bpy.context.window.view_layer = scene.view_layers[0]
scene.view_layers.remove(texturing)
sets.remove(scene, next(i for i, t in enumerate(project.texture_sets) if t.id == prop_ts.id))
print('BAKE_CORRECTIONS_VIEW_LAYER_OK', flush=True)
print('BAKE_CORRECTIONS_PASS', flush=True)
open_painter.unregister()
