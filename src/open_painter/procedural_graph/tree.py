"""Mask Graph nodes, sockets and Add menu, in the Open Painter Graph editor.

A Mask Graph is an Open Painter Graph tree (``graph.tree``, the one Open Painter node
editor) marked with ``MARKER``: its nodes are these, a texture set's graph never shows
them and they never show its layer nodes. Graphs saved by 0.13 builds as a tree type of
their own (``LEGACY_ID``) load through a hidden type and are rebuilt on load
(``convert_legacy``). Nodes carry their settings as properties and plain value sockets;
nothing here evaluates anything. ``program`` reads a tree into the ordered steps both
evaluators share. A property edit is *numeric* (a uniform: the engine renders
the new value, nothing recompiles) or *structural* (a kind, an operation, a
link: the graph's pass is rebuilt); ``edits`` routes both to the masks that
use the graph.
"""
import zlib

import bpy

from ..graph.tree import TREE_ID
from . import MARKER, is_native

LEGACY_ID = 'OpenPainterMaskGraph'   # a Mask Graph's own tree type in 0.13 builds before one editor
VALUE_SOCKET, VECTOR_SOCKET = 'OPMaskGraphValue', 'OPMaskGraphVector'


def new_tree(name):
    """A new, empty Mask Graph."""
    tree = bpy.data.node_groups.new(name, TREE_ID)
    tree[MARKER] = True   # before any node: the nodes' poll reads it
    return tree


def _numeric(self, context):
    from . import edits
    edits.changed(self, structural=False)


def _structural(self, context):
    from . import edits
    edits.changed(self, structural=True)


def _items(entries):
    """Enum items with values derived from the identifier: a file keeps its choice when a
    later version adds items (Blender stores the value, not the name)."""
    return [(key, name, description, zlib.crc32(key.encode()) & 0x7fffffff) for key, name, description in entries]


def _kind_items(names):
    from ..texture_engine.procedurals import FILLS, NOISES
    catalog = {**NOISES, **FILLS}
    return _items((name, name.replace('_', ' ').title(), catalog.get(name, '')) for name in names)


NOISE_KINDS = ('FBM', 'PERLIN', 'SIMPLEX', 'VALUE', 'FRACTAL', 'CLOUD', 'RIDGED', 'TURBULENCE', 'MULTI_FRACTAL',
               'DIRECTIONAL', 'ANISOTROPIC', 'WARPED')
CELL_KINDS = ('VORONOI', 'CELLULAR', 'WORLEY')
PATTERN_KINDS = ('CHECKER', 'STRIPES', 'DOTS', 'GRID', 'PATTERN', 'TILE_PATTERN', 'BRICK_PATTERN', 'HEX_PATTERN',
                 'GRADIENT', 'LINEAR_GRADIENT', 'RADIAL_GRADIENT')


def _surface_kinds():
    """Every other GPU-exact catalog source: grunge, wear, material grain, marks."""
    from ..evaluation.engine.procedural_glsl import KINDS
    from ..texture_engine.procedurals import CATALOG
    taken = set(NOISE_KINDS) | set(CELL_KINDS) | set(PATTERN_KINDS) | {'SOLID'}
    return tuple(name for name in CATALOG if name in KINDS and name not in taken)


SURFACE_KINDS = _surface_kinds()


MESH_MAPS = ('AO', 'CURVATURE', 'THICKNESS', 'POSITION', 'UV_BORDER', 'MEAN_CURVATURE', 'SELECTION', 'BEVEL_MASK')
MATH_OPERATIONS = _items((
    ('ADD', 'Add', 'A + B'), ('SUBTRACT', 'Subtract', 'A − B'), ('MULTIPLY', 'Multiply', 'A × B'),
    ('DIVIDE', 'Divide', 'A ÷ B (0 where B is 0)'), ('POWER', 'Power', 'A to the power B'),
    ('MINIMUM', 'Minimum', 'The smaller of A and B'), ('MAXIMUM', 'Maximum', 'The larger of A and B'),
    ('LESS_THAN', 'Less Than', '1 where A < B'), ('GREATER_THAN', 'Greater Than', '1 where A > B'),
    ('MODULO', 'Modulo', 'Remainder of A ÷ B (truncated)'), ('SNAP', 'Snap', 'A rounded down to a multiple of B'),
    ('ONE_MINUS', 'One Minus', '1 − A'), ('ABSOLUTE', 'Absolute', '|A|'), ('SQRT', 'Square Root', '√A (0 below 0)'),
    ('ROUND', 'Round', 'Nearest whole number'), ('FLOOR', 'Floor', 'Largest whole number ≤ A'),
    ('CEIL', 'Ceil', 'Smallest whole number ≥ A'), ('FRACT', 'Fraction', 'A − floor(A)'),
    ('SINE', 'Sine', 'sin(A), radians'), ('COSINE', 'Cosine', 'cos(A), radians')))
