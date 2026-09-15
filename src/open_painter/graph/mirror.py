"""Keep a graph tree equal to the projection of its texture set.

``project(ts)`` computes the expected nodes, sockets and links from authoring
data; ``reconcile`` edits the tree incrementally until it matches; ``sync`` is
called from the UI lifecycle tick and only reconciles when the structural
signature changed (numeric edits bump the edit serial too, so the serial is
not the trigger). Nodes are found by ``op_id``; no RNA reference survives a
tick, matching the rest of the add-on's collection-reallocation discipline.
"""
from contextlib import contextmanager
from hashlib import sha256

import bpy

from ..constants import CHANNEL_LABELS, CHANNELS, OWNER
from ..core.layer_stack import channel_for
from ..evaluation import anchors
from ..evaluation.anchors import DETAIL_KINDS
from . import layout
from .tree import (NODE_TYPES, OUTPUT_NODE, SOCKET_CONTENT, SOCKET_COVERAGE, SOCKET_SCALAR,
                   TREE_ID, is_graph)

SCALAR_CHANNELS = frozenset({'ROUGHNESS', 'METALLIC', 'HEIGHT', 'AO', 'OPACITY'})
FRAME_PREFIX = 'frame:'
_syncing = False


@contextmanager
def syncing():
    """Our own tree edits must not be read back as user link edits."""
    global _syncing
    previous, _syncing = _syncing, True
    try:
        yield
    finally:
        _syncing = previous


def is_syncing():
    return _syncing


# ---- projection ------------------------------------------------------------

def _content_socket(channel):
    return SOCKET_SCALAR if channel in SCALAR_CHANNELS else SOCKET_CONTENT


def legacy_targets(ts):
    """{(layer id, channel or 'MASK')} whole-layer references still in use."""
    found = set()
    for layer in ts.layers:
        for mask in layer.masks:
            if mask.source == 'REFERENCE' and not mask.reference_anchor and mask.reference_layer:
                found.add((mask.reference_layer, mask.reference_channel))
    return found


