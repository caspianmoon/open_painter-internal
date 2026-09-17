"""Texture set lifecycle (0.9.7): a set created after the target was chosen follows it, a
duplicate leaves its source clean, and a removed set or layer releases the images it alone
owns: unbound and without a fake user, so a save purges them while an undo finds their pixels."""
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

# ---- deleting a layer releases its painted images, undo brings their pixels back -------------
# A removed image datablock comes back from a memfile undo without its pixels
# (generated, unpacked): the release drops the fake user and leaves the datablock,
# so an undo finds the strokes and a save purges what nothing binds any more.
ts = find(set_id)
doomed_layer = stack.add(ts, 'PAINT', 'Doomed')
doomed_id = doomed_layer.id
doomed_canvas = target(ts, doomed_layer, 'BASE_COLOR')
strokes = np.zeros((512, 512, 4), np.float32)
strokes[..., :3], strokes[..., 3] = (.9, .1, .2), 1.
images.write(doomed_canvas, strokes, pack=False)
doomed_layer = next(l for l in ts.layers if l.id == doomed_id)
painted_mask = doomed_layer.masks.add()
painted_mask.id, painted_mask.source = 'painted-mask', 'PAINT'
painted_mask.image = images.create('Doomed mask', 512, (1, 1, 1, 1))
images.write(painted_mask.image, np.full((512, 512, 4), .5, np.float32), pack=False)
doomed_names = (doomed_canvas.name, painted_mask.image.name)
assert all(bpy.data.images[name].use_fake_user and bpy.data.images[name].users == 2 for name in doomed_names)
bpy.ops.ed.undo_push(message='before the layer goes')
stack.remove_tree(ts, doomed_id)
assert not any(l.id == doomed_id for l in ts.layers)
for name in doomed_names:
    released = bpy.data.images[name]
    assert not released.use_fake_user and released.users == 0, (name, released.users, released.use_fake_user)
bpy.ops.ed.undo_push(message='layer removed')
assert bpy.ops.ed.undo() == {'FINISHED'}
scene = bpy.context.scene; project = scene.open_painter   # memfile undo re-reads the scene
ts = find(set_id)
restored = next(l for l in ts.layers if l.id == doomed_id)
canvas_back = stack.channel_for(restored, 'BASE_COLOR').image
assert canvas_back is not None and canvas_back.name == doomed_names[0] and canvas_back.use_fake_user
assert np.allclose(images.pixels(canvas_back)[..., :3], (.9, .1, .2), atol=1e-6), 'the undone canvas lost its strokes'
assert np.allclose(images.pixels(restored.masks[0].image), .5, atol=1e-6), 'the undone painted mask lost its strokes'
stack.remove(ts, next(i for i, l in enumerate(ts.layers) if l.id == doomed_id))
assert all(not bpy.data.images[name].use_fake_user and bpy.data.images[name].users == 0 for name in doomed_names)
print('LAYER_REMOVE_RELEASES_OK', flush=True)

# ---- repeated rebakes keep at most one superseded generation per kind resident -------------------
ts = find(set_id)
from open_painter.baking import bake_manager
from open_painter.baking.bake_manager import register_map
from open_painter.baking.maps.curvature import EDGE_MAP_KINDS
generations = []
for step in range(4):   # the first bake, then three rebakes of the whole edge-map set
    produced = {kind: images.create(f'Hull_{kind}', 64) for kind in EDGE_MAP_KINDS}
    bake_manager.register_maps(ts, produced)
    generations.append({kind: image.name for kind, image in produced.items()})
    if step:
        bpy.ops.ed.undo_push(message=f'rebake {step}')
    for kind in EDGE_MAP_KINDS:
        released = [g[kind] for g in generations if g[kind] in bpy.data.images
                    and not bpy.data.images[g[kind]].users and not bpy.data.images[g[kind]].use_fake_user]
        assert len(released) <= 1, (step, kind, released)
        # The immediate predecessor stays for an undo of this rebake; the one before it is gone.
        if step:
            assert generations[-2][kind] in bpy.data.images, (step, kind)
        if step > 1:
            assert generations[-3][kind] not in bpy.data.images, (step, kind, generations[-3][kind])
live = {m.image.name for m in ts.baked_maps if m.kind in EDGE_MAP_KINDS}
assert live == set(generations[-1].values()) and all(bpy.data.images[n].use_fake_user for n in live)
# The save handler sweeps the remaining predecessor: nothing zero-user is left, the live maps stay.
cache.on_save(None)
ts = find(set_id)
for kind in EDGE_MAP_KINDS:
    assert generations[-2][kind] not in bpy.data.images, (kind, generations[-2][kind])
    assert generations[-1][kind] in bpy.data.images and bpy.data.images[generations[-1][kind]].users >= 1
assert not [g[kind] for g in generations for kind in EDGE_MAP_KINDS if g[kind] in bpy.data.images
            and not bpy.data.images[g[kind]].users and not bpy.data.images[g[kind]].use_fake_user]
