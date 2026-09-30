"""The focused bake view (UI plan phase 4): Setup, Maps and Results sections, truthful map
states, a recommendation that never overwrites the selection, previous maps kept when a
rebake fails, and the queue bound to the set it started on.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import ui_fixture
from ui_layout_recorder import Recorder
from open_painter.baking import bake_manager, history
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.ui import bake_view, editor

open_painter.register()


def sections(context, ts):
    setup, maps, results = Recorder(), Recorder(), Recorder()
    bake_view.draw_setup(setup, context, ts)
    bake_view.draw_maps(maps, context, ts)
    bake_view.draw_results(results, context, ts)
    return setup, maps, results


def states(context, ts):
    return {kind: bake_view.map_state(ts, kind)[0] for _key, kind, *_ in bake_view.MAPS}


def run(context):
    scene = context.scene
    cube = scene.objects["Cube"]
    ts = sets.create(scene, "Body", 512)
    sets.assign(scene, ts, cube)
    settings = ts.bake
    editor.mount(context.window, context.area)

    # --- setup hides what does not apply ---
    settings.selected_to_active = False
    settings.normal = settings.ao = settings.curvature = False
    setup, maps, results = sections(context, ts)
    text = setup.text
    assert "Targets: Cube" in text and "Model size 2.00 × 2.00 × 2.00 m" in text, setup.labels()
    assert "painter_bake_sources" not in text and "ray_distance" not in text, "target-only bakes show no sources or cage"
    assert "painter_bake_cycles" not in text, "no Cycles sampling without a Cycles map"
    assert "targets alone" in text
    settings.curvature = True
    assert bake_view.uses_cycles(settings), "the Curvature edge set is a Cycles Bevel bake: its sampling shows"
    settings.curvature = False
    settings.selected_to_active = True
    settings.normal = True
    setup = Recorder()
    bake_view.draw_setup(setup, context, ts)
    text = setup.text
    assert "painter_bake_sources" in text and "ray_distance" in text and "Ray Distance (model 2.00 m)" in text
    assert "painter_bake_cycles" in text and "samples" in text
    assert "Create Editable Cages" in text
    print("BAKE_VIEW setup: PASS", flush=True)

    # --- maps: full names, methods, states, settings behind each row ---
    text = maps.text
    for name in ("Ambient Occlusion", "Curvature (edge set)", "Mean Curvature (signed)", "Bevel Normal", "Thickness"):
        assert name in text, name
    assert "Cycles Bevel node, four reaches" in text and "Evaluated target, radius" in text, "curvature methods stay distinct"
    assert "mean_curvature_distance" in text and "ao_distance" in text and "thickness_samples" in text
    assert states(context, ts) == {kind: "Missing" for kind in states(context, ts)}
    assert "No layer reads a mesh map yet" in text
    # A generator makes a recommendation; selecting it keeps what was ticked.
    layer = stack.add(ts, "FILL", "Dirt")
    mask = layer.masks.add()
    mask.id, mask.source, mask.generator = "m-dirt", "GENERATOR", "CAVITY_DIRT"
    assert bake_view.recommended(ts) == ["AO"]
    settings.normal, settings.ao, settings.curvature = True, False, False
    assert bpy.ops.open_painter.bake(action="SELECT_RECOMMENDED") == {"FINISHED"}
    assert settings.ao and settings.normal and not settings.curvature, "recommendation adds, never overwrites"
    maps = Recorder()
    bake_view.draw_maps(maps, context, ts)
    assert "The material reads: Ao" in maps.text
    print("BAKE_VIEW maps: PASS", flush=True)

    # --- states: not checked without provenance, up to date after a bake, out of date after a UV edit ---
    ui_fixture.bake_synthetic(ts)          # images without provenance records
    assert states(context, ts)["AO"] == "Not checked" and states(context, ts)["CURVATURE"] == "Not checked"
    settings.normal = settings.ao = settings.curvature = False
    settings.position = True
    bake_manager.bake_selected(context, ts)
    assert states(context, ts)["POSITION"] == "Up to date", states(context, ts)
    record = history.last(ts)
    assert record and record["done"] == ["POSITION"] and not record["failed"] and not record["error"]
    results = Recorder()
    bake_view.draw_results(results, context, ts)
    assert "landed: Position" in results.text and "Add Baked Normal and AO Layer" in results.text
    uv = cube.data.uv_layers.active.data
    uv[0].uv = (uv[0].uv[0] + .05, uv[0].uv[1])
    cube.data.update()
    context.view_layer.update()    # the depsgraph update an artist's edit produces
    assert states(context, ts)["POSITION"] == "Out of date", states(context, ts)
    print("BAKE_VIEW states: PASS", flush=True)

    # --- a failed rebake keeps the previous map; the failure is reported per map ---
    previous = next(m for m in ts.baked_maps if m.kind == "POSITION").image
    previous_pixels = images.pixels(previous).copy()
    original = bake_manager.bake_utility

    def failing(ts_, kind):
        raise RuntimeError("simulated ray failure")

    bake_manager.bake_utility = failing
    try:
        try:
            bake_manager.bake_selected(context, ts)
            raise AssertionError("the simulated failure did not surface")
        except RuntimeError as exc:
            assert "simulated" in str(exc)
    finally:
        bake_manager.bake_utility = original
    prop = next(m for m in ts.baked_maps if m.kind == "POSITION")
    assert prop.image == previous, "the previous map survives a failed rebake"
    np.testing.assert_array_equal(images.pixels(prop.image), previous_pixels)
    assert states(context, ts)["POSITION"] == "Failed"
    results = Recorder()
    bake_view.draw_results(results, context, ts)
    assert "Position failed: simulated ray failure" in results.text and "previous map of that kind is kept" in results.text
    bake_manager.bake_selected(context, ts)
    assert states(context, ts)["POSITION"] == "Up to date", "a later success clears the failure"
    print("BAKE_VIEW failed rebake keeps the previous map: PASS", flush=True)

    # --- the queue captures its set; stop is only offered while it runs ---
    from open_painter.baking.jobs import OP_OT_bake_queue, OP_OT_bake_stop
    assert not OP_OT_bake_stop.poll(context) and OP_OT_bake_queue.running_set_id == ""
    queue = SimpleNamespace(_set_id=ts.id, _set_pointer=ts.as_pointer())
    assert OP_OT_bake_queue._set(queue, context) is not None
    other = sets.create(scene, "Other", 512)      # the collection may move: the queue notices
    resolved = OP_OT_bake_queue._set(queue, context)
    assert resolved is None or resolved.id == ts.id
    scene.open_painter.active_texture_set = 0
    print("BAKE_VIEW queue target: PASS", flush=True)

    # --- the sections draw as panels and the compact popover; the header opens the tab ---
    for cls in (bake_view.OP_PT_bake_setup, bake_view.OP_PT_bake_maps, bake_view.OP_PT_bake_results):
        layout = Recorder()
        cls.draw(SimpleNamespace(layout=layout), context)
        assert layout.items, cls.__name__
    setup = Recorder()
    bake_view.OP_PT_bake_setup.draw(SimpleNamespace(layout=setup), context)
    assert "Back to Painting" in setup.text and "open_painter.bake_queue" in setup.text
    compact = Recorder()
    bake_view.draw_compact(compact, context, ts)
    assert "painter_bake_compact_setup" in compact.text and "painter_bake_compact_results" in compact.text
    result = bpy.ops.open_painter.show_task(task="BAKE")
    assert result == {"FINISHED"}, result
    print("BAKE_VIEW_PASS", flush=True)


area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
region = next(r for r in area.regions if r.type == "WINDOW")
with bpy.context.temp_override(area=area, region=region):
    run(bpy.context)
