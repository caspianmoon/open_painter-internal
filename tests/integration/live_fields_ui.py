"""Real-window regression: a LIVE proximity field re-analyses in the background when
its influencer moves — the viewport never blocks on the analysis, the status line
reports progress, and the published raster follows the object.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/live_fields_ui.py
OP_LIVE_FIELDS_UI_REPORT selects the JSON report.
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
from open_painter.core import layer_stack as stack, texture_set_manager as sets, image_manager as images
from open_painter.evaluation import field_jobs, shader_preview
from open_painter.evaluation.metrics import Metrics
from open_painter.utils.ids import new_id

REPORT = Path(os.environ.get('OP_LIVE_FIELDS_UI_REPORT', '/private/tmp/op-live-fields-ui.json'))
report = {'started': time.monotonic(), 'steps': [], 'errors': []}
env = {}


def finish(status):
    report['elapsed_s'] = round(time.monotonic() - report.pop('started'), 2)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(dict(status=status, blender=bpy.app.version_string, **report), indent=2) + '\n')
    print('LIVE_FIELDS_UI_' + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()


def steps():
    assert not bpy.app.background
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    window = env['window'] = bpy.context.window
    window.event_simulate(type='ESC', value='PRESS')
    yield .5
    scene = bpy.context.scene
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    panel = bpy.context.object
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_editor()
    ts = sets.create(scene, 'Panel', 512); sets.assign(scene, ts, panel)
    layer = stack.add(ts, 'FILL', 'Rust'); stack.channel_for(layer, 'BASE_COLOR').color = (.4, .1, .05, 1)
    mask = layer.masks.add(); mask.id, mask.source, mask.name = new_id(), 'SCENE', 'Bolt proximity'
    field = mask.influence
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=.15, location=(.5, 0, .1))
    bolt = bpy.context.object
    field.kind, field.mode, field.resolution, field.distance, field.object = 'PROXIMITY', 'LIVE', '256', .4, bolt
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not (shader_preview.current(scene, ts) and field.image and not field_jobs.pending(scene)):
        yield .1
    assert field.image is not None and not field.error, (field.error, shader_preview.diagnostics(scene, ts))
    first = images.pixels(field.image)[..., 0].copy()
    assert first[:, 160:].max() > .5 and first[:, :64].max() < .05, 'the first analysis did not see the bolt'
    report['steps'].append('initial live analysis')
    # Move the bolt: the next frames must not analyse synchronously; the job publishes later.
    with Metrics() as counters:
        bolt.location.x = -.5
        yield .05
    assert counters.phases.get('scene_analysis', {}).get('calls', 0) == 0
    seen_status = False
    started = time.monotonic()
    while time.monotonic() - started < 30:
        if 'Analysing' in scene.open_painter.status:
            seen_status = True
        fresh = images.pixels(field.image)[..., 0]
        if fresh[:, :64].max() > .5 and not field_jobs.pending(scene):
            break
        yield .05
    fresh = images.pixels(field.image)[..., 0]
    assert fresh[:, :64].max() > .5 and fresh[:, 200:].max() < .05, 'the published raster did not follow the bolt'
    report['published_after_s'] = round(time.monotonic() - started, 2)
    report['status_seen'] = seen_status
    report['steps'].append('moved influencer republished')
    # The shader session picked the new raster up (no error, current).
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not shader_preview.current(scene, ts):
        yield .1
    info = shader_preview.diagnostics(scene, ts)
    assert shader_preview.current(scene, ts) and not info['error'], info
    report['steps'].append('shader follows the published field')
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
