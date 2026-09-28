"""Keep a graph tree equal to the projection of its texture set.

``project(ts)`` computes the expected nodes, sockets and links from authoring
data; ``reconcile`` edits the tree incrementally until it matches; ``sync`` is
called from the UI lifecycle tick and only reconciles when the structural
signature changed (numeric edits bump the edit serial too, so the serial is
not the trigger). Nodes are found by ``op_id``; no RNA reference survives a
tick, matching the rest of the add-on's collection-reallocation discipline.
"""
from contextlib import contextmanager
from functools import lru_cache
from hashlib import sha256

import bpy

from ..constants import CHANNEL_LABELS, CHANNELS, OWNER
from ..core.layer_stack import channel_for, visible_rows
from ..evaluation import anchors
from ..evaluation.anchors import DETAIL_KINDS
from . import layout
from .tree import (MASK_NODE_TYPES, NODE_TYPES, OUTPUT_NODE, SOCKET_COLOR, SOCKET_CONTENT, SOCKET_COVERAGE, SOCKET_DESCRIPTIONS,
                   SOCKET_NORMAL, SOCKET_SCALAR, TREE_ID, IMAGE_PREFIX, is_graph)

SCALAR_CHANNELS = frozenset({'ROUGHNESS', 'METALLIC', 'HEIGHT', 'AO', 'OPACITY'})
FRAME_PREFIX = 'frame:'
STACK_PREFIX = 'stack#'   # one Texture Set Output input per root layer: <prefix><layer id>
# Bumped when the projection's sockets change: trees saved with the previous socket types
# reconcile once on load (the signature otherwise depends on the stack alone).
GRAPH_FORMAT = 9
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
    """The socket type a channel's content actually is: scalar, colour or normal (never a bundle)."""
    if channel in SCALAR_CHANNELS:
        return SOCKET_SCALAR
    return SOCKET_NORMAL if channel == 'NORMAL' else SOCKET_COLOR


def legacy_targets(ts):
    """{(layer id, channel or 'MASK')} whole-layer references still in use."""
    found = set()
    for layer in ts.layers:
        for mask in layer.masks:
            if mask.source == 'REFERENCE' and not mask.reference_anchor and mask.reference_layer:
                found.add((mask.reference_layer, mask.reference_channel))
    return found


def mask_node_id(layer, index, mask):
    return mask.id or f'{layer.id}/mask/{index}'


def effect_node_id(layer, channel, index, effect):
    return effect.id or f'{layer.id}/{channel}/{index}'


def layer_frame_id(layer):
    return FRAME_PREFIX + 'layer:' + layer.id


def mask_frame_id(mask):
    return FRAME_PREFIX + 'mask:' + mask.id


_owner_indexes = {}   # set struct address -> {node id: (layer, mask, channel, effect) positions}


def _owner_index(ts):
    index = {}
    for li, layer in enumerate(ts.layers):
        index[layer.id] = (li, None, None, None)
        for mi, mask in enumerate(layer.masks):
            index[mask_node_id(layer, mi, mask)] = (li, mi, None, None)
        for ci, prop in enumerate(layer.channels):
            for ei, effect in enumerate(prop.effects):
                index[effect_node_id(layer, prop.channel, ei, effect)] = (li, None, ci, ei)
    return index


def _at(ts, node_id, position):
    """The items at ``position`` when they still carry ``node_id``, else None."""
    li, mi, ci, ei = position
    layers = ts.layers
    if li >= len(layers):
        return None
    layer = layers[li]
    if mi is None and ci is None:
        return (layer, None, None, None) if layer.id == node_id else None
    if mi is not None:
        masks = layer.masks
        if mi >= len(masks):
            return None
        mask = masks[mi]
        return (layer, mask, None, None) if mask_node_id(layer, mi, mask) == node_id else None
    channels = layer.channels
    if ci >= len(channels):
        return None
    prop = channels[ci]
    effects = prop.effects
    if ei >= len(effects):
        return None
    effect = effects[ei]
    return (layer, None, prop, effect) if effect_node_id(layer, prop.channel, ei, effect) == node_id else None


def owner_of(ts, node_id):
    """(layer, mask, channel prop, effect) behind a node id; unknown parts are None.

    Every drawn graph node asks this, so positions are indexed per set and each hit
    is verified against the id it was stored for: a stale index (any stack edit)
    re-indexes once instead of returning another item.
    """
    if not node_id:
        return None, None, None, None
    key = ts.as_pointer()
    index = _owner_indexes.get(key)
    if index is not None:
        position = index.get(node_id)
        found = _at(ts, node_id, position) if position is not None else None
        if found is not None:
            return found
    if len(_owner_indexes) > 64:
        _owner_indexes.clear()
    index = _owner_indexes[key] = _owner_index(ts)
    position = index.get(node_id)
    found = _at(ts, node_id, position) if position is not None else None
    return found if found is not None else (None, None, None, None)


