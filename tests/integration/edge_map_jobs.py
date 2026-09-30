"""The background edge-map rebake as a job: the generator survives texture-set
add/remove between steps, releases partial reaches when stopped or failed, and
the landing registers on the recorded scene with its own undo step."""
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.baking.bake_manager import register_map, stale_curvature
from open_painter.baking.jobs import land_edge_maps
from open_painter.baking.maps.curvature import EDGE_MAP_KINDS, bake_curvature_set, curvature_steps, edge_map_set_complete
from open_painter.core import image_manager as images, texture_set_manager as sets
from open_painter.geometry.provenance import record

open_painter.register()
SCENE = bpy.context.scene.name
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=2)
cube = bpy.context.object
bevel = cube.modifiers.new('rims', 'BEVEL')
bevel.width, bevel.segments = .12, 3
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=.02)
bpy.ops.object.mode_set(mode='OBJECT')
filler = sets.create(scene, 'Filler', 512)
ts = sets.create(scene, 'Edge jobs', 512)
sets.assign(scene, ts, cube)
ts.bake.device = 'CPU'
SET_ID = ts.id


def texture_set():
    return next(item for item in bpy.data.scenes[SCENE].open_painter.texture_sets if item.id == SET_ID)


def curvature_images():
    return {image.name for image in bpy.data.images if 'BakedCurvature' in image.name}


# The panel button must not inherit the set the auto rebake last targeted.
assert bpy.ops.open_painter.edge_map_refresh.get_rna_type().properties['set_id'].is_skip_save, \
    'set_id is stored as a last-used property'

# Adding and removing sets while the job runs reallocates the collection under
# the generator's reference; the images are still named after the right set.
job = curvature_steps(bpy.context, ts)
next(job)
for index in range(24):
    sets.create(scene, f'Added {index}', 512)
sets.remove(scene, next(i for i, item in enumerate(scene.open_painter.texture_sets) if item.name == 'Filler'))
while True:
    try:
        next(job)
    except StopIteration as done:
        produced = done.value
        break
assert tuple(produced) == EDGE_MAP_KINDS
for image in produced.values():
    assert image.name.startswith('Edge jobs_BakedCurvature'), image.name
for image in produced.values():
    bpy.data.images.remove(image)
while len(scene.open_painter.texture_sets) > 1:
    sets.remove(scene, len(scene.open_painter.texture_sets) - 1)
ts = texture_set()
print('EDGE_MAP_JOBS set add/remove mid-job: PASS', flush=True)

# Stopping the job after some reaches landed releases the packed images.
before = curvature_images()
job = curvature_steps(bpy.context, ts)
while len(curvature_images()) < len(before) + 2:
    next(job)
job.close()
assert curvature_images() == before, curvature_images() - before
# A failure part-way releases them too.
from open_painter.baking import cycles_baker
original_run = cycles_baker._run_bake
calls = []


def failing_run(**kwargs):
    calls.append(kwargs.get('type'))
    if len(calls) == 2:
        raise RuntimeError('out of memory')
    return original_run(**kwargs)


cycles_baker._run_bake = failing_run
try:
    bake_curvature_set(ts)
    raise AssertionError('the failing bake did not raise')
except RuntimeError:
    pass
finally:
    cycles_baker._run_bake = original_run
assert len(calls) == 2 and curvature_images() == before, (calls, curvature_images() - before)
print('EDGE_MAP_JOBS stop/failure releases partial reaches: PASS', flush=True)

# Landing: the crease-only map is replaced on the recorded scene even while
# another scene is current, and the maps survive an undo of a later edit.
n = 64
old = images.create(f'{ts.name}_BakedCurvature', n)
old['open_painter_curvature_method'] = 'GEOMETRIC_CREASES_V1'
images.write(old, __import__('numpy').zeros((n, n, 4), 'float32'), pack=False)
register_map(ts, 'CURVATURE', old)
old_name = old.name
assert stale_curvature(ts) is not None
other = bpy.data.scenes.new('Other')
other.open_painter.status = 'untouched'
bpy.ops.ed.undo_push(message='A')
ts = texture_set()
expected = record(ts, 'CURVATURE')
produced = bake_curvature_set(ts)
with bpy.context.temp_override(scene=other):
    status = land_edge_maps(scene.name, SET_ID, expected, produced)
