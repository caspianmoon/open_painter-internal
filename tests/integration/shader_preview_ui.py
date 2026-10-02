"""Real native mask slider changes completed viewport frames before release.

Launch with --factory-startup --enable-event-simulate --python this_file.py.
The ROI is projected inside the material, away from the sidebar and overlays.
Blender's screen capture reads the completed window framebuffer. Reported latency
includes event dispatch, viewport scheduling, PNG capture and readback; it is not
CPU submission time or display scanout. Direct POST_PIXEL attachment readback is
not used because this Metal build returns zeros for that intermediate attachment.
"""

import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
import gpu
from mathutils import Quaternion, Vector
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_preview import (RasterObservation, fixture, graph_state, image_hash,
                            images, material_manager, open_painter, output_state, render_setup, shader_preview)

REPORT = Path(os.environ.get("OP_SHADER_UI_REPORT", "/private/tmp/op-shader-preview-ui.json"))
REPORT.parent.mkdir(parents=True, exist_ok=True)
REPORT.write_text(json.dumps({"result": "RUNNING"}))
# Set this before Blender's deferred startup splash, not in the setup timer.
bpy.context.preferences.view.show_splash = False
area = region = ts = plane = handler = observation = None
phase = "SETUP"
held = False
frames, samples, calibration = [], [], []
last_frame = None
pending = None
deadline = time.monotonic() + 150
control_drawn = False
last_ready_report = 0
draw_serial = sampled_serial = 0
event_trace = []


class OP_PT_shader_acceptance(bpy.types.Panel):
    """Isolate the product's native RNA widget so event coordinates are reliable."""
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Shader Proof"
    bl_label = "Shader mask proof"
    bl_options = {"HIDE_HEADER"}

    @classmethod
    def poll(cls, context):
        return ts is not None

    def draw(self, context):
        global control_drawn
        self.layout.prop(controlled(), "black", text="Mask Black", slider=True)
        control_drawn = "OP_Mask.black" in str(self.layout.introspect())


def finish(error=None):
    global handler, observation
    if handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(handler, "WINDOW")
        handler = None
    observed = observation.report() if observation else {}
    if observation:
        observation.__exit__(None, None, None)
        observation = None
    report = dict(result="FAIL" if error else "PASS", error=error,
        blender=bpy.app.version_string, gpu_backend=gpu.platform.backend_type_get(),
        control="Isolated native Blender sidebar slider, bound to the actual coating OP_Mask.black RNA",
        calibration=calibration, samples=samples, instrumentation=observed,
        enqueued_native_events=event_trace,
        measurement="Event enqueue to first changed material in Blender's completed screen framebuffer capture. "
                    "Includes dispatch, viewport scheduling, PNG capture and readback; not scanout or submission latency.",
        limitations="Test panel isolates the same native RNA widget used in the product; "
                    "full product-panel navigation is not tested. One material ROI and one mask control; "
                    "not an arbitrary-stack frame-rate guarantee.")
    if ts is not None:
        engine = shader_preview.session_engine(bpy.context.scene, ts)
        report['execution_backend'] = engine.report.get('execution_backend') if engine else None
        expected = 'c17-host-' + gpu.platform.backend_type_get().lower()
        if os.environ.get('OP_EXPECT_NATIVE') == '1' and report['execution_backend'] != expected:
            error = f'Expected the C17 host executor {expected}: ' + str(report['execution_backend'])
            report.update(result='FAIL', error=error)
    if not error:
        report.update(material_roi=roi, baseline_jitter=baseline_jitter,
                      restoration_mean_error=restoration_error,
                      source_hashes=source_hashes, texture_set_resolution=ts.resolution,
                      fixture_source_sizes={image.name: list(image.size) for image in sources},
                      drag_native_events=event_trace[drag_event_start:])
        np.savez_compressed(REPORT.with_suffix(".frames.npz"), baseline=baseline,
                            restored=last_frame, **{f"drag_{i}": value for i, value in enumerate(frames)})
    if area is not None:
        bpy.ops.screen.screenshot(filepath=str(REPORT.with_suffix(".png")))
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    print("SHADER_PREVIEW_UI_" + ("FAIL" if error else "PASS"), str(REPORT), flush=True)
    if error:
        print(error, flush=True)
    bpy.ops.wm.quit_blender()


def later(function, delay=.08):
    def run():
        try:
            assert time.monotonic() < deadline, "UI acceptance deadline exceeded"
            # Let Blender route queued native UI events to the actual region.
            # Setup/focus operations establish their own narrow overrides.
            function()
        except Exception:
            finish(traceback.format_exc())
    bpy.app.timers.register(run, first_interval=delay)


