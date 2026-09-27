"""Apply validated stacks with fresh IDs and independent packed image assets."""

import json
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import bpy

from ..constants import OWNER
from ..core.layer_stack import copy_ids, invalidate, restore, snapshot
from ..core.surface_settings import DEFAULTS as SURFACE_DEFAULTS
from ..core.surface_settings import FIELDS as SURFACE_FIELDS
from ..properties.mesh_selector import degrees_to_radians
from ..utils.ids import new_id
from .format import asset_colorspace, read


@contextmanager
def decode_assets(assets, loaded):
    """Yield a decoder turning ``{"asset": key}`` values into packed, owned images.

    A lookup asset (fill or detail image, frozen field) whose bytes and colorspace
    are already packed in the file, tagged with their content hash, is shared:
    updates and repeated inserts add no copies and the module base compares it
    by content. Painted images (paint masks, paint layer channels) are always
    the instance's own copy and carry no hash.
    """
    from hashlib import sha256

    from ..constants import OWNER
    from .modules import ASSET_HASH, asset_image
    decoded_images = {}
    with TemporaryDirectory(prefix="open-painter-import-") as directory:

        def load(value, painted):
            key = value["asset"]
            if key not in assets:
                raise ValueError(f"Missing smart material texture: {key}")
            colorspace = asset_colorspace(value.get("colorspace", "Non-Color"))
            alpha_mode = value.get("alpha_mode")
            if alpha_mode not in {"STRAIGHT", "PREMUL", "CHANNEL_PACKED", "NONE"}:
                alpha_mode = None      # documents before the field: Blender's default for the file
            identity = (key, colorspace, painted, alpha_mode)
            if identity in decoded_images:
                return decoded_images[identity]
            digest = sha256(assets[key]).hexdigest()
            image = None if painted else asset_image(digest, colorspace)
            if image is not None and alpha_mode is not None and image.alpha_mode != alpha_mode:
                image = None           # shared by content, but read differently: its own copy
            if image is None:
                dest = Path(directory) / f"{new_id()}.exr"
                dest.write_bytes(assets[key])
                image = bpy.data.images.load(str(dest), check_existing=False)
                loaded.append(image)
                image[OWNER] = True
                image.colorspace_settings.name = colorspace
                if alpha_mode is not None:
                    image.alpha_mode = alpha_mode
                image.pack()
                image.use_fake_user = True
                if not painted:
                    image[ASSET_HASH] = digest
            decoded_images[identity] = image
            return image

        def decode(value, painted=False):
            if isinstance(value, dict) and "asset" in value:
                return load(value, painted)
            if isinstance(value, dict):
                if isinstance(value.get("mesh"), dict):
                    # A mask saved while selector angles were degrees: converted
                    # here so the insert, the update's parent and the base
                    # snapshot written from it all hold radians.
                    value = dict(value, mesh=degrees_to_radians(value["mesh"]))
                own = value.get("source") == "PAINT" or value.get("type") == "PAINT"
                return {k: decode(v, painted or (own and k in ("image", "channels"))) for k, v in value.items()}
            if isinstance(value, list):
                return [decode(v, painted) for v in value]
            return value

        yield decode


def _stroke_records(data):
    """The document's vector strokes, checked before any layer is added.

    Samples decode to the raster's row shape and finite values; the fields are
    typed by RNA when the record is restored (a failure there rolls the import
    back). A stroke keyed to nothing the document renders is dropped.
    """
    from binascii import Error as DecodeError

    import numpy as np

    from ..painting.stroke_raster import unpack_samples
    from ..painting.surface_reach import unpack_anchors
    records = data.get('strokes', [])
    if not isinstance(records, list) or len(records) > 16384:
        raise ValueError('Invalid smart material strokes.')
    for record in records:
        if (not isinstance(record, dict) or not isinstance(record.get('layer_key'), str)
                or not isinstance(record.get('samples', ''), str)):
            raise ValueError('Invalid smart material stroke record.')
        try:
            samples = unpack_samples(record.get('samples', ''))
            anchors = unpack_anchors(record.get('anchors', '') if isinstance(record.get('anchors', ''), str) else '')
        except (ValueError, DecodeError):
            raise ValueError('Invalid smart material stroke samples.') from None
        if not np.isfinite(anchors).all():
            raise ValueError('Invalid smart material stroke anchors.')
        if not np.isfinite(samples).all():
            raise ValueError('Invalid smart material stroke samples.')
        from types import SimpleNamespace
        from ..painting.payload import decode
        decode(SimpleNamespace(dab_payload=record.get('dab_payload', '')), len(samples))
        if record.get('payload_version', 0) not in {0, 1}:
            raise ValueError('Unsupported smart material stroke payload')
        if record.get('brush_graph_data'):
            import json
            program = json.loads(record['brush_graph_data'])
            if not isinstance(program, dict) or program.get('version') != 1 or len(program.get('steps', [])) > 256:
                raise ValueError('Invalid saved Brush Graph')
    return records


