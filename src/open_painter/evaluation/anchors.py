"""Anchor point registry: pass-through stack items publishing the result at their position.

A mask anchor is an OP_Mask with source ANCHOR, identified by the mask id; it
publishes the accumulated coverage of the masks before it. A channel anchor is an
OP_TextureEffect with kind ANCHOR, identified by the effect id; it publishes the
channel's straight RGBA after the effects before it. Consumers store the anchor id
in one of CONSUMER_FIELDS. Works on RNA groups and evaluation snapshots alike.
"""
from dataclasses import dataclass

CONSUMER_FIELDS = {'REFERENCE_MASK': 'reference_anchor', 'CHANNEL': 'anchor',
                   'DETAIL_HEIGHT': 'detail_height_anchor', 'DETAIL_NORMAL': 'detail_normal_anchor'}
CONSUMER_LABELS = {'REFERENCE_MASK': 'Reference mask', 'CHANNEL': 'Channel content',
                   'DETAIL_HEIGHT': 'Micro Height', 'DETAIL_NORMAL': 'Micro Normal'}
DETAIL_KINDS = frozenset({'EDGE_WEAR', 'CHIPS', 'DIRT', 'CAVITY_DIRT', 'RUST', 'DRIPS'})


@dataclass(frozen=True)
class Anchor:
    id: str
    kind: str          # 'MASK' or 'CHANNEL'
    layer: object
    layer_id: str
    channel: str       # 'MASK' for mask anchors, else the channel name
    position: int      # index of the anchor item; the published prefix is stack[:position]
    name: str


def index(ts, memo=None):
    key = ('anchors', getattr(ts, 'id', ''), getattr(ts, 'edit_serial', 0))
    if memo is not None and key in memo:
        return memo[key]
    result = {}
    for layer in ts.layers:
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
    return ([mask.id for mask in layer.masks if mask.source == 'ANCHOR' and mask.id]
            + [effect.id for prop in layer.channels for effect in prop.effects
               if effect.kind == 'ANCHOR' and effect.id])


def sort_key(ts, anchor):
    layer_index = next((i for i, layer in enumerate(ts.layers) if layer.id == anchor.layer_id), -1)
    return (layer_index, 0 if anchor.kind == 'MASK' else 1, anchor.position)


def stack_label(anchor):
    if anchor.kind == 'MASK':
        return 'Mask stack'
    from ..constants import CHANNEL_LABELS
    return CHANNEL_LABELS.get(anchor.channel, anchor.channel) + ' filters'


def label(ts, anchor):
    return f'{anchor.name} · {anchor.layer.name} · {stack_label(anchor)}'
