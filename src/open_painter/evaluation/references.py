"""Within-set published inputs and anchor points. References never share editable paint buffers.

Channel references sample the source layer's own channel before composition;
mask references sample its mask stack (optionally through a selected effect).
Anchor points publish a stack prefix at their position; a missing anchor reads as
neutral and is reported by warnings(), while dependency cycles remain errors.
This module stays bpy-free (tests/unit run it under plain Python): warnings that need
evaluated geometry (mesh_masks.warnings) are composed by core.validation.warnings.
Composite anchors (a layer's blended output, a folder's child composite) are
modelled with composition nodes ('OUT', layer, channel), ('BELOW', layer,
channel) and ('GROUP', folder, channel), so "an output may only feed layers
above it" falls out of the same cycle check. Legacy folder channel references
are rejected: publish the folder composite instead.
"""
from contextlib import contextmanager

import numpy as np

from . import structure_memo

LUMA = np.array((.2126, .7152, .0722), np.float32)
COMPOSITION = frozenset({'OUT', 'BELOW', 'GROUP'})


_scoped = {}   # (set pointer, id) -> graph(ts), while a caller holds it (``scope``)


@contextmanager
def scope(ts):
    """Answer ``graph(ts)`` once for a caller that asks several times without editing in
    between: a build checks the hierarchy's issues, then collects its warnings."""
    key = (ts.as_pointer(), ts.id) if hasattr(ts, 'as_pointer') else None
    owner = key is not None and key not in _scoped
    if owner:
        _scoped[key] = None
    try:
        yield
    finally:
        if owner:
            _scoped.pop(key, None)


def graph(ts):
    key = (ts.as_pointer(), ts.id) if _scoped and hasattr(ts, 'as_pointer') else None
    if key is not None and _scoped.get(key) is not None:
        return _scoped[key]
    result = _graph(ts)
    if key is not None and key in _scoped:
        _scoped[key] = result
    return result


def _graph(ts):
    """Item-granular dependency edges, structural errors and non-fatal warnings.

    Nodes are stack positions (layer id, 'MASK' or channel, count): each item
    depends on the accumulated result before it, consumers depend on the anchor's
    position, and a channel consumer feeds the channel's own effect chain.
    """
    from ..core.layer_stack import channel_for
    from .anchors import index, consumed_channels, channel_of
    registry, by_id = index(ts), {layer.id: layer for layer in ts.layers}
    edges, errors, warnings, seen_ids = {}, [], [], set()
    children, materialized = {}, set()
    for layer in ts.layers:
        children.setdefault(getattr(layer, 'parent_id', ''), []).append(layer)

    def add(node, target):
        edges.setdefault(node, set()).add(target)

    def composition(node):
        """Materialize the composition chain a composite anchor depends on."""
        if node in materialized:
            return
        materialized.add(node)
        kind, layer_id, channel = node
        layer = by_id.get(layer_id)
        if layer is None:
            return
        parent_id = getattr(layer, 'parent_id', '')
        siblings = children.get(parent_id, [])
        parent = by_id.get(parent_id) if parent_id else None
        if kind == 'OUT':
            prop = channel_for(layer, channel)
            if layer.type != 'FOLDER' and prop is not None:
                add(node, (layer.id, channel, len(prop.effects)))
            add(node, (layer.id, 'MASK', len(layer.masks)))
            if layer.type == 'FOLDER':
                add(node, ('GROUP', layer.id, channel))
                composition(('GROUP', layer.id, channel))
            add(node, ('BELOW', layer.id, channel))
            composition(('BELOW', layer.id, channel))
        elif kind == 'BELOW':
            position = next((i for i, item in enumerate(siblings) if item.id == layer.id), 0)
            if position > 0:
                add(node, ('OUT', siblings[position - 1].id, channel))
                composition(('OUT', siblings[position - 1].id, channel))
            elif parent is not None and parent.group_mode == 'PASSTHROUGH':
                add(node, ('BELOW', parent.id, channel))
                composition(('BELOW', parent.id, channel))
        else:  # GROUP: the child composite, over the backdrop for passthrough folders
            own = children.get(layer.id, [])
            if own:
                add(node, ('OUT', own[-1].id, channel))
                composition(('OUT', own[-1].id, channel))
            if layer.group_mode == 'PASSTHROUGH':
                add(node, ('BELOW', layer.id, channel))
                composition(('BELOW', layer.id, channel))

    def anchored(node, layer, item):
        for identifier, requested in consumed_channels(item):
            anchor = registry.get(identifier)
            if anchor is None:
                warnings.append(f'{layer.name}: anchor point is missing')
                continue
            if anchor.kind in {'OUTPUT', 'GROUP'}:
                target = ('OUT' if anchor.kind == 'OUTPUT' else 'GROUP', anchor.layer_id,
                          channel_of(anchor, requested))
                add(node, target)
                composition(target)
                continue
            add(node, (anchor.layer_id, anchor.channel, anchor.position))

    for layer in ts.layers:
        for position, mask in enumerate(layer.masks):
            node = (layer.id, 'MASK', position + 1)
            add(node, (layer.id, 'MASK', position))
            if mask.source == 'ANCHOR':
                if mask.id in seen_ids:
                    warnings.append(f'{layer.name}: duplicate anchor id')
                seen_ids.add(mask.id)
                continue
            if not mask.enabled:
                continue
            if mask.source == 'REFERENCE' and not mask.reference_anchor:
                target = by_id.get(mask.reference_layer)
                if target is None:
                    warnings.append(f'{layer.name}: published source layer is missing')
                    continue
                if mask.reference_channel == 'MASK':
                    through = len(target.masks)
                    if mask.reference_mask:
                        through = next((i + 1 for i, m in enumerate(target.masks) if m.id == mask.reference_mask), None)
                        if through is None:
                            warnings.append(f'{layer.name}: published mask effect is missing')
                            continue
                    add(node, (target.id, 'MASK', through))
                elif target.type == 'FOLDER':
                    errors.append(f'{layer.name}: reference a folder mask, or publish the folder composite '
                                  'and pick its anchor')
                else:
                    prop = channel_for(target, mask.reference_channel)
                    count = 0 if prop is None or mask.reference_stage == 'PRE_FILTER' else len(prop.effects)
                    add(node, (target.id, mask.reference_channel, count))
            anchored(node, layer, mask)
        for prop in layer.channels:
            for position, effect in enumerate(prop.effects):
                add((layer.id, prop.channel, position + 1), (layer.id, prop.channel, position))
                if effect.kind == 'ANCHOR' and effect.id:
                    if effect.id in seen_ids:
                        warnings.append(f'{layer.name}: duplicate anchor id')
                    seen_ids.add(effect.id)
            if prop.enabled:
                anchored((layer.id, prop.channel, 0), layer, prop)

    state = {}
    for start in edges:
        if start in state:
            continue
        stack = [(start, iter(edges.get(start, ())))]
        state[start] = 'visiting'
        while stack:
            node, targets = stack[-1]
            for target in targets:
                status = state.get(target)
                if status == 'visiting':
                    names = {by_id[n[1] if n[0] in COMPOSITION else n[0]].name for n, _ in stack
                             if (n[1] if n[0] in COMPOSITION else n[0]) in by_id}
                    errors.append('Published inputs contain a reference cycle through ' + ', '.join(sorted(names)))
                    stack.clear()
                    break
                if status is None:
                    state[target] = 'visiting'
                    stack.append((target, iter(edges.get(target, ()))))
                    break
            else:
                state[node] = 'done'
                stack.pop()
        if errors and errors[-1].startswith('Published inputs contain a reference cycle'):
            break
    return edges, errors, warnings


