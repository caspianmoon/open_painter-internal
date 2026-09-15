"""Translate link edits in the graph into anchor references on the stack.

A link from a producer socket (an anchor id, a layer's ``out`` or a folder's
``group``) into a consumer socket (``content#<channel>``, ``mask#<id>`` or
``detail#<id>#<slot>``) sets the consumer's anchor field; removing it clears
the field. Every edit is validated with ``references.issues`` and reverted on
failure, so the stack never holds a cycle. Tree mutation itself is left to the
mirror's next sync: rejected links disappear when the tree is reconciled.
"""
from ..core.layer_stack import channel_for
from ..evaluation import anchors
from ..evaluation.references import issues
from .tree import OUTPUT_NODE, is_graph


class LinkError(ValueError):
    pass


def _layer(ts, identifier):
    return next((layer for layer in ts.layers if layer.id == identifier), None)


def _consumer(ts, layer_id, socket):
    """(owner, field, kind) for a consumer socket identifier."""
    layer = _layer(ts, layer_id)
    if layer is None:
        raise LinkError('That layer no longer exists.')
    kind, _, rest = socket.partition('#')
    if kind == 'content':
        prop = channel_for(layer, rest)
        if prop is None or layer.type != 'FILL':
            raise LinkError('Anchored content belongs on fill layer channels.')
        return prop, 'anchor', 'CHANNEL'
    if kind == 'mask':
        mask = next((m for m in layer.masks if m.id == rest), None)
        if mask is None or mask.source != 'REFERENCE':
            raise LinkError('Select a Published Input mask as the target.')
        return mask, 'reference_anchor', 'REFERENCE_MASK'
    if kind == 'detail':
        mask_id, _, slot = rest.partition('#')
        mask = next((m for m in layer.masks if m.id == mask_id), None)
        if mask is None or mask.source != 'GENERATOR':
            raise LinkError('Micro details belong to generator masks.')
        return mask, anchors.CONSUMER_FIELDS['DETAIL_' + slot], 'DETAIL_' + slot
    raise LinkError('That input cannot take a link.')


def _producer(ts, layer_id, socket):
    """Anchor id for a producer socket, publishing the layer when the link asks for it."""
    layer = _layer(ts, layer_id)
    if layer is None:
        raise LinkError('That layer no longer exists.')
    if socket == 'out':
        return anchors.output_id(layer.id), (layer, 'publish_output')
    if socket == 'group':
        if layer.type != 'FOLDER':
            raise LinkError('Only folders publish a composite.')
        return anchors.group_id(layer.id), (layer, 'publish_group')
    if socket.startswith('legacy#'):
        raise LinkError('Whole-layer references are read-only here. Add an anchor point instead.')
    if socket not in anchors.index(ts):
        raise LinkError('That output is not an anchor point.')
    return socket, None


def connect(ts, from_layer, from_socket, to_layer, to_socket):
    """Set the consumer field named by ``to_socket`` to the anchor behind ``from_socket``."""
    from ..ui.anchors import _eligible
    if to_layer == OUTPUT_NODE:
        raise LinkError('The output node shows the stack result; connect anchors to layer inputs.')
    identifier, publish = _producer(ts, from_layer, from_socket)
    owner, field, kind = _consumer(ts, to_layer, to_socket)
    changes = []
    if publish is not None and not getattr(*publish):
        setattr(*publish, True)
        changes.append((publish[0], publish[1], False))
    anchor = anchors.index(ts)[identifier]
    if not _eligible(anchor, kind):
        _revert(changes)
        raise LinkError(f'{anchors.label(ts, anchor)} cannot feed {anchors.CONSUMER_LABELS[kind]}.')
    previous = getattr(owner, field)
    setattr(owner, field, identifier)
    changes.append((owner, field, previous))
    errors = issues(ts)
    if errors:
        _revert(changes)
        raise LinkError(' | '.join(errors))
    return identifier


def disconnect(ts, to_layer, to_socket):
    """Clear the consumer field behind a removed link (legacy references included)."""
    if to_layer == OUTPUT_NODE:
        return False
    try:
        owner, field, _ = _consumer(ts, to_layer, to_socket)
    except LinkError:
        return False
    changed = False
    if getattr(owner, field):
        setattr(owner, field, '')
        changed = True
    if field == 'reference_anchor' and getattr(owner, 'reference_layer', ''):
        owner.reference_layer = ''
        changed = True
    return changed


def _revert(changes):
    for owner, field, value in reversed(changes):
        setattr(owner, field, value)


def link_keys(tree):
    """{(from id, from socket, to id, to socket)} of the tree's current links."""
    keys = set()
    for link in tree.links:
        source, target = link.from_node, link.to_node
        if not hasattr(source, 'op_id') or not hasattr(target, 'op_id'):
            continue
        keys.add((source.op_id, link.from_socket.identifier, target.op_id, link.to_socket.identifier))
    return keys


def on_tree_update(tree):
    """NodeTree.update: diff links against the projection and apply the user's edit."""
    from . import mirror
    if mirror.is_syncing() or not is_graph(tree):
        return
    import bpy
    ts = mirror.texture_set_of(tree, getattr(bpy.context, 'scene', None))
    if ts is None:
        return
    expected = {key for key in (tuple(link) for link in mirror.project(ts)[2])}
    actual = link_keys(tree)
    status, touched = None, set()
    for source, from_socket, target, to_socket in sorted(actual - expected):
        touched.add((target, to_socket))
        try:
            connect(ts, source, from_socket, target, to_socket)
        except LinkError as error:
            status = str(error)
        tree['op_links_dirty'] = True
    for source, from_socket, target, to_socket in sorted(expected - actual):
        tree['op_links_dirty'] = True
        # An input that just received a link was either re-pointed (the field
        # already changed) or kept its reference after a refusal; the sync
        # redraws the right link either way.
        if target == OUTPUT_NODE or (target, to_socket) in touched:
            continue
        disconnect(ts, target, to_socket)
    if status:
        scene = getattr(bpy.context, 'scene', None)
        if scene is not None:
            scene.open_painter.status = status