def project(ts):
    """Expected graph: nodes {id: spec}, frames {id: spec}, links [(from, socket, to, socket)].

    Every layer, mask and channel filter is a node. Links show the actual data
    flow: enabled masks chain into the layer's Mask input, enabled filters
    into their channel content, nested siblings into the Backdrop of the layer
    above them, the top child into its folder's Children input, every root
    layer into its own Texture Set Output socket, and every anchor reference.
    Parked (disabled) masks and filters draw no chain wires: unlinking parks,
    linking re-enables. Each stack socket is a material bundle spanning every
    channel (listed under the output spec's background ``maps`` key), so the
    compact single-line view loses no channel data. Root order reads from Y
    rows, so roots never link to each other.
    """
    registry = anchors.index(ts)
    legacy = legacy_targets(ts)
    nodes, frames, links = {}, {}, []
    children = {}
    for layer in ts.layers:
        children.setdefault(layer.parent_id, []).append(layer)
    for layer in ts.layers:
        outer = FRAME_PREFIX + layer.parent_id if layer.parent_id else ''
        own_frame = FRAME_PREFIX + layer.id if layer.type == 'FOLDER' else outer
        if layer.type == 'FOLDER':
            frames[own_frame] = {'label': layer.name, 'frame': outer}
        has_chain = bool(layer.masks) or any(prop.effects for prop in layer.channels)
        if layer.type != 'FOLDER' and has_chain:
            frames[layer_frame_id(layer)] = {'label': '', 'frame': outer}
            own_frame = layer_frame_id(layer)
        inputs = [('below', 'Backdrop', SOCKET_CONTENT)]
        if layer.type == 'FOLDER':
            inputs.append(('children', 'Children', SOCKET_CONTENT))
        # Every layer shows its Mask input, even before it has a mask: the
        # node editor is a first-class authoring surface, not a mirror that
        # only lights up after panel edits.
        inputs.append(('mask', 'Mask', SOCKET_COVERAGE))
        for prop in layer.channels:
            if (layer.type == 'FILL' and prop.enabled) or prop.effects:
                inputs.append((f'content#{prop.channel}', CHANNEL_LABELS.get(prop.channel, prop.channel),
                               _content_socket(prop.channel)))
        outputs = [('out', 'Output' if layer.publish_output else 'Output (unpublished)', SOCKET_CONTENT)]
        if layer.type == 'FOLDER':
            outputs.append(('group', 'Composite' if layer.publish_group else 'Composite (unpublished)', SOCKET_CONTENT))
        for target, channel in sorted(legacy):
            if target == layer.id:
                outputs.append((f'legacy#{channel}', 'Whole ' + ('mask stack' if channel == 'MASK'
                                                                 else CHANNEL_LABELS.get(channel, channel)),
                                SOCKET_COVERAGE if channel == 'MASK' else _content_socket(channel)))
        nodes[layer.id] = {'kind': 'FOLDER' if layer.type == 'FOLDER' else 'LAYER', 'label': layer.name,
                           'inputs': inputs, 'outputs': outputs, 'frame': own_frame, 'owner': layer.id, 'channel': ''}
        # Mask chain: previous coverage in, coverage out, ending in the layer's Mask input.
        previous = {}
        groups = {mask.id: mask for mask in layer.masks if mask.source == 'GROUP'}
        for index, mask in enumerate(layer.masks):
            identifier = mask_node_id(layer, index, mask)
            mask_inputs = [('prev', 'Previous', SOCKET_COVERAGE)]
            parent = mask.parent_id if mask.parent_id in groups else ''
            mask_frame = mask_frame_id(groups[parent]) if parent else own_frame
            if mask.source == 'GROUP':
                frames[mask_frame_id(mask)] = {'label': mask.name, 'frame': mask_frame}
                mask_frame = mask_frame_id(mask)
                mask_inputs.append(('members', 'Members', SOCKET_COVERAGE))
            elif mask.source == 'REFERENCE':
                mask_inputs.append(('ref', 'Published Input', SOCKET_COVERAGE))
            elif mask.source == 'GENERATOR' and mask.generator in DETAIL_KINDS:
                mask_inputs.append(('detail#HEIGHT', 'Micro Height', SOCKET_SCALAR))
                mask_inputs.append(('detail#NORMAL', 'Micro Normal', SOCKET_NORMAL))
            label = mask.name or mask.source.title()
            if mask.source == 'ANCHOR':
                label = 'Anchor: ' + label
            nodes[identifier] = {'kind': 'MASK', 'label': label, 'inputs': mask_inputs,
                                 'node_type': MASK_NODE_TYPES.get(mask.source, NODE_TYPES['MASK']),
                                 'outputs': [('out', 'Coverage', SOCKET_COVERAGE)],
                                 'frame': mask_frame, 'owner': layer.id, 'channel': ''}
            if mask.enabled:
                if parent in previous:
                    links.append((previous[parent], 'out', identifier, 'prev'))
                previous[parent] = identifier
        for parent, identifier in previous.items():
            links.append((identifier, 'out', parent or layer.id, 'members' if parent else 'mask'))
        # Filter chains per channel: content in, content out, ending in the channel input.
        for prop in layer.channels:
            previous = None
            for index, effect in enumerate(prop.effects):
                identifier = effect_node_id(layer, prop.channel, index, effect)
                kind = _content_socket(prop.channel)
                # The name property defaults to "Levels" whatever the kind: trust it only
                # when it was set to something else (anchors are named by the artist).
                kind_label = effect.kind.replace('_', ' ').title()
                label = effect.name if effect.name and effect.name != 'Levels' else kind_label
                if effect.kind == 'ANCHOR':
                    label = 'Anchor: ' + (effect.name or kind_label)
                nodes[identifier] = {'kind': 'EFFECT', 'label': f'{label} ({CHANNEL_LABELS.get(prop.channel, prop.channel)})',
                                     'inputs': [('in', 'Content', kind)], 'outputs': [('out', 'Content', kind)],
                                     'frame': own_frame, 'owner': layer.id, 'channel': prop.channel}
                if effect.enabled:
                    if previous is not None:
                        links.append((previous, 'out', identifier, 'in'))
                    previous = identifier
            if previous is not None:
                links.append((previous, 'out', layer.id, f'content#{prop.channel}'))
    by_layer = {layer.id: layer for layer in ts.layers}
    root_ids = [ts.layers[index].id for index, _ in visible_rows(ts)
                if not ts.layers[index].parent_id]
    stack_inputs = [(f'{STACK_PREFIX}{lid}', by_layer[lid].name, SOCKET_CONTENT) for lid in root_ids]
    nodes[OUTPUT_NODE] = {'kind': 'OUTPUT', 'label': 'Texture Set Output', 'frame': '',
                          'inputs': stack_inputs,
                          # Background data, never drawn: the bundle each stack
                          # socket carries spans every channel, so the compact
                          # single-line view loses no information.
                          'maps': list(CHANNELS),
                          'outputs': [], 'owner': '', 'channel': ''}
    # Anchor references: the consumer socket sits on the first filter of a
    # filtered channel, on the layer otherwise; on the mask node for masks.
    for layer in ts.layers:
        for prop in layer.channels:
            if getattr(prop, 'anchor', '') and prop.enabled and layer.type == 'FILL':
                anchor = registry.get(prop.anchor)
                if anchor is not None:
                    links.append((*producer_of(ts, anchor), *consumer_of(layer, prop)))
        for index, mask in enumerate(layer.masks):
            identifier = mask_node_id(layer, index, mask)
            if mask.source == 'REFERENCE':
                if mask.reference_anchor:
                    anchor = registry.get(mask.reference_anchor)
                    if anchor is not None:
                        links.append((*producer_of(ts, anchor), identifier, 'ref'))
                elif mask.reference_layer in nodes:
                    links.append((mask.reference_layer, f'legacy#{mask.reference_channel}', identifier, 'ref'))
            elif mask.source == 'GENERATOR' and mask.generator in DETAIL_KINDS:
                for slot, anchor_id in (('HEIGHT', mask.detail_height_anchor), ('NORMAL', mask.detail_normal_anchor)):
                    anchor = registry.get(anchor_id) if anchor_id else None
                    if anchor is not None:
                        links.append((*producer_of(ts, anchor), identifier, f'detail#{slot}'))
    # Composition: nested siblings chain bottom to top through Backdrop, the top
    # child feeds its folder's Children, and every root layer feeds the output
    # node from its own socket. Root order reads from Y rows, so roots never
    # link to each other.
    for parent_id, siblings in children.items():
        if not parent_id:
            continue
        for below, above in zip(siblings, siblings[1:]):
            links.append((below.id, 'out', above.id, 'below'))
        if parent_id in nodes:
            links.append((siblings[-1].id, 'out', parent_id, 'children'))
    for root in children.get('', []):
        links.append((root.id, 'out', OUTPUT_NODE, f'{STACK_PREFIX}{root.id}'))
    return nodes, frames, links


