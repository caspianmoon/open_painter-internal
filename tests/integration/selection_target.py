"""One editing target (UI plan phase 1): Layers, Nodes and the inspector resolve the same
thing, by stable id; reordering keeps the paint destination, deleting the selected mask
falls back to something valid, and an unrelated area keeps its own selection kind.
"""
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import ui_fixture
from open_painter.core import layer_stack as stack
from open_painter.graph import mirror
from open_painter.ui import editor, selection
from open_painter.ui.dock import state

open_painter.register()


def by_name(ts, name):
    return next(layer for layer in ts.layers if layer.name == name)


def run(context):
    window = context.window
    area = context.area
    ids = ui_fixture.build(context.scene)
    project = context.scene.open_painter
    body = next(ts for ts in project.texture_sets if ts.id == ids["body"])
    editor.mount(window, area)   # a Painter session on this screen
    assert editor.is_area(area)

    # The smart material folder is active after the fixture: a MATERIAL target with its path.
    target = selection.resolve(context)
    assert target.kind == "MATERIAL" and target.layer_id == ids["material"], (target.kind, target.path)
    assert selection.breadcrumb_text(target) == "Body › Worn Painted Metal", target.path
    assert target.paint.kind == "NONE" and "folder" in target.paint.reason
    assert target.display_channel == "BASE_COLOR" and target.blend_channel == "BASE_COLOR"
    project.display_channel, project.blend_channel = "ROUGHNESS", "METALLIC"
    target = selection.resolve(context)
    assert (target.display_channel, target.blend_channel) == ("ROUGHNESS", "METALLIC"), "three separate channels"
    assert project.paint_channel == "ROUGHNESS" and project.properties_channel == "BASE_COLOR"
    project.display_channel = project.blend_channel = "BASE_COLOR"

    # Content, then the mask stack, then one specific mask — through the operator.
    coat = ids["material_layers"]["Blue paint coat"]
    assert bpy.ops.open_painter.native_select(layer_id=coat) == {"FINISHED"}
    target = selection.resolve(context)
    assert target.kind == "FILL" and target.layer_id == coat
    assert selection.breadcrumb_text(target) == "Body › Worn Painted Metal › Blue paint coat", target.path
    assert target.channel is not None and target.channel.channel == "BASE_COLOR"
    assert bpy.ops.open_painter.native_select(layer_id=coat, mask=True) == {"FINISHED"}
    target = selection.resolve(context)
    assert target.kind == "MASK" and target.mask is not None and target.mask.name == "Chips", target.path
    assert selection.breadcrumb_text(target) == "Body › Worn Painted Metal › Blue paint coat › Chips"
    assert target.paint.kind == "NONE" and "Add Paint to Mask" in target.paint.reason, target.paint
    assert state.get(area).mask_selected, "the legacy flag mirrors the selection kind"
    grunge = ids["grime_masks"][2]
    assert bpy.ops.open_painter.native_select(layer_id=ids["grime"], mask_id=grunge) == {"FINISHED"}
    target = selection.resolve(context)
    grime = by_name(body, "Grime")
    assert target.kind == "MASK" and target.mask_id == grunge and grime.active_mask == 2, (target.mask_id, grime.active_mask)
    print("SELECTION layers → inspector: PASS", flush=True)

    # A channel filter is a target of its own.
    stripes = by_name(body, "Stripes")
    prop = stack.channel_for(stripes, "BASE_COLOR")
    effect = prop.effects.add()
    effect.id, effect.kind, effect.name = "fx-stripes-levels", "LEVELS", "Levels"
    assert bpy.ops.open_painter.native_select(layer_id=ids["stripes"], channel="BASE_COLOR", effect_id="fx-stripes-levels") == {"FINISHED"}
    target = selection.resolve(context)
    assert target.kind == "FILTER" and target.effect_id == "fx-stripes-levels", target.kind
    assert selection.breadcrumb_text(target) == "Body › Details › Stripes › Levels", target.path
    assert project.properties_channel == "BASE_COLOR"

    # Nodes: clicking a mask node lands on the same target; the mirror does not ping-pong.
    tree = mirror.ensure_tree(body)
    mirror.reconcile(tree, body)
    levels_id = ids["grime_masks"][4]
    node = next(item for item in tree.nodes if getattr(item, "op_id", "") == levels_id)
    for item in tree.nodes:
        item.select = item == node
    tree.nodes.active = node
    tree["op_active_node"] = ""
    assert mirror.mirror_selection(tree, body, window, area)
    target = selection.resolve(context)
    assert target.kind == "MASK" and target.mask_id == levels_id and target.layer_id == ids["grime"], target.path
    assert not mirror.mirror_selection(tree, body, window, area), "a mirrored selection must not re-trigger"
    # Selection sync changed nothing in the material.
    from open_painter.authoring import document
    before = document.digest(body)
    with context.temp_override(space_data=None):
        assert bpy.ops.open_painter.graph_select(layer_id=ids["stripes"], channel="BASE_COLOR", effect_index=0) == {"FINISHED"}
    target = selection.resolve(context)
    assert target.kind == "FILTER" and target.effect_id == "fx-stripes-levels"
    assert document.digest(body) == before, "selection must not edit the document"
    print("SELECTION nodes ↔ layers: PASS", flush=True)

    # Reordering does not redirect the paint destination.
    assert bpy.ops.open_painter.native_select(layer_id=ids["paint"]) == {"FINISHED"}
    target = selection.resolve(context)
    assert target.kind == "PAINT" and target.paint.kind == "LAYER" and target.paint.layer_id == ids["paint"], target.paint
    index = stack.index_of(body, ids["paint"])
    stack.move(body, index, -1)
    assert stack.index_of(body, ids["paint"]) != index
    target = selection.resolve(context)
    assert target.layer_id == ids["paint"] and target.paint.layer_id == ids["paint"], "the target follows the id"
    assert bpy.ops.open_painter.native_select(layer_id=ids["paint"], mask=True) == {"FINISHED"}
    target = selection.resolve(context)
    assert target.kind == "MASK" and target.mask_id == ids["painted_mask"]
    assert target.paint.kind == "MASK" and target.paint.mask_id == ids["painted_mask"], target.paint
    stack.move(body, stack.index_of(body, ids["paint"]), 1)
    target = selection.resolve(context)
    assert target.paint.mask_id == ids["painted_mask"], "moving the layer keeps the mask destination"
    print("SELECTION reorder keeps the destination: PASS", flush=True)

    # Deleting the selected mask: a valid fallback, never a stale reference.
    assert bpy.ops.open_painter.native_select(layer_id=ids["grime"], mask_id=ids["grime_masks"][0]) == {"FINISHED"}
    assert bpy.ops.open_painter.mask(action="DELETE") == {"FINISHED"}
    grime = by_name(body, "Grime")
    target = selection.resolve(context)
    assert target.kind == "MASK" and target.mask_id in {m.id for m in grime.masks}, "another mask of the stack"
    assert target.mask_id != ids["grime_masks"][0]
    assert bpy.ops.open_painter.native_select(layer_id=ids["paint"], mask=True) == {"FINISHED"}
    assert bpy.ops.open_painter.mask(action="DELETE") == {"FINISHED"}
    target = selection.resolve(context)
    assert target.kind == "PAINT" and target.mask is None, "content when the last mask is gone"
    assert state.get(area).selection_kind == selection.CONTENT
    # A mask flag on a layer without masks still resolves to a valid content target.
    state.get(area).selection_kind = selection.MASK
    assert selection.resolve(context).kind == "PAINT"
    state.get(area).selection_kind = selection.CONTENT
    print("SELECTION deleted mask falls back: PASS", flush=True)

    # Another area that is not part of this screen's session keeps its own kind.
    foreign = state.states.setdefault(area.as_pointer() + 4096, state.DockState())
    foreign.selection_kind = selection.MASK
    assert bpy.ops.open_painter.native_select(layer_id=ids["stripes"]) == {"FINISHED"}
    assert state.get(area).selection_kind == selection.CONTENT
    assert foreign.selection_kind == selection.MASK, "an unrelated window keeps its target"
    # A companion context (no Painter area of its own) resolves the session's target.
    other = next(a for a in context.screen.areas if a is not area)
    with context.temp_override(area=other):
        assert selection.painter_area(bpy.context) == area
        assert selection.resolve(bpy.context).layer_id == ids["stripes"]
    print("SELECTION session scope: PASS", flush=True)

    # Undo keeps the flags valid.
    bpy.ops.ed.undo_push(message="before")
    assert bpy.ops.open_painter.native_select(layer_id=ids["grime"], mask=True) == {"FINISHED"}
    assert selection.resolve(context).kind == "MASK"
    bpy.ops.ed.undo_push(message="after")
    bpy.ops.ed.undo()
    target = selection.resolve(context)
    assert target.kind in {"MASK", "FILL", "PAINT", "FOLDER", "MATERIAL"} and target.layer is not None
    if target.kind == "MASK":
        assert target.mask is not None and target.mask_id in {m.id for m in target.layer.masks}
    print("SELECTION_TARGET_PASS", flush=True)


area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
region = next(r for r in area.regions if r.type == "WINDOW")
with bpy.context.temp_override(area=area, region=region):
    run(bpy.context)