UNARY = frozenset({'ONE_MINUS', 'ABSOLUTE', 'SQRT', 'ROUND', 'FLOOR', 'CEIL', 'FRACT', 'SINE', 'COSINE'})
MIX_BLENDS = _items((('MIX', 'Mix', 'Blend from A to B by Factor'), ('MULTIPLY', 'Multiply', ''), ('SCREEN', 'Screen', ''),
              ('OVERLAY', 'Overlay', ''), ('ADD', 'Add', 'A + B × Factor'), ('SUBTRACT', 'Subtract', 'A − B × Factor'),
              ('DIFFERENCE', 'Difference', ''), ('LIGHTEN', 'Lighten', ''), ('DARKEN', 'Darken', '')))
RAMP_MODES = _items((('LINEAR', 'Linear', 'Straight segments between stops'), ('EASE', 'Ease', 'Smooth segments'),
                     ('CONSTANT', 'Constant', 'Each stop holds until the next')))


class OP_MaskGraphTreeLegacy(bpy.types.NodeTree):
    """A Mask Graph saved as a tree type of its own (0.13 builds); rebuilt as an Open Painter Graph on load"""
    bl_idname = LEGACY_ID
    bl_label = 'Open Painter Mask Graph (converted on load)'
    bl_icon = 'NODETREE'

    @classmethod
    def poll(cls, context):
        return False   # never an editor of its own: files keep loading, the menu shows one Open Painter editor


# ---- sockets -----------------------------------------------------------------------------

class OP_MaskGraphValueSocket(bpy.types.NodeSocket):
    """A grayscale value"""
    bl_idname = VALUE_SOCKET
    bl_label = 'Value'

    default_value: bpy.props.FloatProperty(name='Value', default=0.0, update=_numeric)

    def draw(self, context, layout, node, text):
        if self.is_output or self.is_linked:
            layout.label(text=text)
        else:
            layout.prop(self, 'default_value', text=text)

    def draw_color(self, context, node):
        return (0.63, 0.63, 0.63, 1.0)

    @classmethod
    def draw_color_simple(cls):
        return (0.63, 0.63, 0.63, 1.0)


class OP_MaskGraphVectorSocket(bpy.types.NodeSocket):
    """Texture coordinates or a world-space direction"""
    bl_idname = VECTOR_SOCKET
    bl_label = 'Vector'

    def draw(self, context, layout, node, text):
        layout.label(text=text if (self.is_output or self.is_linked) else f'{text} (UV)')

    def draw_color(self, context, node):
        return (0.39, 0.39, 0.78, 1.0)

    @classmethod
    def draw_color_simple(cls):
        return (0.39, 0.39, 0.78, 1.0)


# ---- nodes -----------------------------------------------------------------------------

class _MaskGraphNode:
    @classmethod
    def poll(cls, tree):
        return is_native(tree) or tree.bl_idname == LEGACY_ID

    def _value(self, name, default, output=False):
        socket = (self.outputs if output else self.inputs).new(VALUE_SOCKET, name)
        if not output:
            socket.default_value = default
        return socket

    def _vector(self, name, output=False):
        return (self.outputs if output else self.inputs).new(VECTOR_SOCKET, name)


class OP_MaskGraphCoordinates(_MaskGraphNode, bpy.types.Node):
    """Texture coordinates and the surface's world position and normal"""
    bl_idname = 'OPMGCoordinates'
    bl_label = 'Coordinates'
    bl_icon = 'ORIENTATION_GLOBAL'

    def init(self, context):
        for name in ('UV', 'Position', 'Normal'):
            self._vector(name, output=True)


from ..properties.text import OP_TextSettings