def project(ts):
    """Expected graph: nodes {id: spec}, frames {id: label}, links [(from, socket, to, socket)]."""
    registry = anchors.index(ts)
    legacy = legacy_targets(ts)
    nodes, frames, links = {}, {}, []
    for layer in ts.layers:
        inputs, outputs = [], []
        if layer.type == 'FILL':
            for prop in layer.channels:
                if prop.enabled:
                    inputs.append((f'content#{prop.channel}', CHANNEL_LABELS.get(prop.channel, prop.channel),
                                   _content_socket(prop.channel)))
        for mask in layer.masks:
            if mask.source == 'REFERENCE':
                inputs.append((f'mask#{mask.id}', 'Mask: ' + mask.name, SOCKET_COVERAGE))
            elif mask.source == 'GENERATOR' and mask.generator in DETAIL_KINDS:
                inputs.append((f'detail#{mask.id}#HEIGHT', 'Micro Height: ' + mask.name, SOCKET_SCALAR))
                inputs.append((f'detail#{mask.id}#NORMAL', 'Micro Normal: ' + mask.name, SOCKET_CONTENT))
        outputs.append(('out', 'Output' if layer.publish_output else 'Output (unpublished)', SOCKET_CONTENT))
        if layer.type == 'FOLDER':
            outputs.append(('group', 'Composite' if layer.publish_group else 'Composite (unpublished)', SOCKET_CONTENT))
        for mask in layer.masks:
            if mask.source == 'ANCHOR' and mask.id:
                outputs.append((mask.id, mask.name, SOCKET_COVERAGE))
        for prop in layer.channels:
            for effect in prop.effects:
                if effect.kind == 'ANCHOR' and effect.id:
                    outputs.append((effect.id, f'{effect.name} ({CHANNEL_LABELS.get(prop.channel, prop.channel)})',
                                    _content_socket(prop.channel)))
        for target, channel in sorted(legacy):
            if target == layer.id:
                outputs.append((f'legacy#{channel}', 'Whole ' + ('mask stack' if channel == 'MASK'
                                                                 else CHANNEL_LABELS.get(channel, channel)),
                                SOCKET_COVERAGE if channel == 'MASK' else _content_socket(channel)))
        # A folder node sits inside its own frame, above the children it composes.
        frame = FRAME_PREFIX + (layer.id if layer.type == 'FOLDER' else layer.parent_id)
        nodes[layer.id] = {'kind': 'FOLDER' if layer.type == 'FOLDER' else 'LAYER', 'label': layer.name,
                           'inputs': inputs, 'outputs': outputs,
                           'frame': frame if (layer.type == 'FOLDER' or layer.parent_id) else ''}
        if layer.type == 'FOLDER':
            frames[FRAME_PREFIX + layer.id] = {'label': layer.name,
                                               'frame': FRAME_PREFIX + layer.parent_id if layer.parent_id else ''}
    nodes[OUTPUT_NODE] = {'kind': 'OUTPUT', 'label': 'Texture Set Output', 'frame': '',
                          'inputs': [(f'final#{channel}', CHANNEL_LABELS.get(channel, channel),
                                      _content_socket(channel)) for channel in CHANNELS],
                          'outputs': []}
    # Links: every consumer field, plus the informational stack result.
    for layer in ts.layers:
        for prop in layer.channels:
            if getattr(prop, 'anchor', '') and prop.enabled and layer.type == 'FILL':
                anchor = registry.get(prop.anchor)
                if anchor is not None:
                    links.append((anchor.layer_id, _producer_socket(anchor), layer.id, f'content#{prop.channel}'))
        for mask in layer.masks:
            if mask.source == 'REFERENCE':
                if mask.reference_anchor:
                    anchor = registry.get(mask.reference_anchor)
                    if anchor is not None:
                        links.append((anchor.layer_id, _producer_socket(anchor), layer.id, f'mask#{mask.id}'))
                elif mask.reference_layer in nodes:
                    links.append((mask.reference_layer, f'legacy#{mask.reference_channel}', layer.id, f'mask#{mask.id}'))
            elif mask.source == 'GENERATOR' and mask.generator in DETAIL_KINDS:
                for slot, identifier in (('HEIGHT', mask.detail_height_anchor), ('NORMAL', mask.detail_normal_anchor)):
                    anchor = registry.get(identifier) if identifier else None
                    if anchor is not None:
                        links.append((anchor.layer_id, _producer_socket(anchor), layer.id, f'detail#{mask.id}#{slot}'))
    roots = [layer for layer in ts.layers if not layer.parent_id]
    if roots:
        top = roots[-1]
        for channel in CHANNELS:
            links.append((top.id, 'out', OUTPUT_NODE, f'final#{channel}'))
    return nodes, frames, links


def _producer_socket(anchor):
    if anchor.kind == 'OUTPUT':
        return 'out'
    if anchor.kind == 'GROUP':
        return 'group'
    return anchor.id


def signature(ts):
    """Structural fingerprint of everything the projection reads."""
    parts = []
    for layer in ts.layers:
        parts.append((layer.id, layer.type, layer.parent_id, layer.name, layer.visible, layer.blend_mode,
                      layer.group_mode, layer.publish_output, layer.publish_group,
                      tuple((m.id, m.source, m.name, m.enabled, m.generator, m.reference_anchor, m.reference_layer,
                             m.reference_channel, m.reference_mask, m.detail_height_anchor, m.detail_normal_anchor)
                            for m in layer.masks),
                      tuple((p.channel, p.enabled, p.anchor, p.anchor_channel,
                             tuple((e.kind, e.id, e.name) for e in p.effects)) for p in layer.channels)))
    return sha256(repr(parts).encode()).hexdigest()


# ---- tree ownership ----------------------------------------------------------

def find_tree(ts):
    return next((tree for tree in bpy.data.node_groups
                 if tree.bl_idname == TREE_ID and tree.get('op_graph_set') == ts.id), None)


def ensure_tree(ts):
    tree = find_tree(ts)
    if tree is None:
        tree = bpy.data.node_groups.new('OP Graph · ' + ts.name, TREE_ID)
        tree[OWNER] = True
        tree['op_graph_set'] = ts.id
        tree.use_fake_user = True
    reconcile(tree, ts)
    return tree


