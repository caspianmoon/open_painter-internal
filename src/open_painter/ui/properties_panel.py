import bpy

from ..core.image_manager import create
from ..core.layer_stack import active, invalidate
from ..core.project_manager import active_set, require_set
from ..utils.ids import new_id
from .operators import SafeOperator


def draw_mesh_selector(layout, ts, mask):
    """Mesh mask (0.9.2): pick what Blender structure drives the coverage."""
    selector = mask.mesh
    layout.prop(selector, 'kind')
    kind = selector.kind
    if kind in {'OBJECT', 'UV_ISLAND'}:
        layout.prop(selector, 'object', text='Object' if kind == 'OBJECT' else 'On Object')
        if selector.object is None and selector.object_name:
            layout.label(text=f"'{selector.object_name}' is not in this file", icon='ERROR')
    if kind == 'COLLECTION':
        layout.prop(selector, 'collection')
        layout.prop(selector, 'include_children')
        collection = selector.resolve('collection')
        if collection is not None:
            assigned = {r.object for r in ts.assignments if r.object}
            members = [o for o in (collection.all_objects if selector.include_children else collection.objects)
                       if o in assigned]
            layout.label(text=f'{len(members)} assigned object(s) in the collection', icon='OUTLINER_COLLECTION')
        elif selector.collection_name:
            layout.label(text=f"'{selector.collection_name}' is not in this file", icon='ERROR')
    elif kind == 'MATERIAL':
        layout.prop(selector, 'material')
    elif kind in {'VERTEX_GROUP', 'ATTRIBUTE'}:
        objects = [r.object for r in ts.assignments if r.object]
        if kind == 'VERTEX_GROUP' and objects:
            layout.prop_search(selector, 'attribute', objects[0], 'vertex_groups', text='Group')
        elif objects:
            layout.prop_search(selector, 'attribute', objects[0].data, 'attributes', text='Attribute')
        else:
            layout.prop(selector, 'attribute')
    elif kind == 'FACE_SET':
        layout.prop(selector, 'face_set')
    elif kind == 'UV_ISLAND':
        layout.prop(selector, 'island')
        layout.label(text='-1 uses the islands of the selected faces', icon='INFO')
    elif kind == 'NORMAL_DIRECTION':
        layout.prop(selector, 'direction')
        layout.prop(selector, 'angle')
        layout.prop(selector, 'softness')
    elif kind == 'FACE_SELECTION':
        layout.label(text='Follows the face selection of the assigned meshes', icon='RESTRICT_SELECT_OFF')
    layout.label(text='Exact through the render UVs; follows scene changes live', icon='INFO')