def consumer_of(layer, prop):
    """(node id, socket) an anchored channel links into: the first filter, else the layer."""
    if prop.effects:
        return effect_node_id(layer, prop.channel, 0, prop.effects[0]), 'in'
    return layer.id, f'content#{prop.channel}'


def producer_of(ts, anchor):
    """(node id, socket) that publishes ``anchor``."""
    if anchor.kind == 'OUTPUT':
        return anchor.layer_id, 'out'
    if anchor.kind == 'GROUP':
        return anchor.layer_id, 'group'
    return anchor.id, 'out'   # mask and channel anchors are nodes of their own


@lru_cache(maxsize=64)
def _signature_digest(parts):
    # Keep the serialized signature identical to existing saved graph trees.
    return sha256(repr(list(parts)).encode()).hexdigest()


def clear_caches():
    _signature_digest.cache_clear()
    _owner_indexes.clear()


def signature(ts):
    """Structural fingerprint of everything the projection reads."""
    parts = []
    for layer in ts.layers:
        parts.append((layer.id, layer.type, layer.parent_id, layer.name, layer.visible, layer.blend_mode,
                      layer.group_mode, layer.publish_output, layer.publish_group,
                      tuple((m.id, m.parent_id, m.source, m.name, m.enabled, m.generator, m.reference_anchor, m.reference_layer,
                             m.reference_channel, m.reference_mask, m.detail_height_anchor, m.detail_normal_anchor,
                             m.effect.kind if m.source == 'FILTER' else '')
                            for m in layer.masks),
                      tuple((p.channel, p.enabled, p.anchor, p.anchor_channel,
                             tuple((e.kind, e.id, e.name, e.enabled) for e in p.effects)) for p in layer.channels)))
    parts.append(('format', GRAPH_FORMAT))
    # Always read live RNA: raw names, IDs and collection edits need not bump
    # edit_serial. Only serialization/hashing of identical content is reused.
    return _signature_digest(tuple(parts))


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

def synced(tree):
    """(link keys, mask/filter node ids) the tree showed after its last reconcile."""
    links = {tuple(item.split('\t')) for item in tree.get('op_links', [])}
    return links, set(tree.get('op_nodes', []))


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
        description = SOCKET_DESCRIPTIONS.get(kind, '')
        if socket.description != description:
            socket.description = description   # the tooltip says what the socket carries
    for index, (identifier, _, _) in enumerate(specs):
        current = next(i for i, socket in enumerate(collection) if socket.identifier == identifier)
        if current != index:
            collection.move(current, index)


