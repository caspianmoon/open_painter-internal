"""Real-window regression: layers reorder by pressing and dragging their rows in
the native Painter sidebar, sideways motion changes the folder, Esc cancels and
a plain click still selects.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/layer_drag_ui.py
OP_LAYER_DRAG_UI_REPORT selects the JSON report.
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
from open_painter.core import layer_stack as stack
from open_painter.ui import native
from open_painter.ui.dock import state as dock_state

REPORT = Path(os.environ.get('OP_LAYER_DRAG_UI_REPORT', '/private/tmp/op-layer-drag-ui.json'))
report = {'started': time.monotonic(), 'steps': [], 'errors': []}
env = {}


def finish(status):
    report['elapsed_s'] = round(time.monotonic() - report.pop('started'), 2)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(dict(status=status, blender=bpy.app.version_string, **report), indent=2) + '\n')
    print('LAYER_DRAG_UI_' + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()


def texture_set():
    return bpy.context.scene.open_painter.texture_sets[0]


def visible():
    ts = texture_set()
    return [(ts.layers[index].name, depth) for index, depth in stack.visible_rows(ts)]


def by_name(name):
    return next(layer for layer in texture_set().layers if layer.name == name)


def mouse(kind, value, x, y):
    env['window'].event_simulate(type=kind, value=value, x=int(x), y=int(y))


def row_centre(index):
    # Rows are one widget unit tall from the top edge of the list box.
    return env['top'] - env['pad'] - env['unit'] // 2 - index * env['unit']


def press(index):
    x, y = env['x'], row_centre(index)
    env['window'].cursor_warp(x, y)
    mouse('MOUSEMOVE', 'NOTHING', x, y)
    yield .3
    mouse('LEFTMOUSE', 'PRESS', x, y)
    env['held'] = (x, y)
    yield .3


def move(rows=0, columns=0):
    x0, y0 = env['held']
    x, y = x0 + columns * env['unit'], y0 - rows * env['unit']
    for step in range(1, 5):
        mouse('MOUSEMOVE', 'NOTHING', x0 + (x - x0) * step / 4, y0 + (y - y0) * step / 4)
    env['held'] = (x, y)
    yield .3


def release():
    mouse('LEFTMOUSE', 'RELEASE', *env['held'])
    yield .4


def steps():
    assert not bpy.app.background, 'This regression requires a real Blender GUI'
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    window = env['window'] = bpy.context.window
    area = env['area'] = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_editor()
        bpy.ops.open_painter.texture_set(action='ADD')
    ts = texture_set()
    ts.resolution = '512'
    for name in ['Bottom', 'Middle', 'Top']:
        stack.add(ts, 'FILL', name)
    assert visible() == [('Top', 0), ('Middle', 0), ('Bottom', 0)]
    yield 1.0
    sidebar = env['sidebar'] = next(r for r in area.regions if r.type == 'UI' and r.width > 10)
    sidebar.active_panel_category = 'Painter'
    sidebar.tag_redraw()
    yield .5
    system = bpy.context.preferences.system
    env['unit'] = int(round(18 * system.ui_scale) + 2 * system.pixel_size)
    env['pad'] = int(3 * system.pixel_size)
    # Over the name text, past the visibility and type icons of a nested row.
    env['x'] = sidebar.x + int(sidebar.width * .32)
    found = []
    for y in range(sidebar.y + sidebar.height - 1, sidebar.y, -2):
        window.cursor_warp(env['x'], y)
        with bpy.context.temp_override(window=window, area=area, region=sidebar):
            ui_list = bpy.context.ui_list
        if ui_list is not None and ui_list.bl_idname == 'OP_UL_native_layers':
            found.append(y)
    assert found, 'the layer list is not under the sidebar'
    env['top'] = max(found)
    report['list_px'] = (min(found), max(found))
    report['unit'] = env['unit']
    # The first press after launch only focuses the window.
    yield from press(0)
    yield from release()

    yield from press(0)
    assert stack.active(ts).name == 'Top'
    yield from move(rows=2)
    assert visible() == [('Middle', 0), ('Bottom', 0), ('Top', 0)], visible()
    assert native._drag['active']
    yield from release()
    assert not native._drag['active']
    assert visible() == [('Middle', 0), ('Bottom', 0), ('Top', 0)], visible()
    assert stack.active(ts).name == 'Top'
    report['steps'].append('drag down')

    yield from press(2)
    yield from move(rows=-2)
    yield from release()
    assert visible() == [('Top', 0), ('Middle', 0), ('Bottom', 0)], visible()
    report['steps'].append('drag up')

    ts.active_layer = stack.index_of(ts, by_name('Top').id)
    folder = stack.add(ts, 'FOLDER', 'Folder')
    folder_id = folder.id
    assert visible() == [('Folder', 0), ('Top', 0), ('Middle', 0), ('Bottom', 0)]
    yield .5
    yield from press(1)
    yield from move(columns=1.5)
    yield from release()
    assert by_name('Top').parent_id == folder_id
    assert visible() == [('Folder', 0), ('Top', 1), ('Middle', 0), ('Bottom', 0)], visible()
    report['steps'].append('drag into folder')

    saved = stack.arrangement(ts)
    yield from press(2)
    yield from move(rows=-2)
    assert visible()[0] == ('Middle', 0), visible()
    mouse('ESC', 'PRESS', *env['held'])
    mouse('ESC', 'RELEASE', *env['held'])
    yield .3
    assert not native._drag['active']
    assert stack.arrangement(ts) == saved
    assert visible() == [('Folder', 0), ('Top', 1), ('Middle', 0), ('Bottom', 0)], visible()
    yield from release()
    assert stack.arrangement(ts) == saved
    report['steps'].append('cancel')

    yield from press(3)
    yield from release()
    assert stack.active(ts).name == 'Bottom'
    assert dock_state.get(area).selected_layers == {by_name('Bottom').id}
    assert not dock_state.get(area).mask_selected
    assert not native._drag['active']
    report['steps'].append('click selects')

    # Ctrl-click (like a double-click) edits a name in place, as in
    # Blender's own lists; the press must not reach the drag operator.
    x, y = env['x'], row_centre(3)
    env['window'].event_simulate(type='LEFTMOUSE', value='PRESS', ctrl=True, x=x, y=y)
    env['window'].event_simulate(type='LEFTMOUSE', value='RELEASE', ctrl=True, x=x, y=y)
    yield .3
    assert not native._drag['active']
    mouse('BACK_SPACE', 'PRESS', x, y)
    mouse('BACK_SPACE', 'RELEASE', x, y)
    for key, char in (('B', 'B'), ('A', 'a'), ('S', 's'), ('E', 'e')):
        env['window'].event_simulate(type=key, value='PRESS', unicode=char, x=x, y=y)
        env['window'].event_simulate(type=key, value='RELEASE', x=x, y=y)
    mouse('RET', 'PRESS', x, y)
    mouse('RET', 'RELEASE', x, y)
    yield .4
    assert [layer.name for layer in ts.layers if layer.parent_id == ''] == ['Base', 'Middle', 'Folder'], [layer.name for layer in ts.layers]
    assert not native._drag['active']
    report['steps'].append('ctrl-click renames')

    channel = stack.channel_for(by_name('Base'), 'BASE_COLOR')
    channel.opacity_percent = 40
    assert abs(channel.opacity - .4) < 1e-6
    shot = REPORT.with_suffix('.png')
    bpy.ops.screen.screenshot(filepath=str(shot))
    report['screenshot'] = str(shot)
    yield .3


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
