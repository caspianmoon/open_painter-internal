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
from open_painter.core import layer_stack as stack
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
# A spatial filter: its node holds the effect's struct, which an undo moves (``_follow_undo``).
blurred = next(item for item in ts.layers if item.type != 'FOLDER' and not item.masks)
blur = stack.channel_for(blurred, 'BASE_COLOR').effects.add()
blur.kind, blur.radius = 'GAUSSIAN_BLUR', 3.
blurred_id = blurred.id
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

# Undo: the handler drops the session. The undo restored only a numeric value (the mask's
# amount), so the next tick has the retired graph follow the restored structs, keeping its
# engine (undo_follow); with OP_FOLLOW_NUMERIC_UNDO=0 it compiles the restored stack and adopts
# the CPU inputs it binds (no source is generated again).
started = perf_counter()
bpy.ops.ed.undo()
scene = bpy.context.scene
ts = active_set()
assert not shader_preview.current(scene, ts), 'undo did not clear the runtime session'
with Metrics() as observed:
    info = ready(scene, ts)
undo_ms = (perf_counter() - started) * 1000
followed = shader_preview.FOLLOW_NUMERIC_UNDO and shader_preview.UNDO_ADOPTION
assert (info['undo_follows'], info['graph_builds']) == ((1, 0) if followed else (0, 1)), info
assert observed.counts['shader_resource_requests'] == 0, observed.report()
mask = next(item for item in ts.layers if item.id == layer_id).masks[0]
assert abs(mask.amount - (original * .5 + .1)) < 1e-6
assert same(published(scene, ts), first), 'undo published a state other than the one it restored'
assert material_shape(ts.material) == shape, 'undo changed the material'
report['undo_ms'] = round(undo_ms, 1)
# The units the undo left unchanged come over from the graph it retired, re-pointed to the
# restored structs, with their engine states (programs and results): nothing to compile, carry
# or rebind. With OP_UNDO_ADOPTION=0 the rebuild compiles every unit instead, and the engine an
# undo dropped hands its results over: units whose content stamps still match are not rendered
# again, each carried result with its own program rebound to the rebuilt unit's tree.
report['undo_adopted'] = observed.counts.get('shader_undo_adopted_units', 0)
report['undo_carried'] = observed.counts.get('engine_carried_results', 0)
report['undo_rebound'] = observed.counts.get('engine_rebound_programs', 0)
report['undo_followed'] = observed.counts.get('shader_undo_followed_graphs', 0)
if followed:
    assert report['undo_followed'] == 1 and not report['undo_adopted'], report
elif shader_preview.UNDO_ADOPTION:
    assert report['undo_adopted'] > 0, 'the rebuild adopted no unit of the graph the undo retired'
else:
    assert report['undo_carried'] > 0, "the rebuilt session started without the dropped engine's results"
    assert report['undo_rebound'] > 0, 'no carried program was rebound to its rebuilt unit'

# Every struct an adopted unit's nodes hold is one of the restored set's (an RNA struct a node
# kept from before the undo would read freed memory).
graph = shader_preview.ensure(scene, ts)
live = {struct.as_pointer() for layer in ts.layers for prop in layer.channels
        for struct in (prop, *prop.effects)} | {struct.as_pointer() for layer in ts.layers for mask in layer.masks
                                               for struct in (mask, mask.effect, mask.influence, mask.mesh)}
held = [(key, node.bl_idname, value.as_pointer()) for key in graph._reused for node in graph.units[key].rna_nodes
        for name, value in node.__dict__.items() if isinstance(value, bpy.types.bpy_struct) and name != 'id_data']
assert all(pointer in live for _, _, pointer in held) and (held or not shader_preview.UNDO_ADOPTION), held
report['undo_filter_nodes'] = len(held)
# The re-pointed effect still answers its edits.
effect = stack.channel_for(next(item for item in ts.layers if item.id == blurred_id), 'BASE_COLOR').effects[0]
before_blur = published(scene, ts)
effect.radius = 6.
assert not same(published(scene, ts), before_blur), 'an edit of an adopted filter changed nothing'
effect.radius = 3.
assert same(published(scene, ts), before_blur), 'an adopted filter did not return to its radius'

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
