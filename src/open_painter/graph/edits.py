"""Translate edits in the graph into edits of the stack.

Links carry two kinds of meaning. A link into a consumer socket (an anchor
reference: ``ref`` / ``detail#<slot>`` on a mask node, ``in`` on a channel's
first filter, ``content#<channel>`` on an unfiltered layer channel) sets that
anchor field; removing it clears the field. A link into a structural socket
(``prev`` of a mask, ``in`` of a later filter, a layer's ``mask`` /
``content#<channel>`` chain end, ``below`` / ``children``) is an order edit:
"place this mask before that one", "make this layer sit under that one".
Deleting a mask or filter node deletes it from the stack (painted masks are
kept). Every edit is validated with ``references.issues`` and reverted on
failure, so the stack never holds a cycle. Tree mutation itself is left to the
mirror's next sync: rejected links disappear when the tree is reconciled.
"""
from ..core.layer_stack import channel_for, invalidate
from ..evaluation import anchors
from ..evaluation.references import issues
from .tree import OUTPUT_NODE, is_graph

STRUCTURAL = frozenset({'prev', 'mask', 'below', 'children'})


class LinkError(ValueError):
    pass


def _layer(ts, identifier):
    return next((layer for layer in ts.layers if layer.id == identifier), None)


def _owner(ts, node_id):
    from .mirror import owner_of
    layer, mask, prop, effect = owner_of(ts, node_id)
    if layer is None:
        raise LinkError('That node no longer exists.')
    return layer, mask, prop, effect


def _index(collection, item):
    return next(i for i, candidate in enumerate(collection) if candidate == item)


def is_structural(ts, node_id, socket):
    """True when a link into ``socket`` of ``node_id`` orders the stack rather than sets an anchor."""
    if node_id == OUTPUT_NODE or socket in STRUCTURAL:
        return True
    layer, mask, prop, effect = _owner(ts, node_id)
    if effect is not None:
        return socket == 'in' and _index(prop.effects, effect) > 0
    if mask is None and effect is None and socket.startswith('content#'):
        channel = channel_for(layer, socket.partition('#')[2])
        return channel is not None and len(channel.effects) > 0
    return False


def is_chain_link(ts, from_node, from_socket, to_node, to_socket):
    """True when a new link orders a chain rather than sets an anchor: its target
    is a structural socket, or its source is a mask/filter that publishes nothing.
    Anchor producers always go through ``connect``, which explains filtered channels."""
    if to_node == OUTPUT_NODE or to_socket in STRUCTURAL:
        return True
    if from_socket != 'out':
        return False
    _, mask, _, effect = _owner(ts, from_node)
    return (mask is not None and mask.source != 'ANCHOR') or (effect is not None and effect.kind != 'ANCHOR')


def _consumer(ts, node_id, socket):
    """(owner, field, kind) for a consumer socket identifier."""
    layer, mask, prop, effect = _owner(ts, node_id)
    if mask is not None:
        if socket == 'ref':
            if mask.source != 'REFERENCE':
                raise LinkError('Select a Published Input mask as the target.')
            return mask, 'reference_anchor', 'REFERENCE_MASK'
        if socket.startswith('detail#'):
            if mask.source != 'GENERATOR':
                raise LinkError('Micro details belong to generator masks.')
            slot = socket.partition('#')[2]
            return mask, anchors.CONSUMER_FIELDS['DETAIL_' + slot], 'DETAIL_' + slot
        raise LinkError('That input cannot take a link.')
    if effect is not None:
        if socket == 'in' and _index(prop.effects, effect) == 0 and layer.type == 'FILL':
            return prop, 'anchor', 'CHANNEL'
        raise LinkError('Anchored content enters a channel at its first filter.')
    if socket.startswith('content#'):
        prop = channel_for(layer, socket.partition('#')[2])
        if prop is None or layer.type != 'FILL':
            raise LinkError('Anchored content belongs on fill layer channels.')
        if prop.effects:
            raise LinkError('This channel is filtered: link the anchor into its first filter.')
        return prop, 'anchor', 'CHANNEL'
    raise LinkError('That input cannot take a link.')


def _producer(ts, node_id, socket):
    """Anchor id for a producer socket, publishing the layer when the link asks for it."""
    layer, mask, prop, effect = _owner(ts, node_id)
    if mask is not None:
        if mask.source == 'ANCHOR' and mask.id and socket == 'out':
            return mask.id, None
        raise LinkError('Add an Anchor Point after this mask to publish its coverage.')
    if effect is not None:
        if effect.kind == 'ANCHOR' and effect.id and socket == 'out':
            return effect.id, None
        raise LinkError('Add an Anchor Point after this filter to publish its content.')
    if socket == 'out':
        return anchors.output_id(layer.id), (layer, 'publish_output')
    if socket == 'group':
        if layer.type != 'FOLDER':
            raise LinkError('Only folders publish a composite.')
        return anchors.group_id(layer.id), (layer, 'publish_group')
    if socket.startswith('legacy#'):
        raise LinkError('Whole-layer references are read-only here. Add an anchor point instead.')
    raise LinkError('That output is not an anchor point.')


