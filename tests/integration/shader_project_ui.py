"""Held native mask drags on the user's saved asset, at its saved resolution.

Launch Blender with --enable-event-simulate --python this_file.py. This test opens
a temporary copy beside the supplied .blend so relative asset paths stay valid.
It creates no geometry, material recipe, lighting fixture, or resized source.
"""

import hashlib
import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import traceback

import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
import gpu
from mathutils import Vector
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_preview import (RasterObservation, graph_state, image_hash, images,
                            material_manager, open_painter, output_state, shader_preview)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(os.environ.get("OP_SHADER_PROJECT", ROOT / "open painter test.blend"))
REPORT = Path(os.environ.get("OP_SHADER_PROJECT_REPORT", ROOT / ".blender-test/project-native/ui.json"))
REPORT.parent.mkdir(parents=True, exist_ok=True)
CASES = [
    ("Cavity dirt strength", "c688bdd442fd49ea9ecfa7025318527d", "strength", .15, .85),
    ("Abrasion coverage", "12ab70dd45b546de8dd038d9779b472e", "black", .025, .60),
]
DIAGNOSTIC = os.environ.get("OP_SHADER_PROJECT_DIAGNOSTIC") == "abrasion"
if DIAGNOSTIC:
    CASES = [next(item for item in CASES if item[2] == "black")]
elif os.environ.get("OP_SHADER_PROJECT_CONTROL") == "cavity":
    CASES = [next(item for item in CASES if item[2] == "strength")]
PRODUCT_UI = os.environ.get("OP_SHADER_PROJECT_PRODUCT_UI") == "1"
DRAG_STEPS = 2 if DIAGNOSTIC else 8
phase = "LOAD"
window = area = region = sidebar = ts = obj = handler = observation = None
temporary_project = None
case_index = -1
results, samples, calibration, input_events = [], [], [], []
saved_frames = {}
last_frame = None
draw_serial = sampled_serial = 0
pending = None
held = False
deadline = time.monotonic() + 600
control_drawn = False
last_progress = 0
REPORT.write_text(json.dumps({"result": "RUNNING", "source": str(SOURCE)}))
bpy.context.preferences.view.show_splash = False


class PngImage(ctypes.Structure):
    # Public libpng 1.6 simplified-read ABI, png.h PNG_IMAGE_VERSION=1.
    _fields_ = [("opaque", ctypes.c_void_p)] + [
        (name, ctypes.c_uint32) for name in
        ("version", "width", "height", "format", "flags", "colormap_entries", "warning_or_error")
    ] + [("message", ctypes.c_char * 64)]


png_library = ctypes.CDLL(os.environ.get("OP_TEST_LIBPNG") or
                         ctypes.util.find_library("png16") or "/opt/homebrew/lib/libpng16.dylib")
png_library.png_image_begin_read_from_file.argtypes = [ctypes.POINTER(PngImage), ctypes.c_char_p]
png_library.png_image_begin_read_from_file.restype = ctypes.c_int
png_library.png_image_finish_read.argtypes = [ctypes.POINTER(PngImage), ctypes.c_void_p,
                                            ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p]
png_library.png_image_finish_read.restype = ctypes.c_int
png_library.png_image_free.argtypes = [ctypes.POINTER(PngImage)]


