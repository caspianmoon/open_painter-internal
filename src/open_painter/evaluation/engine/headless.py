"""In-memory node trees with the part of the bpy node API the shader compiler uses.

``shader_graph.Graph`` expresses every layer, mask and filter as nodes it creates,
links and parameterises. Built as real Blender node trees, each insertion
revalidates the tree and each numeric write makes Blender re-derive the whole
material. These plain objects cost nothing to build; ``engine.glsl`` translates
the units they describe into Open Painter's own GPU programs.

Only the node types the compiler emits are modelled, with Blender's socket names,
identifiers, types and defaults (5.2), so the compiler runs unchanged on them.
"""

from collections import Counter

VALUE, VECTOR, RGBA = 'VALUE', 'VECTOR', 'RGBA'
SOCKET_TYPES = {'NodeSocketFloat': VALUE, 'NodeSocketColor': RGBA, 'NodeSocketVector': VECTOR}
_DEFAULTS = {VALUE: 0.0, VECTOR: (0.0, 0.0, 0.0), RGBA: (0.0, 0.0, 0.0, 1.0)}


class Socket:
    __slots__ = ('node', 'name', 'identifier', 'type', 'is_output', 'default_value', 'links', 'hide')

    def __init__(self, node, name, kind, is_output, default=None, identifier=None):
        self.node = node
        self.name = name
        self.identifier = identifier or name
        self.type = kind
        self.is_output = is_output
        self.default_value = _DEFAULTS[kind] if default is None else default
        self.links = []
        self.hide = False

    @property
    def id_data(self):
        return self.node.id_data

    @property
    def is_linked(self):
        return bool(self.links)

    def as_pointer(self):
        return id(self)

    def __repr__(self):
        return f'<{"out" if self.is_output else "in"} {self.node.bl_idname}.{self.identifier}>'


class Sockets:
    """Socket collection addressed by index, name or identifier, like bpy's."""

    def __init__(self):
        self._items = []

    def _add(self, socket):
        self._items.append(socket)
        return socket

    def _remove(self, socket):
        self._items.remove(socket)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._items[key]
        for socket in self._items:
            if socket.name == key:
                return socket
        for socket in self._items:
            if socket.identifier == key:
                return socket
        raise KeyError(key)

    def __contains__(self, key):
        return any(socket.name == key or socket.identifier == key for socket in self._items)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def _math(node):
    for index, identifier in enumerate(('Value', 'Value_001', 'Value_002')):
        node.inputs._add(Socket(node, 'Value', VALUE, False, 0.5, identifier))
    node.outputs._add(Socket(node, 'Value', VALUE, True))
    node.operation = 'ADD'
    node.use_clamp = False


def _vector_math(node):
    for identifier in ('Vector', 'Vector_001', 'Vector_002'):
        node.inputs._add(Socket(node, 'Vector', VECTOR, False, (0.0, 0.0, 0.0), identifier))
    node.inputs._add(Socket(node, 'Scale', VALUE, False, 1.0))
    node.outputs._add(Socket(node, 'Vector', VECTOR, True))
    node.outputs._add(Socket(node, 'Value', VALUE, True))
    node.operation = 'ADD'


def _combine(node):
    for axis in 'XYZ':
        node.inputs._add(Socket(node, axis, VALUE, False, 0.0))
    node.outputs._add(Socket(node, 'Vector', VECTOR, True))


def _separate(node):
    node.inputs._add(Socket(node, 'Vector', VECTOR, False, (0.0, 0.0, 0.0)))
    for axis in 'XYZ':
        node.outputs._add(Socket(node, axis, VALUE, True))


def _rgb(node):
    node.outputs._add(Socket(node, 'Color', RGBA, True, (0.5, 0.5, 0.5, 1.0)))


def _value(node):
    node.outputs._add(Socket(node, 'Value', VALUE, True, 0.5))


# Images bound by headless Image Texture nodes. Blender counts no user for them, so code
# that asks whether a node still binds an image (shader_resources.bound) asks here too.
_image_refs = Counter()
# Every image or GPU resource assignment on a headless node. While it stands still, every
# node samples what it did: a scan of the sampled images reads only the distinct images.
assignments = [0]


def _image_pointer(image):
    try:
        return image.as_pointer()
    except ReferenceError:
        return None


def bound_count(image):
    pointer = _image_pointer(image) if image is not None else None
    return _image_refs.get(pointer, 0) if pointer is not None else 0


