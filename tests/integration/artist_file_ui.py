"""Real-window probe on the artist's asset file: the add-on is enabled, then the file is opened
(as at every Blender start), and the session must keep working through the scheduler's own
timer: the shader comes up, a native brush stroke lands on the 2048 canvas, a decal Empty
dragged with the transform operator follows without a rebuild, an unrelated part can be moved
freely, and Ctrl+S saves with the session intact.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/artist_file_ui.py
OP_ARTIST_FILE_UI_REPORT selects the JSON report; OP_ARTIST_FILE the .blend. Passes (SKIPPED)
without the file.
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
FILE = Path(os.environ.get('OP_ARTIST_FILE', ROOT / 'open painter test.blend'))
REPORT = Path(os.environ.get('OP_ARTIST_FILE_UI_REPORT', '/private/tmp/op-artist-file-ui.json'))
report = {'started': time.monotonic(), 'steps': [], 'errors': []}
env = {}


def finish(status):
    report['elapsed_s'] = round(time.monotonic() - report.pop('started'), 2)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(dict(status=status, blender=bpy.app.version_string, **report), indent=2) + '\n')
    print('ARTIST_FILE_UI_' + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()


def mouse(kind, value, x, y):
    env['window'].event_simulate(type=kind, value=value, x=int(x), y=int(y))


def steps():
    assert not bpy.app.background
    if not FILE.exists():
        report['skipped'] = str(FILE)
        return
    import open_painter
    from open_painter.core import image_manager as images, layer_stack as stack
    from open_painter.evaluation import cache, dependencies, shader_preview
    from open_painter.painting import decals
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    window = env['window'] = bpy.context.window
    window.event_simulate(type='ESC', value='PRESS')
    yield .5
    bpy.ops.wm.open_mainfile(filepath=str(FILE))
    yield .5
    window = env['window'] = bpy.context.window
    scene = bpy.context.scene
    project = scene.open_painter
    ts = project.texture_sets[0]
    low = next(r.object for r in ts.assignments if r.object)
    assert bpy.app.timers.is_registered(cache.tick), 'the scheduler timer must survive the file load'
    assert dependencies.on_depsgraph in bpy.app.handlers.depsgraph_update_post, 'the depsgraph handler must survive the file load'
    report['steps'].append('file opened with the add-on live')
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    area.spaces.active.shading.type = 'MATERIAL'
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_editor()
        bpy.ops.object.select_all(action='DESELECT')
        low.select_set(True)
        bpy.context.view_layer.objects.active = low
        bpy.ops.view3d.view_axis(type='TOP')
        bpy.ops.view3d.view_selected(use_all_regions=False)
    # The scheduler's own timer brings the session up.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and not shader_preview.current(scene, ts):
        yield .1
    info = shader_preview.diagnostics(scene, ts)
    assert shader_preview.current(scene, ts), info
    assert not info['warnings'], info['warnings']
    report['session'] = {k: info[k] for k in ('units', 'unique_images', 'graph_builds')}
    report['steps'].append('shader session current from the timer')

    # A paint layer, then a native brush drag across the prop seen from above.
    paint = stack.add(ts, 'PAINT', 'Hand-painted grime')
    paint_id = paint.id
    ts.active_layer = stack.index_of(ts, paint_id)
    project.paint_channel = 'BASE_COLOR'
    cx, cy = region.x + region.width // 2, region.y + region.height // 2
    window.cursor_warp(cx, cy)
    mouse('LEFTMOUSE', 'PRESS', cx, cy); mouse('LEFTMOUSE', 'RELEASE', cx, cy)
    yield .3
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.paint()
        bpy.ops.ed.undo_push(message='Paint Selected Channel')   # what the button click records
    yield .5
    assert project.paint_image is not None and low.mode == 'TEXTURE_PAINT', (project.paint_image, low.mode)
    canvas = project.paint_image
    canvas_name = canvas.name
    assert tuple(canvas.size) == (2048, 2048)
    brush = scene.tool_settings.image_paint.brush
    unified = scene.tool_settings.image_paint.unified_paint_settings
    for owner in (brush, unified):
        owner.strength, owner.size = 1, 80
    x0, x1 = region.x + int(region.width * .3), region.x + int(region.width * .7)
    window.cursor_warp(x0, cy)
    mouse('MOUSEMOVE', 'NOTHING', x0, cy)
    yield .2
    frozen_seen = 0
    mouse('LEFTMOUSE', 'PRESS', x0, cy)
    for step in range(1, 31):
        mouse('MOUSEMOVE', 'NOTHING', x0 + (x1 - x0) * step / 30, cy + 6 * ((-1) ** step))
        yield .033
        frozen_seen += bool(shader_preview.diagnostics(scene, ts)['frozen'])
    mouse('LEFTMOUSE', 'RELEASE', x1, cy)
    yield .6
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not shader_preview.current(scene, ts):
        yield .1
    assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
    report['canvas_alpha_max'] = float(images.pixels(canvas)[..., 3].max())
    report['frozen_observations'] = frozen_seen
    assert report['canvas_alpha_max'] > .5, 'the drag must paint the canvas'
    report['steps'].append('native stroke painted the 2048 canvas')
    # Ctrl+Z takes the stroke back through Blender's image undo; the session follows, and
    # Ctrl+Shift+Z brings it back.
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.ed.undo()
    yield .5
    scene, project = bpy.context.scene, bpy.context.scene.open_painter
    ts = project.texture_sets[0]
    canvas = bpy.data.images[canvas_name]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not shader_preview.current(scene, ts):
        yield .1
    assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
    report['canvas_alpha_after_undo'] = float(images.pixels(canvas)[..., 3].max())
    assert report['canvas_alpha_after_undo'] < .01, 'undo must take the stroke back'
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.ed.redo()
    yield .5
    scene, project = bpy.context.scene, bpy.context.scene.open_painter
    ts = project.texture_sets[0]
    canvas = bpy.data.images[canvas_name]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not shader_preview.current(scene, ts):
        yield .1
    assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
    assert float(images.pixels(canvas)[..., 3].max()) > .5, 'redo must bring the stroke back'
    low = next(r.object for r in ts.assignments if r.object)
    report['steps'].append('undo and redo of the stroke kept the session current')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.paint(stop=True)
    yield .3
    assert low.mode == 'OBJECT' and project.paint_image is None

    # A decal on the prop, then a real transform drag: the shader follows without a rebuild.
    decal_image = images.create('Caution decal', 64, (1., .8, 0., 1.))
    from open_painter.painting.projection import geometry
    positions, normals, on_mesh = geometry(ts, 128, coverage=True)
    upward = on_mesh & (normals[..., 2] > .9)
    spot = positions[upward][np.argmax(positions[upward][:, 2])]
    extent = max(low.dimensions)
    scene.cursor.location = (spot[0], spot[1], spot[2] + extent * .02)
    with bpy.context.temp_override(window=window, area=area, region=region, object=low, active_object=low):
        bpy.ops.open_painter.decal(action='ADD', name='Caution_01', image=decal_image.name, size=extent * .15)
    decal_layer = ts.layers[ts.active_layer]
    decal_id = decal_layer.id
    empty = decals.resolve(decal_layer)
    assert empty is not None and bpy.context.view_layer.objects.active == empty
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not shader_preview.current(scene, ts):
        yield .1
    builds = shader_preview.diagnostics(scene, ts)['graph_builds']
    serial = ts.edit_serial
    axes_before = tuple(next(l for l in ts.layers if l.id == decal_id).decal_axes)
    with bpy.context.temp_override(window=window, area=area, region=region, object=empty, active_object=empty,
                                   selected_objects=[empty], selected_editable_objects=[empty]):
        bpy.ops.transform.rotate(value=.6, orient_axis='Z')
        bpy.ops.transform.translate(value=(extent * .05, 0., 0.))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and (ts.edit_serial == serial or not shader_preview.current(scene, ts)):
        yield .1
    axes_after = tuple(next(l for l in ts.layers if l.id == decal_id).decal_axes)
    assert ts.edit_serial != serial, 'the scheduler never noticed the transform (depsgraph handler gone?)'
    assert axes_after != axes_before, 'the decal frame uniforms did not follow the rotation'
    assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
    assert shader_preview.diagnostics(scene, ts)['graph_builds'] == builds, 'a decal drag must not rebuild the shader'
    report['steps'].append('decal drag followed through the timer without a rebuild')

    # Moving an unrelated part leaves the set alone.
    other = next(o for o in scene.objects if o.type == 'MESH' and o != low)
    serial = ts.edit_serial
    with bpy.context.temp_override(window=window, area=area, region=region, object=other, active_object=other,
                                   selected_objects=[other], selected_editable_objects=[other]):
        bpy.ops.transform.translate(value=(0., 0., extent * .1))
    yield 1.0
    assert ts.edit_serial == serial, 'an unrelated transform invalidated the set'
    report['steps'].append('unrelated transform ignored')

    # Ctrl+S: the session survives the save handlers.
    target = ROOT / '.blender-test' / 'artist-file-ui.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(target), copy=True)
    yield .5
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not shader_preview.current(scene, ts):
        yield .1
    assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
    assert not shader_preview.diagnostics(scene, ts).get('warnings')
    shot = REPORT.with_suffix('.png')
    bpy.ops.screen.screenshot(filepath=str(shot))
    report['screenshot'] = str(shot)
    report['steps'].append('saved with the session intact')


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


# persistent: opening the file must not end the probe's own timer.
bpy.app.timers.register(poll, first_interval=1.0, persistent=True)