def reconcile(tree, ts, force_layout=False):
    """Edit ``tree`` in place until it equals ``project(ts)``; positions of known nodes survive."""
    nodes, frames, links = project(ts)
    with syncing():
        # Duplicated or pasted nodes share an id with the original: keep one.
        seen = set()
        for node in list(tree.nodes):
            identifier = node.op_id if hasattr(node, 'op_id') else node.get('op_id', '')
            if identifier in seen:
                tree.nodes.remove(node)
            seen.add(identifier)
        existing = nodes_by_id(tree)
        replaced = {}
        for identifier, node in list(existing.items()):
            wanted = (identifier in nodes and node.bl_idname == nodes[identifier].get(
                'node_type', NODE_TYPES[nodes[identifier]['kind']])) or \
                     (identifier in frames and node.bl_idname == 'NodeFrame') or \
                     (getattr(node, 'op_kind', '') == 'IMAGE' and identifier.startswith(IMAGE_PREFIX))
            if not identifier or not wanted:
                if identifier in nodes:
                    replaced[identifier] = (tuple(node.location_absolute), node.width, node.hide)
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
                node = tree.nodes.new(spec.get('node_type', NODE_TYPES[spec['kind']]))
                node.op_id, node.op_kind = identifier, spec['kind']
                node.name = identifier
                node.width = 220 if spec['kind'] in {'LAYER', 'FOLDER', 'OUTPUT'} else 190
                existing[identifier] = node
                if identifier in replaced:
                    node.location_absolute, node.width, node.hide = replaced[identifier]
                else:
                    created.append(identifier)
            node.label = spec['label']
            node.op_owner, node.op_channel = spec.get('owner', ''), spec.get('channel', '')
            _sockets(node.inputs, spec['inputs'])
            _sockets(node.outputs, spec['outputs'])
            for socket in node.inputs:
                socket.link_limit = 1
        for node in tree.nodes:
            if getattr(node, 'op_kind', '') == 'IMAGE':
                # Free nodes keep one bundle output; the feed links below are
                # redrawn from the recorded mapping, never from the projection.
                _sockets(node.inputs, [])
                _sockets(node.outputs, [('out', 'Image', SOCKET_CONTENT)])
        for identifier, spec in list(nodes.items()) + list(frames.items()):
            node = existing[identifier]
            parent = existing.get(spec['frame']) if spec['frame'] else None
            if node.parent != parent:
                position = tuple(node.location_absolute)
                node.parent = parent
                node.location_absolute = position
        # Relink incrementally: every link Blender inserts revalidates the tree, and the
        # projection usually differs from what the tree shows by a few links at most.
        wanted = set(links)
        present = {}
        for link in list(tree.links):
            source, target = link.from_node, link.to_node
            key = (getattr(source, 'op_id', source.get('op_id', '')), link.from_socket.identifier,
                   getattr(target, 'op_id', target.get('op_id', '')), link.to_socket.identifier)
            if key in wanted and key not in present:
                present[key] = link
            else:
                tree.links.remove(link)
        for source, from_socket, target, to_socket in links:
            if (source, from_socket, target, to_socket) in present:
                continue
            producer, consumer = existing.get(source), existing.get(target)
            if producer is None or consumer is None:
                continue
            a = next((s for s in producer.outputs if s.identifier == from_socket), None)
            b = next((s for s in consumer.inputs if s.identifier == to_socket), None)
            if a is not None and b is not None:
                tree.links.new(a, b)
        feed_links = _feed_links(tree, ts)
        for source, from_socket, target, to_socket in feed_links:
            producer, consumer = existing.get(source), existing.get(target)
            if producer is None or consumer is None:
                continue
            a = next((s for s in producer.outputs if s.identifier == from_socket), None)
            b = next((s for s in consumer.inputs if s.identifier == to_socket), None)
            if a is not None and b is not None:
                tree.links.new(a, b)
        links = links + feed_links
        place(tree, ts, existing, nodes, links, only=None if force_layout else set(created))
        tree['op_signature'] = signature(ts)
        tree['op_serial'] = ts.edit_serial
        # What the tree shows now: user edits are diffed against this, never
        # against a projection the tree may not have caught up with yet.
        tree['op_links'] = ['\t'.join(link) for link in links]
        tree['op_nodes'] = [identifier for identifier, spec in nodes.items()
                            if spec['kind'] in {'MASK', 'EFFECT', 'LAYER', 'FOLDER'}]
        from .rows import remember_positions
        remember_positions(tree, ts)
        tree['op_node_count'] = len(tree.nodes)
    return tree


STRUCTURAL = frozenset({'below', 'children'})


def _feed_links(tree, ts):
    """Drawable image-feed wires, pruning dead mapping entries first.

    A feed redraws while its node, layer and channel exist and the channel
    still holds the node's image (a panel override breaks the feed and frees
    the node). A filtered channel keeps its mapping but draws no wire: the
    socket already ends the filter chain.
    """
    from . import edits
    by_id = nodes_by_id(tree)
    mapping, kept, result = edits.feeds(tree), {}, []
    for key, node_id in mapping.items():
        layer_id, _, channel = key.partition('\t')
        node = by_id.get(node_id)
        layer = next((item for item in ts.layers if item.id == layer_id), None)
        prop = channel_for(layer, channel) if layer is not None else None
        try:
            match = (prop is not None and node is not None and prop.image
                     and node.image and prop.image == node.image)
        except ReferenceError:
            match = False
        if (node is None or getattr(node, 'op_kind', '') != 'IMAGE' or prop is None
                or layer.type != 'FILL' or not match):
            continue  # node deleted, channel gone, or the panel overrode the image
        kept[key] = node_id
        if prop.enabled and not prop.effects and not prop.anchor:
            result.append((node_id, 'out', layer_id, f'content#{channel}'))
    if kept != mapping:
        edits._store_feeds(tree, kept)
    return result


