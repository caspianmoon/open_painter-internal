"""Open new compositing controls through actual mouse input in Blender's editor."""

import sys
import traceback
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import layer_stack as stack
from open_painter.ui.dock import interaction, state

bpy.context.preferences.view.show_splash = False
open_painter.register()
bpy.ops.wm.open_mainfile(filepath=str(root / "examples/open_painter_demo.blend"))
result = root / ".blender-test/compositing_ui_result.txt"
result.write_text("RUNNING")
area = None
sections = []
original_draw = interaction.OP_OT_dock_popup.draw


def record_draw(self, context):
    original_draw(self, context)
    sections.append(self.section)


interaction.OP_OT_dock_popup.draw = record_draw


def step(fn):
    def run():
        try:
            if area:
                region = next(r for r in area.regions if r.type == "WINDOW")
                with bpy.context.temp_override(area=area, region=region):
                    fn()
            else:
                fn()
        except Exception:
            result.write_text(traceback.format_exc())
            traceback.print_exc()
            bpy.ops.wm.quit_blender()
        return None

    return run


@step
def setup():
    global area
    bpy.ops.open_painter.open_editor(view_mode="SINGLE")
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    ts = bpy.context.scene.open_painter.texture_sets[0]
    ts.active_layer = next(i for i, l in enumerate(ts.layers) if l.type == "FILL")
    bpy.context.scene.open_painter.blend_channel = "ROUGHNESS"
    prop = stack.channel_for(stack.active(ts), "ROUGHNESS")
    prop.blend_mode, prop.opacity = "SOFT_LIGHT", 0.7
    bpy.app.timers.register(open_channel_popup, first_interval=3)


@step
def open_channel_popup():
    ui = state.get(area)
    hit = next(h for h in ui.hits if h[4:6] == ("POPUP", "CHANNEL_BLEND"))
    region = bpy.context.region
    x, y = int(region.x + hit[0] + hit[2] * 0.5), int(region.y + hit[1] + hit[3] * 0.5)
    for kind, value in [("MOUSEMOVE", "NOTHING"), ("LEFTMOUSE", "PRESS"), ("LEFTMOUSE", "RELEASE")]:
        bpy.context.window.event_simulate(type=kind, value=value, x=x, y=y)
    bpy.app.timers.register(check_popup, first_interval=1)


@step
def check_popup():
    assert "CHANNEL_BLEND" in sections, sections
    bpy.ops.screen.screenshot(filepath=str(root / "docs/channel_blending.png"))
    bpy.context.window.event_simulate(type="ESC", value="PRESS")
    bpy.app.timers.register(open_folder_popup, first_interval=1)


@step
def open_folder_popup():
    ts = bpy.context.scene.open_painter.texture_sets[0]
    folder = stack.add(ts, "FOLDER", "Passthrough UI check")
    folder.group_mode = "PASSTHROUGH"
    bpy.ops.open_painter.dock_popup("INVOKE_DEFAULT", section="BLEND")
    bpy.app.timers.register(finish, first_interval=1)


@step
def finish():
    assert "BLEND" in sections
    result.write_text(
        "PASS: actual channel-blending button opens native channel/opacity/blend controls; folder composition popup draws"
    )
    print(result.read_text())
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(setup, first_interval=2)
