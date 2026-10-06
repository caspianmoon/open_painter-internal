"""Which memfile undos restored only numeric values of a set (``cache.on_undo``).

A memfile undo reallocates every struct of the restored layer store, so each engine session is
built again, adopting its units one by one: the plan, every unit's structural signature and
owners, the reach map, the native description. When the undo restored only values that the
add-on edits as uniforms (the fields whose update callback is ``numeric``: an opacity, a colour,
a mask's levels), the structure each session compiled still holds, and its graph follows the
restored structs instead (``EngineGraph.follow_restored``), as a numeric edit is applied.

The set's stored properties are compared before (``undo_pre``) and after (``undo_post``) the
restore: the layer store's, read from the Palette that holds it, and the set's own. A field
counts as numeric by its declaration (``numeric`` callbacks of the channel, layer, mask and
effect property groups); bookkeeping the compiler never reads (``BOOKKEEPING``) may differ too.
Any other difference, an unknown field or a collection of another length rebuilds as before.
"""
import bpy

# Fields the engine's graph never reads, which evaluations and selections write: they may differ
# between two undo steps of the same structure.
BOOKKEEPING = {
    'OP_TextureSet': {'edit_serial', 'dirty', 'active_layer', 'active_assignment'},
    'OP_Output': {'dirty', 'revision'},
    'OP_Layer': {'dirty'},
    'OP_Channel': {'dirty'},
}

_shapes = {}   # property group class -> (its datablock fields, its nested groups)


def _shape(cls):
    """The fields of ``cls`` holding a datablock, and its nested groups (name, class, many)."""
    shape = _shapes.get(cls)
    if shape is None:
        ids, nested = [], []
        for base in reversed(getattr(cls, '__mro__', (cls,))):
            for name, field in getattr(base, '__annotations__', {}).items():
                kind = getattr(field, 'function', None)
                target = getattr(field, 'keywords', {}).get('type')
                if not isinstance(target, type):
                    continue
                if kind is bpy.props.PointerProperty and issubclass(target, bpy.types.ID):
                    ids.append(name)
                elif kind in (bpy.props.PointerProperty, bpy.props.CollectionProperty) and issubclass(
                        target, bpy.types.PropertyGroup):
                    nested.append((name, target, kind is bpy.props.CollectionProperty))
        shape = _shapes[cls] = (tuple(ids), tuple(nested))
    return shape


def _freeze(cls, data):
    """``data`` (a stored ``cls`` group, as ``to_dict`` reads it) with its datablock references as
    (type, session id): an undo invalidates the references themselves. Walks only the fields
    that can hold one, so a snapshot costs little more than ``to_dict``."""
    ids, nested = _shape(cls)
    for name in ids:
        value = data.get(name)
        if isinstance(value, bpy.types.ID):
            data[name] = ('ID', type(value).__name__, value.session_uid)
    for name, target, many in nested:
        value = data.get(name)
        if value:
            for item in (value if many else (value,)):
                if isinstance(item, dict):
                    _freeze(target, item)
    return data


def snapshot(scene, ts):
    """The set's stored properties: (each layer's, the rest of its layer store's, the set's own);
    None when unreadable. An undo mostly differs in one layer: the others compare equal at once
    (``numeric_only``)."""
    from ..properties.layer import OP_Layer
    from ..properties.texture_set import OP_LayerStore, OP_TextureSet
    try:
        palette = ts.layer_store
        system = palette.bl_system_properties_get() if palette is not None else None
        store = system.get('open_painter_layers') if system is not None else None
        scene_system = scene.bl_system_properties_get()
        project = scene_system.get('open_painter') if scene_system is not None else None
        if store is None or project is None:
            return None
        sets = project.get('texture_sets') or ()
        index = next((i for i, item in enumerate(ts.id_data.open_painter.texture_sets) if item.id == ts.id), None)
        if index is None or index >= len(sets):
            return None
        stored = store.to_dict()
        layers = stored.pop('layers', None) or []
        return (tuple(_freeze(OP_Layer, layer) for layer in layers), _freeze(OP_LayerStore, stored),
                _freeze(OP_TextureSet, sets[index].to_dict()))
    except (AttributeError, ReferenceError, TypeError, KeyError, ValueError):
        return None


def _numeric_callbacks():
    from ..properties import channel, effect, layer, mask
    return {channel.numeric, effect.numeric, layer.numeric, mask.numeric}


_MISSING = object()


def _field(cls, name):
    for base in getattr(cls, '__mro__', (cls,)):
        found = getattr(base, '__annotations__', {}).get(name)
        if found is not None:
            return found
    return None


def _same(cls, before, after, numeric):
    """Whether ``before`` and ``after`` (stored dicts of a ``cls`` group) differ only in numeric
    fields and bookkeeping."""
    if before == after:
        return True
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    allowed = BOOKKEEPING.get(cls.__name__, set())
    for name in before.keys() | after.keys():
        a, b = before.get(name, _MISSING), after.get(name, _MISSING)
        if a == b or name in allowed:
            continue
        field = _field(cls, name)
        if field is None:
            return False   # a field this walk cannot classify
        kind = getattr(field, 'function', None)
        nested = getattr(field, 'keywords', {}).get('type')
        if kind is bpy.props.CollectionProperty:
            a = [] if a is _MISSING else a
            b = [] if b is _MISSING else b
            if not isinstance(a, list) or not isinstance(b, list) or len(a) != len(b):
                return False
            if not all(_same(nested, x, y, numeric) for x, y in zip(a, b)):
                return False
        elif kind is bpy.props.PointerProperty and isinstance(nested, type) and issubclass(nested, bpy.types.PropertyGroup):
            if not _same(nested, {} if a is _MISSING else a, {} if b is _MISSING else b, numeric):
                return False
        elif getattr(field, 'keywords', {}).get('update') not in numeric:
            return False
    return True


def numeric_only(before, after):
    """Whether two snapshots of a set (``snapshot``) differ only in numeric values."""
    if before is None or after is None:
        return False
    from ..properties.layer import OP_Layer
    from ..properties.texture_set import OP_LayerStore, OP_TextureSet
    if len(before[0]) != len(after[0]):
        return False   # a layer added or removed
    numeric = _numeric_callbacks()
    pairs = [(OP_Layer, a, b) for a, b in zip(before[0], after[0])]
    pairs += [(OP_LayerStore, before[1], after[1]), (OP_TextureSet, before[2], after[2])]
    try:
        return all(a == b or _same(cls, a, b, numeric) for cls, a, b in pairs)
    except ReferenceError:
        return False   # a datablock reference no declared field holds: it went with the undo
