"""Ordered layers with stable hierarchy, duplication and cycle-safe parenting."""

from contextlib import contextmanager

from ..constants import CHANNELS, DEFAULTS, OWNER
from ..utils.ids import new_id

# Properties a getter/setter pair derives from another one: replaying them after
# their source would overwrite it (opacity_percent rounds opacity to 1/100).
DERIVED = {('OP_Channel', 'opacity_percent'), ('OP_Layer', 'opacity_percent'), ('OP_Layer', 'master_factor'),
           ('OP_Layer', 'master_scale'), ('OP_BakeCorrections', 'value'),
           ('OP_Influencer', 'distance_m'), ('OP_Influencer', 'bounds_m'),
           ('OP_Mask', 'chip_radius_m'), ('OP_Mask', 'period_m')}
DERIVED.update(('OP_Channel', 'fill_' + field) for field in
               ('projection', 'scale', 'rotation', 'offset', 'triplanar_hardness'))

# New fill layers author the PBR core; Height, AO, Emission and Opacity stay
# off until enabled, so a fresh layer touches nothing else. Folders keep every
# channel: they composite whatever their children carry.
FILL_DEFAULT_CHANNELS = frozenset({"BASE_COLOR", "ROUGHNESS", "METALLIC", "NORMAL"})

_replays = 0


def invalidate(ts, layers=None) -> None:
    """Mark the set changed. ``layers``: the only layers whose structure changed (an add,
    remove or move of these), so the engine's next structure check plans them and what
    they reach instead of every layer; None when anything may have changed."""
    ts.dirty = True
    ts.edit_serial += 1
    if layers is not None:
        from ..evaluation.shader_preview import invalidated
        invalidated(ts, layers)
    for out in ts.outputs:
        out.dirty = True
    for i, layer in enumerate(ts.layers):
        layer.order = i
        layer.dirty = True
    from .shared_stacks import publish
    publish(ts)


def add(ts, kind="FILL", name=None, parent_id=""):
    # Initializing RNA defaults fires change callbacks. An empty paint layer or
    # folder has no visual contribution, so preserve the existing output state.
    # The callbacks stay quiet while the layer is set up (each one would look up
    # its owner and notify the session); one notification below covers them.
    global _replays
    position = min(len(ts.layers), max(0, ts.active_layer + 1))
    was_dirty, serial = ts.dirty, ts.edit_serial
    output_dirty = {out.channel: out.dirty for out in ts.outputs}
    _replays += 1
    try:
        layer = ts.layers.add()
        layer.id, layer.type = new_id(), kind
        layer.name = name or {"FILL": "Fill Layer", "PAINT": "Paint Layer", "FOLDER": "Folder"}[kind]
        ensure_channels(layer)
        layer.parent_id = parent_id
        identifier = layer.id
        ts.layers.move(len(ts.layers) - 1, position)
        for i, item in enumerate(ts.layers):
            item.order = i
    finally:
        _replays -= 1
    ts.active_layer = position
    layer = next(item for item in ts.layers if item.id == identifier)
    affected = (
        {prop.channel for prop in layer.channels if prop.enabled} if kind == "FILL" else set()
    )
    ts.dirty = was_dirty or bool(affected)
    ts.edit_serial = serial + bool(affected)
    # The restored serial may be one the new channels' numeric callbacks just passed through.
    from ..evaluation.structure_memo import structural_edit
    structural_edit(ts)
    for out in ts.outputs:
        out.dirty = output_dirty.get(out.channel, False) or out.channel in affected
    from .shared_stacks import publish
    publish(ts)
    if not replaying():
        from ..evaluation.shader_preview import notify
        notify(layer, structural=True)   # the new layer, for the session's next structure check
    return layer


def ensure_channels(layer):
    """Also upgrades legacy folders, which stored no per-channel settings."""
    for channel in CHANNELS:
        if channel_for(layer, channel):
            continue
        prop = layer.channels.add()
        prop.channel, prop.layer_id = channel, layer.id
        prop.color, prop.value = DEFAULTS[channel], DEFAULTS[channel][0]
        prop.enabled = layer.type == "FOLDER" or (layer.type == "FILL" and channel in FILL_DEFAULT_CHANNELS)


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
    invalidate(ts, (layer.id,))


def release_image(image) -> None:
    """Let a managed image go without removing its datablock.

    Unpacked generated pixels do not survive a memfile undo of a removed
    datablock: Ctrl+Z after a rebake, a deleted layer or set, or a module
    Replace brought the image back blank. Dropping the fake user instead keeps
    the datablock (and its pixels) for an undo, while an image nothing binds
    any more is neither packed by the save handler nor written into the file:
    Blender purges it on the next save or reload. An image the artist picked
    (no owner tag) or wired into their own material (a user) stays either way.
    """
    if image is None or not image.get(OWNER):
        return
    image.use_fake_user = False