class OP_MaskGraphText(_MaskGraphNode, bpy.types.Node):
    bl_idname = 'OPMGText'
    bl_label = 'Text'
    text: bpy.props.PointerProperty(type=OP_TextSettings)

    def init(self, context):
        self._vector('Vector')
        self._value('Value', 0, output=True)

    def draw_buttons(self, context, layout):
        from ..evaluation.text_source import draw
        draw(layout, self.text)


class OP_MaskGraphValue(_MaskGraphNode, bpy.types.Node):
    """A constant value"""
    bl_idname = 'OPMGValue'
    bl_label = 'Value'

    value: bpy.props.FloatProperty(name='Value', default=0.5, update=_numeric)

    def init(self, context):
        self._value('Value', 0, output=True)

    def draw_buttons(self, context, layout):
        layout.prop(self, 'value', text='')


class _TextureNode(_MaskGraphNode):
    scale: bpy.props.FloatProperty(name='Scale', default=8.0, min=1e-4, soft_max=200, update=_numeric,
                                   description='Repetitions per texture-coordinate unit')
    seed: bpy.props.IntProperty(name='Seed', default=1, min=0, update=_numeric)

    def init(self, context):
        self._vector('Vector')
        self._value('Value', 0, output=True)

    def draw_buttons(self, context, layout):
        layout.prop(self, 'kind', text='')
        layout.prop(self, 'scale')
        layout.prop(self, 'seed')

    def draw_label(self):
        return self.kind.replace('_', ' ').title()


class OP_MaskGraphNoise(_TextureNode, bpy.types.Node):
    """Gradient, value and fractal noise"""
    bl_idname = 'OPMGNoise'
    bl_label = 'Noise'
    bl_icon = 'FORCE_TURBULENCE'

    kind: bpy.props.EnumProperty(name='Noise', items=_kind_items(NOISE_KINDS), default='FBM', update=_structural)


class OP_MaskGraphCells(_TextureNode, bpy.types.Node):
    """Voronoi cells and feature distances"""
    bl_idname = 'OPMGCells'
    bl_label = 'Cells'
    bl_icon = 'MESH_ICOSPHERE'

    kind: bpy.props.EnumProperty(name='Cells', items=_kind_items(CELL_KINDS), default='VORONOI', update=_structural)


class OP_MaskGraphPattern(_TextureNode, bpy.types.Node):
    """Regular patterns and gradients"""
    bl_idname = 'OPMGPattern'
    bl_label = 'Pattern'
    bl_icon = 'TEXTURE'

    kind: bpy.props.EnumProperty(name='Pattern', items=_kind_items(PATTERN_KINDS), default='CHECKER', update=_structural)


class OP_MaskGraphSurface(_TextureNode, bpy.types.Node):
    """Grunge, wear, marks and material grain from the procedural catalog"""
    bl_idname = 'OPMGSurface'
    bl_label = 'Surface Texture'
    bl_icon = 'MATERIAL'

    kind: bpy.props.EnumProperty(name='Texture', items=_kind_items(SURFACE_KINDS), default='GRUNGE_MEDIUM',
                                 update=_structural)


class OP_MaskGraphMeshMap(_MaskGraphNode, bpy.types.Node):
    """A baked mesh map of the texture set (its first channel)"""
    bl_idname = 'OPMGMeshMap'
    bl_label = 'Mesh Map'
    bl_icon = 'MESH_DATA'

    kind: bpy.props.EnumProperty(name='Map', items=_items((k, k.replace('_', ' ').title(), '') for k in MESH_MAPS),
                                 default='CURVATURE', update=_structural)

    def init(self, context):
        self._value('Value', 0, output=True)

    def draw_buttons(self, context, layout):
        layout.prop(self, 'kind', text='')

    def draw_label(self):
        return self.kind.replace('_', ' ').title()


def _math_sockets(self, context):
    # By identifier: a name lookup (``inputs.get('B')``) skips a disabled socket, so B could never come back.
    for socket in self.inputs:
        if socket.identifier == 'B':
            socket.enabled = self.operation not in UNARY
    _structural(self, context)