def coat():
    return next(layer for layer in ts.layers if layer.id == ids["coat"])


def controlled():
    return coat().masks[0]


def event(kind, value, point):
    target = globals().get("sidebar", region)
    event_trace.append(dict(type=kind, value=value, xy=[int(point[0]), int(point[1])],
                            phase=phase, enqueued_seconds=time.perf_counter()))
    with bpy.context.temp_override(window=window, area=area, region=target):
        window.event_simulate(type=kind, value=value, x=int(point[0]), y=int(point[1]))


def setup():
    global area, region, window, ts, plane, ids, source_hashes, sources, handler
    assert not bpy.app.background, "This test requires a real Blender window"
    open_painter.register()
    bpy.utils.register_class(OP_PT_shader_acceptance)
    bpy.context.preferences.view.show_splash = False
    window = bpy.context.window
    assert hasattr(window, "event_simulate"), "Launch Blender with --enable-event-simulate"
    area = max((item for item in window.screen.areas if item.type == "VIEW_3D"),
               key=lambda item: item.width * item.height)
    region = next(item for item in area.regions if item.type == "WINDOW")
    with bpy.context.temp_override(window=window, area=area, region=region):
        ts, plane, ids, sources = fixture(bpy.context.scene, resolution=2048)
        render_setup(bpy.context.scene)
        bpy.context.scene.render.engine = "BLENDER_EEVEE"
        space = area.spaces.active
        space.shading.type = "RENDERED"
        space.overlay.show_overlays = False
        space.show_gizmo = False
        space.show_region_ui = True
        space.show_region_toolbar = False
        space.region_3d.view_perspective = "ORTHO"
        space.region_3d.view_rotation = Quaternion((1, 0, 0, 0))
        space.region_3d.view_location = (0, 0, 0)
        space.region_3d.view_distance = 3
    source_hashes = {image.name: image_hash(image) for image in sources}
    event("ESC", "PRESS", (region.x + 20, region.y + 20))
    event("ESC", "RELEASE", (region.x + 20, region.y + 20))
    handler = bpy.types.SpaceView3D.draw_handler_add(drawn, (), "WINDOW", "POST_PIXEL")
    later(capture, .2)
    later(wait_ready, .3)


def wait_ready():
    global last_ready_report
    material_manager.repair(ts)
    diagnostic = shader_preview.diagnostics(bpy.context.scene, ts)
    assert not diagnostic.get("error"), diagnostic
    area.tag_redraw()
    if time.monotonic() - last_ready_report > 5:
        last_ready_report = time.monotonic()
        info = dict(result="RUNNING", phase="INITIAL_VIEWPORT", diagnostic=diagnostic,
                    framebuffer=None if last_frame is None else dict(
                        minimum=float(last_frame.min()), maximum=float(last_frame.max()),
                        std=float(last_frame.std()), shape=list(last_frame.shape)))
        REPORT.write_text(json.dumps(info, indent=2) + "\n")
        bpy.ops.screen.screenshot(filepath=str(REPORT.with_suffix(".png")))
    if (not shader_preview.current(bpy.context.scene, ts) or diagnostic.get("pending_resources")
            or last_frame is None or np.std(last_frame) < .01):
        later(wait_ready, .1)
        return
    global candidates, attempt, sidebar
    sidebar = next(item for item in area.regions if item.type == "UI" and item.width > 20)
    scale = bpy.context.preferences.system.ui_scale
    print("NATIVE_UI_GEOMETRY", dict(window=(window.width, window.height),
          sidebar=(sidebar.x, sidebar.y, sidebar.width, sidebar.height),
          viewport=(region.x, region.y, region.width, region.height), ui_scale=scale,
          pixel_size=bpy.context.preferences.system.pixel_size), flush=True)
    candidates = [(sidebar.x + int(sidebar.width * fraction),
                   sidebar.y + sidebar.height - int(offset * scale))
                  for offset in (20, 30, 40, 50, 60) for fraction in (.55, .72)]
    attempt = 0
    prepare_control()


def prepare_control():
    with bpy.context.temp_override(window=window, area=area, region=sidebar):
        sidebar.active_panel_category = "Shader Proof"
    area.tag_redraw()
    later(calibrate, .2)


def calibrate():
    global point, calibration_before
    assert control_drawn, "Native OP_Mask.black widget was not drawn"
    assert attempt < len(candidates), "Could not locate the actual native mask control"
    point = candidates[attempt]
    event("ESC", "PRESS", point)
    event("ESC", "RELEASE", point)
    event("MOUSEMOVE", "NOTHING", point)
    later(calibration_press, .15)


def calibration_press():
    event("LEFTMOUSE", "PRESS", point)
    later(calibration_move, .1)


