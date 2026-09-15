"""Anchor point registry: pass-through stack items publishing the result at their position.

A mask anchor is an OP_Mask with source ANCHOR, identified by the mask id; it
publishes the accumulated coverage of the masks before it. A channel anchor is an
OP_TextureEffect with kind ANCHOR, identified by the effect id; it publishes the
channel's straight RGBA after the effects before it. A layer with publish_output
owns the OUTPUT anchor "<layer id>:out" (the layer blended over its backdrop, in
its group context); a folder with publish_group owns the GROUP anchor
"<folder id>:group" (its child composite before the folder blends). Those two
kinds carry every channel: consumers choose one with channel_of(). Consumers
store the anchor id in one of CONSUMER_FIELDS. Works on RNA groups and
evaluation snapshots alike.
"""
from dataclasses import dataclass

CONSUMER_FIELDS = {'REFERENCE_MASK': 'reference_anchor', 'CHANNEL': 'anchor',
                   'DETAIL_HEIGHT': 'detail_height_anchor', 'DETAIL_NORMAL': 'detail_normal_anchor'}
CONSUMER_LABELS = {'REFERENCE_MASK': 'Reference mask', 'CHANNEL': 'Channel content',
                   'DETAIL_HEIGHT': 'Micro Height', 'DETAIL_NORMAL': 'Micro Normal'}
DETAIL_KINDS = frozenset({'EDGE_WEAR', 'CHIPS', 'DIRT', 'CAVITY_DIRT', 'RUST', 'DRIPS'})
COMPOSITE_KINDS = frozenset({'OUTPUT', 'GROUP'})
DEFAULT_CHANNEL = 'BASE_COLOR'
OUTPUT_SUFFIX, GROUP_SUFFIX = ':out', ':group'


def output_id(layer_id):
    return layer_id + OUTPUT_SUFFIX


def group_id(layer_id):
    return layer_id + GROUP_SUFFIX


def derived_mapping(mapping):
    """Extend a layer id mapping with the derived OUTPUT/GROUP anchor ids."""
    result = dict(mapping)
    for old, new in mapping.items():
        result[output_id(old)] = output_id(new)
        result[group_id(old)] = group_id(new)
    return result


def channel_of(anchor, requested=None):
    """The channel a consumer reads: fixed for stack anchors, chosen for composite anchors."""
    if anchor.kind not in COMPOSITE_KINDS:
        return anchor.channel
    from ..constants import CHANNELS
    return requested if requested in CHANNELS else DEFAULT_CHANNEL


@dataclass(frozen=True)
class Anchor:
    id: str
    kind: str          # 'MASK', 'CHANNEL', 'OUTPUT' or 'GROUP'
    layer: object
    layer_id: str
    channel: str       # 'MASK' for mask anchors, '*' for composite anchors, else the channel name
    position: int      # index of the anchor item; the published prefix is stack[:position]
    name: str


def index(ts, memo=None):
    # Not cached across calls: anchor ids are assigned without an edit-serial
    # bump, and Anchor.layer holds an RNA reference that a collection
    # reallocation invalidates. The walk costs microseconds per layer.
    key = ('anchors', getattr(ts, 'id', ''), getattr(ts, 'edit_serial', 0))
    if memo is not None and key in memo:
        return memo[key]
    result = {}
    for layer in ts.layers:
        if getattr(layer, 'publish_output', False):
            result[output_id(layer.id)] = Anchor(output_id(layer.id), 'OUTPUT', layer, layer.id, '*', -1,
                                                 layer.name + ' output')
        if layer.type == 'FOLDER' and getattr(layer, 'publish_group', False):
            result[group_id(layer.id)] = Anchor(group_id(layer.id), 'GROUP', layer, layer.id, '*', -1,
                                                layer.name + ' composite')
        for position, mask in enumerate(layer.masks):
            if mask.source == 'ANCHOR' and mask.id:
                result.setdefault(mask.id, Anchor(mask.id, 'MASK', layer, layer.id, 'MASK', position, mask.name))
        for prop in layer.channels:
            for position, effect in enumerate(prop.effects):
                if effect.kind == 'ANCHOR' and effect.id:
                    result.setdefault(effect.id, Anchor(effect.id, 'CHANNEL', layer, layer.id,
                                                        prop.channel, position, effect.name))
    if memo is not None:
        memo[key] = result
    return result


def resolve(ts, identifier, memo=None):
    return index(ts, memo).get(identifier) if identifier else None


def consumed(item):
    """Anchor ids an item reads: channel content, reference masks, generator detail inputs."""
    if hasattr(item, 'effects'):
        value = getattr(item, 'anchor', '')
        return (value,) if value else ()
    source = getattr(item, 'source', '')
    if source == 'REFERENCE':
        value = getattr(item, 'reference_anchor', '')
        return (value,) if value else ()
    if source == 'GENERATOR' and getattr(item, 'generator', '') in DETAIL_KINDS:
        return tuple(value for value in (getattr(item, 'detail_height_anchor', ''),
                                         getattr(item, 'detail_normal_anchor', '')) if value)
    return ()


