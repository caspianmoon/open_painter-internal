"""Real-window regression: a simulated drag with the Vector Brush records a stroke with
several samples, the overlay handler is removed on Esc, and the stroke renders.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/vector_brush_ui.py
OP_VECTOR_BRUSH_UI_REPORT selects the JSON report.
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
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import shader_preview
from open_painter.painting import stroke_raster, strokes as records
from open_painter.ui.vector_brush import OP_OT_vector_brush

REPORT = Path(os.environ.get('OP_VECTOR_BRUSH_UI_REPORT', '/private/tmp/op-vector-brush-ui.json'))
report = {'started': time.monotonic(), 'steps': [], 'errors': []}
env = {}


def finish(status):
    report['elapsed_s'] = round(time.monotonic() - report.pop('started'), 2)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(dict(status=status, blender=bpy.app.version_string, **report), indent=2) + '\n')
    print('VECTOR_BRUSH_UI_' + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()


def mouse(kind, value, x, y):
    env['window'].event_simulate(type=kind, value=value, x=int(x), y=int(y))


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
    plane = bpy.context.object
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_editor()
    ts = sets.create(scene, 'Canvas', 512); sets.assign(scene, ts, plane)
    layer = stack.add(ts, 'PAINT', 'Marks')
    ts.active_layer = stack.index_of(ts, layer.id)
    space = area.spaces.active
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view3d.view_axis(type='TOP')
        bpy.ops.view3d.view_all(center=True)
    yield 1.0
    settings = scene.open_painter.vector_brush
    settings.radius, settings.spacing, settings.roughness, settings.channels = .15, .3, .9, {'ROUGHNESS', 'BASE_COLOR'}
    settings.base_color = (1., .35, .05, 1.)
    # Focus click, then start the brush from the panel operator.
    cx, cy = region.x + region.width // 2, region.y + region.height // 2
    window.cursor_warp(cx, cy)
    mouse('LEFTMOUSE', 'PRESS', cx, cy); mouse('LEFTMOUSE', 'RELEASE', cx, cy)
    yield .3
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.vector_brush('INVOKE_DEFAULT')
    yield .3
    assert OP_OT_vector_brush._handler is not None, 'the overlay handler was not installed'
    # Drag across the plane.
    x0, x1 = region.x + int(region.width * .35), region.x + int(region.width * .65)
    window.cursor_warp(x0, cy)
    mouse('MOUSEMOVE', 'NOTHING', x0, cy)
    yield .2
    mouse('LEFTMOUSE', 'PRESS', x0, cy)
    for step in range(1, 25):
        mouse('MOUSEMOVE', 'NOTHING', x0 + (x1 - x0) * step / 24, cy + 3 * ((-1) ** step))
    yield .3
    mouse('LEFTMOUSE', 'RELEASE', x1, cy)
    yield .5
    strokes = records.strokes_for(scene.open_painter, layer.stroke_key)
    assert len(strokes) == 1, len(strokes)
    samples = stroke_raster.unpack_samples(strokes[0].samples)
    report['samples'] = int(len(samples))
    assert len(samples) >= 5, len(samples)
    assert abs(samples[:, 2]).max() < 1e-3, 'samples must lie on the plane'
    assert samples[-1, 0] - samples[0, 0] > .5, 'samples must span the drag'
    report['steps'].append('drag records a stroke')
    mouse('ESC', 'PRESS', x1, cy); mouse('ESC', 'RELEASE', x1, cy)
    yield .3
    assert OP_OT_vector_brush._handler is None, 'the overlay handler survived Esc'
    report['steps'].append('esc finishes')
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not shader_preview.current(scene, ts):
        yield .1
    info = shader_preview.diagnostics(scene, ts)
    assert shader_preview.current(scene, ts) and not info['error'], info
    assert any(node.get('op_boundary', '').startswith('["strokes"') for node in shader_preview.ensure(scene, ts).nodes())
    report['steps'].append('stroke renders in the shader preview')
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