def calibration_move():
    global calibration_before
    calibration_before = float(controlled().black)
    event("MOUSEMOVE", "NOTHING", (point[0] + 20, point[1]))
    later(calibration_move_again, .15)


def calibration_move_again():
    event("MOUSEMOVE", "NOTHING", (point[0] + 60, point[1]))
    later(calibration_check, .15)


def render_edit():
    """The engine renders an RNA edit. Through pixels (Vulkan) a tick publishes within a time
    budget, so the edit may take a few ticks to land in every channel; framebuffer backends
    must finish in one."""
    from open_painter.evaluation.engine import runtime
    shader_preview.tick(bpy.context.scene, ts)
    if runtime.device().pixel_publish:
        for _ in range(100):
            if shader_preview.current(bpy.context.scene, ts):
                break
            shader_preview.tick(bpy.context.scene, ts)
    assert shader_preview.current(bpy.context.scene, ts)


def calibration_check():
    global attempt, drag_step, phase, baseline_history, baseline_start
    value = float(controlled().black)
    event("LEFTMOUSE", "RELEASE", (point[0] + 60, point[1]))
    calibration.append(dict(point=point, before=calibration_before, after=value))
    print("NATIVE_MASK_CALIBRATION", calibration[-1], flush=True)
    if abs(value - calibration_before) < .005:
        attempt += 1
        prepare_control()
        return
    # Calibrate Blender's actual numeric drag sensitivity, not an assumed scale.
    drag_step = max(4, min(30, int(.075 * 40 / abs(value - calibration_before))))
    controlled().black = .3
    render_edit()
    phase = "BASELINE"
    baseline_history = []
    baseline_start = time.monotonic()
    later(wait_baseline, .1)


def drawn():
    global draw_serial
    if bpy.context.area == area:
        draw_serial += 1


def capture():
    global last_frame, roi, pending, phase, sampled_serial
    if phase in {"DONE", "FAILED"}:
        return
    if sampled_serial == draw_serial:
        area.tag_redraw()
        later(capture, .08)
        return
    if (phase == "DRAG" and pending is not None
            and abs(float(controlled().black) - pending["before_value"]) >= .005
            and not shader_preview.current(bpy.context.scene, ts)):
        later(capture, .01)   # the engine renders the edit on its next tick: capture that frame
        return
    try:
        sampled_serial = draw_serial
        corners = [location_3d_to_region_2d(region, area.spaces.active.region_3d, Vector(value))
                   for value in ((-.82, -.45, 0), (-.25, .45, 0))]
        assert all(value is not None for value in corners)
        x0, x1 = sorted(int(value.x) for value in corners)
        y0, y1 = sorted(int(value.y) for value in corners)
        assert x0 > 0 and y0 > 0 and x1 < region.width and y1 < region.height
        assert x1 - x0 > 20 and y1 - y0 > 20, "Material ROI is too small"
        roi = [x0, y0, x1 - x0, y1 - y0]
        capture_path = REPORT.with_suffix(".capture.png")
        capture_started = time.perf_counter()
        bpy.ops.screen.screenshot(filepath=str(capture_path))
        screenshot_saved = time.perf_counter()
        captured = bpy.data.images.load(str(capture_path), check_existing=False)
        captured.colorspace_settings.name = "Non-Color"
        try:
            # Blender image rows and window event coordinates both start at bottom left.
            pixels = images.pixels(captured)
            current_pixels = pixels[region.y + y0:region.y + y1,
                                    region.x + x0:region.x + x1, :3].copy()
        finally:
            bpy.data.images.remove(captured)
        completed = time.perf_counter()
        last_frame = current_pixels.copy()
        if phase == "BASELINE":
            baseline_history.append(last_frame)
            del baseline_history[:-12]
        if phase != "DRAG" or pending is None:
            later(capture, .08)
            return
        value = float(controlled().black)
        if abs(value - pending["before_value"]) < .005:
            later(capture, .08)
            return
        diagnostic = shader_preview.diagnostics(bpy.context.scene, ts)
        if not shader_preview.current(bpy.context.scene, ts):
            later(capture, .02)   # the engine renders the edit on its next tick: that frame counts
            return
        assert diagnostic["serial"] == ts.edit_serial, diagnostic
        assert diagnostic["graph_builds"] == graph_builds, diagnostic
        assert not diagnostic.get("pending_resources") and not diagnostic.get("texture_bake_triggered"), diagnostic
        difference = float(np.mean(abs(last_frame - pending["before_frame"])))
        if difference <= visible_threshold:
            later(capture, .08)
            return
        assert held, "A material frame first changed after mouse release"
        samples.append(dict(event_index=pending["index"], observed_value=value,
            before_value=pending["before_value"],
            first_completed_frame_ms=(completed - pending["enqueued"]) * 1000,
            screenshot_save_ms=(screenshot_saved - capture_started) * 1000,
            screenshot_load_and_read_ms=(completed - screenshot_saved) * 1000,
            capture_started_after_event_ms=(capture_started - pending["enqueued"]) * 1000,
            material_mean_change=difference, before_release=held,
            diagnostic=diagnostic))
        frames.append(last_frame.copy())
        pending = None
        later(capture, .08)
    except Exception:
        # Operators cannot reliably quit inside a draw handler.
        error = traceback.format_exc()
        phase = "FAILED"
        later(lambda: finish(error), .01)


