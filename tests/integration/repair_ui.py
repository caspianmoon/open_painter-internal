"""Actual keyboard/mouse regression for groups, panel toggles and dock drawing cost."""

import sys, traceback, statistics
from time import perf_counter
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.ui.dock import state, renderer, drawing, assets
from open_painter.core import layer_stack as stack

bpy.context.preferences.view.show_splash = False
result = root / ".blender-test/repair_ui_result.txt"
result.write_text("RUNNING")
area = None
times = []
original_draw = renderer.draw


def timed_draw():
    start = perf_counter()
    original_draw()
    if state.is_painter(bpy.context):
        times.append(perf_counter() - start)


renderer.draw = timed_draw
open_painter.register()


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


def later(fn, delay=0.4):
    bpy.app.timers.register(fn, first_interval=delay)


def ts():
    return bpy.context.scene.open_painter.texture_sets[0]


def hit(action, payload=None):
    ui = state.get(area)
    found = next(h for h in ui.hits if h[4] == action and (payload is None or h[5] == payload))
    region = bpy.context.region
    return int(region.x + found[0] + found[2] * 0.6), int(region.y + found[1] + found[3] * 0.5)


def event(kind, value="PRESS", point=None, ctrl=False):
    region = bpy.context.region
    point = point or (region.x + region.width // 2, region.y + region.height // 2)
    bpy.context.window.event_simulate(type=kind, value=value, x=point[0], y=point[1], ctrl=ctrl)


def click(action, payload=None, ctrl=False):
    point = hit(action, payload)
    event("MOUSEMOVE", "NOTHING", point)
    event("LEFTMOUSE", "PRESS", point, ctrl)
    event("LEFTMOUSE", "RELEASE", point, ctrl)


@step
def setup():
    global area
    bpy.ops.open_painter.open_editor(view_mode="SINGLE")
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    bpy.ops.open_painter.texture_set(action="ADD")
    assert bpy.context.object.active_material == ts().material and len(ts().outputs) == 6
    ts().resolution = "512"
    for name in ["A", "B", "C"]:
        stack.add(ts(), "FILL", name)
    later(select_a, 2)


@step
def select_a():
    click("SELECT_LAYER", 0)
    later(select_b)


@step
def select_b():
    click("SELECT_LAYER", 1, ctrl=True)
    later(group)


@step
def group():
    assert len(state.get(area).selected_layers) == 2
    event("G", ctrl=True)
    event("G", "RELEASE", ctrl=True)
    later(collapse)


@step
def collapse():
    folder = stack.active(ts())
    assert folder.type == "FOLDER"
    assert sum(item.parent_id == folder.id for item in ts().layers) == 2
    click("GROUP_TOGGLE", folder.id)
    later(expand)


@step
def expand():
    folder = stack.active(ts())
    assert not folder.expanded
    assert len(stack.visible_rows(ts())) == 2
    click("GROUP_TOGGLE", folder.id)
    later(drag_in)


@step
def drag_in():
    source = next(i for i, l in enumerate(ts().layers) if l.name == "C")
    target = next(i for i, l in enumerate(ts().layers) if l.type == "FOLDER")
    a, b = hit("SELECT_LAYER", source), hit("SELECT_LAYER", target)
    event("LEFTMOUSE", point=a)
    event("MOUSEMOVE", "NOTHING", b)
    event("LEFTMOUSE", "RELEASE", b)
    later(drag_out)


@step
def drag_out():
    source = next(i for i, l in enumerate(ts().layers) if l.name == "C")
    assert ts().layers[source].parent_id
    a, b = hit("SELECT_LAYER", source), hit("GROUP_ROOT")
    event("LEFTMOUSE", point=a)
    event("MOUSEMOVE", "NOTHING", b)
    event("LEFTMOUSE", "RELEASE", b)
    later(hide_panels)


@step
def hide_panels():
    assert not next(l for l in ts().layers if l.name == "C").parent_id
    event("T")
    event("T", "RELEASE")
    event("N")
    event("N", "RELEASE")
    later(check_hidden, 1.5)


@step
def check_hidden():
    ui = state.get(area)
    assert not ui.show_assets and not ui.show_properties
    assert ui.left == 0 and ui.right == 0
    assert not any(h[4] in {"SEARCH", "SELECT_LAYER"} for h in ui.hits)
    event("T")
    event("T", "RELEASE")
    event("N")
    event("N", "RELEASE")
    later(show_material)


@step
def show_material():
    ui = state.get(area)
    assert ui.show_assets and ui.show_properties and ui.left > 0 and ui.right > 0
    entry = next(e for e in assets.MATERIALS if e[0] == "Copper")
    # Same path as a shelf click, without requiring the user to scroll to this entry.
    from open_painter.ui.dock.interaction import route

    route(bpy.context, ui, "ASSET", entry)
    later(start_paint, 1)


stroke_revision = 0
stroke_point = None
detect_calls = 0
idle_detect_calls = 0


@step
def start_paint():
    global stroke_revision, stroke_point
    from open_painter.painting import paint_session
    from open_painter.evaluation.evaluator import LayerEvaluator
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    bpy.ops.open_painter.layer(action="PAINT")
    paint_session.begin(bpy.context, ts(), "ROUGHNESS")
    LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts().id)
    stroke_revision = next(o.revision for o in ts().outputs if o.channel == "ROUGHNESS")
    brush = bpy.context.scene.tool_settings.image_paint.brush
    brush.size, brush.strength = 45, 1
    point = location_3d_to_region_2d(
        bpy.context.region,
        area.spaces.active.region_3d,
        bpy.context.object.matrix_world.translation,
    )
    stroke_point = (int(point.x + bpy.context.region.x), int(point.y + bpy.context.region.y))
    event("LEFTMOUSE", point=stroke_point)
    later(release_stroke, 0.3)


