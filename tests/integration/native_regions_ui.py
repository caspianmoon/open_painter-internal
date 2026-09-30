"""Native regions, no overlay, persistent T/N, hierarchical UIList and workspace."""
import sys, traceback
from pathlib import Path
from types import SimpleNamespace
import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
import open_painter
from open_painter.ui.dock import lifecycle
from open_painter.ui.native import OP_UL_native_layers
from open_painter.core import layer_stack

result = root / '.blender-test/native_regions_result.txt'
result.write_text('RUNNING')
open_painter.register()
bpy.context.preferences.view.show_splash = False
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
initial = len(bpy.context.screen.areas)


def later(fn):
    def run():
        global area
        try:
            if area not in list(bpy.context.screen.areas):
                area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
            with bpy.context.temp_override(area=area, region=next(r for r in area.regions if r.type == 'WINDOW')):
                fn()
        except Exception:
            result.write_text(traceback.format_exc())
            traceback.print_exc()
            bpy.ops.wm.quit_blender()
    bpy.app.timers.register(run, first_interval=1)


def keys():
    for key in ('T','N'):
        for value in ('PRESS','RELEASE'):
            bpy.context.window.event_simulate(type=key,value=value,x=area.x+area.width//2,y=area.y+area.height//2)


def setup():
    # The dock synchronizes native regions on a timer but draws nothing over them.
    assert not lifecycle._handlers
    assert not hasattr(bpy.types, 'OP_EditorType')
    bpy.ops.open_painter.open_editor()
    assert len(bpy.context.screen.areas) == initial and area.type == 'VIEW_3D'
    area.spaces.active.show_region_toolbar = True
    area.spaces.active.show_region_ui = True
    bpy.ops.open_painter.texture_set(action='ADD')
    ts = bpy.context.scene.open_painter.texture_sets[0]
    ts.resolution = '512'
    folder_id = layer_stack.add(ts, 'FOLDER').id
    layer_stack.add(ts, 'FILL', parent_id=folder_id)
    # The hierarchical native list; resolved afresh after adds (a collection may move).
    flags, order = OP_UL_native_layers.filter_items(SimpleNamespace(bitflag_filter_item=1), bpy.context, ts, 'layers')
    assert order[0] < order[1] and flags == [1,1]
    next(item for item in ts.layers if item.id == folder_id).expanded = False
    flags, order = OP_UL_native_layers.filter_items(SimpleNamespace(bitflag_filter_item=1), bpy.context, ts, 'layers')
    assert flags == [1,0]
    later(toggle)


def toggle():
    keys()
    later(hidden)


def hidden():
    assert not area.spaces.active.show_region_toolbar
    assert not area.spaces.active.show_region_ui
    assert not any(op.bl_idname == 'OPEN_PAINTER_OT_dock_events' for op in bpy.context.window.modal_operators)
    keys()
    later(restored)


def restored():
    global workspace_name
    assert area.spaces.active.show_region_toolbar and area.spaces.active.show_region_ui
    workspace_name = bpy.context.workspace.name
    bpy.ops.open_painter.workspace()
    later(workspace)


def workspace():
    # The compatibility operator opens Painter in this area: no workspace switch, no new area.
    from open_painter.ui import editor
    assert bpy.context.workspace.name == workspace_name
    assert len(bpy.context.screen.areas) == initial
    assert editor.is_area(area)
    result.write_text('PASS: native toolbar/sidebar T/N, no overlay draw handlers or modal capture, nested/collapsible native layer list, Painter in place of a workspace switch')
    print(result.read_text())
    bpy.ops.wm.quit_blender()

later(setup)
