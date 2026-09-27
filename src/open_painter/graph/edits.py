"""Translate edits in the graph into edits of the stack.

Links carry two kinds of meaning. A link into a consumer socket (an anchor
reference: ``ref`` / ``detail#<slot>`` on a mask node, ``in`` on a channel's
first filter, ``content#<channel>`` on an unfiltered layer channel) sets that
anchor field; removing it clears the field. A link into a structural socket
(``prev`` of a mask, ``in`` of a later filter, a layer's ``mask`` /
``content#<channel>`` chain end, ``below`` / ``children``) is an order edit:
"place this mask before that one", "make this layer sit under that one".
Wiring is optional: only enabled entries draw chain wires, drawing one
re-enables its endpoints, and cutting one parks (disables) its source instead
of forcing the chain redrawn, so floating nodes affect nothing. Dropping a
mask onto another layer's Mask input shares it there (an Anchor Point plus a
Published Input per target).
Deleting a mask or filter node deletes it from the stack (painted masks are
kept); deleting a layer or folder node deletes it with its subtree, like the
panel Delete.
Duplicated nodes (Shift+D) become duplicated stack items with
fresh ids, keeping the position they were dropped at. Every edit is validated
with ``references.issues`` and reverted on
failure, so the stack never holds a cycle. Tree mutation itself is left to the
mirror's next sync: rejected links disappear when the tree is reconciled.
"""
from ..core.layer_stack import channel_for, invalidate
from ..evaluation import anchors
from ..evaluation.references import issues
from .tree import IMAGE_PREFIX, OUTPUT_NODE, is_graph

STRUCTURAL = frozenset({'prev', 'mask', 'members', 'below', 'children'})


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
    if type(owner).__name__ == 'OP_Mask' and not owner.enabled:
        owner.enabled = True   # wiring a parked mask puts it back to work
        changes.append((owner, 'enabled', False))
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
        raise LinkError('The output shows the whole stack; it cannot take links.')
    target, target_mask, target_prop, target_effect = _owner(ts, to_node)
    if source_mask is not None or target_mask is not None:
        from ..core import mask_stack
        if source_mask is None or source.id != target.id or (target_mask is None and to_socket != 'mask'):
            raise LinkError('Masks chain within one layer; share coverage with an Anchor Point and a Published Input.')
        masks = source.masks
        start, stop = mask_stack.block(masks, _index(masks, source_mask))
        if target_mask is not None:
            target_index = _index(masks, target_mask)
            if start <= target_index < stop:
                raise LinkError('A mask group cannot contain itself or its descendants.')
            if to_socket == 'members' and target_mask.source == 'GROUP':
                parent, before = target_mask.id, mask_stack.block(masks, target_index)[1]
            elif to_socket == 'prev':
                parent, before = target_mask.parent_id, target_index
            else:
                raise LinkError('Connect coverage to Previous or a mask group’s Members input.')
        else:
            parent, before = '', len(masks)
        identifier = source_mask.id
        source_mask.parent_id = parent
        destination = before - (stop - start) if start < before else before
        mask_stack._move_range(masks, start, stop, destination)
        source.active_mask = next(i for i, item in enumerate(masks) if item.id == identifier)
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
        if not source.parent_id and not target.parent_id:
            raise LinkError('Root layers stack by row: drag the node above or below instead.')
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
        from ..core import mask_stack
        index = _index(layer.masks, mask)
        if mask.source == 'GROUP':
            # Removing a group must not silently destroy its painted members.
            mask_stack.dissolve(layer, index)
        else:
            layer.masks.remove(index)
        layer.active_mask = max(0, min(layer.active_mask, len(layer.masks) - 1))
    elif effect is not None:
        prop.effects.remove(_index(prop.effects, effect))
        prop.active_effect = max(0, min(prop.active_effect, len(prop.effects) - 1))
    else:
        return False   # layers are deleted in the panel; the mirror recreates the node
    invalidate(ts)
    return True


def delete_layer(ts, node_id):
    """Remove the layer or folder (with its subtree) behind a deleted node."""
    from ..core import layer_stack
    layer = next((item for item in ts.layers if item.id == node_id), None)
    if layer is None:
        return False
    layer_stack.remove_tree(ts, node_id)
    return True