def consumed_channels(item):
    """((anchor id, requested channel), ...) an item reads; the request only
    matters for composite anchors (see channel_of)."""
    if hasattr(item, 'effects'):
        value = getattr(item, 'anchor', '')
        return ((value, getattr(item, 'anchor_channel', '') or item.channel),) if value else ()
    source = getattr(item, 'source', '')
    if source == 'REFERENCE':
        value = getattr(item, 'reference_anchor', '')
        return ((value, getattr(item, 'reference_channel', '')),) if value else ()
    if source == 'GENERATOR' and getattr(item, 'generator', '') in DETAIL_KINDS:
        return tuple((value, slot) for slot, value in (('HEIGHT', getattr(item, 'detail_height_anchor', '')),
                                                       ('NORMAL', getattr(item, 'detail_normal_anchor', '')))
                     if value)
    return ()


def consumers(ts, identifier):
    """[(layer, kind, item)] reading an anchor: find-references, deletion reports, dirtying."""
    found = []
    for layer in ts.layers:
        for prop in layer.channels:
            if getattr(prop, 'anchor', '') == identifier:
                found.append((layer, 'CHANNEL', prop))
        for mask in layer.masks:
            if mask.source == 'REFERENCE' and mask.reference_anchor == identifier:
                found.append((layer, 'REFERENCE_MASK', mask))
            if mask.source == 'GENERATOR' and mask.generator in DETAIL_KINDS:
                if mask.detail_height_anchor == identifier:
                    found.append((layer, 'DETAIL_HEIGHT', mask))
                if mask.detail_normal_anchor == identifier:
                    found.append((layer, 'DETAIL_NORMAL', mask))
    return found


def owned(layer):
    """Anchor ids defined by one layer, for deletion reports."""
    derived = ([output_id(layer.id)] if getattr(layer, 'publish_output', False) else []) + \
              ([group_id(layer.id)] if layer.type == 'FOLDER' and getattr(layer, 'publish_group', False) else [])
    return (derived + [mask.id for mask in layer.masks if mask.source == 'ANCHOR' and mask.id]
            + [effect.id for prop in layer.channels for effect in prop.effects
               if effect.kind == 'ANCHOR' and effect.id])


KIND_ORDER = {'MASK': 0, 'CHANNEL': 1, 'GROUP': 2, 'OUTPUT': 3}


def sort_key(ts, anchor):
    layer_index = next((i for i, layer in enumerate(ts.layers) if layer.id == anchor.layer_id), -1)
    return (layer_index, KIND_ORDER.get(anchor.kind, 1), anchor.position)


def stack_label(anchor):
    if anchor.kind == 'MASK':
        return 'Mask stack'
    if anchor.kind == 'OUTPUT':
        return 'Layer output'
    if anchor.kind == 'GROUP':
        return 'Folder composite'
    from ..constants import CHANNEL_LABELS
    return CHANNEL_LABELS.get(anchor.channel, anchor.channel) + ' filters'


def contributors(ts, anchor):
    """Layer ids whose edits change a composite anchor: the producer, everything
    composed below it in its group, and the backdrop chain through passthrough
    folders. Stack anchors contribute only their own layer."""
    if anchor.kind not in COMPOSITE_KINDS:
        return {anchor.layer_id}
    by_id = {layer.id: layer for layer in ts.layers}
    children = {}
    for layer in ts.layers:
        children.setdefault(getattr(layer, 'parent_id', ''), []).append(layer)

    def subtree(layer_id):
        found, pending = set(), [layer_id]
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.add(current)
            pending.extend(child.id for child in children.get(current, ()))
        return found

    def below(layer_id):
        """The layer's earlier siblings (with descendants) plus the group backdrop chain."""
        layer = by_id.get(layer_id)
        if layer is None:
            return set()
        found = set()
        parent_id = getattr(layer, 'parent_id', '')
        for sibling in children.get(parent_id, ()):
            if sibling.id == layer_id:
                break
            found |= subtree(sibling.id)
        parent = by_id.get(parent_id) if parent_id else None
        if parent is not None and parent.group_mode == 'PASSTHROUGH':
            found |= below(parent.id)
        return found

    result = subtree(anchor.layer_id)
    if anchor.kind == 'OUTPUT':
        result |= below(anchor.layer_id)
    else:
        layer = by_id.get(anchor.layer_id)
        if layer is not None and layer.group_mode == 'PASSTHROUGH':
            result |= below(anchor.layer_id)
    return result


def label(ts, anchor):
    return f'{anchor.name} · {anchor.layer.name} · {stack_label(anchor)}'
