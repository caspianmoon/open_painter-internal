import bpy
from .effect import OP_TextureEffect
from .influencer import OP_Influencer
from .mesh_selector import OP_MeshSelector
from ..constants import CHANNEL_ITEMS


def changed(self, context):
    from .layer import changed as invalidate

    invalidate(self, context)
    from .channel import _resolve_physical
    _resolve_physical(self)


def numeric(self, context):
    """A uniform or resource-identity edit; graph structure is unchanged."""
    from .layer import numeric as invalidate

    invalidate(self, context)


def _edge_source_changed(self, context):
    if self.edge_source == 'LIVE':
        # A live edge is a scene field of kind EDGE analysed live on this mask's influence.
        field = self.influence
        # The resolution bump replaces OP_Influencer's generic default on the
        # first switch only: a field already set up as a live edge (a snapshot
        # restore replays edge_source after influence) keeps the chosen grid.
        fresh = field.kind != 'EDGE'
        if fresh:
            field.kind = 'EDGE'
        if field.mode != 'LIVE':
            field.mode = 'LIVE'
        if fresh and field.resolution == '64':
            field.resolution = '512'
    changed(self, context)


def physical_changed(self, context):
    from .channel import physical_changed as resolve
    resolve(self, context)



def _graph_poll(tree):
    """A Mask Graph (an Open Painter Graph tree marked as one), or an earlier version's shader group
    (or a Mask Graph saved as its own tree type, until it is converted on load)."""
    from ..procedural_graph import is_native
    return is_native(tree) or tree.bl_idname in {'ShaderNodeTree', 'OpenPainterMaskGraph'}

from .text import OP_TextSettings


