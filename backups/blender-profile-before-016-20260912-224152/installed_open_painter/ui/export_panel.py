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
    bl_label = "Export Textures"
    bl_parent_id = "OP_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Painter"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        ts = active_set(context)
        layout = self.layout
        if not ts:
            return
        settings = ts.export
        for key in (
            "directory",
            "preset",
            "resolution",
            "file_format",
            "bit_depth",
            "prefix",
            "suffix",
        ):
            layout.prop(settings, key)
        if settings.preset == "CUSTOM":
            for c in "rgba":
                layout.prop(settings, "custom_" + c)
        elif settings.preset == "UNREAL":
            layout.label(text="ORM: R=AO · G=Roughness · B=Metallic")
            layout.label(text="Normal: DirectX (green inverted)")
        elif settings.preset == "UNITY":
            layout.label(text="MetallicSmoothness: R=Metal · A=Smooth")
        layout.operator("open_painter.export", icon="EXPORT")
