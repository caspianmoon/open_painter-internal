"""Cube regression: empty layers do not re-evaluate; strokes preview before release."""

import sys, traceback, time, statistics
from pathlib import Path
import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.evaluation import cache
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.painting import paint_session

paired = "--paired" in sys.argv
result = root / (
    ".blender-test/paired_paint_result.txt" if paired else ".blender-test/realtime_ui_result.txt"
)
result.write_text("RUNNING")
timings = []
first_update = None
move_time = None
original_tick = cache.tick


def measured_tick():
    start = time.perf_counter()
    global first_update
    try:
        value = original_tick()
        if (
            move_time
            and first_update is None
            and next(o.revision for o in ts().outputs if o.channel == "BASE_COLOR") > revision
        ):
            first_update = (time.perf_counter() - move_time) * 1000
        return value
    finally:
        timings.append((time.perf_counter() - start) * 1000)


cache.tick = measured_tick
open_painter.register()
bpy.context.preferences.view.show_splash = False
area = None
point = None
before = None
revision = None


def later(fn, delay=0.5):
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

    bpy.app.timers.register(run, first_interval=delay)


def ts():
    return bpy.context.scene.open_painter.texture_sets[0]


def event(kind, value):
    bpy.context.window.event_simulate(type=kind, value=value, x=point[0], y=point[1])


def setup():
    global area, before
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    with bpy.context.temp_override(area=area):
        bpy.ops.open_painter.open_editor(view_mode="BOTH" if paired else "SINGLE")
        bpy.ops.open_painter.texture_set(action="ADD")
        ts().resolution = "2048"
        LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts().id)
        before = [o.revision for o in ts().outputs]
        bpy.ops.open_painter.layer(action="PAINT")
    later(empty, 1)


def empty():
    global point, revision
    assert before == [o.revision for o in ts().outputs], (
        "Empty paint layer recomposited output images"
    )
    paint_session.begin(bpy.context, ts(), "BASE_COLOR")
    LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts().id)
    revision = next(o.revision for o in ts().outputs if o.channel == "BASE_COLOR")
    p = location_3d_to_region_2d(
        bpy.context.region,
        area.spaces.active.region_3d,
        bpy.context.object.matrix_world.translation,
    )
    point = (int(p.x + bpy.context.region.x), int(p.y + bpy.context.region.y))
    bpy.context.scene.tool_settings.image_paint.brush.size = 55
    timings.clear()
    later(stroke)


def stroke():
    event("MOUSEMOVE", "NOTHING")
    event("LEFTMOUSE", "PRESS")
    later(move, 0.2)


def move():
    global point, move_time, revision
    if ts().dirty:
        later(move, 0.1)
        return
    revision = next(o.revision for o in ts().outputs if o.channel == "BASE_COLOR")
    move_time = time.perf_counter()
    point = (point[0] + 15, point[1] + 5)
    event("MOUSEMOVE", "NOTHING")
    later(held, 1.2)


def held():
    if paired:
        from open_painter.ui.paired_views import members

        peer = next(a for a in members(bpy.context.window, area) if a.type == "IMAGE_EDITOR")
        assert peer.spaces.active.image == bpy.context.scene.open_painter.paint_image
    assert any(o.bl_idname == "PAINT_OT_image_paint" for o in bpy.context.window.modal_operators), (
        "Brush is not held"
    )
    assert next(o.revision for o in ts().outputs if o.channel == "BASE_COLOR") > revision, (
        "Preview waited for mouse release"
    )
    event("LEFTMOUSE", "RELEASE")
    later(finish, 1)


def finish():
    assert not ts().dirty, "Preview never settled"
    import numpy as np
    from open_painter.core.image_manager import pixels

    output = next(o for o in ts().outputs if o.channel == "BASE_COLOR")
    actual = pixels(output.image)
    LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts().id, force=True)
    np.testing.assert_allclose(pixels(output.image), actual, atol=1e-6)
    text = f"PASS: 2K cube, empty paint layer leaves outputs untouched, stroke composites while mouse is held, settles after release; first moved-stroke preview {first_update:.2f} ms; timer median {statistics.median(timings):.2f} ms, maximum {max(timings):.2f} ms"
    result.write_text(text)
    print(text)
    global idle_reads, original_pixels
    idle_reads = []
    original_pixels = paint_session.pixels
    def counted(image):
        idle_reads.append(image.name)
        return original_pixels(image)
    paint_session.pixels = counted
    later(idle, 1.5)


def idle():
    assert not idle_reads, f"Idle pixel scans: {idle_reads}"
    paint_session.pixels = original_pixels
    global point, revision
    point = (point[0] - 15, point[1])
    from open_painter.painting.brush_bridge import color_owner
    color_owner(bpy.context.scene).color = (0.1, 0.2, 0.3)
    global click_before
    click_before = original_pixels(bpy.context.scene.open_painter.paint_image)
    revision = next(o.revision for o in ts().outputs if o.channel == "BASE_COLOR")
    event("MOUSEMOVE", "NOTHING")
    event("LEFTMOUSE", "PRESS")
    event("LEFTMOUSE", "RELEASE")
    later(clicked, 1.5)


def clicked():
    import numpy as np
    print("CLICK_DIAGNOSTIC", np.max(np.abs(original_pixels(bpy.context.scene.open_painter.paint_image) - click_before)), [op.bl_idname for op in bpy.context.window_manager.operators][-10:], flush=True)
    assert next(o.revision for o in ts().outputs if o.channel == "BASE_COLOR") > revision, "Short native click did not composite"
    result.write_text(result.read_text() + "; no idle pixel scans; short native click updates composite")
    bpy.ops.wm.quit_blender()


later(setup, 2)
