"""Structural edits recompile only their units, in the session's own graph (``EngineGraph.patch``).

A blend mode, a mask added, removed or retyped, an effect, a channel switched on or off, a
folder's composition, a bypass, a reference to another layer's mask: each is applied to the
graph and engine the session already holds, and what they render must equal a rebuild from
scratch. An edit that moves layers in their array still rebuilds. A numeric edit made while a
structural one is pending is read again by the patch. With the native engine, the replaced
units are described again without describing the others, and every incremental restamp is
checked against a full one.
"""
import sys
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images
from open_painter.core import layer_stack as stack
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.native_bridge import graph as native_graph
from open_painter.utils.ids import new_id

native_graph.VERIFY = True   # every incremental restamp is compared with a full one
open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
ts = sets.create(scene, 'Patched', 512)
sets.assign(scene, ts, bpy.data.objects['Cube'])
set_id = ts.id
for index, kind in enumerate(('CURVATURE', 'AO', 'WORLD_NORMAL', 'POSITION')):
    image = images.create(f'Patched {kind}', 64)
    values = np.full((64, 64, 4), .5, np.float32)
    values[..., 0] = np.linspace(0, 1, 64)[None, :] ** (index + 1)
    images.write(image, values)
    entry = ts.baked_maps.add()
    entry.kind, entry.image = kind, image


def current():
    return next(item for item in scene.open_painter.texture_sets if item.id == set_id)


def layer(identifier):
    return next(item for item in current().layers if item.id == identifier)


ids = []
for index in range(10):
    added = stack.add(current(), 'FILL', f'Fill {index}')
    stack.channel_for(added, 'BASE_COLOR').color = (index / 10, .3, 1 - index / 10, 1)
    stack.channel_for(added, 'ROUGHNESS').value = .1 + index * .07
    added.opacity = .7
    ids.append(added.id)
    mask = added.masks.add()
    mask.id = new_id()
    mask.source = 'NOISE' if index % 2 else 'GENERATOR'
    if mask.source == 'GENERATOR':
        mask.generator = 'DIRT'   # a CPU boundary capturing the layer
current().active_layer = len(current().layers) - 1
folder = stack.add(current(), 'FOLDER', 'Folder')
folder_id = folder.id
for index in range(3):
    child = stack.add(current(), 'FILL', f'Child {index}', parent_id=folder_id)
    stack.channel_for(child, 'BASE_COLOR').color = (.2, .8 - index * .2, .4, 1)
    child.opacity = .6
    ids.append(child.id)
stack.invalidate(current())


def session():
    return shader_preview._sessions[cache.key_for(scene, current())]


def settle():
    for _ in range(600):
        shader_preview.tick(scene, current())
        info = shader_preview.diagnostics(scene, current(), details=False)
        assert not info.get('error'), info
        if shader_preview.current(scene, current()) and not info.get('pending_resources'):
            return
    raise AssertionError(shader_preview.diagnostics(scene, current()))


def pixels():
    shader_preview.flush(scene, current(), pixels=True)
    return {out.channel: images.pixels(out.image).copy() for out in current().outputs if out.image is not None}


def described():
    engine = session()['engine']
    native = engine._native_graph
    return (native.described, native.reprogrammed) if native is not None else None


def check(label, edit, patched=True, limit=12):
    """Apply ``edit``; the session patches its graph (or rebuilds), and renders what a
    session built from scratch renders. A patch describes to the native graph only what it
    changed, layers added, removed and moved included (every other unit keeps its index)."""
    graph, engine = session()['graph'], session()['engine']
    before = described()
    edit()
    settle()
    after = session()['graph']
    report = after._last
    if patched:
        assert after is graph and session()['engine'] is engine, (label, 'rebuilt instead of patched')
        assert report.get('patched'), (label, report.get('patched'))
        assert report['rebuilt_units'] < limit, (label, report['rebuilt_units'], report.get('patched_units'))
        if before is not None:
            now = described()
            assert now[0] == before[0] and now[1] > before[1], (label, 'described every unit again', before, now)
    else:
        assert after is not graph, (label, 'patched an edit that needs a rebuild')
    followed = pixels()
    shader_preview.clear()
    settle()
    fresh = pixels()
    for channel, values in followed.items():
        difference = float(np.abs(values - fresh[channel]).max())
        assert difference < 1e-4, (label, channel, difference)
    print('STRUCTURAL_PATCH', label, 'PASS', report.get('rebuilt_units'), 'of', report.get('units'), flush=True)