def capture_pixels(path):
    """Decode the screenshot without creating/deleting Blender image datablocks."""
    header = PngImage()
    header.version = 1
    try:
        assert png_library.png_image_begin_read_from_file(ctypes.byref(header), os.fsencode(path)), header.message
        header.format = 2  # PNG_FORMAT_RGB: 8-bit sRGB, matching Blender screenshots.
        pixels = np.empty((header.height, header.width, 3), dtype=np.uint8)
        assert png_library.png_image_finish_read(ctypes.byref(header), None,
            pixels.ctypes.data, -int(header.width) * 3, None), header.message
        return pixels.astype(np.float32) / 255.0
    finally:
        png_library.png_image_free(ctypes.byref(header))


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scene_geometry():
    result = {}
    for item in bpy.context.scene.objects:
        entry = {"type": item.type, "matrix": [list(row) for row in item.matrix_world]}
        if item.type == "MESH":
            mesh = item.data
            digest = hashlib.sha256()
            for collection, property_name, width, dtype in (
                    (mesh.vertices, "co", 3, np.float32),
                    (mesh.loops, "vertex_index", 1, np.int32),
                    (mesh.polygons, "loop_total", 1, np.int32)):
                values = np.empty(len(collection) * width, dtype=dtype)
                collection.foreach_get(property_name, values)
                digest.update(values.tobytes())
            uv = {}
            for layer in mesh.uv_layers:
                values = np.empty(len(layer.data) * 2, dtype=np.float32)
                layer.data.foreach_get("uv", values)
                uv[layer.name] = hashlib.sha256(values.tobytes()).hexdigest()
            entry.update(vertices=len(mesh.vertices), faces=len(mesh.polygons),
                         topology_and_positions=digest.hexdigest(), uv=uv,
                         materials=[material.name if material else None for material in mesh.materials])
        elif item.type == "LIGHT":
            entry.update(light_type=item.data.type, energy=item.data.energy, color=list(item.data.color))
        result[item.name] = entry
    return result


def authored_sources():
    found = {}
    for layer in ts.layers:
        for prop in layer.channels:
            for image in (prop.image, prop.detail_image):
                if image:
                    found[image.name] = image
        for mask in layer.masks:
            if mask.image:
                found[mask.image.name] = mask.image
    for entry in ts.baked_maps:
        if entry.image:
            found[entry.image.name] = entry.image
    return found


def controlled():
    identifier = CASES[case_index][1]
    return next(mask for layer in ts.layers for mask in layer.masks if mask.id == identifier)


def value():
    return float(getattr(controlled(), CASES[case_index][2]))


