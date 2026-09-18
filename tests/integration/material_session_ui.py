"""A long Paint Material session in a real window (quality remedy M1, plan §5.9): hundreds of
strokes with undo/redo, saves and target changes on the 2K production prop.

Reports peak and settled memory, the worst pauses (event handling, settle, redraw gaps),
history growth (stroke records, leaves, preview images, undo behaviour) and whether the
session stays current; asserts that nothing leaks and every stroke lands.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/material_session_ui.py
OP_SESSION_STROKES (default 500), OP_SESSION_RESOLUTION (2048), OP_SESSION_LAYERS (25).
"""
import json
import os
import resource
import sys
import tempfile
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
from open_painter.core import layer_stack as stack
from open_painter.evaluation import shader_preview
from open_painter.evaluation.shader_resources import RESOURCE
from open_painter.painting import gesture
from open_painter.ui.vector_brush import OP_OT_vector_brush

STROKES = int(os.environ.get("OP_SESSION_STROKES", "500"))
RESOLUTION = int(os.environ.get("OP_SESSION_RESOLUTION", "2048"))
LAYERS = int(os.environ.get("OP_SESSION_LAYERS", "25"))
OUT = ROOT / ".blender-test/quality"
OUT.mkdir(parents=True, exist_ok=True)
REPORT = Path(os.environ.get("OP_SESSION_REPORT", OUT / f"material-session-{RESOLUTION}.json"))
report = {"blender": bpy.app.version_string, "strokes_requested": STROKES, "resolution": RESOLUTION, "layers": LAYERS,
          "steps": [], "errors": [], "measurements": {}}
env = {}


def rss_mb():
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(usage / (1024 * 1024), 1)   # macOS reports bytes