def place(tree, ts, existing, nodes, links, only=None):
    """Position nodes with the pure layout; ``only`` restricts to newly created ids."""
    from ..core.layer_stack import visible_rows
    rows = [(ts.layers[index].id, depth) for index, depth in visible_rows(ts)]
    seen = {identifier for identifier, _ in rows}
    rows += [(layer.id, 0) for layer in ts.layers if layer.id not in seen]   # collapsed folders' children
    # Composition links run bottom to top between rows; ranking them would push
    # every layer one column right of the one below it.
    edges = [(source, target) for source, _, target, to_socket in links
             if target != OUTPUT_NODE and to_socket not in STRUCTURAL]
    # Mask and filter bodies draw their controls: count them as socket rows.
    body = {'MASK': 16, 'EFFECT': 8, 'LAYER': 14, 'FOLDER': 4}
    sockets = {identifier: len(spec['inputs']) + len(spec['outputs']) + body.get(spec['kind'], 0)
               for identifier, spec in nodes.items()}
    for layer in ts.layers:
        if layer.type == 'FILL':
            # Channel switch grid, value/image rows per enabled channel, and
            # one mapping block while any channel is textured.
            sockets[layer.id] = sockets.get(layer.id, 0) + 2
            textured = False
            for prop in layer.channels:
                if not prop.enabled:
                    continue
                sockets[layer.id] += 2
                if prop.image or prop.detail_image or prop.procedural:
                    textured = True
            if textured:
                # Projection row (hardness joins it) plus scale/rotation/offset.
                sockets[layer.id] += 4
        for index, mask in enumerate(layer.masks):
            if mask.source in {'FILL', 'PROCEDURAL'}:
                identifier = mask_node_id(layer, index, mask)
                sockets[identifier] = sockets.get(identifier, 0) + 4
    inline, subrows = {}, {}
    for layer in ts.layers:
        if layer.masks:
            inline[layer.id] = [mask_node_id(layer, i, m) for i, m in enumerate(layer.masks)]
        chains = [[effect_node_id(layer, prop.channel, i, e) for i, e in enumerate(prop.effects)]
                  for prop in layer.channels if prop.effects]
        if chains:
            subrows[layer.id] = chains
    coordinates = layout.positions(rows, edges, output=OUTPUT_NODE, sockets=sockets, subrows=subrows, inline=inline)
    placements = {}
    for identifier, (x, y) in coordinates.items():
        if only is not None and identifier not in only:
            continue
        node = existing.get(identifier)
        if node is None:
            continue
        owner = nodes[identifier].get('owner', '')
        if only is not None and owner and owner != identifier and owner not in only and owner in existing:
            # A new mask/filter joins a layer the user may have moved: keep the
            # layout's offset from the layer, applied to where the layer really is.
            ox, oy = coordinates.get(owner, (x, y))
            ax, ay = existing[owner].location_absolute
            x, y = ax + (x - ox), ay + (y - oy)
        placements[identifier] = (x, y)
    if only is not None and placements:
        # Inserting a new top row would otherwise land on the old top row.
        # Move only the new nodes; hand-placed existing nodes stay untouched.
        occupied = [(tuple(node.location_absolute), node.width, layout.height(sockets.get(identifier, 0)))
                    for identifier, node in existing.items() if identifier in nodes and identifier not in only]

        def overlaps():
            for identifier, (x, y) in placements.items():
                width, height = existing[identifier].width, layout.height(sockets.get(identifier, 0))
                for (ox, oy), ow, oh in occupied:
                    if x < ox + ow + 30 and x + width + 30 > ox and y > oy - oh - 30 and y - height - 30 < oy:
                        return True
            return False

        if occupied and overlaps():
            if any(nodes[identifier]['kind'] in {'LAYER', 'FOLDER'} for identifier in placements):
                shift = min(y - height for (_, y), _, height in occupied) - layout.ROW_GAP - max(
                    y for _, y in placements.values())
                placements = {identifier: (x, y + shift) for identifier, (x, y) in placements.items()}
            else:
                while overlaps():
                    placements = {identifier: (x - layout.COLUMN_WIDTH, y) for identifier, (x, y) in placements.items()}
    for identifier, position in placements.items():
        existing[identifier].location_absolute = position


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
            from . import edits as graph_edits
            from . import rows
            if graph_edits.adopt_duplicates(tree, ts):
                area.tag_redraw()
            if rows.order_from_y(tree, ts):
                area.tag_redraw()
            if tree.get('op_signature') != signature(ts) or tree.get('op_links_dirty'):
                tree['op_links_dirty'] = False
                reconcile(tree, ts)
                area.tag_redraw()
            if mirror_selection(tree, ts, window, area):
                area.tag_redraw()
            if consume_pending_select(tree, ts):
                area.tag_redraw()
        except Exception as error:   # a stale tree must never break the UI timer
            from ..utils.logging import logger
            logger.error('Open Graph sync: %s', error)


