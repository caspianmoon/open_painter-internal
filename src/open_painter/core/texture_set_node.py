"""The Open Painter Texture Set shader node.

One input node gives any material a texture set's channels: Base Color, Metallic,
Roughness, a ready-to-use shading Normal (tangent normal map with the height as
bump), Height, Emission, AO and Alpha, plus the raw Normal Map colour and the UV
Tangent. The set's stack is evaluated into the set's channel images; the node only
samples them, so it never changes when layers, masks or filters do.

A set's managed material is this node, a Principled BSDF and a Material Output,
nothing else: what the surface shows of the channels lives inside the node's group —
AO in the preview (Base Color), Engine Preview's target adapters, View Mask's
snapshot, and the images a CPU-reference display binds instead of the set's own.

Blender does not let add-ons define shader code, so the node is a
``ShaderNodeCustomGroup``: a registered node type whose small internal group (one
per texture set, shared by every node showing it, hidden with a leading dot) holds
the image samplers. Add it from Add ▸ Input ▸ Open Painter Texture Set.
"""
import zlib
from contextlib import contextmanager
from time import perf_counter

import bpy

from ..constants import CHANNELS, OWNER

NODE_ID = 'OPShaderNodeTextureSet'
TREE_PREFIX = '.OP Texture Set · '
EMPTY_TREE = TREE_PREFIX + '(none)'
# Output socket, socket type, channel (None: derived).
OUTPUTS = (
    ('Base Color', 'NodeSocketColor', 'BASE_COLOR'),
    ('Metallic', 'NodeSocketFloat', 'METALLIC'),
    ('Roughness', 'NodeSocketFloat', 'ROUGHNESS'),
    ('Normal', 'NodeSocketVector', None),
    ('Height', 'NodeSocketFloat', 'HEIGHT'),
    ('Emission', 'NodeSocketColor', 'EMISSION'),
    ('AO', 'NodeSocketFloat', 'AO'),
    ('Alpha', 'NodeSocketFloat', 'OPACITY'),
    ('Normal Map', 'NodeSocketColor', 'NORMAL'),
    ('Tangent', 'NodeSocketVector', None),
)
# The raw channel each socket carries, for material wiring (NORMAL is the tangent colour).
CHANNEL_SOCKETS = {'BASE_COLOR': 'Base Color', 'METALLIC': 'Metallic', 'ROUGHNESS': 'Roughness',
                   'NORMAL': 'Normal Map', 'HEIGHT': 'Height', 'EMISSION': 'Emission', 'AO': 'AO',
                   'OPACITY': 'Alpha'}
SCALARS = {'METALLIC', 'ROUGHNESS', 'HEIGHT', 'AO', 'OPACITY'}
MASK_VIEW = 'Mask View'
MASK_VIEW_ROLES = (MASK_VIEW, MASK_VIEW + ' Roughness', MASK_VIEW + ' Metallic', MASK_VIEW + ' Normal')


def _all_sets():
    for scene in bpy.data.scenes:
        project = getattr(scene, 'open_painter', None)
        if project is None:
            continue
        for ts in project.texture_sets:
            yield ts


def find_set(identifier):
    return next((ts for ts in _all_sets() if ts.id == identifier), None) if identifier else None


def _value(identifier):
    return zlib.crc32(identifier.encode()) & 0x7fffffff


_ITEMS = []


def _set_items(self, context):
    # Blender keeps pointers to the strings: hold the list between calls.
    _ITEMS.clear()
    _ITEMS.append(('', 'None', 'No texture set', 'BLANK1', 0))
    for ts in _all_sets():
        _ITEMS.append((ts.id, ts.name, f'Channels of texture set "{ts.name}"', 'TEXTURE', _value(ts.id)))
    return _ITEMS


def _get_set(self):
    return _value(self.texture_set_id) if find_set(self.texture_set_id) is not None else 0