class OP_MaskGraphMath(_MaskGraphNode, bpy.types.Node):
    """Arithmetic on values"""
    bl_idname = 'OPMGMath'
    bl_label = 'Math'

    operation: bpy.props.EnumProperty(name='Operation', items=MATH_OPERATIONS, default='MULTIPLY', update=_math_sockets)
    use_clamp: bpy.props.BoolProperty(name='Clamp', description='Clamp the result to 0–1', update=_structural)

    def init(self, context):
        self._value('A', 0.5)
        self._value('B', 0.5)
        self._value('Value', 0, output=True)

    def draw_buttons(self, context, layout):
        layout.prop(self, 'operation', text='')
        layout.prop(self, 'use_clamp')

    def draw_label(self):
        return next(name for key, name, *_ in MATH_OPERATIONS if key == self.operation)


class OP_MaskGraphMix(_MaskGraphNode, bpy.types.Node):
    """Blend B over A by a factor"""
    bl_idname = 'OPMGMix'
    bl_label = 'Mix'

    blend: bpy.props.EnumProperty(name='Blend', items=MIX_BLENDS, default='MIX', update=_structural)
    use_clamp: bpy.props.BoolProperty(name='Clamp', description='Clamp the result to 0–1', update=_structural)

    def init(self, context):
        self._value('Factor', 0.5)
        self._value('A', 0.0)
        self._value('B', 1.0)
        self._value('Value', 0, output=True)

    def draw_buttons(self, context, layout):
        layout.prop(self, 'blend', text='')
        layout.prop(self, 'use_clamp')


def _stop_changed(self, context):
    from . import edits
    edits.values_changed(self.id_data)


class OP_MaskGraphRampStop(bpy.types.PropertyGroup):
    position: bpy.props.FloatProperty(name='Position', default=0.0, min=0.0, max=1.0, update=_stop_changed)
    value: bpy.props.FloatProperty(name='Value', default=0.0, min=0.0, max=1.0, update=_stop_changed)


class OP_MaskGraphRamp(_MaskGraphNode, bpy.types.Node):
    """Remap a value through stops"""
    bl_idname = 'OPMGRamp'
    bl_label = 'Ramp'
    bl_icon = 'IPO_EASE_IN_OUT'

    interpolation: bpy.props.EnumProperty(name='Interpolation', items=RAMP_MODES, default='LINEAR', update=_structural)
    stops: bpy.props.CollectionProperty(type=OP_MaskGraphRampStop)

    def init(self, context):
        for position, value in ((0.0, 0.0), (1.0, 1.0)):
            stop = self.stops.add()
            stop.position, stop.value = position, value
        self._value('Fac', 0.5)
        self._value('Value', 0, output=True)

    def draw_buttons(self, context, layout):
        layout.prop(self, 'interpolation', text='')
        column = layout.column(align=True)
        for index, stop in enumerate(self.stops):
            row = column.row(align=True)
            row.prop(stop, 'position', text='')
            row.prop(stop, 'value', text='')
            op = row.operator('open_painter.mask_graph_ramp_stop', text='', icon='X')
            op.tree, op.node, op.index, op.action = self.id_data.name, self.name, index, 'REMOVE'
        op = layout.operator('open_painter.mask_graph_ramp_stop', text='Add Stop', icon='ADD')
        op.tree, op.node, op.index, op.action = self.id_data.name, self.name, -1, 'ADD'


class OP_MaskGraphLevels(_MaskGraphNode, bpy.types.Node):
    """Input and output levels with gamma, as the Levels filter"""
    bl_idname = 'OPMGLevels'
    bl_label = 'Levels'

    black: bpy.props.FloatProperty(name='Black', default=0.0, soft_min=0.0, soft_max=1.0, update=_numeric)
    white: bpy.props.FloatProperty(name='White', default=1.0, soft_min=0.0, soft_max=1.0, update=_numeric)
    gamma: bpy.props.FloatProperty(name='Gamma', default=1.0, min=1e-5, soft_max=5.0, update=_numeric)
    output_black: bpy.props.FloatProperty(name='Output Black', default=0.0, soft_min=0.0, soft_max=1.0, update=_numeric)
    output_white: bpy.props.FloatProperty(name='Output White', default=1.0, soft_min=0.0, soft_max=1.0, update=_numeric)

    def init(self, context):
        self._value('Value', 0.5)
        self._value('Value', 0, output=True)

    def draw_buttons(self, context, layout):
        column = layout.column(align=True)
        for name in ('black', 'white', 'gamma', 'output_black', 'output_white'):
            column.prop(self, name)


