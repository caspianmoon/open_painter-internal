"""Real-window probe: undoing a brush stroke keeps the engine's session.

Blender's brush pushes an image undo step per stroke. Undoing or redoing one restores the
canvas's pixels in place and no datablock (``cache.on_undo``): the session keeps its graph,
renders what reads the canvas, and publishes exactly the result from before the undone stroke.
The undo that crosses back past the first stroke restores a memfile step, and that one starts
the session over (still to the same pixels).

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/paint_undo_ui.py
OP_PAINT_UNDO_UI_REPORT selects the JSON report.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, material_manager, texture_set_manager as sets
from open_painter.evaluation import shader_preview
from open_painter.painting.channel_paint import target as paint_target

REPORT = Path(os.environ.get('OP_PAINT_UNDO_UI_REPORT', '/private/tmp/op-paint-undo-ui.json'))
STROKES = 3
report = {'started': time.monotonic(), 'steps': [], 'errors': [], 'undos': []}
env = {}


def finish(status):
    report['elapsed_s'] = round(time.monotonic() - report.pop('started'), 2)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(dict(status=status, blender=bpy.app.version_string, **report), indent=2) + '\n')
    print('PAINT_UNDO_UI_' + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()


def mouse(kind, value, x, y, ctrl=False, shift=False):
    env['window'].event_simulate(type=kind, value=value, x=int(x), y=int(y), ctrl=ctrl, shift=shift)


def undo_pre(*args):
    env['main_before'] = bpy.data.as_pointer()


def undo_post(*args):
    env['main_after'] = bpy.data.as_pointer()
    env['undone_at'] = time.monotonic()


def resolve():
    """The probe's set, read again (a memfile undo replaces every wrapper)."""
    scene = bpy.context.scene
    return scene, next(item for item in scene.open_painter.texture_sets if item.id == env['set_id'])


def published(ts, channel='ROUGHNESS'):
    from open_painter.evaluation.engine.runtime import device
    device().finish()
    image = next(out.image for out in ts.outputs if out.channel == channel)
    return np.array(gpu.texture.from_image(image).read(), np.float32).copy()


def settled():
    """Generator: until the set is current and its history still for half a second."""
    rows, since = -1, None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        scene, ts = resolve()
        info = shader_preview.diagnostics(scene, ts, details=False)
        history = (shader_preview._sessions.get(shader_preview.key_for(scene, ts)) or {}).get('history', ())
        if shader_preview.current(scene, ts) and not info.get('pending_resources') and len(history) == rows:
            if time.monotonic() - since >= .5:
                return
        else:
            rows, since = len(history), time.monotonic()
        yield .05
    raise AssertionError('the set did not settle')


