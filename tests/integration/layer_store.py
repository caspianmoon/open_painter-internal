"""Layer stacks live in hidden Palette stores (core.layer_store), not on their Scene.

Blender copies a Scene into its evaluated copy after every edit of a property it owns,
every property included; with the stacks on the Scene each slider step copied the whole
project. The artist sees no difference: the stack reads, edits, undoes, saves, reloads,
copies and migrates exactly as before.
"""
import os
import sys
import tempfile
from pathlib import Path
from time import perf_counter

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, layer_store, owners
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
ts = sets.create(scene, 'Store', 512)
set_id = ts.id


def current():
    return next(item for item in bpy.context.scene.open_painter.texture_sets if item.id == set_id)


# A new set holds its stack in a hidden store of its own.
palette = current().layer_store
assert palette is not None and palette.name.startswith('.'), palette
assert layer_store.group(palette).set_id == set_id
for index in range(12):
    layer = stack.add(current(), 'FILL', f'Store fill {index}')
    mask = layer.masks.add()
    mask.source = 'NOISE'
layer = current().layers[5]
assert layer.id_data == palette and layer_store.scene_of(layer) == scene
assert owners.locate(layer.masks[0])[1].id == layer.id, 'the owner index finds structs in the store'
print('LAYER_STORE_NEW_OK', flush=True)

# Callbacks of stack structs reach their set through the store.
serial = current().edit_serial
current().layers[5].opacity = .4
current().layers[5].masks[0].invert = True
assert current().edit_serial == serial + 2, (serial, current().edit_serial)

# Paths are spelled from the Scene, as before the store: bindings and provenance keep them.
path = layer_store.scene_path(current().layers[5].masks[0])
assert path == f'{current().path_from_id()}.layers[5].masks[0]', path

# Editing the stack no longer makes Blender copy it with the Scene.
for index in range(300):
    stack.add(current(), 'FILL', f'Bulk {index}').opacity = .5
bpy.context.view_layer.update()
times = []
for step in range(30):
    started = perf_counter()
    current().layers[7].opacity = .01 * step
    bpy.context.view_layer.update()
    times.append((perf_counter() - started) * 1000)
times.sort()
assert times[len(times) // 2] < 2.0, f'an edit costs {times[len(times) // 2]:.2f} ms with the depsgraph'
print('LAYER_STORE_EDIT_OK', round(times[len(times) // 2], 3), 'ms', flush=True)

# Undo and redo restore the stack.
bpy.ops.ed.undo_push(message='before')
current().layers[3].name = 'Renamed'
bpy.ops.ed.undo_push(message='after')
bpy.ops.ed.undo()
assert current().layers[3].name == 'Store fill 3', current().layers[3].name
bpy.ops.ed.redo()
assert current().layers[3].name == 'Renamed'
print('LAYER_STORE_UNDO_OK', flush=True)

# Save and reload.
path = os.path.join(tempfile.mkdtemp(prefix='op-layer-store-'), 'store.blend')
count = len(current().layers)
bpy.ops.wm.save_as_mainfile(filepath=path)
bpy.ops.wm.open_mainfile(filepath=path)
scene = bpy.context.scene
assert len(current().layers) == count and current().layers[3].name == 'Renamed'
assert current().layers[5].masks[0].invert
print('LAYER_STORE_RELOAD_OK', flush=True)

# A file written before the store kept the stack on its set: it moves over unchanged.
before = [stack.snapshot(item) for item in current().layers]
raw = layer_store.group(current().layer_store).bl_system_properties_get()
current().bl_system_properties_get(do_create=True)[layer_store.LEGACY] = raw[layer_store.LEGACY]
old = current().layer_store
current().layer_store = None
bpy.data.palettes.remove(old)
layer_store.clear()
layer_store.ensure_all()
assert [stack.snapshot(item) for item in current().layers] == before, 'the migration changed the stack'
assert layer_store.LEGACY not in current().bl_system_properties_get()
print('LAYER_STORE_MIGRATE_OK', flush=True)

# A full copy of the Scene gets stores of its own: the projects stay independent. The
# original keeps its store even when the copy sorts first among the scenes.
kept = current().layer_store
bpy.ops.scene.new(type='FULL_COPY')
copy = bpy.context.scene
copy.name = 'AAA copy'
layer_store.maintain()
assert next(item for item in scene.open_painter.texture_sets if item.id == set_id).layer_store == kept
copied = next(item for item in copy.open_painter.texture_sets if item.id == set_id)
original = next(item for item in scene.open_painter.texture_sets if item.id == set_id)
assert copied.layer_store != original.layer_store and len(copied.layers) == len(original.layers)
copied.layers[3].name = 'Only in the copy'
assert original.layers[3].name == 'Renamed'
print('LAYER_STORE_COPY_OK', flush=True)

# Removing a set removes its store.

store_name = original.layer_store.name
sets.remove(scene, next(i for i, item in enumerate(scene.open_painter.texture_sets) if item.id == set_id))
assert store_name not in bpy.data.palettes, 'the removed set left its store behind'
print('LAYER_STORE_OK', flush=True)
