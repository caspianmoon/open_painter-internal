"""A long, uninterrupted Paint Material session in a real window (0.13 hardening, H4): hundreds
of gestures through one running tool on the 2K production prop (three sets, 25 layers), the next
gesture usually starting before the previous full-resolution result is ready.

The workload (seeded, repeatable): short corrections, long drags at several event rates, erase
strokes, alpha strokes, cancellations (Esc mid-gesture), layer and set changes through the shared
selection without restarting the tool, cross-set painting toggled on and off, undo/redo cycles,
saves; a fresh paint layer created late in the session and painted for the first time.

What is measured, each reported on its own:
- input → actual visible material change: pixel evidence (the viewport pixels around the pointer
  differ from the capture taken before the press) and the publication→redraw proxy, fresh and
  warm strokes, early and late session, per event rate;
- pointer release → final current result, on settled probes (a gesture that starts after the
  session settled) and for bursts (the last release of a burst until current);
- input loss and destination errors: lost gestures, strokes recorded on a layer other than the
  displayed one, pixels changed on sets the gesture never reached;
- resources: RSS, resource leaves, live/retired preview images, undo history, steady-state growth;
- the expensive operations (undo, redo, save, reopen, export) with their worst pauses.

Correctness at the end: every painted layer's shader leaves equal a fresh full recomputation for
every enabled channel and carry the committed stroke identity; the CPU evaluation of the last
saved file equals the session's; no preview image survives the save; undo/redo are exact.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/material_session_ui.py
OP_SESSION_STROKES (default 500), OP_SESSION_RESOLUTION (2048), OP_SESSION_LAYERS (25),
OP_SESSION_REPORT (the JSON report). Synthetic events only: tablet behaviour is NOT RUN here.
"""
import hashlib
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from time import perf_counter

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import quality_fixtures as fixtures
from open_painter.core import image_manager as images, layer_stack as stack
from open_painter.evaluation import shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.shader_resources import RESOURCE
from open_painter.painting import gesture, stroke_raster, strokes as records
from open_painter.ui import selection, vector_brush
from open_painter.ui.vector_brush import OP_OT_vector_brush

STROKES = int(os.environ.get("OP_SESSION_STROKES", "500"))
SAVE_EVERY = int(os.environ.get("OP_SESSION_SAVE_EVERY", "100"))          # a save every N gestures
UNDO_EVERY = int(os.environ.get("OP_SESSION_UNDO_EVERY", "25"))           # an undo/redo cycle every N gestures
CROSS_EVERY = int(os.environ.get("OP_SESSION_CROSS_EVERY", "80"))         # a cross-set stroke every N gestures (at N - 10)
FRESH_AT = int(os.environ.get("OP_SESSION_FRESH_AT", str(int(STROKES * .6))))   # the fresh layer's gesture
EXACT_EVERY = int(os.environ.get("OP_SESSION_EXACT_EVERY", "100"))        # exact (row digest) undo/redo checks every N gestures
LAYER_EVERY = int(os.environ.get("OP_SESSION_LAYER_EVERY", "40"))         # the displayed layer changes every N gestures
SET_EVERY = int(os.environ.get("OP_SESSION_SET_EVERY", "60"))             # the displayed set changes every N gestures
RESOLUTION = int(os.environ.get("OP_SESSION_RESOLUTION", "2048"))
LAYERS = int(os.environ.get("OP_SESSION_LAYERS", "25"))
OUT = ROOT / ".blender-test/quality"
OUT.mkdir(parents=True, exist_ok=True)
REPORT = Path(os.environ.get("OP_SESSION_REPORT", OUT / f"material-session-{RESOLUTION}.json"))
CAPTURE = 48          # px: the square around the pointer whose pixels prove a visible change
report = {"blender": bpy.app.version_string, "strokes_requested": STROKES, "resolution": RESOLUTION, "layers": LAYERS,
          "steps": [], "errors": [], "measurements": {}, "tablet": "NOT RUN: synthetic events only, no tablet attached"}
env = {"draws": [], "publications": [], "capture": None}


def rss_mb():
    """Peak resident memory of this Blender in MB (macOS ru_maxrss; Windows peak working set)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):   # PROCESS_MEMORY_COUNTERS
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        *((name, ctypes.c_size_t) for name in (
                            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage"))]
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        counters = Counters(cb=ctypes.sizeof(Counters))
        kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        return round(counters.PeakWorkingSetSize / (1024 * 1024), 1)
    import resource
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(usage / (1024 * 1024), 1)   # macOS reports bytes


def finish(status):
    REPORT.write_text(json.dumps(dict(status=status, **report), indent=2, default=str))
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


def stats(values):
    values = np.asarray([v for v in values if v is not None], np.float64)
    if not len(values):
        return {"count": 0}
    return {"count": int(len(values)), "p50_ms": round(float(np.percentile(values, 50)) * 1000, 1),
            "p95_ms": round(float(np.percentile(values, 95)) * 1000, 1), "p99_ms": round(float(np.percentile(values, 99)) * 1000, 1),
            "max_ms": round(float(values.max()) * 1000, 1)}


def digest(pixels):
    return hashlib.sha256(np.ascontiguousarray(pixels).tobytes()).hexdigest()


def outputs_digest(scene, ts):
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    return {o.channel: digest(images.pixels(o.image)) for o in ts.outputs if o.image is not None}


def layer_digest(scene, ts, layer):
    """Digest of a fresh recomputation of the layer's BASE_COLOR stroke rows (what a stroke changes)."""
    n = int(ts.resolution)
    if not layer.stroke_key:
        return "no-strokes"
    return digest(stroke_raster.channel_rows(ts, layer, "BASE_COLOR", n, 0, n, {}))


def set_paint_digest(scene, ts):
    """Digests of every paint layer's stroke rows of a set, plus which keys its layers render:
    a stroke leaking into the set shows here as a linked layer or changed rows."""
    return {layer.name: (layer.stroke_key, layer_digest(scene, ts, layer)) for layer in ts.layers if layer.type == "PAINT"}


