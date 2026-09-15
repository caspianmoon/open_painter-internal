"""Displayed-frame latency of shader-native edits through the ordinary VIEW_3D draw.

A PRE_VIEW handler captures Blender's colour framebuffer, a POST_PIXEL handler
reads a 3x3 patch at the framed cube's centre, which fences the native EEVEE
draw. For each edit the report records the time to the first fenced frame after
the edit and the time to the first frame whose patch matches the settled
appearance (sampled after the edit settles), which includes any partial rebuild
and shader recompilation. Production cache.tick stays registered.

    BLENDER_USER_RESOURCES=/private/tmp/op-frame-user \\
      /Applications/Blender.app/Contents/MacOS/Blender --factory-startup \\
      --python tests/performance/shader_native_frame.py -- --report /private/tmp/op-shader-frame.json

Not physical presentation or OS input latency; the window size and Blender's
event loop affect these timings.
"""
import argparse
import json
import os
import statistics
import sys
import time
import traceback
from pathlib import Path

import bpy
import gpu
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests' / 'performance'))
import open_painter
from fixtures import create as fixture
from open_painter.core import layer_stack, texture_set_manager
from open_painter.evaluation import cache, shader_preview

argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
parser = argparse.ArgumentParser()
parser.add_argument('--report', type=Path, default=ROOT / '.blender-test' / 'shader-native-frame.json')
parser.add_argument('--repeats', type=int, default=5)
parser.add_argument('--settle', type=float, default=1.5)
ARGS = parser.parse_args([a for a in argv if not a.startswith('-Apple') and a != 'YES'])

KINDS = ('redraw_control', 'slider', 'hide', 'show', 'blend_mode', 'undo')
STATE = {'frames': 0, 'pending': None, 'queue': [], 'error': None, 'finished': False, 'handlers': []}
REPORT = {'status': 'RUNNING', 'samples': []}