class OP_MaskGraphMapping(_MaskGraphNode, bpy.types.Node):
    """Offset, rotate and scale coordinates about the texture centre"""
    bl_idname = 'OPMGMapping'
    bl_label = 'Mapping'
    bl_icon = 'ORIENTATION_LOCAL'

    offset: bpy.props.FloatVectorProperty(name='Offset', size=2, update=_numeric)
    rotation: bpy.props.FloatProperty(name='Rotation', subtype='ANGLE', update=_numeric)
    scale: bpy.props.FloatVectorProperty(name='Scale', size=2, default=(1.0, 1.0), update=_numeric)

    def init(self, context):
        self._vector('Vector')
        self._vector('Vector', output=True)

    def draw_buttons(self, context, layout):
        layout.prop(self, 'offset')
        layout.prop(self, 'rotation')
        layout.prop(self, 'scale')


class OP_MaskGraphOutput(_MaskGraphNode, bpy.types.Node):
    """The mask coverage this graph produces"""
    bl_idname = 'OPMGOutput'
    bl_label = 'Mask Output'
    bl_icon = 'OUTPUT'

    def init(self, context):
        self._value('Coverage', 1.0)


TEXTURE_NODES = frozenset({'OPMGNoise', 'OPMGCells', 'OPMGPattern', 'OPMGSurface'})


class OP_OT_mask_graph_ramp_stop(bpy.types.Operator):
    """Add or remove a stop of a Mask Graph ramp"""
    bl_idname = 'open_painter.mask_graph_ramp_stop'
    bl_label = 'Ramp Stop'
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    tree: bpy.props.StringProperty()
    node: bpy.props.StringProperty()
    index: bpy.props.IntProperty(default=-1)
    action: bpy.props.EnumProperty(items=[('ADD', 'Add', ''), ('REMOVE', 'Remove', '')])

    def execute(self, context):
        tree = bpy.data.node_groups.get(self.tree)
        node = tree.nodes.get(self.node) if tree is not None else None
        if node is None or node.bl_idname != 'OPMGRamp':
            self.report({'ERROR'}, 'The ramp node no longer exists')
            return {'CANCELLED'}
        if self.action == 'REMOVE':
            if len(node.stops) <= 2:
                self.report({'ERROR'}, 'A ramp needs at least two stops')
                return {'CANCELLED'}
            node.stops.remove(self.index)
        else:
            stops = sorted(node.stops, key=lambda stop: stop.position)
            # Halfway along the widest gap, at the value the ramp has there.
            low, high = max(zip(stops, stops[1:]), key=lambda pair: pair[1].position - pair[0].position)
            stop = node.stops.add()
            stop.position = (low.position + high.position) / 2
            stop.value = (low.value + high.value) / 2
        from . import edits
        edits.changed(node, structural=True)
        return {'FINISHED'}


# ---- the Add menu -----------------------------------------------------------------------------

MENU = (
    ('Input', (('OPMGCoordinates', 'ORIENTATION_GLOBAL'), ('OPMGValue', 'NONE'), ('OPMGMeshMap', 'MESH_DATA'),
               ('OPMGText', 'FONT_DATA'))),
    ('Texture', (('OPMGNoise', 'FORCE_TURBULENCE'), ('OPMGCells', 'MESH_ICOSPHERE'), ('OPMGPattern', 'TEXTURE'),
                 ('OPMGSurface', 'MATERIAL'))),
    ('Converter', (('OPMGMath', 'NONE'), ('OPMGMix', 'NONE'), ('OPMGRamp', 'IPO_EASE_IN_OUT'),
                   ('OPMGLevels', 'NONE'), ('OPMGMapping', 'ORIENTATION_LOCAL'))),
    ('Output', (('OPMGOutput', 'OUTPUT'),)),
)


def add_menu(self, context):
    space = context.space_data
    if getattr(space, 'tree_type', '') != TREE_ID or not is_native(getattr(space, 'edit_tree', None)):
        return
    layout = self.layout
    for index, (category, entries) in enumerate(MENU):
        if index:
            layout.separator()
        layout.label(text=category)
        for idname, icon in entries:
            cls = getattr(bpy.types, idname, None)
            op = layout.operator('node.add_node', text=cls.bl_label if cls else idname, icon=icon)
            op.type, op.use_transform = idname, True


