"""One authoritative UDIM recipe, with editable tile-local RNA projections.

The owner set stores the authoritative recipe. Other members materialize it into
their ordinary layer collections so Blender's native lists and existing node
interface continue to work. Images and evaluation caches remain local. Every edit
publishes through this resolver synchronously, inside the same Blender undo step.
Unmarked files retain independent stacks.
"""
from contextlib import contextmanager

_syncing = 0
LOCAL = frozenset({'image', 'image_name', 'detail_image', 'detail_image_name', 'cache',
                   'dirty', 'raster_signature', 'refresh_serial'})
IGNORED = LOCAL | {'selected', 'expanded', 'active_mask', 'active_effect', 'order'}


def members(ts):
    key = getattr(ts, 'shared_stack_id', '')
    return [item for item in ts.id_data.open_painter.texture_sets if key and item.shared_stack_id == key]


def layer_owner(ts):
    """The stable owner of the recipe edited through any tile."""
    if not getattr(ts, 'shared_stack_id', ''):
        return ts
    if ts.shared_stack_version != 1:
        raise ValueError('Unsupported shared UDIM recipe version')
    return next((item for item in members(ts) if item.id == ts.shared_stack_owner), ts)


@contextmanager
def syncing():
    global _syncing
    _syncing += 1
    try:
        yield
    finally:
        _syncing -= 1


def _signature(ts):
    from .layer_stack import snapshot
    layers = [snapshot(layer) for layer in ts.layers]
    ids = {}

    def collect(value):
        if isinstance(value, dict):
            for name, item in value.items():
                if name == 'id' and item:
                    ids.setdefault(item, f'id:{len(ids)}')
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(layers)

    def normalize(value):
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items() if key not in IGNORED}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, str):
            return ids.get(value, value)
        return value
    return normalize(layers)


def _write(data, target):
    """Update settings in place; stable nodes keep their live RNA bindings."""
    import bpy
    for key, value in data.items():
        if key in LOCAL:
            continue
        prop = target.bl_rna.properties.get(key)
        if prop is None:
            continue
        current = getattr(target, key)
        if prop.type == 'COLLECTION':
            # ID-bearing collections are reconciled without invalidating unchanged
            # wrappers; channel rows use their stable channel identifier.
            identity = lambda item: item.get('id') or item.get('channel')
            desired = [identity(item) for item in value]
            if all(desired):
                for index in range(len(current) - 1, -1, -1):
                    item = current[index]
                    if (getattr(item, 'id', '') or getattr(item, 'channel', '')) not in desired:
                        current.remove(index)
                for index, values in enumerate(value):
                    found = next((i for i, item in enumerate(current)
                        if (getattr(item, 'id', '') or getattr(item, 'channel', '')) == desired[index]), None)
                    if found is None:
                        current.add()
                        found = len(current) - 1
                    if found != index:
                        current.move(found, index)
                    _write(values, current[index])
            else:
                while len(current) > len(value):
                    current.remove(len(current) - 1)
                while len(current) < len(value):
                    current.add()
                for item, values in zip(current, value):
                    _write(values, item)
        elif prop.type == 'POINTER' and isinstance(current, bpy.types.PropertyGroup):
            _write(value, current)
        elif not prop.is_readonly:
            same = tuple(current) == tuple(value) if getattr(prop, 'is_array', False) else current == value
            if not same:
                setattr(target, key, value)


def _stack(ts):
    """The struct whose RNA ``layers`` collection is the set's stack: its layer store."""
    from .layer_store import ensure
    return ensure(ts)


def publish(ts):
    from .layer_stack import replaying, snapshot, replay
    if _syncing or replaying() or not getattr(ts, 'shared_stack_id', ''):
        return
    if any(not layer.id or len(layer.channels) != 8 for layer in ts.layers):
        return  # layer creation sends its completed recipe at the end
    owner = layer_owner(ts)
    recipe = [snapshot(layer) for layer in ts.layers]
    with syncing():
        for target in members(ts):
            if target.id == ts.id:
                continue
            with replay(target):
                _write({'layers': recipe}, _stack(target))
            target.shared_stack_owner = owner.id


def link(tiles):
    """Explicit, conflict-safe linking; tile pixels and resolutions are untouched."""
    from ..utils.ids import new_id
    tiles = list(tiles)
    if len(tiles) < 2:
        raise ValueError('A shared stack needs at least two UDIM tiles')
    if not tiles[0].udim_group or any(t.udim_group != tiles[0].udim_group for t in tiles):
        raise ValueError('Only tiles from the same UDIM group can share a stack')
    expected = _signature(tiles[0])
    conflicts = [str(t.udim_tile) for t in tiles[1:] if _signature(t) != expected]
    if conflicts:
        raise ValueError('Layer recipes differ on UDIM tiles ' + ', '.join(conflicts) + '; no stacks were changed')
    # Align IDs by position before synchronization; this preserves local images
    # rather than replacing every row because independent IDs differed.
    from .layer_stack import snapshot, replay
    from . import layer_stack
    owner = tiles[0]
    with syncing():
        recipe = [snapshot(layer) for layer in owner.layers]
        for tile in tiles[1:]:
            with replay(tile):
                def ids(source, target):
                    for name, value in source.items():
                        prop = target.bl_rna.properties.get(name)
                        if prop is None:
                            continue
                        if prop.type == 'COLLECTION':
                            for a, b in zip(value, getattr(target, name)):
                                ids(a, b)
                        elif isinstance(value, dict):
                            ids(value, getattr(target, name))
                        elif name in {'id', 'layer_id', 'parent_id', 'reference_layer', 'reference_mask'}:
                            setattr(target, name, value)
                for source, target in zip(recipe, tile.layers):
                    ids(source, target)
        key = new_id()
        for tile in tiles:
            tile.shared_stack_version = 1
            tile.shared_stack_id, tile.shared_stack_owner = key, owner.id
    publish(owner)
    return key


def make_independent(ts):
    """Detach recipes and editable stroke histories while retaining tile pixels."""
    from .layer_stack import replay
    from ..painting.strokes import copy_key
    publish(layer_owner(ts))
    with syncing():
        for tile in members(ts):
            with replay(tile):
                keys = {}
                for layer in tile.layers:
                    for source in (layer, *layer.masks):
                        key = source.stroke_key
                        if key:
                            if key not in keys:
                                keys[key] = copy_key(tile.id_data.open_painter, key, tile, tile)
                            source.stroke_key = keys[key]
                    layer.paint_link = ''
                tile.shared_stack_id = tile.shared_stack_owner = ''
                tile.shared_stack_version = 0


def removing(ts):
    others = [item for item in members(ts) if item.id != ts.id]
    if others and layer_owner(ts).id == ts.id:
        for item in others:
            item.shared_stack_owner = others[0].id