def release_images(layer) -> None:
    """Release the painted images a layer alone owns: a paint layer's canvases and
    its painted masks. Cache images and decal Empties have their own release."""
    if layer.type == "PAINT":
        for channel in layer.channels:
            release_image(channel.image)
    for mask in layer.masks:
        if mask.source == 'TEXT':
            release_image(mask.text.image)
        if mask.source == "PAINT":
            release_image(mask.image)


def remove(ts, index: int) -> None:
    layer = ts.layers[index]
    identifier = layer.id
    for child in ts.layers:
        if child.parent_id == layer.id:
            child.parent_id = layer.parent_id
    from ..evaluation.layer_cache import release_layer
    from ..painting.decals import release as release_decal
    release_layer(layer)
    release_decal(layer)
    release_images(layer)
    ts.layers.remove(index)
    ts.active_layer = min(index, len(ts.layers) - 1)
    invalidate(ts, (identifier,))


def move(ts, index: int, direction: int) -> None:
    target = max(0, min(len(ts.layers) - 1, index + direction))
    identifier = ts.layers[index].id
    ts.layers.move(index, target)
    ts.active_layer = target
    invalidate(ts, (identifier,))


def snapshot(source) -> dict:
    """Detach RNA values before mutating a Blender collection."""
    import bpy

    result = {}
    kind = source.bl_rna.identifier
    for prop in source.bl_rna.properties:
        key = prop.identifier
        if key == "rna_type" or (kind, key) in DERIVED:
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
    kind = target.bl_rna.identifier
    if kind == "OP_MeshSelector":
        # A snapshot taken before angles became radians (a file the migration
        # has not reached yet) would clamp 45 to pi and cover every face.
        from ..properties.mesh_selector import degrees_to_radians
        data = degrees_to_radians(data)
    for key, value in data.items():
        if (kind, key) in DERIVED:
            continue   # snapshots written before 0.9.7 still carry it
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


def replaying() -> bool:
    """Whether snapshots are being replayed: property callbacks skip their set-wide walks."""
    return _replays > 0


@contextmanager
def replay(ts, created_only=False):
    """Restore snapshots into ``ts`` with one invalidation at the end instead of one per property.

    Every value ``restore`` writes fires its update callback, and each callback
    walks the whole set (owner lookup by id or pointer, dependent channels, the
    shader session's owner scan): copying N layers into a stack of L costs
    N x hundreds x L reads, seconds for a folder and minutes for a set. The
    snapshot already holds everything those callbacks derive (mirrored names,
    physical texture sizes, cache rows), so the walks are skipped while a replay
    is open and the set is invalidated, physically resolved and notified once.
    ``created_only``: the replay writes nothing structural but the layers it adds (a
    smart material), which the engine's next structure check plans alone.
    """
    global _replays
    from ..evaluation.shader_preview import authoring_batch, notify
    existing = {layer.id for layer in ts.layers} if created_only else None
    with authoring_batch(ts):
        _replays += 1
        try:
            yield
        finally:
            _replays -= 1
            created = None if existing is None else [l.id for l in ts.layers if l.id not in existing]
            invalidate(ts, created)
        from ..evaluation import physical
        physical.sync(ts, created)   # a replay that only added layers resolves theirs
        if created:
            notify(next(l for l in ts.layers if l.id == created[0]), structural=True)
        else:
            notify(ts, structural=True)


def copy_ids(sources):
    """New ids for copied layer snapshots: ``({old id: new id}, {root id: new instance token})``.

    Plain ids become fresh uuids. A module instance among the copies keeps the
    plain part of its ids under a fresh token (``old.<id>`` -> ``new.<id>``), and
    the copy's root takes that token, so the copy is a second instance the
    module's base snapshot still matches: Update merges it instead of treating
    every module layer as locally deleted and rewriting the copy's ids. An
    origin folder (saved from, token '') is namespaced the same way. A catalog
    recipe's nested instance token is derived from the recipe's, as the update
    that rebuilds the recipe expects.
    """
    from ..smart_materials.modules import namespaced
    by_id = {item['id']: item for item in sources}

    def catalog(item):
        source = item.get('module_source', '')
        return source[len('catalog:'):] if source.startswith('catalog:') else ''

    def enclosing(item):
        cursor = item.get('parent_id', '')
        while cursor in by_id:
            if roots.get(cursor):
                return by_id[cursor]
            cursor = by_id[cursor].get('parent_id', '')
        return None

    def depth(item):
        count, cursor = 0, item.get('parent_id', '')
        while cursor in by_id:
            count, cursor = count + 1, by_id[cursor].get('parent_id', '')
        return count

    roots = {item['id']: item for item in sources
             if item['type'] == 'FOLDER' and item.get('module_id') and not item.get('module_detached')}
    tokens = {}
    for item in sorted(roots.values(), key=depth):   # an outer token before the ones derived from it
        outer = enclosing(item)
        token = new_id()[:8]
        if outer is not None and catalog(outer) and catalog(item):
            from ..smart_materials.composer import nested_token
            if item['module_instance'] == nested_token(catalog(outer), outer['module_instance'], catalog(item)):
                token = nested_token(catalog(outer), tokens[outer['id']], catalog(item))
        tokens[item['id']] = token
    prefixes = sorted(((item['module_instance'] + '.', tokens[identifier] + '.')
                       for identifier, item in roots.items() if item['module_instance']),
                      key=lambda pair: -len(pair[0]))

    def renamed(identifier, item):
        for old, new in prefixes:
            if identifier.startswith(old):
                return new + identifier[len(old):]
        root = item if item['id'] in roots else enclosing(item)
        if root is not None and not root['module_instance'] and not namespaced(identifier):
            return tokens[root['id']] + '.' + identifier
        return new_id()   # an artist addition inside an instance keeps a plain id

    ids = {}
    for item in sources:
        ids[item['id']] = renamed(item['id'], item)
        for mask in item['masks']:
            ids[mask['id']] = renamed(mask['id'], item)
        for channel in item['channels']:
            for effect in channel['effects']:
                if effect['id']:
                    ids[effect['id']] = renamed(effect['id'], item)
    return ids, tokens


