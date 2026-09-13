import bpy
from .effect import OP_TextureEffect


def changed(self, context):
    from .layer import changed as invalidate

    invalidate(self, context)


class OP_Mask(bpy.types.PropertyGroup):
    id: bpy.props.StringProperty()
    name: bpy.props.StringProperty(default="Mask")
    enabled: bpy.props.BoolProperty(default=True, update=changed)
    source: bpy.props.EnumProperty(
        items=[
            ("PAINT", "Painted", "Paint a grayscale mask"),
            ("BAKED", "Baked Map", "Use a mesh map"),
            ("GENERATOR", "Generator", "Procedural mesh-aware mask"),
            ("NOISE", "Noise", "Procedural noise"),
            ("FILL", "Fill", "Project a grayscale texture onto the mesh"),
            ("PROCEDURAL", "Procedural", "Grayscale procedural texture"),
            ("FILTER", "Filter", "Filter the accumulated mask texture"),
        ],
        update=changed,
    )
    procedural: bpy.props.StringProperty(default="FBM", update=changed)
    effect: bpy.props.PointerProperty(type=OP_TextureEffect)
    id_color: bpy.props.FloatVectorProperty(size=3, subtype="COLOR", default=(1,1,1), min=0,max=1, update=changed)
    projection: bpy.props.EnumProperty(
        name="Projection",
        items=[
            ("UV", "UV", ""),
            ("TRIPLANAR", "Triplanar", "World-space projection rasterized through mesh UVs"),
        ],
        update=changed,
    )
    filtering: bpy.props.EnumProperty(
        name="Filtering",
        update=changed,
        items=[
            ("BILINEAR", "Bilinear", "Interpolate adjacent pixels"),
            ("NEAREST", "Nearest", "Keep hard pixel edges"),
        ],
    )
    tiling: bpy.props.EnumProperty(
        name="UV Wrap",
        update=changed,
        items=[
            ("REPEAT", "Repeat", "Repeat the texture outside its tile"),
            ("NONE", "None", "Transparent outside the original tile"),
        ],
    )
    triplanar_hardness: bpy.props.FloatProperty(
        name="Triplanar Hardness",
        default=4,
        min=1,
        max=64,
        update=changed,
    )
    scale: bpy.props.FloatVectorProperty(
        name="Scale", size=2, default=(1, 1), min=0.001, update=changed
    )
    rotation: bpy.props.FloatProperty(name="Rotation", subtype="ANGLE", update=changed)
    offset: bpy.props.FloatVectorProperty(name="Offset", size=2, update=changed)
    image: bpy.props.PointerProperty(type=bpy.types.Image, update=changed)
    map_type: bpy.props.EnumProperty(
        name="Map",
        items=[(x, x.title(), "") for x in ("AO", "CURVATURE", "NORMAL", "POSITION", "WORLD_NORMAL", "OBJECT_NORMAL", "THICKNESS", "MATERIAL_ID", "OBJECT_ID", "UV_ISLAND", "UV_BORDER")],
        update=changed,
    )
    generator: bpy.props.EnumProperty(
        items=[
            ("EDGE_WEAR", "Edge Wear", "Requires Curvature"),
            ("DIRT", "Dirt", "Requires AO and Curvature"),
            ("DUST", "Top Dust", "Requires baked World Normal"),
        ] + [(x,x.replace("_"," ").title(),"Texture generator") for x in ("CAVITY_DIRT","RUST","CHIPS","SCRATCHES","DRIPS","NOISE","WORLD_NORMAL","POSITION","THICKNESS","OBJECT_NORMAL","MATERIAL_ID","OBJECT_ID","UV_ISLAND","UV_BORDER","CURVATURE","AO")
        ],
        update=changed,
    )
    operation: bpy.props.EnumProperty(
        name="Combine",
        items=[
            ("MULTIPLY", "Multiply", ""),
            ("ADD", "Add", ""),
            ("NORMAL", "Normal", "Replace previous mask coverage according to strength"),
        ],
        update=changed,
    )
    invert: bpy.props.BoolProperty(name="Invert", update=changed)
    black: bpy.props.FloatProperty(name="Black Level", min=0, max=1, update=changed)
    white: bpy.props.FloatProperty(name="White Level", default=1, min=0, max=1, update=changed)
    strength: bpy.props.FloatProperty(name="Strength", default=1, min=0, max=1, update=changed)
    amount: bpy.props.FloatProperty(name="Amount", default=0.5, min=0, max=1, update=changed)
    contrast: bpy.props.FloatProperty(name="Contrast", default=2, min=0.01, max=20, update=changed)
    noise_scale: bpy.props.FloatProperty(
        name="Noise Scale", default=12, min=1, max=512, update=changed
    )
    noise_strength: bpy.props.FloatProperty(
        name="Noise Strength", default=0.2, min=0, max=1, update=changed
    )
    seed: bpy.props.IntProperty(name="Seed", default=1, min=0, update=changed)