CLASSES = (OP_MaskGraphTreeLegacy, OP_MaskGraphValueSocket, OP_MaskGraphVectorSocket, OP_MaskGraphRampStop,
           OP_MaskGraphCoordinates, OP_MaskGraphValue, OP_MaskGraphText, OP_MaskGraphNoise, OP_MaskGraphCells, OP_MaskGraphPattern,
           OP_MaskGraphSurface, OP_MaskGraphMeshMap, OP_MaskGraphMath, OP_MaskGraphMix, OP_MaskGraphRamp,
           OP_MaskGraphLevels, OP_MaskGraphMapping, OP_MaskGraphOutput, OP_OT_mask_graph_ramp_stop)


# ---- graphs saved as a tree type of their own ------------------------------------------------------

_BASE = frozenset(prop.identifier for prop in bpy.types.Node.bl_rna.properties)


def _copy_settings(source, target):
    """The node's own settings (not Blender's): its properties and its ramp stops."""
    for prop in source.bl_rna.properties:
        name = prop.identifier
        if name in _BASE or prop.is_readonly and prop.type != 'COLLECTION':
            continue
        if prop.type == 'COLLECTION':
            items = getattr(target, name)
            items.clear()
            for item in getattr(source, name):
                copy = items.add()
                for field in item.bl_rna.properties:
                    if field.identifier != 'rna_type' and not field.is_readonly:
                        setattr(copy, field.identifier, getattr(item, field.identifier))
        elif prop.type != 'POINTER':
            setattr(target, name, getattr(source, name))


def convert_legacy():
    """Rebuild each Mask Graph saved as its own tree type as an Open Painter Graph tree (same
    name, nodes, settings, sockets and links) and point the masks and fake user at it."""
    from ..constants import OWNER
    converted = []
    for old in [tree for tree in bpy.data.node_groups if tree.bl_idname == LEGACY_ID]:
        new = new_tree(old.name + ' (converting)')
        if old.get(OWNER):
            new[OWNER] = True
        nodes = {}
        for node in old.nodes:
            copy = new.nodes.new(node.bl_idname)
            copy.name, copy.label = node.name, node.label
            copy.location, copy.width, copy.mute, copy.hide = node.location, node.width, node.mute, node.hide
            _copy_settings(node, copy)
            for socket in node.inputs:
                inner = next((s for s in copy.inputs if s.identifier == socket.identifier), None)
                if inner is not None:
                    inner.enabled = socket.enabled
                    if hasattr(socket, 'default_value'):
                        inner.default_value = socket.default_value
            nodes[node.name] = copy
        for link in old.links:
            source = next(s for s in nodes[link.from_node.name].outputs if s.identifier == link.from_socket.identifier)
            target = next(s for s in nodes[link.to_node.name].inputs if s.identifier == link.to_socket.identifier)
            new.links.new(source, target).is_muted = link.is_muted
        for scene in bpy.data.scenes:
            project = getattr(scene, 'open_painter', None)
            for ts in (project.texture_sets if project is not None else ()):
                for layer in ts.layers:
                    for mask in layer.masks:
                        if mask.node_group == old:
                            mask.node_group = new
        name, fake = old.name, old.use_fake_user
        bpy.data.node_groups.remove(old)
        new.name, new.use_fake_user = name, fake
        converted.append(new)
    if converted:
        from . import edits
        edits.forget()
    return converted


@bpy.app.handlers.persistent
def _on_load(*_):
    convert_legacy()


def _convert_open_file():
    try:
        convert_legacy()
    except Exception:
        from ..utils.logging import logger
        logger.debug('Mask Graph conversion skipped', exc_info=True)
    return None


def register():
    bpy.types.NODE_MT_add.append(add_menu)
    bpy.app.handlers.load_post.insert(0, _on_load)   # before the engine binds the masks of the file
    bpy.app.timers.register(_convert_open_file, first_interval=0.0)   # a file open while enabling


def unregister():
    bpy.types.NODE_MT_add.remove(add_menu)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_convert_open_file):
        bpy.app.timers.unregister(_convert_open_file)
