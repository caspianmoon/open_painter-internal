import bpy

from ..constants import CHANNEL_ITEMS
from ..painting.brush_bridge import get_value, set_value
from .texture_set import OP_TextureSet


class OP_Project(bpy.types.PropertyGroup):
    id: bpy.props.StringProperty()
    format_version: bpy.props.IntProperty(default=1)
    texture_sets: bpy.props.CollectionProperty(type=OP_TextureSet)
    active_texture_set: bpy.props.IntProperty(default=0)
    status: bpy.props.StringProperty()
    paint_image: bpy.props.PointerProperty(type=bpy.types.Image)
    paint_set_id: bpy.props.StringProperty()
    paint_layer_id: bpy.props.StringProperty()
    asset_category: bpy.props.EnumProperty(
        name="Assets",
        items=[("ALL", "All", ""), ("MATERIALS", "Materials", ""), ("MASKS", "Smart Masks", "")],
        default="ALL",
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
