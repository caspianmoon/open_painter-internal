"""Draw native procedural/filter panels and select a recipe through Blender search."""

import json
import sys
import traceback
from pathlib import Path

import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.ui import native
from open_painter.core import layer_stack as stack, texture_set_manager as sets

result_path = root / ".blender-test" / "texture-operations-ui.json"
result_path.parent.mkdir(exist_ok=True)
result_path.write_text(json.dumps({"status": "RUNNING"}))
drawn, errors = set(), []
area = None
original_draw = native.OP_PT_native_properties.draw


def draw(self, context):
    try:
        original_draw(self, context)
        layer = stack.active(context.scene.open_painter.texture_sets[0])
        drawn.add("mask" if native.state.get(context.area).mask_selected else "channel")
        if native.state.get(context.area).mask_selected and layer.masks:
            mask = layer.masks[layer.active_mask]
            if mask.source == "ANCHOR":
                drawn.add("anchor_mask")
            elif mask.source == "REFERENCE" and mask.reference_anchor:
                drawn.add("anchor_reference")
        else:
            prop = stack.channel_for(layer, context.scene.open_painter.properties_channel)
            if prop and prop.effects and prop.effects[min(prop.active_effect, len(prop.effects) - 1)].kind == "ANCHOR":
                drawn.add("anchor_channel")
    except Exception:
        errors.append(traceback.format_exc())
        raise


native.OP_PT_native_properties.draw = draw
open_painter.register()
bpy.context.preferences.view.show_splash = False


def later(function, delay=1):
    def run():
        try:
            if area:
                region = next(region for region in area.regions if region.type == "WINDOW")
                with bpy.context.temp_override(area=area, region=region):
                    function()
            else:
                function()
        except Exception:
            result_path.write_text(traceback.format_exc())
            traceback.print_exc()
            bpy.ops.wm.quit_blender()
    bpy.app.timers.register(run, first_interval=delay)


def setup():
    global area
    area = max((area for area in bpy.context.screen.areas if area.type == "VIEW_3D"), key=lambda a: a.width * a.height)
    region = next(region for region in area.regions if region.type == "WINDOW")
    with bpy.context.temp_override(area=area, region=region):
        ts = sets.create(bpy.context.scene, "Native texture controls", 512)
        sets.assign(bpy.context.scene, ts, bpy.context.object)
        bpy.ops.open_painter.open_editor()
        bpy.ops.open_painter.catalog_material(choice="Clean Brushed Steel")
        fill = next(layer for layer in ts.layers if layer.type == "FILL")
        bpy.ops.open_painter.native_select(layer_id=fill.id)
        bpy.ops.open_painter.choose_filter(choice="HSL")
    later(mask_stage, 3)


def mask_stage():
    assert "channel" in drawn and not errors, errors
    bpy.ops.open_painter.choose_procedural(choice="SCRATCHES", target="MASK", create=True)
    bpy.ops.open_painter.choose_filter(choice="SLOPE_BLUR", target="MASK")
    later(anchor_stage, 2)


def anchor_stage():
    assert "mask" in drawn and not errors, errors
    ts = bpy.context.scene.open_painter.texture_sets[0]
    assert bpy.ops.open_painter.anchor(action="ADD_MASK") == {"FINISHED"}
    anchor_id = stack.active(ts).masks[-1].id
    later(lambda: anchor_reference_stage(anchor_id), 2)


def anchor_reference_stage(anchor_id):
    assert "anchor_mask" in drawn and not errors, errors
    assert bpy.ops.open_painter.mask(action="ADD", source="REFERENCE") == {"FINISHED"}
    assert bpy.ops.open_painter.anchor_pick(target="REFERENCE_MASK", choice=anchor_id) == {"FINISHED"}
    later(lambda: anchor_channel_stage(anchor_id), 2)


def anchor_channel_stage(anchor_id):
    assert "anchor_reference" in drawn and not errors, errors
    ts = bpy.context.scene.open_painter.texture_sets[0]
    assert bpy.ops.open_painter.anchor_locate(anchor_id=anchor_id) == {"FINISHED"}
    layer = stack.active(ts)
    assert layer.masks[layer.active_mask].id == anchor_id
    bpy.ops.open_painter.native_select(layer_id=layer.id, mask=False)
    assert bpy.ops.open_painter.anchor(action="ADD_CHANNEL") == {"FINISHED"}
    later(search_stage, 2)


def search_stage():
    assert "anchor_channel" in drawn and not errors, errors
    region = bpy.context.region
    bpy.context.window.cursor_warp(region.x + region.width // 2, region.y + region.height // 2)
    assert bpy.ops.open_painter.catalog_material("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
    later(type_search)


def type_search():
    for char in "Polished Chrome":
        bpy.context.window.event_simulate(type="A", value="PRESS", unicode=char)
        bpy.context.window.event_simulate(type="A", value="RELEASE")
    later(choose_result)


def choose_result():
    bpy.context.window.event_simulate(type="RET", value="PRESS")
    bpy.context.window.event_simulate(type="RET", value="RELEASE")
    later(check)


def check():
    ts = bpy.context.scene.open_painter.texture_sets[0]
    assert stack.active(ts).name == "Polished Chrome", stack.active(ts).name
    assert not errors, errors
    result_path.write_text(json.dumps({"native_channel_controls": "PASS", "native_mask_filters": "PASS",
                                       "native_anchor_controls": "PASS", "native_search_selection": "PASS"}, indent=2))
    print("TEXTURE_OPERATIONS_UI_PASS")
    bpy.ops.wm.quit_blender()


later(setup)
