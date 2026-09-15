"""Lifecycle edges for the shader session: undo/redo with a job in flight and a
fully copied scene sharing texture-set ids."""
from pathlib import Path
import sys
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.metrics import Metrics
from open_painter.utils.ids import new_id


def ready(scene, ts, seconds=60):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def find(scene, ts_id):
    return next(ts for ts in scene.open_painter.texture_sets if ts.id == ts_id)


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
ts = sets.create(scene, 'Lifecycle edges', 512)
sets.assign(scene, ts, bpy.context.object)
ts_id = ts.id
layer = stack.add(ts, 'FILL', 'Noisy')
layer_id = layer.id
prop = stack.channel_for(layer, 'ROUGHNESS')
prop.procedural, prop.procedural_scale = 'FBM', 9.0   # a legacy field: an isolated raster boundary
mask = layer.masks.add()
mask.id, mask.source, mask.procedural = new_id(), 'PROCEDURAL', 'VORONOI'
material_manager.repair(ts)
ready(scene, ts)

# --- Undo while a boundary job is still running -------------------------------
bpy.ops.ed.undo_push(message='settled')
ts = find(bpy.context.scene, ts_id)
prop = stack.channel_for(next(l for l in ts.layers if l.id == layer_id), 'ROUGHNESS')
prop.procedural_seed = 7   # new field identity: queues a raster job
info = shader_preview.diagnostics(bpy.context.scene, ts)
assert info['pending_resources'], info
bpy.ops.ed.undo_push(message='seed edit')
graph = shader_preview.ensure(bpy.context.scene, ts)
graph.step_resources(.01)      # advance the job partially, then undo underneath it
assert shader_preview.diagnostics(bpy.context.scene, ts)['pending_resources']
with Metrics() as observed:
    bpy.ops.ed.undo()
scene = bpy.context.scene
ts = find(scene, ts_id)
info = ready(scene, ts)
prop = stack.channel_for(next(l for l in ts.layers if l.id == layer_id), 'ROUGHNESS')
assert prop.procedural_seed == 1, prop.procedural_seed
assert not info['error'] and not info['pending_resources'], info
boundaries = [b for b in shader_preview.ensure(scene, ts).resource_bindings]
assert all(b.node.image and b.node.image.get('op_shader_resource_ready') for b in boundaries), 'a stale placeholder survived undo'
# The undone edit's unfinished resource must not linger as a leaked image.
leaked = [i for i in bpy.data.images if i.get('open_painter_shader_resource') and not i.get('op_shader_resource_ready') and not i.users]
assert not leaked, [i.name for i in leaked]
bpy.ops.ed.redo()
scene = bpy.context.scene
ts = find(scene, ts_id)
info = ready(scene, ts)
prop = stack.channel_for(next(l for l in ts.layers if l.id == layer_id), 'ROUGHNESS')
assert prop.procedural_seed == 7
assert all(b.node.image.get('op_shader_resource_ready') for b in shader_preview.ensure(scene, ts).resource_bindings)
print('LIFECYCLE_EDGE undo during pending job: PASS', flush=True)

# --- Undo while a CPU reference job is in flight ----------------------------------
ts.preview_backend = 'CPU'
cache._pending[cache.key_for(scene, ts)] = (ts.edit_serial, monotonic() - 1)
cache.tick()
assert cache.key_for(scene, ts) in cache._jobs, 'no CPU job started'
bpy.ops.ed.undo_push(message='cpu started')
bpy.ops.ed.undo()
scene = bpy.context.scene
ts = find(scene, ts_id)
assert cache.key_for(scene, ts) not in cache._jobs, 'undo left a CPU job referencing the old scene'
deadline = monotonic() + 60
while ts.dirty and monotonic() < deadline:
    cache.tick()
    ts = find(bpy.context.scene, ts_id)
assert not ts.dirty or ts.preview_backend == 'LIVE', cache.job_state(bpy.context.scene, ts)
ts.preview_backend = 'LIVE'
ready(bpy.context.scene, ts)
print('LIFECYCLE_EDGE undo during CPU job: PASS', flush=True)

# --- Scene copies: a full copy separates its material on repair; a linked copy
# that shares object slots reports an explicit error and leaves the original alone.
scene = bpy.context.scene
ts = find(scene, ts_id)
serial = ts.edit_serial
original_graph = shader_preview.ensure(scene, ts)
original_material = ts.material
bpy.ops.scene.new(type='FULL_COPY')
copy = bpy.context.scene
copy_ts = find(copy, ts_id)
assert copy_ts.material == original_material, 'expected the raw copy to share the material until repaired'
material_manager.repair(copy_ts)
assert copy_ts.material != original_material and ts.material == original_material
for _ in range(3):
    cache.tick()
ready(copy, copy_ts)
assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
assert shader_preview.ensure(scene, ts) is original_graph, (shader_preview.ensure(scene, ts), original_graph, shader_preview.diagnostics(scene, ts))
copy_graph = shader_preview.ensure(copy, copy_ts)
assert copy_graph is not original_graph and copy_graph.tree != original_graph.tree
assert sum(node.get('role') == 'Procedural Graph' for node in original_material.node_tree.nodes) == 1
copy_layer = next(l for l in copy_ts.layers if l.id == layer_id)
copy_layer.opacity = .37
assert shader_preview.current(copy, copy_ts) and shader_preview.current(scene, ts)
assert ts.edit_serial == serial, 'an edit in the copy invalidated the original scene'
original_layer = next(l for l in ts.layers if l.id == layer_id)
opacity = [b for b in original_graph.bindings.values() if b.field == 'opacity' and b.owner == original_layer]
assert opacity and all(abs(b.socket.default_value - 1.0) < 1e-6 for b in opacity)
copy_opacity = [b for b in copy_graph.bindings.values() if b.field == 'opacity' and b.owner == copy_layer]
assert copy_opacity and all(abs(b.socket.default_value - .37) < 1e-6 for b in copy_opacity)
bpy.data.scenes.remove(copy)
cache.tick()
scene = bpy.context.scene
ts = find(scene, ts_id)
assert shader_preview.current(scene, ts) and shader_preview.ensure(scene, ts) is original_graph
print('LIFECYCLE_EDGE full scene copy: PASS', flush=True)

bpy.ops.scene.new(type='LINK_COPY')
linked = bpy.context.scene
linked_ts = find(linked, ts_id)
assert linked_ts.material == original_material
try:
    material_manager.repair(linked_ts)
except ValueError as error:
    assert 'share an object material slot' in str(error), error
else:
    raise AssertionError('a linked copy sharing object slots was repaired silently')
for _ in range(3):
    cache.tick()
info = shader_preview.diagnostics(linked, linked_ts)
assert info['state'] == 'FAILED' and 'share an object material slot' in info['error'], info
assert shader_preview.current(scene, ts) and shader_preview.ensure(scene, ts) is original_graph
assert sum(node.get('role') == 'Procedural Graph' for node in original_material.node_tree.nodes) == 1
next(l for l in ts.layers if l.id == layer_id).opacity = .6
assert shader_preview.current(scene, ts)
bpy.data.scenes.remove(linked)
cache.tick()
assert shader_preview.current(bpy.context.scene, find(bpy.context.scene, ts_id))
print('LIFECYCLE_EDGE linked scene copy: PASS', flush=True)
print('SHADER_LIFECYCLE_EDGES_PASS', flush=True)
open_painter.unregister()
