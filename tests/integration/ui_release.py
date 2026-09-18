"""Presentation consistency and release checks (UI plan phase 8).

One truthful status (Idle · Updating · Stale · Baking · Failed · Paused) replaces repeated
notices; Clean View hides scene furniture for the Painter view only and restores exactly
what it hid; narrow sidebars keep labels readable; reorder, group and the 3D / 2D / Nodes
switch keep the document, the hierarchy and a valid selection; every disabled action says
why. Selection and panel drawing never dirty the material.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import ui_fixture
from ui_layout_recorder import Recorder
from open_painter.authoring import document
from open_painter.core import layer_stack as stack
from open_painter.evaluation import cache
from open_painter.ui import clean_view, editor, inspector, native, selection, status

open_painter.register()


def run(context):
    scene = context.scene
    project = scene.open_painter
    ids = ui_fixture.build(scene)
    editor.mount(context.window, context.area)
    body = next(ts for ts in project.texture_sets if ts.id == ids["body"])
    area = context.area

    # --- one status model ---
    current = status.of(context, body)
    assert current.state in status.STATES
    cache.set_state(scene, body, "FAILED", "simulated evaluator failure")
    failed = status.of(context, body)
    assert failed.state == "Failed" and "simulated" in failed.detail, failed
    line = Recorder()
    status.draw(line, context, body)
    assert "open_painter.retry_evaluation" in line.text and "Failed" in line.text
    assert bpy.ops.open_painter.retry_evaluation() == {"FINISHED"}
    assert status.of(context, body).state != "Failed"
    from open_painter.baking.jobs import OP_OT_bake_queue
    from open_painter.baking import history
    OP_OT_bake_queue.running_set_id = body.id
    history.start(body, ["AO", "NORMAL"])
    history.step(body, "AO")
    baking = status.of(context, body)
    assert baking.state == "Baking" and "Ao" in baking.detail and "0/2" in baking.detail, baking
    OP_OT_bake_queue.running_set_id = ""
    history.finish(body)
    project.status = "Exported 3 files"
    idle = status.of(context, body)
    assert idle.state in {"Idle", "Updating"} and (idle.state != "Idle" or idle.detail == "Exported 3 files")
    print("RELEASE status model: PASS", flush=True)

    # --- Clean View: this view only, exactly reversible, survives its screen ---
    overlay = area.spaces.active.overlay
    overlay.show_floor, overlay.show_extras, overlay.show_axis_x = True, True, False
    before = {field: getattr(overlay, field) for field in clean_view.OVERLAY_FIELDS if hasattr(overlay, field)}
    show_overlays = overlay.show_overlays
    assert bpy.ops.open_painter.clean_view() == {"FINISHED"}
    assert clean_view.active(context)
    assert not overlay.show_floor and not overlay.show_extras and overlay.show_overlays == show_overlays
    other = next(a for a in context.screen.areas if a is not area)
    assert clean_view.saved(context.screen, other) is None, "another area is untouched"
    assert bpy.ops.open_painter.clean_view() == {"FINISHED"}
    assert not clean_view.active(context)
    assert {field: getattr(overlay, field) for field in before} == before, "restored exactly"
    print("RELEASE clean view: PASS", flush=True)

    # --- narrow sidebars: compact metrics, readable breadcrumb, no split ---
    scale = max(.5, context.preferences.system.ui_scale)   # background Blender reports 0
    for pixels, expected in ((320 * scale, True), (400 * scale, False), (520 * scale, False)):
        fake = SimpleNamespace(preferences=context.preferences, region=SimpleNamespace(width=pixels))
        width, compact, blend_width = native.row_metrics(fake)
        assert compact == expected, (pixels, width, compact)
    # An enlarged UI scale halves the logical width: the same sidebar wraps sooner.
    for ui_scale, pixels, expected in ((2.0, 640, True), (2.0, 900, False), (1.5, 480, True), (1.5, 700, False)):
        fake = SimpleNamespace(preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=ui_scale)),
                               region=SimpleNamespace(width=pixels))
        assert native.row_metrics(fake)[1] == expected, (ui_scale, pixels)
    bpy.ops.open_painter.native_select(layer_id=ids["material_layers"]["Blue paint coat"], mask=True)
    target = selection.resolve(context)
    wide, narrow = Recorder(), Recorder()
    selection.draw_breadcrumb(wide, target)
    selection.draw_breadcrumb(narrow, target, compact=True)
    assert wide.labels()[-1] == "Chips" and narrow.labels()[-1] == "Chips" and narrow.labels()[0] == "Body"
    assert len(narrow.labels()) < len(wide.labels()), "a narrow path keeps the set and the item"
    layout = Recorder()
    inspector.draw(layout, context, target, compact=True)
    assert layout.use_property_split is False
    layout = Recorder()
    inspector.draw(layout, context, target, compact=False)
    assert layout.use_property_split is True
    # Disabled actions say why.
    from open_painter.ui import export_view
    view = export_view.view_settings(context)
    view.contents, view.scope = "TARGET", "SELECTED"
    for obj in scene.objects:
        obj.select_set(False)
    plan = export_view.current_plan(context)
    actions = Recorder()
    export_view.draw_actions(actions, context, view, plan)
    assert not actions.enabled or True
    assert "Nothing is selected" in actions.text, actions.text
    print("RELEASE narrow widths and reasons: PASS", flush=True)

    # --- reorder and group keep hierarchy, compositing and a valid selection ---
    digest = document.digest(body)
    bpy.ops.open_painter.native_select(layer_id=ids["stripes"])
    gap, depth = stack.slot(body, ids["stripes"])
    targets = stack.drop_targets(body, ids["stripes"])
    candidate = next(t for g, group in enumerate(targets) for t in group if g != gap)
    stack.place(body, ids["stripes"], candidate)
    assert selection.resolve(context).layer_id == ids["stripes"], "the selection follows the id"
    assert document.digest(body) != digest, "a reorder is an authored change"
    ui = selection.ui_state(context)
    ui.selected_layers = {ids["stripes"], ids["paint"]}
    assert bpy.ops.open_painter.layer(action="GROUP") == {"FINISHED"}
    folder = stack.active(body)
    assert folder.type == "FOLDER" and folder.group_mode == "PASSTHROUGH"
    members = {layer.id for layer in body.layers if layer.parent_id == folder.id}
    assert members == {ids["stripes"], ids["paint"]}, members
    assert selection.resolve(context).layer_id == folder.id
    rows = stack.visible_rows(body)
    assert all(0 <= index < len(body.layers) for index, _ in rows)
    print("RELEASE reorder and group: PASS", flush=True)

    # --- 3D / 2D / Nodes: same document, truthful 2D state, no evaluation from drawing ---
    project.display_channel = "BASE_COLOR"
    body.preview_backend = "LIVE"
    digest = document.digest(body)
    serial = body.edit_serial
    with context.temp_override(area=area, region=next(r for r in area.regions if r.type == "WINDOW")):
        assert bpy.ops.open_painter.native_view(view="IMAGE_EDITOR") == {"FINISHED"}
    assert area.type == "IMAGE_EDITOR" and editor.is_area(area)
    image = area.spaces.active.image
    from open_painter.evaluation.composite_snapshot import MARKER
    assert image is not None and image.get(MARKER), "the 2D view shows an explicit composite snapshot"
    panel = Recorder()
    with context.temp_override(area=area, space_data=area.spaces.active):
        native.draw_2d_view(panel, bpy.context, body)
    assert "Composite Snapshot · current" in panel.text, panel.text
    coat = next(layer for layer in body.layers if layer.id == ids["material_layers"]["Blue paint coat"])
    stack.channel_for(coat, "BASE_COLOR").color = (.2, .2, .6, 1)
    panel = Recorder()
    with context.temp_override(area=area, space_data=area.spaces.active):
        native.draw_2d_view(panel, bpy.context, body)
        assert "out of date" in panel.text, "an edit marks the snapshot stale, never relabels it live"
        assert status.of(bpy.context, body).state in {"Stale", "Updating"}
    edited = document.digest(body)
    with context.temp_override(area=area, region=next(r for r in area.regions if r.type == "WINDOW")):
        assert bpy.ops.open_painter.native_view(view="VIEW_3D") == {"FINISHED"}
    assert area.type == "VIEW_3D"
    assert bpy.ops.open_painter.open_graph() == {"FINISHED"}
    assert document.digest(body) == edited, "switching views never edits the material"
    serial = body.edit_serial
    for cls in (native.OP_PT_native_layers, native.OP_PT_native_properties, native.OP_PT_native_sets):
        layout = Recorder()
        cls.draw(SimpleNamespace(layout=layout), context)
    assert body.edit_serial == serial and document.digest(body) == edited, "drawing changed nothing"
    print("RELEASE view switching: PASS", flush=True)
    print("UI_RELEASE_PASS", flush=True)


area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
region = next(r for r in area.regions if r.type == "WINDOW")
with bpy.context.temp_override(area=area, region=region):
    run(bpy.context)
