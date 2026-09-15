"""Real-window regression: a second window showing the same scene stays in step
with shader edits and can be closed without leaving stale runtime state.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/two_windows_ui.py
OP_TWO_WINDOWS_REPORT selects the JSON report.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, material_manager, texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.ui import editor
from open_painter.ui.dock import lifecycle

REPORT = Path(os.environ.get('OP_TWO_WINDOWS_REPORT', '/private/tmp/op-two-windows-ui.json'))
state = {'phase': 'setup', 'started': time.monotonic(), 'errors': []}
record = {'status': 'RUNNING', 'blender': bpy.app.version_string}


def finish(status):
    record['status'] = status
    record['elapsed_s'] = round(time.monotonic() - state['started'], 2)
    record['errors'] = state['errors']
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(record, indent=2) + '\n')
    print('TWO_WINDOWS_UI_' + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()
    return None


def texture_set():
    scene = bpy.context.scene
    return scene, next(ts for ts in scene.open_painter.texture_sets if ts.id == state['ts_id'])


def poll():
    try:
        if state['phase'] == 'setup':
            assert not bpy.app.background
            open_painter.register()
            bpy.context.preferences.view.show_splash = False
            scene = bpy.context.scene
            ts = sets.create(scene, 'Two windows', 512)
            sets.assign(scene, ts, scene.objects['Cube'])
            layer = stack.add(ts, 'FILL', 'Layer')
            stack.channel_for(layer, 'BASE_COLOR').color = (.8, .2, .1, 1.)
            material_manager.repair(ts)
            state.update(ts_id=ts.id, layer_id=layer.id, windows_before=len(bpy.context.window_manager.windows),
                         phase='open')
            return .2
        scene, ts = texture_set()
        if state['phase'] == 'open':
            if not shader_preview.current(scene, ts):
                return .05
            window = bpy.context.window_manager.windows[0]
            area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
            with bpy.context.temp_override(window=window, area=area):
                result = bpy.ops.wm.window_new()
            assert result == {'FINISHED'}, result
            assert len(bpy.context.window_manager.windows) == state['windows_before'] + 1
            record['windows'] = len(bpy.context.window_manager.windows)
            state['phase'] = 'edit'
            state['until'] = time.monotonic() + 1.0
            return .1
        if state['phase'] == 'edit':
            if time.monotonic() < state['until']:
                return .1
            layer = next(l for l in ts.layers if l.id == state['layer_id'])
            layer.opacity = .5
            assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
            layer.blend_mode = 'MULTIPLY'
            state['phase'] = 'rebuild'
            state['deadline'] = time.monotonic() + 30
            return .05
        if state['phase'] == 'rebuild':
            if not shader_preview.current(scene, ts):
                assert time.monotonic() < state['deadline'], shader_preview.diagnostics(scene, ts)
                return .05
            info = shader_preview.diagnostics(scene, ts)
            record['rebuild'] = {k: info.get(k) for k in ('graph_builds', 'reused_units', 'rebuilt_units')}
            # Every window's 3D areas share the one material; the lifecycle timer must scan both.
            lifecycle.tick()
            for window in bpy.context.window_manager.windows:
                editor.scan(window)
            windows = list(bpy.context.window_manager.windows)
            extra = windows[-1]
            with bpy.context.temp_override(window=extra):
                bpy.ops.wm.window_close()
            state['phase'] = 'closed'
            state['until'] = time.monotonic() + 1.0
            return .1
        if state['phase'] == 'closed':
            if time.monotonic() < state['until']:
                return .1
            assert len(bpy.context.window_manager.windows) == state['windows_before'], len(bpy.context.window_manager.windows)
            layer = next(l for l in ts.layers if l.id == state['layer_id'])
            layer.opacity = .75
            assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
            cache.tick()
            lifecycle.tick()
            assert bpy.app.timers.is_registered(cache.tick)
            return finish('PASS')
    except Exception:
        state['errors'].append(traceback.format_exc())
        return finish('FAIL')


bpy.app.timers.register(poll, first_interval=.5)
