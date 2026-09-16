"""Apply validated stacks with fresh IDs and independent packed image assets."""

from contextlib import contextmanager
from pathlib import Path
import json
from tempfile import TemporaryDirectory

import bpy

from ..constants import OWNER
from ..core.layer_stack import invalidate, restore, snapshot
from ..utils.ids import new_id
from .format import read


@contextmanager
def decode_assets(assets, loaded):
    """Yield a decoder turning ``{"asset": key}`` values into packed, owned images."""
    from ..constants import OWNER
    decoded_images = {}
    with TemporaryDirectory(prefix="open-painter-import-") as directory:

        def decode(value):
            if isinstance(value, dict) and "asset" in value:
                key = value["asset"]
                if key not in assets:
                    raise ValueError(f"Missing smart material texture: {key}")
                identity = (key, value.get("colorspace", "Non-Color"))
                if identity in decoded_images:
                    return decoded_images[identity]
                dest = Path(directory) / f"{new_id()}.exr"
                dest.write_bytes(assets[key])
                image = bpy.data.images.load(str(dest), check_existing=False)
                loaded.append(image)
                image[OWNER] = True
                image.colorspace_settings.name = value.get("colorspace", "Non-Color")
                image.pack()
                image.use_fake_user = True
                decoded_images[identity] = image
                return image
            if isinstance(value, dict):
                return {k: decode(v) for k, v in value.items()}
            if isinstance(value, list):
                return [decode(v) for v in value]
            return value

        yield decode


def apply(ts, path: str | Path, parent_id: str = '', token: str | None = None) -> list:
    """Append the document's layers to ``ts``; returns the new root-level layers.

    A module document keeps its own ids under a fresh (or given) namespace token
    and lands under ``parent_id``; its root folder is stamped with the module
    identity and the inserted version becomes the instance's base snapshot.
    """
    from . import modules
    path = Path(path)
    data, assets = read(path)
    module = data.get('module')
    if module is not None:
        token = token or new_id()[:8]
        namespaced = modules.namespace_mapping(data['layers'], token)
        mapping = {layer['id']: namespaced[layer['id']] for layer in data['layers']}
        mask_mapping = {m['id']: namespaced[m['id']] for layer in data['layers'] for m in layer.get('masks', []) if m.get('id')}
        effect_mapping = {e['id']: namespaced[e['id']] for layer in data['layers'] for c in layer.get('channels', [])
                          for e in c.get('effects', []) if e.get('id')}
    else:
        mapping = {layer["id"]: new_id() for layer in data["layers"]}
        mask_mapping = {m['id']: new_id() for layer in data['layers'] for m in layer.get('masks',[]) if m.get('id')}
        effect_mapping = {e['id']: new_id() for layer in data['layers'] for c in layer.get('channels', [])
                          for e in c.get('effects', []) if e.get('id')}
    if parent_id and not any(layer.id == parent_id and layer.type == 'FOLDER' for layer in ts.layers):
        raise ValueError('The target folder no longer exists.')
    # Anchor ids are mask or effect ids; unknown ones stay verbatim so they report as missing.
    from ..evaluation.anchors import derived_mapping
    anchor_mapping = {**mask_mapping, **effect_mapping, **derived_mapping(mapping)}
    loaded = []
    decoded_images = {}
    start = len(ts.layers)
    old_surface = {key:getattr(ts,key) for key in ("anisotropy","coat_weight","coat_roughness","height_distance")}
    try:
        with TemporaryDirectory(prefix="open-painter-import-") as directory:

            def decode(value):
                if isinstance(value, dict) and "asset" in value:
                    key = value["asset"]
                    if key not in assets:
                        raise ValueError(f"Missing smart material texture: {key}")
                    identity = (key, value.get("colorspace", "Non-Color"))
                    if identity in decoded_images:
                        return decoded_images[identity]
                    dest = Path(directory) / f"{new_id()}.exr"
                    dest.write_bytes(assets[key])
                    image = bpy.data.images.load(str(dest), check_existing=False)
                    loaded.append(image)
                    image[OWNER] = True
                    image.colorspace_settings.name = value.get("colorspace", "Non-Color")
                    image.pack()
                    image.use_fake_user = True
                    decoded_images[identity] = image
                    return image
                if isinstance(value, dict):
                    return {k: decode(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [decode(v) for v in value]
                return value

            for source in data["layers"]:
                values = decode(source)
                values["id"] = mapping[source["id"]]
                values["parent_id"] = mapping.get(source.get("parent_id"), parent_id)
                for channel in values.get("channels", []):
                    channel["layer_id"] = values["id"]
                    if channel.get('anchor'):
                        channel['anchor'] = anchor_mapping.get(channel['anchor'], channel['anchor'])
                    for effect in channel.get('effects', []):
                        if effect.get('id'):
                            effect['id'] = effect_mapping.get(effect['id'], effect['id'])
                for mask in values.get("masks", []):
                    mask['id'] = mask_mapping.get(mask.get('id'), new_id())
                    if mask.get('source') == 'REFERENCE':
                        mask['reference_layer'] = mapping.get(mask.get('reference_layer'), '')
                        mask['reference_mask'] = mask_mapping.get(mask.get('reference_mask'), '')
                    for field in ('reference_anchor', 'detail_height_anchor', 'detail_normal_anchor'):
                        if mask.get(field):
                            mask[field] = anchor_mapping.get(mask[field], mask[field])
                layer = ts.layers.add()
                restore(values, layer)
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
        for key, value in data.get("surface", {}).items():
            if key in old_surface and module is None:
                setattr(ts,key,value)
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
        for image in loaded:
            bpy.data.images.remove(image)
        raise
    ts.active_layer = max(0, len(ts.layers) - 1)
    invalidate(ts)
    roots = [layer for layer in ts.layers[start:] if layer.parent_id == parent_id]
    if module is not None:
        ts.active_layer = next(i for i, layer in enumerate(ts.layers) if layer.id == mapping[module['root']])
    return roots