def _duplicate_mask(ts, layer, mask, copied=None):
    """Copy a mask subtree, parking its root and preserving independent data."""
    from ..core import layer_stack, mask_stack
    from ..core.image_manager import clone
    from ..utils.ids import new_id
    start, stop = mask_stack.block(layer.masks, _index(layer.masks, mask))
    sources = [layer_stack.snapshot(layer.masks[i]) for i in range(start, stop)]
    ids = {source['id']: new_id() for source in sources}
    with layer_stack.replay(ts):
        for source in sources:
            copy = layer.masks.add()
            layer_stack.restore(source, copy)
            copy.id = ids[source['id']]
            copy.name += ' Copy'
            copy.parent_id = ids.get(copy.parent_id, copy.parent_id)
            for field in ('reference_mask', 'reference_anchor', 'detail_height_anchor', 'detail_normal_anchor'):
                setattr(copy, field, ids.get(getattr(copy, field), getattr(copy, field)))
            if source['id'] == sources[0]['id']:
                copy.enabled = False
            if copy.source == 'PAINT' and copy.image:
                copy.image = clone(copy.image)
            if copy.source == 'STROKES' and copy.stroke_key:
                from ..painting.strokes import copy_key
                copy.stroke_key = copy_key(ts.id_data.open_painter, copy.stroke_key, ts)
        for offset, identifier in enumerate(ids.values()):
            index = next(i for i, item in enumerate(layer.masks) if item.id == identifier)
            layer.masks.move(index, stop + offset)
        layer.active_mask = stop
    if copied is not None:
        copied.update(ids)
    return ids[sources[0]['id']]


def _duplicate_effect(ts, layer, prop, effect):
    """Copy one channel filter after the original, parked; return its graph node id."""
    from ..core import layer_stack
    from ..utils.ids import new_id
    index = next(i for i, item in enumerate(prop.effects) if item.id == effect.id)
    data = layer_stack.snapshot(effect)
    copy = prop.effects.add()
    layer_stack.restore(data, copy)
    copy.id = identifier = new_id()
    copy.name = (data['name'] or 'Filter') + ' Copy'
    copy.enabled = False
    prop.effects.move(len(prop.effects) - 1, index + 1)
    prop.active_effect = index + 1
    layer_stack.invalidate(ts)
    return identifier


def _duplicate_layer(ts, node_id, mapping):
    """Duplicate the whole layer or folder behind a duplicated node; return the copy's id.

    Whole layers and folders go through ``layer_stack.duplicate``, exactly
    like the panel Duplicate.
    """
    from .mirror import owner_of
    from ..core import layer_stack
    layer, mask, prop, effect = owner_of(ts, node_id)
    if layer is None or mask is not None or effect is not None:
        return ''
    sources = layer_stack.descendants(ts, layer.id) | {layer.id}
    source_order = [item.id for item in ts.layers if item.id in sources]
    copy = layer_stack.duplicate(ts, layer_stack.index_of(ts, layer.id))
    copies = layer_stack.descendants(ts, copy.id) | {copy.id}
    mapping.update(zip(source_order, [item.id for item in ts.layers if item.id in copies]))
    return copy.id


def _adopt_stack_extra(ts, layer_map, node_id, mask_map):
    """Adopt a duplicated mask/filter node; return the copy's node id.

    When its owner layer was duplicated in the same pass, the extra joins the
    new layer's copy at the same index instead of putting a second mask on
    the original: the duplicated set stays connected to itself and nothing
    else. Otherwise it becomes a parked copy after the original.
    """
    from .mirror import effect_node_id, mask_node_id, owner_of
    layer, mask, prop, effect = owner_of(ts, node_id)
    if layer is None:
        return ''
    if mask is not None and layer.id in layer_map:
        new_layer = _layer(ts, layer_map[layer.id])
        index = next((i for i, item in enumerate(layer.masks) if item.id == mask.id), None)
        if new_layer is not None and index is not None and index < len(new_layer.masks):
            new_mask = new_layer.masks[index]
            return mask_node_id(new_layer, index, new_mask)
    if effect is not None and prop is not None and layer.id in layer_map:
        new_layer = _layer(ts, layer_map[layer.id])
        new_prop = channel_for(new_layer, prop.channel) if new_layer is not None else None
        index = next((i for i, item in enumerate(prop.effects) if item.id == effect.id), None)
        if new_prop is not None and index is not None and index < len(new_prop.effects):
            return effect_node_id(new_layer, prop.channel, index, new_prop.effects[index])
    if mask is not None:
        return mask_map.get(mask.id) or _duplicate_mask(ts, layer, mask, mask_map)
    if effect is not None and prop is not None:
        return _duplicate_effect(ts, layer, prop, effect)
    return ''