def _set_set(self, value):
    ts = next((item for item in _all_sets() if _value(item.id) == value), None)
    self.texture_set_id = ts.id if ts is not None else ''


def _set_changed(self, context):
    _show(self, tree_for(find_set(self.texture_set_id)))


def _show(node, tree):
    """Point ``node`` at ``tree``, removing the internal tree it leaves if nothing else uses it
    (``tree_for`` rebuilds one on demand; an orphan would only vanish on the next reload)."""
    previous = node.node_tree
    if previous == tree:
        return
    node.node_tree = tree
    if previous is not None and previous.get('op_texture_set_node') is not None and not previous.users:
        bpy.data.node_groups.remove(previous)


def bind(node, ts, overrides=None):
    """Show ``ts`` (None: no set) on ``node`` with its internal tree up to date; ``overrides``
    (channel → image) are shown instead of the set's own images (a CPU-reference display)."""
    identifier = ts.id if ts is not None else ''
    if node.texture_set_id != identifier:
        node.texture_set_id = identifier
    _show(node, tree_for(ts, overrides))


_creating = []


@contextmanager
def creating():
    """Nodes made inside are bound by the caller (``bind``). Blender counts a tree assigned
    during ``init`` twice, so such a tree could never be freed; ``init`` only assigns one for
    nodes made elsewhere (a script's ``nodes.new``)."""
    _creating.append(True)
    try:
        yield
    finally:
        _creating.pop()


def _active_set(context):
    project = getattr(getattr(context, 'scene', None), 'open_painter', None)
    if project is not None and 0 <= project.active_texture_set < len(project.texture_sets):
        return project.texture_sets[project.active_texture_set]
    return None


# ---- the shared internal tree --------------------------------------------------------

def _interface(tree):
    existing = [(item.name, item.socket_type) for item in tree.interface.items_tree
                if item.item_type == 'SOCKET' and item.in_out == 'OUTPUT']
    wanted = [(name, kind) for name, kind, _ in OUTPUTS]
    if existing == wanted:
        return
    if existing == wanted[:len(existing)]:
        # A later version added outputs: appended, so the links of the others survive.
        for name, kind in wanted[len(existing):]:
            tree.interface.new_socket(name=name, in_out='OUTPUT', socket_type=kind)
        return
    tree.interface.clear()
    for name, kind in wanted:
        tree.interface.new_socket(name=name, in_out='OUTPUT', socket_type=kind)


class _Index:
    """One pass over a tree's nodes and links, for a sync that looks nodes up by role and links
    sockets many times: ``socket.links`` walks every link of the tree on each call, so checking
    each connection that way made a sync quadratic, and every evaluation syncs."""

    def __init__(self, tree):
        self.tree = tree
        self._read()

    def _read(self):
        self.nodes = {}   # (role, bl_idname) -> the first such node, as the scan finds it
        for node in self.tree.nodes:
            self.nodes.setdefault((node.get('role'), node.bl_idname), node)
        self.into = {}    # input socket address -> addresses of the sockets linked into it
        for link in self.tree.links:
            self.into.setdefault(link.to_socket.as_pointer(), set()).add(link.from_socket.as_pointer())


def _node(index, role, kind, location):
    node = index.nodes.get((role, kind))
    if node is None:
        node = index.tree.nodes.new(kind)
        node['role'] = role
        node.label = role
        index.nodes[(role, kind)] = node
    if tuple(node.location) != location:
        node.location = location
    return node


def _connect(index, source, target):
    pointer, origin = target.as_pointer(), source.as_pointer()
    linked = index.into.get(pointer)
    if linked is None or origin not in linked:
        index.tree.links.new(source, target)
        if target.is_multi_input:
            index.into.setdefault(pointer, set()).add(origin)
        else:
            index.into[pointer] = {origin}   # the new link replaced the one the socket had


