"""Ordered layers with stable hierarchy, duplication and cycle-safe parenting."""

from ..constants import CHANNELS, DEFAULTS
from ..utils.ids import new_id


def invalidate(ts) -> None:
    ts.dirty = True
    ts.edit_serial += 1
    for out in ts.outputs:
        out.dirty = True
    for i, layer in enumerate(ts.layers):
        layer.order = i
        layer.dirty = True


def add(ts, kind="FILL", name=None, parent_id=""):
    # Initializing RNA defaults fires change callbacks. An empty paint layer or
    # folder has no visual contribution, so preserve the existing output state.
    position = min(len(ts.layers), max(0, ts.active_layer + 1))
    was_dirty, serial = ts.dirty, ts.edit_serial
    output_dirty = {out.channel: out.dirty for out in ts.outputs}
    layer = ts.layers.add()
    layer.id, layer.type = new_id(), kind
    layer.name = name or {"FILL": "Fill Layer", "PAINT": "Paint Layer", "FOLDER": "Folder"}[kind]
    ensure_channels(layer)
    layer.parent_id = parent_id
    identifier = layer.id
    ts.layers.move(len(ts.layers) - 1, position)
    for i, item in enumerate(ts.layers):
        item.order = i
    ts.active_layer = position
    layer = next(item for item in ts.layers if item.id == identifier)
    affected = (
        {prop.channel for prop in layer.channels if prop.enabled} if kind == "FILL" else set()
    )
    ts.dirty = was_dirty or bool(affected)
    ts.edit_serial = serial + bool(affected)
    for out in ts.outputs:
        out.dirty = output_dirty.get(out.channel, False) or out.channel in affected
    return layer


def ensure_channels(layer):
    """Also upgrades legacy folders, which stored no per-channel settings."""
    for channel in CHANNELS:
        if channel_for(layer, channel):
            continue
        prop = layer.channels.add()
        prop.channel, prop.layer_id = channel, layer.id
        prop.color, prop.value = DEFAULTS[channel], DEFAULTS[channel][0]
        prop.enabled = layer.type == "FOLDER" or (
            layer.type == "FILL" and channel in {"BASE_COLOR", "ROUGHNESS", "METALLIC"}
        )


def active(ts):
    return ts.layers[ts.active_layer] if 0 <= ts.active_layer < len(ts.layers) else None


def channel_for(layer, channel):
    return next((c for c in layer.channels if c.channel == channel), None)


def parent(ts, layer, folder_id: str) -> None:
    by_id = {item.id: item for item in ts.layers}
    cursor = folder_id
    seen = {layer.id}
    while cursor:
        if cursor in seen:
            raise ValueError("A folder cannot contain itself or its ancestors.")
        seen.add(cursor)
        folder = by_id.get(cursor)
        if folder is None or folder.type != "FOLDER":
            raise ValueError("Parent folder is missing.")
        cursor = folder.parent_id
    layer.parent_id = folder_id
    invalidate(ts)


def remove(ts, index: int) -> None:
    layer = ts.layers[index]
    for child in ts.layers:
        if child.parent_id == layer.id:
            child.parent_id = layer.parent_id
    from ..evaluation.layer_cache import release_layer
    from ..painting.decals import release as release_decal
    release_layer(layer)
    release_decal(layer)
    ts.layers.remove(index)
    ts.active_layer = min(index, len(ts.layers) - 1)
    invalidate(ts)


def move(ts, index: int, direction: int) -> None:
    target = max(0, min(len(ts.layers) - 1, index + direction))
    ts.layers.move(index, target)
    ts.active_layer = target
    invalidate(ts)


def snapshot(source) -> dict:
    """Detach RNA values before mutating a Blender collection."""
    import bpy

    result = {}
    for prop in source.bl_rna.properties:
        key = prop.identifier
        if key == "rna_type":
            continue
        value = getattr(source, key)
        if prop.is_readonly and prop.type != "COLLECTION" and not (
            prop.type == "POINTER" and isinstance(value,bpy.types.PropertyGroup)
        ):
            continue
        if prop.type == "COLLECTION":
            result[key] = [snapshot(item) for item in value]
        elif prop.type == "POINTER" and isinstance(value, bpy.types.PropertyGroup):
            result[key] = snapshot(value)
        elif getattr(prop, "is_array", False):
            result[key] = list(value)
        else:
            result[key] = value
    return result


def restore(data: dict, target) -> None:
    for key, value in data.items():
        prop = target.bl_rna.properties[key]
        if prop.type == "COLLECTION":
            collection = getattr(target, key)
            if len(collection) and value:
                # An update callback replayed above may have seeded rows
                # (cache_mode creates the cache rows); the snapshot wins.
                collection.clear()
            for item in value:
                restore(item, collection.add())
        elif prop.type == "POINTER" and isinstance(value, dict):
            restore(value, getattr(target, key))
        else:
            setattr(target, key, value)


def copy_properties(source, target):
    restore(snapshot(source), target)


