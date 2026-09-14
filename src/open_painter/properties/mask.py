import bpy
from .effect import OP_TextureEffect
from .influencer import OP_Influencer
from ..constants import CHANNEL_ITEMS


def changed(self, context):
    from .layer import changed as invalidate

    invalidate(self, context)


class OP_Mask(bpy.types.PropertyGroup):
    reference_layer: bpy.props.StringProperty(name='Source Layer ID', update=changed)
    reference_mask: bpy.props.StringProperty(name='Through Mask Effect ID', update=changed)
    reference_channel: bpy.props.EnumProperty(name='Published Output', update=changed,
        items=[('MASK','Mask Stack','Source mask stack, independent of layer opacity')] + CHANNEL_ITEMS)
    reference_stage: bpy.props.EnumProperty(name='Stage', default='POST_FILTER', update=changed,
        items=[('PRE_FILTER','Before Filters','Omit source filters'),('POST_FILTER','After Filters','Include source filters')])
    # Anchor ids take precedence over the legacy layer/mask/stage reference fields.
    reference_anchor: bpy.props.StringProperty(name='Anchor', update=changed,
        description='Anchor point whose published result this mask reads')
    detail_height_anchor: bpy.props.StringProperty(name='Micro Height', update=changed,
        description='Anchor point supplying height details the generator wears and soils')
    detail_normal_anchor: bpy.props.StringProperty(name='Micro Normal', update=changed,
        description='Anchor point supplying a tangent normal whose relief the generator follows')
    detail_height_strength: bpy.props.FloatProperty(name='Height Details Intensity', default=1, min=0,
        soft_max=4, max=10, update=changed, description='Strength of the Micro Height convexity and cavities')
    detail_normal_strength: bpy.props.FloatProperty(name='Normal Details Intensity', default=1, min=0,
        soft_max=4, max=10, update=changed, description='Strength of the Micro Normal convexity and cavities')
    detail_radius: bpy.props.FloatProperty(name='Micro Radius', default=2, min=.5, max=16, update=changed,
        description='Texel radius over which micro details are measured; also the micro cavity reach')
    influence: bpy.props.PointerProperty(type=OP_Influencer)
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
            ('SCENE', 'Scene Field', 'Bounded UV analysis of explicit scene inputs'),
            ('REFERENCE', 'Published Input', 'Reference a layer mask or channel in this texture set'),
            ('ANCHOR', 'Anchor Point', 'Publish the mask result at this position for other layers'),
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
        items=[(x, x.replace("_", " ").title(), "") for x in ("AO", "CURVATURE", "NORMAL", "POSITION", "WORLD_NORMAL", "OBJECT_NORMAL", "THICKNESS", "MATERIAL_ID", "OBJECT_ID", "UV_ISLAND", "UV_BORDER", "MEAN_CURVATURE", "SELECTION", "BEVEL_NORMAL", "BEVEL_MASK")],
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
    edge_width: bpy.props.FloatProperty(
        name="Edge Width", default=1.0, min=0.05, max=1.0, subtype="FACTOR", update=changed,
        description="How far edge wear reaches from the edge, as a fraction of the baked Curvature Radius; "
                    "raise the radius and rebake for wider wear",
    )
    noise_scale: bpy.props.FloatProperty(
        name="Noise Scale", default=12, min=1, max=512, update=changed
    )
    noise_strength: bpy.props.FloatProperty(
        name="Noise Strength", default=0.2, min=0, max=1, update=changed
    )
    seed: bpy.props.IntProperty(name="Seed", default=1, min=0, update=changed)
