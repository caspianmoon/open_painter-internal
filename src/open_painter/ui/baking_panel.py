import bpy

from ..core.project_manager import active_set, require_set
from .operators import SafeOperator


class OP_OT_bake(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.bake"
    bl_label = "Bake Selected Mesh Maps"
    bl_description = "Bake explicit high/low meshes with Cycles, then restore scene settings"
    bl_options = {"REGISTER", "UNDO"}
    action: bpy.props.EnumProperty(
        items=[
            ("BAKE", "Bake", ""),
            ("ADD_HIGH", "Add Selected High Poly", ""),
            ("REMOVE_HIGH", "Remove High Poly", ""),
            ("USE_MAPS", "Use Baked Maps", ""),
            (
                "CURVATURE",
                "Rebake Edge Map",
                "Bake target-mesh geometric creases into UV texture pixels",
            ),
        ]
    )

    def run(self, context):
        ts = require_set(context)
        if self.action == "CURVATURE":
            from ..baking.maps.curvature import bake_curvature
            from ..baking.bake_manager import register_map
            from ..core.layer_stack import invalidate

            if not any(ref.object for ref in ts.assignments):
                raise ValueError("Assign the target mesh before baking its edge map.")
            register_map(ts, "CURVATURE", bake_curvature(ts))
            invalidate(ts)
        elif self.action == "ADD_HIGH":
            for obj in context.selected_objects:
                if obj.type == "MESH" and not any(r.object == obj for r in ts.bake.high_objects):
                    ref = ts.bake.high_objects.add()
                    ref.object = obj
                    ref.name = obj.name
        elif self.action == "USE_MAPS":
            from ..core.layer_stack import add, channel_for

            if not ts.baked_maps:
                raise ValueError("Bake mesh maps first.")
            layer = add(ts, "FILL", "Baked Mesh Maps")
            for channel in layer.channels:
                channel.enabled = False
            for prop in ts.baked_maps:
                if prop.kind in {"NORMAL", "AO"} and prop.image:
                    channel = channel_for(layer, prop.kind)
                    channel.enabled = True
                    channel.image = prop.image
        elif self.action == "REMOVE_HIGH":
            if ts.bake.high_objects:
                ts.bake.high_objects.remove(min(ts.bake.active_high, len(ts.bake.high_objects) - 1))
        else:
            from ..baking.bake_manager import bake_selected

            context.window_manager.progress_begin(0, 1)
            try:
                bake_selected(context, ts)
            finally:
                context.window_manager.progress_end()
            self.report({"INFO"}, "Mesh maps baked. Add them to a fill layer or generator mask.")


class OP_UL_high(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(
            text=item.object.name if item.object else f"Missing: {item.name}", icon="MESH_DATA"
        )


class OP_PT_bake(bpy.types.Panel):
    bl_order = 4
    bl_label = "Bake Mesh Maps"
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
        settings = ts.bake
        layout.label(text=f"Low Poly: {len(ts.assignments)} assigned slots")
        layout.prop(settings, "selected_to_active")
        layout.template_list(
            "OP_UL_high", "", settings, "high_objects", settings, "active_high", rows=2
        )
        row = layout.row(align=True)
        row.operator("open_painter.bake", text="Add Selected High Poly").action = "ADD_HIGH"
        row.operator("open_painter.bake", text="", icon="REMOVE").action = "REMOVE_HIGH"
        layout.prop(ts, "resolution")
        for key in ("margin", "cage_mode", "ray_distance", "samples"):
            layout.prop(settings, key)
        if settings.cage_mode == "CUSTOM":
            layout.prop(settings, "cage_object")
        for key in ("normal", "ao", "curvature"):
            layout.prop(settings, key)
        if settings.curvature:
            layout.prop(settings, "curvature_angle")
            layout.prop(settings, "curvature_width")
            layout.label(text="Curvature uses target mesh geometry.")
            layout.operator(
                "open_painter.bake", text="Rebake Edge Map", icon="IMAGE_DATA"
            ).action = "CURVATURE"
        layout.operator("open_painter.bake", icon="RENDER_STILL").action = "BAKE"
        layout.operator(
            "open_painter.bake", text="Use Baked Normal + AO in Stack"
        ).action = "USE_MAPS"
        for prop in ts.baked_maps:
            layout.label(
                text=f"{prop.kind}: {'Ready' if prop.image else 'Missing'}", icon="IMAGE_DATA"
            )
