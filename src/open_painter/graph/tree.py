"""Node tree, node and socket types of the Open Graph (a projection of the stack)."""
import bpy

TREE_ID = "OpenPainterGraph"
OUTPUT_NODE = "output"
SOCKET_CONTENT, SOCKET_SCALAR, SOCKET_COVERAGE = "OPSocketContent", "OPSocketScalar", "OPSocketCoverage"
SOCKET_COLOR, SOCKET_NORMAL = "OPSocketColor", "OPSocketNormal"
# What a socket carries decides its type and colour: a material bundle (every channel at
# once), one colour channel, one scalar channel, a tangent normal, or mask coverage.
SOCKET_COLORS = {SOCKET_CONTENT: (0.93, 0.75, 0.32, 1.0), SOCKET_SCALAR: (0.66, 0.66, 0.66, 1.0),
                 SOCKET_COVERAGE: (0.45, 0.68, 0.93, 1.0), SOCKET_COLOR: (0.78, 0.78, 0.16, 1.0),
                 SOCKET_NORMAL: (0.39, 0.39, 0.78, 1.0)}
SOCKET_DESCRIPTIONS = {SOCKET_CONTENT: "Material bundle: every channel of the stack up to here",
                       SOCKET_SCALAR: "One scalar channel (roughness, metallic, height, AO, opacity)",
                       SOCKET_COVERAGE: "Mask coverage, 0 to 1", SOCKET_COLOR: "One colour channel (RGB)",
                       SOCKET_NORMAL: "Tangent-space normal"}


def is_graph(tree):
    return tree is not None and getattr(tree, "bl_idname", "") == TREE_ID and tree.get("op_graph_set")


class OP_GraphTree(bpy.types.NodeTree):
    """Open Painter's node editor: a texture set's live graph, or a Mask Graph."""

    bl_idname = TREE_ID
    bl_label = "Open Painter Graph"
    bl_icon = "NODETREE"

    def update(self):
        # Blender updates surviving trees while socket/node RNA is only partly
        # registered. Reading their sockets then can dereference retired RNA.
        from ..registration import _registered
        if not _registered:
            return
        if self.get('op_brush_graph'):
            return  # validated and frozen once, when a gesture starts
        if self.get("op_mask_graph"):
            from ..procedural_graph import edits as mask_graph_edits
            mask_graph_edits.tree_changed(self)
            return
        from . import edits
        edits.on_tree_update(self)


class _Socket:
    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return SOCKET_COLORS[self.bl_idname]

    @classmethod
    def draw_color_simple(cls):
        return SOCKET_COLORS[cls.bl_idname]


class OP_SocketContent(_Socket, bpy.types.NodeSocket):
    """A material bundle: a layer's output, a folder's composite, the backdrop below a layer."""
    bl_idname = SOCKET_CONTENT
    bl_label = "Material"


class OP_SocketScalar(_Socket, bpy.types.NodeSocket):
    """One scalar channel (roughness, metallic, height, AO, opacity)."""
    bl_idname = SOCKET_SCALAR
    bl_label = "Scalar"


class OP_SocketColor(_Socket, bpy.types.NodeSocket):
    """One colour channel (base colour, emission)."""
    bl_idname = SOCKET_COLOR
    bl_label = "Color"


class OP_SocketNormal(_Socket, bpy.types.NodeSocket):
    """A tangent-space normal (the Normal channel, micro-normal details)."""
    bl_idname = SOCKET_NORMAL
    bl_label = "Normal"


class OP_SocketCoverage(_Socket, bpy.types.NodeSocket):
    """Mask coverage."""
    bl_idname = SOCKET_COVERAGE
    bl_label = "Coverage"


class _GraphNode:
    op_id: bpy.props.StringProperty()
    op_kind: bpy.props.StringProperty()
    op_owner: bpy.props.StringProperty()      # owning layer id of a mask/filter node
    op_channel: bpy.props.StringProperty()    # channel of a filter node

    @classmethod
    def poll(cls, tree):
        return tree.bl_idname == TREE_ID and not tree.get("op_mask_graph") and not tree.get('op_brush_graph')

    def draw_buttons(self, context, layout):
        from . import mirror
        mirror.draw_node(self, context, layout)

    def draw_label(self):
        from . import mirror
        return mirror.node_label(self)


class OP_LayerNode(_GraphNode, bpy.types.Node):
    """A paint or fill layer: anchor inputs on the left, published outputs on the right."""
    bl_idname = "OPLayerNode"
    bl_label = "Layer"
    bl_icon = "IMAGE_DATA"