def duplicate(ts, index):
    root_id = ts.layers[index].id
    ids = {root_id}
    while True:
        expanded = ids | {item.id for item in ts.layers if item.parent_id in ids}
        if expanded == ids:
            break
        ids = expanded
    sources = [snapshot(item) for item in ts.layers if item.id in ids]
    mapping = {item["id"]: new_id() for item in sources}
    mask_mapping = {m['id']: new_id() for item in sources for m in item['masks']}
    effect_mapping = {e['id']: new_id() for item in sources for c in item['channels'] for e in c['effects'] if e['id']}
    # Anchors outside the copied subtree keep their original ids, like layer references.
    from ..evaluation.anchors import derived_mapping
    anchor_mapping = {**mask_mapping, **effect_mapping, **derived_mapping(mapping)}
    for source in sources:
        target = ts.layers.add()
        restore(source, target)
        target.id = mapping[source["id"]]
        target.name += " Copy"
        target.parent_id = mapping.get(source["parent_id"], source["parent_id"])
        for row in target.cache:
            # A cached image belongs to one layer; the copy rebuilds its own.
            row.image = None
            row.provenance = ''
        for mask in target.masks:
            mask.id = mask_mapping[mask.id]
            if mask.parent_id:   # mask group membership (0.9.3) follows the new ids
                mask.parent_id = mask_mapping.get(mask.parent_id, '')
            if mask.source == 'REFERENCE':
                mask.reference_layer = mapping.get(mask.reference_layer,mask.reference_layer)
                mask.reference_mask = mask_mapping.get(mask.reference_mask,mask.reference_mask)
            for field in ('reference_anchor', 'detail_height_anchor', 'detail_normal_anchor'):
                value = getattr(mask, field)
                if value:
                    setattr(mask, field, anchor_mapping.get(value, value))
            if mask.source == "PAINT" and mask.image:
                mask.image = mask.image.copy()
        for channel in target.channels:
            channel.layer_id = target.id
            if channel.anchor:
                channel.anchor = anchor_mapping.get(channel.anchor, channel.anchor)
            for effect in channel.effects:
                if effect.id:
                    effect.id = effect_mapping.get(effect.id, effect.id)
            if target.type == "PAINT" and channel.image:
                channel.image = channel.image.copy()
    # Move the copied subtree immediately above the original root. Stable IDs
    # preserve parent links even when children precede their folder in storage.
    for offset, source in enumerate(sources):
        source_index = next(i for i, item in enumerate(ts.layers) if item.id == mapping[source["id"]])
        ts.layers.move(source_index, index + 1 + offset)
    ts.active_layer = next(i for i, item in enumerate(ts.layers) if item.id == mapping[root_id])
    invalidate(ts)
    return ts.layers[ts.active_layer]


def visible_rows(ts):
    """Top-to-bottom hierarchy, with descendants contiguous under their group."""
    children = {}
    for index, layer in enumerate(ts.layers):
        children.setdefault(layer.parent_id, []).append((index, layer))
    rows = []

    def visit(parent, depth, ancestry):
        for index, layer in reversed(children.get(parent, [])):
            if layer.id in ancestry:
                continue
            rows.append((index, depth))
            if layer.type == "FOLDER" and layer.expanded:
                visit(layer.id, depth + 1, ancestry | {layer.id})

    visit("", 0, set())
    return rows


def selection_roots(ts, identifiers):
    by_id = {item.id: item for item in ts.layers}
    roots = []
    for layer in ts.layers:
        if layer.id not in identifiers:
            continue
        cursor = layer.parent_id
        seen = set()
        while cursor and cursor not in identifiers and cursor not in seen:
            seen.add(cursor)
            cursor = by_id[cursor].parent_id if cursor in by_id else ""
        if cursor not in identifiers:
            roots.append(layer.id)
    return roots


def reparent_many(ts, identifiers, folder_id):
    roots = selection_roots(ts, set(identifiers))
    by_id = {item.id: item for item in ts.layers}
    cursor, seen = folder_id, set()
    while cursor:
        if cursor in roots or cursor in seen:
            raise ValueError("A group cannot be moved inside itself or its descendants.")
        seen.add(cursor)
        folder = by_id.get(cursor)
        if folder is None or folder.type != "FOLDER":
            raise ValueError("Target group is missing.")
        cursor = folder.parent_id
    for identifier in roots:
        by_id[identifier].parent_id = folder_id
    if folder_id:
        by_id[folder_id].expanded = True
    invalidate(ts)


def group(ts, identifiers, name="Group"):
    roots = selection_roots(ts, set(identifiers))
    parents = {item.parent_id for item in ts.layers if item.id in roots}
    parent_id = next(iter(parents)) if len(parents) == 1 else ""
    position = max(
        (i + 1 for i, item in enumerate(ts.layers) if item.id in roots), default=min(len(ts.layers), max(0, ts.active_layer + 1))
    )
    folder = add(ts, "FOLDER", name)
    folder_id = folder.id
    folder.parent_id = parent_id
    folder.group_mode = "PASSTHROUGH"
    reparent_many(ts, roots, folder_id)
    ts.layers.move(next(i for i, item in enumerate(ts.layers) if item.id == folder_id), position)
    ts.active_layer = position
    invalidate(ts)
    return next(item for item in ts.layers if item.id == folder_id)


