"""Aging, geometry effects and analysis in context (UI plan phase 6).

The aging panel starts with Applied To, Material Construction, Environment and Age in
Years, keeps every control in named sections, marks a modified preset, separates Reset
Environment from Reset Aging Setup, and names its live toggle. The Geometry Effects panel
lists the helpers applied to the set with a selection-specific inspector that says what
each effect produces. The offline analysis actions sit where their inputs are and stay
callable; the old panel is no longer registered.
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
from open_painter.aging import engine
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.geometry_effects import manager
from open_painter.ui import aging_panel, editor, geometry_effects, offline_analysis, performance, properties_panel

open_painter.register()


def run(context):
    scene = context.scene
    cube = scene.objects["Cube"]
    ts = sets.create(scene, "Body", 512)
    sets.assign(scene, ts, cube)
    editor.mount(context.window, context.area)
    aging = ts.aging

    # --- aging: the first controls, sections, modified presets, separate resets ---
    layout = Recorder()
    aging_panel.draw_aging(layout, context, ts)
    text = layout.text
    assert "Applied to Cube" in text and "No aging folder yet" in text
    assert text.index("Applied to") < text.index("painter_aging_construction") < text.index("environment") < text.index("Age in Years")
    assert "Follow Edits Live" in text and "PLAY" not in text, "the live toggle is named, not a play icon"
    for section in ("painter_aging_exposure", "painter_aging_use", "painter_aging_care", "painter_aging_events",
                    "painter_aging_prediction", "painter_aging_art"):
        assert section in text, section
    assert "Exposure (relative intensities)" in text and "no physical units" in text
    assert "not a measurement" in text and "Art Direction (after the simulation)" in text
    assert "Reset Aging Setup" in text and "Reset Art Direction" in text and "Reset Environment" not in text
    assert "Bake Mesh Maps first" in text
    assert "degrees" not in text.lower()
    assert "not degrees" in ts.aging.bl_rna.properties["temperature"].description
    assert "relative intensity" in ts.aging.bl_rna.properties["humidity"].description
    # A moved slider marks the preset as modified from its base; Reset Environment puts it back.
    aging.environment = "OUTDOOR_URBAN"
    preset_uv = aging.uv
    aging.uv = min(1.0, preset_uv + .3)
    assert aging.environment == "CUSTOM" and aging.environment_base == "OUTDOOR_URBAN"
    layout = Recorder()
    aging_panel.draw_aging(layout, context, ts)
    assert "Custom · modified from Outdoor Urban" in layout.text and "Reset Environment" in layout.text
    assert bpy.ops.open_painter.aging(action="RESET_ENVIRONMENT") == {"FINISHED"}
    assert aging.environment == "OUTDOOR_URBAN" and abs(aging.uv - preset_uv) < 1e-6
    # Reset Aging Setup: age, history, care, art direction back; the construction stays.
    aging.material = "OAK"
    aging.age_years, aging.direct_dirt = 33, 2.5
    aging.events.add().year = 3
    assert bpy.ops.open_painter.aging(action="RESET_SETUP") == {"FINISHED"}
    assert aging.material == "OAK", "the construction is not part of the setup reset"
    assert aging.age_years == 10 and aging.direct_dirt == 1 and not len(aging.events)
    print("CONTEXTUAL aging: PASS", flush=True)

    # --- geometry effects: the applied list and the selection-specific inspector ---
    ui_fixture.bake_synthetic(ts)
    engine.apply(ts)
    density = images.create("Density", 32, (1, 1, 1, 1))
    helper = manager.create(ts, cube, density, "BOLT", 4, .05, 2)
    assert helper is not None
    helper_name = helper.name
    layout = Recorder()
    geometry_effects.OP_PT_geometry_effects.draw(SimpleNamespace(layout=layout), context)
    text = layout.text
    assert "OP_MT_geometry_effect_add" in text and "Add Geometry Effect" in text
    assert "Bolt" in text and "open_painter.geometry_select" in text, text
    assert "Adds geometry" not in text or context.active_object == helper
    assert bpy.ops.open_painter.geometry_select(name=helper_name) == {"FINISHED"}
    assert context.view_layer.objects.active == helper
    layout = Recorder()
    geometry_effects.OP_PT_geometry_effects.draw(SimpleNamespace(layout=layout), context)
    text = layout.text
    assert "Adds geometry" in text and "instanced pieces" in text and "Distribution:" in text
    assert "Follow the Material" in text and "Regenerate" in text and "Remove Effect" in text
    assert "painter_geometry_bake_back" in text and "kept unless you choose Hide or Remove" in text
    frozen = SimpleNamespace(get=lambda key, default=None: {"kind": "DISPLACE"}.get(key, default))
    assert geometry_effects.effect_result(frozen)[0] == "Changes displacement"
    # Creation presets are a menu and shelf entries, not a wall of buttons.
    menu = Recorder()
    geometry_effects.OP_MT_geometry_effect_add.draw(SimpleNamespace(layout=menu), context)
    labels = [op["text"] for op in menu.operators("open_painter.geometry_effect")]
    assert "Rust Flakes" in labels and "Displace from Height" in labels and "Stitches" in labels
    from open_painter.ui import native
    assert native.entry_for_key("GEOMETRY:MOSS") is not None
    print("CONTEXTUAL geometry effects: PASS", flush=True)

    # --- analysis actions live where their inputs are; the panel is gone; commands stay ---
    assert "OP_PT_offline_analysis" not in dir(bpy.types)
    assert hasattr(bpy.ops.open_painter, "offline_analysis")
    layer = stack.add(ts, "FILL", "Contact")
    mask = layer.masks.add()
    mask.id, mask.source = "m-contact", "SCENE"
    mask.influence.kind = "CONTACT"
    settings = Recorder()
    properties_panel.draw_mask_settings(settings, context, ts, layer, mask)
    contact = [op for op in settings.operators("open_painter.offline_analysis")]
    assert [op["props"]["action"] for op in contact] == ["CONTACT"], contact
    assert "frozen contact-duration image" in settings.text
    mask.influence.kind = "ATTRIBUTE"
    settings = Recorder()
    properties_panel.draw_mask_settings(settings, context, ts, layer, mask)
    assert [op["props"]["action"] for op in settings.operators("open_painter.offline_analysis")] == ["STRAIN"]
    mask.source = "MESH"
    mask.mesh.kind = "ATTRIBUTE"
    settings = Recorder()
    properties_panel.draw_mask_settings(settings, context, ts, layer, mask)
    actions = [op["props"]["action"] for op in settings.operators("open_painter.offline_analysis")]
    assert actions == ["MARK", "STRAIN"], actions
    assert "open_painter_cut_surface" in settings.text
    menu = Recorder()
    performance.OP_MT_native_performance.draw(SimpleNamespace(layout=menu), context)
    assert [op["props"]["action"] for op in menu.operators("open_painter.offline_analysis")] == ["REPORT"]
    from open_painter.ui import export_view
    prepare = Recorder()
    export_view.draw_prepare(prepare, context)
    assert [op["props"]["action"] for op in prepare.operators("open_painter.offline_analysis")] == ["LOD"]
    for key, _label, description in offline_analysis.ACTIONS:
        assert "Input:" in description and "Output:" in description and "Read by:" in description, key
    # The commands still produce their data: the cut-surface mark and the inventory.
    context.view_layer.objects.active = cube
    for face in cube.data.polygons:
        face.select = face.index < 2
    assert bpy.ops.open_painter.offline_analysis(action="MARK") == {"FINISHED"}
    assert "open_painter_cut_surface" in cube.data.attributes
    assert bpy.ops.open_painter.offline_analysis(action="REPORT") == {"FINISHED"}
    print("CONTEXTUAL_TOOLS_PASS", flush=True)


area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
region = next(r for r in area.regions if r.type == "WINDOW")
with bpy.context.temp_override(area=area, region=region):
    run(bpy.context)