def issues(ts):
    """Errors that block evaluation: reference cycles and folder composite references."""
    return list(graph(ts)[1])


def warnings(ts):
    """Missing anchors and published sources; evaluation continues with neutral values.
    Structural only: ``core.validation.warnings`` adds the geometry-dependent ones."""
    return list(graph(ts)[2])


_READERS = structure_memo.store()   # layer positions holding an item that reads another layer


def _reader_layers(ts):
    """The layers holding an item that reads another layer: a reference mask, an anchored
    channel or a generator's detail input. Every field that makes one is structural, so
    the positions are memoised per structure; each property callback asks."""
    from .anchors import consumed

    def walk():
        return len(ts.layers), tuple(position for position, layer in enumerate(ts.layers)
                                     if any(m.source == 'REFERENCE' or consumed(m) for m in layer.masks)
                                     or any(consumed(p) for p in layer.channels))
    count, positions = structure_memo.memoised(_READERS, ts, walk)
    layers = ts.layers
    if count != len(layers):
        return list(layers)   # the stack moved under an unchanged serial: every layer may read
    return [layers[position] for position in positions]


def dependent_channels(ts, source_layer, source_channel=None):
    """Transitive consumers of a changed source, restricted by enabled channels."""
    from .anchors import index, consumed_channels, channel_of, contributors, COMPOSITE_KINDS
    readers = _reader_layers(ts)
    if not readers:
        return set()
    registry = index(ts)
    composite_sources = {}

    def reads(item, identifier, channel):
        for key, requested in consumed_channels(item):
            anchor = registry.get(key)
            if anchor is None:
                continue
            if anchor.kind in COMPOSITE_KINDS:
                if key not in composite_sources:
                    composite_sources[key] = contributors(ts, anchor)
                if identifier in composite_sources[key] and (channel is None
                                                             or channel_of(anchor, requested) == channel):
                    return True
            elif anchor.layer_id == identifier and (channel is None or anchor.channel == channel):
                return True
        return False

    affected, pending = set(), [(source_layer, source_channel)]
    seen = set()
    while pending:
        identifier, channel = pending.pop()
        if (identifier,channel) in seen:
            continue
        seen.add((identifier,channel))
        for layer in readers:
            if any(m.enabled and ((m.source == 'REFERENCE' and not m.reference_anchor and m.reference_layer == identifier
                                   and (channel is None or m.reference_channel == channel))
                                  or reads(m, identifier, channel)) for m in layer.masks):
                affected.update(p.channel for p in layer.channels if p.enabled)
                pending.append((layer.id,None))
                continue
            for prop in layer.channels:
                if prop.enabled and reads(prop, identifier, channel):
                    affected.add(prop.channel)
                    pending.append((layer.id, prop.channel))
    return affected