def finish(error=None):
    if STATE['finished']:
        return None
    STATE['finished'] = True
    for kind, handler in STATE['handlers']:
        bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
    if error:
        REPORT['error'] = error
    REPORT['status'] = 'FAIL' if error else 'PASS'
    summary = {}
    for kind in KINDS:
        rows = [s for s in REPORT['samples'] if s['kind'] == kind]
        if rows:
            settled = [s['settled_frame_s'] for s in rows if s.get('settled_frame_s') is not None]
            summary[kind] = dict(count=len(rows),
                                 median_first_frame_ms=round(statistics.median(s['first_frame_s'] for s in rows) * 1000, 1),
                                 median_settled_frame_ms=round(statistics.median(settled) * 1000, 1) if settled else None,
                                 median_shader_current_ms=round(statistics.median(s['shader_current_s'] for s in rows) * 1000, 1),
                                 patch_changed=sum(1 for s in rows if s['patch_changed']))
    REPORT['summary'] = summary
    ARGS.report.parent.mkdir(parents=True, exist_ok=True)
    ARGS.report.write_text(json.dumps(REPORT, indent=2) + '\n')
    print('SHADER_NATIVE_FRAME_' + REPORT['status'], json.dumps(summary), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def begin_draw():
    if not STATE['finished'] and bpy.context.area == STATE.get('area') and bpy.context.region == STATE.get('region'):
        STATE['color_framebuffer'] = gpu.state.active_framebuffer_get()
        STATE['color_viewport'] = gpu.state.viewport_get()


def read_patch():
    fb = STATE.pop('color_framebuffer', None)
    if fb is None:
        return None
    x, y, width, height = STATE.pop('color_viewport')
    with fb.bind():
        patch = fb.read_color(x + max(0, width // 2 - 1), y + max(0, height // 2 - 1), 3, 3, 4, 0, 'FLOAT')
    return np.asarray(patch, dtype=np.float32).reshape(-1, 4).mean(axis=0)


def completed_draw():
    try:
        if STATE['finished'] or bpy.context.area != STATE['area'] or bpy.context.region != STATE['region']:
            return
        now = time.perf_counter()
        patch = read_patch()
        if patch is None:
            return
        STATE['frames'] += 1
        STATE['last_patch'] = patch
        pending = STATE['pending']
        if pending is None:
            return
        if 'first_frame_s' not in pending:
            pending['first_frame_s'] = now - pending['started']
        pending['frames'].append((now - pending['started'], patch.tolist()))
    except Exception:
        STATE['error'] = traceback.format_exc()


def setup():
    if bpy.app.background or bpy.context.window is None:
        raise RuntimeError('This probe requires a Blender GUI window')
    open_painter.register()
    if not bpy.app.timers.is_registered(cache.tick):
        raise RuntimeError('The add-on did not register cache.tick')
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    ts = fixture(scene, 'worn', 2048)[0]
    ts.preview_backend = 'LIVE'
    cube = scene.objects['Cube']
    texture_set_manager.assign(scene, ts, cube)
    area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    space = area.spaces.active
    space.shading.type = 'MATERIAL'
    space.shading.use_scene_world = space.shading.use_scene_lights = False
    space.overlay.show_overlays = False
    space.region_3d.view_location = (0, 0, 0)
    space.region_3d.view_distance = 7.5
    space.region_3d.view_rotation = Vector((4.2, -5.1, 3.5)).to_track_quat('Z', 'Y')
    space.region_3d.view_perspective = 'PERSP'
    coat = next(layer for layer in ts.layers if layer.name == 'Blue paint coat')
    mask = next(item for item in coat.masks if item.enabled and item.source == 'GENERATOR')
    STATE.update(scene=scene, ts=ts, ts_id=ts.id, area=area, region=region, coat_id=coat.id, mask_id=mask.id,
                 base_amount=float(mask.amount), base_blend=coat.blend_mode)
    REPORT.update(blender=bpy.app.version_string, gpu_backend=gpu.platform.backend_type_get(),
                  gpu_renderer=gpu.platform.renderer_get(), fixture='Worn Painted Metal on the default cube',
                  resolution=2048, repeats=ARGS.repeats, region_size=[region.width, region.height],
                  timing_scope='Property edit, production cache.tick scheduling, native VIEW_3D draw and a 3x3 '
                               'framebuffer completion fence; not presentation or OS input latency.')
    STATE['handlers'] = [
        ('PRE_VIEW', bpy.types.SpaceView3D.draw_handler_add(begin_draw, (), 'WINDOW', 'PRE_VIEW')),
        ('POST_PIXEL', bpy.types.SpaceView3D.draw_handler_add(completed_draw, (), 'WINDOW', 'POST_PIXEL'))]
    STATE['queue'] = [(kind, i) for kind in ('redraw_control', 'slider') for i in range(ARGS.repeats)]
    # Alternate hide/show so every sample changes what is drawn.
    STATE['queue'] += [(kind, i) for i in range(ARGS.repeats) for kind in ('hide', 'show')]
    STATE['queue'] += [(kind, i) for kind in ('blend_mode', 'undo') for i in range(ARGS.repeats)]
    STATE['warm_until'] = time.perf_counter() + 4.0
    STATE['settled_at'] = None


def current_set():
    scene = bpy.context.scene
    return scene, next(ts for ts in scene.open_painter.texture_sets if ts.id == STATE['ts_id'])


def apply_edit(kind, index):
    scene, ts = current_set()
    coat = next(layer for layer in ts.layers if layer.id == STATE['coat_id'])
    mask = next(item for item in coat.masks if item.id == STATE['mask_id'])
    if kind == 'slider':
        mask.amount = STATE['base_amount'] * (.3 + .1 * index)
    elif kind == 'hide':
        coat.visible = False
    elif kind == 'show':
        coat.visible = True
    elif kind == 'blend_mode':
        coat.blend_mode = ('MULTIPLY', 'SCREEN', 'OVERLAY', 'NORMAL', 'MULTIPLY')[index % 5]
    elif kind == 'undo':
        bpy.ops.ed.undo()
    STATE['area'].tag_redraw()


def poll():
    try:
        if STATE['finished']:
            return None
        if STATE['error']:
            return finish(STATE['error'])
        if 'area' not in STATE:
            setup()
            return .1
        now = time.perf_counter()
        scene, ts = current_set()
        pending = STATE['pending']
        if pending is not None:
            if 'shader_current_s' not in pending and shader_preview.current(scene, ts):
                pending['shader_current_s'] = now - pending['started']
            if now - pending['started'] < ARGS.settle or 'shader_current_s' not in pending:
                if now - pending['started'] > 60:
                    return finish('Sample did not settle: ' + json.dumps(pending['kind']))
                STATE['area'].tag_redraw()
                return .01
            settled = np.asarray(STATE['last_patch'])
            before = np.asarray(pending['patch_before'])
            pending['patch_changed'] = bool(np.abs(settled[:3] - before[:3]).max() > .01)
            pending['settled_frame_s'] = next((t for t, patch in pending['frames']
                                               if np.abs(np.asarray(patch)[:3] - settled[:3]).max() <= .01), None)
            pending['settled_patch'] = settled.tolist()
            pending['frame_count'] = len(pending['frames'])
            del pending['frames']
            REPORT['samples'].append(pending)
            print('SHADER_NATIVE_FRAME_SAMPLE', json.dumps({k: v for k, v in pending.items() if k != 'settled_patch'}), flush=True)
            STATE['pending'] = None
            return .2
        if now < STATE['warm_until'] or not shader_preview.current(scene, ts):
            STATE['area'].tag_redraw()
            return .05
        if not STATE['queue']:
            return finish()
        kind, index = STATE['queue'].pop(0)
        if kind == 'undo':
            # Push the current state, make a numeric edit, then undo it under measurement.
            bpy.ops.ed.undo_push(message='shader frame probe')
            apply_edit('slider', index + 1)
            bpy.ops.ed.undo_push(message='shader frame probe edit')
            STATE['undo_wait'] = time.perf_counter() + .5
            while time.perf_counter() < STATE['undo_wait']:
                pass
            scene, ts = current_set()
        pending = dict(kind=kind, index=index, patch_before=np.asarray(STATE['last_patch']).tolist(), frames=[])
        pending['started'] = time.perf_counter()
        apply_edit(kind, index)
        STATE['pending'] = pending
        return .005
    except Exception:
        return finish(traceback.format_exc())


bpy.app.timers.register(poll, first_interval=.5, persistent=True)
