import bpy

from ..constants import CHANNEL_ITEMS, RESOLUTIONS

PACK_ITEMS = CHANNEL_ITEMS + [
    ("ONE_MINUS_ROUGHNESS", "Smoothness", "1 minus Roughness"),
    ("ZERO", "Zero", ""),
    ("ONE", "One", ""),
]


class OP_ExportSettings(bpy.types.PropertyGroup):
    directory: bpy.props.StringProperty(
        name="Output Directory", subtype="DIR_PATH", options={"PATH_SUPPORTS_BLEND_RELATIVE"}
    )
    resolution: bpy.props.EnumProperty(
        name="Resolution", items=[("SET", "Texture Set", "")] + RESOLUTIONS, default="SET"
    )
    preset: bpy.props.EnumProperty(
        name="Preset",
        items=[
            ("BLENDER", "Blender", "Six separate channels"),
            ("UNREAL", "Unreal Engine 5", "DirectX normal and RGB=AO/Roughness/Metallic"),
            ("UNITY", "Unity Standard Metallic", "R=Metallic, A=Smoothness; OpenGL normal"),
            ("CUSTOM", "Custom RGBA", "Pack channel values into RGBA"),
        ],
    )
    file_format: bpy.props.EnumProperty(
        name="Format", items=[("PNG", "PNG", ""), ("TIFF", "TIFF", "")]
    )
    bit_depth: bpy.props.EnumProperty(
        name="Bit Depth", items=[("8", "8 bit", ""), ("16", "16 bit", "")], default="8"
    )
    prefix: bpy.props.StringProperty(name="Prefix")
    suffix: bpy.props.StringProperty(name="Suffix")
    custom_r: bpy.props.EnumProperty(name="R", items=PACK_ITEMS, default="AO")
    custom_g: bpy.props.EnumProperty(name="G", items=PACK_ITEMS, default="ROUGHNESS")
    custom_b: bpy.props.EnumProperty(name="B", items=PACK_ITEMS, default="METALLIC")
    custom_a: bpy.props.EnumProperty(name="A", items=PACK_ITEMS, default="ONE")