class OP_OT_mask(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.mask"
    bl_label = "Edit Mask"
    bl_options = {"REGISTER", "UNDO"}
    action: bpy.props.EnumProperty(
        items=[(x, x.title(), "") for x in ("ADD", "DELETE", "UP", "DOWN", "BAKE", 'ANALYZE', 'FREEZE')]
    )
    black: bpy.props.BoolProperty(name="Black Mask", default=False)
    source: bpy.props.EnumProperty(
        items=[(x, x.title(), "") for x in ("PAINT", "FILL", "BAKED", "GENERATOR", "NOISE", "PROCEDURAL", "FILTER", 'SCENE', 'REFERENCE', 'MESH')]
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
            from .anchors import broken_labels, report_broken

            image = bake_mask_texture(ts, layer)
            broken = broken_labels(ts, [m.id for m in layer.masks if m.source == "ANCHOR"])
            # Commit only after evaluation and packing have succeeded.
            layer.masks.clear()
            report_broken(self, context, broken)
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
            elif self.source == 'MESH':
                mask.name = 'Mesh Mask'
                mask.mesh.kind = 'COLLECTION'
                mask.mesh.object = next((r.object for r in ts.assignments if r.object == context.object), None)
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
                from .anchors import broken_labels, report_broken
                broken = broken_labels(ts, [layer.masks[i].id] if layer.masks[i].source == "ANCHOR" else [])
                layer.masks.remove(i)
                layer.active_mask = max(0, i - 1)
                report_broken(self, context, broken)
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
        if item.source == "ANCHOR":
            layout.label(text="", icon="BLANK1")
            layout.prop(item, "name", text="", emboss=False, icon="ANCHOR_CENTER")
            return
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
        if mask.source == 'ANCHOR':
            from .anchors import draw_anchor_settings
            draw_anchor_settings(layout, ts, mask)
            layout.label(text='Publishes the mask result up to here; changes nothing itself', icon='INFO')
            return
        if mask.source == 'REFERENCE':
            from .anchors import draw_anchor_field, draw_anchor_channel
            anchor = draw_anchor_field(layout, ts, 'REFERENCE_MASK', mask.reference_anchor, text='Anchor')
            if anchor is not None:
                draw_anchor_channel(layout, anchor, mask)
            if not mask.reference_anchor:
                source = next((item for item in ts.layers if item.id == mask.reference_layer),None)
                layout.label(text='Legacy layer source')
                layout.operator('open_painter.reference_source',text=source.name if source else 'Choose Source Layer',icon='LINKED')
                layout.prop(mask,'reference_channel')
                layout.prop(mask,'reference_stage')
            layout.label(text='Source layer input, before stack blending')
        elif mask.source == 'MESH':
            draw_mesh_selector(layout, ts, mask)
        elif mask.source == 'SCENE':
            field = mask.influence
            layout.menu('OP_MT_scene_field_presets', text='Presets', icon='PRESET')
            layout.prop(field, 'kind')
            layout.prop(field, 'mode')
            layout.prop(field, 'resolution')
            kind = field.kind
            if kind in {'DISTANCE', 'CONTACT', 'EXPOSURE', 'PROXIMITY'}:
                layout.prop(field, 'scope')
                if field.scope == 'SCENE':
                    layout.prop(field, 'exclude_collection')
                    layout.prop(field, 'max_objects')
                else:
                    layout.prop(field, 'object')
                    layout.prop(field, 'collection')
                layout.label(text='Per-object radius: custom property open_painter_field_radius', icon='INFO')
            elif kind in {'HEIGHT', 'HEAT', 'MOISTURE', 'SOOT'}:
                layout.prop(field, 'object')
            elif kind == 'GROUND':
                layout.prop(field, 'ground_object')
            if kind in {'UP', 'DOWN', 'HEIGHT', 'WORLD_HEIGHT', 'GROUND', 'EXPOSURE', 'SOOT'}:
                layout.prop(field, 'direction')
            if kind in {'HEIGHT', 'WORLD_HEIGHT'}:
                layout.prop(field, 'bounds')
            if kind in {'DISTANCE', 'CONTACT', 'PROXIMITY', 'EXPOSURE', 'HEAT', 'MOISTURE', 'SOOT', 'GROUND',
                        'CONVEXITY', 'CONCAVITY', 'EDGE'}:
                layout.prop(field, 'distance', text='Reach' if kind in {'CONVEXITY', 'CONCAVITY', 'EDGE'} else 'Distance / Radius')
            if kind in {'ATTRIBUTE', 'VERTEX_GROUP'}:
                layout.prop(field, 'attribute')
            layout.prop(field, 'falloff')
            row = layout.row(align=True)
            row.operator('open_painter.mask',text='Update Analysis').action = 'ANALYZE'
            row.operator('open_painter.mask',text='Freeze Analysis').action = 'FREEZE'
            layout.label(text='Sampled UV field; authored textures unchanged')
            if field.error:
                layout.label(text=field.error, icon='TIME' if field.error.startswith('Analysing') else 'ERROR')
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
        elif mask.source == 'STROKES':
            from ..painting import strokes as stroke_records
            keys = {item.stroke_key: item.name for other in context.scene.open_painter.texture_sets
                    for item in other.layers if item.stroke_key}
            if mask.stroke_key:
                count = len(stroke_records.strokes_for(context.scene.open_painter, mask.stroke_key))
                layout.label(text=f'{count} strokes of "{keys.get(mask.stroke_key, "?")}"', icon='CURVE_BEZCURVE')
            else:
                layout.label(text='No strokes linked yet', icon='INFO')
            row = layout.row(align=True)
            row.operator('open_painter.mask_strokes', text="This Layer's Strokes", icon='LINKED').action = 'OWN'
            row.operator('open_painter.mask_strokes', text='New Key', icon='ADD').action = 'NEW'
            row.operator('open_painter.mask_strokes', text='', icon='X').action = 'CLEAR'
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
            if mask.generator in {"EDGE_WEAR", "CHIPS"}:
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
                from ..baking.maps.curvature import edge_map_set_complete
                radius = ts.bake.mean_curvature_distance
                if edge_map_set_complete(ts):
                    layout.prop(mask, "edge_size", text=f"Edge Size (thin {radius * 50:.1f} cm ↔ extreme {radius * 400:.1f} cm)", slider=True)
                else:
                    layout.label(text="Rebake the edge maps for a live Edge Size control", icon="INFO")
                layout.prop(mask, "edge_width", text="Edge Width", slider=True)
                box = layout.box()
                box.label(text="Baked edge maps: thin, normal, wide, extreme", icon="IMAGE_DATA")
                box.prop(ts.bake, "mean_curvature_distance", text="Curvature Radius")
                box.prop(ts.bake, "curvature_angle")
                box.prop(ts.bake, "curvature_width")
                box.operator("open_painter.edge_map_refresh", text="Rebake Edge Map", icon="IMAGE_DATA")
            from ..generators.micro_details import KINDS as MICRO_KINDS
            if mask.generator in MICRO_KINDS:
                from .anchors import draw_anchor_field
                box = layout.box()
                box.label(text="Micro Details", icon="ANCHOR_CENTER")
                for target, field, strength, text in (
                        ("DETAIL_HEIGHT", "detail_height_anchor", "detail_height_strength", "Micro Height"),
                        ("DETAIL_NORMAL", "detail_normal_anchor", "detail_normal_strength", "Micro Normal")):
                    draw_anchor_field(box, ts, target, getattr(mask, field), text=text)
                    if getattr(mask, field):
                        box.prop(mask, strength, slider=True)
                if mask.detail_height_anchor or mask.detail_normal_anchor:
                    box.prop(mask, "detail_radius", text="Micro Radius (px)")

        if mask.source in {"GENERATOR", "NOISE"}:
            for name in ("amount", "contrast", "noise_scale", "noise_strength", "seed"):
                layout.prop(mask, name)
        for name in ("operation", "invert", "black", "white", "strength"):
            layout.prop(mask, name)