def _image(node):
    node.inputs._add(Socket(node, 'Vector', VECTOR, False, (0.0, 0.0, 0.0)))
    node.outputs._add(Socket(node, 'Color', RGBA, True))
    node.outputs._add(Socket(node, 'Alpha', VALUE, True))
    node._image, node._image_key, node._image_uid = None, None, None
    node.gpu_resource = None
    node.interpolation = 'Linear'
    node.extension = 'REPEAT'
    node.projection = 'FLAT'


def _geometry(node):
    for name in ('Position', 'Normal', 'Tangent', 'True Normal', 'Incoming', 'Parametric'):
        node.outputs._add(Socket(node, name, VECTOR, True))
    for name in ('Backfacing', 'Pointiness', 'Random Per Island'):
        node.outputs._add(Socket(node, name, VALUE, True))


def _coordinates(node):
    for name in ('Generated', 'Normal', 'UV', 'Object', 'Camera', 'Window', 'Reflection'):
        node.outputs._add(Socket(node, name, VECTOR, True))
    node.object = None
    node.from_instancer = False


def _normal_map(node):
    node.inputs._add(Socket(node, 'Strength', VALUE, False, 1.0))
    node.inputs._add(Socket(node, 'Color', RGBA, False, (0.5, 0.5, 1.0, 1.0)))
    node.outputs._add(Socket(node, 'Normal', VECTOR, True))
    node.space = 'TANGENT'
    node.uv_map = ''


def _uv_map(node):
    node.outputs._add(Socket(node, 'UV', VECTOR, True))
    node.uv_map = ''
    node.from_instancer = False


def _group(node):
    node._tree = None


def _procedural(node):
    """A catalog source (texture_engine.procedurals) evaluated by the engine at a coordinate."""
    node.inputs._add(Socket(node, 'Vector', VECTOR, False, (0.0, 0.0, 0.0)))
    node.inputs._add(Socket(node, 'Scale', VALUE, False, 8.0))
    # A seed is a 32-bit integer: two 16-bit halves, each exact in a float uniform.
    node.inputs._add(Socket(node, 'Seed High', VALUE, False, 0.0))
    node.inputs._add(Socket(node, 'Seed Low', VALUE, False, 1.0))
    node.outputs._add(Socket(node, 'Value', VALUE, True))
    node.kind = 'FBM'


def _group_io(node):
    tree = node.id_data
    output = node.bl_idname == 'NodeGroupInput'
    for item in tree.interface.items_tree:
        if (item.in_out == 'INPUT') == output:
            node._interface_socket(item)


def _blend(node):
    for name, kind in (('Backdrop', VECTOR), ('Backdrop Alpha', VALUE),
                       ('Source', VECTOR), ('Source Alpha', VALUE), ('Coverage', VALUE)):
        node.inputs._add(Socket(node, name, kind, False))
    node.outputs._add(Socket(node, 'Color', VECTOR, True))
    node.outputs._add(Socket(node, 'Alpha', VALUE, True))
    node.operation = 'NORMAL'
    node.opaque = False


def _effect(node):
    node.outputs._add(Socket(node, 'Color', VECTOR, True))
    node.operation = 'INVERT'


def _mask_fold(node):
    for name in ('Previous', 'Value', 'Black', 'White', 'Invert', 'Strength'):
        node.inputs._add(Socket(node, name, VALUE, False))
    node.outputs._add(Socket(node, 'Value', VALUE, True))
    node.operation, node.first = 'NORMAL', False


def _spatial_filter(node):
    node.inputs._add(Socket(node, 'Color', RGBA, False))
    node.inputs._add(Socket(node, 'Alpha', VALUE, False, 1.0))
    node.outputs._add(Socket(node, 'Color', RGBA, True))
    node.effect = None


