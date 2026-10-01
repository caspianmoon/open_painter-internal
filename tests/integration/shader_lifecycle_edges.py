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
from open_painter.evaluation.engine import runtime
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


def settled(scene, ts):
    """Whether one tick makes the set current; publication through pixels (Vulkan) spends a time
    budget per tick, so there the following ticks may finish it."""
    shader_preview.tick(scene, ts)
    for _ in range(100):
        if shader_preview.current(scene, ts) or not runtime.device().pixel_publish:
            break
        shader_preview.tick(scene, ts)
    return shader_preview.current(scene, ts)


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
prop.procedural, prop.procedural_scale = 'FBM', 9.0   # a catalog field: a uniform of its GPU program
mask = layer.masks.add()
mask.id, mask.source, mask.seed = new_id(), 'NOISE', 1   # legacy value noise: an isolated CPU raster
material_manager.repair(ts)
ready(scene, ts)

# --- Undo while a boundary job is still running -------------------------------
bpy.ops.ed.undo_push(message='settled')
ts = find(bpy.context.scene, ts_id)
next(l for l in ts.layers if l.id == layer_id).masks[0].seed = 7   # new noise identity: queues a raster job
info = shader_preview.diagnostics(bpy.context.scene, ts)
assert info['pending_resources'], info
bpy.ops.ed.undo_push(message='seed edit')
graph = shader_preview.ensure(bpy.context.scene, ts)
graph.step_resources(0)        # advance the job one strip, then undo underneath it
assert shader_preview.diagnostics(bpy.context.scene, ts)['pending_resources']
with Metrics() as observed:
    bpy.ops.ed.undo()
scene = bpy.context.scene
ts = find(scene, ts_id)
info = ready(scene, ts)
seed = next(l for l in ts.layers if l.id == layer_id).masks[0].seed
assert seed == 1, seed
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
assert next(l for l in ts.layers if l.id == layer_id).masks[0].seed == 7
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
# Each material reads its own set's images through its own texture set node tree.
from open_painter.core.texture_set_node import NODE_ID
node_of = lambda material: next(n for n in material.node_tree.nodes if n.bl_idname == NODE_ID)
assert node_of(original_material).node_tree != node_of(copy_ts.material).node_tree
images_of = lambda item: {out.image.as_pointer() for out in item.outputs if out.image}
assert images_of(ts) and not images_of(ts) & images_of(copy_ts), 'the copy writes the original set\'s images'
copy_layer = next(l for l in copy_ts.layers if l.id == layer_id)
copy_layer.opacity = .37
assert settled(copy, copy_ts) and shader_preview.current(scene, ts)
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
assert sum(node.bl_idname == NODE_ID for node in original_material.node_tree.nodes) == 1
next(l for l in ts.layers if l.id == layer_id).opacity = .6
assert settled(scene, ts)
bpy.data.scenes.remove(linked)
cache.tick()
assert shader_preview.current(bpy.context.scene, find(bpy.context.scene, ts_id))
print('LIFECYCLE_EDGE linked scene copy: PASS', flush=True)

# --- The status line: messages from other subsystems outlive shader ticks (0.9.7).
scene = bpy.context.scene
ts = find(scene, ts_id)
ready(scene, ts)
scene.open_painter.status = 'Layer cache failed: probe'
for _ in range(3):
    cache.tick()
    shader_preview.tick(scene, ts)
assert scene.open_painter.status == 'Layer cache failed: probe', scene.open_painter.status
noisy = next(l for l in ts.layers if l.id == layer_id)
noisy.opacity = .8   # a numeric edit changes nothing the engine would report
shader_preview.tick(scene, ts)
assert scene.open_painter.status == 'Layer cache failed: probe', scene.open_painter.status
noisy.blend_mode = 'MULTIPLY'   # the engine's own text changes: it is written again
assert scene.open_painter.status == 'Updating textures…', scene.open_painter.status
ready(scene, ts)
assert scene.open_painter.status == 'Open Painter engine · live', scene.open_painter.status
cache._set_progress(scene, 'Caching probe… 50%')   # a finished job's progress line is handed back
cache.tick()
assert scene.open_painter.status == 'Open Painter engine · live', scene.open_painter.status
print('LIFECYCLE_EDGE status line: PASS', flush=True)

# --- A numeric-only batch must not service a rebuild queued before it (0.9.7).
noisy = next(l for l in ts.layers if l.id == layer_id)
noisy.bypass = True
assert shader_preview.diagnostics(scene, ts)['rebuild_pending']
with shader_preview.authoring_batch(ts):
    noisy.opacity = .5
info = shader_preview.diagnostics(scene, ts)
assert info['rebuild_pending'] and not shader_preview.current(scene, ts), info
ready(scene, ts)
graph = shader_preview.ensure(scene, ts)
assert graph.matches_structure()
assert any(key[1] == layer_id and unit.flags.get('bypass') for key, unit in graph.units.items()), sorted(graph.units)
noisy.bypass = False
noisy.opacity = 1.
ready(scene, ts)
print('LIFECYCLE_EDGE batch after structural edit: PASS', flush=True)

