"""Serialize metadata plus lossless linear EXR texture assets."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

import bpy

from ..core.layer_stack import snapshot
from .format import MODULE_VERSION, VERSION


def save(ts, path: str | Path, root_id=None, module=None) -> Path:
    """Write the whole set, or the subtree under ``root_id`` as a module document.

    A module document stores the subtree in the module's own id space (instance
    namespaces stripped) with a ``module`` block ``{id, version, name, root, ...}``.
    """
    from ..evaluation.references import issues
    errors = issues(ts)
    if errors:
        raise ValueError(' | '.join(errors))
    path = Path(path)
    if path.exists():
        raise ValueError("A material already exists at this path. Choose a new filename.")
    path.parent.mkdir(parents=True, exist_ok=True)
    assets = {}
    image_assets = {}

    def encode(value):
        if isinstance(value, bpy.types.Image):
            # The same static detail source can drive several channels and
            # layers. Store it once, preserving that sharing on import.
            pointer = value.as_pointer()
            key = image_assets.get(pointer)
            if key is None:
                key = f"assets/{len(assets):04d}.exr"
                image_assets[pointer] = key
                assets[key] = value
            return {"asset": key, "colorspace": value.colorspace_settings.name}
        if isinstance(value, dict):
            return {
                k: encode(v)
                for k, v in value.items()
                if k not in {"dirty", "revision", "image_name", "detail_image_name"}
            }
        if isinstance(value, list):
            return [encode(v) for v in value]
        return value

    def layer_data(layer):
        for channel in layer.channels:
            for field in ("image", "detail_image"):
                if getattr(channel, field + "_name") and not getattr(channel, field):
                    raise ValueError(f"Missing texture on {layer.name}: {getattr(channel, field + '_name')}")
        values = snapshot(layer)
        for mask in values.get('masks', []):
            if mask.get('source') == 'SCENE':
                field = mask['influence']
                if field['mode'] != 'FROZEN' or not field['image']:
                    raise ValueError('Freeze scene-field analysis before saving a portable material; scene references are not portable')
                field.update(object=None, collection=None, provenance='', error='')
        return encode(values)

    state=json.loads(ts.material_state_data or '{}')
    live={layer.id for layer in ts.layers}
    if state:
        for key in ('baseline','resolved'):
            state[key]={k:v for k,v in state.get(key,{}).items() if k in live}
        state['overrides']=[k for k in state.get('overrides',[]) if k in live]
    if root_id is None:
        layers = [layer_data(layer) for layer in ts.layers]
    else:
        from . import modules
        from ..utils.ids import new_id
        root = next((layer for layer in ts.layers if layer.id == root_id), None)
        if root is None or root.type != 'FOLDER':
            raise ValueError('Save a folder as a module.')
        members = modules.subtree(ts, root_id)
        # Filter items merge by id across versions: every effect needs one.
        for layer in members:
            for channel in layer.channels:
                for effect in channel.effects:
                    if not effect.id:
                        effect.id = new_id()
        layers = [layer_data(layer) for layer in members]
        layers = modules.remap(layers, modules.denamespace_mapping(layers, root.module_instance))
        plain_root = modules.plain_id(root_id, root.module_instance)
        for layer in layers:
            for key in modules.VOLATILE:
                if key.startswith('module_'):
                    layer.pop(key, None)
            if layer['id'] == plain_root:
                layer['parent_id'] = ''   # the module's context is not part of the module
        module = dict(module or {}, root=plain_root)
        state = {}
    data = {
        "format_version": MODULE_VERSION if root_id is not None else VERSION,
        "name": ts.name if root_id is None else (module.get('name') or root.name),
        "layers": layers,
        "surface": {key:getattr(ts,key) for key in ("anisotropy","coat_weight","coat_roughness","height_distance")},
        'authoring_state': state,
    }
    if root_id is not None:
        data['module'] = module
    temp_scene = bpy.data.scenes.new("OP Asset Encoding")
    try:
        fmt = temp_scene.render.image_settings
        fmt.file_format = "OPEN_EXR"
        fmt.color_depth = "32"
        fmt.color_mode = "RGBA"
        fmt.exr_codec = "ZIP"
        with TemporaryDirectory(prefix="open-painter-") as directory:
            staged = Path(directory) / "material.openpaintmaterial"
            with ZipFile(staged, "w", ZIP_DEFLATED) as archive:
                archive.writestr("material.json", json.dumps(data, indent=2, allow_nan=False))
                for key, image in assets.items():
                    asset = Path(directory) / Path(key).name
                    image.save_render(str(asset), scene=temp_scene)
                    archive.write(asset, key)
            # Exclusive destination avoids accidental overwrites.
            with path.open("xb") as destination:
                destination.write(staged.read_bytes())
    finally:
        bpy.data.scenes.remove(temp_scene)
    return path
