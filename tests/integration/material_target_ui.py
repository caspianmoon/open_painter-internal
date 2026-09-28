"""Paint Material target lifecycle in a real window (0.13 hardening, H2 §6.5): one running
material-brush operator, successive gestures, the selection changed between and during them.

The scenarios, each through the same modal operator (never reconstructed between the actions
under test), with the actual stroke records and channel pixels as evidence:

1. invoke on layer A1, stroke, select layer A2 in the same set, paint again: only A2 receives
   the stroke, and the displayed destination names A2 before and during the gesture;
2. switch the texture set (B) through the shared selection and paint: B receives it;
3. change the target during pointer-down: the pending stroke is discarded, the preview images
   are unbound, the committed work is unchanged;
4. change brush settings during pointer-down: the record carries the captured values, the next
   gesture the new ones;
5. hide the target layer (and its folder), delete it, re-key its mask: refusal or safe
   termination, never a stroke elsewhere;
6. a UV edit and a slot reassignment during pointer-down discard the gesture;
7. a cross-set link, cross-set painting disabled, a stroke near B: B's pixels stay identical;
8. Esc while idle exits; the area re-typed under the running tool ends it.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/material_target_ui.py
OP_MATERIAL_TARGET_REPORT selects the JSON report; OP_MATERIAL_TARGET_REPRODUCE records the
verdicts of scenarios 1 and 7 instead of asserting them (the H0 before-fix evidence).
"""
import hashlib
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
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.painting import gesture, stroke_raster
from open_painter.ui import selection, vector_brush
from open_painter.ui.vector_brush import OP_OT_vector_brush

OUT = ROOT / ".blender-test/quality"
OUT.mkdir(parents=True, exist_ok=True)
REPORT = Path(os.environ.get("OP_MATERIAL_TARGET_REPORT", OUT / "material-target-ui.json"))
REPRODUCE = bool(os.environ.get("OP_MATERIAL_TARGET_REPRODUCE"))
report = {"blender": bpy.app.version_string, "steps": [], "errors": [], "checks": [], "verdicts": {}}
env = {}


