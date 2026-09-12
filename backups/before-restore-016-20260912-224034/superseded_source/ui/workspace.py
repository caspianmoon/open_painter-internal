"""Compatibility operator for projects made before the editor-selector entry."""

import bpy


class OP_OT_workspace(bpy.types.Operator):
    bl_idname = "open_painter.workspace"
    bl_label = "Open Painter"
    bl_description = "Open Painter in this area; no workspace switch"

    def execute(self, context):
        return bpy.ops.open_painter.open_editor()


def unregister():
    return None
