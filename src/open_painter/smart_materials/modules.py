"""Material modules: a folder subtree with an identity, a version and instances.

A module document is a smart material whose ``module`` block names the root
folder; its layer ids are the module's *own* id space. Inserting a module gives
the instance a namespace token so that every owned id becomes ``<token>.<id>``:
two instances never collide, an instance's ids map back to the module's ids by
prefix stripping, and re-inserting a newer version yields the same instance ids,
so references from outside into the instance survive an update.

Updates are three-way merges (``core.material_inheritance.preview``) between the
base snapshot of the version an instance was inserted from, the new version and
the instance as edited; local edits win conflicts and every conflict is
reported. The base snapshot lives in a ``bpy.data.texts`` datablock per
(module, version), as JSON without pixels.
"""
import json
from pathlib import Path

from ..evaluation.anchors import derived_mapping

# Stroke keys and paint links are instance state like the cache: the loader keys an
# imported stroke layer or Vector Strokes mask afresh with the strokes it restores, so
# merging the document's own key back over it would alias the origin's strokes (or render
# nothing) and orphan the instance's; ``_apply_update`` carries the local ones over.
VOLATILE = frozenset({'dirty', 'order', 'active_mask', 'active_effect', 'expanded', 'image_name',
                      'detail_image_name', 'revision', 'module_id', 'module_version', 'module_source',
                      'module_instance', 'module_name', 'module_detached', 'layer_id',
                      'bypass', 'cache_mode', 'cache', 'stroke_key', 'paint_link', 'opacity_percent',
                      'master_factor', 'master_scale'})
BASE_TEXT_PREFIX = 'OP Module Base · '
DIRECTOR_PREFIX = 'md.'   # Material Director ids (authoring.commands.allocate) are plain, dot and all
ASSET_HASH = 'open_painter_asset_hash'   # on decoded module images: sha256 of the EXR bytes


def owned_ids(layers):
    """Every id a list of layer dicts defines: layers, masks and effects."""
    found = set()
    for layer in layers:
        found.add(layer['id'])
        for mask in layer.get('masks', []):
            if mask.get('id'):
                found.add(mask['id'])
        for channel in layer.get('channels', []):
            for effect in channel.get('effects', []):
                if effect.get('id'):
                    found.add(effect['id'])
    return found


def namespaced(identifier):
    """Whether an id already carries an instance token (a nested module instance's)."""
    return '.' in identifier and not identifier.startswith(DIRECTOR_PREFIX)


def namespace_mapping(layers, token, keep=()):
    """{own id: instance id} for every owned id (derived anchor ids included).

    Ids that already carry a namespace (a nested module instance's) and ids in
    ``keep`` (added inside the instance by the artist) are left alone.
    """
    if not token:
        return {}
    return derived_mapping({identifier: f'{token}.{identifier}' for identifier in owned_ids(layers)
                            if not namespaced(identifier) and identifier not in keep})


def denamespace_mapping(layers, token):
    prefix = token + '.'
    plain = {identifier: identifier[len(prefix):] for identifier in owned_ids(layers)
             if identifier.startswith(prefix)}
    return derived_mapping(plain)


def plain_id(identifier, token):
    """An instance id without its namespace token (identity for ids outside the module)."""
    prefix = token + '.' if token else ''
    return identifier[len(prefix):] if prefix and identifier.startswith(prefix) else identifier


def remap(layers, mapping):
    """Copies of layer dicts with every id and reference passed through ``mapping``.

    Ids outside the mapping (references to anchors elsewhere in the set) stay
    verbatim, like layer_stack.duplicate keeps anchors outside a copied subtree.
    """
    def value(identifier):
        return mapping.get(identifier, identifier) if identifier else identifier

    result = []
    for source in layers:
        layer = dict(source)
        layer['id'] = value(layer['id'])
        layer['parent_id'] = value(layer.get('parent_id', ''))
        masks = []
        for item in layer.get('masks', []):
            mask = dict(item)
            mask['id'] = value(mask.get('id', ''))
            for field in ('parent_id', 'reference_layer', 'reference_mask', 'reference_anchor',
                          'detail_height_anchor', 'detail_normal_anchor'):
                if mask.get(field):
                    mask[field] = value(mask[field])
            masks.append(mask)
        layer['masks'] = masks
        channels = []
        for item in layer.get('channels', []):
            channel = dict(item)
            channel['layer_id'] = layer['id']
            if channel.get('anchor'):
                channel['anchor'] = value(channel['anchor'])
            channel['effects'] = [dict(effect, id=value(effect.get('id', '')))
                                  for effect in channel.get('effects', [])]
            channels.append(channel)
        layer['channels'] = channels
        result.append(layer)
    return result


