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
        items=[(x, x.title(), "") for x in ("ADD", "DELETE", "UP", "DOWN", "BAKE", 'ANALYZE', 'FREEZE')]
    )
    black: bpy.props.BoolProperty(name="Black Mask", default=False)
    source: bpy.props.EnumProperty(
        items=[(x, x.title(), "") for x in ("PAINT", "FILL", "BAKED", "GENERATOR", "NOISE", "PROCEDURAL", "FILTER", 'SCENE', 'REFERENCE')]
    )

    def run(self, context):
        ts = require_set(context)
        layer = active(ts)
        if layer is None:
            raise ValueError("Select a layer or folder first.")
        if self.action in {'ANALYZE','FREEZE'}:
            mask = layer.masks[layer.active_mask]
            from ..generators.scene_fields import update
            if self.action == 'ANALYZE':
                update(ts, mask)
                mask.influence.update_serial += 1
            else:
                if not mask.influence.image:
                    update(ts,mask)
                mask.influence.mode = 'FROZEN'
        elif self.action == "BAKE":
            if not layer.masks:
                raise ValueError("Select a layer with a mask first.")
            from ..evaluation.mask_eval import bake_mask_texture

            image = bake_mask_texture(ts, layer)
            # Commit only after evaluation and packing have succeeded.
            layer.masks.clear()
            mask = layer.masks.add()
            mask.id, mask.name, mask.source = new_id(), "Baked Mask", "PAINT"
            mask.image = image
            layer.active_mask = 0
        elif self.action == "ADD":
            mask = layer.masks.add()
            mask.id, mask.source = new_id(), self.source
            mask.name = self.source.title() + " Mask"
            if self.source == "FILTER":
                mask.name = "Levels"
                mask.effect.kind = "LEVELS"
            elif self.source == "PROCEDURAL":
                mask.procedural = "FBM"
                mask.name = "FBM"
            if self.source in {"PAINT", "FILL"}:
                mask.image = create(
                    f"{ts.name}_{layer.name}_Mask",
                    int(ts.resolution) if self.source == "PAINT" else 32,
                    (0, 0, 0, 1) if self.black else (1, 1, 1, 1),
                )
                mask.image.pack()
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
        row.menu("OP_MT_native_masks", text="Add Mask Effect", icon="ADD")
        op = row.operator("open_painter.choose_filter", text="Filter", icon="MODIFIER")
        op.target, op.create = "MASK", True
        layout.template_list("OP_UL_masks", "", layer, "masks", layer, "active_mask", rows=2)
        row = layout.row(align=True)
        for action, icon in (("UP", "TRIA_UP"), ("DOWN", "TRIA_DOWN"), ("DELETE", "TRASH")):
            row.operator("open_painter.mask", text="", icon=icon).action = action
        if not layer.masks:
            return
        mask = layer.masks[min(layer.active_mask, len(layer.masks) - 1)]
        layout.operator(
            "open_painter.mask", text="Bake Mask Stack to Texture", icon="IMAGE_DATA"
        ).action = "BAKE"
        layout.prop(mask, "source", text="Source")
        if mask.source == 'REFERENCE':
            source = next((item for item in ts.layers if item.id == mask.reference_layer),None)
            layout.operator('open_painter.reference_source',text=source.name if source else 'Choose Source Layer',icon='LINKED')
            layout.prop(mask,'reference_channel')
            layout.prop(mask,'reference_stage')
            layout.label(text='Source layer input, before stack blending')
        elif mask.source == 'SCENE':
            field = mask.influence
            for name in ('kind','mode','resolution','object','collection','direction','distance','bounds','attribute'):
                layout.prop(field,name)
            row = layout.row(align=True)
            row.operator('open_painter.mask',text='Update Analysis').action = 'ANALYZE'
            row.operator('open_painter.mask',text='Freeze Analysis').action = 'FREEZE'
            layout.label(text='Sampled UV field; authored textures unchanged')
            if field.error:
                layout.label(text=field.error,icon='ERROR')
        elif mask.source == "FILL":
            layout.template_ID(mask, "image")
            for name in ("projection", "filtering", "tiling", "scale", "rotation", "offset"):
                layout.prop(mask, name)
            if mask.projection == "TRIPLANAR":
                layout.prop(mask, "triplanar_hardness")
        elif mask.source == "PAINT":
            layout.template_ID(mask, "image")
            if hasattr(bpy.types, "OP_OT_paint"):
                layout.operator(
                    "open_painter.paint", text="Paint Mask", icon="BRUSH_DATA"
                ).mask = True
        elif mask.source == "BAKED":
            layout.prop(mask, "map_type")
        elif mask.source == "PROCEDURAL":
            op = layout.operator("open_painter.choose_procedural", text=mask.procedural.replace("_", " ").title(), icon="TEXTURE")
            op.target, op.create = "MASK", False
            for name in ("projection", "tiling", "noise_scale", "seed", "scale", "rotation", "offset"):
                layout.prop(mask, name)
            if mask.projection == "TRIPLANAR":
                layout.prop(mask,"triplanar_hardness")
        elif mask.source == "FILTER":
            from .texture_operations import draw_effect_settings
            op = layout.operator("open_painter.choose_filter", text=mask.effect.kind.replace("_", " ").title(), icon="MODIFIER")
            op.target, op.create = "MASK", False
            layout.prop(mask.effect, "enabled", text="Enabled")
            draw_effect_settings(layout, mask.effect, "MASK")
            for name in ("invert", "black", "white", "strength"):
                layout.prop(mask, name)
            return
        elif mask.source == "GENERATOR":
            layout.prop(mask, "generator")
            if mask.generator in {"MATERIAL_ID","OBJECT_ID","UV_ISLAND"}:
                row = layout.row(align=True)
                row.prop(mask,"id_color",text="Selected ID Color")
                row.operator("open_painter.choose_baked_id", text="", icon="VIEWZOOM")
            if mask.generator == "EDGE_WEAR":
                from ..constants import OWNER

                image = next(
                    (m.image for m in ts.baked_maps if m.kind == "CURVATURE" and m.image), None
                )
                if (
                    image
                    and image.get(OWNER)
                    and "BakedCurvature" in image.name
                    and image.get("open_painter_curvature_method") != "GEOMETRIC_CREASES_V2"
                ):
                    layout.label(text="Crease-only curvature: rebake edge map for smooth edges.", icon="ERROR")
                layout.prop(ts.bake, "curvature_angle")
                layout.prop(ts.bake, "curvature_width")
                layout.prop(ts.bake, "mean_curvature_distance")
                layout.operator(
                    "open_painter.bake", text="Rebake Edge Map", icon="IMAGE_DATA"
                ).action = "CURVATURE"

        if mask.source in {"GENERATOR", "NOISE"}:
            for name in ("amount", "contrast", "noise_scale", "noise_strength", "seed"):
                layout.prop(mask, name)
        for name in ("operation", "invert", "black", "white", "strength"):
            layout.prop(mask, name)
