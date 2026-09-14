"""Apply validated stacks with fresh IDs and independent packed image assets."""

from pathlib import Path
import json
from tempfile import TemporaryDirectory

import bpy

from ..constants import OWNER
from ..core.layer_stack import invalidate, restore
from ..utils.ids import new_id
from .format import read


def apply(ts, path: str | Path) -> None:
    data, assets = read(Path(path))
    mapping = {layer["id"]: new_id() for layer in data["layers"]}
    mask_mapping = {m['id']: new_id() for layer in data['layers'] for m in layer.get('masks',[]) if m.get('id')}
    effect_mapping = {e['id']: new_id() for layer in data['layers'] for c in layer.get('channels', [])
                      for e in c.get('effects', []) if e.get('id')}
    # Anchor ids are mask or effect ids; unknown ones stay verbatim so they report as missing.
    anchor_mapping = {**mask_mapping, **effect_mapping}
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
                values["parent_id"] = mapping.get(source.get("parent_id"), "")
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
        for key, value in data.get("surface", {}).items():
            if key in old_surface:
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