# A predecessor the artist wired into their own material is not ours to sweep.
kept = images.create('Hull_CURVATURE', 64)
kept_name = kept.name
register_map(ts, 'CURVATURE', kept)
keeper = bpy.data.materials.new('Keeper')
keep_node = keeper.node_tree.nodes.new('ShaderNodeTexImage')
keep_node.image = bpy.data.images[generations[-1]['CURVATURE']]
register_map(ts, 'CURVATURE', images.create('Hull_CURVATURE', 64))
cache.on_save(None)
ts = find(set_id)
assert generations[-1]['CURVATURE'] in bpy.data.images and kept_name not in bpy.data.images
print('REBAKE_RETENTION_OK', flush=True)

# ---- removing a set releases the images it alone owns -------------------------------------------
ts = find(set_id)
baked = images.create('Hull_BakedNormal', 512)
register_map(ts, 'NORMAL', baked)
picked = bpy.data.images.new('Artist Normal', 64, 64)   # a hand-picked map carries no owner tag
register_map(ts, 'AO', picked)
own_material = bpy.data.materials.new('Artist Material')
own_material.use_fake_user = True
node = own_material.node_tree.nodes.new('ShaderNodeTexImage')
wired = next(o.image for o in ts.outputs if o.channel == 'ROUGHNESS')
node.image = wired   # an output the artist wired into their own material outlives the set
owned = {i.name for i in bpy.data.images if i.get(OWNER)}
canvas = stack.channel_for(next(l for l in ts.layers if l.type == 'PAINT'), 'ROUGHNESS').image
canvas_name, baked_name, wired_name = canvas.name, baked.name, wired.name
assert owned >= {canvas_name, baked_name, wired_name} and len(owned) >= 10, sorted(owned)
set_material = ts.material.name
bpy.ops.ed.undo_push(message='before the set goes')
sets.remove(scene, index_of(set_id))
remaining = {i.name for i in bpy.data.images}
assert remaining >= {canvas_name, baked_name, picked.name, wired_name}, sorted(remaining)
# Released, not removed: no fake user and nothing binding them, the set's own
# material included, so the next save drops them and an undo still finds them.
orphaned = [i for i in bpy.data.images if i.get('texture_set_id') == set_id and i.name != wired_name]
assert orphaned and all(not i.use_fake_user and i.users == 0 for i in orphaned), \
    [(i.name, i.users, i.use_fake_user) for i in orphaned]
for name in (canvas_name, baked_name):
    assert not bpy.data.images[name].use_fake_user and bpy.data.images[name].users == 0
assert not any(n.image is not None and n.image.get(OWNER)
               for n in bpy.data.materials[set_material].node_tree.nodes if n.bl_idname == 'ShaderNodeTexImage')
assert node.image == wired and wired.users >= 1 and not wired.use_fake_user
assert picked.name in remaining and picked.users == 0   # the artist's own map, untouched but unbound
# Undo of the removal: the set and its canvas come back with the paint on it.
bpy.ops.ed.undo_push(message='set removed')
assert bpy.ops.ed.undo() == {'FINISHED'}
scene = bpy.context.scene; project = scene.open_painter
ts = find(set_id)
canvas = stack.channel_for(next(l for l in ts.layers if l.type == 'PAINT'), 'ROUGHNESS').image
assert canvas is not None and canvas.name == canvas_name and canvas.use_fake_user
assert np.allclose(images.pixels(canvas)[..., :3], .25, atol=1e-6), 'the undone canvas lost its paint'
assert next(m.image for m in ts.baked_maps if m.kind == 'NORMAL').name == baked_name
sets.remove(scene, index_of(set_id))
assert all(not bpy.data.images[name].use_fake_user and bpy.data.images[name].users == 0
           for name in (canvas_name, baked_name))
# The copy shares nothing with its source: its own images go, and only its own.
copy = find(copy_id)
copy_canvas = next(c.image for c in next(l for l in copy.layers if l.type == 'PAINT').channels if c.image).name
assert copy_canvas != canvas_name
sets.remove(scene, index_of(copy_id))
assert not bpy.data.images[copy_canvas].use_fake_user and bpy.data.images[copy_canvas].users == 0
assert not [i for i in bpy.data.images if i.get('texture_set_id') == copy_id and (i.users or i.use_fake_user)]
assert not project.texture_sets
cache.tick()
print('TEXTURE_SET_REMOVE_OK', flush=True)

# ---- a save purges the released images; the artist's wired output survives ---------------------
path = ROOT / '.blender-test' / 'texture-set-lifecycle.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
names = {i.name for i in bpy.data.images}
gone = {canvas_name, baked_name, copy_canvas, *doomed_names}
assert not gone & names, sorted(gone & names)
assert not [i for i in bpy.data.images if i.get('texture_set_id') in {set_id, copy_id} and i.name != wired_name]
assert wired_name in names and bpy.data.images[wired_name].packed_file is not None
assert any(n.bl_idname == 'ShaderNodeTexImage' and n.image == bpy.data.images[wired_name]
           for n in bpy.data.materials['Artist Material'].node_tree.nodes)
print('TEXTURE_SET_PURGE_OK', flush=True)
print('TEXTURE_SET_LIFECYCLE_PASS', flush=True)
open_painter.unregister()