def adopt_duplicates(tree, ts):
    """Turn Shift+D duplicated nodes into duplicated stack items; True if any.

    Called from ``mirror.sync`` before reconciling. Layers adopt first, then
    masks and filters: an extra whose owner layer was duplicated in the same
    pass joins the new layer's copy at the same index, so the duplicated set
    stays connected to itself and nothing else. Anything else becomes a real
    duplicated layer, mask or filter with a fresh id, keeping the dropped
    position, while links touching extras are dropped so the link diff never
    misreads them as user edits. Unknown ids (frames, output, stale nodes) are
    left alone: the mirror's dedupe redraws them.
    """
    from . import mirror
    groups = {}
    for node in tree.nodes:
        identifier = node.op_id if hasattr(node, 'op_id') else node.get('op_id', '')
        groups.setdefault(identifier, []).append(node)
    extras = [node for identifier, members in groups.items()
              if identifier for node in members[1:]]
    if not extras:
        return False
    adopted = False
    with mirror.syncing():
        pointers = set()
        layer_map, mask_map = {}, {}
        ordered = sorted(extras, key=lambda node: 0 if getattr(node, 'bl_idname', '') in {
            'OPLayerNode', 'OPFolderNode'} else 1)
        # A folder copy already contains its descendants; adopt those nodes
        # into that copy instead of duplicating their stack items a second time.
        def depth(node):
            layer, mask, _, _ = mirror.owner_of(ts, node.op_id)
            if layer is None:
                return (2, 0)
            if mask is not None:
                from ..core.mask_stack import depth as mask_depth
                return (1, mask_depth(layer.masks, _index(layer.masks, mask)))
            level, parent = 0, layer.parent_id
            while parent:
                level += 1
                owner = _layer(ts, parent)
                parent = owner.parent_id if owner else ''
            return (0, level)
        ordered.sort(key=depth)
        for extra in ordered:
            identifier = extra.op_id if hasattr(extra, 'op_id') else extra.get('op_id', '')
            try:
                if getattr(extra, 'bl_idname', '') == 'OPImageNode':
                    # Tree-only state: a duplicated image node keeps its image and
                    # mapping under a fresh id (the feed mapping still names the
                    # original, so only the original redraws its wire).
                    from ..utils.ids import new_id
                    replacement = IMAGE_PREFIX + new_id()
                elif getattr(extra, 'bl_idname', '') in {'OPLayerNode', 'OPFolderNode'}:
                    replacement = layer_map.get(identifier) or _duplicate_layer(ts, identifier, layer_map)
                    if replacement:
                        layer_map[identifier] = replacement
                else:
                    replacement = _adopt_stack_extra(ts, layer_map, identifier, mask_map)
            except (LinkError, ValueError):
                continue
            if not replacement:
                continue
            pointers.add(extra.as_pointer())
            extra.op_id = replacement
            extra.name = replacement
            adopted = True
        if adopted:
            for link in list(tree.links):
                if link.from_node.as_pointer() in pointers or link.to_node.as_pointer() in pointers:
                    tree.links.remove(link)
    return adopted


def is_mask_share(ts, from_node, from_socket, to_node, to_socket):
    """True when a link shares one layer's mask with another layer's mask chain.

    Dropping a mask's Coverage onto a *different* layer's Mask input does not
    reorder anything: it publishes the source chain through an Anchor Point
    and appends a Published Input to the target chain (see ``share_mask``),
    so one mask can feed any number of layers.
    """
    if from_socket != 'out' or to_socket != 'mask':
        return False
    try:
        from .mirror import owner_of
        source, source_mask, _, _ = owner_of(ts, from_node)
        target, target_mask, _, target_effect = owner_of(ts, to_node)
    except (AttributeError, TypeError):
        return False
    return (source is not None and target is not None and source_mask is not None
            and target_mask is None and target_effect is None and source.id != target.id)