def request_select(tree, node_id):
    """Select ``node_id`` exclusively on the next sync, for placement.

    Add operators record the new node here: reconcile creates it on the tick
    after the operator runs, so selecting immediately would target a node that
    does not exist yet (or lose to the selection mirror).
    """
    tree['op_select_pending'] = node_id


def consume_pending_select(tree, ts):
    """Exclusively select the node an add operator recorded; True if one was."""
    identifier = tree.get('op_select_pending', '')
    if not identifier:
        return False
    tree['op_select_pending'] = ''
    node = nodes_by_id(tree).get(identifier)
    if node is None:
        return False
    with syncing():
        for item in tree.nodes:
            item.select = item is node
        tree.nodes.active = node
    layer, _mask, _prop, _effect = owner_of(ts, identifier)
    tree['op_active_node'] = identifier
    if layer is not None:
        tree['op_active'] = layer.id
    return True


def mirror_selection(tree, ts, window, area):
    """Two-way active selection between the graph and the layer list, without ping-pong."""
    from ..core.layer_stack import active
    recorded_node, recorded_layer = tree.get('op_active_node', ''), tree.get('op_active', '')
    current = active(ts)
    current_id = current.id if current is not None else ''
    node = tree.nodes.active
    node_id = getattr(node, 'op_id', '') if node is not None and node.select else ''
    if node_id and node_id != recorded_node and node_id != OUTPUT_NODE:
        # The user clicked a node: follow it in the layer list (and mask/filter selection).
        layer, mask, prop, effect = owner_of(ts, node_id)
        if layer is not None:
            # The shared target rules (ui/selection.py): by id, one session, no evaluation.
            from ..ui import selection
            with bpy.context.temp_override(window=window, screen=window.screen, area=area):
                selection.select(bpy.context, layer.id,
                                 selection.MASK if mask is not None else
                                 selection.FILTER if effect is not None else selection.CONTENT,
                                 mask_id=mask.id if mask is not None else None,
                                 channel=prop.channel if prop is not None else None,
                                 effect_id=effect.id if effect is not None else None, ts=ts)
            tree['op_active_node'], tree['op_active'] = node_id, layer.id
            return True
    if current_id and current_id != recorded_layer:
        # The layer list moved: select the matching layer node.
        with syncing():
            for item in tree.nodes:
                item.select = getattr(item, 'op_id', '') == current_id
            target = next((item for item in tree.nodes if getattr(item, 'op_id', '') == current_id), None)
            if target is not None:
                tree.nodes.active = target
        tree['op_active_node'], tree['op_active'] = current_id, current_id
        return True
    return False


# ---- drawing --------------------------------------------------------------------

MASK_CONTROLS = {
    'GENERATOR': ('generator', 'amount', 'contrast', 'noise_scale', 'edge_source'),
    'NOISE': ('noise_scale', 'noise_strength', 'seed'),
    'PROCEDURAL': ('noise_scale', 'seed'),
    'BAKED': ('map_type',),
    'STROKES': ('stroke_key',),
}
EFFECT_CONTROLS = {
    'LEVELS': ('black', 'white', 'gamma', 'output_black', 'output_white'),
    'BRIGHTNESS_CONTRAST': ('amount', 'contrast'), 'GAMMA': ('gamma',), 'THRESHOLD': ('amount', 'softness'),
    'HISTOGRAM_SCAN': ('amount', 'softness'), 'HISTOGRAM_RANGE': ('amount', 'softness'), 'CLAMP': ('black', 'white'),
    'POSTERIZE': ('steps',), 'QUANTIZE': ('steps',), 'HSL': ('hue', 'saturation', 'lightness'),
    'HSV': ('hue', 'saturation', 'lightness'), 'HUE_SHIFT': ('hue',), 'SATURATION': ('saturation',),
    'SHARPEN': ('radius', 'amount'), 'HIGH_PASS': ('radius',), 'SMOOTH_MASK': ('radius', 'amount'),
    'DIRECTIONAL_BLUR': ('radius', 'angle'), 'DIRECTIONAL_WARP': ('amount', 'angle'), 'WAVE': ('amount', 'scale', 'angle'),
    'RIPPLE': ('amount', 'scale'), 'TWIRL': ('amount', 'radius'), 'WARP': ('amount',), 'VECTOR_WARP': ('amount',),
    'UV_DISTORTION': ('amount',), 'TINT': ('color', 'amount'), 'COLORIZE': ('color',), 'GRADIENT_MAP': ('color', 'color2'),
}
RADIUS_KINDS = frozenset({'BLUR', 'GAUSSIAN_BLUR', 'RADIAL_BLUR', 'SLOPE_BLUR', 'DILATE', 'ERODE',
                          'MORPHOLOGICAL_EXPAND', 'MORPHOLOGICAL_CONTRACT', 'EDGE_DETECT'})


def _layer_of(node, context):
    tree = node.id_data
    ts = texture_set_of(tree, context.scene if context else None)
    if ts is None:
        return None, None
    return ts, next((layer for layer in ts.layers if layer.id == node.op_id), None)


def node_label(node):
    return node.label or node.bl_label


def _edit_button(layout, layer, mask=None, prop=None, effect=None):
    op = layout.operator('open_painter.graph_select', text='Edit in Panel', icon='PROPERTIES')
    op.layer_id = layer.id
    op.mask_index = next((i for i, item in enumerate(layer.masks) if item == mask), -1) if mask is not None else -1
    op.channel = prop.channel if prop is not None else ''
    op.effect_index = next((i for i, item in enumerate(prop.effects) if item == effect), -1) if effect is not None else -1