assert status.startswith('Edge maps rebaked'), status
assert scene.open_painter.status == status and other.open_painter.status == 'untouched'
ts = texture_set()
assert edge_map_set_complete(ts) and stale_curvature(ts) is None
landed = {m.kind: m.image.name for m in ts.baked_maps if m.kind in EDGE_MAP_KINDS}
assert landed['CURVATURE'] == old_name
assert old.name != old_name
# The superseded map is released, not removed (0.9.7): no fake user and nothing
# binding it, so the next save purges it, while an undo of the rebake still finds
# its pixels (a removed generated datablock comes back blank from the memfile).
superseded = old
assert not superseded.use_fake_user and superseded.users == 0, (superseded.users, superseded.use_fake_user)
cube = bpy.data.objects['Cube']
cube.location.x += 1
bpy.ops.ed.undo_push(message='B')
bpy.ops.ed.undo()
scene, other = bpy.data.scenes[SCENE], bpy.data.scenes['Other']  # memfile undo re-reads the scenes
ts = texture_set()
assert abs(bpy.data.objects['Cube'].location.x) < 1e-6, 'undo did not return to the landed-maps step'
assert {m.kind: m.image.name for m in ts.baked_maps if m.kind in EDGE_MAP_KINDS} == landed, \
    [(m.kind, m.image and m.image.name) for m in ts.baked_maps]
assert all(name in bpy.data.images for name in landed.values())
assert stale_curvature(ts) is None
print('EDGE_MAP_JOBS landing survives undo of a later edit: PASS', flush=True)

# Undoing the rebake itself brings the previous map back with its pixels.
ts = texture_set()
previous = images.create(f'{ts.name}_BakedCurvature', n)
images.write(previous, __import__('numpy').full((n, n, 4), .375, 'float32'), pack=False)
register_map(ts, 'CURVATURE', previous)
previous_name = previous.name
bpy.ops.ed.undo_push(message='before the rebake')
from open_painter.baking.bake_manager import register_maps
register_maps(ts, bake_curvature_set(ts))
ts = texture_set()
assert next(m.image for m in ts.baked_maps if m.kind == 'CURVATURE') != previous
assert not previous.use_fake_user and previous.users == 0
bpy.ops.ed.undo_push(message='rebaked')
assert bpy.ops.ed.undo() == {'FINISHED'}
scene = bpy.data.scenes[SCENE]
ts = texture_set()
restored = next(m.image for m in ts.baked_maps if m.kind == 'CURVATURE')
assert restored is not None and restored.name == previous_name and restored.use_fake_user
assert abs(float(images.pixels(restored).mean()) - .375) < 1e-6, "the undone rebake lost the previous map's pixels"
register_maps(ts, bake_curvature_set(ts))   # leave the set with a current map set
ts = texture_set()
print('EDGE_MAP_JOBS undo of a rebake restores the previous map: PASS', flush=True)

# Discards: a removed scene or changed inputs release the produced images.
ts = texture_set()
before = curvature_images()
produced = bake_curvature_set(ts)
assert land_edge_maps('No such scene', SET_ID, record(ts, 'CURVATURE'), produced).startswith('Scene removed')
assert curvature_images() == before
produced = bake_curvature_set(ts)
status = land_edge_maps(scene.name, SET_ID, 'stale record', produced)
assert status.startswith('Bake inputs changed') and scene.open_painter.status == status
assert curvature_images() == before

# A recipe added to a set whose map is stale while the job serves another set:
# the refresh is deferred to the scheduler's rebake tick, which waits for the
# job, instead of being reported as queued and forgotten (0.9.7).
from time import monotonic
from open_painter.baking.bake_manager import queue_edge_refresh
from open_painter.baking.jobs import OP_OT_edge_map_refresh
from open_painter.evaluation import cache, field_jobs
other_id = sets.create(scene, 'Other set', 512).id
ts = texture_set()
if not bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.register(cache.tick, first_interval=1000)
field_jobs.REBAKE_DEBOUNCE = 0
OP_OT_edge_map_refresh.running, OP_OT_edge_map_refresh.running_set_id = True, other_id
try:
    assert queue_edge_refresh(ts, scene) is True
    assert (scene.as_pointer(), SET_ID) in field_jobs._rebakes, 'the other set\'s job passed for this set\'s rebake'
    assert not field_jobs._tick_rebakes(scene, monotonic()) and (scene.as_pointer(), SET_ID) in field_jobs._rebakes, \
        'the deferred rebake did not wait for the running job'
    OP_OT_edge_map_refresh.running_set_id = SET_ID   # the job serves this set: queued, nothing deferred
    field_jobs.cancel_all()
    assert queue_edge_refresh(ts, scene) is True and not field_jobs._rebakes
    OP_OT_edge_map_refresh.running_set_id = other_id
    assert queue_edge_refresh(ts, scene) is True
    OP_OT_edge_map_refresh.running, OP_OT_edge_map_refresh.running_set_id = False, ''
    field_jobs._tick_rebakes(scene, monotonic())   # the job ended: the tick takes the request (invoking needs a window)
    assert (scene.as_pointer(), SET_ID) not in field_jobs._rebakes
finally:
    OP_OT_edge_map_refresh.running, OP_OT_edge_map_refresh.running_set_id = False, ''
    field_jobs.cancel_all()
    bpy.app.timers.unregister(cache.tick)
print('EDGE_MAP_JOBS deferred behind another set\'s job: PASS', flush=True)
print('EDGE_MAP_JOBS_PASS', flush=True)
open_painter.unregister()