def wait_baseline():
    global baseline, baseline_jitter, visible_threshold, graph_builds, topology, outputs, observation
    area.tag_redraw()
    if len(baseline_history) < 8 or time.monotonic() - baseline_start < 1.5:
        later(wait_baseline, .08)
        return
    baseline = last_frame.copy()
    baseline_jitter = max(float(np.mean(abs(value - baseline))) for value in baseline_history[-5:])
    assert baseline_jitter < .003, f"Viewport did not settle: {baseline_jitter}"
    visible_threshold = max(.001, baseline_jitter * 6)
    outputs = output_state(ts)
    graph_builds = shader_preview.diagnostics(bpy.context.scene, ts)["graph_builds"]
    topology = graph_state(ts.material)[0]
    observation = RasterObservation().__enter__()
    event("MOUSEMOVE", "NOTHING", point)
    later(press_drag, .15)


def press_drag():
    global held, drag_event_start
    held = True
    drag_event_start = len(event_trace)
    event("LEFTMOUSE", "PRESS", point)
    later(start_drag, .1)


def start_drag():
    # Blender consumes the first threshold-crossing movement to unlock numeric
    # dragging and resets dragstartx there. Subsequent events change the value.
    event("MOUSEMOVE", "NOTHING", (point[0] + 20, point[1]))
    later(begin_drag_samples, .15)


def begin_drag_samples():
    global phase, held, drag_index
    phase, held, drag_index = "DRAG", True, 0
    move_drag()


def move_drag():
    global pending
    # One held drag goes right, right, left, left, right, left. Stay away
    # from clamping so each direction must change the actual native RNA.
    offsets = (1, 3, 2, -1, 1, 0)
    pending = dict(index=drag_index, enqueued=time.perf_counter(), before_value=float(controlled().black),
                   before_frame=last_frame.copy())
    event("MOUSEMOVE", "NOTHING", (point[0] + 20 + offsets[drag_index] * drag_step, point[1]))
    later(await_frame, .04)


def await_frame():
    global drag_index, held, phase, restoration_start
    area.tag_redraw()
    if pending is not None:
        assert time.perf_counter() - pending["enqueued"] < 4, (
            "No completed material change during held native drag", pending["index"], controlled().black)
        later(await_frame, .04)
        return
    drag_index += 1
    if drag_index < 6:
        later(move_drag, .06)
        return
    assert len(samples) == 6 and len({round(item["observed_value"], 3) for item in samples}) >= 5
    assert [int(np.sign(item["observed_value"] - item["before_value"]))
            for item in samples] == [1, 1, -1, -1, 1, -1]
    assert all(.02 < item["observed_value"] < .98 for item in samples)
    mouse_buttons = [item for item in event_trace[drag_event_start:] if item["type"] == "LEFTMOUSE"]
    assert [item["value"] for item in mouse_buttons] == ["PRESS"], mouse_buttons
    assert graph_state(ts.material)[0] == topology
    observation.assert_no_raster()
    event("LEFTMOUSE", "RELEASE", (point[0] + 20, point[1]))
    held = False
    phase = "RESTORE"
    # Restoration is an RNA edit; the six measured updates above used only mouse events.
    controlled().black = .3
    render_edit()
    restoration_start = time.monotonic()
    later(await_restoration, .1)


def await_restoration():
    global restoration_error, phase
    area.tag_redraw()
    restoration_error = float(np.mean(abs(last_frame - baseline)))
    tolerance = max(.0015, baseline_jitter * 3)
    if restoration_error > tolerance or time.monotonic() - restoration_start < .5:
        assert time.monotonic() - restoration_start < 6, (restoration_error, tolerance)
        later(await_restoration, .08)
        return
    observation.assert_no_raster()
    assert output_state(ts) == outputs and ts.dirty, "Drag altered authoritative CPU outputs"
    assert {image.name: image_hash(image) for image in sources} == source_hashes
    phase = "DONE"
    finish()


later(setup, 1.5)