def subtree(ts, root_id):
    """Layers of a folder subtree in stack (collection) order, root first among its level."""
    ids = {root_id}
    while True:
        expanded = ids | {layer.id for layer in ts.layers if layer.parent_id in ids}
        if expanded == ids:
            break
        ids = expanded
    return [layer for layer in ts.layers if layer.id in ids]


PAINT_MARKER = {'__paint__': True}


def comparable(layers):
    """Layer dicts stripped of UI state, with datablocks as markers, for three-way merging.

    Images decoded from a module document compare by content hash, so an asset
    the new version left alone equals the base whatever its datablock is named;
    other datablocks (images, decal Empties, mesh selector and influencer
    pointers, node groups) compare by name. Painted images (paint masks, paint
    layer channels) are replaced by a marker: pixels are always the instance's
    own and never merge (see ``_keep_paint``).
    """
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key not in VOLATILE}
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, tuple):
            return list(value)
        if hasattr(value, 'bl_rna') and hasattr(value, 'id_type'):
            if value.id_type == 'IMAGE':
                if value.get(ASSET_HASH):
                    return {'__asset__': value[ASSET_HASH], 'colorspace': value.colorspace_settings.name}
                return {'__image__': value.name}
            return {'__datablock__': value.id_type, 'name': value.name}
        return value
    result = [clean(layer) for layer in layers]
    for layer in result:
        for channel in layer.get('channels', []):
            if channel.get('mesh_map'):
                channel['image'] = None  # Recipient bakes are bindings, never authored overrides.
        for mask in layer.get('masks', []):
            if mask.get('source') == 'PAINT' and mask.get('image'):
                mask['image'] = dict(PAINT_MARKER)
        if layer.get('type') == 'PAINT':
            for channel in layer.get('channels', []):
                if channel.get('image'):
                    channel['image'] = dict(PAINT_MARKER)
    return result


POOLS = {'IMAGE': 'images', 'OBJECT': 'objects', 'COLLECTION': 'collections', 'MATERIAL': 'materials',
         'NODETREE': 'node_groups'}


def asset_image(digest, colorspace):
    """A packed image decoded from a module asset with these bytes and colorspace, if any."""
    import bpy
    for image in bpy.data.images:
        if image.get(ASSET_HASH) == digest and image.colorspace_settings.name == colorspace \
                and image.packed_file is not None:
            return image
    return None


def resolve_images(layers):
    """Turn ``comparable`` datablock markers back into datablocks (missing → None)."""
    import bpy

    def restore(value):
        if isinstance(value, dict):
            if set(value) == {'__image__'}:
                return bpy.data.images.get(value['__image__'])
            if set(value) == {'__asset__', 'colorspace'}:
                return asset_image(value['__asset__'], value['colorspace'])
            if set(value) == {'__datablock__', 'name'}:
                pool = getattr(bpy.data, POOLS.get(value['__datablock__'], ''), None)
                return pool.get(value['name']) if pool is not None else None
            return {key: restore(item) for key, item in value.items()}
        if isinstance(value, list):
            return [restore(item) for item in value]
        return value
    return [restore(layer) for layer in layers]


def merge(base, parent, local):
    """Three-way merge of layer lists; returns (merged layer dicts, conflict paths)."""
    from ..core.material_inheritance import preview
    merged, conflicts = preview({'layers': base}, {'layers': parent}, {'layers': local})
    return merged.get('layers', []), conflicts


# ---- base snapshots -------------------------------------------------------------

def base_name(module_id, version):
    return f'{BASE_TEXT_PREFIX}{module_id} · v{version}'


def write_base(module_id, version, layers):
    import bpy
    from ..constants import OWNER
    name = base_name(module_id, version)
    text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    write_json(text, {'module': module_id, 'version': version, 'layers': layers})
    text.use_fake_user = True
    text[OWNER] = True
    return text