def _enable_endpoints(ts, source, target):
    """Enable mask/filter chain endpoints touched by a newly drawn chain link.

    Drawing a wire into a parked (disabled) mask or filter puts it back to
    work before the reorder below positions it. Anchor feeds keep their own
    path through ``connect``; layer endpoints are untouched.
    """
    from .mirror import owner_of
    for node_id in (source, target):
        try:
            _, mask, _, effect = owner_of(ts, node_id)
        except (AttributeError, TypeError):
            continue
        item = mask if mask is not None else effect
        if item is not None and not item.enabled:
            item.enabled = True


def _unplug_source(ts, source, target, to_socket):
    """Park the mask/filter whose chain wire was removed; True if one was.

    Cutting a chain wire bypasses its source node (``enabled = False``, which
    both evaluators skip), so an unlinked mask floats free instead of forcing
    the chain redrawn. Anchor feeds (``ref`` / ``detail#``) and layer sockets
    keep their existing field-driven behavior.
    """
    if to_socket not in {'prev', 'mask', 'members', 'in'} \
            and not to_socket.startswith('content#'):
        return False
    try:
        from .mirror import owner_of
        layer, mask, prop, effect = owner_of(ts, source)
        target_layer, target_mask, target_prop, target_effect = owner_of(ts, target)
        if layer is None or target_layer is None or layer.id != target_layer.id:
            return False
        if mask is not None and to_socket not in {'prev', 'mask', 'members'}:
            return False
        if effect is not None and not (
                (target_effect is not None and prop.channel == target_prop.channel and to_socket == 'in')
                or to_socket == 'content#' + prop.channel):
            return False
    except (AttributeError, TypeError):
        return False
    item = mask if mask is not None else effect
    if item is None or not item.enabled:
        return False
    item.enabled = False
    invalidate(ts)
    return True


def _remove_added(added):
    """Drop collection items appended by a refused share, by id."""
    for collection, identifier in reversed(added):
        try:
            collection.remove(next(i for i, item in enumerate(collection) if item.id == identifier))
        except (StopIteration, ValueError, RuntimeError):
            continue


def share_mask(ts, from_node, to_node):
    """Feed one layer's mask coverage into another layer's mask chain.

    Publishes the source chain at the dropped mask through an Anchor Point
    (inserted right after it, so the source layer looks unchanged) and appends
    a Published Input to the target chain reading that anchor. Sharing the
    same mask again reuses that anchor, so one mask fans out into any number
    of layers with a single Anchor Point. Refused (with nothing left behind)
    when the reference would cycle.
    """
    from .mirror import owner_of
    from ..core import layer_stack
    from ..utils.ids import new_id
    source, source_mask, _, _ = owner_of(ts, from_node)
    target, target_mask, _, target_effect = owner_of(ts, to_node)
    if (source is None or target is None or source_mask is None or target_mask is not None
            or target_effect is not None or source.id == target.id):
        raise LinkError('Masks chain within one layer; share coverage with an Anchor Point and a Published Input.')
    added = []
    source_mask_id, was_enabled = source_mask.id, source_mask.enabled
    source_mask.enabled = True
    try:
        if source_mask.source == 'ANCHOR' and source_mask.id:
            anchor_id = source_mask.id
            anchor_name = source_mask.name or 'Anchor'
        else:
            from ..ui.anchors import _next_name
            from ..core.mask_stack import block
            source_index = next(i for i, item in enumerate(source.masks) if item.id == source_mask_id)
            _, insertion = block(source.masks, source_index)
            parent_id = source_mask.parent_id
            follower = source.masks[insertion] if insertion < len(source.masks) else None
            if follower is not None and follower.source == 'ANCHOR' and follower.id and follower.enabled:
                anchor_id, anchor_name = follower.id, follower.name or 'Anchor'
            else:
                anchor = source.masks.add()
                anchor.id, anchor.source = new_id(), 'ANCHOR'
                anchor.name = _next_name(ts)
                anchor.parent_id = parent_id
                anchor_id, anchor_name = anchor.id, anchor.name
                added.append((source.masks, anchor_id))
                source.masks.move(len(source.masks) - 1, insertion)
                source.active_mask = insertion
        ref = target.masks.add()
        ref.id, ref.source, ref.name = new_id(), 'REFERENCE', anchor_name + ' Input'
        ref.reference_anchor = anchor_id
        added.append((target.masks, ref.id))
        target.active_mask = len(target.masks) - 1
        errors = issues(ts)
        if errors:
            raise LinkError(' | '.join(errors))
    except Exception:
        _remove_added(added)
        restored = next((m for m in source.masks if m.id == source_mask_id), None)
        if restored is not None:
            restored.enabled = was_enabled
        raise
    layer_stack.invalidate(ts)
    return anchor_id


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


