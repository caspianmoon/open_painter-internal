import bpy

from ..core.project_manager import active_set, require_set
from ..painting import brush_bridge, paint_session
from .operators import SafeOperator


class OP_OT_paint(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.paint"
    bl_label = "Paint Selected Channel"
    bl_description = "Paint the selected layer image with Blender’s native brush"
    bl_options = {"REGISTER", "UNDO"}
    channel: bpy.props.StringProperty()
    mask: bpy.props.BoolProperty(default=False)
    stop: bpy.props.BoolProperty(default=False)

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
        layout.prop(context.scene.open_painter, "paint_channel", text="Channel")
        row = layout.row(align=True)
        row.operator("open_painter.paint", icon="BRUSH_DATA")
        row.operator("open_painter.paint", text="Finish").stop = True
        brush_bridge.draw(layout, context)
