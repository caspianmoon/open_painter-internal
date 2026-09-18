"""Clean-install launch test (0.12.2, remedy plan priority 7): the packaged extension alone.

Run against a fresh Blender profile (BLENDER_USER_RESOURCES pointing at an empty folder) with
the built ZIP, never the source checkout: the script adds nothing to ``sys.path`` and reaches
the add-on only as ``bl_ext.user_default.open_painter``.

Phase 1 (OP_CLEAN_INSTALL_PHASE=1): install and enable the ZIP; open the Painter from the
editor; create a texture set; apply a built-in smart material; move its master controls; add
fill and paint layers; add and edit a mask; bake the maps the material needs; paint a real
stroke; save the .blend; quit.
Phase 2 (OP_CLEAN_INSTALL_PHASE=2, a new Blender process): reopen the file; verify the
project state; export Blender maps and Unreal maps; disable and re-enable the extension;
open the saved project again and verify once more.

    OP_CLEAN_INSTALL_ZIP=dist/open_painter-0.12.2.zip OP_CLEAN_INSTALL_PHASE=1 \
    BLENDER_USER_RESOURCES=/tmp/fresh blender --factory-startup --enable-event-simulate \
        --python tests/integration/clean_install_ui.py
"""
import importlib
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy
import addon_utils

PHASE = int(os.environ.get("OP_CLEAN_INSTALL_PHASE", "1"))
ZIP = Path(os.environ.get("OP_CLEAN_INSTALL_ZIP", ""))
WORK = Path(os.environ.get("OP_CLEAN_INSTALL_DIR", tempfile.mkdtemp(prefix="op-clean-install-")))
REPORT = WORK / f"clean-install-phase{PHASE}.json"
FILE = WORK / "launch.blend"
MODULE = "bl_ext.user_default.open_painter"
assert "open_painter" not in sys.modules, "the source checkout must not be importable in this test"
report = {"phase": PHASE, "steps": [], "errors": [], "blender": bpy.app.version_string,
          "profile": os.environ.get("BLENDER_USER_RESOURCES", "")}
env = {}
bpy.context.preferences.view.show_splash = False


def finish(status):
    REPORT.write_text(json.dumps(dict(status=status, **report), indent=2))
    print(f"CLEAN_INSTALL_PHASE{PHASE}_{status}", REPORT, flush=True)
    bpy.ops.wm.quit_blender()


def mod(name):
    return importlib.import_module(MODULE + "." + name)


