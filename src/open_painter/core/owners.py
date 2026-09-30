"""The texture set and layer owning an RNA struct, found by address without a project walk.

Property callbacks run on every slider step; scanning every layer, channel, effect
and mask of every set for the edited struct made each step linear in the stack.
The index stores positions and ids, never RNA references: a collection
reallocation (adding or removing a layer) invalidates Python struct references.
Every hit is verified against the struct's current members. A miss (a layer added, or
the layers above an insertion shifted) is resolved through the struct's own RNA path,
which names its layer, and that layer alone is indexed again; only a struct the path
does not place rebuilds the scene's index.
"""
import re

_index = {}   # scene address -> {struct address: (set position, set id, layer position or None, layer id)}


def members(layer):
    """Every struct whose edits a layer answers for."""
    yield layer
    for prop in layer.channels:
        yield prop
        yield from prop.effects
    for mask in layer.masks:
        yield mask
        yield mask.effect
        yield mask.influence
        yield mask.mesh
    yield from layer.cache


def _build(scene):
    project = getattr(scene, 'open_painter', None)
    entries = {}
    if project is not None:
        for s, ts in enumerate(project.texture_sets):
            entries[ts.as_pointer()] = (s, ts.id, None, None)
            for i, layer in enumerate(ts.layers):
                for item in members(layer):
                    entries.setdefault(item.as_pointer(), (s, ts.id, i, layer.id))
    _index[scene.as_pointer()] = entries
    return entries


def _resolve(project, pointer, entry):
    s, set_id, position, layer_id = entry
    sets = project.texture_sets
    if s >= len(sets):
        return None
    ts = sets[s]
    if ts.id != set_id:
        return None
    if position is None:
        return (ts, None) if ts.as_pointer() == pointer else None
    layers = ts.layers
    if position >= len(layers):
        return None
    layer = layers[position]
    if layer.id != layer_id or not any(item.as_pointer() == pointer for item in members(layer)):
        return None
    return ts, layer


def locate(owner):
    """``(ts, layer)`` holding ``owner`` (``layer`` is None for the set itself), or None.
    A layer's structs live in its set's store (``core.layer_store``), not on the Scene."""
    from .layer_store import scene_of
    try:
        scene = scene_of(owner)
    except AttributeError:
        return None
    project = getattr(scene, 'open_painter', None)
    if project is None:
        return None
    pointer = owner.as_pointer()
    entry = _index.get(scene.as_pointer(), {}).get(pointer)
    if entry is not None:
        found = _resolve(project, pointer, entry)
        if found is not None:
            return found
    found = _by_path(scene, project, owner, pointer)
    if found is not None:
        return found
    entry = _build(scene).get(pointer)
    return _resolve(project, pointer, entry) if entry is not None else None


_LAYER = re.compile(r'\.layers\[(\d+)\]')


def _by_path(scene, project, owner, pointer):
    """A struct of a layer store the index missed: its layer from its RNA path (Blender finds
    that in C), verified, and that layer's members indexed again."""
    from .layer_store import ATTRIBUTE, owner as store_owner
    import bpy
    palette = owner.id_data
    if not isinstance(palette, bpy.types.Palette):
        return None
    try:
        path = owner.path_from_id()
    except ValueError:
        return None
    match = _LAYER.match(path, len(ATTRIBUTE)) if path.startswith(ATTRIBUTE + '.') else None
    ts = store_owner(palette)[1]
    if match is None or ts is None:
        return None
    position = int(match.group(1))
    layers = ts.layers
    if position >= len(layers):
        return None
    layer = layers[position]
    entries = _index.setdefault(scene.as_pointer(), {})
    s = next((i for i, item in enumerate(project.texture_sets) if item.as_pointer() == ts.as_pointer()), None)
    if s is None:
        return None
    found = False
    for item in members(layer):
        address = item.as_pointer()
        entries[address] = (s, ts.id, position, layer.id)
        found = found or address == pointer
    return (ts, layer) if found else None


def clear():
    """Forget every index (file load, undo, unregister): addresses are not stable across them."""
    _index.clear()
