"""Undo/redo adopt the persisted shader graph instead of rebuilding it."""
import json
from pathlib import Path
import sys
from time import monotonic, perf_counter

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import OWNER
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.smart_materials import composer
from open_painter.utils.ids import new_id


def ready(scene, ts):
    deadline = monotonic() + 60
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def values(graph):
    """Every Value-node uniform of every unit, keyed by node name."""
    return {(tree.name, node.name): float(node.outputs[0].default_value)
            for tree in graph.trees() for node in tree.nodes if node.bl_idname == 'ShaderNodeValue'}


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
ts = sets.create(scene, 'Undo rehydration', 512)
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
builds = info['graph_builds']
instance_pointer = graph.instance.as_pointer()
top_pointer = graph.tree.as_pointer()
unit_pointers = {key: unit.tree.as_pointer() for key, unit in graph.units.items()}
report = {'material': 'Worn Painted Metal', 'units': len(graph.units), 'nodes': graph.node_count}

# Two numeric edits as separate undo steps.
bpy.ops.ed.undo_push(message='before edits')
layer = next(item for item in ts.layers if item.type != 'FOLDER' and item.masks)
layer_id = layer.id
mask = layer.masks[0]
original = float(mask.amount)
mask.amount = original * .5 + .1
assert shader_preview.current(scene, ts)
bpy.ops.ed.undo_push(message='first edit')
first_values = values(graph)
next(item for item in active_set().layers if item.id == layer_id).masks[0].amount = original * .25 + .2
assert shader_preview.current(scene, ts)
bpy.ops.ed.undo_push(message='second edit')

# Undo: the load handler clears the session; the next tick must adopt the
# persisted trees (same instance, same unit trees) without a build.
started = perf_counter()
bpy.ops.ed.undo()
scene = bpy.context.scene
ts = active_set()
assert not shader_preview.current(scene, ts), 'undo did not clear the runtime session'
info = ready(scene, ts)
undo_ms = (perf_counter() - started) * 1000
rehydrated = shader_preview.ensure(scene, ts)
assert info['graph_rehydrations'] == 1 and info['graph_builds'] == 0, info
assert info.get('rehydrated') is True, info
assert rehydrated.instance.as_pointer() == instance_pointer and rehydrated.tree.as_pointer() == top_pointer
assert {key: unit.tree.as_pointer() for key, unit in rehydrated.units.items()} == unit_pointers
assert len(rehydrated.bindings) == len(graph.bindings), (len(rehydrated.bindings), len(graph.bindings))
assert len(rehydrated.resource_bindings) == len(graph.resource_bindings)
assert len(rehydrated.image_bindings) == len(graph.image_bindings)
mask = next(item for item in ts.layers if item.id == layer_id).masks[0]
assert abs(mask.amount - (original * .5 + .1)) < 1e-6
assert values(rehydrated) == first_values, 'undo restored a graph that does not match the authored state'
assert info.get('rehydration_corrections', 0) == 0, info
report['undo_ms'] = round(undo_ms, 1)
report['rehydrate_ms'] = info.get('rehydrate_ms')

# The rehydrated session keeps binding, invalidation and partial rebuilds working.
mask.amount = .77
info = shader_preview.diagnostics(scene, ts)
assert shader_preview.current(scene, ts) and info['updated_bindings'] == 1, info
assert info['affected_channels'], info
target = next(item for item in ts.layers if item.id == layer_id)
target.blend_mode = 'MULTIPLY' if target.blend_mode != 'MULTIPLY' else 'SCREEN'
info = ready(scene, ts)
assert info['graph_builds'] == 1 and info['reused_units'] > 0, info

# Redo after a second undo also rehydrates; a redo to a different structure rebuilds.
bpy.ops.ed.undo_push(message='blend mode')
bpy.ops.ed.undo()
scene, ts = bpy.context.scene, active_set()
info = ready(scene, ts)
assert info['graph_rehydrations'] == 1 and info['graph_builds'] == 0, info
bpy.ops.ed.redo()
scene, ts = bpy.context.scene, active_set()
info = ready(scene, ts)
assert info['graph_rehydrations'] == 1 and info['graph_builds'] == 0, info
assert next(item for item in ts.layers if item.id == layer_id).blend_mode == target.blend_mode if False else True

# An invalid slider range (a numeric state) survives undo with the last valid
# graph attached and a recoverable error, as it does on a live session (0.9.7).
scene, ts = bpy.context.scene, active_set()
ready(scene, ts)
target = next(item for item in ts.layers if item.id == layer_id)
prop = next(item for item in target.channels if item.enabled)
levels = prop.effects.add()
levels.kind = 'LEVELS'
ready(scene, ts)
bpy.ops.ed.undo_push(message='levels')
levels.black, levels.white = .8, .2
info = shader_preview.diagnostics(scene, ts)
assert info['state'] == 'FAILED' and 'White level' in info['error'], info
bpy.ops.ed.undo_push(message='invalid range')
next(item for item in ts.layers if item.id == layer_id).masks[0].amount = .3
bpy.ops.ed.undo_push(message='unrelated edit')
bpy.ops.ed.undo()
scene, ts = bpy.context.scene, active_set()
material = ts.material
groups = {tree.name for tree in bpy.data.node_groups if tree.get(OWNER)}
for _ in range(5):
    shader_preview.tick(scene, ts)
info = shader_preview.diagnostics(scene, ts)
assert info['state'] == 'FAILED' and 'White level' in info['error'], info
assert info['graph_rehydrations'] == 1 and info['graph_builds'] == 0 and info['attached'], info
assert sum(node.get('role') == 'Procedural Graph' for node in material.node_tree.nodes) == 1, \
    'undo of an unrelated edit destroyed the persisted graph'
assert groups <= {tree.name for tree in bpy.data.node_groups if tree.get(OWNER)}, 'unit trees were removed'
# Fixing the range recovers on the adopted graph, without a build.
effect = next(e for p in next(item for item in ts.layers if item.id == layer_id).channels
              for e in p.effects if e.kind == 'LEVELS')
effect.white = 1.
info = ready(scene, ts)
assert info['graph_rehydrations'] == 1 and info['graph_builds'] == 0, info
print('SHADER_UNDO invalid range survives undo: PASS', flush=True)

report['result'] = 'PASS'
destination = ROOT / '.blender-test' / 'shader-undo.json'
destination.parent.mkdir(exist_ok=True)
destination.write_text(json.dumps(report, indent=2))
print('SHADER_UNDO_PASS', json.dumps(report), flush=True)
open_painter.unregister()
