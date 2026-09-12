"""Dedicated window launch, source isolation, dock shortcuts, close and reopen."""

import sys, traceback
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.ui import editor
from open_painter.ui.dock import state

open_painter.register()
bpy.context.preferences.view.show_splash = False
result = root / ".blender-test/separate_window_result.txt"
result.write_text("RUNNING")
source = bpy.context.window
painter = None
snapshot = None


def later(fn, delay=1):
    def run():
        try:
            fn()
        except Exception:
            result.write_text(traceback.format_exc())
            traceback.print_exc()
            bpy.ops.wm.quit_blender()

    bpy.app.timers.register(run, first_interval=delay)


def signature():
    return [(a.as_pointer(), a.type, a.width, a.height) for a in source.screen.areas]


def launch():
    global snapshot, painter
    snapshot = signature()
    before = {w.as_pointer() for w in bpy.context.window_manager.windows}
    area = next(a for a in source.screen.areas if a.type == "VIEW_3D")
    with bpy.context.temp_override(window=source, area=area):
        assert bpy.ops.open_painter.open_editor() == {"FINISHED"}
    painter = next(w for w in bpy.context.window_manager.windows if w.as_pointer() not in before)
    later(check, 2)


def check():
    assert signature() == snapshot, "Main window layout changed"
    assert not any(editor.is_area(a) for a in source.screen.areas)
    assert painter.scene == source.scene
    areas = list(painter.screen.areas)
    assert len(areas) == 2 and {a.type for a in areas} == {"VIEW_3D", "IMAGE_EDITOR"}
    assert all(editor.is_area(a) for a in areas)
    assert not hasattr(bpy.types, "OP_EditorType")
    left = next(a for a in areas if a.type == "VIEW_3D")
    with bpy.context.temp_override(
        window=painter, area=left, region=next(r for r in left.regions if r.type == "WINDOW")
    ):
        bpy.ops.open_painter.texture_set(action="ADD")
        painter.scene.open_painter.texture_sets[0].resolution = "512"
        bpy.ops.open_painter.layer(action="PAINT")
        bpy.ops.open_painter.paint(channel="BASE_COLOR")
    later(stroke, 2)


def stroke():
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    left = next(a for a in painter.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in left.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=painter, area=left, region=region):
        p = location_3d_to_region_2d(
            region, left.spaces.active.region_3d, bpy.context.object.location
        )
    x, y = int(p.x + region.x), int(p.y + region.y)
    for kind, value in [("MOUSEMOVE", "NOTHING"), ("LEFTMOUSE", "PRESS"), ("LEFTMOUSE", "RELEASE")]:
        painter.event_simulate(type=kind, value=value, x=x, y=y)
    later(painted, 2)


def painted():
    import numpy as np

    image = painter.scene.open_painter.paint_image
    assert image
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    assert np.count_nonzero(pixels[3::4]) > 0, "Painting in the separate window failed"
    assert signature() == snapshot
    areas = list(painter.screen.areas)
    right = next(a for a in areas if a.type == "IMAGE_EDITOR")
    for key in ("T", "N"):
        for val in ("PRESS", "RELEASE"):
            painter.event_simulate(
                type=key, value=val, x=right.x + right.width // 2, y=right.y + right.height // 2
            )
    later(hidden)


def hidden():
    assert all(
        not state.get(a).show_assets and not state.get(a).show_properties
        for a in painter.screen.areas
    )
    assert signature() == snapshot
    with bpy.context.temp_override(window=painter):
        bpy.ops.wm.window_close()
    later(reopen)


def reopen():
    assert len(bpy.context.window_manager.windows) == 1
    assert not source.scene.open_painter.paint_image
    assert signature() == snapshot
    area = next(a for a in source.screen.areas if a.type == "VIEW_3D")
    with bpy.context.temp_override(window=source, area=area):
        bpy.ops.open_painter.open_editor()
    later(finish, 2)


def finish():
    windows = [w for w in bpy.context.window_manager.windows if w != source]
    assert len(windows) == 1 and len(windows[0].screen.areas) == 2
    assert signature() == snapshot
    assert all(
        state.get(a).show_assets and state.get(a).show_properties for a in windows[0].screen.areas
    )
    result.write_text(
        "PASS: separate window, paired Painter layout, unchanged main window, shared scene and real painting, no Editor Type registration, T/N scoped to Painter, OS close and reopen"
    )
    print(result.read_text())
    bpy.ops.wm.quit_blender()


later(launch, 2)
