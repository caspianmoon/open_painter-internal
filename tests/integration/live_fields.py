"""Live scene fields: batched Geometry Nodes queries agree with the BVH reference, new
kinds behave, the cooperative job engine never analyses inside an evaluation, and
publication invalidates exactly once."""
import sys
from pathlib import Path
from time import monotonic

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation import cache, dependencies, field_jobs
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.metrics import Metrics
from open_painter.generators import scene_fields
from open_painter.geometry import gn_queries, influencers, queries
from open_painter.utils.ids import new_id

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
panel = bpy.context.object
ts = sets.create(scene, 'Panel', 512); sets.assign(scene, ts, panel)
ts.preview_backend = 'CPU'
layer = stack.add(ts, 'FILL'); rough = stack.channel_for(layer, 'ROUGHNESS'); rough.value = .1
mask = layer.masks.add(); mask.id = new_id(); mask.source = 'SCENE'
field = mask.influence
bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=.15, location=(.3, 0, .1))
bolt = bpy.context.object
bpy.ops.mesh.primitive_cube_add(size=.3, location=(-.5, .4, .2))
block = bpy.context.object
block['open_painter_field_radius'] = .6
bpy.context.view_layer.update()


def rough_pixels():
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    return images.pixels(images.output(ts, 'ROUGHNESS').image)[..., 0].copy()


# 1. Geometry Nodes batched queries match the BVH reference (distance, radius, exposure).
n = 128
from open_painter.painting.projection import geometry
positions, normals, valid = geometry(ts, n, coverage=True)
normals /= np.maximum(np.linalg.norm(normals, axis=-1, keepdims=True), 1e-8)
points, point_normals = positions[valid], normals[valid]
objects = [bolt, block]
radii = influencers.radii(objects, .5)
target = gn_queries.combined_target(scene, objects, radii, .5)
assert target is not None and len(target[1]) > 0
gn_distance, gn_radius = gn_queries.evaluate(scene, target, points, 'DISTANCE', .8)
with queries.acceleration(scene, objects) as trees:
    ref_distance, which = queries.nearest(trees, points, .8)
    ref_exposure = queries.exposure(trees, points, point_normals, np.array((0, 0, 1), np.float32), 1.0)
np.testing.assert_allclose(gn_distance, ref_distance, atol=1e-4 * .8 + 1e-6)
per_tree = np.array([radii[obj.name_full] for obj in objects], np.float32)
expected_radius = np.where(which >= 0, per_tree[np.maximum(which, 0)], gn_radius)
close = ref_distance < .8 - 1e-4
np.testing.assert_allclose(gn_radius[close], expected_radius[close], atol=1e-6)
assert (gn_radius[close] == .6).any() and (gn_radius[close] == .5).any(), 'per-object radii did not reach the query'
gn_exposure, _ = gn_queries.evaluate(scene, target, points, 'EXPOSURE', 1.0, point_normals, np.array((0, 0, 1), np.float32))
assert (gn_exposure == ref_exposure).mean() > .995, (gn_exposure == ref_exposure).mean()
assert not any(obj.get('open_painter_analysis_scratch') for obj in bpy.data.objects), 'scratch objects leaked'
assert not any(s.name == 'OP Field Analysis' for s in bpy.data.scenes)
print('LIVE_FIELDS gn queries: PASS', flush=True)

# 2. New kinds on the synchronous path: proximity with per-object radius, ground, world height, scene scope.
field.kind, field.mode, field.resolution = 'PROXIMITY', 'MANUAL', '128'
field.distance, field.object, field.collection = .3, bolt, None
with Metrics() as counters:
    value = scene_fields.compute(ts, field)
assert counters.counts.get('scene_query_batched', 0) == 1 and counters.counts.get('scene_query_fallback', 0) == 0, counters.counts
assert value.max() > .5 and value[:, :16].max() < .05, (value.max(), value[:, :16].max())
field.scope = 'SCENE'
scene_value = scene_fields.compute(ts, field)
assert scene_value[:, :40].max() > .5, 'the scene scope did not see the block (radius .6)'
field.exclude_collection = bpy.data.collections.new('Excluded'); field.exclude_collection.objects.link(block)
scene.collection.children.link(field.exclude_collection)
excluded = scene_fields.compute(ts, field)
np.testing.assert_allclose(excluded, value, atol=1e-6)
field.exclude_collection = None
field.max_objects = 1
try:
    scene_fields.compute(ts, field)
    raise AssertionError('the scope cap was ignored')
except ValueError as error:
    assert 'limit' in str(error)
