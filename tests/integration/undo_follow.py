"""Memfile undos the graph follows, bit for bit.

A memfile undo that restored only values the add-on edits as uniforms, or other values of some
layers with every collection's items in place (``undo_follow``), has the retired graph follow
the restored structs and keep its engine instead of a rebuild, patching those layers. Every such
undo and redo here (a layer's opacity and visibility, a channel's colour, a mask's and an
effect's values; a blend mode, an effect's kind, a channel switched on, alone or with a numeric
value) must be followed, keep the graph object, verify against a fresh compile (``verify_undo``)
and publish exactly the pixels of the state it returned to. An undo that added or removed a
mask, an effect or a layer rebuilds, and so does the second of two undos with no tick between
them. After a follow, numeric and structural edits still apply.
"""
import sys
from pathlib import Path
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
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


def current():
    """The set, read again: an undo replaces every wrapper."""
    scene = bpy.context.scene
    return scene, scene.open_painter.texture_sets[0]


def published():
    scene, ts = current()
    shader_preview.flush(scene, ts)
    return {out.channel: images.pixels(out.image).copy() for out in ts.outputs if out.image is not None}


def identical(label, expected, actual):
    assert expected.keys() == actual.keys(), label
    for channel, values in expected.items():
        differing = int(np.count_nonzero(values.view(np.uint32) != actual[channel].view(np.uint32)))
        assert differing == 0, f'{label}: {channel} differs in {differing} texels'


def layer(identifier):
    return next(item for item in current()[1].layers if item.id == identifier)


def counts():
    """(undos followed, graphs built) by the session since the last undo, which starts it over."""
    scene, ts = current()
    info = shader_preview.diagnostics(scene, ts)
    return info['undo_follows'], info['graph_builds']


def step(label, edit, followed, undo='undo', settle_between=True):
    """``edit`` as an undo step, undone (or, with ``undo='redo'``, undone and redone): the result
    must equal the state the step returned to, by the path ``followed`` says."""
    scene, ts = current()
    ready(scene, ts)
    bpy.ops.ed.undo_push(message=label + ' before')
    before = published()
    edit()
    scene, ts = current()
    ready(scene, ts)
    after = published()
    assert any(np.any(before[channel] != after[channel]) for channel in before), f'{label} changed nothing'
    bpy.ops.ed.undo_push(message=label)
    graph = shader_preview.ensure(scene, ts)
    bpy.ops.ed.undo()
    scene, ts = current()
    ready(scene, ts)
    expected, state = before, 'undo'
    if undo == 'redo':
        assert counts() == ((1, 0) if followed else (0, 1)), (label, 'undo', counts())
        graph = shader_preview.ensure(scene, ts)
        bpy.ops.ed.redo()
        scene, ts = current()
        ready(scene, ts)
        expected, state = after, 'redo'
    assert counts() == ((1, 0) if followed else (0, 1)), (label, state, counts())
    if followed:
        print('UNDO_FOLLOW', label, state, 'patched units', len(shader_preview.ensure(scene, ts)._last.get('patched_units', ())),
              flush=True)
    if followed:
        assert shader_preview.ensure(scene, ts) is graph, f'{label}: the {state} replaced the graph'
        shader_preview.ensure(scene, ts).verify_undo()
    else:
        assert shader_preview.ensure(scene, ts) is not graph, f'{label}: the {state} kept a graph it could not'
    identical(f'{label} ({state})', expected, published())
    print('UNDO_FOLLOW', label, state, 'followed' if followed else 'rebuilt', flush=True)


