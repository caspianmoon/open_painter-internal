import bpy

from ..core.project_manager import active_set, require_set
from .operators import SafeOperator


class OP_OT_export(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.export"
    bl_label = "Export Textures"
    bl_description = "Export evaluated textures to a new set of files"

    def run(self, context):
        from ..export.exporter import export

        ts = require_set(context)
        paths = export(context, ts, ts.export)
        self.report({"INFO"}, f"Exported {len(paths)} textures to {paths[0].parent}")


class OP_PT_export(bpy.types.Panel):
    bl_order = 5
    bl_label = "Export"
    bl_parent_id = "OP_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Painter"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        if not active_set(context):
            return
        from .export_view import draw_view
        draw_view(self.layout, context, compact=True)
