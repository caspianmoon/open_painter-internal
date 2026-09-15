"""Node tree, node and socket types of the Open Graph (a projection of the stack)."""
import bpy

TREE_ID = "OpenPainterGraph"
OUTPUT_NODE = "output"
SOCKET_CONTENT, SOCKET_SCALAR, SOCKET_COVERAGE = "OPSocketContent", "OPSocketScalar", "OPSocketCoverage"
SOCKET_COLORS = {SOCKET_CONTENT: (0.93, 0.75, 0.32, 1.0), SOCKET_SCALAR: (0.66, 0.66, 0.66, 1.0),
                 SOCKET_COVERAGE: (0.45, 0.68, 0.93, 1.0)}


def is_graph(tree):
    return tree is not None and getattr(tree, "bl_idname", "") == TREE_ID and tree.get("op_graph_set")


class OP_GraphTree(bpy.types.NodeTree):
    """Open Painter's live node view of a texture set."""

    bl_idname = TREE_ID
    bl_label = "Open Painter Graph"
    bl_icon = "NODETREE"

    def update(self):
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
    """Straight RGBA content of a channel, a layer output or a folder composite."""
    bl_idname = SOCKET_CONTENT
    bl_label = "Content"


class OP_SocketScalar(_Socket, bpy.types.NodeSocket):
    """Gray content (roughness, metallic, height, ...)."""
    bl_idname = SOCKET_SCALAR
    bl_label = "Scalar"


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
        return tree.bl_idname == TREE_ID

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


class OP_OutputNode(_GraphNode, bpy.types.Node):
    """The texture set's final channels (informational)."""
    bl_idname = "OPOutputNode"
    bl_label = "Texture Set Output"
    bl_icon = "OUTPUT"


NODE_TYPES = {"LAYER": OP_LayerNode.bl_idname, "FOLDER": OP_FolderNode.bl_idname, "OUTPUT": OP_OutputNode.bl_idname,
              "MASK": OP_MaskNode.bl_idname, "EFFECT": OP_EffectNode.bl_idname}
CLASSES = (OP_GraphTree, OP_SocketContent, OP_SocketScalar, OP_SocketCoverage,
           OP_LayerNode, OP_FolderNode, OP_MaskNode, OP_EffectNode, OP_OutputNode)
