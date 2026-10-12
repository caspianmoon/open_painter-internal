"""Phase 0 baseline of painting (real window): the vector brush and the native brush as they are.

Measures on the production prop (25+ layers, live shader session), at 2K unless
OP_QUALITY_PAINT_RESOLUTION says otherwise:
- a held vector-brush drag: samples kept versus pointer events, whether anything of the
  material changes on screen before release (the bound layer images), and the time from
  release to the shader session reporting current;
- what Esc does during a gesture (commit or discard);
- a native single-channel stroke for comparison (event to current after release).

Writes .blender-test/quality/baseline-painting.json. Launch:
    blender --factory-startup --enable-event-simulate --python tests/integration/quality_baseline_paint_ui.py
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import quality_fixtures as fixtures
from open_painter.core import image_manager as images, layer_stack as stack
from open_painter.evaluation import shader_preview
from open_painter.painting import stroke_raster, strokes as records
from open_painter.ui.vector_brush import OP_OT_vector_brush

OUT = ROOT / ".blender-test/quality"
OUT.mkdir(parents=True, exist_ok=True)
REPORT = Path(os.environ.get("OP_QUALITY_BASELINE_PAINT_REPORT", OUT / "baseline-painting.json"))
RESOLUTION = int(os.environ.get("OP_QUALITY_PAINT_RESOLUTION", "2048"))
report = {"blender": bpy.app.version_string, "steps": [], "errors": [], "measurements": {}}
env = {}


def finish(status):
    REPORT.write_text(json.dumps(dict(status=status, **report), indent=2))
    print("QUALITY_BASELINE_PAINT_" + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()


def mouse(kind, value, x, y):
    env["window"].event_simulate(type=kind, value=value, x=int(x), y=int(y))


from gpu_input_probe import bound_inputs as bound_stroke_images, input_pixels


def steps():
    assert not bpy.app.background
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    window = env["window"] = bpy.context.window
    window.event_simulate(type="ESC", value="PRESS")
    yield .5
    scene = bpy.context.scene
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    body, barrel, plate = fixtures.production_prop(scene, resolution=RESOLUTION, layers=25)
    body.preview_backend = "LIVE"
    area = max((a for a in window.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_editor()
        bpy.ops.object.select_all(action="DESELECT")
        obj = scene.objects["Prop body"]
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.view3d.view_axis(type="TOP")
        bpy.ops.view3d.view_selected(use_all_regions=False)
    area.spaces.active.shading.type = "MATERIAL"
    scene.open_painter.active_texture_set = next(i for i, t in enumerate(scene.open_painter.texture_sets) if t.id == body.id)
    layer = next(l for l in body.layers if l.name == "Mud paint")
    body.active_layer = stack.index_of(body, layer.id)
    started = time.monotonic()
    while time.monotonic() - started < 240 and not shader_preview.current(scene, body):
        yield .2
    assert shader_preview.current(scene, body), shader_preview.diagnostics(scene, body)
    report["measurements"]["first_compile_s"] = round(time.monotonic() - started, 1)
    report["steps"].append("production prop at 2K compiled")

    # --- vector brush: a fast held drag ---
    settings = scene.open_painter.vector_brush
    settings.radius_mode, settings.radius, settings.spacing = "WORLD", .08, .25
    settings.channels = {"BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT"}
    settings.base_color, settings.roughness, settings.metallic, settings.height = (.55, .25, .1, 1), .8, 0.0, .6
    cx, cy = region.x + region.width // 2, region.y + region.height // 2
    window.cursor_warp(cx, cy)
    mouse("LEFTMOUSE", "PRESS", cx, cy); mouse("LEFTMOUSE", "RELEASE", cx, cy)
    yield .3
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.vector_brush("INVOKE_DEFAULT")
    yield .3
    assert OP_OT_vector_brush._handler is not None
    before = bound_stroke_images(scene, body, layer)
    before_pixels = {c: input_pixels(i).copy() for c, i in before.items()}
    x0, x1 = region.x + int(region.width * .42), region.x + int(region.width * .58)
    from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
    view = area.spaces.active.region_3d
    hits = []
    for x in (x0, (x0 + x1) // 2, x1):
        xy = (x - region.x, cy - region.y)
        origin, direction = region_2d_to_origin_3d(region, view, xy), region_2d_to_vector_3d(region, view, xy)
        hit = scene.ray_cast(bpy.context.evaluated_depsgraph_get(), origin, direction)
        hits.append((bool(hit[0]), hit[4].name if hit[0] else None))
    report["measurements"]["drag_hits"] = hits
    events = 6   # a fast drag: few pointer events over a long path
    window.cursor_warp(x0, cy)
    mouse("MOUSEMOVE", "NOTHING", x0, cy)
    yield .2
    mouse("LEFTMOUSE", "PRESS", x0, cy)
    changed_during = False
    for step in range(1, events + 1):
        mouse("MOUSEMOVE", "NOTHING", x0 + (x1 - x0) * step / events, cy)
        yield .04
        now = bound_stroke_images(scene, body, layer)
        for channel, image in now.items():
            if channel in before_pixels and not np.array_equal(input_pixels(image), before_pixels[channel]):
                changed_during = True
    released = time.monotonic()
    mouse("LEFTMOUSE", "RELEASE", x1, cy)
    yield .05
    body = next(t for t in scene.open_painter.texture_sets if t.name == "Body")
    layer = next(l for l in body.layers if l.name == "Mud paint")   # collections may have been reallocated
    strokes = [s for s in scene.open_painter.strokes if s.layer_key == layer.stroke_key]
    report["measurements"]["strokes_total"] = len(scene.open_painter.strokes)
    assert len(strokes) == 1, (len(strokes), len(scene.open_painter.strokes), layer.stroke_key, scene.open_painter.status)
    samples = stroke_raster.unpack_samples(strokes[0].samples)
    gaps = np.linalg.norm(np.diff(samples[:, :3], axis=0), axis=1) / np.maximum(samples[1:, 7], 1e-6)
    # Placeholder watch: does the bound image go through a not-ready state after release?
    grey_seen, deadline = False, time.monotonic() + 60
    while time.monotonic() < deadline and not shader_preview.current(scene, body):
        for image in bound_stroke_images(scene, body, layer).values():
            if not image.get("op_shader_resource_ready", True):
                grey_seen = True
        yield .05
    settle = time.monotonic() - released
    report["measurements"]["vector_fast_drag"] = {
        "pointer_events": events, "samples_kept": int(len(samples)),
        "largest_gap_in_radii": float(gaps.max()) if len(gaps) else 0.0,
        "material_changed_during_drag": changed_during,
        "release_to_current_s": round(settle, 3), "placeholder_seen_after_release": grey_seen,
        "current": shader_preview.current(scene, body),
    }
    report["steps"].append("vector brush fast drag measured")

    # --- Esc during a gesture ---
    count_before = len(scene.open_painter.strokes)
    mouse("MOUSEMOVE", "NOTHING", x0, cy - 40)
    yield .05
    mouse("LEFTMOUSE", "PRESS", x0, cy - 40)
    for step in range(1, 6):
        mouse("MOUSEMOVE", "NOTHING", x0 + 30 * step, cy - 40)
        yield .04
    window.event_simulate(type="ESC", value="PRESS")
    yield .3
    report["measurements"]["esc_during_gesture"] = {
        "strokes_before": count_before, "strokes_after": len(scene.open_painter.strokes),
        "committed": len(scene.open_painter.strokes) > count_before,
        "handler_removed": OP_OT_vector_brush._handler is None,
    }
    if OP_OT_vector_brush._handler is not None:
        window.event_simulate(type="ESC", value="PRESS")
        yield .3
    report["steps"].append("Esc during a gesture measured")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not shader_preview.current(scene, body):
        yield .1

    # --- native single-channel stroke for comparison ---
    project = scene.open_painter
    project.paint_channel = "BASE_COLOR"
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.paint()
    yield .5
    assert project.paint_image is not None
    brush = scene.tool_settings.image_paint.brush
    unified = scene.tool_settings.image_paint.unified_paint_settings
    for owner in (brush, unified):
        owner.strength, owner.size = 1, 60
    window.cursor_warp(x0, cy + 60)
    mouse("MOUSEMOVE", "NOTHING", x0, cy + 60)
    yield .1
    mouse("LEFTMOUSE", "PRESS", x0, cy + 60)
    for step in range(1, 13):
        mouse("MOUSEMOVE", "NOTHING", x0 + (x1 - x0) * step / 12, cy + 60)
        yield .04
    released = time.monotonic()
    mouse("LEFTMOUSE", "RELEASE", x1, cy + 60)
    yield .05
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not shader_preview.current(scene, body):
        yield .05
    report["measurements"]["native_stroke"] = {"release_to_current_s": round(time.monotonic() - released, 3),
                                               "current": shader_preview.current(scene, body)}
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.paint(stop=True)
    yield .3
    report["steps"].append("native stroke measured")
    bpy.ops.screen.screenshot(filepath=str(OUT / "baseline-painting.png"))


generator = steps()


def poll():
    try:
        return next(generator)
    except StopIteration:
        finish("PASS")
    except Exception:
        report["errors"].append(traceback.format_exc())
        traceback.print_exc()
        finish("FAIL")


bpy.app.timers.register(poll, first_interval=1.0, persistent=True)