TEMPLATES = {
    'ShaderNodeMath': _math, 'ShaderNodeVectorMath': _vector_math, 'ShaderNodeCombineXYZ': _combine,
    'ShaderNodeSeparateXYZ': _separate, 'ShaderNodeRGB': _rgb, 'ShaderNodeValue': _value,
    'ShaderNodeTexImage': _image, 'ShaderNodeNewGeometry': _geometry, 'ShaderNodeTexCoord': _coordinates,
    'ShaderNodeNormalMap': _normal_map, 'ShaderNodeUVMap': _uv_map, 'ShaderNodeGroup': _group,
    'NodeGroupInput': _group_io, 'NodeGroupOutput': _group_io, 'OPProcedural': _procedural,
    'OPSpatialFilter': _spatial_filter, 'OPBlend': _blend, 'OPEffect': _effect, 'OPMaskFold': _mask_fold,
}
# Blender's node.type strings, for code that inspects built trees.
TYPES = {'ShaderNodeMath': 'MATH', 'ShaderNodeVectorMath': 'VECT_MATH', 'ShaderNodeCombineXYZ': 'COMBXYZ',
         'ShaderNodeSeparateXYZ': 'SEPXYZ', 'ShaderNodeRGB': 'RGB', 'ShaderNodeValue': 'VALUE',
         'ShaderNodeTexImage': 'TEX_IMAGE', 'ShaderNodeNewGeometry': 'NEW_GEOMETRY', 'ShaderNodeTexCoord': 'TEX_COORD',
         'ShaderNodeNormalMap': 'NORMAL_MAP', 'ShaderNodeUVMap': 'UVMAP', 'ShaderNodeGroup': 'GROUP',
         'NodeGroupInput': 'GROUP_INPUT', 'NodeGroupOutput': 'GROUP_OUTPUT', 'OPProcedural': 'OP_PROCEDURAL'}
TYPES['OPSpatialFilter'] = 'OP_SPATIAL_FILTER'
TYPES['OPBlend'] = 'OP_BLEND'
TYPES['OPEffect'] = 'OP_EFFECT'
TYPES['OPMaskFold'] = 'OP_MASK_FOLD'


class Node:
    def __init__(self, tree, kind):
        if kind not in TEMPLATES:
            raise ValueError(f'The Open Painter engine does not evaluate {kind} nodes')
        self.id_data = tree
        self.bl_idname = kind
        self.type = TYPES[kind]
        self.inputs, self.outputs = Sockets(), Sockets()
        self.name = ''
        self.label = ''
        self.location = (0.0, 0.0)
        self._props = {}
        TEMPLATES[kind](self)

    # Custom properties, as on bpy nodes.
    def __getitem__(self, key):
        return self._props[key]

    def __setitem__(self, key, value):
        self._props[key] = value

    def __contains__(self, key):
        return key in self._props

    def get(self, key, default=None):
        return self._props.get(key, default)

    def keys(self):
        return self._props.keys()

    def as_pointer(self):
        return id(self)

    # ---- image nodes ------------------------------------------------------------

    @property
    def image(self):
        return self.__dict__.get('_image')

    @image.setter
    def image(self, value):
        previous = self.__dict__.get('_image_key')
        if previous is not None:
            _image_refs[previous] -= 1
            if _image_refs[previous] <= 0:
                del _image_refs[previous]
        key = _image_pointer(value) if value is not None else None
        if key is not None:
            _image_refs[key] += 1
        uid = getattr(value, 'session_uid', None) if value is not None else None
        same = key == previous and uid == self.__dict__.get('_image_uid')
        self._image, self._image_key, self._image_uid = value, key, uid
        if not same:
            # The same datablock again (an edit changed its pixels, not what the node samples):
            # nothing a scan of the sampled images groups by has changed.
            assignments[0] += 1
            self._image_assigned = assignments[0]

    @property
    def gpu_resource(self):
        return self.__dict__.get('_gpu_resource')

    @gpu_resource.setter
    def gpu_resource(self, value):
        self.__dict__['_gpu_resource'] = value
        assignments[0] += 1
        self.__dict__['_image_assigned'] = assignments[0]

    def release_image(self):
        """Stop binding the image (the unit is discarded)."""
        if self.bl_idname == 'ShaderNodeTexImage':
            self.gpu_resource = None
            self.image = None

    # ---- group nodes ----------------------------------------------------------

    @property
    def node_tree(self):
        return self._tree if self.bl_idname == 'ShaderNodeGroup' else None

    @node_tree.setter
    def node_tree(self, tree):
        if self.bl_idname != 'ShaderNodeGroup':
            raise AttributeError('node_tree')
        previous = self._tree
        if previous is not None and isinstance(previous, Tree):
            previous._instances.discard(self)
        for socket in list(self.inputs) + list(self.outputs):
            for link in list(socket.links):
                self.id_data.links.remove(link)
        self.inputs, self.outputs = Sockets(), Sockets()
        self._tree = tree
        if tree is None:
            return
        if isinstance(tree, Tree):
            tree._instances.add(self)
        # A real bpy tree (a Custom Graph mask's shader group) exposes the same interface API.
        for item in tree.interface.items_tree:
            if getattr(item, 'item_type', 'SOCKET') == 'SOCKET':
                self._interface_socket(item, instance=True)

    def _interface_socket(self, item, instance=False):
        """Add the socket an interface item implies on this group / group input / group output node."""
        kind = SOCKET_TYPES.get(item.socket_type, VALUE)
        if instance:
            is_output = item.in_out == 'OUTPUT'
        else:
            # A Group Input node outputs the interface inputs; a Group Output node takes the outputs.
            is_output = self.bl_idname == 'NodeGroupInput'
        default = getattr(item, 'default_value', None)
        if default is not None and kind != VALUE:
            default = tuple(default)
        collection = self.outputs if is_output else self.inputs
        return collection._add(Socket(self, item.name, kind, is_output, default, getattr(item, 'identifier', item.name)))