def feeds(tree):
    """{layer id + tab + channel: image node id} feeding fill channels."""
    stored = tree.get('op_image_feeds', {})
    try:
        items = stored.items()
    except AttributeError:
        return {}
    return {str(key): str(value) for key, value in items}


def _store_feeds(tree, mapping):
    tree['op_image_feeds'] = dict(mapping)


def _apply_feed(prop, node):
    """Copy an image node's image and mapping onto a fill channel."""
    from .mirror import syncing
    with syncing():
        prop.image = node.image
        prop.projection = node.projection
        prop.scale = node.scale
        prop.rotation = node.rotation
        prop.offset = node.offset
        prop.triplanar_hardness = node.triplanar_hardness


def feed_key(layer_id, channel):
    return f'{layer_id}\t{channel}'


def feed_image(ts, tree, from_node, to_node, to_socket):
    """Feed an Image Texture node into a fill channel.

    The channel takes the node's image and its mapping (projection, scale,
    rotation, offset, hardness); the mapping is recorded on the tree so the
    mirror redraws the wire. Filtered channels refuse: their content socket
    already ends the filter chain, so set the image in the panel instead.
    """
    from . import mirror
    node = mirror.nodes_by_id(tree).get(from_node)
    if node is None or getattr(node, 'op_kind', '') != 'IMAGE':
        raise LinkError('That output is not an anchor point.')
    if node.image is None:
        raise LinkError('Choose an image on the Image Texture node first.')
    layer, mask, _prop, effect = _owner(ts, to_node)
    if mask is not None or effect is not None:
        raise LinkError('Images feed fill layer channels, not masks or filters.')
    if not to_socket.startswith('content#'):
        raise LinkError('Images feed a channel input.')
    prop = channel_for(layer, to_socket.partition('#')[2])
    if prop is None or layer.type != 'FILL':
        raise LinkError('Images feed fill layer channels.')
    if prop.effects:
        raise LinkError('That channel has filters: set its image in the panel, or remove the filters first.')
    prop.anchor = prop.anchor_channel = ''
    prop.procedural = ''
    prop.mesh_map = ''
    _apply_feed(prop, node)
    mapping = feeds(tree)
    mapping[feed_key(layer.id, prop.channel)] = from_node
    _store_feeds(tree, mapping)
    return from_node


def detach_image_feed(prop):
    """A panel image override ends its graph feed before a later node edit."""
    from . import mirror
    from ..core.layer_stack import replaying
    if mirror.is_syncing() or replaying():
        return
    from ..properties.channel import owning_set
    ts = owning_set(prop)
    tree = mirror.find_tree(ts) if ts is not None else None
    if tree is None:
        return
    mapping = feeds(tree)
    key = feed_key(prop.layer_id, prop.channel)
    if key in mapping:
        del mapping[key]
        with mirror.syncing():
            _store_feeds(tree, mapping)
            tree['op_links_dirty'] = True


def push_image_node(node, context):
    """Copy an image node's current values into every fill channel it feeds.

    Node-side edits (the picker, projection, transform) follow the wire that
    already exists; nothing happens for an unlinked node.
    """
    from . import mirror
    import bpy
    tree = getattr(node, 'id_data', None)
    if mirror.is_syncing() or tree is None or not is_graph(tree):
        return
    ts = mirror.texture_set_of(tree, getattr(bpy.context, 'scene', None))
    if ts is None:
        return
    identifier = node.op_id if hasattr(node, 'op_id') else node.get('op_id', '')
    for key, node_id in feeds(tree).items():
        if node_id != identifier:
            continue
        layer_id, _, channel = key.partition('\t')
        layer = _layer(ts, layer_id)
        prop = channel_for(layer, channel) if layer is not None else None
        if prop is None:
            continue
        _apply_feed(prop, node)