def texture_set_of(tree, scene=None):
    """The texture set a graph tree mirrors, searched in the given scene first."""
    identifier = tree.get('op_graph_set') if tree is not None else None
    if not identifier:
        return None
    scenes = [scene] if scene is not None else []
    scenes += [item for item in bpy.data.scenes if item is not scene]
    for candidate in scenes:
        project_data = getattr(candidate, 'open_painter', None)
        if project_data is None:
            continue
        for ts in project_data.texture_sets:
            if ts.id == identifier:
                return ts
    return None


# ---- reconciliation ------------------------------------------------------------

def nodes_by_id(tree):
    return {node.op_id if hasattr(node, 'op_id') else node.get('op_id', ''): node for node in tree.nodes}


def _sockets(collection, specs):
    """Make ``collection`` hold exactly ``specs`` = [(identifier, name, type)], in order."""
    wanted = {identifier for identifier, _, _ in specs}
    for socket in list(collection):
        if socket.identifier not in wanted:
            collection.remove(socket)
    existing = {socket.identifier: socket for socket in collection}
    for identifier, name, kind in specs:
        socket = existing.get(identifier)
        if socket is not None and socket.bl_idname != kind:
            collection.remove(socket)
            socket = None
        if socket is None:
            socket = collection.new(kind, name, identifier=identifier)
            existing[identifier] = socket
        if socket.name != name:
            socket.name = name
    for index, (identifier, _, _) in enumerate(specs):
        current = next(i for i, socket in enumerate(collection) if socket.identifier == identifier)
        if current != index:
            collection.move(current, index)


def reconcile(tree, ts, force_layout=False):
    """Edit ``tree`` in place until it equals ``project(ts)``; positions of known nodes survive."""
    nodes, frames, links = project(ts)
    with syncing():
        existing = nodes_by_id(tree)
        for identifier, node in list(existing.items()):
            wanted = (identifier in nodes and node.bl_idname == NODE_TYPES[nodes[identifier]['kind']]) or \
                     (identifier in frames and node.bl_idname == 'NodeFrame')
            if not identifier or not wanted:
                tree.nodes.remove(node)
        existing = nodes_by_id(tree)
        created = []
        for identifier, spec in frames.items():
            node = existing.get(identifier)
            if node is None:
                node = tree.nodes.new('NodeFrame')
                node['op_id'] = identifier
                node.shrink = True
                existing[identifier] = node
            node.label = spec['label']
        for identifier, spec in nodes.items():
            node = existing.get(identifier)
            if node is None:
                node = tree.nodes.new(NODE_TYPES[spec['kind']])
                node.op_id, node.op_kind = identifier, spec['kind']
                node.name = identifier
                node.width = 220
                existing[identifier] = node
                created.append(identifier)
            node.label = spec['label']
            _sockets(node.inputs, spec['inputs'])
            _sockets(node.outputs, spec['outputs'])
            for socket in node.inputs:
                socket.link_limit = 1
        for identifier, spec in list(nodes.items()) + list(frames.items()):
            node = existing[identifier]
            parent = existing.get(spec['frame']) if spec['frame'] else None
            if node.parent != parent:
                node.parent = parent
        tree.links.clear()
        for source, from_socket, target, to_socket in links:
            producer, consumer = existing.get(source), existing.get(target)
            if producer is None or consumer is None:
                continue
            a = next((s for s in producer.outputs if s.identifier == from_socket), None)
            b = next((s for s in consumer.inputs if s.identifier == to_socket), None)
            if a is not None and b is not None:
                tree.links.new(a, b)
        place(tree, ts, existing, nodes, links, only=None if force_layout else set(created))
        tree['op_signature'] = signature(ts)
        tree['op_serial'] = ts.edit_serial
    return tree


