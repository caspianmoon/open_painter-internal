"""Texture set lifecycle (0.9.7): a set created after the target was chosen follows it, a
duplicate leaves its source clean, and a removed set takes the images it alone owns with it."""
import sys
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import OWNER
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import cache
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.painting.channel_paint import target

open_painter.register()
scene = bpy.context.scene
project = scene.open_painter
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
obj = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.uv.smart_project()
bpy.ops.object.mode_set(mode='OBJECT')


def find(identifier):
    return next(t for t in project.texture_sets if t.id == identifier)


def index_of(identifier):
    return next(i for i, t in enumerate(project.texture_sets) if t.id == identifier)


# ---- a set added after the target was picked follows it -----------------------------------------
project.target = 'UNREAL'
ts = sets.create(scene, 'Hull', 512)
assert ts.export.follow_target and ts.export.preset == 'UNREAL', ts.export.preset
ts.export.follow_target = False
ts.export.preset = 'GLTF'
ts.export.follow_target = True
assert ts.export.preset == 'UNREAL', 'switching Follow Project Target on adopts the target'
project.target = 'CUSTOM'
ts.export.preset = 'GLTF'
ts.export.follow_target = False
ts.export.follow_target = True
assert ts.export.preset == 'GLTF', 'CUSTOM leaves the set alone'
project.target = 'BLENDER'
assert ts.export.preset == 'BLENDER'
print('TEXTURE_SET_TARGET_OK', flush=True)

# ---- duplicating a set leaves the evaluated source clean ----------------------------------------
set_id = ts.id
sets.assign(scene, ts, obj)
ts.preview_backend = 'CPU'
fill = stack.add(ts, 'FILL', 'Colour')
stack.channel_for(fill, 'BASE_COLOR').color = (.2, .7, .3, 1.)
marks = stack.add(ts, 'PAINT', 'Marks')
canvas = target(ts, marks, 'ROUGHNESS')
values = np.zeros((512, 512, 4), np.float32)
values[..., :3], values[..., 3] = .25, 1.
images.write(canvas, values, pack=False)
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
ts = find(set_id)
assert not ts.dirty and not any(out.dirty for out in ts.outputs)
serial = ts.edit_serial
base = images.pixels(next(o.image for o in ts.outputs if o.channel == 'BASE_COLOR'))[..., :3].copy()
copy = sets.duplicate(scene, index_of(set_id))
copy_id = copy.id
ts = find(set_id)
assert not ts.dirty and ts.edit_serial == serial, (ts.dirty, ts.edit_serial - serial)
assert not any(out.dirty for out in ts.outputs) and not any(layer.dirty for layer in ts.layers)
assert all(c.layer_id == layer.id for layer in copy.layers for c in layer.channels)
assert copy.dirty and all(layer.dirty for layer in copy.layers), 'the copy still evaluates from scratch'
copy.preview_backend = 'CPU'
LayerEvaluator(scene).evaluate_texture_set(copy_id, force=True)
copy = find(copy_id)
copied = images.pixels(next(o.image for o in copy.outputs if o.channel == 'BASE_COLOR'))[..., :3]
assert np.abs(copied - base).max() < 1e-6
print('TEXTURE_SET_DUPLICATE_OK', flush=True)

# ---- removing a set releases the images it alone owns -------------------------------------------
ts = find(set_id)
from open_painter.baking.bake_manager import register_map
baked = images.create('Hull_BakedNormal', 512)
register_map(ts, 'NORMAL', baked)
picked = bpy.data.images.new('Artist Normal', 64, 64)   # a hand-picked map carries no owner tag
register_map(ts, 'AO', picked)
own_material = bpy.data.materials.new('Artist Material')
node = own_material.node_tree.nodes.new('ShaderNodeTexImage')
wired = next(o.image for o in ts.outputs if o.channel == 'ROUGHNESS')
node.image = wired   # an output the artist wired into their own material outlives the set
owned = {i.name for i in bpy.data.images if i.get(OWNER)}
canvas_name, baked_name, wired_name = canvas.name, baked.name, wired.name
assert owned >= {canvas_name, baked_name, wired_name} and len(owned) >= 10, sorted(owned)
before = len(bpy.data.images)
sets.remove(scene, index_of(set_id))
remaining = {i.name for i in bpy.data.images}
assert canvas_name not in remaining and baked_name not in remaining, sorted(remaining)
assert not [i.name for i in bpy.data.images if i.get('texture_set_id') == set_id and i.name != wired_name]
assert remaining >= {picked.name, wired_name}, sorted(remaining)
assert node.image == wired and wired.users >= 1
assert before - len(bpy.data.images) >= 9, (before, len(bpy.data.images))
# The copy shares nothing with its source: its own images go, and only its own.
copy = find(copy_id)
copy_canvas = next(c.image for c in next(l for l in copy.layers if l.type == 'PAINT').channels if c.image).name
assert copy_canvas != canvas_name
sets.remove(scene, index_of(copy_id))
assert copy_canvas not in {i.name for i in bpy.data.images}
assert not [i for i in bpy.data.images if i.get('texture_set_id') == copy_id]
assert not project.texture_sets
cache.tick()
print('TEXTURE_SET_REMOVE_OK', flush=True)
print('TEXTURE_SET_LIFECYCLE_PASS', flush=True)
open_painter.unregister()
