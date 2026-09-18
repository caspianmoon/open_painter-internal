import bpy

from ..core.project_manager import active_set, require_set
from ..painting import brush_bridge, paint_session
from .operators import SafeOperator


class OP_OT_paint(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.paint"
    bl_label = "Paint Selected Channel"
    bl_description = "Paint the selected layer image with Blender’s native brush"
    bl_options = {"REGISTER", "UNDO"}
    # SKIP_SAVE: a button click stores the properties it set as the operator's last-used
    # values, and later clicks that leave them unset inherit them (Finish would turn every
    # later Paint button into a Finish).
    channel: bpy.props.StringProperty(options={"SKIP_SAVE"})
    mask: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})
    stop: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})

    def run(self, context):
        if self.stop:
            paint_session.finish(context)
        else:
            if self.channel:
                context.scene.open_painter.paint_channel = self.channel
            project = context.scene.open_painter
            if project.paint_all_sets and not self.mask:
                paint_session.begin_multi(context, require_set(context), project.paint_channel)
            else:
                paint_session.begin(context, require_set(context), project.paint_channel, self.mask)


class OP_PT_paint(bpy.types.Panel):
    bl_order = 1
    bl_label = "Paint · Brushes and Alphas"
    bl_parent_id = "OP_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Painter"

    def draw(self, context):
        layout = self.layout
        if not active_set(context):
            return
        layout.prop(context.scene.open_painter, "paint_channel", text="Paint Channel")
        row = layout.row(align=True)
        row.operator("open_painter.paint", icon="BRUSH_DATA")
        row.operator("open_painter.paint", text="Finish").stop = True
        brush_bridge.draw(layout, context)
