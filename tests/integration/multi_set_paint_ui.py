"""Real-window regression: one native brush stroke across two texture sets' faces
paints both sets' canvases through Blender's material paint mode.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/multi_set_paint_ui.py
OP_MULTI_SET_PAINT_UI_REPORT selects the JSON report.
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
from open_painter.core import image_manager as images, layer_stack as stack, material_manager, texture_set_manager as sets
from open_painter.painting import paint_session

REPORT = Path(os.environ.get('OP_MULTI_SET_PAINT_UI_REPORT', '/private/tmp/op-multi-set-paint-ui.json'))
report = {'started': time.monotonic(), 'steps': [], 'errors': []}
env = {}


def finish(status):
    report['elapsed_s'] = round(time.monotonic() - report.pop('started'), 2)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(dict(status=status, blender=bpy.app.version_string, **report), indent=2) + '\n')
    print('MULTI_SET_PAINT_UI_' + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()


def steps():
    assert not bpy.app.background
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    window = env['window'] = bpy.context.window
    window.event_simulate(type='ESC', value='PRESS')
    yield .5
    scene = bpy.context.scene
    project = scene.open_painter
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add(size=2)
    cube = bpy.context.object
    bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.subdivide(number_cuts=1)
    bpy.ops.uv.smart_project(island_margin=.03); bpy.ops.object.mode_set(mode='OBJECT')
    cube.data.materials.append(bpy.data.materials.new('Lower')); cube.data.materials.append(bpy.data.materials.new('Upper'))
    for polygon in cube.data.polygons:
        polygon.material_index = 1 if polygon.center.z > 0 else 0
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_editor()
    lower = sets.create(scene, 'Lower', 512); sets.assign(scene, lower, cube, 0)
    upper = sets.create(scene, 'Upper', 512); sets.assign(scene, upper, cube, 1)
    for ts in (lower, upper):
        base = stack.add(ts, 'FILL', 'Base'); stack.channel_for(base, 'ROUGHNESS').value = .2
        material_manager.repair(ts)
    paint = stack.add(lower, 'PAINT', 'Marks')
    lower.active_layer = stack.index_of(lower, paint.id)
    project.active_texture_set = 0
    project.paint_channel = 'ROUGHNESS'
    project.paint_all_sets = True
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view3d.view_axis(type='RIGHT')
        bpy.ops.view3d.view_all(center=True)
    yield 1.0
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.paint()
    yield .5
    assert len(project.paint_canvases) == 2, len(project.paint_canvases)
    assert scene.tool_settings.image_paint.mode == 'MATERIAL'
    report['steps'].append('multi-set session began')
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    rv3d = area.spaces.active.region_3d
    brush = scene.tool_settings.image_paint.brush
    unified = scene.tool_settings.image_paint.unified_paint_settings
    for owner in (brush, unified):
        owner.color, owner.strength, owner.size = (.9, .9, .9), 1, 45
    stroke = []
    for z in np.linspace(-.8, .8, 14):
        xy = location_3d_to_region_2d(region, rv3d, (1.0, 0., float(z)))
        stroke.append(dict(name='stroke', location=(1.0, 0., float(z)), mouse=(xy.x, xy.y), mouse_event=(xy.x, xy.y),
                           size=45, pressure=1., time=0., is_start=not stroke, x_tilt=0., y_tilt=0.))
    with bpy.context.temp_override(window=window, area=area, region=region):
        result = bpy.ops.paint.image_paint(stroke=stroke)
    assert result == {'FINISHED'}, result
    yield .5
    canvases = [entry.image for entry in project.paint_canvases]
    painted = [float(images.pixels(image)[..., 3].max()) for image in canvases]
    report['canvas_alpha_max'] = painted
    assert all(value > .5 for value in painted), 'both canvases must receive the stroke'
    report['steps'].append('one stroke painted both canvases')
    paint_session.finish(bpy.context)
    yield .5
    deadline = time.monotonic() + 30
    from open_painter.evaluation import shader_preview
    while time.monotonic() < deadline and not all(shader_preview.current(scene, ts) for ts in (lower, upper)):
        yield .1
    assert all(shader_preview.current(scene, ts) for ts in (lower, upper))
    report['steps'].append('both sets current after finish')
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