def duplicate(ts, index):
    root_id = ts.layers[index].id
    ids = {root_id}
    while True:
        expanded = ids | {item.id for item in ts.layers if item.parent_id in ids}
        if expanded == ids:
            break
        ids = expanded
    sources = [snapshot(item) for item in ts.layers if item.id in ids]
    ids, tokens = copy_ids(sources)
    mapping = {item["id"]: ids[item["id"]] for item in sources}
    # Anchors outside the copied subtree keep their original ids, like layer references.
    from ..evaluation.anchors import derived_mapping
    anchor_mapping = {**ids, **derived_mapping(mapping)}
    from .image_manager import clone
    with replay(ts):
        for source in sources:
            target = ts.layers.add()
            restore(source, target)
            target.id = mapping[source["id"]]
            target.name += " Copy"
            target.parent_id = mapping.get(source["parent_id"], source["parent_id"])
            if source["id"] in tokens:
                target.module_instance = tokens[source["id"]]
            for row in target.cache:
                # A cached image belongs to one layer; the copy rebuilds its own.
                row.image = None
                row.provenance = ''
            if getattr(target, 'stroke_key', ''):
                # The copy keeps the strokes it shows (those this set may render), under a
                # key of its own scoped to this set: painting on either layer afterwards
                # must not reach the other, and the copy widens no stroke's reach.
                from ..painting.strokes import copy_key
                target.stroke_key = copy_key(ts.id_data.open_painter, target.stroke_key, ts)
            for mask in target.masks:
                mask.id = ids[mask.id]
                if mask.source == 'STROKES' and mask.stroke_key:
                    from ..painting.strokes import copy_key
                    mask.stroke_key = copy_key(ts.id_data.open_painter, mask.stroke_key, ts)
                if mask.parent_id:   # mask group membership (0.9.3) follows the new ids
                    mask.parent_id = ids.get(mask.parent_id, '')
                if mask.source == 'REFERENCE':
                    mask.reference_layer = mapping.get(mask.reference_layer,mask.reference_layer)
                    mask.reference_mask = ids.get(mask.reference_mask,mask.reference_mask)
                for field in ('reference_anchor', 'detail_height_anchor', 'detail_normal_anchor'):
                    value = getattr(mask, field)
                    if value:
                        setattr(mask, field, anchor_mapping.get(value, value))
                if mask.source == "PAINT" and mask.image:
                    mask.image = clone(mask.image)
            for channel in target.channels:
                channel.layer_id = target.id
                if channel.anchor:
                    channel.anchor = anchor_mapping.get(channel.anchor, channel.anchor)
                for effect in channel.effects:
                    if effect.id:
                        effect.id = ids.get(effect.id, effect.id)
                if target.type == "PAINT" and channel.image:
                    channel.image = clone(channel.image)
        # Move the copied subtree immediately above the original root. Stable IDs
        # preserve parent links even when children precede their folder in storage.
        for offset, source in enumerate(sources):
            source_index = next(i for i, item in enumerate(ts.layers) if item.id == mapping[source["id"]])
            ts.layers.move(source_index, index + 1 + offset)
        ts.active_layer = next(i for i, item in enumerate(ts.layers) if item.id == mapping[root_id])
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
            release_images(ts.layers[i])
            ts.layers.remove(i)
    ts.active_layer = min(ts.active_layer, max(0, len(ts.layers) - 1))
    invalidate(ts, identifiers)


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
    invalidate(ts, roots)


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
    invalidate(ts, (identifier,))
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
