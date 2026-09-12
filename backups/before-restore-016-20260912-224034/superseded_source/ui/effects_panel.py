"""Native Blender effect lists and operators, shared by the dock and N-panel."""

import json

import bpy

from ..core import effect_stack
from ..core.layer_stack import active, snapshot
from ..core.project_manager import active_set, require_set
from .operators import SafeOperator


class OP_OT_effect(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.effect"
    bl_label = "Edit Effect Stack"
    bl_options = {"REGISTER", "UNDO"}
    action: bpy.props.EnumProperty(
        items=[
            (x, x.title(), "")
            for x in ("ADD", "DELETE", "UP", "DOWN", "DUPLICATE", "COPY", "PASTE")
        ]
    )
    kind: bpy.props.EnumProperty(items=[("LEVELS", "Levels", ""), ("FILTER", "Filter", "")])
    target: bpy.props.EnumProperty(
        items=[("CONTENT", "Layer Content", ""), ("MASK", "Combined Mask", "")]
    )

    def run(self, context):
        ts = require_set(context)
        layer = active(ts)
        if not layer:
            raise ValueError("Select a layer or folder first.")
        layer.effect_target = self.target
        project = context.scene.open_painter
        if self.action == "ADD":
            effect_stack.add(ts, layer, self.target, self.kind, project.blend_channel)
        elif self.action == "COPY":
            effects = effect_stack.collection(layer, self.target)
            if not effects:
                raise ValueError("Select an effect to copy.")
            index = max(
                0, min(getattr(layer, effect_stack.index_key(self.target)), len(effects) - 1)
            )
            project.effect_clipboard = json.dumps(snapshot(effects[index]))
        elif self.action == "PASTE":
            if not project.effect_clipboard:
                raise ValueError("Copy an effect first.")
            if len(project.effect_clipboard) > 65536:
                raise ValueError("Effect clipboard is too large.")
            data = json.loads(project.effect_clipboard)
            if not isinstance(data, dict) or data.get("type") not in {"LEVELS", "FILTER"}:
                raise ValueError("Invalid effect clipboard.")
            effect_stack.add(ts, layer, self.target, data=data)
        else:
            effect_stack.edit(ts, layer, self.target, self.action)


class OP_UL_effects(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(item, "enabled", text="")
        layout.prop(item, "name", text="", emboss=False, icon="MODIFIER")
        if active_propname == "active_effect":
            layout.label(text=item.channel.replace("_", " ").title())


def draw_effects(layout, context):
    ts = active_set(context)
    layer = active(ts) if ts else None
    if not layer:
        layout.label(text="Select a layer or folder first.")
        return
    layout.prop(layer, "effect_target", expand=True)
    target = layer.effect_target
    key = effect_stack.index_key(target)
    effects = effect_stack.collection(layer, target)
    row = layout.row(align=True)
    for kind in ("LEVELS", "FILTER"):
        op = row.operator("open_painter.effect", text="Add " + kind.title(), icon="ADD")
        op.action, op.kind, op.target = "ADD", kind, target
    layout.template_list(
        "OP_UL_effects",
        target,
        layer,
        "mask_effects" if target == "MASK" else "effects",
        layer,
        key,
        rows=3,
    )
    row = layout.row(align=True)
    for action, icon in (
        ("UP", "TRIA_UP"),
        ("DOWN", "TRIA_DOWN"),
        ("DUPLICATE", "DUPLICATE"),
        ("DELETE", "TRASH"),
        ("COPY", "COPYDOWN"),
        ("PASTE", "PASTEDOWN"),
    ):
        op = row.operator("open_painter.effect", text="", icon=icon)
        op.action, op.target = action, target
    layout.label(text="Effects run from top to bottom.")
    if target == "MASK":
        layout.label(text="Adjusts the combined mask; no mask starts white.")
    if not effects:
        return
    effect = effects[max(0, min(getattr(layer, key), len(effects) - 1))]
    layout.prop(effect, "name")
    if target == "CONTENT":
        layout.prop(effect, "channel")
        if effect.channel in {"BASE_COLOR", "NORMAL"}:
            layout.prop(effect, "component")
    layout.prop(effect, "opacity")
    if effect.type == "LEVELS":
        for name in ("input_black", "gamma", "input_white", "output_black", "output_white"):
            layout.prop(effect, name)
        if effect.input_white <= effect.input_black:
            layout.label(text="Input White must exceed Input Black.", icon="ERROR")
    else:
        layout.prop(effect, "filter")
        if effect.filter == "BRIGHTNESS_CONTRAST":
            layout.prop(effect, "brightness")
            layout.prop(effect, "contrast")
    layout.prop(effect, "clamp")


class OP_PT_effects(bpy.types.Panel):
    bl_order = 3
    bl_label = "Effects"
    bl_parent_id = "OP_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Painter"

    def draw(self, context):
        draw_effects(self.layout, context)