settle()
pixels()

check('blend mode', lambda: setattr(layer(ids[4]), 'blend_mode', 'MULTIPLY'))
check('blend mode back', lambda: setattr(layer(ids[4]), 'blend_mode', 'NORMAL'))


def add_mask():
    mask = layer(ids[3]).masks.add()
    mask.id = new_id()
    mask.source = 'NOISE'
    mask.invert = True
check('mask added', add_mask)   # the new mask unit may change what is inlined: its consumers are described again


def remove_mask():
    layer(ids[3]).masks.remove(1)
    stack.invalidate(current())   # a collection edit has no callback: the operators invalidate
check('mask removed', remove_mask)
check('mask retyped', lambda: setattr(layer(ids[6]).masks[0], 'source', 'NOISE'))


def add_effect():
    effect = stack.channel_for(layer(ids[6]), 'ROUGHNESS').effects.add()
    effect.kind = 'LEVELS'
    effect.output_black, effect.output_white = .2, .6
check('effect added', add_effect)
check('channel off', lambda: setattr(stack.channel_for(layer(ids[7]), 'ROUGHNESS'), 'enabled', False))
check('channel on', lambda: setattr(stack.channel_for(layer(ids[7]), 'ROUGHNESS'), 'enabled', True))
# The folder's channels and its children's (their backdrop is no longer opaque).
check('folder passthrough', lambda: setattr(layer(folder_id), 'group_mode', 'PASSTHROUGH'), limit=24)
check('bypass', lambda: setattr(layer(ids[2]), 'bypass', True))
check('bypass off', lambda: setattr(layer(ids[2]), 'bypass', False))


def reference():
    mask = layer(ids[8]).masks.add()
    mask.id = new_id()
    mask.source, mask.reference_layer, mask.reference_channel = 'REFERENCE', ids[1], 'MASK'
check('reference added', reference)
check('referenced mask retyped', lambda: setattr(layer(ids[1]).masks[0], 'source', 'GENERATOR'))


def structural_and_numeric():
    # Both before one tick: the numeric edit arrives while the structural one is pending.
    layer(ids[4]).blend_mode = 'SCREEN'
    layer(ids[9]).opacity = .35
    stack.channel_for(layer(ids[0]), 'ROUGHNESS').value = .95
check('numeric edit while pending', structural_and_numeric)


# Layers added, removed and moved shift the layer structs above them in their array: the
# units of those layers are followed to their new addresses, not recompiled.
def insert():
    current().active_layer = next(i for i, item in enumerate(current().layers) if item.id == ids[4])
    stack.channel_for(stack.add(current(), 'FILL', 'Inserted'), 'BASE_COLOR').color = (.9, .1, .1, 1)
check('layer inserted', insert)
layer(ids[9]).opacity = .3   # a moved layer's bindings still answer
settle()
check('layer removed', lambda: stack.remove(current(), stack.index_of(current(), next(
    item.id for item in current().layers if item.name == 'Inserted'))))
check('layer moved', lambda: stack.move(current(), stack.index_of(current(), ids[3]), 3))


def insert_and_edit_above():
    # The edit of a layer above the insertion arrives while the patch is pending, and that
    # layer's struct moved: the patch reads it again at its new address.
    current().active_layer = next(i for i, item in enumerate(current().layers) if item.id == ids[2])
    stack.add(current(), 'FILL', 'Second insert')
    layer(ids[7]).opacity = .15
    stack.channel_for(layer(ids[8]), 'BASE_COLOR').color = (.1, .9, .3, 1)
check('insert, then edits of moved layers', insert_and_edit_above)


def burst():
    # Enough layers that the array grows: every layer struct moves.
    for index in range(12):
        current().active_layer = 2
        stack.add(current(), 'FILL', f'Burst {index}').opacity = .2
check('layers added until the array grew', burst, limit=120)
layer(ids[1]).opacity = .45
stack.channel_for(layer(ids[2]), 'ROUGHNESS').value = .75
settle()
check('a structural edit after the growth', lambda: setattr(layer(ids[5]), 'blend_mode', 'OVERLAY'))
print('STRUCTURAL_PATCH_OK', flush=True)