def write_json(text, data):
    """Store ``data`` in a Text datablock as JSON, one value per line: Blender inserts text in
    time quadratic in the line's length (a module base on one line took 140 ms to write, split
    into lines 1 ms). Content it already holds is not written again."""
    payload = json.dumps(data, indent=0)
    if text.as_string() != payload:
        text.clear()
        text.write(payload)


def read_base(module_id, version):
    import bpy
    text = bpy.data.texts.get(base_name(module_id, version))
    if text is None:
        return None
    try:
        return json.loads(text.as_string()).get('layers')
    except ValueError:
        return None


# ---- library --------------------------------------------------------------------

_index = {}


def library_index(context):
    """{module id: {'path', 'version', 'name'}} for the newest version of each library module."""
    from zipfile import ZipFile, is_zipfile
    from . import library
    newest = {}
    for path in library.entries(context):
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            continue
        cached = _index.get(str(path))
        if cached is None or cached[0] != stamp:
            block = None
            try:
                if is_zipfile(path):
                    with ZipFile(path) as archive:
                        block = json.loads(archive.read('material.json')).get('module')
                else:
                    block = json.loads(path.read_text()).get('module')
            except (OSError, ValueError, KeyError):
                block = None
            cached = (stamp, block)
            _index[str(path)] = cached
        block = cached[1]
        if not isinstance(block, dict) or not block.get('id'):
            continue
        entry = {'path': Path(path), 'version': int(block.get('version', 1)), 'name': block.get('name', path.stem)}
        if block['id'] not in newest or entry['version'] > newest[block['id']]['version']:
            newest[block['id']] = entry
    return newest


def instances(ts, module_id=None):
    """Module instance roots in a set (all modules when ``module_id`` is None)."""
    return [layer for layer in ts.layers
            if layer.type == 'FOLDER' and layer.module_id and not layer.module_detached
            and (module_id is None or layer.module_id == module_id)]


def update_available(ts, layer, index):
    if not layer.module_id or layer.module_detached:
        return False
    if catalog_name(layer):
        data = catalog_definition(catalog_name(layer))
        return data is not None and int(data.get('version', 1)) > layer.module_version
    entry = index.get(layer.module_id)
    return entry is not None and entry['version'] > layer.module_version


# ---- Blender-facing operations -------------------------------------------------