def coverage_frame(frame):
    """Measure coverage on 8x8 block means; keep/export the full raw capture."""
    height, width = frame.shape[:2]
    return frame[:height//8*8, :width//8*8].reshape(height//8, 8, width//8, 8, 3).mean((1, 3))


def coverage_difference(a, b):
    return float(np.mean(abs(coverage_frame(a)-coverage_frame(b))))


def set_value(number):
    setattr(controlled(), CASES[case_index][2], number)


class OP_PT_project_shader_proof(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Shader Proof"
    bl_label = "Saved project mask proof"
    bl_options = {"HIDE_HEADER"}

    @classmethod
    def poll(cls, context):
        return ts is not None and 0 <= case_index < len(CASES) and phase not in {"DONE", "FAILED"}

    def draw(self, context):
        global control_drawn
        prop = CASES[case_index][2]
        self.layout.prop(controlled(), prop, text=CASES[case_index][0], slider=True)
        control_drawn = f"OP_Mask.{prop}" in str(self.layout.introspect())


def later(function, delay=.08):
    def run():
        if phase in {"DONE", "FAILED"}:
            return
        try:
            assert time.monotonic() < deadline, "Saved project acceptance deadline exceeded"
            function()
        except Exception:
            finish(traceback.format_exc())
    bpy.app.timers.register(run, first_interval=delay)


def event(kind, state, point):
    input_events.append(dict(type=kind, value=state, xy=[int(point[0]), int(point[1])],
                             phase=phase, enqueued_seconds=time.perf_counter()))
    with bpy.context.temp_override(window=window, area=area, region=sidebar or region):
        window.event_simulate(type=kind, value=state, x=int(point[0]), y=int(point[1]))


def finish(error=None):
    global phase, handler, observation
    phase = "FAILED" if error else "DONE"
    observed = observation.report() if observation else None
    if observation:
        observation.__exit__(None, None, None)
        observation = None
    if handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(handler, "WINDOW")
        handler = None
    unchanged = file_hash(SOURCE) == source_hash
    if not unchanged:
        error = (error or "") + "\nOriginal .blend hash changed"
    report = dict(result="FAIL" if error else "PASS", error=error,
                  source=str(SOURCE), original_sha256=source_hash, original_unchanged=unchanged,
                  blender=bpy.app.version_string, gpu_backend=gpu.platform.backend_type_get(),
                  texture_set=dict(id=ts.id, name=ts.name, resolution=ts.resolution,
                                   layers=len(ts.layers)) if ts else None,
                  cases=results, active_case_samples=samples if error else None,
                  active_observation=observed, native_input_events=input_events,
                  active_roi=roi if "roi" in globals() else None,
                  capture_geometry=capture_geometry if "capture_geometry" in globals() else None,
                  geometry=geometry_before if "geometry_before" in globals() else None,
                  viewport=viewport_settings if "viewport_settings" in globals() else None,
                  source_images=source_manifest if "source_manifest" in globals() else None,
                  initial_ready_seconds=native_ready_seconds if "native_ready_seconds" in globals() else None,
                  measurement="Enqueue to changed completed window screenshot; includes viewport scheduling, "
                              "GPU synchronization, PNG encoding, load and readback. Not scanout latency.",
                  capture_decode="libpng simplified RGB8 read into NumPy; no Blender image datablock mutations",
                  coverage_measurement="8x8 block means distinguish persistent coverage changes from "
                                       "fine-detail temporal antialiasing. Saved captures are unchanged full resolution.",
                  scope=f"The supplied asset has one existing texture set and six layers. {len(results)} "
                        "actual mask controls completed the selected test through native RNA widgets. "
                        "Each case records its UI route. Geometry, UVs, source pixels, lighting and "
                        "saved texture resolution are preserved.")
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    if last_frame is not None:
        saved_frames["final_frame"] = last_frame
    if saved_frames:
        np.savez_compressed(REPORT.with_suffix(".frames.npz"), **saved_frames)
    if area:
        bpy.ops.screen.screenshot(filepath=str(REPORT.with_suffix(".png")))
    if temporary_project:
        temporary_project.unlink(missing_ok=True)
    print("SHADER_PROJECT_UI_" + report["result"], str(REPORT), flush=True)
    if error:
        print(error, flush=True)
    bpy.ops.wm.quit_blender()


def load_project():
    global temporary_project, source_hash, open_started
    assert not bpy.app.background and SOURCE.is_file()
    source_hash = file_hash(SOURCE)
    open_painter.register()
    bpy.utils.register_class(OP_PT_project_shader_proof)
    descriptor, path = tempfile.mkstemp(prefix=".shader-project-", suffix=".blend", dir=SOURCE.parent)
    os.close(descriptor)
    temporary_project = Path(path)
    shutil.copyfile(SOURCE, temporary_project)
    open_started = time.monotonic()
    bpy.ops.wm.open_mainfile(filepath=str(temporary_project), use_scripts=False)
    later(configure_view, .6)


def configure_view():
    global window, area, region, sidebar, ts, obj, handler, geometry_before
    global saved_resolution, source_manifest, outputs_before, viewport_settings
    window = bpy.context.window or bpy.context.window_manager.windows[0]
    area = max((item for item in window.screen.areas if item.type == "VIEW_3D"),
               key=lambda item: item.width * item.height)
    region = next(item for item in area.regions if item.type == "WINDOW")
    ts = next(item for item in bpy.context.scene.open_painter.texture_sets
              if item.id == "2dd556f037ba4bdf9f2161e4311b5916")
    obj = bpy.data.objects["defaultMaterial.003"]
    assert obj.active_material == ts.material, "Saved assigned material differs from the native target"
    saved_resolution = ts.resolution
    assert saved_resolution == "2048", f"Saved project changed: {saved_resolution}"
    geometry_before = scene_geometry()
    source_manifest = {name: dict(size=list(image.size), sha256=image_hash(image))
                       for name, image in authored_sources().items()}
    outputs_before = output_state(ts)
    assert ts.preview_backend == "LIVE", "Use the saved native authoring backend"
    space = area.spaces.active
    viewport_settings = dict(engine=bpy.context.scene.render.engine,
        world=bpy.context.scene.world.name if bpy.context.scene.world else None,
        saved_shading=space.shading.type, studio_light=space.shading.studio_light,
        studio_light_rotation=space.shading.studiolight_rotate_z,
        saved_view_rotation=list(space.region_3d.view_rotation))
    # Keep saved material-preview lighting. Only a non-material editor mode
    # would need to be switched to material preview to exercise the shader.
    if space.shading.type not in {"MATERIAL", "RENDERED"}:
        space.shading.type = "MATERIAL"
    viewport_settings["tested_shading"] = space.shading.type
    space.overlay.show_overlays = False
    space.show_gizmo = False
    space.show_region_ui = True
    space.show_region_toolbar = False
    for item in bpy.context.view_layer.objects:
        item.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    with bpy.context.temp_override(window=window, area=area, region=region):
        if obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.view3d.view_selected(use_all_regions=False)
    handler = bpy.types.SpaceView3D.draw_handler_add(drawn, (), "WINDOW", "POST_PIXEL")
    later(wait_native, .3)


def wait_native():
    global sidebar, last_progress, native_ready_seconds
    material_manager.repair(ts)
    diagnostic = shader_preview.diagnostics(bpy.context.scene, ts)
    assert not diagnostic.get("error"), diagnostic
    if time.monotonic() - last_progress > 5:
        last_progress = time.monotonic()
        print("SAVED_PROJECT_PREPARING", diagnostic, flush=True)
    if not shader_preview.current(bpy.context.scene, ts):
        later(wait_native, .2)
        return
    sidebar = next(item for item in area.regions if item.type == "UI" and item.width > 20)
    native_ready_seconds = time.monotonic() - open_started
    begin_case()
    later(capture, .2)


def begin_case():
    global case_index, phase, samples, calibration, original_value, center, target_span
    global history, settle_started, attempt, candidates, control_drawn, ui_route
    case_index += 1
    if case_index == len(CASES):
        assert scene_geometry() == geometry_before, "Project mesh, UVs, assignment or light changed"
        assert ts.resolution == saved_resolution and output_state(ts) == outputs_before
        assert {name: dict(size=list(image.size), sha256=image_hash(image))
                for name, image in authored_sources().items()} == source_manifest
        finish()
        return
    samples, calibration = [], []
    original_value = value()
    low, high = CASES[case_index][3:]
    center, target_span = (low + high) / 2, (high - low) / 2
    phase, history, settle_started = "ORIGINAL", [], time.monotonic()
    control_drawn = False
    ui_route = "isolated native RNA sidebar"
    scale = bpy.context.preferences.system.ui_scale
    candidates = [(sidebar.x + int(sidebar.width * fraction),
                   sidebar.y + sidebar.height - int(offset * scale))
                  for offset in (20, 30, 40, 50) for fraction in (.30, .48)]
    attempt = 0
    area.tag_redraw()
    later(activate_control, .2)


def activate_control():
    # A newly eligible Panel category is registered by the next sidebar draw.
    try:
        with bpy.context.temp_override(window=window, area=area, region=sidebar):
            if PRODUCT_UI:
                layer = next(item for item in ts.layers if any(m.id == controlled().id for m in item.masks))
                layer.active_mask = next(i for i,m in enumerate(layer.masks) if m.id == controlled().id)
                bpy.ops.open_painter.native_select(layer_id=layer.id, mask=True)
            sidebar.active_panel_category = "Painter" if PRODUCT_UI else "Shader Proof"
    except TypeError:
        area.tag_redraw()
        later(activate_control, .2)
        return
    area.tag_redraw()
    later(scroll_product if PRODUCT_UI else wait_original, .2)


def scroll_product():
    global product_points, product_index
    point = (sidebar.x + sidebar.width*.75, sidebar.y + sidebar.height*.45)
    event("MOUSEMOVE", "NOTHING", point)
    for _ in range(36):
        event("WHEELDOWNMOUSE", "PRESS", point)
    scale = bpy.context.preferences.system.ui_scale
    product_points = [(sidebar.x + int(sidebar.width*fraction), sidebar.y + int(offset*scale))
                      for offset in range(12,180,12) for fraction in (.76,.9)]
    product_index = 0
    later(probe_product, .5)


def probe_product():
    global PRODUCT_UI, candidates, ui_route
    if product_index == len(product_points):
        PRODUCT_UI = False
        ui_route = "isolated native RNA sidebar; product control hover lookup unavailable"
        print("PRODUCT_CONTROL_LOCATION_UNAVAILABLE", CASES[case_index][0], flush=True)
        later(activate_control, .1)
        return
    event("MOUSEMOVE", "NOTHING", product_points[product_index])
    later(check_product, .12)


def check_product():
    global product_index, candidates, control_drawn, ui_route
    with bpy.context.temp_override(window=window, area=area, region=sidebar):
        prop = getattr(bpy.context, "button_prop", None)
        owner = getattr(bpy.context, "button_pointer", None)
        if (prop and owner and prop.identifier == CASES[case_index][2]
                and owner.as_pointer() == controlled().as_pointer()):
            candidates = [product_points[product_index]]
            control_drawn = True
            ui_route = "existing Open Painter Properties panel, native RNA control"
            print("PRODUCT_CONTROL_LOCATED", CASES[case_index][0], candidates[0], flush=True)
            later(wait_original, .2)
            return
    product_index += 1
    later(probe_product, .01)


def settled():
    return len(history) >= 6 and time.monotonic() - settle_started >= 1.5


def wait_original():
    global original_frame, phase, original_jitter, original_raw_jitter
    if not settled():
        later(wait_original, .1)
        return
    original_jitter = max(coverage_difference(frame, last_frame) for frame in history[-4:])
    original_raw_jitter = max(float(np.mean(abs(frame-last_frame))) for frame in history[-4:])
    if original_jitter >= .001 or time.monotonic()-settle_started < 3:
        assert time.monotonic()-settle_started < 15, ("Saved-value viewport stability", original_jitter)
        later(wait_original, .2)
        return
    original_frame = last_frame.copy()
    saved_frames[f"case_{case_index}_saved"] = original_frame
    phase = "CALIBRATE"
    calibrate()


def calibrate():
    global point, calibration_before
    assert control_drawn and attempt < len(candidates), "Could not locate native project mask slider"
    point = candidates[attempt]
    set_value(center)
    event("ESC", "PRESS", point)
    event("ESC", "RELEASE", point)
    event("MOUSEMOVE", "NOTHING", point)
    later(lambda: (event("LEFTMOUSE", "PRESS", point), later(calibration_unlock, .15)), .2)


def calibration_unlock():
    global calibration_before
    calibration_before = value()
    event("MOUSEMOVE", "NOTHING", (point[0] + 20, point[1]))
    later(lambda: (event("MOUSEMOVE", "NOTHING", (point[0] + 60, point[1])),
                   later(calibration_check, .15)), .15)


def calibration_check():
    global attempt, drag_span, phase, history, settle_started
    observed = value()
    event("LEFTMOUSE", "RELEASE", (point[0] + 60, point[1]))
    calibration.append(dict(point=point, before=calibration_before, after=observed))
    if abs(observed - calibration_before) < .005:
        attempt += 1
        later(calibrate, .2)
        return
    drag_span = max(12, int(target_span * 40 / abs(observed - calibration_before)))
    set_value(center if DIAGNOSTIC else CASES[case_index][3])
    phase, history, settle_started = "BASELINE", [], time.monotonic()
    later(wait_baseline, .2)


def drawn():
    global draw_serial
    if bpy.context.area == area:
        draw_serial += 1


def capture():
    global sampled_serial, last_frame, roi, pending, capture_geometry
    if sampled_serial == draw_serial:
        area.tag_redraw()
        later(capture, .08)
        return
    sampled_serial = draw_serial
    corners = [location_3d_to_region_2d(region, area.spaces.active.region_3d,
               obj.matrix_world @ Vector(corner)) for corner in obj.bound_box]
    corners = [point for point in corners if point is not None]
    assert len(corners) == 8, "Assigned asset is outside the current view"
    side = next((item for item in area.regions if item.type == "UI" and item.width > 20), None)
    right = side.x - region.x - 12 if side else region.width - 12
    x0, x1 = max(12, int(min(p.x for p in corners))), min(right, int(max(p.x for p in corners)))
    y0, y1 = max(12, int(min(p.y for p in corners))), min(region.height - 12, int(max(p.y for p in corners)))
    # Blender's WINDOW rectangle overlaps the native asset shelf in this saved
    # workspace; remove horizontal UI overlays from the material measurement.
    for overlay in area.regions:
        if overlay.type in {"ASSET_SHELF", "ASSET_SHELF_HEADER", "HEADER", "TOOL_HEADER"} and overlay.height:
            if overlay.y < region.y + region.height / 2:
                y0 = max(y0, overlay.y + overlay.height - region.y + 8)
            else:
                y1 = min(y1, overlay.y - region.y - 8)
    assert x1-x0 > 100 and y1-y0 > 100, "Projected asset ROI is too small"
    roi = [x0, y0, x1-x0, y1-y0]
    start = time.perf_counter()
    bpy.ops.screen.screenshot(filepath=str(REPORT.with_suffix(".capture.png")))
    saved = time.perf_counter()
    pixels = capture_pixels(REPORT.with_suffix(".capture.png"))
    capture_geometry = dict(window=[window.width, window.height], screenshot=[pixels.shape[1], pixels.shape[0]],
        viewport=[region.x, region.y, region.width, region.height],
        regions=[dict(type=item.type, rect=[item.x,item.y,item.width,item.height]) for item in area.regions])
    last_frame = pixels[region.y+y0:region.y+y1, region.x+x0:region.x+x1, :3].copy()
    complete = time.perf_counter()
    if phase in {"ORIGINAL", "BASELINE"}:
        history.append(last_frame)
        del history[:-8]
    if phase == "DRAG" and pending is not None and abs(value()-pending["before_value"]) > .02:
        diagnostic = shader_preview.diagnostics(bpy.context.scene, ts)
        assert shader_preview.current(bpy.context.scene, ts), diagnostic
        assert diagnostic["graph_builds"] == graph_builds and not diagnostic.get("pending_resources"), diagnostic
        assert not diagnostic.get("texture_bake_triggered"), diagnostic
        difference = coverage_difference(last_frame, pending["before_frame"])
        ready = difference > visible_threshold
        settled_noise = None
        if DIAGNOSTIC:
            pending["history"].append(last_frame.copy())
            del pending["history"][:-4]
            settled_noise = max(coverage_difference(frame,last_frame) for frame in pending["history"])
            ready = (len(pending["history"]) == 4 and complete-pending["enqueued"] > 3
                     and settled_noise < .001)
        if ready:
            assert held
            samples.append(dict(index=pending["index"], before_value=pending["before_value"],
                observed_value=value(), first_completed_frame_ms=(complete-pending["enqueued"])*1000,
                screenshot_save_ms=(saved-start)*1000, screenshot_load_and_read_ms=(complete-saved)*1000,
                material_mean_change=difference,
                settled_window_noise=settled_noise,
                raw_material_mean_change=float(np.mean(abs(last_frame-pending["before_frame"]))),
                before_release=held, diagnostic=diagnostic))
            saved_frames[f"case_{case_index}_drag_{pending['index']}"] = last_frame.copy()
            pending = None
    later(capture, .08)


def wait_baseline():
    global baseline, jitter, raw_jitter, visible_threshold, graph_builds, topology, observation
    global drag_input_start, held
    if not settled():
        later(wait_baseline, .1)
        return
    baseline = last_frame.copy()
    jitter = max(coverage_difference(frame, baseline) for frame in history[-4:])
    raw_jitter = max(float(np.mean(abs(frame-baseline))) for frame in history[-4:])
    if (jitter >= .001 or time.monotonic()-settle_started < 3) and time.monotonic()-settle_started < 15:
        later(wait_baseline, .2)
        return
    assert jitter < .001, f"Saved asset viewport not settled: {jitter}"
    visible_threshold = max(.003, jitter * 5)
    graph_builds = shader_preview.diagnostics(bpy.context.scene, ts)["graph_builds"]
    topology = graph_state(ts.material)[0]
    saved_frames[f"case_{case_index}_baseline"] = baseline
    observation = RasterObservation().__enter__()
    drag_input_start = len(input_events)
    event("MOUSEMOVE", "NOTHING", point)
    held = True
    later(lambda: (event("LEFTMOUSE", "PRESS", point), later(unlock_drag, .15)), .2)


def unlock_drag():
    event("MOUSEMOVE", "NOTHING", (point[0]+20, point[1]))
    later(begin_drag, .15)


def begin_drag():
    global phase, drag_index
    phase, drag_index = "DRAG", 0
    move_drag()


def move_drag():
    global pending
    # Eight wide reversals within one press; property assignments are never
    # used for these measured changes.
    direction = (1 if drag_index % 2 == 0 else -1) if DIAGNOSTIC else (2 if drag_index % 2 == 0 else 0)
    pending = dict(index=drag_index, before_value=value(), before_frame=last_frame.copy(),
                   enqueued=time.perf_counter(), history=[])
    event("MOUSEMOVE", "NOTHING", (point[0]+20+direction*drag_span, point[1]))
    later(await_frame, .05)


def await_frame():
    global drag_index, held, phase, restoration_started
    area.tag_redraw()
    if pending is not None:
        assert time.perf_counter()-pending["enqueued"] < (15 if DIAGNOSTIC else 5), (
            "No visible actual material change during held drag", CASES[case_index],
            pending["index"], pending["before_value"], value(), visible_threshold)
        later(await_frame, .05)
        return
    drag_index += 1
    if drag_index < DRAG_STEPS:
        later(move_drag, .05)
        return
    assert [int(np.sign(s["observed_value"]-s["before_value"])) for s in samples] == [1,-1]*(DRAG_STEPS//2)
    if DIAGNOSTIC:
        difference = coverage_difference(saved_frames[f"case_{case_index}_drag_0"],
                                         saved_frames[f"case_{case_index}_drag_1"])
        noise = max(sample["settled_window_noise"] for sample in samples)
        assert difference > max(.003, noise*5), ("Settled high-low coverage signal", difference, noise)
    assert all(.005 < s["observed_value"] < .995 for s in samples)
    assert max(s["observed_value"] for s in samples)-min(s["observed_value"] for s in samples) > .45
    assert [e["value"] for e in input_events[drag_input_start:] if e["type"] == "LEFTMOUSE"] == ["PRESS"]
    assert graph_state(ts.material)[0] == topology
    observation.assert_no_raster()
    event("LEFTMOUSE", "RELEASE", (point[0]+20-(drag_span if DIAGNOSTIC else 0), point[1]))
    held, phase = False, "RESTORE"
    set_value(original_value)
    restoration_started = time.monotonic()
    later(await_restoration, .2)


def await_restoration():
    global observation
    error = coverage_difference(last_frame, original_frame)
    tolerance = max(.001, original_jitter*3)
    if error > tolerance or time.monotonic()-restoration_started < .7:
        assert time.monotonic()-restoration_started < 8, ("Saved value restoration", error, tolerance)
        later(await_restoration, .1)
        return
    observation.assert_no_raster()
    assert ts.resolution == saved_resolution and value() == original_value
    results.append(dict(name=CASES[case_index][0], mask_id=controlled().id,
        ui_route=ui_route,
        parameter=CASES[case_index][2], original_value=original_value,
        material_roi=roi, baseline_jitter=jitter, visible_threshold=visible_threshold,
        baseline_raw_jitter=raw_jitter, original_baseline_jitter=original_jitter,
        original_baseline_raw_jitter=original_raw_jitter,
        restoration_mean_error=error, calibration=calibration, samples=samples,
        instrumentation=observation.report(), native_events=input_events[drag_input_start:]))
    saved_frames[f"case_{case_index}_restored"] = last_frame.copy()
    observation.__exit__(None, None, None)
    observation = None
    print("SAVED_PROJECT_MASK_PASS", CASES[case_index][0], flush=True)
    later(begin_case, .2)


later(load_project, 1.2)