class OP_Mask(bpy.types.PropertyGroup):
    text: bpy.props.PointerProperty(type=OP_TextSettings)
    # Opt-in metric coordinates shared with surface channels. Legacy controls
    # remain untouched; derived UI values are excluded from graph snapshots.
    material_mapping_version: bpy.props.IntProperty(default=0, min=0, max=1, update=changed)
    material_period_m: bpy.props.FloatProperty(name='Period (m)', default=.001, min=1e-9, precision=6, update=numeric)
    material_meters_per_unit: bpy.props.FloatProperty(default=1, min=1e-12, update=numeric)
    material_frame: bpy.props.EnumProperty(name='Material Frame', default='WORLD', update=changed,
        items=[('WORLD', 'World', ''), ('OBJECT', 'Object', ''), ('COMPONENT', 'Connected Member', '')])
    material_origin: bpy.props.FloatVectorProperty(size=3, update=numeric)
    material_axis_u: bpy.props.FloatVectorProperty(size=3, default=(1, 0, 0), update=numeric)
    material_axis_v: bpy.props.FloatVectorProperty(size=3, default=(0, 1, 0), update=numeric)
    material_image_extent_m: bpy.props.FloatVectorProperty(size=2, default=(.01, .01), min=1e-9, update=numeric)
    material_source_id: bpy.props.StringProperty(update=changed)
    material_provenance: bpy.props.StringProperty(description='Source of the explicit metre conversion')
    period_m: bpy.props.FloatProperty(name='Period (m)', precision=6, min=1e-9,
        get=lambda self: self.material_period_m if self.material_mapping_version else 0,
        set=lambda self, value: setattr(self, 'material_period_m', value))
    chip_radius_m: bpy.props.FloatProperty(name='Circular Chip Radius (m)', precision=6, min=1e-9,
        description='DOTS radius at 50% coverage before geometry clipping; not an irregular-chip size guarantee',
        get=lambda self: .28 * self.material_period_m if self.material_mapping_version and self.procedural == 'DOTS' else 0,
        set=lambda self, value: setattr(self, 'material_period_m', value / .28))
    surface_role: bpy.props.StringProperty(description="Stable native surface-effect role", update=changed)
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
        soft_max=4, max=10, update=numeric, description='Strength of the Micro Height convexity and cavities')
    detail_normal_strength: bpy.props.FloatProperty(name='Normal Details Intensity', default=1, min=0,
        soft_max=4, max=10, update=numeric, description='Strength of the Micro Normal convexity and cavities')
    detail_radius: bpy.props.FloatProperty(name='Micro Radius', default=2, min=.5, max=16, update=numeric,
        description='Texel radius over which micro details are measured; also the micro cavity reach')
    influence: bpy.props.PointerProperty(type=OP_Influencer)
    mesh: bpy.props.PointerProperty(type=OP_MeshSelector)
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
            ('STROKES', 'Vector Strokes', 'Coverage of the project\'s vector strokes with this mask\'s key'),
            ('MESH', 'Mesh Mask', 'Objects, collections, materials, vertex groups, face sets, selection, UV islands, normal direction or attributes of the assigned meshes'),
            ('GROUP', 'Mask Group', 'A nested mask stack: its members combine among themselves, then the group combines with the stack like any other source'),
            ('GRAPH', 'Custom Graph', 'A Mask Graph you author with Open Painter nodes; its Mask Output is the coverage'),
            ('TEXT', 'Text', 'Editable text rasterized with Blender fonts'),
        ],
        update=changed,
    )
    # Mask groups (0.9.3): members carry the id of their group and follow it in the list.
    parent_id: bpy.props.StringProperty(update=changed)
    node_group: bpy.props.PointerProperty(name='Mask Graph', type=bpy.types.NodeTree, update=changed,
        poll=lambda self, tree: _graph_poll(tree),
        description='The Mask Graph whose Mask Output drives the mask (a Blender shader group from an earlier '
                    'version still works: its first output, baked on the CPU)')
    stroke_key: bpy.props.StringProperty(update=changed, description='Key of the vector strokes this mask renders')
    procedural: bpy.props.StringProperty(default="FBM", update=changed)
    effect: bpy.props.PointerProperty(type=OP_TextureEffect)
    id_color: bpy.props.FloatVectorProperty(size=3, subtype="COLOR", default=(1,1,1), min=0,max=1, update=numeric)
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
        name="Scale", size=2, default=(1, 1), min=0.001, update=numeric
    )
    rotation: bpy.props.FloatProperty(name="Rotation", subtype="ANGLE", update=numeric)
    offset: bpy.props.FloatVectorProperty(name="Offset", size=2, update=numeric)
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
        ] + [(x,x.replace("_"," ").title(),"Texture generator") for x in ("CAVITY_DIRT","RUST","CHIPS","SCRATCHES","DRIPS","RUNOFF","NOISE","WORLD_NORMAL","POSITION","THICKNESS","OBJECT_NORMAL","MATERIAL_ID","OBJECT_ID","UV_ISLAND","UV_BORDER","CURVATURE","AO")
        ],
        update=changed,
    )
    operation: bpy.props.EnumProperty(
        name="Combine",
        items=[
            ("MULTIPLY", "Multiply", ""),
            ("ADD", "Add", ""),
            ("NORMAL", "Normal", "Replace previous mask coverage according to strength"),
            ("MAX", "Lighten (Max)", "Keep the larger coverage: an OR of scene fields"),
            ("MIN", "Darken (Min)", "Keep the smaller coverage: an AND of scene fields"),
        ],
        update=changed,
    )
    edge_source: bpy.props.EnumProperty(
        name="Edges From", default="BAKED", update=lambda self, context: _edge_source_changed(self, context),
        items=[("BAKED", "Baked Edge Maps", "The four-reach Curvature set, blended by Edge Size"),
               ("LIVE", "Live Geometry", "Convexity and creases measured on the evaluated mesh: "
                                         "bevel an edge and the wear follows, no rebake")],
    )
    invert: bpy.props.BoolProperty(name="Invert", update=numeric)
    black: bpy.props.FloatProperty(name="Black Level", min=0, max=1, update=numeric)
    white: bpy.props.FloatProperty(name="White Level", default=1, min=0, max=1, update=numeric)
    strength: bpy.props.FloatProperty(name="Strength", default=1, min=0, max=1, update=numeric)
    amount: bpy.props.FloatProperty(name="Amount", default=0.5, min=0, max=1, update=numeric)
    contrast: bpy.props.FloatProperty(name="Contrast", default=2, min=0.01, max=20, update=numeric)
    edge_width: bpy.props.FloatProperty(
        name="Edge Width", default=1.0, min=0.05, max=1.0, subtype="FACTOR", update=numeric,
        description="How much of the chosen edge band is used, from the edge outwards",
    )
    edge_size: bpy.props.FloatProperty(
        name="Edge Size", default=1.0, min=0.0, max=3.0, update=numeric,
        description="Reach of the edge wear, blending live between the four baked edge maps: "
                    "0 thin, 1 normal, 2 wide, 3 extreme",
    )
    noise_scale: bpy.props.FloatProperty(
        name="Noise Scale", default=12, min=1, max=512, update=numeric
    )
    # Physical scale (0.9.5): sizes in metres, resolved to texture units per asset.
    units: bpy.props.EnumProperty(name='Units', default='TEXTURE', update=physical_changed, items=[
        ('TEXTURE', 'Texture', 'Sizes in UV repeats and texels'),
        ('PHYSICAL', 'Physical', 'Sizes in world distance; the texture values follow the assigned mesh')])
    feature_size: bpy.props.FloatProperty(name='Feature Size', default=.02, min=1e-5, soft_max=10, subtype='DISTANCE',
        unit='LENGTH', update=physical_changed, description='World size of one noise cell, scratch or spot')
    noise_strength: bpy.props.FloatProperty(
        name="Noise Strength", default=0.2, min=0, max=1, update=numeric
    )
    seed: bpy.props.IntProperty(name="Seed", default=1, min=0, update=numeric)