def finish(status):
    REPORT.write_text(json.dumps(dict(status=status, **report), indent=2))
    print("MATERIAL_SESSION_" + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()


def mouse(kind, value, x, y):
    env["window"].event_simulate(type=kind, value=value, x=int(x), y=int(y))


def key(kind, ctrl=False, shift=False):
    env["window"].event_simulate(type=kind, value="PRESS", ctrl=ctrl, shift=shift)
    env["window"].event_simulate(type=kind, value="RELEASE", ctrl=ctrl, shift=shift)


def progress(message):
    report["steps"].append(message)
    print("MATERIAL_SESSION step:", message, flush=True)


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
    layer_names = ["Mud paint", "Marks"]
    started = time.monotonic()
    while time.monotonic() - started < 600 and not shader_preview.current(scene, body):
        yield .2
    assert shader_preview.current(scene, body), shader_preview.diagnostics(scene, body)
    report["measurements"]["first_compile_s"] = round(time.monotonic() - started, 1)
    report["measurements"]["rss_mb_after_compile"] = rss_mb()
    progress("fixture compiled")

    def resolve(name):
        scene = bpy.context.scene
        ts = next(t for t in scene.open_painter.texture_sets if t.name == "Body")
        return scene, ts, next(l for l in ts.layers if l.name == name)

    settings = bpy.context.scene.open_painter.vector_brush
    settings.radius_mode, settings.radius, settings.spacing, settings.hardness = "WORLD", .06, .25, .8
    settings.universal, settings.blend, settings.depth_limit = False, "PAINT", .5
    settings.channels = {"BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT"}
    cx, cy = region.x + region.width // 2, region.y + region.height // 2
    window.cursor_warp(cx, cy)
    mouse("LEFTMOUSE", "PRESS", cx, cy); mouse("LEFTMOUSE", "RELEASE", cx, cy)
    yield .3
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.vector_brush("INVOKE_DEFAULT")
    yield .3
    assert OP_OT_vector_brush._handler is not None

    draws = []
    handler = bpy.types.SpaceView3D.draw_handler_add(lambda: draws.append(perf_counter()), (), "WINDOW", "POST_PIXEL")
    rng = np.random.default_rng(7)
    span_x = int(region.width * .16)
    span_y = int(region.height * .16)
    settles, event_pauses, undo_results, saves, images_over_time, rss_over_time = [], [], [], [], [], []
    committed = 0
    lost = []
    active_name = layer_names[0]
    with tempfile.TemporaryDirectory(prefix="op-session-") as tmp:
        for index in range(STROKES):
            scene, body, layer = resolve(active_name)
            before = len(scene.open_painter.strokes)
            # A random short drag inside the central region (the body fills the view centre).
            x0 = cx + int(rng.uniform(-span_x, span_x) * .6)
            y0 = cy + int(rng.uniform(-span_y, span_y) * .6)
            dx, dy = rng.uniform(-span_x, span_x) * .5, rng.uniform(-span_y, span_y) * .5
            settings.base_color = (float(rng.uniform(.1, .9)), float(rng.uniform(.1, .9)), float(rng.uniform(.1, .9)), 1)
            settings.roughness, settings.metallic, settings.height = float(rng.uniform(0, 1)), float(rng.choice([0.0, 1.0])), float(rng.uniform(0, 1))
            settings.blend = "ERASE" if index % 17 == 16 else "PAINT"
            window.cursor_warp(x0, y0)
            mouse("MOUSEMOVE", "NOTHING", x0, y0)
            yield .01
            events = int(rng.integers(3, 12))
            mouse("LEFTMOUSE", "PRESS", x0, y0)
            for step in range(1, events + 1):
                sent = perf_counter()
                mouse("MOUSEMOVE", "NOTHING", x0 + dx * step / events, y0 + dy * step / events)
                yield .016
                event_pauses.append(perf_counter() - sent)
            released = perf_counter()
            mouse("LEFTMOUSE", "RELEASE", x0 + dx, y0 + dy)
            yield .01
            scene, body, layer = resolve(active_name)
            if len(scene.open_painter.strokes) != before + 1:
                lost.append(index)
            else:
                committed += 1
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not shader_preview.current(scene, body):
                yield .01
            settles.append(perf_counter() - released)
            if not shader_preview.current(scene, body):
                report["errors"].append(f"stroke {index}: not current after 60 s: {shader_preview.diagnostics(scene, body)}")
                break
            # Session events: undo/redo, a save, a target change.
            if index % 25 == 24:
                count = len(scene.open_painter.strokes)
                key("Z", ctrl=True)
                yield .5
                scene = bpy.context.scene
                after_undo = len(scene.open_painter.strokes)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline and not shader_preview.current(scene, resolve(active_name)[1]):
                    yield .02
                key("Z", ctrl=True, shift=True)
                yield .5
                scene = bpy.context.scene
                after_redo = len(scene.open_painter.strokes)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline and not shader_preview.current(scene, resolve(active_name)[1]):
                    yield .02
                undo_results.append({"at": index, "before": count, "after_undo": after_undo, "after_redo": after_redo,
                                     "tool_running": OP_OT_vector_brush._handler is not None})
                if OP_OT_vector_brush._handler is None:
                    with bpy.context.temp_override(window=window, area=area, region=region):
                        bpy.ops.open_painter.vector_brush("INVOKE_DEFAULT")
                    yield .3
            if index % 100 == 99:
                path = Path(tmp) / f"session-{index}.blend"
                started = perf_counter()
                bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
                saves.append({"at": index, "seconds": round(perf_counter() - started, 2), "bytes": path.stat().st_size})
                yield .3
            if index % 40 == 39:
                # Change the target layer: the tool follows the active layer at the next press.
                active_name = layer_names[(layer_names.index(active_name) + 1) % len(layer_names)]
                scene, body, layer = resolve(active_name)
                body.active_layer = stack.index_of(body, layer.id)
                if OP_OT_vector_brush._handler is not None:
                    window.event_simulate(type="ESC", value="PRESS")
                    yield .2
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.open_painter.vector_brush("INVOKE_DEFAULT")
                yield .3
            if index % 50 == 49:
                images_over_time.append({"at": index, "images": len(bpy.data.images),
                                         "leaves": sum(1 for i in bpy.data.images if i.get(RESOURCE)),
                                         "previews": sum(1 for i in bpy.data.images if i.get("op_gesture_preview")),
                                         "strokes": len(scene.open_painter.strokes)})
                rss_over_time.append({"at": index, "rss_mb": rss_mb()})
                progress(f"{index + 1} strokes · settle p95 {np.percentile(settles, 95):.3f}s · rss {rss_mb()} MB")
    gesture.sweep()
    gesture._reap()
    yield 2.5
    gesture._reap()
    draws_np = np.asarray(draws)
    gaps = np.diff(draws_np) if len(draws_np) > 1 else np.zeros(1)
    settles_np = np.asarray(settles)
    leaves = [i for i in bpy.data.images if i.get(RESOURCE)]
    previews = [i for i in bpy.data.images if i.get("op_gesture_preview")]
    report["measurements"]["datablocks"] = {
        "node_groups": len(bpy.data.node_groups), "materials": len(bpy.data.materials), "images": len(bpy.data.images),
        "leaves": {"total": len(leaves), "with_users": sum(1 for i in leaves if i.users), "ready": sum(1 for i in leaves if i.get("op_shader_resource_ready")),
                   "has_data": sum(1 for i in leaves if i.has_data), "users_max": max((i.users for i in leaves), default=0)},
        "previews": {"total": len(previews), "with_users": sum(1 for i in previews if i.users), "users": [i.users for i in previews]},
        "orphan_node_groups": sum(1 for g in bpy.data.node_groups if not g.users),
    }
    report["measurements"].update({
        "strokes_committed": committed, "strokes_lost": lost,
        "settle_s": {"p50": round(float(np.percentile(settles_np, 50)), 3), "p95": round(float(np.percentile(settles_np, 95)), 3),
                     "max": round(float(settles_np.max()), 3), "first": round(float(settles_np[0]), 3), "last": round(float(settles_np[-1]), 3)},
        "event_pause_s": {"p95": round(float(np.percentile(event_pauses, 95)), 3), "max": round(float(np.max(event_pauses)), 3)},
        "redraw_gap_s": {"p95": round(float(np.percentile(gaps, 95)), 3), "max": round(float(gaps.max()), 3)},
        "undo": undo_results, "saves": saves, "history": images_over_time, "rss": rss_over_time,
        "rss_mb_peak": rss_mb(),
        "preview_images_left": [i.name for i in bpy.data.images if i.get("op_gesture_preview")],
        "leaves_left": sum(1 for i in bpy.data.images if i.get(RESOURCE)),
        "strokes_recorded": len(bpy.context.scene.open_painter.strokes),
    })
    bpy.types.SpaceView3D.draw_handler_remove(handler, "WINDOW")
    assert not lost, lost
    assert all(u["after_undo"] == u["before"] - 1 and u["after_redo"] == u["before"] for u in undo_results), undo_results
    assert not report["measurements"]["preview_images_left"], report["measurements"]["preview_images_left"]
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