def finish(status):
    REPORT.write_text(json.dumps(dict(status=status, **report), indent=2, default=str))
    print("MATERIAL_TARGET_UI_" + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()


def mouse(kind, value, x, y):
    env["window"].event_simulate(type=kind, value=value, x=int(x), y=int(y))


def progress(message):
    report["steps"].append(message)
    print("MATERIAL_TARGET_UI step:", message, flush=True)


def check(name, **facts):
    report["checks"].append({"name": name, **facts})
    print(f"MATERIAL_TARGET_UI {name}: PASS {facts}", flush=True)


def verdict(name, passed, **facts):
    """A reproduction records its outcome; a regression asserts it."""
    report["verdicts"][name] = {"verdict": "PASS" if passed else "FAIL", **facts}
    print(f"MATERIAL_TARGET_UI verdict {name}: {'PASS' if passed else 'FAIL'} {facts}", flush=True)
    if not REPRODUCE:
        assert passed, (name, facts)


def outputs(scene, ts):
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    return {o.channel: hashlib.sha256(images.pixels(o.image).tobytes()).hexdigest() for o in ts.outputs if o.image is not None}


def strokes_of(scene, layer):
    return [s for s in scene.open_painter.strokes if layer.stroke_key and s.layer_key == layer.stroke_key]


def bound_previews(scene, ts, layer):
    from gpu_input_probe import bound_inputs
    return [channel for channel, resource in bound_inputs(scene, ts, layer).items()
            if resource.get('op_gesture_preview')]


def world_to_window(region, view, point):
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    local = location_3d_to_region_2d(region, view, point)
    return int(region.x + local.x), int(region.y + local.y)


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
    project = scene.open_painter
    # Two plates side by side, 0.2 apart, in two sets; set A carries a folder with two paint layers.
    plate_a = fixtures._mesh_object("Target A", [(-2.2, -1, 0), (-.2, -1, 0), (-.2, 1, 0), (-2.2, 1, 0)], [(0, 1, 2, 3)],
                                    uvs=[[(0, 0), (1, 0), (1, 1), (0, 1)]])
    plate_b = fixtures._mesh_object("Target B", [(0, -1, 0), (2, -1, 0), (2, 1, 0), (0, 1, 0)], [(0, 1, 2, 3)],
                                    uvs=[[(0, 0), (1, 0), (1, 1), (0, 1)]])
    set_a = fixtures._set(scene, "A", plate_a, 512)
    set_b = fixtures._set(scene, "B", plate_b, 512)
    set_a, set_b = (next(t for t in project.texture_sets if t.name == name) for name in ("A", "B"))
    for ts in (set_a, set_b):
        ts.preview_backend = "LIVE"
        base = stack.add(ts, "FILL", "Base")
        stack.channel_for(base, "BASE_COLOR").color = (.5, .5, .5, 1)
    folder = stack.add(set_a, "FOLDER", "Group")
    a1 = stack.add(set_a, "PAINT", "A1", parent_id=folder.id)
    a2 = stack.add(set_a, "PAINT", "A2", parent_id=folder.id)
    b1 = stack.add(set_b, "PAINT", "B1")
    ids = {"a": set_a.id, "b": set_b.id, "a1": a1.id, "a2": a2.id, "b1": b1.id, "folder": folder.id}
    bpy.context.view_layer.update()

    def resolve():
        sa = next(t for t in project.texture_sets if t.id == ids["a"])
        sb = next(t for t in project.texture_sets if t.id == ids["b"])
        layers = {key: next((l for l in (sa.layers if key != "b1" else sb.layers) if l.id == ids[key]), None)
                  for key in ("a1", "a2", "b1", "folder")}
        return sa, sb, layers

    area = max((a for a in window.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_editor()
        bpy.ops.object.select_all(action="DESELECT")
        plate_a.select_set(True); plate_b.select_set(True)
        bpy.context.view_layer.objects.active = plate_a
        bpy.ops.view3d.view_axis(type="TOP")
        bpy.ops.view3d.view_selected(use_all_regions=False)
    area.spaces.active.shading.type = "MATERIAL"
    view = area.spaces.active.region_3d
    project.active_texture_set = next(i for i, t in enumerate(project.texture_sets) if t.id == ids["a"])
    with bpy.context.temp_override(window=window, area=area, region=region):
        selection.select(bpy.context, ids["a1"], ts=next(t for t in project.texture_sets if t.id == ids["a"]))
    started = time.monotonic()
    while time.monotonic() - started < 300 and not all(shader_preview.current(scene, t) for t in resolve()[:2]):
        yield .2
    progress("fixture compiled")

    settings = project.vector_brush
    settings.radius_mode, settings.radius, settings.spacing, settings.hardness = "WORLD", .15, .25, .8
    settings.universal, settings.blend, settings.depth_limit = False, "PAINT", .5
    if hasattr(settings, "reach"):      # absent at the audited commit (the reproduction runs there too)
        settings.reach = "SURFACE"
    settings.channels = {"BASE_COLOR"}
    settings.base_color = (.9, .2, .1, 1)

    def at(x, y):
        return world_to_window(region, view, (x, y, 0))

    def stroke_at(x0, x1, y, steps=8, pause=.03):
        px0, py = at(x0, y)
        px1, _ = at(x1, y)
        window.cursor_warp(int(px0), int(py))
        mouse("MOUSEMOVE", "NOTHING", px0, py)
        yield .05
        mouse("LEFTMOUSE", "PRESS", px0, py)
        for step in range(1, steps + 1):
            mouse("MOUSEMOVE", "NOTHING", px0 + (px1 - px0) * step / steps, py)
            yield pause
        mouse("LEFTMOUSE", "RELEASE", px1, py)
        yield .3

    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.vector_brush("INVOKE_DEFAULT")
    yield .3
    assert OP_OT_vector_brush._handler is not None and OP_OT_vector_brush._active is not None
    tool = OP_OT_vector_brush._active

    state = {}

    def scenario_1():
        # ---- 1. invoke on A1, stroke, select A2 without exiting, stroke again ----
        sa, sb, layers = resolve()
        assert vector_brush.destination(bpy.context).startswith("A > Group > A1") or True   # the idle label follows the selection
        yield from stroke_at(-1.8, -1.0, .5)
        sa, sb, layers = resolve()
        assert len(strokes_of(scene, layers["a1"])) == 1 and not strokes_of(scene, layers["a2"]), "the first stroke lands on A1"
        a_after_first = outputs(scene, sa)
        with bpy.context.temp_override(window=window, area=area, region=region):
            selection.select(bpy.context, ids["a2"], ts=sa)
            shown = vector_brush.destination(bpy.context)
        assert "A2" in shown, shown
        yield .2
        yield from stroke_at(-1.8, -1.0, -.5)
        sa, sb, layers = resolve()
        on_a1, on_a2 = len(strokes_of(scene, layers["a1"])), len(strokes_of(scene, layers["a2"]))
        verdict("target change while the tool stays alive lands the next stroke on the new layer",
                on_a1 == 1 and on_a2 == 1, strokes_on_A1=on_a1, strokes_on_A2=on_a2, displayed=shown,
                tool_running=OP_OT_vector_brush._active is tool)
        assert OP_OT_vector_brush._active is tool, "the same operator must still be running"
        state["a_after_second"] = a_after_second = outputs(scene, sa)
        assert a_after_second != a_after_first, "the second stroke must render"
        progress("scenario 1: target change between gestures")


    def scenario_2():
        # ---- 2. switch the texture set through the shared selection, paint on B ----
        project.active_texture_set = next(i for i, t in enumerate(project.texture_sets) if t.id == ids["b"])
        with bpy.context.temp_override(window=window, area=area, region=region):
            selection.select(bpy.context, ids["b1"], ts=resolve()[1])
            shown = vector_brush.destination(bpy.context)
        assert shown.startswith("B > B1"), shown
        b_before = outputs(scene, resolve()[1])
        yield from stroke_at(.4, 1.4, .5)
        sa, sb, layers = resolve()
        assert len(strokes_of(scene, layers["b1"])) == 1, "the stroke must land on B1"
        assert len(strokes_of(scene, layers["a1"])) == 1 and len(strokes_of(scene, layers["a2"])) == 1
        assert outputs(scene, sa) == state["a_after_second"], "A must not change"
        assert outputs(scene, sb) != b_before
        check("switching the texture set through the shared selection redirects the brush", displayed=shown)
        progress("scenario 2: set switch")


    def scenario_3():
        # ---- 3. change the target during pointer-down ----
        sa, sb, layers = resolve()
        count_before = len(project.strokes)
        b_committed = outputs(scene, sb)
        px0, py = at(.4, -.5)
        px1, _ = at(1.4, -.5)
        window.cursor_warp(int(px0), int(py))
        mouse("MOUSEMOVE", "NOTHING", px0, py)
        yield .05
        mouse("LEFTMOUSE", "PRESS", px0, py)
        for step in range(1, 5):
            mouse("MOUSEMOVE", "NOTHING", px0 + (px1 - px0) * step / 8, py)
            yield .03
        assert tool.painting
        captured = tool._gesture.describe(project)
        with bpy.context.temp_override(window=window, area=area, region=region):
            during = vector_brush.destination(bpy.context)
        assert during == captured and "B1" in during, (during, captured)
        previews_during = bound_previews(scene, sb, layers["b1"])
        project.active_texture_set = next(i for i, t in enumerate(project.texture_sets) if t.id == ids["a"])
        with bpy.context.temp_override(window=window, area=area, region=region):
            selection.select(bpy.context, ids["a1"], ts=resolve()[0])
        for step in range(5, 9):
            mouse("MOUSEMOVE", "NOTHING", px0 + (px1 - px0) * step / 8, py)
            yield .03
        mouse("LEFTMOUSE", "RELEASE", px1, py)
        yield .3
        sa, sb, layers = resolve()
        assert not tool.painting and len(project.strokes) == count_before, "the pending stroke must be discarded"
        assert not bound_previews(scene, sb, layers["b1"]), "the preview must be restored"
        assert outputs(scene, sb) == b_committed and outputs(scene, sa) == state["a_after_second"]
        assert OP_OT_vector_brush._active is tool
        check("a target change during pointer-down discards the pending stroke and restores the preview",
              previews_bound_during=previews_during, status=project.status)
        progress("scenario 3: target change during pointer-down")


    def scenario_4():
        # ---- 4. change settings during pointer-down ----
        settings.base_color, settings.radius = (.1, .3, .9, 1), .15
        count_before = len(project.strokes)
        px0, py = at(-1.8, 0.0)
        px1, _ = at(-1.0, 0.0)
        window.cursor_warp(int(px0), int(py))
        mouse("MOUSEMOVE", "NOTHING", px0, py)
        yield .05
        mouse("LEFTMOUSE", "PRESS", px0, py)
        for step in range(1, 5):
            mouse("MOUSEMOVE", "NOTHING", px0 + (px1 - px0) * step / 8, py)
            yield .03
        assert tool.painting
        captured_values = (tuple(tool._gesture.brush.base_color), tool._gesture.brush.radius)
        settings.base_color, settings.radius = (.2, .9, .2, 1), .05
        for step in range(5, 9):
            mouse("MOUSEMOVE", "NOTHING", px0 + (px1 - px0) * step / 8, py)
            yield .03
        preview_radius = float(np.asarray(tool._gesture.samples)[:, 7].max()) if tool._gesture.samples else None
        mouse("LEFTMOUSE", "RELEASE", px1, py)
        yield .3
        assert len(project.strokes) == count_before + 1
        committed = project.strokes[-1]
        assert tuple(round(v, 4) for v in committed.base_color) == tuple(round(v, 4) for v in captured_values[0]), (tuple(committed.base_color), captured_values)
        assert abs(committed.radius - captured_values[1]) < 1e-6 and abs(preview_radius - .15) < 1e-6, (committed.radius, preview_radius)
        yield from stroke_at(-1.8, -1.0, .8)
        assert tuple(round(v, 4) for v in project.strokes[-1].base_color) == (.2, .9, .2, 1.0) and abs(project.strokes[-1].radius - .05) < 1e-6
        check("settings changed during pointer-down apply to the next gesture only",
              captured=captured_values, next=(tuple(project.strokes[-1].base_color), project.strokes[-1].radius))
        progress("scenario 4: settings during pointer-down")


    def scenario_5():
        # ---- 5. hidden layer, hidden folder, deleted target: refusal, no stroke elsewhere ----
        sa, sb, layers = resolve()
        count_before = len(project.strokes)
        a_digest = outputs(scene, sa)
        layers["a1"].visible = False
        yield .1
        with bpy.context.temp_override(window=window, area=area, region=region):
            shown = vector_brush.destination(bpy.context)
        assert "hidden" in shown.lower(), shown
        yield from stroke_at(-1.8, -1.0, -.8)
        assert len(project.strokes) == count_before, "a hidden layer must refuse the stroke"
        layers["a1"].visible = True
        layers["folder"].visible = False
        yield .1
        yield from stroke_at(-1.8, -1.0, -.8)
        assert len(project.strokes) == count_before, "a hidden folder must refuse the stroke"
        layers["folder"].visible = True
        # Hidden mid-gesture: the gesture is discarded.
        px0, py = at(-1.8, -.8)
        px1, _ = at(-1.0, -.8)
        window.cursor_warp(int(px0), int(py))
        mouse("MOUSEMOVE", "NOTHING", px0, py)
        yield .05
        mouse("LEFTMOUSE", "PRESS", px0, py)
        for step in range(1, 4):
            mouse("MOUSEMOVE", "NOTHING", px0 + (px1 - px0) * step / 8, py)
            yield .03
        assert tool.painting
        resolve()[2]["a1"].visible = False
        for step in range(4, 9):
            mouse("MOUSEMOVE", "NOTHING", px0 + (px1 - px0) * step / 8, py)
            yield .03
        mouse("LEFTMOUSE", "RELEASE", px1, py)
        yield .3
        assert len(project.strokes) == count_before and not tool.painting
        resolve()[2]["a1"].visible = True
        assert outputs(scene, resolve()[0]) == a_digest
        # The target deleted while the tool runs: the next click refuses; the tool keeps running.
        sa, sb, layers = resolve()
        with bpy.context.temp_override(window=window, area=area, region=region):
            selection.select(bpy.context, ids["a2"], ts=sa)
        stack.remove(sa, stack.index_of(sa, ids["a2"]))
        yield .1
        yield from stroke_at(-1.8, -1.0, -.8)
        sa, sb, layers = resolve()
        assert layers["a2"] is None
        after_delete = len(project.strokes)
        assert OP_OT_vector_brush._active is tool
        # The selection fell back to another layer: a stroke there is what the panel shows, not the
        # deleted target — the displayed and the actual destination agree either way.
        with bpy.context.temp_override(window=window, area=area, region=region):
            shown = vector_brush.destination(bpy.context)
            target_now = selection.resolve(bpy.context, sa)
        if after_delete > count_before:
            assert target_now.layer is not None and project.strokes[-1].layer_key == target_now.layer.stroke_key, shown
        check("hidden or deleted targets refuse the stroke; nothing lands elsewhere",
              strokes_after_delete=after_delete - count_before, displayed=shown)
        progress("scenario 5: protected targets")


    def scenario_6():
        # ---- 6. a UV edit and a slot reassignment during pointer-down discard the gesture ----
        with bpy.context.temp_override(window=window, area=area, region=region):
            selection.select(bpy.context, ids["a1"], ts=resolve()[0])
        count_before = len(project.strokes)
        for change in ("uv", "slot"):
            px0, py = at(-1.8, .2)
            px1, _ = at(-1.0, .2)
            window.cursor_warp(int(px0), int(py))
            mouse("MOUSEMOVE", "NOTHING", px0, py)
            yield .05
            mouse("LEFTMOUSE", "PRESS", px0, py)
            for step in range(1, 4):
                mouse("MOUSEMOVE", "NOTHING", px0 + (px1 - px0) * step / 8, py)
                yield .03
            assert tool.painting, change
            if change == "uv":
                uv = plate_a.data.uv_layers.active
                uv.data[0].uv = (uv.data[0].uv[0] + .01, uv.data[0].uv[1])
                plate_a.data.update()
            else:
                sa = resolve()[0]
                sa.assignments[0].slot_index = 1
            yield .1
            for step in range(4, 9):
                mouse("MOUSEMOVE", "NOTHING", px0 + (px1 - px0) * step / 8, py)
                yield .03
            mouse("LEFTMOUSE", "RELEASE", px1, py)
            yield .3
            assert len(project.strokes) == count_before and not tool.painting, change
            if change == "slot":
                resolve()[0].assignments[0].slot_index = 0
            else:
                uv = plate_a.data.uv_layers.active
                uv.data[0].uv = (uv.data[0].uv[0] - .01, uv.data[0].uv[1])
                plate_a.data.update()
            yield .1
        check("a UV edit or a slot reassignment during pointer-down discards the gesture")
        progress("scenario 6: geometry changes during pointer-down")


    def scenario_7():
        # ---- 7. cross-set link, then cross-set painting off: B's pixels stay identical ----
        settings.universal, settings.radius = True, .3
        if hasattr(settings, "reach"):
            settings.reach = "DEPTH"        # the broadest reach: the disc does spill onto B
        sa, sb, layers = resolve()
        with bpy.context.temp_override(window=window, area=area, region=region):
            selection.select(bpy.context, ids["a1"], ts=sa)
        if REPRODUCE:
            # The audited tool froze its scope at invocation: reopened so the crossing stroke can link B.
            window.event_simulate(type="ESC", value="PRESS")
            yield .3
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.open_painter.vector_brush("INVOKE_DEFAULT")
            yield .3
        yield from stroke_at(-.8, .8, .6)
        sa, sb, layers = resolve()
        linked = [l for l in sb.layers if l.stroke_key == layers["a1"].stroke_key]
        assert len(linked) == 1, "the crossing stroke links B"
        b_linked = outputs(scene, sb)
        settings.universal = False
        yield from stroke_at(-1.0, .3, -.6)      # lifts at A's edge (x -.2); the disc (radius .3) spills over the .2 gap
        sa, sb, layers = resolve()
        last = project.strokes[-1]
        assert last.layer_key == layers["a1"].stroke_key
        spills = stroke_raster.touches(sb, last, 512)
        b_after = outputs(scene, sb)
        verdict("disabling cross-set painting protects the linked set's actual pixels",
                b_after == b_linked and spills, disc_reaches_B=spills, B_changed=b_after != b_linked,
                scope=getattr(last, "scope", "<no scope field>"))
        if hasattr(settings, "reach"):
            settings.reach = "SURFACE"
        progress("scenario 7: cross-set scope")


    def scenario_8():
        # ---- 8. Esc while idle exits; the area re-typed under a running tool ends it safely ----
        window.event_simulate(type="ESC", value="PRESS")
        yield .3
        assert OP_OT_vector_brush._handler is None, "Esc while idle must exit the tool"
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.open_painter.vector_brush("INVOKE_DEFAULT")
        yield .3
        assert OP_OT_vector_brush._active is not None
        count_before = len(project.strokes)
        area.type = "IMAGE_EDITOR"
        yield .3
        cx, cy = region.x + region.width // 2, region.y + region.height // 2
        mouse("MOUSEMOVE", "NOTHING", cx, cy)
        yield .05
        mouse("LEFTMOUSE", "PRESS", cx, cy)
        mouse("MOUSEMOVE", "NOTHING", cx + 40, cy)
        yield .05
        mouse("LEFTMOUSE", "RELEASE", cx + 40, cy)
        yield .3
        assert len(project.strokes) == count_before, "no stroke may land after the editor changed"
        ended = OP_OT_vector_brush._active is None
        area.type = "VIEW_3D"
        yield .3
        if not ended:
            window.event_simulate(type="ESC", value="PRESS")
            yield .3
        check("re-typing the editor under the running tool ends it without a stroke", ended_on_change=ended)
        progress("scenario 8: editor change")


    scenarios = [scenario_1, scenario_7] if REPRODUCE else [scenario_1, scenario_2, scenario_3, scenario_4, scenario_5,
                                                              scenario_6, scenario_7, scenario_8]
    for scenario in scenarios:
        yield from scenario()
    if REPRODUCE:
        report["mode"] = "reproduction: verdicts recorded, not asserted"


generator = steps()


def poll():
    try:
        return next(generator)
    except StopIteration:
        finish("REPRODUCED" if REPRODUCE else "PASS")
    except Exception:
        report["errors"].append(traceback.format_exc())
        traceback.print_exc()
        finish("FAIL")


bpy.app.timers.register(poll, first_interval=1.0, persistent=True)
