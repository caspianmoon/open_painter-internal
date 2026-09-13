"""Native brush alpha import and paint-value actions."""

import bpy
from bpy_extras.io_utils import ImportHelper

from ..painting import alphas, brush_bridge
from .operators import SafeOperator


class OP_OT_import_alpha(SafeOperator, bpy.types.Operator, ImportHelper):
    bl_idname = "open_painter.import_alpha"
    bl_label = "Import Brush Alpha"
    bl_description = "Load and pack a grayscale image as the current brush mask"
    bl_options = {"REGISTER", "UNDO"}
    filter_glob: bpy.props.StringProperty(
        default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.exr;*.bmp", options={"HIDDEN"}
    )

    def run(self, context):
        texture = alphas.import_alpha(context, self.filepath)
        context.scene.open_painter.asset_category = "ALPHAS"
        self.report({"INFO"}, f"Brush alpha: {texture.name}")


class OP_OT_brush_action(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.brush_action"
    bl_label = "Paint Brush"
    bl_options = {"REGISTER", "UNDO"}
    action: bpy.props.EnumProperty(
        items=[
            (key, label, "")
            for key, label in (
                ("BLACK", "Paint Black"),
                ("WHITE", "Paint White"),
                ("CLEAR_ALPHA", "Remove Alpha"),
                ("ERASE", "Toggle Erase"),
            )
        ]
    )

    def run(self, context):
        brush = brush_bridge.ensure_brush(context)
        if self.action == "CLEAR_ALPHA":
            brush.mask_texture = None
        elif self.action == "ERASE":
            brush.blend = "MIX" if brush.blend == "ERASE_ALPHA" else "ERASE_ALPHA"
        else:
            context.scene.open_painter.paint_value = 0 if self.action == "BLACK" else 1
