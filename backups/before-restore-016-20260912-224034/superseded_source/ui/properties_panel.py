import bpy

from ..core.image_manager import create
from ..core.layer_stack import active, invalidate
from ..core.project_manager import active_set, require_set
from ..utils.ids import new_id
from .operators import SafeOperator


class OP_OT_mask(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.mask"
    bl_label = "Edit Mask"
    bl_options = {"REGISTER", "UNDO"}
    action: bpy.props.EnumProperty(
        items=[(x, x.title(), "") for x in ("ADD", "DELETE", "UP", "DOWN")]
    )
    source: bpy.props.EnumProperty(
        items=[(x, x.title(), "") for x in ("PAINT", "BAKED", "GENERATOR", "NOISE")]
    )

    initial_value: bpy.props.FloatProperty(default=1, min=0, max=1)

    def run(self, context):
        ts = require_set(context)
        layer = active(ts)
        if layer is None:
            raise ValueError("Select a layer or folder first.")
        if self.action == "ADD":
            mask = layer.masks.add()
            mask.id, mask.source = new_id(), self.source
            mask.name = self.source.title() + " Mask"
            if self.source == "PAINT":
                mask.image = create(
                    f"{ts.name}_{layer.name}_Mask",
                    int(ts.resolution),
                    (self.initial_value, self.initial_value, self.initial_value, 1),
                )
                mask.name = "White Mask" if self.initial_value else "Black Mask"
            layer.active_mask = len(layer.masks) - 1
        elif layer.masks:
            i = max(0, min(layer.active_mask, len(layer.masks) - 1))
            if self.action == "DELETE":
                layer.masks.remove(i)
                layer.active_mask = max(0, i - 1)
            else:
                j = max(0, min(len(layer.masks) - 1, i + (-1 if self.action == "UP" else 1)))
                layer.masks.move(i, j)
                layer.active_mask = j
        invalidate(ts)
        from ..painting.paint_session import synchronize
        from .dock import state

        if context.area:
            ui = state.get(context.area)
            ui.mask_selected = bool(layer.masks)
            ui.properties_scroll = 0
        synchronize(context, mask=True)


class OP_UL_masks(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(item, "enabled", text="")
        layout.prop(item, "name", text="", emboss=False, icon="MOD_MASK")


class OP_PT_masks(bpy.types.Panel):
    bl_order = 2
    bl_label = "Masks"
    bl_parent_id = "OP_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Painter"

    def draw(self, context):
        ts = active_set(context)
        layer = active(ts) if ts else None
        layout = self.layout
        if not layer:
            layout.label(text="Select a layer to add a mask.")
            return
        row = layout.row(align=True)
        for source in ("PAINT", "BAKED", "GENERATOR", "NOISE"):
            op = row.operator("open_painter.mask", text=source.title())
            op.action, op.source = "ADD", source
        layout.template_list("OP_UL_masks", "", layer, "masks", layer, "active_mask", rows=2)
        row = layout.row(align=True)
        for action, icon in (("UP", "TRIA_UP"), ("DOWN", "TRIA_DOWN"), ("DELETE", "TRASH")):
            row.operator("open_painter.mask", text="", icon=icon).action = action
        if not layer.masks:
            return
        mask = layer.masks[min(layer.active_mask, len(layer.masks) - 1)]
        if mask.source == "PAINT":
            layout.template_ID(mask, "image")
            if hasattr(bpy.types, "OP_OT_paint"):
                layout.operator(
                    "open_painter.paint", text="Paint Mask", icon="BRUSH_DATA"
                ).mask = True
        elif mask.source == "BAKED":
            layout.prop(mask, "map_type")
        elif mask.source == "GENERATOR":
            layout.prop(mask, "generator")
        if mask.source in {"GENERATOR", "NOISE"}:
            for name in ("amount", "contrast", "noise_scale", "noise_strength", "seed"):
                layout.prop(mask, name)
        for name in ("operation", "invert", "black", "white", "strength"):
            layout.prop(mask, name)


class OP_OT_link_projection(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.link_projection"
    bl_label = "Apply Projection to All Channels"
    bl_options = {"REGISTER", "UNDO"}

    def run(self, context):
        from ..core.layer_stack import channel_for

        layer = active(require_set(context))
        if not layer or layer.type != "FILL":
            raise ValueError("Select a fill layer.")
        source = channel_for(layer, context.scene.open_painter.blend_channel)
        for prop in layer.channels:
            if prop != source:
                for key in (
                    "projection",
                    "filtering",
                    "tiling",
                    "scale",
                    "rotation",
                    "offset",
                    "triplanar_hardness",
                ):
                    setattr(prop, key, getattr(source, key))


class OP_OT_bitmap_mask(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.bitmap_mask"
    bl_label = "Add Bitmap Mask"
    bl_options = {"REGISTER", "UNDO"}
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(
        default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.exr;*.tga", options={"HIDDEN"}
    )

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def run(self, context):
        from ..constants import OWNER

        ts = require_set(context)
        layer = active(ts)
        if not layer:
            raise ValueError("Select a layer first.")
        image = bpy.data.images.load(self.filepath, check_existing=False)
        image[OWNER] = True
        image.colorspace_settings.name = "Non-Color"
        image.use_fake_user = True
        mask = layer.masks.add()
        mask.id, mask.name, mask.source, mask.image = new_id(), "Bitmap Mask", "PAINT", image
        layer.active_mask = len(layer.masks) - 1
        invalidate(ts)
        if context.area:
            from .dock import state

            state.get(context.area).mask_selected = True

        from ..painting.paint_session import synchronize

        synchronize(context, mask=True)
