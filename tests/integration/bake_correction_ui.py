"""Real-window probe: the Bake Correction Brush paints a projection-distance stroke with the
native brush and the scheduler rebakes the touched region from its timer once the stroke ends.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/bake_correction_ui.py
OP_BAKE_CORRECTION_UI_REPORT selects the JSON report.
"""
import json
import math
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
from open_painter.baking import corrections
from open_painter.baking.bake_manager import register_map
from open_painter.baking.cycles_baker import bake
from open_painter.core import image_manager as images, texture_set_manager as sets

REPORT = Path(os.environ.get('OP_BAKE_CORRECTION_UI_REPORT', '/private/tmp/op-bake-correction-ui.json'))
report = {'started': time.monotonic(), 'steps': [], 'errors': []}
env = {}


def finish(status):
    report['elapsed_s'] = round(time.monotonic() - report.pop('started'), 2)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(dict(status=status, blender=bpy.app.version_string, **report), indent=2) + '\n')
    print('BAKE_CORRECTION_UI_' + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()


def mouse(kind, value, x, y):
    env['window'].event_simulate(type=kind, value=value, x=int(x), y=int(y))


def cube(name, size, subdivisions=0):
    bpy.ops.mesh.primitive_cube_add(size=size)
    obj = bpy.context.object
    obj.name = name
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    if subdivisions:
        bpy.ops.mesh.subdivide(number_cuts=subdivisions)
    bpy.ops.uv.smart_project(island_margin=.03)
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


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
    low = cube('Body_low', 2.0, 2)
    high = cube('Body_high', 2.0, 2)
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    plate = bpy.context.object
    plate.name = 'Plate_high'
    plate.scale = (.5, .5, .02)
    plate.location = (0, 0, 1.3)
    plate.rotation_euler = (math.radians(25), 0, 0)
    bpy.ops.object.transform_apply(scale=True, rotation=True)
    plate.hide_set(True)
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_editor()
    ts = sets.create(scene, 'Correction probe', 512)
    sets.assign(scene, ts, low)
    for obj in (high, plate):
        ref = ts.bake.high_objects.add()
        ref.object, ref.name = obj, obj.name
    settings = ts.bake
    settings.selected_to_active, settings.cage_mode = True, 'AUTO'
    settings.separate_cage_distance, settings.cage_extrusion, settings.ray_distance = True, .1, 1.0
    settings.samples, settings.margin = 1, 2
    settings.normal, settings.ao, settings.curvature = True, False, False
    settings.corrections.offset_range = .4
    scene.render.engine = 'CYCLES'
    with bpy.context.temp_override(window=window, area=area, region=region):
        register_map(ts, 'NORMAL', bake(bpy.context, ts, 'NORMAL'))
    prop = next(m for m in ts.baked_maps if m.kind == 'NORMAL')
    baseline = images.pixels(prop.image)[..., :3].copy()
    report['steps'].append('baseline normal map baked')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view3d.view_axis(type='TOP')
        bpy.ops.view3d.view_all(center=True)
    yield .5
    cx, cy = region.x + region.width // 2, region.y + region.height // 2
    window.cursor_warp(cx, cy)
    mouse('LEFTMOUSE', 'PRESS', cx, cy); mouse('LEFTMOUSE', 'RELEASE', cx, cy)
    yield .3
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.bake_correction(action='BRUSH', kind='OFFSET')
    yield .5
    assert project.paint_correction == 'OFFSET' and low.mode == 'TEXTURE_PAINT', (project.paint_correction, low.mode)
    settings.corrections.value = .35
    assert abs(settings.corrections.value - .35) < 1e-3
    brush = scene.tool_settings.image_paint.brush
    unified = scene.tool_settings.image_paint.unified_paint_settings
    for owner in (brush, unified):
        owner.strength, owner.size = 1, 220
    report['steps'].append('correction brush session began')
    # Drag across the middle of the top face (the plate's footprint), seen from above.
    x0, x1 = region.x + int(region.width * .4), region.x + int(region.width * .6)
    y0, y1 = cy - int(region.height * .12), cy + int(region.height * .12)
    window.cursor_warp(x0, y0)
    mouse('MOUSEMOVE', 'NOTHING', x0, y0)
    yield .2
    mouse('LEFTMOUSE', 'PRESS', x0, y0)
    for step in range(1, 25):
        t = step / 24
        mouse('MOUSEMOVE', 'NOTHING', x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
        yield .033
    mouse('LEFTMOUSE', 'RELEASE', x1, y1)
    yield .5
    painted = images.pixels(settings.corrections.offset_image)[..., 0]
    report['painted_texels'] = int((np.abs(painted - .5) > .05).sum())
    assert report['painted_texels'] > 50, 'the drag must paint the correction image'
    report['steps'].append('stroke painted the projection distance')
    from open_painter.evaluation import cache
    report['debug'] = {'timer': bpy.app.timers.is_registered(cache.tick),
                       'paint_input': scene.as_pointer() in cache._paint_input,
                       'pending': scene.as_pointer() in corrections._pending,
                       'snapshot_differs': bool(not np.array_equal(corrections._snapshots.get(scene.as_pointer(), (0, None))[1], images.pixels(settings.corrections.offset_image))),
                       'modal': cache._modal_painting(scene), 'status': project.status}
    # The scheduler notices the stroke end, rebuilds the overlay and rebakes the region from its timer.
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        current = images.pixels(prop.image)[..., :3]
        if np.abs(current - baseline).max(-1).sum() > 0 and not corrections.pending_rebake(scene) \
                and scene.as_pointer() not in corrections._pending:
            break
        yield .2
    current = images.pixels(prop.image)[..., :3]
    changed = np.abs(current - baseline).max(-1) > .05
    report['changed_texels'] = int(changed.sum())
    report['status_line'] = project.status
    assert changed.any(), f'the live region rebake did not change the map: {project.status}'
    overlays = [o for o in scene.objects if o.get(corrections.OVERLAY)]
    report['overlay_objects'] = len(overlays)
    assert overlays, 'the corrected-cage overlay must exist while painting'
    report['steps'].append('live region rebake landed from the timer')
    shot = REPORT.with_suffix('.png')
    bpy.ops.screen.screenshot(filepath=str(shot))
    report['screenshot'] = str(shot)
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.bake_correction(action='STOP')
    yield .3
    assert not project.paint_correction and not [o for o in scene.objects if o.get(corrections.OVERLAY)]
    report['steps'].append('session ended cleanly')


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
