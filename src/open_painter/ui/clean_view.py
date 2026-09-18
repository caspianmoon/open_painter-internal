"""Clean View and the retry action (UI plan phase 8).

Clean View hides the viewport's scene furniture — floor, axes, extras, relationship lines,
the selection outline and the text overlay — for the Painter area alone, remembers what it
changed in the screen (so the choice survives a save) and puts every value back on the
second press. Overlays as a whole, the brush cursor and the navigation gizmos are never
touched: painting and navigation keep their aids.
"""
import json

import bpy

KEY = "open_painter_clean_view"
OVERLAY_FIELDS = ("show_floor", "show_axis_x", "show_axis_y", "show_axis_z", "show_extras", "show_relationship_lines",
                  "show_outline_selected", "show_text", "show_cursor", "show_annotation", "show_bones")


def area_key(screen, area):
    # The area's index in its screen survives a save and reload; a pointer does not.
    return str(next((i for i, item in enumerate(screen.areas) if item == area), -1))


def saved(screen, area):
    """The overlay values Clean View replaced for this area, if it is on."""
    data = json.loads(screen.get(KEY, "{}"))
    return data.get(area_key(screen, area))


def active(context):
    area = getattr(context, "area", None)
    screen = getattr(context, "screen", None)
    return bool(area is not None and screen is not None and saved(screen, area))


class OP_OT_clean_view(bpy.types.Operator):
    bl_idname = "open_painter.clean_view"
    bl_label = "Clean View"
    bl_description = ("Hide the floor, axes, extras, relationship lines, selection outline and text overlay of this "
                      "Painter view; press again to restore exactly what was hidden")
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        from . import editor
        return context.area is not None and context.area.type == "VIEW_3D" and editor.is_area(context.area)

    def execute(self, context):
        area, screen = context.area, context.screen
        overlay = area.spaces.active.overlay
        data = json.loads(screen.get(KEY, "{}"))
        key = area_key(screen, area)
        if key in data:
            for field, value in data.pop(key).items():
                if hasattr(overlay, field):
                    setattr(overlay, field, value)
        else:
            before = {field: getattr(overlay, field) for field in OVERLAY_FIELDS if hasattr(overlay, field)}
            data[key] = before
            for field in before:
                setattr(overlay, field, False)
        screen[KEY] = json.dumps(data)
        area.tag_redraw()
        return {"FINISHED"}


class OP_OT_retry_evaluation(bpy.types.Operator):
    bl_idname = "open_painter.retry_evaluation"
    bl_label = "Retry"
    bl_description = "Ask the preview to try the failed step again (a failed isolated input or shader build)"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from ..core.project_manager import active_set
        from ..evaluation import cache
        ts = active_set(context)
        if ts is None:
            return {"CANCELLED"}
        cache.retry(context.scene, ts)
        context.scene.open_painter.status = "Retrying the preview…"
        if context.area is not None:
            context.area.tag_redraw()
        return {"FINISHED"}


CLASSES = (OP_OT_clean_view, OP_OT_retry_evaluation)
