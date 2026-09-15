import bpy

from ..constants import CHANNEL_ITEMS
from ..targets import ITEMS as TARGET_ITEMS
from ..painting.brush_bridge import get_value, set_value
from .texture_set import OP_TextureSet
from ..ui.library_categories import CATEGORY_ITEMS, refresh


def target_changed(self, context):
    from ..targets import apply, get
    from ..core import material_manager
    target = get(self.target)
    for ts in self.texture_sets:
        if ts.export.follow_target:
            apply(target, ts.export)
        if self.engine_preview:
            material_manager.repair(ts)
    if self.engine_preview:
        from ..targets import preview
        preview.enter(self.id_data, self)


def engine_preview_changed(self, context):
    from ..core import material_manager
    from ..targets import preview
    scene = self.id_data
    if self.engine_preview:
        preview.enter(scene, self)
    else:
        preview.leave(scene, self)
    for ts in self.texture_sets:
        material_manager.repair(ts)


class OP_Project(bpy.types.PropertyGroup):
    id: bpy.props.StringProperty()
    # Target renderer (0.5.1): one choice configuring export conventions and the engine preview.
    target: bpy.props.EnumProperty(name="Target", default="BLENDER", update=target_changed,
                                   items=[(key, label, description) for key, label, description in TARGET_ITEMS])
    engine_preview: bpy.props.BoolProperty(name="Engine Preview", default=False, update=engine_preview_changed,
        description="Show the display material the way the target engine will compute it from the exported textures")
    engine_preview_quantize: bpy.props.BoolProperty(name="8-bit Textures", default=True, update=engine_preview_changed,
        description="Quantise colour, roughness and metallic to 8 bits (through sRGB for colour)")
    engine_preview_normal_reconstruct: bpy.props.BoolProperty(name="Reconstruct Normal Z", default=True, update=engine_preview_changed,
        description="Rebuild the normal's Z from X and Y as BC5 / normal-map compression does")
    engine_preview_ao_indirect: bpy.props.BoolProperty(name="AO Indirect Only", default=True, update=engine_preview_changed,
        description="Engines apply the AO map to indirect light: a directly lit viewport shows none of it")
    saved_view_transform: bpy.props.StringProperty()
    saved_look: bpy.props.StringProperty()
    export_directory: bpy.props.StringProperty(name="Export Directory", subtype="DIR_PATH",
                                               options={"PATH_SUPPORTS_BLEND_RELATIVE"})
    export_mesh: bpy.props.BoolProperty(name="Export Mesh", default=True,
                                        description="Also export the assigned objects in the target's mesh format")
    format_version: bpy.props.IntProperty(default=1)
    texture_sets: bpy.props.CollectionProperty(type=OP_TextureSet)
    active_texture_set: bpy.props.IntProperty(default=0)
    status: bpy.props.StringProperty()
    paint_image: bpy.props.PointerProperty(type=bpy.types.Image)
    paint_set_id: bpy.props.StringProperty()
    paint_layer_id: bpy.props.StringProperty()
    asset_category: bpy.props.EnumProperty(
        name="Assets",
        items=CATEGORY_ITEMS,
        default="ALL",
        update=refresh,
    )
    paint_channel: bpy.props.EnumProperty(name="Channel", items=CHANNEL_ITEMS, default="ROUGHNESS")
    blend_channel: bpy.props.EnumProperty(name="Channel", items=CHANNEL_ITEMS, default="BASE_COLOR")
    properties_channel: bpy.props.EnumProperty(
        name="Channel", items=CHANNEL_ITEMS, default="BASE_COLOR"
    )
    paint_mask: bpy.props.BoolProperty()
    paint_mask_id: bpy.props.StringProperty()
    paint_target_channel: bpy.props.StringProperty()
    paint_object: bpy.props.PointerProperty(type=bpy.types.Object)
    paint_face_selection: bpy.props.StringProperty()
    paint_original_face_mask: bpy.props.BoolProperty()
    paint_value: bpy.props.FloatProperty(
        name="Paint Value", min=0, max=1, get=get_value, set=set_value
    )