def place(tree, ts, existing, nodes, links, only=None):
    """Position nodes with the pure layout; ``only`` restricts to newly created ids."""
    from ..core.layer_stack import visible_rows
    rows = [(ts.layers[index].id, depth) for index, depth in visible_rows(ts)]
    seen = {identifier for identifier, _ in rows}
    rows += [(layer.id, 0) for layer in ts.layers if layer.id not in seen]   # collapsed folders' children
    edges = [(source, target) for source, _, target, _ in links if target != OUTPUT_NODE]
    sockets = {identifier: len(spec['inputs']) + len(spec['outputs']) for identifier, spec in nodes.items()}
    coordinates = layout.positions(rows, edges, output=OUTPUT_NODE, sockets=sockets)
    for identifier, (x, y) in coordinates.items():
        if only is not None and identifier not in only:
            continue
        node = existing.get(identifier)
        if node is not None:
            node.location_absolute = (x, y)


# ---- lifecycle -----------------------------------------------------------------

def sync(window):
    """Reconcile graph areas of a window when their set changed; mirror the selection."""
    scene = window.scene
    for area in window.screen.areas:
        if area.type != 'NODE_EDITOR' or area.spaces.active.tree_type != TREE_ID:
            continue
        tree = area.spaces.active.node_tree
        if not is_graph(tree):
            continue
        ts = texture_set_of(tree, scene)
        if ts is None:
            continue
        try:
            if tree.get('op_signature') != signature(ts) or tree.get('op_links_dirty'):
                tree['op_links_dirty'] = False
                reconcile(tree, ts)
                area.tag_redraw()
            if mirror_selection(tree, ts, window, area):
                area.tag_redraw()
        except Exception as error:   # a stale tree must never break the UI timer
            from ..utils.logging import logger
            logger.error('Open Graph sync: %s', error)


def mirror_selection(tree, ts, window, area):
    """Two-way active selection between the graph and the layer list, without ping-pong."""
    from ..core.layer_stack import active
    from ..ui.dock import state
    recorded = tree.get('op_active', '')
    current = active(ts)
    current_id = current.id if current is not None else ''
    node = tree.nodes.active
    node_id = getattr(node, 'op_id', '') if node is not None and node.select else ''
    if node_id and node_id != recorded and node_id in {layer.id for layer in ts.layers}:
        # The user clicked a node: follow it in the layer list.
        ts.active_layer = next(i for i, layer in enumerate(ts.layers) if layer.id == node_id)
        for other in window.screen.areas:
            if other.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                ui = state.get(other)
                ui.selected_layers = {node_id}
                other.tag_redraw()
        tree['op_active'] = node_id
        return True
    if current_id and current_id != recorded:
        # The layer list moved: select the matching node.
        with syncing():
            for item in tree.nodes:
                item.select = getattr(item, 'op_id', '') == current_id
            target = next((item for item in tree.nodes if getattr(item, 'op_id', '') == current_id), None)
            if target is not None:
                tree.nodes.active = target
        tree['op_active'] = current_id
        return True
    return False


# ---- drawing --------------------------------------------------------------------

def _layer_of(node, context):
    tree = node.id_data
    ts = texture_set_of(tree, context.scene if context else None)
    if ts is None:
        return None, None
    return ts, next((layer for layer in ts.layers if layer.id == node.op_id), None)


def node_label(node):
    return node.label or node.bl_label


def draw_node(node, context, layout):
    ts, layer = _layer_of(node, context)
    if node.op_kind == 'OUTPUT':
        layout.label(text=ts.name if ts else 'Texture set', icon='TEXTURE')
        return
    if layer is None:
        layout.label(text='Layer removed', icon='ERROR')
        return
    row = layout.row(align=True)
    row.prop(layer, 'visible', text='', icon='HIDE_OFF' if layer.visible else 'HIDE_ON')
    row.prop(layer, 'opacity', text='Opacity', slider=True)
    if layer.type == 'FOLDER':
        layout.prop(layer, 'group_mode', text='')
    else:
        layout.prop(layer, 'blend_mode', text='')
    row = layout.row(align=True)
    row.prop(layer, 'publish_output', text='Publish', toggle=True, icon='ANCHOR_CENTER')
    if layer.type == 'FOLDER':
        row.prop(layer, 'publish_group', text='Composite', toggle=True)
    op = layout.operator('open_painter.graph_select', text='Edit in Panel', icon='PROPERTIES')
    op.layer_id = layer.id
