"""The selection-specific inspector (UI plan phase 2), drawn into a recorder.

Each target kind shows its own first controls: a smart material its authored master
controls bound to real properties (a deleted target reports a broken binding), a fill
layer its channels and projection, a paint layer its destination and brush, a mask its
stack with View Mask and Add Paint to Mask, a filter its parameters, a plain folder its
group controls; publishing, modules and performance follow after. View Mask rewires
the display material to a snapshot and back; Add Paint to Mask keeps the generator.
Drawing changes nothing in the document.
"""
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import ui_fixture
from ui_layout_recorder import Recorder
from open_painter.authoring import document
from open_painter.core import layer_stack as stack
from open_painter.smart_materials import controls
from open_painter.ui import editor, inspector, mask_view, native, selection

open_painter.register()


def draw(context):
    target = selection.resolve(context)
    layout = Recorder()
    inspector.draw(layout, context, target)
    return target, layout


def run(context):
    ids = ui_fixture.build(context.scene)
    editor.mount(context.window, context.area)
    project = context.scene.open_painter
    body = next(ts for ts in project.texture_sets if ts.id == ids["body"])
    digest = document.digest(body)

    # --- smart material: authored master controls first, bound to the real properties ---
    target, layout = draw(context)
    assert target.kind == "MATERIAL"
    text = layout.text
    for label in ("Paint Color", "Paint Roughness", "Chip Amount", "Scratch Amount", "Dirt Amount", "Rust Amount"):
        assert label in text, label
    assert text.index("Paint Color") < text.index("Group Opacity") < text.index("painter_publish"), "appearance first"
    assert "painter_module" in text and "Performance" in text and "painter_layer_ai" in text
    folder = next(item for item in body.layers if item.id == ids["material"])
    bindings = {b.control.label: b for b in controls.bindings(body, folder)}
    coat = next(item for item in body.layers if item.id == ids["material_layers"]["Blue paint coat"])
    assert bindings["Paint Color"].owner == stack.channel_for(coat, "BASE_COLOR") and bindings["Paint Color"].field == "color"
    assert bindings["Chip Amount"].owner == coat.masks[0] and coat.masks[0].generator == "CHIPS"
    dirt = next(item for item in body.layers if item.id == ids["material_layers"]["Recess dirt"])
    # Dirt Amount is a coverage-gated amount (0.12.2): the mask's generator amount, opacity gated.
    assert bindings["Dirt Amount"].control.mode == "coverage" and bindings["Dirt Amount"].owner == folder
    assert bindings["Dirt Amount"].members[1][0] == dirt.masks[0] and dirt.masks[0].generator == "CAVITY_DIRT"
    scratches = next(item for item in body.layers if item.id == ids["material_layers"]["Scratched exposed steel"])
    assert bindings["Scratch Amount"].owner == scratches and bindings["Scratch Amount"].field == "opacity"
    # Editing the layer moves the control (same property); the control edits the layer.
    coat.masks[0].amount = .77
    assert abs(bindings["Chip Amount"].value - .77) < 1e-6
    setattr(bindings["Chip Amount"].owner, bindings["Chip Amount"].field, .29)
    assert abs(coat.masks[0].amount - .29) < 1e-6
    # Drawing the material inspector changed nothing.
    assert document.digest(body) == digest
    # An ordinary folder has no master controls; a stamped one keeps them; a renamed target still binds.
    plain = next(item for item in body.layers if item.id == ids["details"])
    assert not controls.available(plain)
    coat.name = "Renamed coat"
    assert controls.resolve(body, folder, bindings["Paint Color"].control).ok, "ids, not names, bind"
    # A similarly named layer never stands in for a deleted target: the binding reports broken.
    stand_in = stack.add(body, "FILL", "Blue paint coat", parent_id=folder.id)
    stand_in_id = stand_in.id
    body.active_layer = stack.index_of(body, ids["material_layers"]["Blue paint coat"])
    assert bpy.ops.open_painter.layer(action="DELETE") == {"FINISHED"}
    folder = next(item for item in body.layers if item.id == ids["material"])
    broken = {b.control.label: b for b in controls.bindings(body, folder)}
    assert not broken["Paint Color"].ok and "deleted" in broken["Paint Color"].reason, broken["Paint Color"].reason
    assert not broken["Chip Amount"].ok
    assert broken["Dirt Amount"].ok, "other controls keep working"
    assert any(item.id == stand_in_id and item.name == "Blue paint coat" for item in body.layers)
    bpy.ops.ed.undo_push(message="deleted coat")
    body.active_layer = stack.index_of(body, ids["material"])
    target, layout = draw(context)
    assert "Paint Color: layer 'Blue paint coat' was deleted" in layout.text, layout.labels()
    print("INSPECTOR material: PASS", flush=True)

    # --- fill layer: channels, values, projection; blend distinctions ---
    body = next(ts for ts in project.texture_sets if ts.id == ids["body"])
    assert bpy.ops.open_painter.native_select(layer_id=ids["stripes"]) == {"FINISHED"}
    target, layout = draw(context)
    assert target.kind == "FILL"
    text = layout.text
    assert text.index("enabled") < text.index("properties_channel") < text.index("procedural_scale")
    assert "Layer Opacity" in text and "Layer Blend" in text and "Blend Override" in text
    assert "Projection and Scale" in text and "painter_publish" in text
    assert text.index("Layer Opacity") < text.index("painter_publish")
    assert "Surface Response" in text
    print("INSPECTOR fill: PASS", flush=True)

    # --- paint layer: destination and brush first ---
    assert bpy.ops.open_painter.native_select(layer_id=ids["paint"]) == {"FINISHED"}
    target, layout = draw(context)
    assert target.kind == "PAINT" and target.paint.kind == "LAYER"
    text = layout.text
    assert "Paint destination: Hand Paint" in text and "open_painter.paint" in text
    assert text.index("Paint destination") < text.index("painter_brush") < text.index("Layer Opacity")
    # Paint Material (0.13): the destination line, the tool button and the channel toggles sit in
    # the inspector, one control per row (no channel row that a narrow sidebar truncates).
    assert "painter_material_brush" in text and "open_painter.vector_brush" in text
    assert " > Hand Paint | " in text, text[text.index("painter_material_brush"):][:300]
    assert text.index("painter_material_brush") < text.index("painter_brush")
    material_block = text[text.index("painter_material_brush"):text.index("painter_brush")]
    items = layout.items[next(i for i, (kind, data) in enumerate(layout.items) if kind == "panel" and data["idname"] == "painter_material_brush"):]
    toggles = [data for kind, data in items if kind == "prop_enum" and data["name"] == "channels"]
    assert len(toggles) >= 4 and any(kind == "grid" for kind, _ in items), "channel toggles must sit in a grid, not one row"
    assert all(line.count("Color") <= 1 for line in material_block.splitlines())
    assert "universal" in material_block and "pressure_size" in material_block
    assert "Paint layers are always protected" in text
    print("INSPECTOR paint: PASS", flush=True)

    # --- mask: stack, View Mask, Add Paint to Mask, settings ---
    coat_id = ids["material_layers"]["Recess dirt"]
    assert bpy.ops.open_painter.native_select(layer_id=coat_id, mask=True) == {"FINISHED"}
    target, layout = draw(context)
    assert target.kind == "MASK" and target.mask.generator == "CAVITY_DIRT"
    text = layout.text
    assert "View Mask" in text and "Add Paint to Mask" in text and "OP_UL_masks" in text
    assert "Cavity Dirt Settings" in text and "amount" in text and "invert" in text
    assert "procedural" in text.lower() and "Paint Mask" not in text
    assert text.index("View Mask") < text.index("OP_UL_masks") < text.index("amount")
    # View Mask: the display material reads a snapshot; the mask itself is untouched.
    dirt = next(item for item in body.layers if item.id == coat_id)
    sources = [(m.source, m.generator) for m in dirt.masks]
    assert bpy.ops.open_painter.view_mask(action="VIEW") == {"FINISHED"}
    view = mask_view.active(body)
    assert view is not None and view["layer_id"] == coat_id
    image = mask_view.image_of(view)
    assert image is not None and image.size[0] == min(int(body.resolution), mask_view.MAX_SIDE)
    from open_painter.core import texture_set_node

    def group_output():
        group = bpy.data.node_groups[texture_set_node.tree_name(body)]
        return group, next(n for n in group.nodes if n.bl_idname == "NodeGroupOutput")

    # The material stays its three nodes; the Texture Set node's group shows the snapshot.
    kinds = sorted(n.bl_idname for n in body.material.node_tree.nodes)
    assert kinds == ["OPShaderNodeTextureSet", "ShaderNodeBsdfPrincipled", "ShaderNodeOutputMaterial"], kinds
    assert texture_set_node.showing_mask(body)
    group, output = group_output()
    assert output.inputs["Base Color"].links[0].from_node.get("role") == texture_set_node.MASK_VIEW
    assert output.inputs["Roughness"].links[0].from_node.get("role") == texture_set_node.MASK_VIEW_ROLES[1]
    assert output.inputs["Normal"].links[0].from_node.bl_idname == "ShaderNodeNewGeometry"
    assert [(m.source, m.generator) for m in dirt.masks] == sources, "never rasterised"
    target, layout = draw(context)
    assert "Back to Material" in layout.text and "Snapshot current" in layout.text
    dirt.masks[0].amount = .61
    assert mask_view.stale(body)
    target, layout = draw(context)
    assert "out of date" in layout.text
    assert bpy.ops.open_painter.view_mask(action="REFRESH") == {"FINISHED"}
    assert not mask_view.stale(body)
    assert bpy.ops.open_painter.view_mask(action="BACK") == {"FINISHED"}
    assert mask_view.active(body) is None
    assert not texture_set_node.showing_mask(body)
    group, output = group_output()
    assert not any(n.get("role") in texture_set_node.MASK_VIEW_ROLES for n in group.nodes)
    assert output.inputs["Normal"].links[0].from_node.bl_idname == "ShaderNodeBump"
    assert len(body.material.node_tree.nodes) == 3
    # Viewing a layer that is then deleted falls back to the material.
    assert bpy.ops.open_painter.view_mask(action="VIEW") == {"FINISHED"}
    assert bpy.ops.open_painter.layer(action="DELETE") == {"FINISHED"}
    assert mask_view.active(body) is None
    assert not texture_set_node.showing_mask(body)
    print("INSPECTOR mask view: PASS", flush=True)

    # --- Add Paint to Mask keeps the generator, adds an explicit role on top ---
    grime_id = ids["grime"]
    assert bpy.ops.open_painter.native_select(layer_id=grime_id, mask=True) == {"FINISHED"}
    grime = next(item for item in body.layers if item.id == grime_id)
    before = [m.source for m in grime.masks]
    assert bpy.ops.open_painter.mask_paint(role="REMOVE", start_painting=False) == {"FINISHED"}
    grime = next(item for item in body.layers if item.id == grime_id)
    assert [m.source for m in grime.masks] == before + ["PAINT"]
    added = grime.masks[-1]
    assert added.operation == "MULTIPLY" and "Remove Coverage" in added.name
    pixels = added.image.pixels[:4]
    assert tuple(round(v) for v in pixels) == (1, 1, 1, 1), "white: paint black to remove"
    assert selection.resolve(context).mask_id == added.id
    assert bpy.ops.open_painter.mask_paint(role="ADD", start_painting=False) == {"FINISHED"}
    grime = next(item for item in body.layers if item.id == grime_id)
    assert grime.masks[-1].operation == "ADD" and tuple(round(v) for v in grime.masks[-1].image.pixels[:3]) == (0, 0, 0)
    target, layout = draw(context)
    assert target.paint.kind == "MASK" and "Paint Mask" in layout.text
    print("INSPECTOR add paint to mask: PASS", flush=True)

    # --- channel filter: parameters first, then the stack ---
    stripes = next(item for item in body.layers if item.id == ids["stripes"])
    prop = stack.channel_for(stripes, "BASE_COLOR")
    effect = prop.effects.add()
    effect.id, effect.kind, effect.name = "fx-levels", "LEVELS", "Levels"
    assert bpy.ops.open_painter.native_select(layer_id=ids["stripes"], channel="BASE_COLOR", effect_id="fx-levels") == {"FINISHED"}
    target, layout = draw(context)
    assert target.kind == "FILTER"
    text = layout.text
    assert "Levels" in text and "Filters Base Color of Stripes" in text
    assert text.index("Filters Base Color") < text.index("painter_filter_stack")
    assert "Back to Stripes" in text
    print("INSPECTOR filter: PASS", flush=True)

    # --- plain folder ---
    assert bpy.ops.open_painter.native_select(layer_id=ids["details"]) == {"FINISHED"}
    target, layout = draw(context)
    assert target.kind == "FOLDER"
    text = layout.text
    assert "Group Opacity" in text and "Group Compositing" in text and "Group Mask" in text
    assert "Paint Color" not in text and "Chip Amount" not in text
    # Layer rows: the name has the row; blend controls sit in the selected-item row.
    assert not native.dense_rows(context), "compact rows by default"
    row = Recorder()
    native.draw_selected_row(row, context, body)
    assert "blend_channel" in row.text and "opacity_percent" in row.text, "an isolated folder blends per channel"
    # Re-resolved by id: the reference taken before the stack changed is stale (RNA reallocation).
    plain = next(item for item in body.layers if item.id == ids["details"])
    plain.group_mode = "PASSTHROUGH"
    row = Recorder()
    native.draw_selected_row(row, context, body)
    assert "group_mode" in row.text and "blend_mode" not in row.text, "a passthrough folder shows its compositing, not a blend"
    assert bpy.ops.open_painter.native_select(layer_id=ids["stripes"]) == {"FINISHED"}
    row = Recorder()
    native.draw_selected_row(row, context, body)
    assert "blend_mode" in row.text and "opacity_percent" in row.text
    print("INSPECTOR_PANELS_PASS", flush=True)


area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
region = next(r for r in area.regions if r.type == "WINDOW")
with bpy.context.temp_override(area=area, region=region):
    run(bpy.context)
