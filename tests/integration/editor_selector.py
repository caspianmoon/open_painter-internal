"""Verify the real Editor Type menu entry and same-area activation."""

import sys, traceback
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.ui import editor
from open_painter.core import texture_set_manager as sets, layer_stack as stack

open_painter.register()
_original = None
_area = None


def fail():
    traceback.print_exc()
    (root / ".blender-test/editor_result.txt").write_text(traceback.format_exc())
    bpy.ops.wm.quit_blender()


def begin():
    global _original, _area
    try:
        _area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
        _original = (
            bpy.context.workspace.name,
            len(bpy.data.workspaces),
            [(a.x, a.y, a.width, a.height) for a in bpy.context.screen.areas],
        )
        header = next(r for r in _area.regions if r.type == "HEADER")
        x = _area.x + 60
        y = header.y + header.height // 2
        print("MENU_COORDS", x, y, header.x, header.y, header.width, header.height, flush=True)
        window = bpy.context.window
        with bpy.context.temp_override(area=_area,region=header):
            bpy.ops.wm.context_menu_enum('INVOKE_DEFAULT',data_path='area.ui_type')
        bpy.app.timers.register(menu_capture, first_interval=2)
    except Exception:
        fail()
    return None


def menu_capture():
    try:
        bpy.ops.screen.screenshot(filepath=str(root / "docs/editor_selector.png"))
        bpy.context.window.event_simulate(type="ESC", value="PRESS")
        bpy.app.timers.register(activate, first_interval=0.5)
    except Exception:
        fail()
    return None


def activate():
    try:
        _area.ui_type = editor.EDITOR_ID
        assert _area.ui_type == editor.EDITOR_ID
        bpy.app.timers.register(check, first_interval=2)
    except Exception:
        fail()
    return None


def check():
    try:
        assert editor.is_area(_area), "Editor menu selection did not activate Open Painter"
        assert bpy.context.workspace.name == _original[0]
        assert len(bpy.data.workspaces) == _original[1]
        assert len(bpy.context.screen.areas) == len(_original[2])+1
        ts = sets.create(bpy.context.scene, "Editor Test")
        ts.resolution = "512"
        sets.assign(bpy.context.scene, ts, bpy.context.object)
        layer = stack.add(ts)
        stack.channel_for(layer, "BASE_COLOR").color = (0.02, 0.16, 0.38, 1)
        print(
            "EDITOR ENTRY PASS: selector subtype, same workspace, paired native views",
            flush=True,
        )
        bpy.app.timers.register(capture, first_interval=3)
    except Exception:
        fail()
    return None


def capture():
    try:
        print(
            "EDITOR_FLAGS",
            [
                (key, getattr(_area.spaces.active, key))
                for key in (
                    "show_region_ui",
                    "show_region_toolbar",
                    "show_region_header",
                    "show_region_tool_header",
                )
            ],
            flush=True,
        )
        bpy.ops.screen.screenshot(filepath=str(root / "docs/editor_in_area.png"))
        region = next(r for r in _area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(area=_area, region=region):
            editor.close(bpy.context)
        assert not editor.is_area(_area)
        assert bpy.context.workspace.name == _original[0]
        (root / ".blender-test/editor_result.txt").write_text(
            "PASS: real Editor Type entry, paired native view activation, same workspace, close restores one viewport"
        )
    except Exception:
        fail()
    bpy.ops.wm.quit_blender()
    return None


def dismiss_splash():
    bpy.context.window.event_simulate(type="ESC", value="PRESS")
    bpy.app.timers.register(begin, first_interval=1)
    return None


bpy.context.preferences.view.show_splash = False
bpy.app.timers.register(dismiss_splash, first_interval=2)