def _drop(index, predicate):
    doomed = [n for n in index.tree.nodes if predicate(n)]
    for node in doomed:
        index.tree.nodes.remove(node)
    if doomed:
        index._read()   # their links went with them


def tree_name(ts):
    """The internal tree of a set: named by its id, or as ``material_manager.repair`` recorded
    when it separated a scene copy that shares the id."""
    name = ts.get('op_node_tree') or TREE_PREFIX + ts.id
    tree = bpy.data.node_groups.get(name)
    if tree is not None:
        owner = tree.get('op_scene_owner')
        if owner is None:
            # Legacy trees carry no scene binding. Resolve the material that
            # already uses the tree before a copied scene can synchronize it.
            owner = next((scene for scene in bpy.data.scenes if hasattr(scene, 'open_painter')
                          for other in scene.open_painter.texture_sets if other.id == ts.id and
                          other.material and other.material.use_nodes and any(
                              getattr(node, 'node_tree', None) == tree for node in other.material.node_tree.nodes)), None)
        if owner is not None and owner != ts.id_data:
            name = f'{TREE_PREFIX}{ts.id} · {ts.id_data.name}'
            ts['op_node_tree'] = name
    return name


def tree_for(ts, overrides=None):
    """The internal tree of ``ts`` (created and brought up to date), or the neutral one."""
    name = EMPTY_TREE if ts is None else tree_name(ts)
    tree = bpy.data.node_groups.get(name)
    if tree is None:
        tree = bpy.data.node_groups.new(name, 'ShaderNodeTree')
        tree[OWNER] = True
        tree['op_texture_set_node'] = ts.id if ts is not None else ''
    if ts is not None:
        tree['op_scene_owner'] = ts.id_data
    sync_tree(tree, ts, overrides)
    return tree


def release(ts, material):
    """A removed set's unused ``material`` lets the set's images go: its internal tree stops
    binding them, unless a node in another material shows the set too."""
    tree = bpy.data.node_groups.get(tree_name(ts))
    if tree is None:
        return
    own = sum(1 for node in material.node_tree.nodes if node.bl_idname == NODE_ID and node.node_tree == tree)
    if tree.users > own:
        return
    for node in tree.nodes:
        if node.bl_idname == 'ShaderNodeTexImage' and node.image is not None:
            node.image = None


# Every evaluation syncs (a replaced output image must reach the node), so a drag or a stroke
# synced on each tick: 0.5-0.9 ms of RNA reads that found nothing to change. A sync whose inputs
# and tree match the last full one is skipped, for at most SYNC_RECHECK seconds.
SYNC_RECHECK = 1.0
_synced = {}   # tree session uid -> (inputs key, time of the full sync, {channel: its Image Texture node's name})


def sync(ts, images=True):
    """Bring the set's internal tree up to date (UV map, height distance, AO in the preview,
    Engine Preview, View Mask); ``images=False`` keeps the images a display bound."""
    tree = bpy.data.node_groups.get(tree_name(ts))
    if tree is None:
        return tree
    last = _synced.get(tree.session_uid)
    if last is not None and perf_counter() - last[1] < SYNC_RECHECK:
        key = _sync_key(tree, ts, images, last[2])
        if key is not None and key == last[0]:
            return tree
    sync_tree(tree, ts, images=images)
    # By name: a Python reference to a node removed since reads freed memory (no ReferenceError);
    # ``nodes.get`` only finds live ones.
    nodes = {node['role']: node.name for node in tree.nodes
             if node.bl_idname == 'ShaderNodeTexImage' and node.get('role') in CHANNELS}
    key = _sync_key(tree, ts, images, nodes)
    if key is None:
        _synced.pop(tree.session_uid, None)
    else:
        _synced[tree.session_uid] = (key, perf_counter(), nodes)
    return tree


