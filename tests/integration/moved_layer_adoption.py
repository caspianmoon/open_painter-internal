"""A layer inserted, removed or moved mid-stack moves every layer struct above it in memory.

Their compiled units are adopted and re-pointed at the moved structs instead of recompiled
(``Graph._follow_moved_layer``). What they render must equal a rebuild from scratch, and
edits of the moved layers must keep reaching their units.
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

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
ts = sets.create(scene, 'Moved layers', 512)
sets.assign(scene, ts, bpy.data.objects['Cube'])
set_id = ts.id
for index, kind in enumerate(('CURVATURE', 'AO', 'WORLD_NORMAL', 'POSITION')):
    image = images.create(f'Moved {kind}', 64)
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
for index in range(12):
    added = stack.add(current(), 'FILL', f'Fill {index}')
    stack.channel_for(added, 'BASE_COLOR').color = (index / 12, .3, 1 - index / 12, 1)
    stack.channel_for(added, 'ROUGHNESS').value = .1 + index * .05
    added.opacity = .6
    ids.append(added.id)
    mask = added.masks.add()
    if index % 3 == 0:
        mask.source = 'NOISE'
    elif index % 3 == 1:
        mask.source, mask.generator = 'GENERATOR', 'DIRT'    # a CPU boundary capturing the layer
    else:
        mask.source = 'TEXT'                                   # an image getter capturing the mask
        mask.text.body = f'L{index}'


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


def rebuilt():
    graph = shader_preview._sessions[cache.key_for(scene, current())]['graph']
    return graph._last['rebuilt_units'], graph._last['reused_units']


def matches_fresh(label):
    """The session's result equals a session built from scratch."""
    followed = pixels()
    shader_preview.clear()
    settle()
    fresh = pixels()
    for channel, values in followed.items():
        difference = float(np.abs(values - fresh[channel]).max())
        assert difference < 1e-4, (label, channel, difference)
    print('MOVED_LAYERS', label, 'PASS', flush=True)


settle()
pixels()

# Insert a fill above the fifth layer: every struct above it moves.
current().active_layer = next(i for i, item in enumerate(current().layers) if item.id == ids[4])
inserted = stack.add(current(), 'FILL', 'Inserted')
inserted_id = inserted.id
stack.channel_for(inserted, 'BASE_COLOR').color = (.9, .1, .1, 1)
stack.invalidate(current())
settle()
count, reused = rebuilt()
assert count < 10 and reused > 30, ('the moved layers were recompiled, not followed', count, reused)
layer(ids[9]).opacity = .25                         # a layer-level binding of a moved layer
stack.channel_for(layer(ids[10]), 'ROUGHNESS').value = .9
settle()
matches_fresh('insert')

# Remove it again: the layers above move back.
stack.remove(current(), next(i for i, item in enumerate(current().layers) if item.id == inserted_id))
settle()
count, reused = rebuilt()
assert count < 10, ('removal recompiled the moved layers', count, reused)
layer(ids[8]).opacity = .8
settle()
matches_fresh('remove')

# Move a layer past another: both swap places in the array.
stack.move(current(), next(i for i, item in enumerate(current().layers) if item.id == ids[6]), 1)
settle()
layer(ids[6]).opacity = .45
layer(ids[7]).opacity = .35
settle()
matches_fresh('move')
print('MOVED_LAYERS_OK', flush=True)