def painter_area():
    window = bpy.context.window
    return window, max((a for a in window.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)


def override():
    window, area = painter_area()
    region = next(r for r in area.regions if r.type == "WINDOW")
    return bpy.context.temp_override(window=window, area=area, region=region)


def mouse(kind, value, x, y):
    bpy.context.window.event_simulate(type=kind, value=value, x=int(x), y=int(y))


def wait_current(ts, seconds=180):
    shader_preview = mod("evaluation.shader_preview")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not shader_preview.current(bpy.context.scene, ts):
        yield .2
    assert shader_preview.current(bpy.context.scene, ts), shader_preview.diagnostics(bpy.context.scene, ts)


def project_state():
    project = bpy.context.scene.open_painter
    ts = project.texture_sets[0]
    controls = mod("smart_materials.controls")
    folder = next(l for l in ts.layers if l.type == "FOLDER" and l.module_name)
    bindings = {b.control.label: b for b in controls.bindings(ts, folder)}
    return {"sets": [t.name for t in project.texture_sets], "layers": [(l.name, l.type) for l in ts.layers],
            "material": folder.module_name, "bindings_ok": all(b.ok for b in bindings.values()),
            "paint_color": [round(v, 3) for v in bindings["Paint Color"].value][:3],
            "chip_amount": round(bindings["Chip Amount"].value, 3),
            "masks": [(l.name, [m.source for m in l.masks]) for l in ts.layers if l.masks],
            "baked": sorted(m.kind for m in ts.baked_maps if m.image)}


def phase_one():
    assert ZIP.is_file(), f"package missing: {ZIP}"
    assert not addon_utils.check(MODULE)[1], "a fresh profile has no Open Painter"
    result = bpy.ops.extensions.package_install_files(filepath=str(ZIP), repo="user_default", enable_on_install=True)
    assert result == {"FINISHED"}, result
    assert addon_utils.check(MODULE)[1], "the extension is enabled"
    bpy.ops.wm.save_userpref()
    constants = mod("constants")
    report["version"] = constants.VERSION
    report["steps"].append(f"1 installed and enabled {ZIP.name} ({constants.VERSION})")
    yield .5
    # 2. Open Painter from an ordinary viewport (what the editor-type menu does).
    with override():
        bpy.ops.open_painter.open_editor()
    yield .5
    editor = mod("ui.editor")
    assert editor.is_area(painter_area()[1])
    report["steps"].append("2 Painter opened in the viewport area")
    # 3. A texture set on the default cube.
    cube = bpy.context.scene.objects["Cube"]
    with override():
        bpy.ops.object.select_all(action="DESELECT")
        cube.select_set(True)
        bpy.context.view_layer.objects.active = cube
        assert bpy.ops.open_painter.texture_set(action="ADD") == {"FINISHED"}
    project = bpy.context.scene.open_painter
    ts = project.texture_sets[0]
    ts.resolution = "512"
    assert any(r.object == cube for r in ts.assignments), "the selected mesh was assigned"
    report["steps"].append("3 texture set created and assigned")
    # 8 (early, the material needs its maps): a target-only bake of the recommended maps.
    ts.bake.selected_to_active = False
    ts.bake.normal = ts.bake.ao = ts.bake.curvature = True
    bake_manager = mod("baking.bake_manager")
    with override():
        bake_manager.bake_selected(bpy.context, ts)
    baked = sorted(m.kind for m in ts.baked_maps if m.image)
    assert {"NORMAL", "AO", "CURVATURE"} <= set(baked), baked
    report["steps"].append(f"8 baked {baked}")
    # 4. A built-in smart material through the catalog operator.
    with override():
        assert bpy.ops.open_painter.catalog_material(choice="Worn Painted Metal") == {"FINISHED"}
    ts = project.texture_sets[0]
    folder = next(l for l in ts.layers if l.type == "FOLDER" and l.module_name == "Worn Painted Metal")
    report["steps"].append("4 Worn Painted Metal applied")
    # 5. Master controls, through the folder's bindings and vector.
    controls = mod("smart_materials.controls")
    bindings = {b.control.label: b for b in controls.bindings(ts, folder)}
    assert all(b.ok for b in bindings.values()), [(k, b.reason) for k, b in bindings.items() if not b.ok]
    bindings["Paint Color"].owner.color = (.6, .1, .1, 1)
    bindings["Chip Amount"].owner.amount = .5
    vector = list(folder.master_factor)
    vector[bindings["Dirt Amount"].index] = 0.0
    folder.master_factor = vector
    assert bindings["Dirt Amount"].members[0][0].opacity == 0
    report["steps"].append("5 master controls moved (colour, chips, dirt to none)")
    # 6. Fill and paint layers above the material.
    ts.active_layer = len(ts.layers) - 1
    with override():
        assert bpy.ops.open_painter.layer(action="FILL") == {"FINISHED"}
        assert bpy.ops.open_painter.layer(action="PAINT") == {"FINISHED"}
    ts = project.texture_sets[0]
    assert [l.type for l in ts.layers[-2:]] == ["FILL", "PAINT"], [l.type for l in ts.layers]
    report["steps"].append("6 fill and paint layers added")
    # 7. A generator mask on the fill layer, then edited.
    ts.active_layer = len(ts.layers) - 2
    with override():
        assert bpy.ops.open_painter.mask(action="ADD", source="GENERATOR") == {"FINISHED"}
    fill = project.texture_sets[0].layers[-2]
    assert fill.masks and fill.masks[0].source == "GENERATOR"
    fill.masks[0].generator = "EDGE_WEAR"
    fill.masks[0].amount = .4
    report["steps"].append("7 mask added and edited")
    # 9. A real brush stroke on the paint layer, seen from above.
    ts = project.texture_sets[0]
    paint_id = ts.layers[-1].id
    stack = mod("core.layer_stack")
    ts.active_layer = stack.index_of(ts, paint_id)
    project.paint_channel = "BASE_COLOR"
    window, area = painter_area()
    env["window"] = window
    region = next(r for r in area.regions if r.type == "WINDOW")
    area.spaces.active.shading.type = "MATERIAL"
    with override():
        bpy.ops.view3d.view_axis(type="TOP")
        bpy.ops.view3d.view_selected(use_all_regions=False)
    yield .5
    cx, cy = region.x + region.width // 2, region.y + region.height // 2
    window.cursor_warp(cx, cy)
    mouse("LEFTMOUSE", "PRESS", cx, cy)
    mouse("LEFTMOUSE", "RELEASE", cx, cy)
    yield .3
    with override():
        bpy.ops.open_painter.paint()
    yield .5
    assert project.paint_image is not None, "painting started"
    canvas_name = project.paint_image.name
    brush = bpy.context.scene.tool_settings.image_paint.brush
    unified = bpy.context.scene.tool_settings.image_paint.unified_paint_settings
    for owner in (brush, unified):
        owner.strength, owner.size = 1, 80
    x0, x1 = region.x + int(region.width * .35), region.x + int(region.width * .65)
    window.cursor_warp(x0, cy)
    mouse("MOUSEMOVE", "NOTHING", x0, cy)
    yield .2
    mouse("LEFTMOUSE", "PRESS", x0, cy)
    for step in range(1, 25):
        mouse("MOUSEMOVE", "NOTHING", x0 + (x1 - x0) * step / 24, cy + 5 * ((-1) ** step))
        yield .033
    mouse("LEFTMOUSE", "RELEASE", x1, cy)
    yield .8
    images = mod("core.image_manager")
    alpha = float(images.pixels(bpy.data.images[canvas_name])[..., 3].max())
    assert alpha > .5, f"the stroke must paint the canvas (alpha max {alpha})"
    with override():
        bpy.ops.open_painter.paint(stop=True)
    yield .3
    report["steps"].append("9 painted a stroke and finished")
    yield from wait_current(project.texture_sets[0])
    # 9b. Paint Material (0.13): a four-channel gesture through the modal brush, with the
    # material changing under the pointer, one stroke record on release.
    settings = project.vector_brush
    settings.radius_mode, settings.radius, settings.channels = "WORLD", .1, {"BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT"}
    settings.base_color, settings.roughness = (.2, .5, .8, 1), .3
    strokes_before = len(project.strokes)
    with override():
        bpy.ops.open_painter.vector_brush("INVOKE_DEFAULT")
    yield .3
    brush_module = mod("ui.vector_brush")
    assert brush_module.OP_OT_vector_brush._handler is not None, "the material brush did not start"
    window.cursor_warp(x0, cy - 60)
    mouse("MOUSEMOVE", "NOTHING", x0, cy - 60)
    yield .2
    mouse("LEFTMOUSE", "PRESS", x0, cy - 60)
    previews_seen = False
    for step in range(1, 13):
        mouse("MOUSEMOVE", "NOTHING", x0 + (x1 - x0) * step / 12, cy - 60)
        yield .033
        active = brush_module.OP_OT_vector_brush._active
        if active is not None and active._gesture.preview is not None and active._gesture.preview.active:
            previews_seen = True
    mouse("LEFTMOUSE", "RELEASE", x1, cy - 60)
    yield .3
    project = bpy.context.scene.open_painter
    assert len(project.strokes) == strokes_before + 1, (len(project.strokes), strokes_before)
    assert previews_seen, "the material must change under the pointer (live preview)"
    window.event_simulate(type="ESC", value="PRESS")
    yield .3
    assert brush_module.OP_OT_vector_brush._handler is None, "Esc while idle exits the tool"
    report["steps"].append("9b painted a material stroke with live feedback")
    yield from wait_current(project.texture_sets[0])
    # 10. Save.
    report["state_before_save"] = project_state()
    bpy.ops.wm.save_as_mainfile(filepath=str(FILE))
    assert FILE.is_file()
    report["steps"].append(f"10 saved {FILE.name}")
    # 11. Quit: finish() closes Blender.


def phase_two():
    assert addon_utils.check(MODULE)[1], "the extension stays enabled in the saved preferences"
    report["version"] = mod("constants").VERSION
    # 12–13. Reopen and verify.
    bpy.ops.wm.open_mainfile(filepath=str(FILE))
    yield 1.0
    state = project_state()
    report["state_after_reopen"] = state
    assert state["material"] == "Worn Painted Metal" and state["bindings_ok"], state
    assert state["paint_color"] == [.6, .1, .1] and state["chip_amount"] == .5, state
    assert {"NORMAL", "AO", "CURVATURE"} <= set(state["baked"]), state["baked"]
    assert any(t == "PAINT" for _, t in state["layers"]) and any(t == "FILL" for _, t in state["layers"])
    assert len(bpy.context.scene.open_painter.strokes) == 1, "the material stroke must survive save and reopen"
    report["steps"].append("13 project state verified after reopening (material stroke included)")
    with override():
        bpy.ops.open_painter.open_editor()
    yield .5
    # 14–15. Export for Blender, then for Unreal, through the Export tab's plan.
    project = bpy.context.scene.open_painter
    export_view = mod("ui.export_view")
    view = export_view.view_settings(bpy.context)
    view.contents, view.scope, view.folder_source = "TARGET", "ACTIVE", "PROJECT"
    for target in ("BLENDER", "UNREAL"):
        project.target = target
        project.export_directory = str(WORK / f"export-{target.lower()}")
        with override():
            plan = export_view.current_plan(bpy.context)
            assert bpy.ops.open_painter.export_plan() == {"FINISHED"}, project.status
        written = sorted(p.name for p in Path(plan.directory).iterdir())
        listed = sorted(p.name for p in plan.files)
        assert written == listed, (target, written, listed)
        report[f"export_{target.lower()}"] = written
        padding_line = project.texture_sets[0].export.last_report
        assert padding_line.startswith("UV padding: ") and "px" in padding_line, padding_line
        report[f"padding_{target.lower()}"] = padding_line
        report["steps"].append(f"{'14' if target == 'BLENDER' else '15'} exported {len(written)} files for {target} · {padding_line}")
    # 16. Disable and re-enable the extension.
    addon_utils.disable(MODULE, default_set=True)
    assert not hasattr(bpy.types.Scene, "open_painter")
    addon_utils.enable(MODULE, default_set=True)
    assert hasattr(bpy.types.Scene, "open_painter")
    report["steps"].append("16 extension disabled and re-enabled")
    yield .5
    # 17. The saved project once more, with the re-enabled extension.
    bpy.ops.wm.open_mainfile(filepath=str(FILE))
    yield 1.0
    again = project_state()
    assert again["material"] == "Worn Painted Metal" and again["bindings_ok"] and again["paint_color"] == [.6, .1, .1], again
    report["steps"].append("17 project opened again after the re-enable")
    bpy.ops.screen.screenshot(filepath=str(WORK / "clean-install.png"))
    # 18. An upgrade session: a file saved by 0.12.2 (built by tests/integration/compatibility_contract.py
    # --build with the 0.12.2 source) opens with the installed 0.13 extension; the migration to
    # automatic export padding is visible, and its strokes and export still work.
    legacy = WORK / "compat-0.12.2.blend"
    if legacy.is_file():
        bpy.ops.wm.open_mainfile(filepath=str(legacy))
        yield 1.0
        project = bpy.context.scene.open_painter
        assert project.format_version >= 3, project.format_version
        assert all(ts.export.padding_migrated for ts in project.texture_sets), "the upgrade must be visible"
        assert len(project.strokes) >= 1 and all(s.sample_count > 0 for s in project.strokes)
        with override():
            bpy.ops.open_painter.open_editor()
        yield .5
        view = export_view.view_settings(bpy.context)
        view.contents, view.scope, view.folder_source = "TARGET", "ALL", "PROJECT"
        project.target = "UNREAL"
        project.export_directory = str(WORK / "export-upgrade")
        with override():
            plan = export_view.current_plan(bpy.context)
            assert bpy.ops.open_painter.export_plan() == {"FINISHED"}, project.status
        written = sorted(p.name for p in Path(plan.directory).iterdir())
        assert written == sorted(p.name for p in plan.files), written
        report["upgrade"] = {"files": written, "padding": [ts.export.last_report for ts in project.texture_sets]}
        report["steps"].append(f"18 a 0.12.2 file opened, migrated visibly and exported {len(written)} files")
    else:
        report["upgrade"] = "SKIPPED: no 0.12.2 file in the work folder"
        report["steps"].append("18 upgrade session SKIPPED (no 0.12.2 file)")


generator = phase_one() if PHASE == 1 else phase_two()


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
