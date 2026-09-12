"""Real keyboard regression for empty projects and lost modal handlers."""

import sys
import traceback
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.ui.dock import state

result = root / ".blender-test/panel_lifecycle_result.txt"
result.write_text("RUNNING")
bpy.context.preferences.view.show_splash = False
open_painter.register()
area = None


def schedule(fn, delay=0.7):
    def run():
        try:
            fn()
        except Exception:
            result.write_text(traceback.format_exc())
            traceback.print_exc()
            bpy.ops.wm.quit_blender()

    bpy.app.timers.register(run, first_interval=delay)


def keys(center=False):
    # Pointer over the left dock where the overlapping shelf was reported.
    for key in ("T", "N"):
        for value in ("PRESS", "RELEASE"):
            bpy.context.window.event_simulate(
                type=key,
                value=value,
                x=area.x + (area.width // 2 if center else 30),
                y=area.y + area.height // 2,
            )


def clean():
    assert not area.spaces.active.show_region_toolbar
    assert not area.spaces.active.show_region_ui
    assert (
        sum(
            op.bl_idname == "OPEN_PAINTER_OT_dock_events"
            for op in bpy.context.window.modal_operators
        )
        == 1
    )


def setup():
    global area
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    with bpy.context.temp_override(area=area):
        bpy.ops.open_painter.open_editor(view_mode="SINGLE")
    assert not bpy.context.scene.open_painter.texture_sets
    schedule(expose)


def expose():
    clean()
    area.spaces.active.show_region_toolbar = True
    area.spaces.active.show_region_ui = True
    schedule(hide, 2)


def hide():
    clean()
    keys()
    schedule(hidden)


def hidden():
    clean()
    assert not state.get(area).show_assets and not state.get(area).show_properties
    keys()
    schedule(switch)


def switch():
    clean()
    assert state.get(area).show_assets and state.get(area).show_properties
    area.type = "TEXT_EDITOR"
    schedule(reopen)


def reopen():
    # A stale entry must not prevent replacement of the removed modal handler.
    state.windows.add(bpy.context.window.as_pointer())
    with bpy.context.temp_override(area=area):
        bpy.ops.open_painter.open_editor(view_mode="SINGLE")
    schedule(rehide, 1.2)


def rehide():
    clean()
    keys()
    schedule(maximize)


def maximize():
    global area
    clean()
    assert not state.get(area).show_assets and not state.get(area).show_properties
    window = bpy.context.window
    with bpy.context.temp_override(area=area):
        bpy.ops.screen.screen_full_area()
    area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
    schedule(after_maximize)


def after_maximize():
    from open_painter.ui import editor

    assert editor.is_area(area), "Maximizing lost painter ownership"
    clean()
    keys()
    schedule(image_view)


def image_view():
    assert state.get(area).show_assets and state.get(area).show_properties
    from open_painter.ui.dock.interaction import route

    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(area=area, region=region):
        route(bpy.context, state.get(area), "VIEW", "IMAGE_EDITOR")
    schedule(image_keys)


def image_keys():
    clean()
    keys()
    schedule(finish)


def finish():
    clean()
    assert not state.get(area).show_assets and not state.get(area).show_properties
    from open_painter.ui.dock.interaction import route

    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(area=area, region=region):
        route(bpy.context, state.get(area), "VIEW", "VIEW_3D")
    schedule(unmaximize)


def unmaximize():
    global area
    window = bpy.context.window
    with bpy.context.temp_override(area=area):
        bpy.ops.screen.screen_full_area()
    area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
    schedule(close)


def close():
    from open_painter.ui import editor

    assert editor.is_area(area)
    clean()
    assert not state.get(area).show_assets and not state.get(area).show_properties
    with bpy.context.temp_override(area=area):
        bpy.ops.open_painter.close_editor()
    schedule(native_keys)


def native_keys():
    assert area.spaces.active.show_region_toolbar
    assert not area.spaces.active.show_region_ui
    keys(center=True)
    schedule(native_check, 1.2)


def native_check():
    assert not area.spaces.active.show_region_toolbar
    assert area.spaces.active.show_region_ui
    result.write_text(
        "PASS: empty project, native toolbar/sidebar recovery, T/N over left dock, editor switch and stale handler recovery, maximize/unmaximize, 2D/3D, ordinary Blender T/N after close; exactly one input handler"
    )
    print(result.read_text())
    bpy.ops.wm.quit_blender()


schedule(setup, 2)