def feed_remove(ts, tree, source, to_node, to_socket, source_present):
    """Clear an image feed whose wire was removed; True if it was one.

    Cutting the wire stops the feed, so the channel image goes with it (unless
    the panel already replaced it). Deleting the node instead keeps the
    channel's image as an ordinary panel value.
    """
    if not to_socket.startswith('content#'):
        return False
    from . import mirror
    mapping = feeds(tree)
    key = feed_key(to_node, to_socket.partition('#')[2])
    if mapping.get(key) != source:
        return False
    del mapping[key]
    _store_feeds(tree, mapping)
    if source_present:
        node = mirror.nodes_by_id(tree).get(source)
        layer = _layer(ts, to_node)
        prop = channel_for(layer, to_socket.partition('#')[2]) if layer is not None else None
        if prop is not None and node is not None:
            try:
                if prop.image and node.image and prop.image == node.image:
                    prop.image = None
            except ReferenceError:
                pass
    invalidate(ts)
    return True


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
    shown_links, shown_nodes = mirror.synced(tree)
    actual = link_keys(tree)
    status, touched = None, set()
    # Diff against what the tree showed after its last sync: a projection the
    # tree may not have caught up with yet (a mask just added in the panel) is not an edit.
    present = set(mirror.nodes_by_id(tree))
    # Shift+D duplicates share their op_id until the next sync adopts them: their
    # links are not user edits, so both diffs ignore anything touching them.
    seen, duplicates = set(), set()
    for node in tree.nodes:
        identifier = node.op_id if hasattr(node, 'op_id') else node.get('op_id', '')
        if identifier in seen:
            duplicates.add(identifier)
        seen.add(identifier)
    by_id = mirror.nodes_by_id(tree)
    missing = shown_nodes - present
    deleted = {layer.id for layer in ts.layers if layer.id in missing}
    # Only nodes present at the last sync can express deletions, including when
    # a layer and its masks are deleted together or panel changes are pending.
    for identifier in sorted(deleted):
        delete_layer(ts, identifier)
        tree['op_links_dirty'] = True
    for identifier in sorted(missing - deleted):
        if mirror.owner_of(ts, identifier)[0] is None:
            continue
        try:
            delete(ts, identifier)
        except LinkError as error:
            status = str(error)
        tree['op_links_dirty'] = True
    for source, from_socket, target, to_socket in sorted(actual - shown_links):
        if source in duplicates or target in duplicates:
            continue
        touched.add((target, to_socket))
        try:
            source_node = by_id.get(source)
            if source_node is not None and getattr(source_node, 'op_kind', '') == 'IMAGE':
                feed_image(ts, tree, source, target, to_socket)
            elif is_mask_share(ts, source, from_socket, target, to_socket):
                share_mask(ts, source, target)
            elif is_chain_link(ts, source, from_socket, target, to_socket):
                reorder(ts, source, from_socket, target, to_socket)
                _enable_endpoints(ts, source, target)
            else:
                connect(ts, source, from_socket, target, to_socket)
        except LinkError as error:
            status = str(error)
        except ValueError as error:   # layer_stack refusals (folder cycles)
            status = str(error)
        tree['op_links_dirty'] = True
    for source, from_socket, target, to_socket in sorted(shown_links - actual):
        tree['op_links_dirty'] = True
        # An input that just received a link was either re-pointed (the field
        # already changed) or kept its reference after a refusal; the sync
        # redraws the right link either way.
        if target == OUTPUT_NODE or (target, to_socket) in touched:
            continue
        if source in deleted or target in deleted:
            continue  # its node was deleted with it; the subtree is already gone
        if feed_remove(ts, tree, source, target, to_socket, source in present):
            continue
        if _unplug_source(ts, source, target, to_socket):
            continue
        disconnect(ts, target, to_socket)
    if len(tree.nodes) != tree.get('op_node_count', len(tree.nodes)):
        tree['op_links_dirty'] = True   # a deleted frame/layer node or a pasted duplicate: redraw
    # Edits made before the next sync diff against the tree as it is now.
    tree['op_links'] = ['\t'.join(key) for key in actual]
    tree['op_nodes'] = sorted(shown_nodes & present)
    tree['op_node_count'] = len(tree.nodes)
    if status:
        scene = getattr(bpy.context, 'scene', None)
        if scene is not None:
            scene.open_painter.status = status