def draw_mask(layout, ts, layer, mask):
    row = layout.row(align=True)
    row.prop(mask, 'enabled', text='', icon='HIDE_OFF' if mask.enabled else 'HIDE_ON')
    row.prop(mask, 'name', text='')
    if mask.source not in {'ANCHOR', 'REFERENCE'}:
        row = layout.row(align=True)
        row.prop(mask, 'operation', text='')
        row.prop(mask, 'invert', text='', icon='ARROW_LEFTRIGHT')
        layout.prop(mask, 'strength', slider=True)
    if mask.source == 'PROCEDURAL':
        layout.label(text=mask.procedural.replace('_', ' ').title() or 'Procedural', icon='TEXTURE')
    elif mask.source == 'SCENE':
        layout.prop(mask.influence, 'kind', text='')
        layout.prop(mask.influence, 'mode', text='')
    elif mask.source == 'MESH':
        selector = mask.mesh
        layout.prop(selector, 'kind', text='')
        field = {'OBJECT': 'object', 'UV_ISLAND': 'object', 'COLLECTION': 'collection', 'MATERIAL': 'material',
                 'VERTEX_GROUP': 'attribute', 'ATTRIBUTE': 'attribute', 'FACE_SET': 'face_set',
                 'NORMAL_DIRECTION': 'direction'}.get(selector.kind)
        if field:
            layout.prop(selector, field, text='')
        for name in {'NORMAL_DIRECTION': ('angle', 'softness'), 'UV_ISLAND': ('island',),
                     'COLLECTION': ('include_children',)}.get(selector.kind, ()):
            layout.prop(selector, name)
        if selector.kind == 'REGION':
            layout.label(text='Edit saved faces in the Inspector', icon='INFO')
    elif mask.source == 'GROUP':
        layout.label(text='Combine the Members input', icon='NODETREE')
    elif mask.source == 'GRAPH':
        from ..procedural_graph import is_native
        layout.template_ID(mask, 'node_group')
        if is_native(mask.node_group):
            layout.operator('open_painter.mask_graph_edit', icon='NODETREE').tree = mask.node_group.name
        elif mask.node_group is not None:
            op = layout.operator('open_painter.graph_edit_shader', text='Edit Shader Group', icon='NODETREE')
            op.mask_id = mask.id
            layout.label(text='Earlier-version shader group · CPU', icon='INFO')
    elif mask.source == 'FILTER':
        layout.label(text=mask.effect.kind.replace('_', ' ').title(), icon='SHADERFX')
        for name in EFFECT_CONTROLS.get(mask.effect.kind, ('radius',) if mask.effect.kind in RADIUS_KINDS else ('amount',)):
            layout.prop(mask.effect, name)
    elif mask.source in {'FILL', 'PAINT'}:
        layout.template_ID(mask, 'image', open='image.open')
    elif mask.source == 'REFERENCE':
        layout.label(text='Reads the linked anchor', icon='LINKED')
    elif mask.source == 'ANCHOR':
        layout.label(text='Publishes the coverage so far', icon='ANCHOR_CENTER')
    for name in MASK_CONTROLS.get(mask.source, ()):
        if name in {'generator', 'map_type', 'edge_source'}:
            layout.prop(mask, name, text='')
        else:
            layout.prop(mask, name)
    if mask.source in {'FILL', 'PROCEDURAL'}:
        # Coordinate selection lives on the node: UV or Triplanar plus the
        # transform the compiled shader reads. Hardness joins the projection
        # row: it is the weight the manual triplanar blend uses, so no
        # Box-projection Blend control applies (see shader_graph.projected).
        row = layout.row(align=True)
        row.prop(mask, 'projection', text='')
        if mask.projection == 'TRIPLANAR':
            row.prop(mask, 'triplanar_hardness', text='')
        layout.prop(mask, 'scale', text='Scale')
        layout.prop(mask, 'rotation', text='Rotation')
        layout.prop(mask, 'offset', text='Offset')
    if mask.source not in {'ANCHOR', 'REFERENCE'}:
        row = layout.row(align=True)
        row.prop(mask, 'black', text='Black')
        row.prop(mask, 'white', text='White')
    _edit_button(layout, layer, mask=mask)


def draw_effect(layout, ts, layer, prop, effect):
    row = layout.row(align=True)
    row.prop(effect, 'enabled', text='', icon='HIDE_OFF' if effect.enabled else 'HIDE_ON')
    row.label(text=effect.kind.replace('_', ' ').title(), icon='SHADERFX')
    if effect.kind == 'ANCHOR':
        layout.prop(effect, 'name', text='')
        layout.label(text='Publishes the content so far', icon='ANCHOR_CENTER')
    else:
        controls = EFFECT_CONTROLS.get(effect.kind, ('radius',) if effect.kind in RADIUS_KINDS else ('amount',))
        for name in controls:
            layout.prop(effect, name)
        layout.prop(effect, 'strength', slider=True)
    _edit_button(layout, layer, prop=prop, effect=effect)


# Short switch labels for the Fill node's channel grid (one toggle per channel).
CHANNEL_SHORT = {'BASE_COLOR': 'Base', 'ROUGHNESS': 'Rough', 'METALLIC': 'Metal', 'NORMAL': 'Normal',
                 'HEIGHT': 'Height', 'AO': 'AO', 'EMISSION': 'Emis.', 'OPACITY': 'Opac.'}