def save_module(context, ts, root, path, name='', description=''):
    """Save a folder subtree as a module document; stamps the folder as its origin instance."""
    from datetime import datetime, timezone
    from ..utils.ids import new_id
    from . import serializer
    module_id = root.module_id or new_id()
    version = (root.module_version + 1) if root.module_id else 1
    block = {'id': module_id, 'version': version, 'name': name or root.module_name or root.name,
             'description': description, 'created': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    path = serializer.save(ts, path, root_id=root.id, module=block)
    # The base first: a folder stamped with a version whose base is missing could
    # never be updated, only replaced.
    members = [_snapshot(layer) for layer in subtree(ts, root.id)]
    write_base(module_id, version, comparable(remap(members, denamespace_mapping(members, root.module_instance))))
    root.module_id, root.module_version = module_id, version
    root.module_source, root.module_name = 'library:' + path.name, block['name']
    root.module_detached = False
    return path, block


def _snapshot(layer):
    from ..core.layer_stack import snapshot
    return snapshot(layer)


def detach(root):
    root.module_detached = True


def update(ts, root, path, replace=False):
    """Update one instance to the module document at ``path``; returns (version, conflicts)."""
    from .format import read
    from .loader import decode_assets
    data, assets = read(Path(path))
    from . import portable
    portable.preflight(ts, data)
    module = data.get('module')
    if module is None or module['id'] != root.module_id:
        raise ValueError('That document is not a version of this module.')
    if not replace and read_base(root.module_id, root.module_version) is None:
        raise ValueError(MISSING_BASE)   # before any asset is decoded into the file
    loaded = []
    with decode_assets(assets, loaded) as decode:
        parent_raw = [decode(layer) for layer in data['layers']]
        for layer in parent_raw:
            for channel in layer.get('channels', []):
                if channel.get('mesh_map'):
                    channel['image'] = portable.mesh_image(ts, channel['mesh_map'])
    if any(layer.get('master_controls') for layer in parent_raw):
        # Authored library files can omit default fields/channels. Merge the same
        # complete RNA shape that insertion recorded as the instance's base.
        root_id = root.id
        from ..core.layer_stack import replay, restore
        expanded = []
        with replay(ts):
            for values in parent_raw:
                scratch = ts.layers.add()
                try:
                    restore(values, scratch)
                    _ensure_channels(scratch)
                    for channel in scratch.channels:
                        channel.layer_id = scratch.id
                    expanded.append(_snapshot(scratch))
                finally:
                    ts.layers.remove(len(ts.layers) - 1)
        parent_raw = expanded
        root = next(layer for layer in ts.layers if layer.id == root_id)
    result = apply_update(ts, root, comparable(parent_raw), int(module['version']),
                        module.get('name') or root.module_name, 'library:' + Path(path).name, loaded, replace,
                        parent_raw=parent_raw)
    portable.bind_mesh_maps(ts)
    return result


def catalog_name(root):
    return root.module_source[len('catalog:'):] if root.module_source.startswith('catalog:') else ''


def catalog_definition(name):
    from .composer import CATALOG, MODULES
    return MODULES.get(name) or CATALOG.get(name)


def update_catalog(ts, root, replace=False):
    """Update an instance of a catalog recipe or catalog module to the add-on's current definition.

    The definition is built into the set (transactionally, with the instance's
    own token so nested ids match), snapshotted and removed again.
    """
    from . import composer
    name = catalog_name(root)
    data = catalog_definition(name)
    if data is None:
        raise ValueError(f'The catalog no longer defines {name!r}.')
    root_id, token, parent_id = root.id, root.module_instance, root.parent_id
    # The scratch build shares this instance's ids: keep it apart until it is gone.
    scratch = composer.apply_document(ts, data, name, token='scratch.' + token, parent_id=parent_id, surface=False)
    scratch_id = scratch.id
    try:
        raw = [_snapshot(layer) for layer in subtree(ts, scratch_id)]
        parent_raw = remap(raw, denamespace_mapping(raw, 'scratch.' + token))
        # Nested module instances derive their tokens from ours: map the scratch
        # build's nested ids onto the live instance's so they merge by id.
        for module_name, _ in data.get('modules', ()):
            live_child = composer.nested_token(name, token, module_name)
            scratch_child = composer.nested_token(name, 'scratch.' + token, module_name)
            parent_raw = remap(parent_raw, derived_mapping({identifier: live_child + identifier[len(scratch_child):]
                                                            for identifier in owned_ids(parent_raw)
                                                            if identifier.startswith(scratch_child + '.')}))
        parent = comparable(parent_raw)
    finally:
        doomed = {layer.id for layer in subtree(ts, scratch_id)}
        for index in sorted((i for i, item in enumerate(ts.layers) if item.id in doomed), reverse=True):
            ts.layers.remove(index)
    root = next(layer for layer in ts.layers if layer.id == root_id)   # collections were reallocated
    return apply_update(ts, root, parent, int(data.get('version', 1)), name, 'catalog:' + name, [], replace,
                        parent_raw=parent_raw)


MISSING_BASE = 'The base snapshot of this instance is missing; use Replace to discard local edits.'


def apply_update(ts, root, parent, version, name, source, loaded, replace=False, parent_raw=()):
    """Three-way merge of base (the version the instance came from), ``parent``
    (the new definition, comparable form) and local (the instance as edited),
    applied transactionally: the subtree is replaced by the merged layers in
    place; any failure restores it and drops the images decoded for the update.
    Returns (version, conflicts)."""
    try:
        return _apply_update(ts, root, parent, version, name, source, loaded, replace, parent_raw)
    except Exception:
        _discard(loaded)
        raise


def _apply_update(ts, root, parent, version, name, source, loaded, replace, parent_raw):
    from ..core.layer_stack import invalidate, restore
    from ..evaluation.references import issues
    token = root.module_instance
    if token and not root.id.startswith(token + '.'):
        # The namespace round-trip below would rewrite every id and stamp nothing:
        # a stamped folder whose ids are not under its token is a copy made
        # before 0.9.7 (whole-set import, Duplicate) and cannot be merged.
        raise ValueError('This folder is a copy whose ids are not namespaced; detach it or re-insert the module.')
    members = subtree(ts, root.id)
    local_raw = [_snapshot(layer) for layer in members]
    local_plain_raw = remap(local_raw, denamespace_mapping(local_raw, token))
    local = comparable(local_plain_raw)
    base = read_base(root.module_id, root.module_version)
    if base is None and not replace:
        raise ValueError(MISSING_BASE)
    from copy import deepcopy
    if replace or base is None:
        merged, conflicts = deepcopy(parent), []
    else:
        merged, conflicts = merge(base, parent, local)
    # Nested module instances keep their own identity across the outer update, and
    # a layer's cache (preview-only state, not merged) stays as the artist set it.
    nested = {layer['id']: {key: layer[key] for key in layer if key.startswith('module_')}
              for layer in local_raw if layer.get('module_id') and layer['id'] != root.id}
    cached = {layer['id']: {'cache_mode': layer['cache_mode'], 'cache': layer['cache']}
              for layer in local_raw if layer.get('cache_mode', 'LIVE') != 'LIVE'}
    # Stroke keys name this project's strokes (see VOLATILE): a member keeps its own on Replace
    # too, or the strokes it renders would be orphaned; a layer new to this version starts unkeyed.
    keyed = {layer['id']: {key: layer[key] for key in ('stroke_key', 'paint_link') if layer.get(key)}
             for layer in local_raw}
    keyed_masks = {(layer['id'], mask['id']): mask['stroke_key']
                   for layer in local_raw for mask in layer.get('masks', []) if mask.get('stroke_key')}
    plain_root = plain_id(root.id, token)
    _keep_paint(merged, [] if replace else local_plain_raw, parent_raw)
    for layer in merged:
        if layer['id'] == plain_root:
            layer['parent_id'] = root.parent_id
    # Ids the artist added inside the instance are not the module's: they keep
    # their plain ids so references from outside the instance still find them.
    local_ids = {identifier for identifier in owned_ids(local_raw) if not namespaced(identifier)}
    mapping = namespace_mapping(merged, token, keep=local_ids)
    mapping.pop(root.parent_id, None)
    instance = remap(merged, mapping)
    instance = resolve_images(instance)
    # Replace the subtree in place: remember what was there for rollback.
    saved = [(next(i for i, item in enumerate(ts.layers) if item.id == layer.id), _snapshot(layer)) for layer in members]
    saved_active = ts.active_layer
    stamp = dict(module_id=root.module_id, module_version=version, module_source=source,
                 module_instance=token, module_name=name or root.module_name, module_detached=False)
    root_id = root.id
    position = saved[0][0]
    try:
        for index, _ in sorted(saved, reverse=True):
            ts.layers.remove(index)
        added = []
        for values in instance:
            layer = ts.layers.add()
            values = dict(values, **cached.get(values['id'], {}), **keyed.get(values['id'], {}))
            if 'masks' in values:
                values['masks'] = [dict(mask, stroke_key=keyed_masks[(values['id'], mask.get('id'))])
                                   if (values['id'], mask.get('id')) in keyed_masks else mask
                                   for mask in values['masks']]
            for key in ('dirty', 'order'):
                values.pop(key, None)
            # Merged dicts come in set order: restore in RNA order so a pointer's
            # update callback runs before the name mirroring it is restored.
            restore(_rna_ordered(values, layer.bl_rna), layer)
            for key, value in (stamp if layer.id == root_id else nested.get(layer.id, {})).items():
                setattr(layer, key, value)
            _ensure_channels(layer)
            added.append(layer.id)
        for offset, identifier in enumerate(added):
            source = next(i for i, item in enumerate(ts.layers) if item.id == identifier)
            ts.layers.move(source, min(position + offset, len(ts.layers) - 1))
        problems = issues(ts)
        if problems:
            raise ValueError(' | '.join(problems))
    except Exception:
        for identifier in [layer['id'] for layer in instance]:
            index = next((i for i, item in enumerate(ts.layers) if item.id == identifier), None)
            if index is not None:
                ts.layers.remove(index)
        for index, values in saved:
            layer = ts.layers.add()
            restore(values, layer)
            ts.layers.move(len(ts.layers) - 1, min(index, len(ts.layers) - 1))
        ts.active_layer = saved_active
        invalidate(ts)
        raise
    used = {image.name for layer in instance for image in _images(layer)}
    _discard([image for image in loaded if image.name not in used])
    write_base(stamp['module_id'], version, parent)
    _release(saved, {layer['id'] for layer in instance})
    ts.active_layer = next((i for i, item in enumerate(ts.layers) if item.id == root_id),
                           min(saved_active, len(ts.layers) - 1))
    invalidate(ts)
    return version, conflicts


def _discard(images):
    import bpy
    for image in images:
        try:
            bpy.data.images.remove(image)
        except ReferenceError:
            pass   # already gone


def _rna_ordered(values, rna):
    """``values`` with keys in RNA declaration order, nested property groups included."""
    result = {}
    for prop in rna.properties:
        key = prop.identifier
        if key not in values:
            continue
        value = values[key]
        if prop.type == 'COLLECTION':
            value = [_rna_ordered(item, prop.fixed_type) if isinstance(item, dict) else item for item in value]
        elif prop.type == 'POINTER' and isinstance(value, dict):
            value = _rna_ordered(value, prop.fixed_type)
        result[key] = value
    for key in values:
        result.setdefault(key, values[key])
    return result


def _release(saved, kept_ids):
    """After the subtree was replaced: drop what only the old layers held.

    Cache images and images the add-on made for those layers (decoded assets a
    new version superseded, painted images Replace discarded) lose their fake
    user unless a layer, output or baked map anywhere still shows them: the
    datablocks stay for an undo of the update (a removed one comes back blank,
    see ``layer_stack.release_image``) and are purged on the next save. Decal
    Empties of layers the merge removed go the way ``layer_stack.remove``
    releases them.
    """
    import bpy
    from ..constants import OWNER
    from ..evaluation.layer_cache import TAG
    from ..painting.decals import MARKER
    referenced, decal_users = set(), set()
    for scene in bpy.data.scenes:
        project = getattr(scene, 'open_painter', None)
        if project is None:
            continue
        for ts in project.texture_sets:
            for layer in ts.layers:
                referenced.update(image.as_pointer() for image in _images(_snapshot(layer)))
                if getattr(layer, 'decal_object', None) is not None:
                    decal_users.add(layer.decal_object.as_pointer())
            referenced.update(row.image.as_pointer() for row in (*ts.outputs, *ts.baked_maps) if row.image is not None)
    doomed = {}
    for _, values in saved:
        for image in _images(values):
            try:
                if image.as_pointer() not in referenced and (image.get(OWNER) or image.get(TAG)):
                    doomed[image.as_pointer()] = image
            except ReferenceError:
                continue
        obj = values.get('decal_object')
        if values['id'] not in kept_ids and obj is not None and obj.get(MARKER) == values['id'] \
                and obj.as_pointer() not in decal_users:
            bpy.data.objects.remove(obj)
    for image in doomed.values():
        try:
            image.use_fake_user = False
        except ReferenceError:
            pass


def _keep_paint(merged, local_raw, parent_raw):
    """Painted pixels are always the instance's: paint masks and paint layer channels
    take the local image when the instance has one, else the definition's."""
    def images(layers):
        masks = {(layer['id'], mask.get('id')): mask.get('image') for layer in layers
                 for mask in layer.get('masks', []) if mask.get('source') == 'PAINT'}
        channels = {(layer['id'], channel.get('channel')): channel.get('image') for layer in layers
                    if layer.get('type') == 'PAINT' for channel in layer.get('channels', [])}
        return masks, channels
    local_masks, local_channels = images(local_raw)
    parent_masks, parent_channels = images(parent_raw)
    for layer in merged:
        for mask in layer.get('masks', []):
            if mask.get('source') == 'PAINT':
                key = (layer['id'], mask.get('id'))
                mask['image'] = local_masks.get(key) or parent_masks.get(key)
        if layer.get('type') == 'PAINT':
            for channel in layer.get('channels', []):
                key = (layer['id'], channel.get('channel'))
                channel['image'] = local_channels.get(key) or parent_channels.get(key)


def _images(value):
    if hasattr(value, 'bl_rna') and value.__class__.__name__ == 'Image':
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _images(item)
    elif isinstance(value, list):
        for item in value:
            yield from _images(item)


def _ensure_channels(layer):
    if layer.type == 'FOLDER':
        return
    from ..constants import CHANNELS, DEFAULTS
    for key in CHANNELS:
        if not any(c.channel == key for c in layer.channels):
            prop = layer.channels.add()
            prop.channel, prop.layer_id, prop.enabled = key, layer.id, False
            prop.color, prop.value = DEFAULTS[key], DEFAULTS[key][0]