def mask_value(rgba, channel):
    """Gray coverage of a published channel: red for scalars, luminance for colors, times alpha."""
    gray = rgba[...,0] if channel not in {'BASE_COLOR','EMISSION','NORMAL'} else rgba[...,:3] @ LUMA
    return gray * rgba[...,3]


def evaluate_anchor(ts, identifier, n, y0, y1, cache, resources, channel=None):
    """Straight RGBA rows for a channel anchor, coverage rows for a mask anchor, None if missing.

    ``channel`` selects the channel of a composite anchor (see anchors.channel_of).
    """
    from ..core.layer_stack import channel_for
    from .anchors import resolve, channel_of, COMPOSITE_KINDS
    from .snapshots import capture, Parameters
    anchor = resolve(ts, identifier, getattr(resources, 'signatures', None))
    if anchor is None:
        return None
    if anchor.kind in COMPOSITE_KINDS:
        from .composite import Composer
        channel = channel_of(anchor, channel)
        key = ('anchor_out', identifier, channel, n, y0, y1, getattr(ts, 'edit_serial', 0))
        if key not in cache:
            composer = Composer(ts, channel, n, cache, resources)
            cache[key] = (composer.output(anchor.layer_id, y0, y1) if anchor.kind == 'OUTPUT'
                          else composer.group(anchor.layer_id, y0, y1))
        return cache[key]
    layer = anchor.layer if isinstance(anchor.layer, Parameters) else capture(anchor.layer)
    if anchor.kind == 'MASK':
        from .mask_eval import evaluate_masks
        # Derived ids keep the prefix apart from the owner's stack in every cache key.
        prefix = layer.replaced(id=layer.id + ':anchor:' + identifier, masks=tuple(layer.masks)[:anchor.position])
        return evaluate_masks(ts, prefix, n, y0, y1, cache, resources)
    prop = channel_for(layer, anchor.channel)
    if prop is None or not prop.enabled or layer.type == 'FOLDER':
        return np.zeros((y1 - y0, n, 4), np.float32)
    from .channel_eval import source
    prefix = prop.replaced(effects=tuple(prop.effects)[:anchor.position])
    return np.broadcast_to(source(prefix, layer.type, n, y0, y1, cache, ts, resources), (y1 - y0, n, 4))


def evaluate(ts, mask, n, y0, y1, cache, resources):
    from ..core.layer_stack import channel_for
    from .snapshots import capture, Parameters
    if mask.reference_anchor:
        from .anchors import resolve, channel_of
        value = evaluate_anchor(ts, mask.reference_anchor, n, y0, y1, cache, resources,
                                channel=mask.reference_channel)
        if value is None:
            return np.zeros((y1-y0,n),np.float32)
        if value.ndim == 2:
            return value
        anchor = resolve(ts, mask.reference_anchor, getattr(resources, 'signatures', None))
        return np.ascontiguousarray(mask_value(value, channel_of(anchor, mask.reference_channel)), np.float32)
    source_layer = next((layer for layer in ts.layers if layer.id == mask.reference_layer),None)
    if source_layer is None:
        return np.zeros((y1-y0,n),np.float32)
    layer = source_layer if isinstance(source_layer, Parameters) else capture(source_layer)
    if mask.reference_channel == 'MASK':
        from .mask_eval import evaluate_masks
        masks = list(layer.masks)
        if mask.reference_mask:
            index = next((i for i,m in enumerate(masks) if m.id == mask.reference_mask),None)
            if index is None:
                return np.zeros((y1-y0,n),np.float32)
            masks = masks[:index+1]
        if mask.reference_stage == 'PRE_FILTER':
            masks = [m for m in masks if m.source != 'FILTER']
        # Distinguish a selected source stage from the original full stack.
        layer = layer.replaced(id=layer.id+':published:'+mask.reference_mask+':'+mask.reference_stage,
                               masks=tuple(masks))
        return evaluate_masks(ts,layer,n,y0,y1,cache,resources)
    prop = channel_for(layer,mask.reference_channel)
    if prop is None or not prop.enabled:
        return np.zeros((y1-y0,n),np.float32)
    if layer.type == 'FOLDER':
        raise ValueError('Folder references support published masks only')
    if mask.reference_stage == 'PRE_FILTER':
        prop = prop.replaced(effects=())
    from .channel_eval import source
    rgba = source(prop,layer.type,n,y0,y1,cache,ts,resources)
    return np.broadcast_to(mask_value(rgba, mask.reference_channel), (y1-y0,n)).copy()