def connect(ts, from_node, from_socket, to_node, to_socket):
    """Set the consumer field named by ``to_socket`` to the anchor behind ``from_socket``."""
    from ..ui.anchors import _eligible
    if to_node == OUTPUT_NODE:
        raise LinkError('The output node shows the stack result; connect anchors to layer inputs.')
    identifier, publish = _producer(ts, from_node, from_socket)
    owner, field, kind = _consumer(ts, to_node, to_socket)
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


def reorder(ts, from_node, from_socket, to_node, to_socket):
    """Apply a structural link: place a mask/filter before another, or a layer under another."""
    from ..core import layer_stack
    if from_socket != 'out':
        raise LinkError('Chain links start at an Output or Coverage socket.')
    source, source_mask, source_prop, source_effect = _owner(ts, from_node)
    if to_node == OUTPUT_NODE:
        raise LinkError('The output node shows the top layer; move a layer above the others instead.')
    target, target_mask, target_prop, target_effect = _owner(ts, to_node)
    if source_mask is not None or target_mask is not None:
        if source_mask is None or source.id != target.id or (target_mask is None and to_socket != 'mask'):
            raise LinkError('Masks chain within one layer; share coverage with an Anchor Point and a Published Input.')
        _move(source.masks, _index(source.masks, source_mask),
              _index(target.masks, target_mask) if target_mask is not None else len(source.masks))
    elif source_effect is not None or target_effect is not None:
        if source_effect is None or source.id != target.id:
            raise LinkError('Filters chain within one channel; share content with an Anchor Point.')
        if target_effect is not None and target_prop.channel == source_prop.channel:
            before = _index(target_prop.effects, target_effect)
        elif target_effect is None and to_socket == f'content#{source_prop.channel}':
            before = len(source_prop.effects)
        else:
            raise LinkError('Filters chain within one channel; share content with an Anchor Point.')
        _move(source_prop.effects, _index(source_prop.effects, source_effect), before)
    elif to_socket == 'below':
        if source.id == target.id:
            raise LinkError('A layer cannot sit under itself.')
        layer_stack.place(ts, source.id, (0, target.parent_id, 'BELOW', target.id))   # depth is informational
        return
    elif to_socket == 'children':
        if target.type != 'FOLDER':
            raise LinkError('Only folders take children.')
        layer_stack.place(ts, source.id, (0, target.id, 'FIRST_CHILD', target.id))
        return
    else:
        raise LinkError('That input cannot take a link.')
    invalidate(ts)


def _move(collection, index, before):
    """Move item ``index`` so it sits right before position ``before`` (``len`` = the end)."""
    target = before - 1 if index < before else before
    if target != index:
        collection.move(index, target)


def delete(ts, node_id):
    """Remove the mask or filter behind a deleted node; painted coverage stays."""
    layer, mask, prop, effect = _owner(ts, node_id)
    if mask is not None:
        if mask.source in {'PAINT', 'STROKES'}:
            raise LinkError('Painted masks are deleted in the Painter panel, where their pixels are shown.')
        layer.masks.remove(_index(layer.masks, mask))
        layer.active_mask = max(0, min(layer.active_mask, len(layer.masks) - 1))
    elif effect is not None:
        prop.effects.remove(_index(prop.effects, effect))
        prop.active_effect = max(0, min(prop.active_effect, len(prop.effects) - 1))
    else:
        return False   # layers are deleted in the panel; the mirror recreates the node
    invalidate(ts)
    return True


def disconnect(ts, to_node, to_socket):
    """Clear the consumer field behind a removed link (legacy references included)."""
    if to_node == OUTPUT_NODE:
        return False
    try:
        if is_structural(ts, to_node, to_socket):
            return False   # chains have no gaps; the sync redraws the link
        owner, field, _ = _consumer(ts, to_node, to_socket)
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
    """NodeTree.update: diff nodes and links against the projection and apply the user's edit."""
    from . import mirror
    if mirror.is_syncing() or not is_graph(tree):
        return
    import bpy
    ts = mirror.texture_set_of(tree, getattr(bpy.context, 'scene', None))
    if ts is None:
        return
    nodes, _, projected = mirror.project(ts)
    expected = {tuple(link) for link in projected}
    actual = link_keys(tree)
    status, touched = None, set()
    # A missing mask/filter node is a deletion, but only while the tree was in
    # sync: a stale tree simply has not drawn a mask added in the panel yet.
    if tree.get('op_signature') == mirror.signature(ts):
        present = set(mirror.nodes_by_id(tree))
        for identifier, spec in nodes.items():
            if spec['kind'] in {'MASK', 'EFFECT'} and identifier not in present:
                try:
                    delete(ts, identifier)
                except LinkError as error:
                    status = str(error)
                tree['op_links_dirty'] = True
        nodes, _, projected = mirror.project(ts)
        expected = {tuple(link) for link in projected}
    for source, from_socket, target, to_socket in sorted(actual - expected):
        touched.add((target, to_socket))
        try:
            if is_chain_link(ts, source, from_socket, target, to_socket):
                reorder(ts, source, from_socket, target, to_socket)
            else:
                connect(ts, source, from_socket, target, to_socket)
        except LinkError as error:
            status = str(error)
        except ValueError as error:   # layer_stack refusals (folder cycles)
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
