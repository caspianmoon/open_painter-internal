"""Verify theme adaptation, viewport preferences and cancellable property input."""

import sys
import traceback
from pathlib import Path

import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.ui.dock import drawing, interaction, state

open_painter.register()
bpy.ops.wm.open_mainfile(filepath=str(root / "examples/open_painter_demo.blend"))
result = root / ".blender-test/native_ui_result.txt"
result.write_text("RUNNING")
area = None
original = None
mouse = None
popup_sections = []
original_draw = interaction.OP_OT_dock_popup.draw


def record_popup(self, context):
    popup_sections.append(self.section)
    original_draw(self, context)


interaction.OP_OT_dock_popup.draw = record_popup


def step(fn):
    def wrapped():
        try:
            fn()
        except Exception:
            result.write_text(traceback.format_exc())
            traceback.print_exc()
            bpy.ops.wm.quit_blender()
        return None

    return wrapped


def layer():
    return bpy.context.scene.open_painter.texture_sets[0].layers[1]


def event(kind, value="PRESS", x=None, shift=False):
    bpy.context.window.event_simulate(
        type=kind, value=value, x=int(mouse[0] if x is None else x), y=int(mouse[1]), shift=shift
    )


@step
def setup():
    global area, original
    bpy.ops.open_painter.open_editor(view_mode="SINGLE")
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    theme = bpy.context.preferences.themes[0]
    saved = tuple(theme.user_interface.wcol_regular.inner)
    theme.user_interface.wcol_regular.inner = (0.2, 0.35, 0.5, 1)
    drawing.sync_theme(bpy.context)
    assert drawing.CARD == tuple(theme.user_interface.wcol_regular.inner)
    assert drawing.CARD != saved, "Theme override not reflected"
    theme.user_interface.wcol_regular.inner = saved
    drawing.sync_theme(bpy.context)
    area.spaces.active.shading.type = "SOLID"
    area.spaces.active.overlay.show_overlays = True
    interaction.configure(area)
    assert area.spaces.active.shading.type == "SOLID"
    assert area.spaces.active.overlay.show_overlays
    ts = bpy.context.scene.open_painter.texture_sets[0]
    ts.active_layer = 1
    original = layer().opacity = 0.5
    bpy.app.timers.register(start_drag, first_interval=3)


@step
def start_drag():
    global mouse
    ui = state.get(area)
    hit = next(h for h in ui.hits if h[4] == "SLIDER" and h[5][0] == ("LAYER", "opacity"))
    region = next(r for r in area.regions if r.type == "WINDOW")
    mouse = (region.x + hit[0] + hit[2] * 0.5, region.y + hit[1] + hit[3] * 0.5)
    event("MOUSEMOVE", "NOTHING")
    event("LEFTMOUSE")
    event("MOUSEMOVE", "NOTHING", x=mouse[0] + hit[2] * 0.25)
    bpy.app.timers.register(cancel_drag, first_interval=0.5)


@step
def cancel_drag():
    assert 0.70 < layer().opacity < 0.80, layer().opacity
    event("ESC")
    event("LEFTMOUSE", "RELEASE")
    bpy.app.timers.register(precision_drag, first_interval=0.5)


@step
def precision_drag():
    assert abs(layer().opacity - original) < 1e-5, "Escape failed to restore value"
    event("MOUSEMOVE", "NOTHING")
    event("LEFTMOUSE")
    event("MOUSEMOVE", "NOTHING", x=mouse[0] + 100, shift=True)
    event("LEFTMOUSE", "RELEASE", x=mouse[0] + 100)
    bpy.app.timers.register(native_popup, first_interval=0.5)


@step
def native_popup():
    assert 0.50 < layer().opacity < 0.53, layer().opacity
    event("MOUSEMOVE", "NOTHING")
    event("RIGHTMOUSE")
    event("RIGHTMOUSE", "RELEASE")
    bpy.app.timers.register(edit_number, first_interval=1)


@step
def edit_number():
    # Popup contains a real RNA property field, with native text entry/undo.
    assert "NUMBER" in popup_sections, "Native numeric popup did not draw"
    event("ESC")
    layer().opacity = original
    result.write_text(
        "PASS: live Blender theme, preserved shading/overlays, Escape cancellation, Shift precision, native numeric popup"
    )
    print(result.read_text())
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(setup, first_interval=2)
