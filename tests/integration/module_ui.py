"""Real-window regression: the module box draws on a folder, Save Folder as Module
writes a library file and badges the row, Insert Module lists it and inserts an
instance, Update reports the version.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/module_ui.py
OP_MODULE_UI_REPORT selects the JSON report.
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
from open_painter.smart_materials import library, modules

REPORT = Path(os.environ.get('OP_MODULE_UI_REPORT', '/private/tmp/op-module-ui.json'))
report = {'started': time.monotonic(), 'steps': [], 'errors': []}
env = {}


def finish(status):
    report['elapsed_s'] = round(time.monotonic() - report.pop('started'), 2)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(dict(status=status, blender=bpy.app.version_string, **report), indent=2) + '\n')
    print('MODULE_UI_' + status, REPORT, flush=True)
    for path in env.get('cleanup', []):
        Path(path).unlink(missing_ok=True)
    bpy.ops.wm.quit_blender()


def texture_set():
    return bpy.context.scene.open_painter.texture_sets[0]


def steps():
    assert not bpy.app.background, 'This regression requires a real Blender GUI'
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    window = env['window'] = bpy.context.window
    window.event_simulate(type='ESC', value='PRESS')
    yield .5
    area = env['area'] = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_editor()
        bpy.ops.open_painter.texture_set(action='ADD')
    ts = texture_set()
    ts.resolution = '512'
    folder = stack.add(ts, 'FOLDER', 'Damage'); folder_id = folder.id
    child = stack.add(ts, 'FILL', 'Scratches'); child.parent_id = folder_id
    ts.active_layer = stack.index_of(ts, folder_id)
    yield .8
    sidebar = next(r for r in area.regions if r.type == 'UI' and r.width > 10)
    sidebar.active_panel_category = 'Painter'
    sidebar.tag_redraw()
    yield .5
    # Save as module through the operator (the file dialog is a native window; run it directly).
    target = library.directory(bpy.context) / 'GUI Module v1.openpaintmaterial'
    target.unlink(missing_ok=True)
    env['cleanup'] = [str(target)]
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.module_save(filepath=str(target), module_name='GUI Module')
    yield .5
    folder = next(l for l in texture_set().layers if l.id == folder_id)
    assert target.exists() and folder.module_id and folder.module_version == 1, (folder.module_id, folder.module_version)
    report['steps'].append('save folder as module')
    # The insert popup lists it; inserting places an instance.
    from open_painter.ui.modules import module_items
    items = module_items(None, bpy.context)
    assert any(str(target) == identifier for identifier, _, _ in items), items
    assert any(identifier == 'catalog:Damage' for identifier, _, _ in items)
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.module_insert(module=str(target))
    yield .5
    instances = modules.instances(texture_set(), folder.module_id)
    assert len(instances) == 2, [l.name for l in instances]
    report['steps'].append('insert module instance')
    # Draw once with the instance selected: the module box and badge render without errors.
    ts = texture_set()
    ts.active_layer = stack.index_of(ts, next(l.id for l in instances if l.module_instance))
    sidebar.tag_redraw()
    yield .6
    with bpy.context.temp_override(window=window, area=area, region=region):
        result = bpy.ops.open_painter.module_update()
    assert result == {'FINISHED'}, result
    instance = next(l for l in modules.instances(texture_set(), folder.module_id) if l.module_instance)
    assert instance.module_version == 1
    report['steps'].append('update on the current version is a no-op')
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