def leaves_of(scene, ts, layer):
    """channel -> (leaf image, recorded state) of the layer's published strokes leaves."""
    graph = shader_preview.session_graph(scene, ts)
    found = {}
    if graph is None:
        return found
    for node in graph.nodes():
        descriptor = node.get("op_boundary", "")
        if descriptor.startswith('["strokes"') and layer.id in descriptor and node.image is not None:
            channel = json.loads(descriptor)[2]
            state = node.image.get("op_resource_state")
            found[channel] = (node.image, json.loads(state) if state else None)
    return found


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
    body_id, barrel_id, plate_id = body.id, barrel.id, plate.id
    SINGLE_SET = bool(os.environ.get("OP_SESSION_SINGLE_SET"))     # the 4K stress: the body alone, live
    for ts in ((body,) if SINGLE_SET else (body, barrel, plate)):
        ts.preview_backend = "LIVE"
    stack.add(barrel, "PAINT", "Barrel marks")
    area = max((a for a in window.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_editor()
        bpy.ops.object.select_all(action="DESELECT")
        for name in ("Prop body", "Prop barrel"):
            scene.objects[name].select_set(True)
        bpy.context.view_layer.objects.active = scene.objects["Prop body"]
        bpy.ops.view3d.view_axis(type="TOP")
        bpy.ops.view3d.view_selected(use_all_regions=False)
    area.spaces.active.shading.type = "MATERIAL"
    view = area.spaces.active.region_3d
    project = scene.open_painter

    def set_by(identifier):
        return next(t for t in bpy.context.scene.open_painter.texture_sets if t.id == identifier)

    def layer_by(ts, name):
        return next(l for l in ts.layers if l.name == name)

    project.active_texture_set = next(i for i, t in enumerate(project.texture_sets) if t.id == body_id)
    with bpy.context.temp_override(window=window, area=area, region=region):
        selection.select(bpy.context, layer_by(set_by(body_id), "Mud paint").id, ts=set_by(body_id))
    started = time.monotonic()
    live_ids = (body_id,) if SINGLE_SET else (body_id, barrel_id, plate_id)
    while time.monotonic() - started < 900 and not all(shader_preview.current(scene, set_by(i)) for i in live_ids):
        yield .2
    assert all(shader_preview.current(scene, set_by(i)) for i in live_ids), shader_preview.diagnostics(scene, set_by(body_id))
    report["measurements"]["first_compile_s"] = round(time.monotonic() - started, 1)
    report["measurements"]["rss_mb_after_compile"] = rss_mb()
    progress("fixture compiled")

    # ---- instrumentation: redraws, publications, and pixel evidence around the pointer ----
    def draw_stamp():
        env["draws"].append(perf_counter())
    handler = bpy.types.SpaceView3D.draw_handler_add(draw_stamp, (), "WINDOW", "POST_PIXEL")
    # Pixel evidence: the region framebuffer a draw callback can read holds only the overlays,
    # so the view is re-rendered into an offscreen with the viewport's own engine, matrices and
    # GPU textures (what a redraw shows at that moment), and the square around the pointer is
    # compared with the same render taken before the press. Rendered at half the region size.
    space = area.spaces.active
    square = CAPTURE
    offscreen = gpu.types.GPUOffScreen(square, square, format="RGBA8")
    from mathutils import Matrix

    def render_probe():
        """The material in the CAPTURE-pixel square around the pointer as the viewport engine
        renders it now (uint8 RGB): the view's projection is zoomed so that square fills a
        small offscreen, so the probe renders the same pixels the screen shows there."""
        capture = env["capture"]
        if capture is None:
            return None
        x, y = capture["xy"]
        sx, sy = region.width / square, region.height / square
        ndc_x, ndc_y = 2.0 * x / region.width - 1.0, 2.0 * y / region.height - 1.0
        zoom = Matrix.Translation((-ndc_x * sx, -ndc_y * sy, 0.0)) @ Matrix.Diagonal((sx, sy, 1.0, 1.0)) @ view.window_matrix
        with bpy.context.temp_override(window=window, area=area, region=region):
            offscreen.draw_view3d(bpy.context.scene, bpy.context.view_layer, space, region, view.view_matrix, zoom,
                                  do_color_management=True, draw_background=True)
        return np.asarray(offscreen.texture_color.read(), np.uint8).reshape(square, square, 4)[..., :3].copy()

    def probe(now=None):
        """Compare the pointer square with the reference; record the first visible change."""
        capture = env["capture"]
        if capture is None or capture.get("seen") is not None:
            return
        started_at = perf_counter()
        pixels = render_probe()
        if pixels is None:
            return
        if capture.get("reference") is None:
            capture["reference"] = pixels
            return
        if pixels.shape != capture["reference"].shape:
            return
        differing = int((np.abs(pixels.astype(np.int16) - capture["reference"].astype(np.int16)).max(axis=-1) > 2).sum())
        capture["last_differing"] = differing
        capture["draws"] = capture.get("draws", 0) + 1
        capture["probe_ms"] = round((perf_counter() - started_at) * 1000, 1)
        if differing >= capture["threshold"]:
            capture["seen"] = started_at
            capture["differing"] = differing
    # The tool's orange guide line would count as a visible change: only the material may.
    original_guide = OP_OT_vector_brush._draw
    OP_OT_vector_brush._draw = lambda self, context: None
    report["measurements"]["pixel_evidence"] = {
        "capture_px": square, "guide_line_drawn": False,
        "rule": "first offscreen re-render of the square around the pointer (the viewport engine, view and GPU textures, "
                "projection zoomed onto the square) after the press in which >= threshold pixels differ by > 2/255 from "
                "the pre-press render; polled between simulated events, so the time carries the polling cadence"}
    original_publish = gesture.Preview.publish

    def timed_publish(self, *args, **kwargs):
        done = original_publish(self, *args, **kwargs)
        if done:
            env["publications"].append(perf_counter())
        return done
    gesture.Preview.publish = timed_publish

    def px(point):
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        local = location_3d_to_region_2d(region, view, point)
        return int(region.x + local.x), int(region.y + local.y)

    settings = project.vector_brush
    settings.radius_mode, settings.radius, settings.spacing, settings.hardness = "WORLD", .06, .25, .8
    settings.universal, settings.blend, settings.depth_limit, settings.reach = False, "PAINT", .5, "SURFACE"
    settings.channels = {"BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT"}
    alpha = bpy.data.images.new("Session alpha", 64, 64, alpha=True, float_buffer=True)
    yy, xx = np.mgrid[0:64, 0:64]
    radial = np.clip(1.0 - np.hypot(xx - 31.5, yy - 31.5) / 32.0, 0, 1).astype(np.float32)
    alpha.pixels.foreach_set(np.repeat(radial[..., None], 4, -1).ravel())
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.vector_brush("INVOKE_DEFAULT")
    yield .3
    assert OP_OT_vector_brush._handler is not None
    tool = OP_OT_vector_brush._active

    rng = np.random.default_rng(int(os.environ.get("OP_SESSION_SEED", "7")))
    # Where the body (a 1.6 cube, bevelled) and the barrel (radius .4 at x 1.6) sit on screen.
    body_lo, body_hi = px((-.7, -.7, .8)), px((.7, .7, .8))
    barrel_c = px((1.6, 0, .6))
    gesture_log = []          # one record per gesture: kind, layer, events, timings, evidence
    lost, misdirected, outside_scope = [], [], []
    undo_cycles, saves, exports = [], [], []
    settled_probes, burst_settles = [], []
    history, rss_series = [], []
    scope_checks = []
    active = {"set": body_id, "layer": "Mud paint", "since": 0}
    last_release = {"at": None, "burst_since": None}
    fresh_created = False

    def select_target(set_id, layer_name):
        ts = set_by(set_id)
        project = bpy.context.scene.open_painter
        project.active_texture_set = next(i for i, t in enumerate(project.texture_sets) if t.id == set_id)
        with bpy.context.temp_override(window=window, area=area, region=region):
            selection.select(bpy.context, layer_by(ts, layer_name).id, ts=ts)
        active.update(set=set_id, layer=layer_name)

    def current_all():
        scene = bpy.context.scene
        return all(shader_preview.current(scene, set_by(i)) for i in live_ids)

    def wait_current(limit=120):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline and not current_all():
            yield .01
        return current_all()

    def agreement_now(label):
        """Debug: compare every painted layer's leaves with a fresh recomputation right now."""
        scene = bpy.context.scene
        project = scene.open_painter
        n = RESOLUTION
        found = []
        for set_id in (body_id, barrel_id):
            ts = set_by(set_id)
            for layer in ts.layers:
                if layer.type != "PAINT" or not layer.stroke_key:
                    continue
                for channel, (image, state) in leaves_of(scene, ts, layer).items():
                    prop = stack.channel_for(layer, channel)
                    base = None
                    if prop is not None and prop.image is not None:
                        from open_painter.evaluation.channel_eval import _image_source
                        base = _image_source(prop.image, prop, "PAINT", n, 0, n, {}, ts, {})
                    fresh = stroke_raster.channel_rows(ts, layer, channel, n, 0, n, {}, base=base)
                    leaf_pixels = images.pixels(image)
                    difference = np.abs(fresh - leaf_pixels).max(axis=-1)
                    count = int((difference > 1e-4).sum())
                    if count:
                        found.append({"label": label, "layer": layer.name, "channel": channel, "differing": count,
                                      "ready": bool(image.get("op_shader_resource_ready")), "image": image.name,
                                      "state_strokes": len((state or {}).get("identity", [])),
                                      "strokes_now": len(records.strokes_for(project, layer.stroke_key, ts))})
                        if channel == "BASE_COLOR" and not report.get("debug_dumped"):
                            report["debug_dumped"] = True
                            strokes = records.strokes_for(project, layer.stroke_key, ts)
                            from open_painter.evaluation.raster_cache import for_texture_set
                            cached = stroke_raster.rasters(ts, strokes, n, for_texture_set(scene, ts))
                            fresh_rasters = stroke_raster.rasters(ts, strokes, n, {})
                            info = []
                            for st, c, f in zip(strokes, cached, fresh_rasters):
                                ce, fe = stroke_raster._expand(c, 0, n, n), stroke_raster._expand(f, 0, n, n)
                                info.append({"id": st.id, "name": st.name, "blend": st.blend, "band_cached": stroke_raster.raster_band(c, n),
                                             "band_fresh": stroke_raster.raster_band(f, n), "raster_differs": int((np.abs(ce - fe) > 1e-6).sum()),
                                             "samples": st.sample_count, "anchors": len(st.anchors) > 0, "reach": st.reach})
                            rows, cols = np.nonzero(difference > 1e-4)
                            np.savez(str(OUT / "session-first-disagreement.npz"), fresh=fresh, leaf=leaf_pixels, rows=rows, cols=cols,
                                     info=json.dumps(info), state=json.dumps(state))
                            print("DEBUG dumped first disagreement", json.dumps(info), "rows", int(rows.min()), int(rows.max()), flush=True)
        if found:
            report.setdefault("debug_disagreements", []).extend(found)
            print("DEBUG disagreement", label, found[:2], flush=True)
            # Every ready leaf of the layer's key: do its pixels match its own recorded state?
            for set_id in (body_id, barrel_id):
                ts = set_by(set_id)
                for layer in ts.layers:
                    if layer.type != "PAINT" or not layer.stroke_key:
                        continue
                    by_id = {s.id: s for s in scene.open_painter.strokes}
                    for image in bpy.data.images:
                        text = image.get("op_resource_state")
                        if not image.get(RESOURCE) or not text:
                            continue
                        state = json.loads(text)
                        if state.get("key") != layer.stroke_key or state.get("channel") != "BASE_COLOR":
                            continue
                        identity = state.get("identity", [])
                        missing = [i for i, _ in identity if i not in by_id]
                        if missing or tuple(image.size) != (n, n):
                            print("DEBUG leaf", image.name, "ready", bool(image.get("op_shader_resource_ready")), "has_data", image.has_data,
                                  "strokes", len(identity), "missing", len(missing), "users", image.users, flush=True)
                            continue
                        # Recompute its own state: the strokes it lists, in stack order.
                        listed = {i for i, _ in identity}
                        rows = np.zeros((n, n, 4), np.float32)
                        from open_painter.painting.strokes import channel_value, stroke_channels
                        strokes = [st for st in records.strokes_for(scene.open_painter, layer.stroke_key) if st.id in listed and "BASE_COLOR" in stroke_channels(st)]
                        for st, raster in zip(strokes, stroke_raster.rasters(ts, strokes, n, {})):
                            cov = stroke_raster._expand(raster, 0, n, n)[..., None]
                            if st.blend == "ERASE":
                                rows[..., 3:4] *= 1.0 - cov
                                continue
                            color = np.asarray(channel_value(st, "BASE_COLOR"), np.float32)
                            out_alpha = cov + rows[..., 3:4] * (1.0 - cov)
                            pre = color * cov + rows[..., :3] * rows[..., 3:4] * (1.0 - cov)
                            rows[..., :3] = np.divide(pre, out_alpha, out=np.zeros_like(pre), where=out_alpha > 1e-8)
                            rows[..., 3:4] = out_alpha
                        own = int((np.abs(rows - images.pixels(image)).max(axis=-1) > 1e-4).sum())
                        print("DEBUG leaf", image.name, "ready", bool(image.get("op_shader_resource_ready")), "has_data", image.has_data,
                              "strokes", len(identity), "users", image.users, "differs_from_own_state", own, flush=True)
        return found

    def leaf_snapshot():
        found = {}
        for image in bpy.data.images:
            text = image.get("op_resource_state")
            if image.get(RESOURCE) and text:
                state = json.loads(text)
                found[image.name] = {"channel": state.get("channel"), "strokes": len(state.get("identity", [])),
                                     "identity": hashlib.sha1(json.dumps(state.get("identity")).encode()).hexdigest()[:8],
                                     "pixels": digest(images.pixels(image))[:12] if image.has_data else None,   # never read a bufferless leaf: reading regenerates it
                                     "ready": bool(image.get("op_shader_resource_ready")), "users": image.users, "pointer": image.as_pointer()}
        return found

    def gesture_path(kind):
        """Screen path and pacing of one gesture of ``kind`` on the active target."""
        if active["set"] == barrel_id:
            cx, cy = barrel_c
            span = 40
        else:
            cx, cy = (body_lo[0] + body_hi[0]) // 2, (body_lo[1] + body_hi[1]) // 2
            span = min(abs(body_hi[0] - body_lo[0]), abs(body_hi[1] - body_lo[1])) // 2 - 10
        x0 = cx + int(rng.uniform(-span, span) * .8)
        y0 = cy + int(rng.uniform(-span, span) * .8)
        if kind == "long":
            dx, dy = rng.uniform(-span, span) * 1.6, rng.uniform(-span, span) * 1.6
            events, pace = int(rng.integers(20, 41)), float(rng.choice([.008, .016, .033]))
        elif kind == "cross":
            # From the body onto the barrel: the pointer crosses the .4 gap.
            x0, y0 = body_hi[0] - 30, cy
            dx, dy = barrel_c[0] - x0, 0.0
            events, pace = 24, .016
        else:
            dx, dy = rng.uniform(-span, span) * .4, rng.uniform(-span, span) * .4
            events, pace = int(rng.integers(3, 7)), float(rng.choice([.008, .016, .033]))
        return (x0, y0), (dx, dy), events, pace

    with tempfile.TemporaryDirectory(prefix="op-session-") as tmp:
        for index in range(STROKES):
            scene = bpy.context.scene
            project = scene.open_painter
            settings = project.vector_brush            # re-resolved: an undo replaces the scene datablock
            alpha = bpy.data.images.get("Session alpha")
            # ---- session events between gestures (the tool keeps running through them) ----
            if index == FRESH_AT and not fresh_created:
                # A fresh paint layer, created late: its first stroke is measured on its own.
                ts = set_by(body_id)
                fresh = stack.add(ts, "PAINT", "Fresh late layer")
                fresh_created = True
                select_target(body_id, "Fresh late layer")
                yield .3
            elif index % LAYER_EVERY == LAYER_EVERY - 1:
                names = ["Mud paint", "Marks"] + (["Fresh late layer"] if fresh_created else [])
                select_target(body_id, names[(names.index(active["layer"]) + 1) % len(names)] if active["layer"] in names else names[0])
            if index % SET_EVERY == SET_EVERY - 1 and not SINGLE_SET:
                select_target(barrel_id, "Barrel marks") if active["set"] != barrel_id else select_target(body_id, "Mud paint")
            kind = "short"
            roll = rng.uniform()
            if index % CROSS_EVERY == CROSS_EVERY - 10 and not SINGLE_SET:
                kind = "cross"
            elif roll < .2:
                kind = "long"
            elif roll < .3:
                kind = "erase"
            elif roll < .35:
                kind = "alpha"
            elif roll < .4:
                kind = "cancel"
            if kind == "cross":
                select_target(body_id, "Mud paint")
                settings.universal, settings.radius = True, .12
                plate_before = set_paint_digest(scene, set_by(plate_id))
            else:
                settings.universal, settings.radius = False, .06
            settings.blend = "ERASE" if kind == "erase" else "PAINT"
            settings.alpha_image = alpha if kind == "alpha" else None
            settings.base_color = (float(rng.uniform(.1, .9)), float(rng.uniform(.1, .9)), float(rng.uniform(.1, .9)), 1)
            settings.roughness, settings.metallic, settings.height = float(rng.uniform(0, 1)), float(rng.choice([0.0, 1.0])), float(rng.uniform(0, 1))
            (x0, y0), (dx, dy), events, pace = gesture_path(kind)
            if os.environ.get("OP_SESSION_TRACE"):
                print(f"TRACE gesture {index} {kind} on {active['layer']} events {events} t={time.monotonic():.1f}", flush=True)
            scene = bpy.context.scene
            ts = set_by(active["set"])
            layer = layer_by(ts, active["layer"])
            key_before = layer.stroke_key
            count_before = len(scene.open_painter.strokes)
            with bpy.context.temp_override(window=window, area=area, region=region):
                displayed = vector_brush.destination(bpy.context)
            settled_before = current_all()
            first_on_layer = not records.strokes_for(scene.open_painter, key_before) if key_before else True
            # ---- the gesture ----
            window.cursor_warp(x0, y0)
            mouse("MOUSEMOVE", "NOTHING", x0, y0)
            watched = index % 5 == 0 or first_on_layer or kind == "cross"   # representative gestures carry pixel evidence
            capture = env["capture"] = {"xy": (x0 - region.x, y0 - region.y), "reference": None, "seen": None,
                                        "threshold": 6 if kind != "erase" else 2} if watched else None
            if watched:
                probe()      # the pre-press render
            reference_taken = watched and capture["reference"] is not None
            publications_before = len(env["publications"])
            pressed = perf_counter()
            mouse("LEFTMOUSE", "PRESS", x0, y0)
            pauses = []
            cancelled = False
            for step in range(1, events + 1):
                sent = perf_counter()
                mouse("MOUSEMOVE", "NOTHING", x0 + dx * step / events, y0 + dy * step / events)
                yield pace
                pauses.append(perf_counter() - sent)
                if watched:
                    probe()
                if kind == "cancel" and step == events // 2:
                    window.event_simulate(type="ESC", value="PRESS")
                    cancelled = True
                    yield .02
            released = perf_counter()
            mouse("LEFTMOUSE", "RELEASE", x0 + dx, y0 + dy)
            yield .01
            if watched and capture["seen"] is None and not cancelled:
                # The change may show after the release (a fresh layer's first stroke has no live
                # preview: it appears when its leaf publishes): keep probing, bounded.
                for _ in range(60):
                    yield .03
                    probe()
                    if capture["seen"] is not None:
                        break
            visible = capture["seen"] if watched else None
            env["capture"] = None
            later = [t for t in env["publications"][publications_before:]]
            first_publish = later[0] if later else None
            first_draw_after_publish = next((t for t in env["draws"] if first_publish is not None and t > first_publish), None)
            scene = bpy.context.scene
            ts = set_by(active["set"])
            layer = layer_by(ts, active["layer"])
            recorded = len(scene.open_painter.strokes) - count_before
            entry = {"index": index, "kind": kind, "set": ts.name, "layer": layer.name, "events": events, "pace_ms": pace * 1000,
                     "displayed": displayed, "recorded": recorded, "settled_before": settled_before, "first_on_layer": first_on_layer,
                     "event_pause_max_ms": round(max(pauses) * 1000, 1),
                     "visible_ms": round((visible - pressed) * 1000, 1) if visible else None,
                     "visible_before_release": (visible is not None and visible <= released),
                     "publish_ms": round((first_publish - pressed) * 1000, 1) if first_publish else None,
                     "proxy_visible_ms": round((first_draw_after_publish - pressed) * 1000, 1) if first_draw_after_publish else None,
                     "watched": watched, "capture_pixels_changed": capture.get("differing") if watched else None,
                     "reference_taken": reference_taken, "probes": capture.get("draws", 0) if watched else 0,
                     "probe_ms": capture.get("probe_ms") if watched else None,
                     "draws_during": int(np.sum(np.asarray(env["draws"]) > pressed)), "publications": len(later)}
            if cancelled:
                if recorded != 0:
                    lost.append({"index": index, "kind": kind, "recorded": recorded, "expected": 0})
            elif recorded != 1:
                lost.append({"index": index, "kind": kind, "recorded": recorded, "expected": 1})
            elif scene.open_painter.strokes[-1].layer_key != layer.stroke_key:
                misdirected.append({"index": index, "displayed": displayed, "recorded_key": scene.open_painter.strokes[-1].layer_key,
                                    "target_key": layer.stroke_key})
            if kind == "cross" and recorded == 1:
                stroke = scene.open_painter.strokes[-1]
                entry["scope"] = sorted(records.scope_ids(stroke))
                entry["linked_barrel"] = any(l.stroke_key == stroke.layer_key for l in set_by(barrel_id).layers)
                plate_after = set_paint_digest(scene, set_by(plate_id))
                scope_checks.append({"index": index, "plate_unchanged": plate_after == plate_before, "scope": entry["scope"]})
                if plate_after != plate_before:
                    outside_scope.append(index)
            gesture_log.append(entry)
            if os.environ.get("OP_SESSION_TRACE"):
                print(f"TRACE done {index} recorded {recorded} visible {entry['visible_ms']} t={time.monotonic():.1f}", flush=True)
            if os.environ.get("OP_SESSION_DEBUG_EVERY"):
                current = yield from wait_current()
                agreement_now(f"after gesture {index} ({kind})")
            # ---- bursts and settled probes ----
            if index % 50 == 49:
                current = yield from wait_current()
                settled_probes.append({"index": index, "release_to_current_ms": round((perf_counter() - released) * 1000, 1), "current": current,
                                       "burst_length": index - (last_release["burst_since"] if last_release["burst_since"] is not None else index)})
                if last_release["burst_since"] is not None:
                    burst_settles.append({"from": last_release["burst_since"], "to": index,
                                          "last_release_to_current_ms": round((perf_counter() - released) * 1000, 1)})
                last_release["burst_since"] = None
                if not current:
                    report["errors"].append(f"gesture {index}: not current after 120 s: {shader_preview.diagnostics(scene, ts)}")
                    break
                if os.environ.get("OP_SESSION_DEBUG"):
                    agreement_now(f"settled probe at {index}")
            else:
                if last_release["burst_since"] is None:
                    last_release["burst_since"] = index
                yield float(rng.uniform(0.0, .08))    # the next gesture begins before this one settles
            # ---- undo / redo (exact), saves ----
            if index % UNDO_EVERY == UNDO_EVERY - 1:
                scene = bpy.context.scene
                count = len(scene.open_painter.strokes)
                exact = index % EXACT_EVERY == EXACT_EVERY - 1
                target_layer = layer_by(set_by(active["set"]), active["layer"])
                before_digest = layer_digest(scene, set_by(active["set"]), target_layer) if exact else None
                if os.environ.get("OP_SESSION_DEBUG"):
                    current = yield from wait_current()
                    snap_before = leaf_snapshot()
                ready_before = {i.name for i in bpy.data.images if i.get(RESOURCE) and i.get("op_shader_resource_ready")}
                started_at = perf_counter()
                key("Z", ctrl=True)
                yield .3
                scene = bpy.context.scene
                after_undo = len(scene.open_painter.strokes)
                ready_after = {i.name for i in bpy.data.images if i.get(RESOURCE) and i.get("op_shader_resource_ready")}
                retired = len(ready_before - ready_after)
                current = yield from wait_current()
                undo_s = perf_counter() - started_at
                undo_digest = layer_digest(scene, set_by(active["set"]), layer_by(set_by(active["set"]), active["layer"])) if exact else None
                started_at = perf_counter()
                key("Z", ctrl=True, shift=True)
                yield .3
                scene = bpy.context.scene
                after_redo = len(scene.open_painter.strokes)
                current = (yield from wait_current()) and current
                redo_s = perf_counter() - started_at
                redo_digest = layer_digest(scene, set_by(active["set"]), layer_by(set_by(active["set"]), active["layer"])) if exact else None
                if os.environ.get("OP_SESSION_DEBUG"):
                    snap_after = leaf_snapshot()
                    for name, before_entry in snap_before.items():
                        after_entry = snap_after.get(name)
                        if after_entry is None:
                            print("DEBUG leaf gone after undo/redo:", name, before_entry, flush=True)
                        elif (after_entry["identity"], after_entry["pixels"], after_entry["ready"]) != (before_entry["identity"], before_entry["pixels"], before_entry["ready"]):
                            print("DEBUG leaf changed:", name, "before", before_entry, "after", after_entry, flush=True)
                    for name in set(snap_after) - set(snap_before):
                        print("DEBUG leaf new after undo/redo:", name, snap_after[name], flush=True)
                    agreement_now(f"after undo/redo at {index}")
                undo_cycles.append({"at": index, "before": count, "after_undo": after_undo, "after_redo": after_redo,
                                    "undo_s": round(undo_s, 2), "redo_s": round(redo_s, 2), "current": current,
                                    "leaves_ready_before": len(ready_before), "leaves_retired_by_undo": retired,
                                    "tool_running": OP_OT_vector_brush._active is tool,
                                    "exact": None if not exact else (undo_digest != before_digest and redo_digest == before_digest)})
                if OP_OT_vector_brush._active is None:
                    with bpy.context.temp_override(window=window, area=area, region=region):
                        bpy.ops.open_painter.vector_brush("INVOKE_DEFAULT")
                    yield .3
                    tool = OP_OT_vector_brush._active
                last_release["burst_since"] = None
            if index % SAVE_EVERY == SAVE_EVERY - 1:
                scene = bpy.context.scene
                path = Path(tmp) / f"session-{index}.blend"
                started_at = perf_counter()
                bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
                saves.append({"at": index, "seconds": round(perf_counter() - started_at, 2), "bytes": path.stat().st_size,
                              "previews_after_save": sum(1 for i in bpy.data.images if i.get("op_gesture_preview") and i.users)})
                yield .3
                if OP_OT_vector_brush._active is None:
                    with bpy.context.temp_override(window=window, area=area, region=region):
                        bpy.ops.open_painter.vector_brush("INVOKE_DEFAULT")
                    yield .3
                    tool = OP_OT_vector_brush._active
                last_release["burst_since"] = None
            if index % 50 == 49:
                scene = bpy.context.scene
                history.append({"at": index, "images": len(bpy.data.images),
                                "leaves": sum(1 for i in bpy.data.images if i.get(RESOURCE)),
                                "previews": sum(1 for i in bpy.data.images if i.get("op_gesture_preview")),
                                "strokes": len(scene.open_painter.strokes)})
                rss_series.append({"at": index, "rss_mb": rss_mb()})
                visible_so_far = [g["visible_ms"] for g in gesture_log if g["visible_ms"] is not None]
                progress(f"{index + 1} gestures · visible p95 {np.percentile(visible_so_far, 95) if visible_so_far else float('nan'):.0f} ms · rss {rss_mb()} MB")

        # ---- the end of the session: settle, then correctness against a fresh recomputation ----
        current = yield from wait_current(300)
        assert current, shader_preview.diagnostics(bpy.context.scene, set_by(body_id))
        gesture.sweep()
        gesture._reap()
        yield 2.5
        gesture._reap()
        scene = bpy.context.scene
        project = scene.open_painter
        n = RESOLUTION
        agreement = []
        for set_id in live_ids:
            ts = set_by(set_id)
            for layer in ts.layers:
                if layer.type != "PAINT" or not layer.stroke_key:
                    continue
                leaves = leaves_of(scene, ts, layer)
                identity = [[s.id, s.revision] for s in records.strokes_for(project, layer.stroke_key, ts)]
                resources = {}
                for channel, (image, state) in leaves.items():
                    prop = stack.channel_for(layer, channel)
                    base = None
                    if prop is not None and prop.image is not None:
                        # The leaf composites the strokes over the layer's paint canvas, as the boundary does.
                        from open_painter.evaluation.channel_eval import _image_source
                        base = _image_source(prop.image, prop, "PAINT", n, 0, n, {}, ts, {})
                    fresh = stroke_raster.channel_rows(ts, layer, channel, n, 0, n, resources, base=base)
                    leaf_pixels = images.pixels(image)
                    if tuple(image.size) != (n, n):
                        agreement.append({"set": ts.name, "layer": layer.name, "channel": channel,
                                          "leaf_size": list(image.size), "size_mismatch": True, "image_name": image.name,
                                          "is_resource": bool(image.get(RESOURCE)), "has_state": bool(state),
                                          "leaf_ready": bool(image.get("op_shader_resource_ready")),
                                          "strokes": len(identity)})
                        report["errors"].append(f"leaf {ts.name}/{layer.name}/{channel} is {list(image.size)} "
                                                f"(name={image.name!r} resource={bool(image.get(RESOURCE))} "
                                                f"ready={bool(image.get('op_shader_resource_ready'))}), expected {n}")
                        continue
                    difference = np.abs(fresh - leaf_pixels)
                    error = float(difference.max())
                    if error >= 1e-4 and os.environ.get("OP_SESSION_DEBUG"):
                        bands = {s.id: stroke_raster.raster_band(r, n) for s, r in zip(records.strokes_for(project, layer.stroke_key, ts),
                                 stroke_raster.rasters(ts, records.strokes_for(project, layer.stroke_key, ts), n, resources))}
                        np.savez(str(OUT / f"session-disagreement-{layer.name}-{channel}.npz"), fresh=fresh, leaf=leaf_pixels,
                                 state=json.dumps(state), bands=json.dumps(bands),
                                 kinds=json.dumps([(s.id, s.blend, s.order) for s in records.strokes_for(project, layer.stroke_key, ts)]))
                    agreement.append({"set": ts.name, "layer": layer.name, "channel": channel, "max_error": error,
                                      "identity_matches": (state or {}).get("identity") == identity,
                                      "strokes": len(identity), "leaf_ready": bool(image.get("op_shader_resource_ready")),
                                      "leaf_size": list(image.size), "differing_fraction": float((difference.max(axis=-1) > 1e-4).mean()),
                                      "fresh_alpha": [float(fresh[..., 3].min()), float(fresh[..., 3].max())],
                                      "leaf_alpha": [float(leaf_pixels[..., 3].min()), float(leaf_pixels[..., 3].max())],
                                      "base": base is not None})
        report["measurements"]["final_agreement"] = agreement
        report["measurements"]["rss_mb_before_export"] = rss_mb()
        # Export (its own cost), then the CPU evaluation of the session for the reopen comparison.
        from open_painter.export.exporter import export
        ts = set_by(body_id)
        ts.export.padding_mode, ts.export.resolution, ts.export.preset, ts.export.file_format, ts.export.bit_depth = "AUTOMATIC", "SET", "BLENDER", "PNG", "8"
        started_at = perf_counter()
        exported = export(bpy.context, ts, ts.export, directory=Path(tmp) / "export", overwrite=True)
        exports.append({"seconds": round(perf_counter() - started_at, 2), "files": len(exported)})
        session_digest = {set_id: outputs_digest(scene, set_by(set_id)) for set_id in (body_id, barrel_id, plate_id)}
        session_rows = {set_id: set_paint_digest(scene, set_by(set_id)) for set_id in (body_id, barrel_id, plate_id)}
        # The body's output pixels themselves, for the magnitude of any reopen difference.
        kept = Path(tmp) / "session-outputs"
        kept.mkdir()
        for output in set_by(body_id).outputs:
            if output.image is not None:
                np.save(str(kept / f"{output.channel}.npy"), images.pixels(output.image))
        final_path = Path(tmp) / "session-final.blend"
        started_at = perf_counter()
        bpy.ops.wm.save_as_mainfile(filepath=str(final_path), copy=True)
        saves.append({"at": "final", "seconds": round(perf_counter() - started_at, 2), "bytes": final_path.stat().st_size,
                      "previews_after_save": sum(1 for i in bpy.data.images if i.get("op_gesture_preview") and i.users)})
        strokes_saved = len(scene.open_painter.strokes)
        bpy.types.SpaceView3D.draw_handler_remove(handler, "WINDOW")
        gesture.Preview.publish = original_publish
        OP_OT_vector_brush._draw = original_guide
        offscreen.free()
        draws_np = np.asarray(env["draws"])
        gaps = np.diff(draws_np) if len(draws_np) > 1 else np.zeros(1)
        leaves = [i for i in bpy.data.images if i.get(RESOURCE)]
        previews = [i for i in bpy.data.images if i.get("op_gesture_preview")]
        report["measurements"]["datablocks"] = {
            "images": len(bpy.data.images), "node_groups": len(bpy.data.node_groups),
            "leaves": {"total": len(leaves), "with_users": sum(1 for i in leaves if i.users),
                       "ready": sum(1 for i in leaves if i.get("op_shader_resource_ready"))},
            "previews": {"total": len(previews), "with_users": sum(1 for i in previews if i.users)},
            "released_datablocks": __import__("open_painter.evaluation.shader_resources", fromlist=["released_count"]).released_count(),
        }
        report["measurements"]["preview_images_left"] = [i.name for i in previews]
        bpy.ops.screen.screenshot(filepath=str(REPORT.with_suffix(".png")))
        # ---- reopen the final save: the CPU evaluation must equal the session's ----
        started_at = perf_counter()
        bpy.ops.wm.open_mainfile(filepath=str(final_path))
        reopen_s = perf_counter() - started_at
        yield .5
        scene = bpy.context.scene
        reopened = {set_id: outputs_digest(scene, set_by(set_id)) for set_id in (body_id, barrel_id, plate_id)}
        reopened_rows = {set_id: set_paint_digest(scene, set_by(set_id)) for set_id in (body_id, barrel_id, plate_id)}
        report["measurements"]["reopen"] = {
            "seconds": round(reopen_s, 2), "strokes": len(scene.open_painter.strokes), "strokes_saved": strokes_saved,
            "outputs_equal": reopened == session_digest,
            "stroke_rows_equal": reopened_rows == session_rows,
            "differing_outputs": [(set_by(set_id).name, channel) for set_id in reopened for channel in reopened[set_id]
                                  if reopened[set_id].get(channel) != session_digest[set_id].get(channel)],
            "differing_rows": [(set_by(set_id).name, layer) for set_id in reopened_rows for layer in reopened_rows[set_id]
                               if reopened_rows[set_id].get(layer) != session_rows[set_id].get(layer)],
            "body_max_abs_difference": {output.channel: float(np.abs(np.load(str(kept / f"{output.channel}.npy")) - images.pixels(output.image)).max())
                                        for output in set_by(body_id).outputs if output.image is not None and (kept / f"{output.channel}.npy").exists()},
            "preview_images_in_file": sum(1 for i in bpy.data.images if i.get("op_gesture_preview")),
            "preview_bound_to_layers": any(prop.image is not None and prop.image.get("op_gesture_preview")
                                           for t in scene.open_painter.texture_sets for l in t.layers for prop in l.channels),
        }

    # ---- the report ----
    log = gesture_log
    committed = [g for g in log if g["kind"] != "cancel"]
    early, late = committed[:100], committed[-100:]
    fresh = [g for g in committed if g["first_on_layer"]]
    warm = [g for g in committed if not g["first_on_layer"]]
    by_rate = {}
    for g in committed:
        by_rate.setdefault(f"{g['pace_ms']:.0f}ms", []).append(g["visible_ms"] / 1000 if g["visible_ms"] else None)

    def visible(group):
        return stats([g["visible_ms"] / 1000 for g in group if g["visible_ms"] is not None])

    def proxy(group):
        return stats([g["proxy_visible_ms"] / 1000 for g in group if g["proxy_visible_ms"] is not None])
    missing_visible = [g["index"] for g in committed if g["watched"] and g["visible_ms"] is None]
    warm_probes = [p for p in settled_probes if p["index"] >= 100]
    report["measurements"].update({
        "gestures": {"total": len(log), "committed": len(committed), "cancelled": len(log) - len(committed),
                     "by_kind": {k: sum(1 for g in log if g["kind"] == k) for k in ("short", "long", "erase", "alpha", "cancel", "cross")}},
        "input_to_visible_pixels": {"watched_gestures": sum(1 for g in committed if g["watched"]),
                                    "all": visible(committed), "early": visible(early), "late": visible(late),
                                    "fresh_layer_strokes": visible(fresh), "warm": visible(warm), "by_event_rate": {k: stats(v) for k, v in by_rate.items()},
                                    "before_release": sum(1 for g in committed if g.get("visible_before_release")),
                                    "missing": missing_visible, "missing_count": len(missing_visible)},
        "input_to_visible_proxy": {"all": proxy(committed), "early": proxy(early), "late": proxy(late)},
        "input_to_publish": stats([g["publish_ms"] / 1000 for g in committed if g["publish_ms"] is not None]),
        "event_pause": stats([g["event_pause_max_ms"] / 1000 for g in log]),
        "release_to_current_settled_probes": {"probes": settled_probes,
                                              "warm": stats([p["release_to_current_ms"] / 1000 for p in warm_probes]),
                                              "all": stats([p["release_to_current_ms"] / 1000 for p in settled_probes])},
        "burst_settle": burst_settles,
        "fresh_layer_first_stroke": [g for g in log if g["first_on_layer"]],
        "lost_gestures": lost, "misdirected_strokes": misdirected, "writes_outside_scope": outside_scope, "scope_checks": scope_checks,
        "undo_redo": undo_cycles,
        "undo_s": stats([u["undo_s"] for u in undo_cycles]), "redo_s": stats([u["redo_s"] for u in undo_cycles]),
        "saves": saves, "exports": exports,
        "redraw_gap": stats(gaps.tolist()),
        "history": history, "rss": rss_series, "rss_mb_peak": rss_mb(),
        "strokes_recorded": strokes_saved,
        "targets": {"input_to_visible_p95_ms": 100, "warm_release_to_current_ms": 250,
                    "note": "quality targets kept visible; measured values above them are reported as limits, not failures of this test"},
    })
    report["gesture_log"] = log
    agreement = report["measurements"]["final_agreement"]
    assert not lost, lost
    assert not misdirected, misdirected
    assert not outside_scope, outside_scope
    assert all(u["after_undo"] == u["before"] - 1 and u["after_redo"] == u["before"] for u in undo_cycles), undo_cycles
    assert all(u["exact"] is not False for u in undo_cycles), [u for u in undo_cycles if u["exact"] is False]
    assert agreement and all(a["max_error"] < 1e-4 and a["identity_matches"] for a in agreement), [a for a in agreement if a["max_error"] >= 1e-4 or not a["identity_matches"]]
    assert not report["measurements"]["preview_images_left"], report["measurements"]["preview_images_left"]
    assert report["measurements"]["reopen"]["outputs_equal"] and not report["measurements"]["reopen"]["preview_bound_to_layers"], report["measurements"]["reopen"]
    assert report["measurements"]["reopen"]["strokes"] == strokes_saved


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