class OP_FolderNode(_GraphNode, bpy.types.Node):
    """A folder: its mask inputs and its composite/output on the right."""
    bl_idname = "OPFolderNode"
    bl_label = "Folder"
    bl_icon = "FILE_FOLDER"


class OP_MaskNode(_GraphNode, bpy.types.Node):
    """One mask of a layer's mask chain: previous coverage in, its coverage out."""
    bl_idname = "OPMaskNode"
    bl_label = "Mask"
    bl_icon = "MOD_MASK"


class OP_EffectNode(_GraphNode, bpy.types.Node):
    """One filter of a channel's filter chain: content in, filtered content out."""
    bl_idname = "OPEffectNode"
    bl_label = "Filter"
    bl_icon = "SHADERFX"


class OP_MeshMaskNode(_GraphNode, bpy.types.Node):
    bl_idname = "OPMeshMaskNode"
    bl_label = "Mesh Mask"
    bl_icon = "MESH_DATA"


class OP_MaskGroupNode(_GraphNode, bpy.types.Node):
    bl_idname = "OPMaskGroupNode"
    bl_label = "Mask Group"
    bl_icon = "NODETREE"


class OP_CustomGraphNode(_GraphNode, bpy.types.Node):
    bl_idname = "OPCustomGraphNode"
    bl_label = "Custom Graph"
    bl_icon = "NODETREE"


class OP_StrokeMaskNode(_GraphNode, bpy.types.Node):
    bl_idname = "OPStrokeMaskNode"
    bl_label = "Vector Strokes"
    bl_icon = "CURVE_DATA"


def _push_image_feed(node, context):
    """A node-side image/mapping edit feeds the linked fill channel, if any."""
    from . import edits
    try:
        edits.push_image_node(node, context)
    except (AttributeError, RuntimeError):
        pass  # midway through load/undo the tree or set may not exist yet


class OP_ImageNode(_GraphNode, bpy.types.Node):
    """An imported image feeding a fill channel: outputs only, never stack state.

    The node owns its mapping controls; drawing its output into a fill
    channel's content input copies the image and the mapping onto that
    channel (see edits.feed_image). The node itself is tree-only state the
    mirror preserves; the stack only ever holds the copied values.
    """
    bl_idname = "OPImageNode"
    bl_label = "Image Texture"
    bl_icon = "IMAGE_DATA"
    image: bpy.props.PointerProperty(name="Image", type=bpy.types.Image, update=_push_image_feed)
    projection: bpy.props.EnumProperty(
        name="Projection",
        items=[("UV", "UV", ""), ("TRIPLANAR", "Triplanar", "World-space projection")],
        update=_push_image_feed,
    )
    scale: bpy.props.FloatVectorProperty(name="Scale", size=2, default=(1, 1), min=0.001,
                                         update=_push_image_feed)
    rotation: bpy.props.FloatProperty(name="Rotation", subtype="ANGLE", update=_push_image_feed)
    offset: bpy.props.FloatVectorProperty(name="Offset", size=2, update=_push_image_feed)
    triplanar_hardness: bpy.props.FloatProperty(name="Triplanar Hardness", default=4, min=1, max=64,
                                                update=_push_image_feed)


class OP_OutputNode(_GraphNode, bpy.types.Node):
    """The texture set's final channels (informational)."""
    bl_idname = "OPOutputNode"
    bl_label = "Texture Set Output"
    bl_icon = "OUTPUT"


NODE_TYPES = {"LAYER": OP_LayerNode.bl_idname, "FOLDER": OP_FolderNode.bl_idname, "OUTPUT": OP_OutputNode.bl_idname,
              "MASK": OP_MaskNode.bl_idname, "EFFECT": OP_EffectNode.bl_idname, "IMAGE": OP_ImageNode.bl_idname}
IMAGE_PREFIX = "image:"
MASK_NODE_TYPES = {'MESH': OP_MeshMaskNode.bl_idname, 'GROUP': OP_MaskGroupNode.bl_idname,
                   'GRAPH': OP_CustomGraphNode.bl_idname, 'STROKES': OP_StrokeMaskNode.bl_idname}
CLASSES = (OP_GraphTree, OP_SocketContent, OP_SocketScalar, OP_SocketCoverage, OP_SocketColor, OP_SocketNormal,
           OP_LayerNode, OP_FolderNode, OP_MaskNode, OP_EffectNode, OP_OutputNode,
           OP_MeshMaskNode, OP_MaskGroupNode, OP_CustomGraphNode, OP_StrokeMaskNode, OP_ImageNode)