def _sync_key(tree, ts, images, nodes):
    """Everything ``sync_tree`` reads besides the tree's own settings: the set's struct, outputs,
    UV/UDIM, height distance, AO in the preview, View Mask's snapshot; the tree's node and link
    counts and the images its channel samplers (``nodes``: channel -> node name) show (a display
    binds its own between syncs). None while Engine Preview adapts the channels (its adapters read
    the target's settings), or when a sampler is gone."""
    project = getattr(ts.id_data, 'open_painter', None)
    if project is not None and project.engine_preview:
        return None
    samplers = []
    for channel, name in nodes.items():
        node = tree.nodes.get(name)
        if node is None or node.bl_idname != 'ShaderNodeTexImage' or node.get('role') != channel:
            return None
        samplers.append((channel, node.image.session_uid if node.image is not None else 0))
    from ..ui import mask_view
    shown = mask_view.shown_image(ts)
    return (ts.as_pointer(), images, len(tree.nodes), len(tree.links), getattr(ts, 'udim_tile', 0),
            getattr(ts, 'udim_source_uv', ''), ts.height_distance, ts.preview_ao,
            shown.session_uid if shown is not None else 0,
            tuple((o.channel, o.image.session_uid if o.image is not None else 0) for o in ts.outputs),
            tuple(samplers))


def channel_nodes(ts):
    """The set's channel samplers inside its internal tree (channel → Image Texture node)."""
    tree = bpy.data.node_groups.get(tree_name(ts))
    if tree is None:
        return {}
    return {node['role']: node for node in tree.nodes
            if node.bl_idname == 'ShaderNodeTexImage' and node.get('role') in CHANNELS}


def show_image(ts, channel, image):
    """Show ``image`` for ``channel`` instead of the set's own (a display's proxy); True if it changed."""
    node = channel_nodes(ts).get(channel)
    if node is None or node.image == image:
        return False
    node.image = image
    return True


def showing_mask(ts):
    """The node shows View Mask's snapshot of ``ts`` instead of its surface."""
    tree = bpy.data.node_groups.get(tree_name(ts))
    node = next((n for n in tree.nodes if n.get('role') == MASK_VIEW), None) if tree is not None else None
    return node is not None and bool(node.outputs['Color'].links)


def ao_factor(ts):
    """How much the set's AO darkens its Base Color output: none when AO in Preview is off,
    or while Engine Preview's target applies AO to indirect light only."""
    project = getattr(ts.id_data, 'open_painter', None)
    if project is not None and project.engine_preview and project.engine_preview_ao_indirect:
        from ..targets import get
        if get(project.target).ao_usage == 'INDIRECT':
            return 0.0
    return 1.0 if ts.preview_ao else 0.0