def _stroke_keys(layers):
    """Fresh keys for the stroke keys the document's layers and masks render.

    A key names strokes of the project that saved the material; kept verbatim
    it would alias another set's strokes here (or render nothing elsewhere).
    Every layer and mask sharing a saved key shares the new one.
    """
    keys = {}
    for layer in layers:
        for key in (layer.get('stroke_key', ''), *(mask.get('stroke_key', '') for mask in layer.get('masks', []))):
            if key and isinstance(key, str):
                keys.setdefault(key, new_id())
    return keys


def _copyable(layers):
    """Document layers in the shape ``copy_ids`` reads; older formats lack some of the keys."""
    return [dict(layer, module_instance=layer.get('module_instance', ''),
                 masks=[mask for mask in layer.get('masks', []) if mask.get('id')],
                 channels=[{'effects': [effect for effect in channel.get('effects', []) if effect.get('id')]}
                           for channel in layer.get('channels', [])])
            for layer in layers]


def apply(ts, path: str | Path, parent_id: str = '', token: str | None = None) -> list:
    """Append the document's layers to ``ts``; returns the new root-level layers.

    A module document keeps its own ids under a fresh (or given) namespace token
    and lands under ``parent_id``; its root folder is stamped with the module
    identity and the inserted version becomes the instance's base snapshot.
    """
    from . import modules
    path = Path(path)
    data, assets = read(path)
    from . import portable
    portable.preflight(ts, data)
    module = data.get('module')
    tokens = {}
    if module is not None:
        # The module's id space is closed: an id already under a token (a nested
        # instance, which serializer.save refuses since 0.9.7) would be inserted
        # verbatim and collide with the original and with every other insert.
        if any(modules.namespaced(identifier) for identifier in modules.owned_ids(data['layers'])):
            raise ValueError('This module document nests another module instance and cannot be inserted; '
                             'save the module again from a folder without one.')
        token = token or new_id()[:8]
        namespaced = modules.namespace_mapping(data['layers'], token)
        mapping = {layer['id']: namespaced.get(layer['id'], layer['id']) for layer in data['layers']}
        mask_mapping = {m['id']: namespaced.get(m['id'], m['id']) for layer in data['layers']
                        for m in layer.get('masks', []) if m.get('id')}
        effect_mapping = {e['id']: namespaced.get(e['id'], e['id']) for layer in data['layers']
                          for c in layer.get('channels', []) for e in c.get('effects', []) if e.get('id')}
    else:
        # A whole-set document carries its module instances as saved: like Duplicate,
        # the import puts each one under a fresh token so it stays an instance the
        # module's base matches, instead of a stamped folder with unrelated ids.
        ids, tokens = copy_ids(_copyable(data['layers']))
        mapping = {layer['id']: ids[layer['id']] for layer in data['layers']}
        mask_mapping = {m['id']: ids[m['id']] for layer in data['layers'] for m in layer.get('masks', []) if m.get('id')}
        effect_mapping = {e['id']: ids[e['id']] for layer in data['layers'] for c in layer.get('channels', [])
                          for e in c.get('effects', []) if e.get('id')}
    if parent_id and not any(layer.id == parent_id and layer.type == 'FOLDER' for layer in ts.layers):
        raise ValueError('The target folder no longer exists.')
    # Anchor ids are mask or effect ids; unknown ones stay verbatim so they report as missing.
    from ..evaluation.anchors import derived_mapping
    anchor_mapping = {**mask_mapping, **effect_mapping, **derived_mapping(mapping)}
    stroke_keys = _stroke_keys(data['layers'])
    stroke_records = _stroke_records(data)
    project = ts.id_data.open_painter
    loaded = []
    loaded_graphs = []
    added_strokes = []
    start = len(ts.layers)
    old_surface = {key:getattr(ts,key) for key in SURFACE_FIELDS}
    try:
        with decode_assets(assets, loaded) as decode:
            for source in data["layers"]:
                values = decode(source)
                values["id"] = mapping[source["id"]]
                values["parent_id"] = mapping.get(source.get("parent_id"), parent_id)
                # Vector strokes (0.6.0) follow the layer under a key of this import's own;
                # a paint link (0.9.7) names layers of the saving project and is dropped
                # (documents before 0.9.8 carry it).
                if values.get('stroke_key'):
                    values['stroke_key'] = stroke_keys[values['stroke_key']]
                values['paint_link'] = ''
                for channel in values.get("channels", []):
                    if channel.get('mesh_map'):
                        channel['image'] = portable.mesh_image(ts, channel['mesh_map'])
                    channel["layer_id"] = values["id"]
                    if channel.get('anchor'):
                        channel['anchor'] = anchor_mapping.get(channel['anchor'], channel['anchor'])
                    for effect in channel.get('effects', []):
                        if effect.get('id'):
                            effect['id'] = effect_mapping.get(effect['id'], effect['id'])
                for mask in values.get("masks", []):
                    if 'graph_data' in mask:
                        from .graph_codec import decode as decode_graph
                        graph = decode_graph(mask.pop('graph_data'))
                        loaded_graphs.append(graph)
                        mask['node_group'] = graph
                    mask['id'] = mask_mapping.get(mask.get('id'), new_id())
                    if mask.get('parent_id'):   # mask group membership (0.9.3) follows the new ids
                        mask['parent_id'] = mask_mapping.get(mask['parent_id'], '')
                    if mask.get('source') == 'REFERENCE':
                        mask['reference_layer'] = mapping.get(mask.get('reference_layer'), '')
                        mask['reference_mask'] = mask_mapping.get(mask.get('reference_mask'), '')
                    for field in ('reference_anchor', 'detail_height_anchor', 'detail_normal_anchor'):
                        if mask.get(field):
                            mask[field] = anchor_mapping.get(mask[field], mask[field])
                    if mask.get('stroke_key'):
                        mask['stroke_key'] = stroke_keys[mask['stroke_key']]
                layer = ts.layers.add()
                restore(values, layer)
                if source['id'] in tokens:
                    layer.module_instance = tokens[source['id']]
                if layer.type != "FOLDER":
                    from ..constants import CHANNELS, DEFAULTS

                    for key in CHANNELS:
                        if not any(c.channel == key for c in layer.channels):
                            prop = layer.channels.add()
                            prop.channel = key
                            prop.layer_id = layer.id
                            prop.enabled = False
                            prop.color = DEFAULTS[key]
                            prop.value = DEFAULTS[key][0]
            # The strokes the imported layers render, as the project's own records.
            from ..painting.strokes import restore_records
            restore_records(project, ts, [decode(record) for record in stroke_records], stroke_keys, added_strokes)
        from ..evaluation.references import issues
        problems = issues(ts)
        if problems:
            raise ValueError(' | '.join(problems))
        # Sizes authored in metres resolve to this asset's texel density (0.9.5).
        from ..evaluation import physical
        physical.sync(ts)
        from ..painting import decals
        decals.sync(ts)   # decal layers re-link their Empties by name (0.9.6)
        if module is not None:
            root = next(layer for layer in ts.layers if layer.id == mapping[module['root']])
            root.module_id, root.module_version = module['id'], int(module['version'])
            root.module_source = module.get('source') or 'library:' + path.name
            root.module_instance, root.module_name = token, module.get('name') or root.name
            root.module_detached = False
            inserted = [snapshot(layer) for layer in modules.subtree(ts, root.id)]
            modules.write_base(module['id'], int(module['version']),
                               modules.comparable(modules.remap(inserted, modules.denamespace_mapping(inserted, token))))
        optical = {key: SURFACE_DEFAULTS[key] for key in ("ior", "transmission_weight", "subsurface_weight", "subsurface_scale", "sheen_weight", "sheen_roughness")}
        for key, value in {**optical, **data.get("surface", {})}.items():
            if key in old_surface and (module is None or any(layer.get('master_controls') for layer in data['layers'])):
                setattr(ts,key,value)
        aging = data.get('aging')
        if aging and module is None and aging.get('folder_id') in mapping:
            from ..aging import engine as aging_engine
            aging_engine.import_settings(ts.aging, aging, mapping[aging['folder_id']])
        state = data.get('authoring_state',{})
        if state:
            state = dict(state)
            for key in ('baseline','resolved'):
                state[key] = {mapping[k]:v for k,v in state.get(key,{}).items() if k in mapping}
            state['overrides'] = [mapping[k] for k in state.get('overrides',[]) if k in mapping]
            existing = json.loads(ts.material_state_data or '{}')
            for key in ('baseline','resolved'):
                state[key] = dict(existing.get(key,{}),**state[key])
            state['overrides'] = list(set(existing.get('overrides',[])) | set(state['overrides']))
            ts.material_state_data = json.dumps(state)
    except Exception:
        for key,value in old_surface.items():
            setattr(ts,key,value)
        while len(ts.layers) > start:
            ts.layers.remove(len(ts.layers) - 1)
        if added_strokes:
            for index in reversed([i for i, stroke in enumerate(project.strokes) if stroke.id in added_strokes]):
                project.strokes.remove(index)
            project.stroke_serial += 1
        for graph in loaded_graphs:
            bpy.data.node_groups.remove(graph)
        for image in loaded:
            bpy.data.images.remove(image)
        raise
    ts.active_layer = max(0, len(ts.layers) - 1)
    invalidate(ts)
    roots = [layer for layer in ts.layers[start:] if layer.parent_id == parent_id]
    if module is not None:
        ts.active_layer = next(i for i, layer in enumerate(ts.layers) if layer.id == mapping[module['root']])
    return roots
