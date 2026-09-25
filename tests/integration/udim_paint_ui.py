"""Real Blender brushes on UDIM 1002 and across the 1001/1002 boundary.

Run with --factory-startup --enable-event-simulate (no --background).
"""
import json
import sys
import traceback
from pathlib import Path

import bpy
import numpy as np
from bpy_extras.view3d_utils import location_3d_to_region_2d

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, image_manager as images
from open_painter.painting import paint_session

REPORT = ROOT / '.blender-test/udim-paint-ui.json'
report = {'blender': bpy.app.version_string, 'steps': [], 'errors': []}


def steps():
    assert not bpy.app.background
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    window = bpy.context.window
    window.event_simulate(type='ESC', value='PRESS')
    yield .5
    scene = bpy.context.scene
    project = scene.open_painter
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    mesh = bpy.data.meshes.new('UDIM')
    mesh.from_pydata([(-1, 0, 0), (0, 0, 0), (1, 0, 0), (-1, 1, 0), (0, 1, 0), (1, 1, 0)],
                     [], [(0, 1, 4, 3), (1, 2, 5, 4)])
    obj = bpy.data.objects.new('UDIM brush test', mesh)
    scene.collection.objects.link(obj)
    source = mesh.uv_layers.new(name='UVMap')
    original = np.array([(0, 0), (1, 0), (1, 1), (0, 1), (1, 0), (2, 0), (2, 1), (1, 1)], np.float32)
    source.data.foreach_set('uv', original.ravel())
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.ed.undo_push(message='Before UDIM creation')
    assert bpy.ops.open_painter.texture_set(action='ADD_UDIM', udim_name='Brush', udim_resolution='512') == {'FINISHED'}
    bpy.ops.ed.undo_push(message='After UDIM creation')
    assert bpy.ops.ed.undo() == {'FINISHED'}
    yield .2
    project = bpy.context.scene.open_painter
    assert len(project.texture_sets) == 0
    assert len(bpy.data.objects['UDIM brush test'].material_slots) == 0
    assert bpy.ops.ed.redo() == {'FINISHED'}
    yield .2
    scene, project = bpy.context.scene, bpy.context.scene.open_painter
    obj = bpy.data.objects['UDIM brush test']
    mesh, source = obj.data, obj.data.uv_layers['UVMap']
    assert [ts.udim_tile for ts in project.texture_sets] == [1001, 1002]
    assert [p.material_index for p in mesh.polygons] == [1, 2]
    report['steps'].append('UDIM creation operator undo and redo restore sets and material assignments')
    members = list(project.texture_sets)
    for ts in members:
        base = stack.add(ts, 'FILL', 'Base')
        stack.channel_for(base, 'BASE_COLOR').color = (.04, .08, .16, 1)
    second = members[1]
    paint = stack.add(second, 'PAINT', 'Brush marks')
    second.active_layer = stack.index_of(second, paint.id)
    project.active_texture_set = 1
    project.paint_channel = 'BASE_COLOR'
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view3d.view_axis(type='TOP')
        bpy.ops.view3d.view_selected()
    yield 1

    def brush(points):
        paint_settings = scene.tool_settings.image_paint
        for owner in (paint_settings.brush, paint_settings.unified_paint_settings):
            owner.color, owner.strength, owner.size = (.9, .9, .9), 1, 28
        paint_settings.brush.blend = 'MIX'
        stroke = []
        for point in points:
            xy = location_3d_to_region_2d(region, area.spaces.active.region_3d, point)
            stroke.append(dict(name='stroke', location=point, mouse=(xy.x, xy.y), mouse_event=(xy.x, xy.y),
                               size=28, pressure=1., time=0., is_start=not stroke, x_tilt=0., y_tilt=0.))
        with bpy.context.temp_override(window=window, area=area, region=region):
            assert bpy.ops.paint.image_paint(stroke=stroke) == {'FINISHED'}

    with bpy.context.temp_override(window=window, area=area, region=region):
        canvas = paint_session.begin(bpy.context, second, 'BASE_COLOR')
    yield .5
    brush([(float(x), .35, 0) for x in np.linspace(.2, .8, 10)])
    yield .3
    pixels = images.pixels(canvas)
    assert pixels[150:210, 90:420, 3].max() > .5, 'native brush missed tile 1002'
    assert pixels[400:, :, 3].max() == 0, 'native brush landed at wrong UV coordinates'
    paint_session.finish(bpy.context)
    assert mesh.uv_layers.active.name == 'UVMap'
    assert source.active_render
    report['steps'].append('native brush paints local pixels on tile 1002; source UV selection restored')

    project.paint_all_sets = True
    with bpy.context.temp_override(window=window, area=area, region=region):
        paint_session.begin_multi(bpy.context, second, 'BASE_COLOR')
    yield .5
    canvases = [entry.image for entry in project.paint_canvases]
    before = [images.pixels(image) for image in canvases]
    brush([(float(x), .65, 0) for x in np.linspace(-.8, .8, 25)])
    yield .3
    assert len(canvases) == 2
    for image, prior in zip(canvases, before):
        pixels = images.pixels(image)
        assert np.max(pixels[290:390, :, 3] - prior[290:390, :, 3]) > .5, image.name
    report['steps'].append('one native stroke across the UDIM boundary paints both tile canvases')
    paint_session.finish(bpy.context)
    after = np.empty_like(original)
    source.data.foreach_get('uv', after.ravel())
    assert np.array_equal(original, after) and mesh.uv_layers.active.name == 'UVMap' and source.active_render
    report['steps'].append('finish preserves source UVs and render UV map')
    area.spaces.active.shading.type = 'MATERIAL'
    yield 3
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.screen.screenshot(filepath=str(REPORT.with_suffix('.png')))


generator = steps()


def poll():
    try:
        return next(generator)
    except StopIteration:
        status = 'PASS'
    except Exception:
        report['errors'].append(traceback.format_exc())
        traceback.print_exc()
        status = 'FAIL'
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(dict(status=status, **report), indent=2))
    print('UDIM_PAINT_UI_' + status, flush=True)
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(poll, first_interval=1)