def sync_tree(tree, ts, overrides=None, images=True):
    from ..constants import DEFAULTS
    _interface(tree)
    found = _Index(tree)
    output = _node(found, 'Output', 'NodeGroupOutput', (1400, 0))
    if not output.is_active_output:
        output.is_active_output = True
    if ts is None:
        for name, kind, channel in OUTPUTS:
            socket = output.inputs[name]
            for link in list(socket.links):
                tree.links.remove(link)
            if channel is not None and kind != 'NodeSocketVector':
                value = DEFAULTS[channel]
                socket.default_value = tuple(value) if kind == 'NodeSocketColor' else float(value[0])
        return
    vector = None
    uv_name = ''
    if getattr(ts, 'udim_tile', 0):
        from ..geometry.udim import tile_origin
        uv_name = ts.udim_source_uv
        uv = _node(found, 'UDIM Source UV', 'ShaderNodeUVMap', (-900, 0))
        if uv.uv_map != uv_name:
            uv.uv_map = uv_name
        local = _node(found, 'UDIM Local UV', 'ShaderNodeVectorMath', (-700, 0))
        if local.operation != 'SUBTRACT':
            local.operation = 'SUBTRACT'
        origin = (*tile_origin(ts.udim_tile), 0.0)
        if tuple(local.inputs[1].default_value) != origin:
            local.inputs[1].default_value = origin
        _connect(found, uv.outputs['UV'], local.inputs[0])
        vector = local.outputs['Vector']
    samples = {}
    for index, channel in enumerate(CHANNELS):
        texture = _node(found, channel, 'ShaderNodeTexImage', (-400, 450 - index * 280))
        if images:
            image = (overrides[channel] if overrides is not None and channel in overrides
                     else next((o.image for o in ts.outputs if o.channel == channel), None))
            if texture.image != image:
                texture.image = image
        if texture.interpolation != 'Linear':
            texture.interpolation = 'Linear'
        if vector is not None:
            _connect(found, vector, texture.inputs['Vector'])
        if channel in SCALARS:
            split = _node(found, channel + ' Value', 'ShaderNodeSeparateColor', (-150, 450 - index * 280))
            _connect(found, texture.outputs['Color'], split.inputs['Color'])
            samples[channel] = split.outputs['Red']
        else:
            samples[channel] = texture.outputs['Color']
    display = _display(found, ts, samples)
    ao = _node(found, 'AO Preview', 'ShaderNodeMixRGB', (700, 450))
    if ao.blend_type != 'MULTIPLY':
        ao.blend_type = 'MULTIPLY'
    factor = ao_factor(ts)
    if ao.inputs[0].default_value != factor:
        ao.inputs[0].default_value = factor
    _connect(found, display['BASE_COLOR'], ao.inputs[1])
    _connect(found, samples['AO'], ao.inputs[2])
    normal_map = _node(found, 'Normal Map', 'ShaderNodeNormalMap', (450, -250))
    if normal_map.space != 'TANGENT':
        normal_map.space = 'TANGENT'
    if normal_map.uv_map != uv_name:
        normal_map.uv_map = uv_name
    bump = _node(found, 'Height', 'ShaderNodeBump', (700, -250))
    if bump.inputs['Distance'].default_value != ts.height_distance:
        bump.inputs['Distance'].default_value = ts.height_distance
    _connect(found, display['NORMAL'], normal_map.inputs['Color'])
    _connect(found, normal_map.outputs['Normal'], bump.inputs['Normal'])
    _connect(found, samples['HEIGHT'], bump.inputs['Height'])
    tangent = _node(found, 'Tangent', 'ShaderNodeTangent', (700, -600))
    if tangent.direction_type != 'UV_MAP':
        tangent.direction_type = 'UV_MAP'
    if tangent.uv_map != uv_name:
        tangent.uv_map = uv_name
    routes = {'Base Color': ao.outputs[0], 'Metallic': display['METALLIC'], 'Roughness': display['ROUGHNESS'],
              'Normal': bump.outputs['Normal'], 'Height': samples['HEIGHT'], 'Emission': samples['EMISSION'],
              'AO': samples['AO'], 'Alpha': samples['OPACITY'], 'Normal Map': samples['NORMAL'],
              'Tangent': tangent.outputs['Tangent']}
    routes.update(_mask_view(found, ts, vector))
    for name, source in routes.items():
        _connect(found, source, output.inputs[name])


def _display(found, ts, samples):
    """The channels as the surface shows them: Engine Preview's target adapters, or as sampled."""
    from ..targets import preview
    project = getattr(ts.id_data, 'open_painter', None)
    if project is None or not project.engine_preview:
        _drop(found, lambda node: str(node.get('role', '')).startswith(preview.PREFIX))
        return samples
    return preview.adapt(ts, project, samples, lambda role, kind, location: _node(found, role, kind, location),
                         lambda source, target: _connect(found, source, target))


