import bpy

from ..core import layer_stack
from ..core.project_manager import active_set, require_set
from .operators import SafeOperator


def folders(self, context):
    ts = active_set(context)
    return [("ROOT", "No folder", "")] + (
        [(item.id, item.name, "") for item in ts.layers if item.type == "FOLDER"] if ts else []
    )


def step(ts, layer, direction):
    """Move ``layer`` (with its subtree) one drop candidate up or down the list.

    The list is drawn by parent, so swapping raw storage neighbours (as
    ``layer_stack.move`` does) often changes nothing on screen while the raw
    order consumers read (the anchor picker's "above") still flips. Walking
    the drag's candidates instead passes every folder edge the way a drop
    would: into an expanded folder, out of it, then past the next row.
    """
    try:
        gap, depth = layer_stack.slot(ts, layer.id)
    except ValueError:
        # Under a collapsed folder there is no row to step through.
        layer_stack.move(ts, ts.active_layer, direction)
        return
    steps = [(g, target) for g, candidates in enumerate(layer_stack.drop_targets(ts, layer.id))
             for target in candidates]
    current = next((i for i, (g, target) in enumerate(steps) if g == gap and target[0] == depth), None)
    if current is None:
        layer_stack.move(ts, ts.active_layer, direction)
        return
    wanted = current - direction   # candidates run top to bottom; UP is the earlier one
    if 0 <= wanted < len(steps):
        layer_stack.place(ts, layer.id, steps[wanted][1])


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
                "TEXT",
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

    @classmethod
    def description(cls, context, properties):
        # Icon-only buttons rely on this tooltip.
        return {
            "FILL": "Add a fill layer above the selected layer",
            "PAINT": "Add a paint layer above the selected layer",
            "FOLDER": "Add a folder above the selected layer",
            "DELETE": "Delete the selected layer, including a folder's contents",
            "DUPLICATE": "Duplicate the selected layer",
            "UP": "Move the selected layer up",
            "DOWN": "Move the selected layer down",
            "PARENT": "Move the selected layer into a folder",
            "GROUP": "Group the selected layers in a new folder",
            "UNGROUP": "Dissolve the selected folder, keeping its layers",
        }.get(properties.action, "")

    def invoke(self, context, event):
        if self.action == "PARENT":
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        self.layout.prop(self, "folder")

    def run(self, context):
        ts = require_set(context)
        before = layer_stack.active(ts)
        before_id = before.id if before else ""
        self.edit(context, ts)
        if context.area:
            # The panels show content for a layer without masks, but the shelf
            # reads the flag as it is: left over from a mask on the previous
            # layer (or one undone since), a Texture click would add a mask.
            from .dock import state

            layer = layer_stack.active(ts)
            if layer is None or layer.id != before_id or not layer.masks:
                state.get(context.area).mask_selected = False

    def edit(self, context, ts):
        if self.action == "GROUP":
            from .dock import state

            ui = state.get(context.area) if context.area else None
            current = layer_stack.active(ts)
            # Ids of undone or deleted layers would group nothing; the active
            # layer stands in only for an empty selection, since a Ctrl-click
            # in the dock may deliberately leave it out.
            existing = {item.id for item in ts.layers}
            ids = {i for i in (ui.selected_layers if ui else ()) if i in existing} or (
                {current.id} if current else set()
            )
            folder = layer_stack.group(ts, ids)
            if ui:
                ui.selected_layers = {folder.id}
            return
        if self.action in {"FILL", "PAINT", "FOLDER", "TEXT"}:
            previous = layer_stack.active(ts)
            parent_id = (
                previous.parent_id
                if previous
                else ""
            )
            created = layer_stack.add(ts, 'FILL' if self.action == 'TEXT' else self.action, parent_id=parent_id)
            if self.action == 'TEXT':
                from ..utils.ids import new_id
                created.name = 'Text'
                mask = created.masks.add()
                mask.id, mask.name, mask.source = new_id(), 'Text', 'TEXT'
                mask.operation = 'MULTIPLY'
            if parent_id:
                next(item for item in ts.layers if item.id == parent_id).expanded = True
            if context.area:
                from .dock import state

                state.get(context.area).selected_layers = {created.id}
            from ..graph import mirror
            tree = mirror.find_tree(ts)
            if tree is not None:
                # The node appears on the next sync: select it then, exclusively,
                # so it can be placed immediately.
                mirror.request_select(tree, created.id)
            return
        layer = layer_stack.active(ts)
        if not layer:
            raise ValueError("Select a layer first.")
        if self.action == "UNGROUP":
            if layer.type != "FOLDER":
                raise ValueError("Select a group first.")
            layer_stack.remove(ts, ts.active_layer)
        elif self.action == "DELETE":
            from ..evaluation import anchors
            from .anchors import report_broken
            doomed = {layer.id}
            while True:
                grown = doomed | {item.id for item in ts.layers if item.parent_id in doomed}
                if grown == doomed:
                    break
                doomed = grown
            broken = [f"{owner.name} ({anchors.CONSUMER_LABELS[kind]})"
                      for item in ts.layers if item.id in doomed for identifier in anchors.owned(item)
                      for owner, kind, _ in anchors.consumers(ts, identifier) if owner.id not in doomed]
            layer_stack.remove_tree(ts, layer.id)
            report_broken(self, context, broken)
            from . import mask_view
            mask_view.on_layer_removed(ts)
        elif self.action == "DUPLICATE":
            layer_stack.duplicate(ts, ts.active_layer)
        elif self.action in {"UP", "DOWN"}:
            step(ts, layer, 1 if self.action == "UP" else -1)
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
        if getattr(item, "bypass", False):
            layout.label(text="", icon="RESTRICT_VIEW_ON")


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
        from ..core.layer_store import rna_owner
        stack = rna_owner(ts)   # the stack lives in the set's layer store
        if stack is not None:
            layout.template_list(
                "OP_UL_layers", "", stack, "layers", ts, "active_layer", rows=6, sort_reverse=True
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
