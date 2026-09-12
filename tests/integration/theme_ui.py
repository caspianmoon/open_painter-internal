"""Render dark/light themes and custom widget styling in a real Blender window."""

import sys
import traceback
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.ui.dock import drawing

result = root / ".blender-test/theme_ui_result.txt"
result.write_text("RUNNING")
bpy.context.preferences.view.show_splash = False
open_painter.register()
area = None


def later(fn):
    def run():
        try:
            fn()
        except Exception:
            result.write_text(traceback.format_exc())
            traceback.print_exc()
            bpy.ops.wm.quit_blender()

    bpy.app.timers.register(run, first_interval=1.5)


def setup():
    global area
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    with bpy.context.temp_override(area=area):
        bpy.ops.open_painter.open_editor(view_mode="SINGLE")
    later(dark)


def dark():
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    bpy.ops.screen.screenshot(filepath=str(root / "docs/theme_dark.png"))
    path = Path(bpy.utils.system_resource("SCRIPTS")) / "presets/interface_theme/Blender_Light.xml"
    bpy.ops.script.execute_preset(
        filepath=str(path), menu_idname="USERPREF_MT_interface_theme_presets"
    )
    area.tag_redraw()
    later(light)


def light():
    assert drawing.TEXT[0] < 0.5, "Light theme still uses dark-theme text"
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    bpy.ops.screen.screenshot(filepath=str(root / "docs/theme_light.png"))
    ui = bpy.context.preferences.themes[0].user_interface
    ui.wcol_tool.inner = (0.25, 0.4, 0.5, 1)
    ui.wcol_tool.outline = (0.1, 0.2, 0.3, 1)
    ui.wcol_tool.show_shaded = True
    ui.wcol_tool.shadetop = 20
    ui.wcol_tool.shadedown = -20
    ui.wcol_radio.text_sel = (0.9, 0.2, 0.1)
    area.tag_redraw()
    later(custom)


def custom():
    assert drawing.shaded_batch.cache_info().currsize > 0, "Custom shaded widgets were not drawn"
    assert abs(drawing.WIDGETS["radio"]["text_sel"][0] - 0.9) < 0.01
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    bpy.ops.screen.screenshot(filepath=str(root / ".blender-test/theme_custom.png"))
    result.write_text(
        "PASS: dark/light themes render; light text contrast, custom tool shading and radio selected text follow live preferences"
    )
    print(result.read_text())
    bpy.ops.wm.quit_blender()


later(setup)
