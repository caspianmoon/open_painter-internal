"""Within-set published inputs. References never share editable paint buffers.

Channel references sample the source layer's own channel before composition;
mask references sample its mask stack (optionally through a selected effect).
Folder mask references are supported; folder composite channels are not implied.
"""
import numpy as np


def issues(ts):
    by_id = {layer.id: layer for layer in ts.layers}
    edges = {identifier: set() for identifier in by_id}
    problems = []
    for layer in ts.layers:
        for mask in layer.masks:
            if not mask.enabled or mask.source != 'REFERENCE':
                continue
            target = by_id.get(mask.reference_layer)
            if target is None:
                problems.append(f'{layer.name}: published source layer is missing')
                continue
            if mask.reference_mask and not any(m.id == mask.reference_mask for m in target.masks):
                problems.append(f'{layer.name}: published mask effect is missing')
            if target.type == 'FOLDER' and mask.reference_channel != 'MASK':
                problems.append(f'{layer.name}: reference a folder mask, not a folder composite channel')
            edges[layer.id].add(target.id)
    visiting, done = set(), set()
    def visit(identifier):
        if identifier in visiting:
            raise ValueError('Published inputs contain a reference cycle')
        if identifier in done:
            return
        visiting.add(identifier)
        for target in edges[identifier]:
            visit(target)
        visiting.remove(identifier); done.add(identifier)
    try:
        for identifier in edges:
            visit(identifier)
    except ValueError as exc:
        problems.append(str(exc))
    return problems


def dependent_channels(ts, source_layer, source_channel=None):
    """Transitive consumers of a changed source, restricted by enabled channels."""
    affected, pending = set(), [(source_layer, source_channel)]
    seen = set()
    while pending:
        identifier, channel = pending.pop()
        if (identifier,channel) in seen:
            continue
        seen.add((identifier,channel))
        for layer in ts.layers:
            if any(m.enabled and m.source == 'REFERENCE' and m.reference_layer == identifier and
                   (channel is None or m.reference_channel == channel) for m in layer.masks):
                affected.update(p.channel for p in layer.channels if p.enabled)
                pending.append((layer.id,None))
    return affected


def evaluate(ts, mask, n, y0, y1, cache, resources):
    from ..core.layer_stack import channel_for
    from .snapshots import capture, Parameters
    source_layer = next((layer for layer in ts.layers if layer.id == mask.reference_layer),None)
    if source_layer is None:
        raise ValueError('Published source layer is missing')
    layer = source_layer if isinstance(source_layer, Parameters) else capture(source_layer)
    if mask.reference_channel == 'MASK':
        from .mask_eval import evaluate_masks
        masks = list(layer.masks)
        if mask.reference_mask:
            index = next((i for i,m in enumerate(masks) if m.id == mask.reference_mask),None)
            if index is None:
                raise ValueError('Published mask effect is missing')
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
    gray = rgba[...,0] if mask.reference_channel not in {'BASE_COLOR','EMISSION','NORMAL'} else rgba[...,:3] @ np.array((.2126,.7152,.0722),np.float32)
    return np.broadcast_to(gray * rgba[...,3], (y1-y0,n)).copy()