@step
def release_stroke():
    event("LEFTMOUSE", "RELEASE", stroke_point)
    later(check_stroke, 1.5)


@step
def check_stroke():
    global idle_detect_calls
    import numpy as np
    from open_painter.core.image_manager import pixels
    from open_painter.painting import paint_session

    assert np.count_nonzero(pixels(bpy.context.scene.open_painter.paint_image)[..., 3]) > 0
    assert next(o.revision for o in ts().outputs if o.channel == "ROUGHNESS") > stroke_revision, (
        "Native stroke did not automatically composite"
    )
    # Idle input must not keep reading and hashing a dirty canvas.
    original_detect = paint_session.detect

    def counted(*args, **kwargs):
        global detect_calls
        detect_calls += 1
        return original_detect(*args, **kwargs)

    paint_session.detect = counted
    idle_detect_calls = detect_calls
    later(start_profile)


profile_count = 0


@step
def start_profile():
    global profile_count
    profile_count += 1
    area.tag_redraw()
    if profile_count < 35:
        later(start_profile, 0.05)
    else:
        later(finish)


@step
def finish():
    assert not area.spaces.active.show_region_asset_shelf, "Native brush shelf covers the painter docks"
    bpy.ops.screen.screenshot(filepath=str(root / "docs/repaired_workspace.png"))
    assert drawing.rounded_batch.cache_info().hits > drawing.rounded_batch.cache_info().misses
    assert detect_calls == idle_detect_calls, "Idle painting repeatedly scanned texture pixels"
    median = statistics.median(times[10:]) * 1000
    text = f"PASS: real Ctrl selection/grouping, collapse/expand, drag into/out of groups, T/N hide/restore survive timer, real stroke auto-composites, no idle canvas scans; warm dock draw median {median:.2f} ms ({len(times)} draws)"
    result.write_text(text)
    print(text)
    bpy.ops.wm.quit_blender()


later(setup, 2)