def _mask_view(found, ts, vector):
    """While View Mask shows a snapshot of ``ts``: the snapshot as the colour of a flat,
    rough, non-metallic surface (the outputs it replaces)."""
    from ..ui import mask_view
    image = mask_view.shown_image(ts)
    if image is None:
        _drop(found, lambda node: node.get('role') in MASK_VIEW_ROLES)
        return {}
    texture = _node(found, MASK_VIEW, 'ShaderNodeTexImage', (700, 900))
    if texture.image != image:
        texture.image = image
    if vector is not None:
        _connect(found, vector, texture.inputs['Vector'])
    values = {}
    for role, value, location in ((MASK_VIEW_ROLES[1], 1.0, (1000, 900)), (MASK_VIEW_ROLES[2], 0.0, (1000, 780))):
        node = _node(found, role, 'ShaderNodeValue', location)
        if node.outputs[0].default_value != value:
            node.outputs[0].default_value = value
        values[role] = node.outputs[0]
    geometry = _node(found, MASK_VIEW_ROLES[3], 'ShaderNodeNewGeometry', (1000, 660))
    return {'Base Color': texture.outputs['Color'], 'Roughness': values[MASK_VIEW_ROLES[1]],
            'Metallic': values[MASK_VIEW_ROLES[2]], 'Normal': geometry.outputs['Normal']}


# ---- the node ---------------------------------------------------------------------------

class OP_ShaderNodeTextureSet(bpy.types.ShaderNodeCustomGroup):
    """An Open Painter texture set's channels, evaluated by Open Painter"""
    bl_idname = NODE_ID
    bl_label = 'Open Painter Texture Set'
    bl_icon = 'TEXTURE'
    bl_width_default = 240

    texture_set_id: bpy.props.StringProperty(name='Texture Set ID', update=_set_changed)
    texture_set: bpy.props.EnumProperty(name='Texture Set', description='The texture set whose channels this node reads',
                                        items=_set_items, get=_get_set, set=_set_set)

    def init(self, context):
        if not _creating:
            bind(self, _active_set(context))

    def draw_buttons(self, context, layout):
        layout.prop(self, 'texture_set', text='')
        ts = find_set(self.texture_set_id)
        if self.texture_set_id and ts is None:
            layout.label(text='Texture set missing', icon='ERROR')
        elif ts is not None and getattr(ts, 'preview_backend', 'LIVE') == 'LIVE':
            layout.label(text='Evaluated by Open Painter', icon='CHECKMARK')

    def draw_label(self):
        ts = find_set(self.texture_set_id)
        return f'Open Painter · {ts.name}' if ts is not None else self.bl_label


class OP_OT_add_texture_set_node(bpy.types.Operator):
    """Add a node giving this material an Open Painter texture set's channels"""
    bl_idname = 'open_painter.add_texture_set_node'
    bl_label = 'Open Painter Texture Set'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return (getattr(space, 'type', '') == 'NODE_EDITOR' and space.tree_type == 'ShaderNodeTree'
                and space.edit_tree is not None)

    def invoke(self, context, event):
        tree = context.space_data.edit_tree
        before = {node.as_pointer() for node in tree.nodes}
        with creating():
            bpy.ops.node.add_node('INVOKE_DEFAULT', type=NODE_ID, use_transform=True)
        for node in tree.nodes:
            if node.bl_idname == NODE_ID and node.as_pointer() not in before:
                bind(node, _active_set(context))
        return {'FINISHED'}


def draw_menu(self, context):
    if getattr(context.space_data, 'tree_type', '') != 'ShaderNodeTree':
        return
    self.layout.separator()
    self.layout.operator_context = 'INVOKE_DEFAULT'
    self.layout.operator(OP_OT_add_texture_set_node.bl_idname, icon='TEXTURE')


CLASSES = (OP_ShaderNodeTextureSet, OP_OT_add_texture_set_node)
MENUS = ('NODE_MT_category_shader_input', 'NODE_MT_category_shader_texture')


def register():
    for name in MENUS:
        menu = getattr(bpy.types, name, None)
        if menu is not None:
            menu.append(draw_menu)


def unregister():
    for name in MENUS:
        menu = getattr(bpy.types, name, None)
        if menu is not None:
            menu.remove(draw_menu)
