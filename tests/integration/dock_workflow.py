"""Exercise actual dock mouse events, property dragging and painting viewport."""

import sys, traceback
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.ui.dock import state

open_painter.register()
bpy.ops.wm.open_mainfile(filepath=str(root / "examples/open_painter_demo.blend"))
count = 0


def fail():
    traceback.print_exc()
    (root / ".blender-test/dock_result.txt").write_text(traceback.format_exc())
    bpy.ops.wm.quit_blender()


def area():
    return max(
        (a for a in bpy.context.screen.areas if a.type in {"VIEW_3D", "IMAGE_EDITOR"}),
        key=lambda a: a.width * a.height,
    )


def click(action, payload=None):
    current = area()
    ui = state.get(current)
    hit = next(h for h in ui.hits if h[4] == action and (payload is None or h[5] == payload))
    region = next(r for r in current.regions if r.type == "WINDOW")
    x = int(region.x + hit[0] + hit[2] / 2)
    y = int(region.y + hit[1] + hit[3] / 2)
    window = bpy.context.window
    for kind, value in [("MOUSEMOVE", "NOTHING"), ("LEFTMOUSE", "PRESS"), ("LEFTMOUSE", "RELEASE")]:
        window.event_simulate(type=kind, value=value, x=x, y=y)


def setup():
    try:
        bpy.ops.open_painter.open_editor(view_mode="SINGLE")
        bpy.app.timers.register(add_layer, first_interval=5)
    except Exception:
        fail()
    return None


def add_layer():
    global count
    try:
        assert bpy.data.workspaces.get("Layout")
        from open_painter.ui.editor import is_area

        assert is_area(area())
        ts = bpy.context.scene.open_painter.texture_sets[0]
        count = len(ts.layers)
        print(
            "DOCK_GEOMETRY",
            area().width,
            area().height,
            bpy.context.preferences.system.ui_scale,
            len(state.get(area()).hits),
        )
        click("LAYER", "FILL")
        bpy.app.timers.register(delete_layer, first_interval=1)
    except Exception:
        fail()
    return None


def delete_layer():
    try:
        ts = bpy.context.scene.open_painter.texture_sets[0]
        assert len(ts.layers) == count + 1, "Dock Fill button failed"
        click("LAYER", "DELETE")
        bpy.app.timers.register(select_layer, first_interval=1)
    except Exception:
        fail()
    return None


def select_layer():
    try:
        ts = bpy.context.scene.open_painter.texture_sets[0]
        assert len(ts.layers) == count, "Dock Delete button failed"
        click("SELECT_LAYER", 1)
        bpy.app.timers.register(drag_slider, first_interval=1)
    except Exception:
        fail()
    return None


def drag_slider():
    try:
        current = area()
        ui = state.get(current)
        hit = next(h for h in ui.hits if h[4] == "SLIDER" and h[5][0] == ("LAYER", "opacity"))
        region = next(r for r in current.regions if r.type == "WINDOW")
        start = hit[5][3]
        width = hit[5][4]
        x = int(region.x + start + width * 0.5)
        y = int(region.y + hit[1] + hit[3] / 2)
        window = bpy.context.window
        window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
        window.event_simulate(type="LEFTMOUSE", value="PRESS", x=x, y=y)
        window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x + 1, y=y)
        window.event_simulate(type="LEFTMOUSE", value="RELEASE", x=x + 1, y=y)
        bpy.app.timers.register(capture, first_interval=2)
    except Exception:
        fail()
    return None


def capture():
    try:
        ts = bpy.context.scene.open_painter.texture_sets[0]
        assert 0.45 < ts.layers[1].opacity < 0.55, f"Slider failed: {ts.layers[1].opacity}"
        ts.layers[1].opacity = 1
        bpy.app.timers.register(switch_2d, first_interval=2)
    except Exception:
        fail()
    return None


def switch_2d():
    try:
        click("VIEW", "IMAGE_EDITOR")
        bpy.app.timers.register(switch_3d, first_interval=1)
    except Exception:
        fail()
    return None


def switch_3d():
    try:
        assert area().type == "IMAGE_EDITOR", "2D view failed"
        click("VIEW", "VIEW_3D")
        bpy.app.timers.register(clear_hover, first_interval=1)
    except Exception:
        fail()
    return None


def clear_hover():
    current = area()
    bpy.context.window.event_simulate(
        type="MOUSEMOVE",
        value="NOTHING",
        x=current.x + current.width // 2,
        y=current.y + current.height // 2,
    )
    bpy.app.timers.register(finish, first_interval=1)
    return None


def finish():
    try:
        assert area().type == "VIEW_3D", "3D view failed"
        bpy.ops.wm.save_as_mainfile(filepath=str(root / "examples/open_painter_demo.blend"))
        bpy.ops.screen.screenshot(filepath=str(root / "docs/workspace.png"))
        (root / ".blender-test/dock_result.txt").write_text(
            "PASS: dock layout, actual Fill/Delete mouse clicks, layer selection and opacity drag"
        )
        print("DOCK WORKFLOW PASS")
        click("EXIT")
        bpy.app.timers.register(check_exit, first_interval=1)
    except Exception:
        fail()
    return None


def check_exit():
    try:
        assert bpy.context.workspace.name == "Layout", bpy.context.workspace.name
        print("EXIT PASS: returned to original Layout")
    except Exception:
        fail()
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(setup, first_interval=2)