field.max_objects, field.scope = 64, 'EXPLICIT'
field.kind, field.distance = 'GROUND', .5
panel.location.z = .25; bpy.context.view_layer.update()
ground = scene_fields.compute(ts, field)
np.testing.assert_allclose(ground[valid[::4, ::4]] if False else ground[64, 64], .5, atol=1e-3)
field.ground_object = block   # ground plane at z = .2 → panel .05 above it
np.testing.assert_allclose(scene_fields.compute(ts, field)[64, 64], .9, atol=1e-3)
field.ground_object = None
field.kind, field.bounds = 'WORLD_HEIGHT', (0., 1.)
np.testing.assert_allclose(scene_fields.compute(ts, field)[64, 64], .25, atol=1e-3)
panel.location.z = 0; bpy.context.view_layer.update()
field.kind, field.object, field.distance = 'PROXIMITY', bolt, .3
field.falloff = 'SMOOTH'
mask.operation = 'NORMAL'
linear = scene_fields.compute(ts, field)
field.mode = 'LIVE'
rough_smooth = rough_pixels()
field.falloff = 'LINEAR'
rough_linear = rough_pixels()
assert not np.allclose(rough_smooth, rough_linear) and field.provenance, 'falloff is applied at evaluation'
print('LIVE_FIELDS kinds: PASS', flush=True)

# 3. The cooperative engine (timer emulated): an evaluation never analyses; the job publishes after the debounce.
if not bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.register(cache.tick, first_interval=1000)   # registered → the engine is active; we drive it by hand
assert field_jobs.active()
bolt.location.x = -.3; bpy.context.view_layer.update()
dependencies.refresh(scene)
before_serial = ts.edit_serial
assert not ts.dirty, 'a live-field-only change must not invalidate the set synchronously'
assert field_jobs.pending(scene)
with Metrics() as counters:
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
assert counters.phases.get('scene_analysis', {}).get('calls', 0) == 0, 'the evaluation analysed synchronously'
stale = images.pixels(field.image)[..., 0].copy()
started = monotonic()
while field_jobs.pending(scene):
    field_jobs.tick(scene, budget=.004)
    assert monotonic() - started < 30
fresh = images.pixels(field.image)[..., 0]
assert not np.array_equal(stale, fresh) and fresh[:, :40].max() > .5, 'the job did not publish the moved bolt'
assert ts.dirty and ts.edit_serial > before_serial
assert not field.error
# A superseded job never publishes: request, start, move again, finish → only the newest lands.
bolt.location.x = .3; bpy.context.view_layer.update(); dependencies.refresh(scene)
field_jobs.tick(scene, budget=0)          # debounce not elapsed: nothing starts
assert not field_jobs.progress(scene)
field_jobs.DEBOUNCE = 0
field_jobs.tick(scene, budget=0)          # starts the job, first strip only
assert field_jobs.progress(scene) is not None
first_signature = field_jobs._jobs[next(iter(field_jobs._jobs))]['signature']
bolt.location.x = -.2; bpy.context.view_layer.update(); dependencies.refresh(scene)
assert not any(job['signature'] == first_signature for job in field_jobs._jobs.values()), 'the superseded job survived'
while field_jobs.pending(scene):
    field_jobs.tick(scene, budget=.004)
assert images.pixels(field.image)[..., 0][:, :40].max() > .5 and field.provenance == repr(scene_fields.signature(ts, field))
# Moving back to a published position discards the pending work: nothing to analyse.
bolt.location.x = .3; bpy.context.view_layer.update(); dependencies.refresh(scene)
field_jobs.tick(scene, budget=0)
bolt.location.x = -.2; bpy.context.view_layer.update(); dependencies.refresh(scene)
assert not field_jobs.pending(scene), 'a request survived for the raster already published'
# A normal-direction mesh mask on the layer carries the panel's placement in its own signature:
# moving the panel must still request the live field (the raster reads where it stands).
direction = layer.masks.add(); direction.id, direction.source = new_id(), 'MESH'
direction.mesh.kind = 'NORMAL_DIRECTION'
mask = layer.masks[0]; field = mask.influence   # masks.add() re-allocated the collection
panel.location.z = .1; bpy.context.view_layer.update(); dependencies.refresh(scene)
assert field_jobs.pending(scene), 'a mesh mask on the layer swallowed the live-field request'
while field_jobs.pending(scene):
    field_jobs.tick(scene, budget=.004)
assert field.provenance == repr(scene_fields.signature(ts, field))
layer.masks[1].enabled = False
panel.location.z = 0; bpy.context.view_layer.update(); dependencies.refresh(scene)
while field_jobs.pending(scene):
    field_jobs.tick(scene, budget=.004)
