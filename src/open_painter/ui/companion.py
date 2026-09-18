"""Open Inspector: the same inspector in a companion Properties area (UI plan phase 2).

The compact layout keeps a bounded layer list and a short inspector in one sidebar,
which scroll together. For longer sessions *Open Inspector* splits the Painter area
(or, when the artist chooses, reuses a Properties area already on the screen) and
shows the inspector's drawing functions in the Properties editor's Tool tab, so the
layer stack and the detailed controls scroll independently. The companion follows
the Painter session of its own screen (ui/selection.py) and never another window's.
Only the chosen area changes; Blender's own panels stay registered.
"""
import bpy

from . import selection

_companions = {}   # screen pointer -> dict(area=pointer, previous=type, created=bool)


def companion_for(screen):
    record = _companions.get(screen.as_pointer())
    if record is None:
        return None
    area = next((a for a in screen.areas if a.as_pointer() == record["area"]), None)
    if area is None or area.type != "PROPERTIES":
        _companions.pop(screen.as_pointer(), None)
        return None
    return area


class OP_PT_companion_inspector(bpy.types.Panel):
    """The Painter inspector, hosted by a Properties editor's Tool tab.

    The Properties editor's Tool tab shows the 3D viewport's *Tool* sidebar panels
    (Active Tool, Options, Workspace) in every mode and with any active object, so the
    inspector registers there and polls for a Properties editor: the viewport sidebars
    themselves keep their own Painter tab.
    """
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Tool"
    bl_label = "Painter Inspector"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        space = getattr(context, "space_data", None)
        return (space is not None and space.type == "PROPERTIES"
                and selection.session_area(context) is not None)

    def draw_header(self, context):
        target = selection.resolve(context)
        if target.layer is not None:
            self.layout.label(text=target.label, icon=target.icon)

    def draw(self, context):
        layout = self.layout
        target = selection.resolve(context)
        row = layout.row(align=True)
        selection.draw_breadcrumb(row, target)
        row.operator("open_painter.close_inspector", text="", icon="X")
        if target.layer is not None:
            name = layout.row(align=True)
            name.prop(target.layer, "name", text="")
        from . import inspector
        inspector.draw(layout, context, target)


class OP_OT_open_inspector(bpy.types.Operator):
    bl_idname = "open_painter.open_inspector"
    bl_label = "Open Inspector"
    bl_description = ("Show the inspector in a companion Properties area next to the Painter view, so the "
                      "layer stack and the detailed controls scroll independently")
    bl_options = {"REGISTER"}
    placement: bpy.props.EnumProperty(name="Where", default="SPLIT", items=[
        ("SPLIT", "Split This Area", "Split the Painter area and open a Properties editor in the new part"),
        ("EXISTING", "Existing Properties Area", "Use a Properties editor already on this screen")])

    def invoke(self, context, event):
        if context.area is not None and any(a.type == "PROPERTIES" for a in context.screen.areas):
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        self.layout.prop(self, "placement", expand=True)

    def execute(self, context):
        screen = context.screen
        painter = selection.session_area(context)
        if painter is None:
            self.report({"ERROR"}, "Open Painter in an area first.")
            return {"CANCELLED"}
        existing = companion_for(screen)
        if existing is not None:
            area, created = existing, _companions[screen.as_pointer()]["created"]
        elif self.placement == "EXISTING":
            area = next((a for a in screen.areas if a.type == "PROPERTIES"), None)
            if area is None:
                self.report({"ERROR"}, "This screen has no Properties area; choose Split This Area.")
                return {"CANCELLED"}
            created = False
        else:
            before = {a.as_pointer() for a in screen.areas}
            with context.temp_override(area=painter):
                bpy.ops.screen.area_split(direction="VERTICAL", factor=0.68)
            area = next((a for a in screen.areas if a.as_pointer() not in before), None)
            if area is None:
                self.report({"ERROR"}, "Blender could not split this area; make the Painter area larger.")
                return {"CANCELLED"}
            area.type = "PROPERTIES"
            created = True
        previous = _companions.get(screen.as_pointer(), {}).get("previous", area.type)
        _companions[screen.as_pointer()] = dict(area=area.as_pointer(), previous=previous, created=created)
        space = area.spaces.active
        try:
            space.context = "TOOL"
        except TypeError:
            pass
        area.tag_redraw()
        return {"FINISHED"}


class OP_OT_close_inspector(bpy.types.Operator):
    bl_idname = "open_painter.close_inspector"
    bl_label = "Close Inspector"
    bl_description = "Close the companion inspector area (an area you split is closed; a reused one is left as it is)"
    bl_options = {"REGISTER"}

    def execute(self, context):
        screen = context.screen
        record = _companions.pop(screen.as_pointer(), None)
        area = next((a for a in screen.areas if record and a.as_pointer() == record["area"]), None)
        if area is None:
            return {"CANCELLED"}
        if record["created"]:
            try:
                with context.temp_override(area=area):
                    bpy.ops.screen.area_close()
                return {"FINISHED"}
            except RuntimeError:
                pass   # a screen that cannot join (a single remaining area) keeps it as Properties
        area.tag_redraw()
        return {"FINISHED"}


def forget(screen_pointer=None):
    if screen_pointer is None:
        _companions.clear()
    else:
        _companions.pop(screen_pointer, None)


CLASSES = (OP_PT_companion_inspector, OP_OT_open_inspector, OP_OT_close_inspector)
