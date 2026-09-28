"""Paint Material in a real window (quality remedy M1, plan §5.8-5.9): pointer events through
the modal operator on the 2K production prop with a live shader session.

Measures and checks:
- a fast drag (few pointer events over a long path) leaves no holes: the largest sample gap
  is at the brush spacing;
- the material changes on the model while the pointer is down (the layer's channel inputs
  are replaced by preview images that change), and the event-to-visible latency, taken as
  pointer event → preview publication → next viewport redraw, at the 95th percentile;
- release → shader session current (settle), with the preview bound until the leaf publishes;
- Esc during a gesture discards it and leaves the tool running; Esc while idle exits;
- Ctrl+Z after a stroke removes exactly that stroke (one undo step);
- the native single-channel route still paints.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/material_painting_ui.py
OP_MATERIAL_UI_RESOLUTION (default 2048) and OP_MATERIAL_UI_LAYERS (25) size the fixture;
OP_MATERIAL_UI_REPORT selects the JSON report.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path
from time import perf_counter

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import quality_fixtures as fixtures
from open_painter.core import image_manager as images, layer_stack as stack
from open_painter.evaluation import shader_preview
from open_painter.painting import gesture, stroke_raster, strokes as records
from open_painter.ui.vector_brush import OP_OT_vector_brush

RESOLUTION = int(os.environ.get("OP_MATERIAL_UI_RESOLUTION", "2048"))
LAYERS = int(os.environ.get("OP_MATERIAL_UI_LAYERS", "25"))
OUT = ROOT / ".blender-test/quality"
OUT.mkdir(parents=True, exist_ok=True)
REPORT = Path(os.environ.get("OP_MATERIAL_UI_REPORT", OUT / f"material-painting-ui-{RESOLUTION}.json"))
report = {"blender": bpy.app.version_string, "resolution": RESOLUTION, "layers": LAYERS, "steps": [], "errors": [],
          "measurements": {}}
env = {"draws": [], "publications": []}


def finish(status):
    REPORT.write_text(json.dumps(dict(status=status, **report), indent=2))
    print("MATERIAL_PAINTING_UI_" + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()


def mouse(kind, value, x, y):
    env["window"].event_simulate(type=kind, value=value, x=int(x), y=int(y))


def progress(message):
    report["steps"].append(message)
    print("MATERIAL_PAINTING_UI step:", message, flush=True)


def key(kind, ctrl=False):
    env["window"].event_simulate(type=kind, value="PRESS", ctrl=ctrl)
    env["window"].event_simulate(type=kind, value="RELEASE", ctrl=ctrl)


from gpu_input_probe import bound_inputs, input_pixels


def latency_stats(events, publications, draws):
    """Per pointer event: the first redraw after the first publication after it, split into
    event → publication (the operator's work) and publication → redraw (Blender's draw)."""
    latencies, to_publish, to_draw = [], [], []
    publications, draws = np.asarray(publications), np.asarray(draws)
    for t in events:
        later = publications[publications > t]
        if not len(later):
            continue
        shown = draws[draws > later[0]]
        if not len(shown):
            continue
        latencies.append(shown[0] - t)
        to_publish.append(later[0] - t)
        to_draw.append(shown[0] - later[0])
    if not latencies:
        return {"events": len(events), "measured": 0}

    def stats(values):
        values = np.asarray(values) * 1000
        return {"p50_ms": round(float(np.percentile(values, 50)), 1), "p95_ms": round(float(np.percentile(values, 95)), 1),
                "max_ms": round(float(values.max()), 1)}
    return {"events": len(events), "measured": int(len(latencies)), **stats(latencies),
            "event_to_publish": stats(to_publish), "publish_to_draw": stats(to_draw)}


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
    body, barrel, plate = fixtures.production_prop(scene, resolution=RESOLUTION, layers=LAYERS)
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
    compile_deadline = float(os.environ.get("OP_MATERIAL_UI_COMPILE_SECONDS", "300"))
    while time.monotonic() - started < compile_deadline and not shader_preview.current(scene, body):
        yield .2
    assert shader_preview.current(scene, body), shader_preview.diagnostics(scene, body)
    report["measurements"]["first_compile_s"] = round(time.monotonic() - started, 1)
    progress(f"production prop at {RESOLUTION} compiled")

    def draw_stamp():
        env["draws"].append(perf_counter())
    handler = bpy.types.SpaceView3D.draw_handler_add(draw_stamp, (), "WINDOW", "POST_PIXEL")
    original_publish = gesture.Preview.publish

    def timed_publish(self, *args, **kwargs):
        done = original_publish(self, *args, **kwargs)
        if done:
            env["publications"].append(perf_counter())
        return done
    gesture.Preview.publish = timed_publish
    if os.environ.get("OP_MATERIAL_UI_NO_PREVIEW"):
        real_init = gesture.Gesture.__init__

        def no_preview_init(self, *args, **kwargs):
            kwargs["preview"] = False
            real_init(self, *args, **kwargs)
        gesture.Gesture.__init__ = no_preview_init
    if os.environ.get("OP_MATERIAL_UI_NO_RETIRE"):
        gesture.RETIRE_PREVIEWS = False
    real_tick = shader_preview.tick

    def timed_tick(*args, **kwargs):
        started_at = perf_counter()
        result = real_tick(*args, **kwargs)
        env.setdefault("ticks", []).append((started_at, perf_counter() - started_at))
        return result
    shader_preview.tick = timed_tick

    settings = scene.open_painter.vector_brush
    settings.radius_mode, settings.radius, settings.spacing, settings.hardness = "WORLD", .08, .25, .8
    settings.universal, settings.blend, settings.depth_limit = False, "PAINT", .5
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
    x0, x1 = region.x + int(region.width * .42), region.x + int(region.width * .58)

    def resolve():
        ts = next(t for t in scene.open_painter.texture_sets if t.name == "Body")
        return ts, next(l for l in ts.layers if l.name == "Mud paint")

    def strokes_of(layer):
        return [s for s in scene.open_painter.strokes if layer.stroke_key and s.layer_key == layer.stroke_key]

    # --- warm-up: the first stroke on a layer without strokes adds a shader node (one rebuild) ---
    window.cursor_warp(x0, cy + 90)
    mouse("MOUSEMOVE", "NOTHING", x0, cy + 90)
    yield .1
    mouse("LEFTMOUSE", "PRESS", x0, cy + 90)
    for step in range(1, 9):
        mouse("MOUSEMOVE", "NOTHING", x0 + (x1 - x0) * step / 8, cy + 90)
        yield .03
    released = perf_counter()
    mouse("LEFTMOUSE", "RELEASE", x1, cy + 90)
    yield .05
    body, layer = resolve()
    assert len(strokes_of(layer)) == 1, (len(strokes_of(layer)), scene.open_painter.status)
    deadline = time.monotonic() + 120
    grey = False
    while time.monotonic() < deadline and not shader_preview.current(scene, body):
        for image in bound_inputs(scene, body, layer).values():
            if image.get("open_painter_shader_resource") and not image.get("op_shader_resource_ready"):
                grey = True
        yield .02
    report["measurements"]["first_stroke_on_fresh_layer"] = {
        "release_to_current_s": round(perf_counter() - released, 3), "placeholder_seen": grey,
        "current": shader_preview.current(scene, body)}
    progress("warm-up stroke (structural rebuild) measured")
    yield .5    # the leaves the warm-up published reach the GPU before the measured gesture starts

    # --- the measured fast drag: 6 pointer events over 16% of the region width ---
    env["draws"].clear(); env["publications"].clear(); env["ticks"] = []
    body, layer = resolve()
    before = {c: input_pixels(i).copy() for c, i in bound_inputs(scene, body, layer).items()}
    events, injected = 6, []
    window.cursor_warp(x0, cy)
    mouse("MOUSEMOVE", "NOTHING", x0, cy)
    yield .1
    injected.append(perf_counter())
    mouse("LEFTMOUSE", "PRESS", x0, cy)
    yield .03
    changed_during, previews_bound = False, False
    for step in range(1, events + 1):
        injected.append(perf_counter())
        mouse("MOUSEMOVE", "NOTHING", x0 + (x1 - x0) * step / events, cy)
        yield .04
        now = bound_inputs(scene, body, layer)
        if any(image.get("op_gesture_preview") for image in now.values()):
            previews_bound = True
        for channel, image in now.items():
            if channel in before and not np.array_equal(input_pixels(image), before[channel]):
                changed_during = True
    active_gesture = OP_OT_vector_brush._active._gesture
    profile = dict(active_gesture.preview.profile) if active_gesture.preview is not None else {}
    profile.update({k: (v if not isinstance(v, list) else list(v)) for k, v in active_gesture.profile.items()})
    drag_ticks = [(round((t - injected[0]) * 1000, 1), round(d * 1000, 1)) for t, d in env["ticks"] if t >= injected[0] and d > .002]
    env["ticks"] = []
    released = perf_counter()
    mouse("LEFTMOUSE", "RELEASE", x1, cy)
    yield .02
    body, layer = resolve()
    strokes = strokes_of(layer)
    assert len(strokes) == 2, (len(strokes), scene.open_painter.status)
    samples = stroke_raster.unpack_samples(strokes[-1].samples)
    gaps = np.linalg.norm(np.diff(samples[:, :3], axis=0), axis=1) / np.maximum(samples[1:, 7], 1e-6)
    still_bound = any(image.get("op_gesture_preview") for image in bound_inputs(scene, body, layer).values())
    deadline = time.monotonic() + 120
    from open_painter.evaluation.metrics import Metrics
    import cProfile, pstats, io
    profiler = cProfile.Profile()
    profiler.enable()
    with Metrics() as observed:
        while time.monotonic() < deadline and not shader_preview.current(scene, body):
            yield .01
    profiler.disable()
    settle = perf_counter() - released
    if os.environ.get("OP_MATERIAL_UI_PROFILE"):
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(45)
        Path(os.environ["OP_MATERIAL_UI_PROFILE"]).write_text(stream.getvalue())
    ticks = [t for t in env["ticks"] if t[0] >= released]
    report["measurements"]["fast_drag_settle_metrics"] = {
        "counts": {k: v for k, v in observed.counts.items() if "resource" in k or "stroke" in k or "raster" in k},
        "samples": {k: round(float(np.sum(v)), 1) for k, v in observed.samples.items()},
        "shader_ticks": len(ticks), "tick_ms": [round(d * 1000, 1) for _, d in ticks],
        "tick_gaps_ms": [round((b[0] - a[0] - a[1]) * 1000, 1) for a, b in zip(ticks, ticks[1:])],
        "first_tick_after_release_ms": round((ticks[0][0] - released) * 1000, 1) if ticks else None,
    }
    print("SETTLE METRICS", json.dumps(report["measurements"]["fast_drag_settle_metrics"]), flush=True)
    if not shader_preview.current(scene, body):
        report["measurements"]["fast_drag_diagnostics"] = shader_preview.diagnostics(scene, body)
        print("DIAGNOSTICS", shader_preview.diagnostics(scene, body), flush=True)
    yield .3
    gesture._reap()
    leftover = [image.name for image in bpy.data.images if image.get("op_gesture_preview") and image.users]
    report["measurements"]["fast_drag"] = {
        "pointer_events": events, "samples_kept": int(len(samples)),
        "largest_gap_in_radii": round(float(gaps.max()), 3) if len(gaps) else 0.0,
        "spacing_in_radii": settings.spacing,
        "material_changed_during_drag": changed_during, "previews_bound_during_drag": previews_bound,
        "preview_bound_after_release": still_bound,
        "release_to_current_s": round(settle, 3), "current": shader_preview.current(scene, body),
        "latency": latency_stats(injected, env["publications"], env["draws"]),
        "preview_images_left": leftover,
        "preview_profile": {k: (round(v, 1) if not isinstance(v, list) else [round(x, 1) for x in v]) for k, v in profile.items()},
        "ticks_during_drag": drag_ticks,
        "end_ms": round(active_gesture.profile["end_ms"], 1),
    }
    print("FAST DRAG", json.dumps(report["measurements"]["fast_drag"]), flush=True)
    if not os.environ.get("OP_MATERIAL_UI_NO_PREVIEW"):
        assert changed_during and previews_bound, report["measurements"]["fast_drag"]
    assert gaps.max() <= settings.spacing * 1.5 + 1e-3, float(gaps.max())
    assert not leftover, leftover
    progress("fast drag measured")

    # --- a slow drag with many events (the settle and latency of an ordinary stroke) ---
    env["draws"].clear(); env["publications"].clear()
    injected = []
    window.cursor_warp(x0, cy - 60)
    mouse("MOUSEMOVE", "NOTHING", x0, cy - 60)
    yield .1
    injected.append(perf_counter())
    mouse("LEFTMOUSE", "PRESS", x0, cy - 60)
    yield .02
    for step in range(1, 41):
        injected.append(perf_counter())
        mouse("MOUSEMOVE", "NOTHING", x0 + (x1 - x0) * step / 40, cy - 60 + 12 * np.sin(step / 4))
        yield .016
    released = perf_counter()
    mouse("LEFTMOUSE", "RELEASE", x1, cy - 60)
    yield .02
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and not shader_preview.current(scene, body):
        yield .01
    body, layer = resolve()
    report["measurements"]["slow_drag"] = {
        "pointer_events": 40, "release_to_current_s": round(perf_counter() - released, 3),
        "samples_kept": int(strokes_of(layer)[-1].sample_count),
        "latency": latency_stats(injected, env["publications"], env["draws"]),
    }
    assert len(strokes_of(layer)) == 3
    progress("slow drag measured")

    # --- Esc during a gesture discards it; the tool keeps running ---
    count_before = len(scene.open_painter.strokes)
    mouse("MOUSEMOVE", "NOTHING", x0, cy - 120)
    yield .05
    mouse("LEFTMOUSE", "PRESS", x0, cy - 120)
    for step in range(1, 6):
        mouse("MOUSEMOVE", "NOTHING", x0 + 30 * step, cy - 120)
        yield .04
    window.event_simulate(type="ESC", value="PRESS")
    yield .3
    body, layer = resolve()
    esc = {"strokes_before": count_before, "strokes_after": len(scene.open_painter.strokes),
           "tool_running": OP_OT_vector_brush._handler is not None,
           "preview_left_bound": any(i.get("op_gesture_preview") for i in bound_inputs(scene, body, layer).values()),
           "status": scene.open_painter.status}
    report["measurements"]["esc_during_gesture"] = esc
    assert esc["strokes_after"] == count_before and esc["tool_running"] and not esc["preview_left_bound"], esc
    mouse("LEFTMOUSE", "RELEASE", x0 + 150, cy - 120)   # the release after a discard commits nothing
    yield .2
    assert len(scene.open_painter.strokes) == count_before
    progress("Esc discards the gesture")

    # --- Ctrl+Z after a stroke removes exactly that stroke ---
    before_undo = len(scene.open_painter.strokes)
    mouse("MOUSEMOVE", "NOTHING", x0, cy + 40)
    yield .05
    mouse("LEFTMOUSE", "PRESS", x0, cy + 40)
    for step in range(1, 9):
        mouse("MOUSEMOVE", "NOTHING", x0 + (x1 - x0) * step / 8, cy + 40)
        yield .03
    mouse("LEFTMOUSE", "RELEASE", x1, cy + 40)
    yield .3
    assert len(scene.open_painter.strokes) == before_undo + 1
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and not shader_preview.current(scene, resolve()[0]):
        yield .02
    key("Z", ctrl=True)
    yield .5
    scene = bpy.context.scene     # undo replaced the scene datablock
    report["measurements"]["undo"] = {"before": before_undo, "after_stroke": before_undo + 1,
                                      "after_undo": len(scene.open_painter.strokes)}
    assert len(scene.open_painter.strokes) == before_undo, report["measurements"]["undo"]
    progress("undo removes one stroke")

    # --- Esc while idle exits ---
    window.event_simulate(type="ESC", value="PRESS")
    yield .3
    assert OP_OT_vector_brush._handler is None, "Esc while idle must exit the tool"
    progress("Esc while idle exits")
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and not shader_preview.current(scene, resolve()[0]):
        yield .05

    # --- native single-channel stroke: the existing route still paints ---
    body, layer = resolve()
    project = scene.open_painter
    project.paint_channel = "BASE_COLOR"
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.paint()
    yield .5
    assert project.paint_image is not None
    canvas_before = images.pixels(project.paint_image).copy()
    brush = scene.tool_settings.image_paint.brush
    unified = scene.tool_settings.image_paint.unified_paint_settings
    for owner in (brush, unified):
        owner.strength, owner.size = 1, 60
    window.cursor_warp(x0, cy + 140)
    mouse("MOUSEMOVE", "NOTHING", x0, cy + 140)
    yield .1
    mouse("LEFTMOUSE", "PRESS", x0, cy + 140)
    for step in range(1, 13):
        mouse("MOUSEMOVE", "NOTHING", x0 + (x1 - x0) * step / 12, cy + 140)
        yield .04
    released = perf_counter()
    mouse("LEFTMOUSE", "RELEASE", x1, cy + 140)
    yield .05
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and not shader_preview.current(scene, body):
        yield .02
    changed = not np.array_equal(images.pixels(project.paint_image), canvas_before)
    report["measurements"]["native_stroke"] = {"release_to_current_s": round(perf_counter() - released, 3),
                                               "canvas_changed": changed, "current": shader_preview.current(scene, body)}
    assert changed, "the native route must still paint"
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.paint(stop=True)
    yield .3
    progress("native stroke still paints")
    bpy.types.SpaceView3D.draw_handler_remove(handler, "WINDOW")
    gesture.Preview.publish = original_publish
    shader_preview.tick = real_tick
    # Evidence for the small-window check: the sidebar with the Paint Material section at the
    # default UI scale and at 1.5×, the sidebar at its default width.
    sidebar = next((r for r in area.regions if r.type == "UI"), None)
    for scale in (1.0, 1.5):
        bpy.context.preferences.view.ui_scale = scale
        for r in area.regions:
            r.tag_redraw()
        yield .6
        bpy.ops.screen.screenshot(filepath=str(REPORT.with_name(REPORT.stem + f"-ui-scale-{scale:.1f}.png")))
        report["measurements"][f"sidebar_px_at_{scale:.1f}"] = [sidebar.width, sidebar.height] if sidebar else None
    bpy.context.preferences.view.ui_scale = 1.0
    yield .3
    bpy.ops.screen.screenshot(filepath=str(REPORT.with_suffix(".png")))


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
