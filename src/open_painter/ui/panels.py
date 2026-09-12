import bpy

from ..core.project_manager import active_set


class OP_UL_texture_sets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(item, "name", text="", emboss=False, icon="MATERIAL")


class OP_UL_assignments(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(
            text=f"{item.object.name if item.object else item.object_name} · slot {item.slot_index + 1}",
            icon="MESH_DATA",
        )


class OP_PT_main(bpy.types.Panel):
    bl_label = "Open Painter"
    bl_idname = "OP_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Painter"

    @classmethod
    def poll(cls, context):
        from . import editor
        return not editor.is_area(context.area)

    def draw(self, context):
        layout = self.layout
        if context.workspace.name != "Open Painter":
            layout.operator("open_painter.open_editor", icon="BRUSH_DATA")
        project = context.scene.open_painter
        layout.template_list(
            "OP_UL_texture_sets", "", project, "texture_sets", project, "active_texture_set", rows=2
        )
        row = layout.row(align=True)
        row.operator("open_painter.texture_set", text="New", icon="ADD").action = "ADD"
        row.operator("open_painter.texture_set", text="Copy", icon="DUPLICATE").action = "DUPLICATE"
        row.operator("open_painter.texture_set", text="Remove", icon="REMOVE").action = "DELETE"
        ts = active_set(context)
        if not ts:
            layout.label(text="Create a texture set to begin.")
            return
        if not ts.assignments:
            layout.operator(
                "open_painter.texture_set", text="Assign Selected Mesh Slots", icon="MESH_DATA"
            ).action = "ASSIGN"
        if project.status:
            layout.label(text=project.status, icon="INFO")


class OP_PT_texture_set(bpy.types.Panel):
    bl_label = "Texture Set"
    bl_parent_id = "OP_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Painter"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 3

    def draw(self, context):
        layout = self.layout
        project = context.scene.open_painter
        ts = active_set(context)
        if not ts:
            return
        addon = context.preferences.addons.get(__package__.rsplit(".", 1)[0])
        if addon and addon.preferences.developer_mode:
            layout.label(text=f"ID: {ts.id}")
        layout.prop(ts, "name", text="Name")
        layout.prop(ts, "resolution")
        layout.prop(ts, "preview_ao")
        layout.prop(ts, "height_distance")
        row = layout.row(align=True)
        row.operator(
            "open_painter.texture_set", text="Refresh", icon="FILE_REFRESH"
        ).action = "REFRESH"
        row.operator("open_painter.texture_set", text="Repair Material").action = "REPAIR"
        layout.template_list(
            "OP_UL_assignments", "", ts, "assignments", ts, "active_assignment", rows=2
        )
        row = layout.row(align=True)
        row.operator("open_painter.texture_set", text="Assign Selected Slots").action = "ASSIGN"
        row.operator("open_painter.texture_set", text="Remove Slot").action = "UNASSIGN"
        layout.operator(
            "open_painter.texture_set", text="Validate Project", icon="CHECKMARK"
        ).action = "VALIDATE"
        if project.status:
            layout.label(text=project.status)