class Link:
    __slots__ = ('from_socket', 'to_socket')
    is_valid = True
    is_muted = False

    def __init__(self, from_socket, to_socket):
        self.from_socket, self.to_socket = from_socket, to_socket

    @property
    def from_node(self):
        return self.from_socket.node

    @property
    def to_node(self):
        return self.to_socket.node


class Links:
    def __init__(self, tree):
        self._tree = tree
        self._items = []

    def new(self, from_socket, to_socket):
        if not from_socket.is_output or to_socket.is_output:
            raise ValueError('Links run from an output socket to an input socket')
        for link in list(to_socket.links):   # an input takes one link, as in Blender
            self.remove(link)
        link = Link(from_socket, to_socket)
        from_socket.links.append(link)
        to_socket.links.append(link)
        self._items.append(link)
        return link

    def remove(self, link):
        link.from_socket.links.remove(link)
        link.to_socket.links.remove(link)
        self._items.remove(link)

    def clear(self):
        for link in list(self._items):
            self.remove(link)

    def __iter__(self):
        return iter(list(self._items))

    def __len__(self):
        return len(self._items)


class InterfaceItem:
    item_type = 'SOCKET'

    def __init__(self, name, in_out, socket_type):
        self.name = self.identifier = name
        self.in_out = in_out
        self.socket_type = socket_type
        self.description = ''
        self.default_value = None


class Interface:
    def __init__(self, tree):
        self._tree = tree
        self.items_tree = []

    def new_socket(self, name, in_out='INPUT', socket_type='NodeSocketFloat', description='', **_):
        item = InterfaceItem(name, in_out, socket_type)
        item.description = description
        self.items_tree.append(item)
        side = 'NodeGroupInput' if in_out == 'INPUT' else 'NodeGroupOutput'
        for node in self._tree.nodes:
            if node.bl_idname == side:
                node._interface_socket(item)
        for instance in self._tree._instances:
            instance._interface_socket(item, instance=True)
        return item


class Nodes:
    def __init__(self, tree):
        self._tree = tree
        self._items = []
        self._image_nodes = None

    def new(self, kind):
        node = Node(self._tree, kind)
        self._items.append(node)
        self._image_nodes = None
        return node

    def remove(self, node):
        for socket in list(node.inputs) + list(node.outputs):
            for link in list(socket.links):
                self._tree.links.remove(link)
        if node.bl_idname == 'ShaderNodeGroup' and isinstance(node._tree, Tree):
            node._tree._instances.discard(node)
        self._items.remove(node)
        self._image_nodes = None

    @property
    def image_nodes(self):
        """Stable image-node topology; each node's image remains a live binding.

        Runtime signatures query these on every edit. Compiled trees contain
        many arithmetic nodes, so rescan only after insertion or removal. The
        tuple belongs to this collection and cannot retain a replaced tree.
        """
        if self._image_nodes is None:
            self._image_nodes = tuple(node for node in self._items
                                      if node.bl_idname == 'ShaderNodeTexImage')
        return self._image_nodes

    def get(self, name, default=None):
        return next((node for node in self._items if node.name == name), default)

    def __iter__(self):
        return iter(list(self._items))

    def __len__(self):
        return len(self._items)


class Tree:
    """A unit (or the top level) of a compiled graph."""
    bl_idname = 'ShaderNodeTree'

    def __init__(self, name):
        self.name = name
        self._props = {}
        self._instances = set()
        self.nodes = Nodes(self)
        self.links = Links(self)
        self.interface = Interface(self)

    @property
    def users(self):
        return len(self._instances)

    def __getitem__(self, key):
        return self._props[key]

    def __setitem__(self, key, value):
        self._props[key] = value

    def __contains__(self, key):
        return key in self._props

    def get(self, key, default=None):
        return self._props.get(key, default)

    def as_pointer(self):
        return id(self)

    def __repr__(self):
        return f'<headless tree {self.name!r} ({len(self.nodes)} nodes)>'


def is_headless(value):
    return isinstance(value, (Tree, Node, Socket))