def steps():
    assert not bpy.app.background
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    window = env['window'] = bpy.context.window
    window.event_simulate(type='ESC', value='PRESS')
    yield .5
    bpy.app.handlers.undo_pre.append(undo_pre)
    bpy.app.handlers.redo_pre.append(undo_pre)
    bpy.app.handlers.undo_post.append(undo_post)
    bpy.app.handlers.redo_post.append(undo_post)
    scene = bpy.context.scene
    project = scene.open_painter
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    plane = bpy.context.object
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_editor()
    ts = sets.create(scene, 'Paint undo', 1024)
    env['set_id'] = ts.id
    sets.assign(scene, ts, plane)
    base = stack.add(ts, 'FILL', 'Base'); stack.channel_for(base, 'ROUGHNESS').value = .2
    paint = stack.add(ts, 'PAINT', 'Painted roughness')
    paint_id = paint.id
    image = paint_target(ts, paint, 'ROUGHNESS')
    above = stack.add(ts, 'FILL', 'Above')
    prop = stack.channel_for(above, 'ROUGHNESS')
    prop.enabled, prop.value = True, .8
    above.opacity, above.blend_mode = .4, 'MULTIPLY'
    ts.active_layer = stack.index_of(ts, paint_id)
    project.paint_channel = 'ROUGHNESS'
    material_manager.repair(ts)
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view3d.view_axis(type='TOP')
        bpy.ops.view3d.view_all(center=True)
    yield from settled()
    bpy.ops.ed.undo_push(message='Paint undo stack')   # what the operators building it would push
    cx, cy = region.x + region.width // 2, region.y + region.height // 2
    window.cursor_warp(cx, cy)
    mouse('LEFTMOUSE', 'PRESS', cx, cy); mouse('LEFTMOUSE', 'RELEASE', cx, cy)
    yield .3
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.paint()
    yield .5
    assert project.paint_image == image and plane.mode == 'TEXTURE_PAINT', (project.paint_image, plane.mode)
    brush = scene.tool_settings.image_paint.brush
    for owner in (brush, scene.tool_settings.image_paint.unified_paint_settings):
        owner.strength, owner.size = 1, 40
    yield from settled()
    report['steps'].append('paint session began')

    states = []
    x0, x1 = region.x + int(region.width * .3), region.x + int(region.width * .7)
    for stroke in range(STROKES):
        scene, ts = resolve()
        states.append(published(ts))
        y = region.y + int(region.height * (.3 + .15 * stroke))
        window.cursor_warp(x0, y)
        mouse('MOUSEMOVE', 'NOTHING', x0, y)
        yield .2
        mouse('LEFTMOUSE', 'PRESS', x0, y)
        for step in range(1, 11):
            mouse('MOUSEMOVE', 'NOTHING', x0 + (x1 - x0) * step / 10, y)
            yield .033
        mouse('LEFTMOUSE', 'RELEASE', x1, y)
        yield from settled()
    scene, ts = resolve()
    states.append(published(ts))
    for before, after in zip(states, states[1:]):
        assert np.any(before != after), 'every stroke must change the published channel'
    report['steps'].append(f'{STROKES} strokes painted')

    def ctrl_z(shift, expected, label):
        scene, ts = resolve()
        entry = shader_preview._sessions[shader_preview.key_for(scene, ts)]
        graph, builds = entry['graph'], entry.get('graph_builds', 0)
        env['main_before'] = env['main_after'] = None
        pressed = time.monotonic()
        mouse('Z', 'PRESS', x1, cy, ctrl=True, shift=shift); mouse('Z', 'RELEASE', x1, cy, ctrl=True, shift=shift)
        yield .1
        yield from settled()
        scene, ts = resolve()
        restored = env['main_before'] is not None and env['main_before'] != env['main_after']
        entry = shader_preview._sessions[shader_preview.key_for(scene, ts)]
        kept = entry.get('graph') is graph and entry.get('graph_builds', 0) == builds
        difference = float(np.abs(published(ts) - expected).max())
        report['undos'].append({'step': label, 'memfile': restored, 'session_kept': kept, 'difference': difference,
                                'undo_post_ms': round(((env.get('undone_at') or pressed) - pressed) * 1000, 1)})
        env['last'] = (restored, kept, difference)

    for undo in range(STROKES - 1):   # the strokes' own image steps
        yield from ctrl_z(False, states[STROKES - 1 - undo], f'undo {undo}')
        restored, kept, difference = env['last']
        assert env['main_before'] is not None, 'the undo ran no pre handler'
        assert not restored and kept, f'an image undo started the session over: {report["undos"][-1]}'
        assert difference == 0.0, f'the undone stroke is still published: {report["undos"][-1]}'
    report['steps'].append('image undos kept the session')
    for redo in range(STROKES - 1):
        yield from ctrl_z(True, states[2 + redo], f'redo {redo}')
        restored, kept, difference = env['last']
        assert not restored and kept, f'an image redo started the session over: {report["undos"][-1]}'
        assert difference == 0.0, f'the redone stroke is not published: {report["undos"][-1]}'
    report['steps'].append('image redos kept the session')
    for undo in range(STROKES):   # the last crosses back past the first stroke, to a memfile step
        yield from ctrl_z(False, states[STROKES - 1 - undo], f'back {undo}')
    restored, kept, difference = env['last']
    assert restored and not kept, f'a memfile undo must start the session over: {report["undos"][-1]}'
    assert difference == 0.0, f'the memfile undo publishes another result: {report["undos"][-1]}'
    report['steps'].append('the memfile undo started over, to the same pixels')


generator = steps()


def poll():
    try:
        return next(generator)
    except StopIteration:
        finish('PASS')
    except Exception:
        report['errors'].append(traceback.format_exc())
        traceback.print_exc()
        finish('FAIL')


bpy.app.timers.register(poll, first_interval=1.0)
