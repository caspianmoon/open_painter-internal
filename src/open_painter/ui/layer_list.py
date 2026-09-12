import bpy

from ..core import layer_stack
from ..core.project_manager import active_set, require_set
from .operators import SafeOperator


def folders(self, context):
    ts = active_set(context)
    return [("ROOT", "No folder", "")] + (
        [(item.id, item.name, "") for item in ts.layers if item.type == "FOLDER"] if ts else []
    )


class OP_OT_layer(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.layer"
    bl_label = "Edit Layer Stack"
    bl_options = {"REGISTER", "UNDO"}
    action: bpy.props.EnumProperty(
        items=[
            (x, x.title(), "")
            for x in (
                "FILL",
                "PAINT",
                "FOLDER",
                "DELETE",
                "DUPLICATE",
                "UP",
                "DOWN",
                "PARENT",
                "GROUP",
                "UNGROUP",
            )
        ]
    )
    folder: bpy.props.EnumProperty(name="Parent Folder", items=folders)

    def invoke(self, context, event):
        if self.action == "PARENT":
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        self.layout.prop(self, "folder")

    def run(self, context):
        ts = require_set(context)
        if self.action == "GROUP":
            from .dock import state

            ui = state.get(context.area) if context.area else None
            current = layer_stack.active(ts)
            ids = (
                ui.selected_layers
                if ui and ui.selected_layers
                else ({current.id} if current else set())
            )
            folder = layer_stack.group(ts, ids)
            if ui:
                ui.selected_layers = {folder.id}
            return
        if self.action in {"FILL", "PAINT", "FOLDER"}:
            previous = layer_stack.active(ts)
            parent_id = (
                previous.parent_id
                if previous
                else ""
            )
            created = layer_stack.add(ts, self.action, parent_id=parent_id)
            if parent_id:
                next(item for item in ts.layers if item.id == parent_id).expanded = True
            if context.area:
                from .dock import state

                state.get(context.area).selected_layers = {created.id}
            return
        layer = layer_stack.active(ts)
        if not layer:
            raise ValueError("Select a layer first.")
        if self.action == "UNGROUP":
            if layer.type != "FOLDER":
                raise ValueError("Select a group first.")
            layer_stack.remove(ts, ts.active_layer)
        elif self.action == "DELETE":
            layer_stack.remove_tree(ts, layer.id)
        elif self.action == "DUPLICATE":
            layer_stack.duplicate(ts, ts.active_layer)
        elif self.action in {"UP", "DOWN"}:
            layer_stack.move(ts, ts.active_layer, 1 if self.action == "UP" else -1)
        else:
            layer_stack.parent(ts, layer, "" if self.folder == "ROOT" else self.folder)


class OP_UL_layers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(
            item, "visible", text="", emboss=False, icon="HIDE_OFF" if item.visible else "HIDE_ON"
        )
        if item.parent_id:
            layout.label(text="", icon="BLANK1")
        layout.prop(
            item,
            "name",
            text="",
            emboss=False,
            icon={"PAINT": "BRUSH_DATA", "FILL": "MATERIAL", "FOLDER": "FILE_FOLDER"}[item.type],
        )
        if hasattr(item, "masks") and item.masks:
            layout.label(text="", icon="MOD_MASK")


class OP_PT_layers(bpy.types.Panel):
    bl_order = 0
    bl_label = "Layers"
    bl_parent_id = "OP_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Painter"

    def draw(self, context):
        ts = active_set(context)
        layout = self.layout
        if not ts:
            return
        row = layout.row(align=True)
        for action, label in (("PAINT", "Paint"), ("FILL", "Fill"), ("GROUP", "Group")):
            row.operator("open_painter.layer", text=label, icon="ADD").action = action
        layout.template_list(
            "OP_UL_layers", "", ts, "layers", ts, "active_layer", rows=6, sort_reverse=True
        )
        row = layout.row(align=True)
        for action, icon in (
            ("UP", "TRIA_UP"),
            ("DOWN", "TRIA_DOWN"),
            ("DUPLICATE", "DUPLICATE"),
            ("DELETE", "TRASH"),
            ("PARENT", "FILE_FOLDER"),
        ):
            row.operator("open_painter.layer", text="", icon=icon).action = action
        layer = layer_stack.active(ts)
        if not layer:
            layout.label(text="Add a fill or paint layer.")
            return
        layout.prop(layer, "name", text="Name")
        layout.prop(layer, "opacity")
        if layer.type == "FOLDER":
            layout.prop(layer, "group_mode")
        if layer.type != "FOLDER" or layer.group_mode == "ISOLATED":
            layout.prop(layer, "blend_mode")
        layout.operator(
            "open_painter.dock_popup", text="Channel Blending…"
        ).section = "CHANNEL_BLEND"
        if layer.parent_id:
            folder = next((item for item in ts.layers if item.id == layer.parent_id), None)
            layout.label(text=f"Folder: {folder.name if folder else 'Missing'}")
        for channel in layer.channels:
            row = layout.row(align=True)
            row.prop(channel, "enabled", text=channel.channel.replace("_", " ").title())
            if layer.type == "PAINT":
                op = row.operator("open_painter.paint", text="Paint", icon="BRUSH_DATA")
                op.channel = channel.channel
            elif layer.type == "FILL":
                sub = row.row()
                sub.enabled = channel.enabled
                sub.prop(
                    channel,
                    "color" if channel.channel in {"BASE_COLOR", "NORMAL"} else "value",
                    text="",
                )
            if layer.type == "FILL" and channel.enabled:
                layout.template_ID(channel, "image", open="image.open")
                if channel.image:
                    layout.prop(channel, "projection")
                    layout.prop(channel, "filtering")
                    layout.prop(channel, "tiling")
                    if channel.projection == "TRIPLANAR":
                        layout.prop(channel, "triplanar_hardness")
                    layout.prop(channel, "scale")
                    layout.prop(channel, "rotation")
                    layout.prop(channel, "offset")