# Edit mode defers analysis; leaving it resumes.
bolt.location.x = .3; bpy.context.view_layer.update(); dependencies.refresh(scene)
bpy.context.view_layer.objects.active = panel
bpy.ops.object.mode_set(mode='EDIT')
field_jobs.tick(scene, budget=.004)
assert field_jobs.pending(scene) and field_jobs.progress(scene) is None
bpy.ops.object.mode_set(mode='OBJECT')
while field_jobs.pending(scene):
    field_jobs.tick(scene, budget=.004)
assert images.pixels(field.image)[..., 0][:, 80:].max() > .5
# Manual analysis stays synchronous.
field.mode = 'MANUAL'
with Metrics() as counters:
    scene_fields.update(ts, mask)
assert counters.phases['scene_analysis']['calls'] == 1
print('LIVE_FIELDS job engine: PASS', flush=True)

# 4. A scene-scope provenance names the members by name, placement and update epoch, never
# by content: the scope can hold every mesh of the scene and the signature is asked for on
# the depsgraph tick, so it hashes no member (the field's own object keeps its record). The
# epoch is unique for the session, so a redo never reads as the members an undo published;
# reopening the file (``dependencies.clear``) keeps a member published untouched current.
field.mode, field.scope, field.distance = 'LIVE', 'SCENE', .3
dependencies.refresh(scene)
while field_jobs.pending(scene):
    field_jobs.tick(scene, budget=.004)
scene_fields.update(ts, mask)   # publish the current members synchronously
assert repr(scene_fields.signature(ts, field)) == field.provenance
with Metrics() as counters:
    influencers.signature(ts, field)
assert counters.phases.get('geometry_signature', {}).get('calls', 0) == 1, \
    'a scene-scope signature hashed its members (only the field object, the bolt, is hashed)'


def edit_block(z):
    before = dependencies.mesh_epoch(block.data)
    for vertex in block.data.vertices:
        if vertex.co.z < 0:
            vertex.co.z = z
    block.data.update(); bpy.context.view_layer.update()   # the edit's depsgraph update stamps the mesh
    assert dependencies.mesh_epoch(block.data) > before, 'the edit reported no geometry update'


edit_block(-.6)                                                    # the edit: the block's bottom reaches through the panel
dependencies.refresh(scene)
assert field_jobs.pending(scene), 'a member mesh edit did not request the field'
while field_jobs.pending(scene):
    field_jobs.tick(scene, budget=.004)
edited, edited_provenance = images.pixels(field.image)[..., 0].copy(), field.provenance
# A reopen: the epochs restart, so a member edited in the publishing session costs one
# analysis (its content is not in the token) that lands on the same raster ...
dependencies.clear()                                               # what load_post, undo_post and redo_post run
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
while field_jobs.pending(scene):
    field_jobs.tick(scene, budget=.004)
np.testing.assert_allclose(images.pixels(field.image)[..., 0], edited, atol=1e-6)
assert field.provenance != edited_provenance
# ... after which a member published untouched keeps its identity: no work on the next reopen.
dependencies.clear()
with Metrics() as counters:
    assert repr(scene_fields.signature(ts, field)) == field.provenance, 'a reopened file re-analysed untouched members'
assert counters.phases.get('geometry_signature', {}).get('calls', 0) == 2, 'a reopen hashed the members'   # the panel and the bolt
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
assert not field_jobs.pending(scene), 'a reopened file requested an analysis for the published members'
dependencies.clear(); edit_block(-.15)                             # undo: the epochs restart, one update re-stamps the mesh
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)      # the evaluation after an undo step re-requests
assert field_jobs.pending(scene)
while field_jobs.pending(scene):
    field_jobs.tick(scene, budget=.004)
undone, undone_provenance = images.pixels(field.image)[..., 0].copy(), field.provenance
assert not np.array_equal(undone, edited)
dependencies.clear(); edit_block(-.6)                              # redo: the same single update, the edited content again
assert repr(scene_fields.signature(ts, field)) != undone_provenance, 'the redo read as the undone members'
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
assert field_jobs.pending(scene), 'the redo did not request the field'
while field_jobs.pending(scene):
    field_jobs.tick(scene, budget=.004)
np.testing.assert_allclose(images.pixels(field.image)[..., 0], edited, atol=1e-6)
# The epoch stamps themselves never repeat within a session: the counter behind them
# survives the undo and redo that empty the per-mesh map.
stamps = []
for step in range(2):                                              # undo, then redo: one reported update each
    dependencies.clear()
    for vertex in block.data.vertices:
        vertex.co.x += .01
    block.data.update(); bpy.context.view_layer.update()
    stamps.append(dependencies.mesh_epoch(block.data))
assert stamps[0] and stamps[1] > stamps[0], stamps
field.mode = 'MANUAL'
bpy.app.timers.unregister(cache.tick)
print('LIVE_FIELDS persisted identity: PASS', flush=True)
print('LIVE_FIELDS_PASS', flush=True)
open_painter.unregister()
