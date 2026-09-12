"""Reference layout: two native paint views, settings tabs, and collapse back."""

import sys, traceback
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.ui import editor, paired_views
from open_painter.ui.dock import state, interaction
from open_painter.core import layer_stack

open_painter.register()
bpy.context.preferences.view.show_splash = False
result = root / ".blender-test/reference_layout_result.txt"
result.write_text("RUNNING")
area = None
initial = None


def later(fn, delay=0.8):
    def run():
        try:
            fn()
        except Exception:
            result.write_text(traceback.format_exc())
            traceback.print_exc()
            bpy.ops.wm.quit_blender()

    bpy.app.timers.register(run, first_interval=delay)


def with_area(a):
    return bpy.context.temp_override(
        area=a, region=next(r for r in a.regions if r.type == "WINDOW")
    )


def setup():
    global area, initial
    initial = len(bpy.context.screen.areas)
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    with with_area(area):
        bpy.ops.open_painter.open_editor()
        bpy.ops.open_painter.texture_set(action="ADD")
        ts = bpy.context.scene.open_painter.texture_sets[0]
        ts.resolution = "512"
        layer_stack.add(ts, "FILL")
        paired_views.both(bpy.context)
    later(check_pair, 2)


def check_pair():
    global area
    pair = paired_views.members(bpy.context.window, area)
    assert len(pair) == 2 and len(bpy.context.screen.areas) == initial + 1
    left, right = sorted(pair, key=lambda a: a.x)
    assert left.type == "VIEW_3D" and right.type == "IMAGE_EDITOR"
    assert state.get(left).right == 0 and state.get(right).left == 0
    assert right.spaces.active.image
    assert (
        not left.spaces.active.show_region_toolbar and not right.spaces.active.show_region_toolbar
    )
    area = right
    hit = next(h for h in state.get(right).hits if h[4] == "RIGHT_TAB" and h[5] == "TEXTURE")
    region = next(r for r in right.regions if r.type == "WINDOW")
    x, y = int(region.x + hit[0] + hit[2] / 2), int(region.y + hit[1] + hit[3] / 2)
    for kind, value in (("MOUSEMOVE", "NOTHING"), ("LEFTMOUSE", "PRESS"), ("LEFTMOUSE", "RELEASE")):
        bpy.context.window.event_simulate(type=kind, value=value, x=x, y=y)
    later(settings)


def settings():
    assert state.get(area).right_tab == "TEXTURE"
    assert not any(h[4] == "LAYER" and h[5] == "PAINT" for h in state.get(area).hits), (
        "Settings tab did not redraw"
    )
    assert any(h[4] == "POPUP" and h[5] == "TEXTURE" for h in state.get(area).hits)
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    later(capture)


def capture():
    bpy.ops.screen.screenshot(filepath=str(root / "docs/reference_layout.png"))
    # N from the image side toggles the same right dock; T toggles the left asset dock.
    for key in ("T", "N"):
        for value in ("PRESS", "RELEASE"):
            bpy.context.window.event_simulate(
                type=key, value=value, x=area.x + area.width // 2, y=area.y + area.height // 2
            )
    later(hidden)


def hidden():
    pair = paired_views.members(bpy.context.window, area)
    assert all(not state.get(a).show_assets and not state.get(a).show_properties for a in pair)
    assert all(state.get(a).left == 0 and state.get(a).right == 0 for a in pair)
    file = root / ".blender-test/paired_layout.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(file))
    bpy.ops.wm.open_mainfile(filepath=str(file))
    later(restored, 2)


def restored():
    global area
    painters = [a for a in bpy.context.screen.areas if editor.is_area(a)]
    assert len(painters) == 2
    area = next(a for a in painters if a.type == "IMAGE_EDITOR")
    assert len(paired_views.members(bpy.context.window, area)) == 2
    assert all(not state.get(a).show_assets and not state.get(a).show_properties for a in painters)
    with with_area(area):
        paired_views.single(bpy.context, "VIEW_3D")
    later(finish)


def finish():
    assert len(bpy.context.screen.areas) == initial
    assert len([a for a in bpy.context.screen.areas if editor.is_area(a)]) == 1
    result.write_text(
        "PASS: native 3D+2D views, live image target, single asset/property docks, settings tab, shared T/N, paired save/reload, and safe return to one view"
    )
    print(result.read_text())
    bpy.ops.wm.quit_blender()


later(setup, 2)