def draw_image_node(layout, node):
    """An Image Texture node: the image plus the mapping a fed channel copies."""
    layout.template_ID(node, 'image', new='image.new', open='image.open')
    row = layout.row(align=True)
    row.prop(node, 'projection', text='')
    if node.projection == 'TRIPLANAR':
        row.prop(node, 'triplanar_hardness', text='')
    layout.prop(node, 'scale', text='Scale')
    layout.prop(node, 'rotation', text='Rotation')
    layout.prop(node, 'offset', text='Offset')


def draw_node(node, context, layout):
    if getattr(node, 'op_kind', '') == 'IMAGE':
        draw_image_node(layout, node)
        return
    tree = node.id_data
    ts = texture_set_of(tree, context.scene if context else None)
    if node.op_kind == 'OUTPUT':
        layout.label(text=ts.name if ts else 'Texture set', icon='TEXTURE')
        return
    layer, mask, prop, effect = owner_of(ts, node.op_id) if ts is not None else (None, None, None, None)
    if layer is None:
        layout.label(text='Removed', icon='ERROR')
        return
    if mask is not None:
        draw_mask(layout, ts, layer, mask)
        return
    if effect is not None:
        draw_effect(layout, ts, layer, prop, effect)
        return
    row = layout.row(align=True)
    row.prop(layer, 'visible', text='', icon='HIDE_OFF' if layer.visible else 'HIDE_ON')
    row.prop(layer, 'opacity', text='Opacity', slider=True)
    if layer.type == 'FOLDER':
        layout.prop(layer, 'group_mode', text='')
    else:
        layout.prop(layer, 'blend_mode', text='')
    if layer.type == 'FILL':
        # Channel switches, no panel needed: two compact rows toggle every
        # channel on or off; the value, image and mapping rows follow only the
        # enabled ones. Toggling is structural (sockets come and go).
        from ..constants import CHANNELS
        # All three control groups read the same small collection. Index it
        # once for this draw, preserving channel_for's first-match semantics
        # without retaining RNA references across collection edits or undo.
        channels = {}
        for prop in layer.channels:
            channels.setdefault(prop.channel, prop)
        for block in (CHANNELS[:4], CHANNELS[4:]):
            row = layout.row(align=True)
            for channel_id in block:
                prop = channels.get(channel_id)
                if prop is None:
                    continue
                row.prop(prop, 'enabled', text=CHANNEL_SHORT[channel_id], toggle=True)
        for channel_id in CHANNELS:
            prop = channels.get(channel_id)
            if prop is None or not prop.enabled:
                continue
            label = CHANNEL_LABELS.get(channel_id, channel_id)
            if channel_id in {'BASE_COLOR', 'NORMAL', 'EMISSION'}:
                layout.prop(prop, 'color', text=label)
            else:
                layout.prop(prop, 'value', text=label, slider=True)
            # Any image attaches to any channel here, exactly like the panel's
            # Texture picker: the shader reads channel.image, so no link is needed.
            layout.template_ID(prop, 'image', open='image.open')
        textured = [channels.get(channel_id) for channel_id in CHANNELS]
        textured = [prop for prop in textured
                    if prop is not None and prop.enabled
                    and (prop.image or prop.detail_image or prop.procedural)]
        if textured:
            # Explicit shared controls; ordinary channel edits preserve independent mappings.
            first = textured[0]
            row = layout.row(align=True)
            row.prop(first, 'fill_projection', text='')
            if first.projection == 'TRIPLANAR':
                row.prop(first, 'fill_triplanar_hardness', text='')
            layout.prop(first, 'fill_scale', text='Scale')
            layout.prop(first, 'fill_rotation', text='Rotation')
            layout.prop(first, 'fill_offset', text='Offset')
    row = layout.row(align=True)
    row.prop(layer, 'publish_output', text='Publish', toggle=True, icon='ANCHOR_CENTER')
    if layer.type == 'FOLDER':
        row.prop(layer, 'publish_group', text='Composite', toggle=True)
    from ..painting import decals
    decal = decals.resolve(layer) if layer.type == 'FILL' else None
    if decal is not None:
        # Decal objects (0.9.6): the Empty's transform is the decal's transform, edited here too.
        box = layout.box()
        box.label(text=decal.name, icon='OUTLINER_OB_IMAGE')
        box.prop(decal, 'location', text='Location')
        box.prop(decal, 'rotation_euler', text='Rotation')
        box.prop(decal, 'scale', text='Scale')
        box.prop(layer, 'decal_depth', text='Depth')
    if layer.type == 'FILL':
        channel = context.scene.open_painter.properties_channel
        prop = channels.get(channel)
        if prop is not None:
            box = layout.box()
            box.label(text=CHANNEL_LABELS.get(channel, channel))
            controls = box.column()
            controls.enabled = prop.enabled
            if prop.anchor:
                controls.label(text='Content from linked anchor', icon='LINKED')
            else:
                if prop.procedural:
                    controls.label(text=prop.procedural.replace('_', ' ').title(), icon='TEXTURE')
                    controls.prop(prop, 'procedural_scale')
                    controls.prop(prop, 'procedural_seed')
                    controls.prop(prop, 'procedural_height' if channel == 'HEIGHT' else 'procedural_detail')
    _edit_button(layout, layer)
