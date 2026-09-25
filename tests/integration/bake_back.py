"""Bake back: generated bolts on a panel bake into NORMAL and AO; the maps are non-flat under
the bolts, the bake settings are restored, provenance goes Stale after a regenerate.

The panel has a trench (its own occlusion) and bakes from itself: Bake Back must keep that
occlusion next to the bolts, a later panel rebake too, and Remove must leave the set bakeable
with the maps current. Staging keeps the listed rows' flags across a collection reallocation."""
import sys
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.baking import bake_back, bake_manager
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.core.published_outputs import publish_mask
from open_painter.geometry import provenance
from open_painter.geometry_effects import manager
from open_painter.utils.ids import new_id

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_grid_add(x_subdivisions=40, y_subdivisions=40, size=2)
panel = bpy.context.object
# A trench along +X (UV x in .875..975, away from the bolt spots) gives the panel occlusion of its own.
co = np.empty(len(panel.data.vertices) * 3, np.float32); panel.data.vertices.foreach_get('co', co)
co = co.reshape(-1, 3); co[(co[:, 0] > .75) & (co[:, 0] < .95), 2] = -.3
panel.data.vertices.foreach_set('co', co.ravel()); panel.data.update()
TRENCH = (slice(None), slice(456, 492))
ts = sets.create(scene, 'Panel', 512); sets.assign(scene, ts, panel)
assert ts.bake.selected_to_active is False, 'projection from high poly is opt-in'
ts.preview_backend = 'CPU'
layer = stack.add(ts, 'FILL', 'Bolts')
mask = layer.masks.add(); mask.id, mask.source = new_id(), 'PAINT'
spots = images.create('Bolt spots', 512)
data = np.zeros((512, 512, 4), np.float32); data[..., 3] = 1
for cy, cx in ((128, 128), (128, 384), (384, 128), (384, 384)):
    data[cy - 6:cy + 6, cx - 6:cx + 6, :3] = 1.0
images.write(spots, data, pack=True)
mask.image = spots
bpy.context.view_layer.update()
published = publish_mask(ts, layer)
helper = manager.create(ts, panel, published, 'BOLT', density=4000, size=.08, seed=1)
assert helper is not None and 0 < helper['instance_count'] <= 40, helper['instance_count']


def baked(kind):
    return images.pixels(next(m.image for m in ts.baked_maps if m.kind == kind))


def status_of(kind):
    return provenance.status(ts, next(m for m in ts.baked_maps if m.kind == kind))


# --- staging alone: listed rows keep their flags, and row references are not held across add() ---
highs = []
for i in range(2):
    bpy.ops.mesh.primitive_cube_add(location=(10 + 3 * i, 0, 0))
    highs.append(bpy.context.object)
    ref = ts.bake.high_objects.add(); ref.object, ref.name, ref.target = highs[-1], highs[-1].name, panel
ts.bake.high_objects[0].ignorable = True
extra = [manager.create(ts, panel, published, 'BOLT', density=400, size=.05, seed=seed) for seed in (2, 3, 4, 5)]
assert all(extra)
ts.bake.selected_to_active = True   # the original must survive the staged block
with bake_back.staged(ts, [helper, *extra]) as saved:   # 2 rows + 5 helpers crosses the 4-slot block
    assert len(ts.bake.high_objects) == 7 and ts.bake.selected_to_active, len(ts.bake.high_objects)
    assert ts.bake.ray_distance > .23 and saved['selected_to_active'] is True and saved['ray_distance'] < .11
assert [ref.object for ref in ts.bake.high_objects] == [*highs, helper, *extra]
assert [ref.ignorable for ref in ts.bake.high_objects] == [True] + [False] * 6, 'Ignore Brush Excludes lost'
assert all(ref.target == panel for ref in ts.bake.high_objects)
for obj in extra:
    manager.remove(obj)
for obj in highs:
    bpy.data.objects.remove(obj, do_unlink=True)
ts.bake.high_objects.clear()
ts.bake.ray_distance = .1
print('BAKE_BACK staged rows: PASS', flush=True)