def main():
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    assert shader_preview.FOLLOW_NUMERIC_UNDO and shader_preview.UNDO_ADOPTION
    scene = bpy.context.scene
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    ts = sets.create(scene, 'Undo follow', 512)
    sets.assign(scene, ts, bpy.context.object)
    n = 32
    u, v = np.meshgrid((np.arange(n, dtype=np.float32) + .5) / n, (np.arange(n, dtype=np.float32) + .5) / n)
    for kind in ('CURVATURE', 'AO', 'POSITION', 'WORLD_NORMAL', 'THICKNESS', 'UV_BORDER'):
        entry = ts.baked_maps.add()
        entry.kind = kind
        entry.image = images.create('Undo follow ' + kind, n)
        data = np.ones((n, n, 4), np.float32)
        data[..., 0], data[..., 1], data[..., 2] = .1 + .8 * u, .1 + .8 * v, v
        images.write(entry.image, data, pack=False)
    composer.apply(ts, 'Worn Painted Metal')
    material_manager.repair(ts)
    fill = stack.add(ts, 'FILL', 'Top fill')
    fill_id = fill.id
    stack.channel_for(fill, 'BASE_COLOR').color = (.8, .3, .2, 1)
    fill.opacity = .6
    # A spatial filter over varying content: its node holds the effect's struct.
    blurred = next(item for item in ts.layers if item.type != 'FOLDER' and not item.masks and item.id != fill_id)
    blurred_id = blurred.id
    blur = stack.channel_for(blurred, 'BASE_COLOR').effects.add()
    blur.kind, blur.radius = 'GAUSSIAN_BLUR', 3.
    masked = next(item for item in ts.layers if item.type != 'FOLDER' and item.masks)
    masked_id = masked.id
    ready(scene, ts)

    step('layer opacity', lambda: setattr(layer(fill_id), 'opacity', .3), followed=True)
    step('channel colour', lambda: setattr(stack.channel_for(layer(fill_id), 'BASE_COLOR'), 'color', (.1, .6, .9, 1)),
         followed=True)
    step('effect radius', lambda: setattr(stack.channel_for(layer(blurred_id), 'BASE_COLOR').effects[0], 'radius', 7.),
         followed=True)
    step('mask amount', lambda: setattr(layer(masked_id).masks[0], 'amount', .2), followed=True)
    step('layer visibility', lambda: setattr(layer(fill_id), 'visible', False), followed=True)
    step('layer opacity', lambda: setattr(layer(fill_id), 'opacity', .45), followed=True, undo='redo')
    step('blend mode', lambda: setattr(layer(fill_id), 'blend_mode', 'MULTIPLY'), followed=True)

    def mixed():
        target = layer(fill_id)
        target.opacity = .2
        target.blend_mode = 'SCREEN'
    step('opacity with a blend mode', mixed, followed=True)
    step('blend mode', lambda: setattr(layer(fill_id), 'blend_mode', 'OVERLAY'), followed=True, undo='redo')
    step('effect kind', lambda: setattr(stack.channel_for(layer(blurred_id), 'BASE_COLOR').effects[0], 'kind', 'SHARPEN'),
         followed=True)
    stack.channel_for(layer(fill_id), 'ROUGHNESS').value = .95   # under OVERLAY, .5 changes nothing

    def toggle_channel():
        prop = stack.channel_for(layer(fill_id), 'ROUGHNESS')
        prop.enabled = not prop.enabled
    step('channel switched', toggle_channel, followed=True)

    def add_mask():
        mask = layer(fill_id).masks.add()
        mask.source = 'PROCEDURAL'
    step('a mask added', add_mask, followed=False)
    step('a layer added', lambda: stack.add(current()[1], 'FILL', 'Added'), followed=False)

    # Two undos with no tick between them: the second finds no session to follow.
    scene, ts = current()
    ready(scene, ts)
    bpy.ops.ed.undo_push(message='two before')
    two_before = published()
    layer(fill_id).opacity = .9
    scene, ts = current()
    ready(scene, ts)
    bpy.ops.ed.undo_push(message='two first')
    layer(fill_id).opacity = .1
    scene, ts = current()
    ready(scene, ts)
    bpy.ops.ed.undo_push(message='two second')
    bpy.ops.ed.undo()
    bpy.ops.ed.undo()
    scene, ts = current()
    ready(scene, ts)
    assert counts() == (0, 1), counts()
    identical('two undos', two_before, published())
    print('UNDO_FOLLOW two undos rebuilt', flush=True)

    # After a follow, edits still apply: numeric through the followed bindings, structural by a patch.
    step('layer opacity', lambda: setattr(layer(fill_id), 'opacity', .35), followed=True)
    before = published()
    layer(fill_id).opacity = .55
    scene, ts = current()
    ready(scene, ts)
    after_numeric = published()
    assert any(np.any(before[c] != after_numeric[c]) for c in before), 'a followed graph ignored an edit'
    target = layer(fill_id)
    target.blend_mode = 'MULTIPLY' if target.blend_mode != 'MULTIPLY' else 'SCREEN'
    ready(scene, ts)
    assert any(np.any(after_numeric[c] != published()[c]) for c in before), 'a followed graph ignored a structure edit'
    print('UNDO_FOLLOW_PASS', flush=True)
    open_painter.unregister()


main()