def descendants(ts, identifier):
    """Ids of every layer nested under ``identifier``, at any depth."""
    identifiers = {identifier}
    while True:
        expanded = identifiers | {item.id for item in ts.layers if item.parent_id in identifiers}
        if expanded == identifiers:
            return identifiers - {identifier}
        identifiers = expanded


def remove_tree(ts, identifier):
    identifiers = descendants(ts, identifier) | {identifier}
    from ..evaluation.layer_cache import release_layer
    from ..painting.decals import release as release_decal
    for i in reversed(range(len(ts.layers))):
        if ts.layers[i].id in identifiers:
            release_layer(ts.layers[i])
            release_decal(ts.layers[i])
            ts.layers.remove(i)
    ts.active_layer = min(ts.active_layer, max(0, len(ts.layers) - 1))
    invalidate(ts)


def move_above(ts, identifiers, target_id):
    roots = selection_roots(ts, set(identifiers))
    if target_id in roots:
        return
    anchor = target_id
    for identifier in roots:
        source = next(i for i, item in enumerate(ts.layers) if item.id == identifier)
        target = next(i for i, item in enumerate(ts.layers) if item.id == anchor)
        ts.layers.move(source, target if source < target else target + 1)
        anchor = identifier
    invalidate(ts)


def index_of(ts, identifier):
    return next(i for i, item in enumerate(ts.layers) if item.id == identifier)


def drop_targets(ts, identifier):
    """Every place a dragged layer (with its subtree) can land in the stack.

    One entry per gap between the remaining visible rows, top to bottom. A gap
    lists its candidate depths, deepest first, as ``(depth, parent_id, kind,
    anchor_id)``: directly ABOVE or BELOW a visible row, the FIRST_CHILD of an
    expanded folder, or the END of an otherwise empty stack. The candidates
    depend only on the other rows, so they stay valid while the dragged layer
    is repositioned among them.
    """
    excluded = descendants(ts, identifier) | {identifier}
    by_id = {item.id: item for item in ts.layers}
    rows = [(ts.layers[index], depth) for index, depth in visible_rows(ts)
            if ts.layers[index].id not in excluded]
    gaps = []
    for gap in range(len(rows) + 1):
        candidates = []
        following = rows[gap] if gap < len(rows) else None
        if gap:
            layer, depth = rows[gap - 1]
            if layer.type == "FOLDER" and layer.expanded:
                candidates.append((depth + 1, layer.id, "FIRST_CHILD", layer.id))
            # Below the preceding row, or below any of its ancestors down to
            # the depth of the row that follows: the closing edge of a folder
            # offers every nesting level it leaves open.
            floor = following[1] if following else 0
            while layer is not None and depth >= floor:
                candidates.append((depth, layer.parent_id, "BELOW", layer.id))
                layer, depth = by_id.get(layer.parent_id), depth - 1
        elif following:
            candidates.append((following[1], following[0].parent_id, "ABOVE", following[0].id))
        else:
            candidates.append((0, "", "END", ""))
        gaps.append(candidates)
    return gaps


def slot(ts, identifier):
    """The gap and depth ``identifier`` occupies now, in ``drop_targets`` terms."""
    for gap, (index, depth) in enumerate(visible_rows(ts)):
        if ts.layers[index].id == identifier:
            return gap, depth
    raise ValueError("The layer is not visible.")


def place(ts, identifier, target):
    """Move ``identifier`` (and its subtree) to a ``drop_targets`` candidate."""
    _, parent_id, kind, anchor_id = target
    layer = next(item for item in ts.layers if item.id == identifier)
    if layer.parent_id != parent_id:
        parent(ts, layer, parent_id)
    source = index_of(ts, identifier)
    if kind == "FIRST_CHILD":
        siblings = [i for i, item in enumerate(ts.layers)
                    if item.parent_id == anchor_id and item.id != identifier]
        if siblings:
            kind, anchor_id = "ABOVE", ts.layers[max(siblings)].id
        else:
            kind = "BELOW"
    if kind == "END":
        ts.layers.move(source, 0)
    else:
        # Layers are stored bottom-first: above a row means a higher index.
        anchor = index_of(ts, anchor_id)
        if kind == "ABOVE":
            ts.layers.move(source, anchor if source < anchor else anchor + 1)
        else:
            ts.layers.move(source, anchor - 1 if source < anchor else anchor)
    invalidate(ts)
    ts.active_layer = index_of(ts, identifier)
    return ts.active_layer


def arrangement(ts):
    """Order and parents, enough to put a stack back after a cancelled move."""
    return [(item.id, item.parent_id) for item in ts.layers]


def restore_arrangement(ts, saved):
    by_id = {item.id: item for item in ts.layers}
    for identifier, parent_id in saved:
        if identifier in by_id and by_id[identifier].parent_id != parent_id:
            by_id[identifier].parent_id = parent_id
    for target, (identifier, _) in enumerate(saved):
        if identifier in by_id:
            source = index_of(ts, identifier)
            if source != target:
                ts.layers.move(source, target)
    invalidate(ts)