# --- the panel bakes from itself (no high-poly) before any geometry is generated ---
scene.render.engine = 'CYCLES'; scene.cycles.device = 'CPU'; scene.cycles.samples = 4
ts.bake.selected_to_active = False
ts.bake.normal, ts.bake.ao, ts.bake.curvature = True, True, False
bpy.context.view_layer.update()
bake_manager.bake_selected(bpy.context, ts)
own_ao = baked('AO')[..., 0]
assert own_ao[TRENCH].mean() < .9 < own_ao[246:266, 246:266].min(), (own_ao[TRENCH].mean(), own_ao[256, 256])
assert not status_of('AO').startswith('Stale')

saved = (ts.bake.selected_to_active, ts.bake.ray_distance, len(ts.bake.high_objects), ts.bake.normal, ts.bake.ao)
ts.bake.selection = True   # a utility map left ticked is not rebaked by Bake Back
bpy.context.view_layer.update()
bake_back.run(bpy.context, ts, [helper], then='HIDE')
# Map toggles restored; the helper stays a listed source (with the ray distance that reached it)
# so the maps read current until the helper regenerates.
assert (ts.bake.normal, ts.bake.ao, ts.bake.selection) == (saved[3], saved[4], True)
assert not any(m.kind == 'SELECTION' for m in ts.baked_maps), 'Bake Back baked the Selected Faces map'
assert ts.bake.ray_distance >= saved[1]
assert len(ts.bake.high_objects) == saved[2] + 1 and ts.bake.high_objects[-1].object == helper
normal = baked('NORMAL')
ao = baked('AO')
flat = np.abs(normal[..., :2] - .5).max(axis=-1)
assert flat[128 - 20:128 + 20, 128 - 20:128 + 20].max() > .1, 'no relief under a bolt'
assert flat[256 - 10:256 + 10, 256 - 10:256 + 10].max() < .02, 'relief where there is no bolt'
assert ao[128 - 20:128 + 20, 128 - 20:128 + 20, 0].min() < ao[256, 256, 0] - .05, 'no occlusion under a bolt'
# The panel's own occlusion survives: the helpers are the only sources, so its surface projects too.
assert abs(ao[..., 0][TRENCH].mean() - own_ao[TRENCH].mean()) < .08, (ao[..., 0][TRENCH].mean(), own_ao[TRENCH].mean())
assert helper.hide_viewport and helper.hide_render
assert next(m.image for m in ts.baked_maps if m.kind == 'NORMAL').get('open_painter_bake_back')
status = status_of('NORMAL')
assert not status.startswith('Stale'), status
print('BAKE_BACK maps and settings: PASS', status, flush=True)
# A panel rebake in the kept state (projection on, helper-only sources) gives the same maps.
bake_manager.bake_selected(bpy.context, ts)
rebaked = baked('AO')[..., 0]
assert abs(rebaked[TRENCH].mean() - own_ao[TRENCH].mean()) < .08, rebaked[TRENCH].mean()
assert rebaked[128 - 20:128 + 20, 128 - 20:128 + 20].min() < rebaked[256, 256] - .05
print('BAKE_BACK panel rebake keeps own occlusion: PASS', flush=True)
# A separate cage extrusion beyond the ray distance cannot reach the helpers, but the panel's
# surface is a direct bake rather than a projection onto a copy of itself: its occlusion stays.
ts.bake.separate_cage_distance, ts.bake.cage_extrusion = True, ts.bake.ray_distance * 4
bake_manager.bake_selected(bpy.context, ts)
extruded = baked('AO')[..., 0]
assert abs(extruded[TRENCH].mean() - own_ao[TRENCH].mean()) < .08, extruded[TRENCH].mean()
assert extruded[246:266, 246:266].min() > .9, extruded[246:266, 246:266].min()
ts.bake.separate_cage_distance = False
print('BAKE_BACK separate cage extrusion keeps own occlusion: PASS', flush=True)
# Each bake realised the helper through an owned node tree; the stage removes them with
# its copies instead of leaving zero-user trees in the Geometry Nodes picker (0.9.7).
leaked = [g.name for g in bpy.data.node_groups if g.name.startswith('OP Realize Instances')]
assert not leaked, f'the bake stage leaked its realise trees: {leaked}'
print('BAKE_BACK stage releases realise trees: PASS', flush=True)

