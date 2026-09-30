"""Undo/redo: the engine session rebuilds from the restored stack and reuses its CPU inputs.

Memfile undo restores the Scene without property callbacks, so the session is dropped and
the next tick compiles the restored stack. The material never changes (it samples the
set's images through the Open Painter Texture Set node), the CPU input leaves an undone
state bound are adopted rather than regenerated, and the published images match the
state undo returned to.
"""
import json
from pathlib import Path
import sys
from time import monotonic, perf_counter

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import CHANNELS
from open_painter.core import image_manager as images, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.core.texture_set_node import NODE_ID
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.metrics import Metrics
from open_painter.smart_materials import composer


def ready(scene, ts):
    deadline = monotonic() + 60
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def published(scene, ts):
    """The set's channel images as the engine published them (pixels synchronised)."""
    shader_preview.flush(scene, ts)
    return {out.channel: images.pixels(out.image).copy() for out in ts.outputs if out.image is not None}


def same(a, b, atol=1e-6):
    return all(np.allclose(a[channel], b[channel], atol=atol) for channel in CHANNELS)


def material_shape(material):
    return sorted((node.bl_idname, node.get('role', '')) for node in material.node_tree.nodes)


def active_set(_=None):
    # Undo replaces the Scene datablock; always go through the live context.
    scene = bpy.context.scene
    return scene.open_painter.texture_sets[scene.open_painter.active_texture_set]


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
ts = sets.create(scene, 'Undo rebuild', 512)
sets.assign(scene, ts, bpy.context.object)
n = 32
u, v = np.meshgrid((np.arange(n, dtype=np.float32) + .5) / n, (np.arange(n, dtype=np.float32) + .5) / n)
for kind in ('CURVATURE', 'AO', 'POSITION', 'WORLD_NORMAL', 'THICKNESS', 'UV_BORDER'):
    entry = ts.baked_maps.add()
    entry.kind = kind
    entry.image = images.create('Undo ' + kind, n)
    data = np.ones((n, n, 4), np.float32)
    data[..., 0], data[..., 1], data[..., 2] = .1 + .8 * u, .1 + .8 * v, v
    images.write(entry.image, data, pack=False)
composer.apply(ts, 'Worn Painted Metal')
material_manager.repair(ts)
info = ready(scene, ts)
graph = shader_preview.ensure(scene, ts)
shape = material_shape(ts.material)
assert sum(kind == NODE_ID for kind, _ in shape) == 1 and len(shape) <= 10, shape
report = {'material': 'Worn Painted Metal', 'units': len(graph.units), 'nodes': graph.node_count}

# Two numeric edits as separate undo steps.
bpy.ops.ed.undo_push(message='before edits')
layer = next(item for item in ts.layers if item.type != 'FOLDER' and item.masks)
layer_id = layer.id
mask = layer.masks[0]
original = float(mask.amount)
mask.amount = original * .5 + .1
ready(scene, ts)
bpy.ops.ed.undo_push(message='first edit')
first = published(scene, ts)
next(item for item in active_set().layers if item.id == layer_id).masks[0].amount = original * .25 + .2
ready(scene, ts)
second = published(scene, ts)
assert not same(first, second), 'the second edit changed no channel'
bpy.ops.ed.undo_push(message='second edit')

# Undo: the handler drops the session; the next tick compiles the restored stack and adopts
# the CPU inputs it binds (no source is generated again).
started = perf_counter()
bpy.ops.ed.undo()
scene = bpy.context.scene
ts = active_set()
assert not shader_preview.current(scene, ts), 'undo did not clear the runtime session'
with Metrics() as observed:
    info = ready(scene, ts)
undo_ms = (perf_counter() - started) * 1000
assert info['graph_builds'] == 1, info
assert observed.counts['shader_resource_requests'] == 0, observed.report()
mask = next(item for item in ts.layers if item.id == layer_id).masks[0]
assert abs(mask.amount - (original * .5 + .1)) < 1e-6
assert same(published(scene, ts), first), 'undo published a state other than the one it restored'
assert material_shape(ts.material) == shape, 'undo changed the material'
report['undo_ms'] = round(undo_ms, 1)
report['undo_build_ms'] = round(info.get('build_ms', 0.0), 1)
report['undo_evaluate_ms'] = round(info['engine'].get('evaluate_ms', 0.0), 1)
print('SHADER_UNDO undo restores the published state: PASS', flush=True)

# The rebuilt session keeps binding, invalidation and partial rebuilds working.
builds = info['graph_builds'] + info['graph_patches']   # rebuilds, and patches of the graph in place
mask.amount = .77
info = shader_preview.diagnostics(scene, ts)
assert not info['rebuild_pending'] and info['updated_bindings'] == 1, info
assert info['affected_channels'], info
info = ready(scene, ts)
assert info['graph_builds'] + info['graph_patches'] == builds, info
target = next(item for item in ts.layers if item.id == layer_id)
target.blend_mode = 'MULTIPLY' if target.blend_mode != 'MULTIPLY' else 'SCREEN'
info = ready(scene, ts)
assert info['graph_builds'] + info['graph_patches'] == builds + 1 and info['reused_units'] > 0, info

# Undo then redo return to each state's own images.
bpy.ops.ed.undo_push(message='blend mode')
blended = published(scene, ts)
bpy.ops.ed.undo()
scene, ts = bpy.context.scene, active_set()
ready(scene, ts)
undone = published(scene, ts)
assert not same(undone, blended)
bpy.ops.ed.redo()
scene, ts = bpy.context.scene, active_set()
ready(scene, ts)
assert same(published(scene, ts), blended), 'redo published a state other than the one it restored'
print('SHADER_UNDO redo restores the published state: PASS', flush=True)

# An invalid slider range (a numeric state) survives undo as a recoverable error; the last
# valid images stay on screen, and fixing the range recovers (0.9.7).
scene, ts = bpy.context.scene, active_set()
ready(scene, ts)
target = next(item for item in ts.layers if item.id == layer_id)
prop = next(item for item in target.channels if item.enabled)
levels = prop.effects.add()
levels.kind = 'LEVELS'
ready(scene, ts)
bpy.ops.ed.undo_push(message='levels')
valid = published(scene, ts)
levels.black, levels.white = .8, .2
info = shader_preview.diagnostics(scene, ts)
assert info['state'] == 'FAILED' and 'White level' in info['error'], info
bpy.ops.ed.undo_push(message='invalid range')
next(item for item in ts.layers if item.id == layer_id).masks[0].amount = .3
bpy.ops.ed.undo_push(message='unrelated edit')
bpy.ops.ed.undo()
scene, ts = bpy.context.scene, active_set()
for _ in range(5):
    shader_preview.tick(scene, ts)
info = shader_preview.diagnostics(scene, ts)
assert info['state'] == 'FAILED' and 'White level' in info['error'], info
assert material_shape(ts.material) == shape, 'undo of an unrelated edit changed the material'
assert same({out.channel: images.pixels(out.image) for out in ts.outputs}, valid, atol=1e-3), \
    'a failed session replaced the last valid images'
effect = next(e for p in next(item for item in ts.layers if item.id == layer_id).channels
              for e in p.effects if e.kind == 'LEVELS')
effect.white = 1.
info = ready(scene, ts)
assert not info['error'], info
print('SHADER_UNDO invalid range survives undo: PASS', flush=True)

report['result'] = 'PASS'
destination = ROOT / '.blender-test' / 'shader-undo.json'
destination.parent.mkdir(exist_ok=True)
destination.write_text(json.dumps(report, indent=2))
print('SHADER_UNDO_PASS', json.dumps(report), flush=True)
open_painter.unregister()
