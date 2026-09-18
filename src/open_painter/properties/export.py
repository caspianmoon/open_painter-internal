import bpy

from ..constants import CHANNEL_ITEMS, RESOLUTIONS

PACK_ITEMS = CHANNEL_ITEMS + [
    ("ONE_MINUS_ROUGHNESS", "Smoothness", "1 minus Roughness"),
    ("ZERO", "Zero", ""),
    ("ONE", "One", ""),
]


def follow_target_changed(self, context):
    project = getattr(self.id_data, "open_painter", None)
    if self.follow_target and project:
        # Switching it on adopts the target now; the set is not left on the settings it had.
        from ..targets import apply, get
        apply(get(project.target), self)


def padding_changed(self, context):
    self.padding_migrated = False    # the artist chose: the upgrade note has served


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
            ("BLENDER", "Blender", "Separate channels; normal includes height relief, height is also supplied for displacement"),
            ("UNREAL", "Unreal Engine 5", "DirectX normal and RGB=AO/Roughness/Metallic"),
            ("UNITY", "Unity Standard Metallic", "R=Metallic, A=Smoothness; OpenGL normal"),
            ("CUSTOM", "Custom RGBA", "Pack channel values into RGBA"),
            ('GODOT','Godot 4 ORM','ORM texture inputs, OpenGL normals'),
            ('GLTF','glTF 2 Metallic Roughness','Base color/opacity, ORM, OpenGL normals'),
            ('UNITY_URP','Unity URP Lit Metallic','Metallic alpha smoothness workflow'),
            ('UNITY_HDRP','Unity HDRP Lit','Metallic/AO/detail-mask/smoothness Mask Map'),
        ],
    )
    file_format: bpy.props.EnumProperty(
        name="Format", items=[("PNG", "PNG", ""), ("TIFF", "TIFF", "")]
    )
    bit_depth: bpy.props.EnumProperty(
        name="Bit Depth", items=[("8", "8 bit", ""), ("16", "16 bit", "")], default="8"
    )
    follow_target: bpy.props.BoolProperty(name="Follow Project Target", default=True, update=follow_target_changed,
        description="Take preset, format and bit depth from the project's Target renderer")
    prefix: bpy.props.StringProperty(name="Prefix")
    suffix: bpy.props.StringProperty(name="Suffix")
    padding_mode: bpy.props.EnumProperty(name="UV Padding", default="AUTOMATIC", update=padding_changed,
        items=[("AUTOMATIC", "Automatic", "A gutter sized for the export resolution: 4 px at 512 up to 64 px at 8K"),
               ("CUSTOM", "Custom", "A gutter of the given width in output pixels"),
               ("FILL", "Fill Empty Space", "Extend the nearest surface over every empty texel"),
               ("NONE", "None", "No gutter: for diagnostics and special workflows")],
        description="How far exported maps extend past their UV islands so filtering and mips stay clean")
    padding_pixels: bpy.props.IntProperty(name="Padding", default=16, min=0, max=64, subtype="PIXEL",
                                          description="Gutter width in output pixels (Custom)")
    padding_migrated: bpy.props.BoolProperty(default=False, options={"HIDDEN"},
        description="Automatic padding was set when this file was upgraded; changing the mode clears it")
    last_report: bpy.props.StringProperty(default="", options={"HIDDEN"}, description="What the last export did about gutters")
    last_report_detail: bpy.props.StringProperty(default="", options={"HIDDEN"})
    custom_r: bpy.props.EnumProperty(name="R", items=PACK_ITEMS, default="AO")
    custom_g: bpy.props.EnumProperty(name="G", items=PACK_ITEMS, default="ROUGHNESS")
    custom_b: bpy.props.EnumProperty(name="B", items=PACK_ITEMS, default="METALLIC")
    custom_a: bpy.props.EnumProperty(name="A", items=PACK_ITEMS, default="ONE")