# --- texture_sets.add()/remove() relocate the set structs: a session follows its set (0.9.7).
bpy.ops.mesh.primitive_cube_add(size=1)
second = sets.create(scene, 'Second object')   # native, no layers: the natural add-then-delete-the-old-one flow
sets.assign(scene, second, bpy.context.object)
second_id = second.id
ready(scene, second)
first = find(scene, ts_id)
ready(scene, first)   # a reallocated array is followed, never read through the old address
assert shader_preview.ensure(scene, first).ts.as_pointer() == first.as_pointer()
sets.remove(scene, next(i for i, item in enumerate(scene.open_painter.texture_sets) if item.id == ts_id))
second = find(scene, second_id)
live = shader_preview.session_graph(scene, second)
assert live is None or live.ts.as_pointer() == second.as_pointer(), 'the session still reads the deleted slot'
cache.tick()
fill = stack.add(second, 'FILL', 'Colour')
fill_id = fill.id
stack.channel_for(fill, 'BASE_COLOR').color = (.2, .7, .3, 1.)
info = ready(scene, second)
graph = shader_preview.ensure(scene, second)
assert graph.ts.as_pointer() == second.as_pointer()
assert any(key[1] == fill_id for key in graph.units), sorted(graph.units)
assert graph.matches_structure(), info
# A removal that bypasses the manager (export/atlas.py) heals on the next tick.
third = sets.create(scene, 'Third')
third_id = third.id
base = stack.add(third, 'FILL', 'Base')
base_id = base.id
stack.channel_for(base, 'BASE_COLOR').color = (.4, .4, .1, 1.)
info = ready(scene, third)
builds = info['graph_builds']
scene.open_painter.texture_sets.remove(next(i for i, item in enumerate(scene.open_painter.texture_sets) if item.id == second_id))
third = find(scene, third_id)
assert shader_preview.session_graph(scene, third) is None, 'a moved graph was handed out'
assert not shader_preview.current(scene, third)
info = ready(scene, third)
graph = shader_preview.ensure(scene, third)
assert graph.ts.as_pointer() == third.as_pointer() and graph.matches_structure()
assert info['graph_builds'] == builds + 1, info   # the relocated set compiles again
print('LIFECYCLE_EDGE relocated texture sets: PASS', flush=True)

# --- A failed replacement build keeps its rebuild requirement (0.9.7).
from unittest.mock import patch
from open_painter.evaluation.shader_graph import Graph
original_build = Graph.build
attempts = []


def failing_build(self, reuse=None):
    attempts.append(reuse)
    if len(attempts) == 1:
        raise RuntimeError('simulated compile failure')
    return original_build(self, reuse=reuse)


from open_painter.evaluation.engine.compiler import EngineGraph
# A layer added is patched into the session's graph; declined here, it is rebuilt.
with patch.object(Graph, 'build', failing_build), patch.object(EngineGraph, 'patch', lambda self, **_: None):
    extra = stack.add(third, 'FILL', 'Extra')
    extra_id = extra.id
    for _ in range(3):
        shader_preview.tick(scene, third)
    info = shader_preview.diagnostics(scene, third)
    assert info['state'] == 'FAILED' and 'simulated' in info['error'] and info['rebuild_pending'], info
    assert len(attempts) == 1, 'a parked failure was retried without an edit'
    stack.channel_for(next(l for l in third.layers if l.id == base_id), 'BASE_COLOR').color = (.5, .5, .5, 1.)
    info = shader_preview.diagnostics(scene, third)
    assert not shader_preview.current(scene, third) and info['rebuild_pending'], info
    assert 'simulated' in info['error'], 'a numeric edit masked the failed build'
    info = ready(scene, third)
assert len(attempts) == 2, attempts
graph = shader_preview.ensure(scene, third)
assert any(key[1] == extra_id for key in graph.units) and graph.matches_structure()
print('LIFECYCLE_EDGE failed replacement build: PASS', flush=True)

# --- A patch whose compile fails puts its graph back; the rebuild then applies the edit (0.14).
original_compile = Graph._compile
failures = []


def failing_compile(self, key, layer, stub=None, bypass=False):
    if not failures:
        failures.append(key)
        raise ValueError('simulated unit failure')
    return original_compile(self, key, layer, stub=stub, bypass=bypass)


graph = shader_preview.ensure(scene, third)
kept = {key: unit.tree for key, unit in graph.units.items()}
with patch.object(Graph, '_compile', failing_compile):
    next(l for l in third.layers if l.id == extra_id).blend_mode = 'MULTIPLY'
    info = ready(scene, third)
assert failures, 'the patch compiled nothing'
rebuilt = shader_preview.ensure(scene, third)
assert rebuilt is not graph and rebuilt.matches_structure(), 'the failed patch was not rebuilt'
# The rebuild adopted what the failed patch put back: every unit of another layer, intact.
assert all(rebuilt.units[key].tree is tree for key, tree in kept.items() if key[1] != extra_id), \
    'the failed patch changed its graph'
print('LIFECYCLE_EDGE failed patch: PASS', flush=True)
print('SHADER_LIFECYCLE_EDGES_PASS', flush=True)
open_painter.unregister()