helper.hide_viewport = helper.hide_render = False
replacement = manager.regenerate(ts, helper)
assert replacement is not None
status = status_of('NORMAL')
assert status.startswith('Stale'), status   # the regenerated helper is a new source
ts.bake.ray_distance = .1   # the artist's own distance again; Remove hands it back
bake_back.run(bpy.context, ts, [replacement], then='REMOVE')
assert not any(ref.object is None for ref in ts.bake.high_objects)
assert not any(obj.get(manager.TAG) == 'INSTANCE_HELPER' for obj in scene.objects)
# Remove leaves the set the way it baked before (no sources to project from) with the maps current.
assert len(ts.bake.high_objects) == 0 and ts.bake.selected_to_active is False, ts.bake.selected_to_active
assert abs(ts.bake.ray_distance - .1) < 1e-6, ts.bake.ray_distance
for kind in ('NORMAL', 'AO'):
    assert not status_of(kind).startswith('Stale'), (kind, status_of(kind))
assert next(bake_manager.bake_steps(bpy.context, ts)) == 'NORMAL'   # the preflight passes again
assert np.abs(baked('NORMAL')[..., :2] - .5).max() > .1, 'Remove lost the baked relief'

# --- a convex cube decorated by a helper alone bakes its own surface: nothing but the helper
# occludes it (no coincident copy of itself), the helper is projected on top, and the surface
# the helper sits on still darkens it (Cycles hides the active low; an unselected stand-in occludes).
from open_painter.baking import cycles_baker
bpy.ops.mesh.primitive_cube_add(size=2, location=(20, 0, 0))
cube = bpy.context.object
bpy.ops.mesh.primitive_plane_add(size=.3, location=(20.3, .3, 1.15), rotation=(0, np.radians(60), 0))
ramp = bpy.context.object   # leans over the top face: a quarter of its hemisphere looks down at it
ramp[manager.TAG] = 'INSTANCE_HELPER'
cube_ts = sets.create(scene, 'Cube', 512); sets.assign(scene, cube_ts, cube)
cube_ts.preview_backend = 'CPU'
row = cube_ts.bake.high_objects.add(); row.object, row.name, row.target = ramp, ramp.name, cube
cube_ts.bake.selected_to_active, cube_ts.bake.ray_distance, cube_ts.bake.ao_scope = True, .3, 'SET'
bpy.context.view_layer.update()
datablocks = (len(bpy.data.objects), len(bpy.data.meshes))
cube_ao = images.pixels(cycles_baker.bake(bpy.context, cube_ts, 'AO'))[..., 0]
cube_normal = images.pixels(cycles_baker.bake(bpy.context, cube_ts, 'NORMAL'))
assert np.quantile(cube_ao, .5) > .995 and cube_ao.mean() > .97, (np.quantile(cube_ao, .5), cube_ao.mean())
assert cube_ao.min() < .9, cube_ao.min()   # the ramp shades the face beneath it
assert np.quantile(np.abs(cube_normal[..., :2] - .5).max(axis=-1), .5) < .02
assert (len(bpy.data.objects), len(bpy.data.meshes)) == datablocks, 'the stage left its stand-in behind'
# Under PAIR scope nothing shades the cube, and the only thing the ramp can see is the face
# it leans over, which Cycles hides from the projection: the stand-in supplies its occlusion.
cube_ts.bake.ao_scope = 'PAIR'
pair_ao = images.pixels(cycles_baker.bake(bpy.context, cube_ts, 'AO'))[..., 0]
assert np.quantile(pair_ao, .5) > .995 and pair_ao.min() < .9, (np.quantile(pair_ao, .5), pair_ao.min())
print('BAKE_BACK convex cube self-bake with helper: PASS', flush=True)
print('BAKE_BACK_PASS', flush=True)
open_painter.unregister()
