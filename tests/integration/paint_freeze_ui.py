"""Real-window probe: a native brush drag on a blurred paint layer freezes boundary
work while Blender's brush operator is modal, and one catch-up follows release.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/paint_freeze_ui.py
OP_PAINT_FREEZE_UI_REPORT selects the JSON report.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
# Retain explicit coverage of CPU-boundary scheduling and invalidation.
from unittest.mock import patch
patch('open_painter.evaluation.engine.filters.supported', return_value=False).start()
from open_painter.core import image_manager as images, layer_stack as stack, material_manager, texture_set_manager as sets
from open_painter.evaluation import cache, metrics, shader_preview
from open_painter.painting import paint_session
from open_painter.painting.channel_paint import target as paint_target

REPORT = Path(os.environ.get('OP_PAINT_FREEZE_UI_REPORT', '/private/tmp/op-paint-freeze-ui.json'))
report = {'started': time.monotonic(), 'steps': [], 'errors': [], 'tick_ms_during_stroke': []}
env = {}


def finish(status):
    report['elapsed_s'] = round(time.monotonic() - report.pop('started'), 2)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(dict(status=status, blender=bpy.app.version_string, **report), indent=2) + '\n')
    print('PAINT_FREEZE_UI_' + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()


def mouse(kind, value, x, y):
    env['window'].event_simulate(type=kind, value=value, x=int(x), y=int(y))


def timed_tick():
    started = time.perf_counter()
    interval = cache.tick()
    if env.get('stroke'):
        report['tick_ms_during_stroke'].append((time.perf_counter() - started) * 1000)
    return interval


def steps():
    assert not bpy.app.background
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    window = env['window'] = bpy.context.window
    window.event_simulate(type='ESC', value='PRESS')
    yield .5
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    bpy.app.timers.register(timed_tick, first_interval=.1, persistent=True)
    scene = bpy.context.scene
    project = scene.open_painter
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    plane = bpy.context.object
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_editor()
    ts = sets.create(scene, 'Freeze probe', 1024)
    sets.assign(scene, ts, plane)
    base = stack.add(ts, 'FILL', 'Base'); stack.channel_for(base, 'ROUGHNESS').value = .2
    paint = stack.add(ts, 'PAINT', 'Painted roughness')
    paint_id = paint.id
    image = paint_target(ts, paint, 'ROUGHNESS')
    blur = stack.channel_for(next(l for l in ts.layers if l.id == paint_id), 'ROUGHNESS').effects.add()
    blur.kind, blur.radius = 'GAUSSIAN_BLUR', 6.
    ts.active_layer = stack.index_of(ts, paint_id)
    project.paint_channel = 'ROUGHNESS'
    material_manager.repair(ts)
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view3d.view_axis(type='TOP')
        bpy.ops.view3d.view_all(center=True)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not shader_preview.current(scene, ts):
        yield .1
    assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
    report['steps'].append('shader current before painting')
    cx, cy = region.x + region.width // 2, region.y + region.height // 2
    window.cursor_warp(cx, cy)
    mouse('LEFTMOUSE', 'PRESS', cx, cy); mouse('LEFTMOUSE', 'RELEASE', cx, cy)
    yield .3
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.paint()
    yield .5
    assert project.paint_image == image and plane.mode == 'TEXTURE_PAINT', (project.paint_image, plane.mode)
    brush = scene.tool_settings.image_paint.brush
    unified = scene.tool_settings.image_paint.unified_paint_settings
    for owner in (brush, unified):
        owner.strength, owner.size = 1, 60
    report['steps'].append('paint session began')

    # The drag: Blender's brush operator is modal between these events.
    observed = metrics.Metrics()
    observed.__enter__()
    x0, x1 = region.x + int(region.width * .3), region.x + int(region.width * .7)
    window.cursor_warp(x0, cy)
    mouse('MOUSEMOVE', 'NOTHING', x0, cy)
    yield .2
    env['stroke'] = True
    frozen_seen = 0
    mouse('LEFTMOUSE', 'PRESS', x0, cy)
    for step in range(1, 31):
        mouse('MOUSEMOVE', 'NOTHING', x0 + (x1 - x0) * step / 30, cy + 4 * ((-1) ** step))
        yield .033
        info = shader_preview.diagnostics(scene, ts)
        frozen_seen += bool(info['frozen'])
    during = observed.report()['counters']
    mouse('LEFTMOUSE', 'RELEASE', x1, cy)
    env['stroke'] = False
    yield .5
    report['frozen_observations'] = frozen_seen
    report['requests_during_stroke'] = during.get('shader_resource_requests', 0)
    report['deferrals_during_stroke'] = during.get('shader_paint_deferrals', 0)
    assert frozen_seen > 0, 'the freeze was never observed during the drag'
    assert during.get('shader_resource_requests', 0) == 0, during
    assert during.get('shader_paint_deferrals', 0) > 0, during
    report['steps'].append('stroke deferred boundary work')
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not shader_preview.current(scene, ts):
        yield .1
    observed.__exit__(None, None, None)
    total = observed.report()['counters']
    report['resumes'] = total.get('shader_paint_resumes', 0)
    report['requests_total'] = total.get('shader_resource_requests', 0)
    assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
    assert total.get('shader_paint_resumes', 0) >= 1, total
    assert total.get('shader_resource_requests', 0) >= 1, total
    report['canvas_alpha_max'] = float(images.pixels(image)[..., 3].max())
    assert report['canvas_alpha_max'] > .5, 'the drag must paint the canvas'
    ticks = sorted(report['tick_ms_during_stroke'])
    if ticks:
        report['p95_tick_ms_during_stroke'] = round(ticks[min(len(ticks) - 1, int(len(ticks) * .95))], 2)
        report['max_tick_ms_during_stroke'] = round(ticks[-1], 2)
    report['tick_ms_during_stroke'] = len(ticks)
    report['steps'].append('release caught up once')
    paint_session.finish(bpy.context)
    yield .3
    shot = REPORT.with_suffix('.png')
    bpy.ops.screen.screenshot(filepath=str(shot))
    report['screenshot'] = str(shot)


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
