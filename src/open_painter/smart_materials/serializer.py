"""Serialize metadata plus lossless linear EXR texture assets."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

import bpy

from ..core.layer_stack import snapshot
from ..core.surface_settings import FIELDS as SURFACE_FIELDS
from . import format as material_format
from .format import LINEAR, MODULE_VERSION, VERSION


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

    def encode(value, where=''):
        if isinstance(value, bpy.types.Image):
            # The same static detail source can drive several channels and
            # layers. Store it once, preserving that sharing on import.
            pointer = value.as_pointer()
            key = image_assets.get(pointer)
            if key is None:
                key = f"assets/{len(assets):04d}.exr"
                image_assets[pointer] = key
                assets[key] = value
            # The EXR holds what save_render wrote: scene-linear for every
            # non-data source, a byte sRGB albedo included.
            colorspace = "Non-Color" if value.colorspace_settings.is_data else LINEAR
            # The alpha mode travels too: a channel-packed brush alpha reloaded as a
            # premultiplied EXR would be divided by its own alpha on read.
            return {"asset": key, "colorspace": colorspace, "alpha_mode": value.alpha_mode}
        if isinstance(value, bpy.types.ID):
            # Any other datablock still in the snapshot is a gap in layer_data;
            # name the field rather than let json.dumps fail on the type.
            raise ValueError(f"{where} references {value.name!r}, which is not portable; clear it before saving")
        if isinstance(value, dict):
            return {
                k: encode(v, f"{where}.{k}" if where else k)
                for k, v in value.items()
                if k not in {"dirty", "revision", "image_name", "detail_image_name"}
            }
        if isinstance(value, list):
            return [encode(v, f"{where}[{i}]") for i, v in enumerate(value)]
        return value

    def layer_data(layer):
        from ..evaluation.text_source import ensure as ensure_text
        for mask in layer.masks:
            if mask.source == 'TEXT':
                ensure_text(mask.text, int(ts.resolution))
        for channel in layer.channels:
            for field in ("image", "detail_image"):
                if getattr(channel, field + "_name") and not getattr(channel, field):
                    raise ValueError(f"Missing texture on {layer.name}: {getattr(channel, field + '_name')}")
        values = snapshot(layer)
        if values.get('decal_object') is not None:
            values['decal_object_name'] = values['decal_object'].name
        values['decal_object'] = None   # decal Empties re-link by name on import (0.9.6)
        # Preview-only state (0.9.0) is not part of a portable material.
        values.pop('cache', None)
        values['cache_mode'] = 'LIVE'
        values['bypass'] = False
        # A paint link pairs this layer with layers of other sets in this project; the
        # imported copy paints on its own. The stroke key stays: the loader re-keys it
        # together with the strokes saved below.
        values['paint_link'] = ''
        for channel in values.get('channels', []):
            if channel.get('mesh_map'):
                channel['image'] = None  # Rebind to the recipient mesh; never package its donor bake.
                channel['image_name'] = ''
        for mask in values.get('masks', []):
            if 'mesh' in mask:
                # Mesh masks re-link by name on import; datablock pointers are not portable.
                # The names are read from the pointers now: a datablock renamed after it was
                # picked would otherwise travel under its old name.
                for name in ('object', 'collection', 'material'):
                    if mask['mesh'].get(name) is not None:
                        mask['mesh'][name + '_name'] = mask['mesh'][name].name
                mask['mesh'].update(object=None, collection=None, material=None)
            if mask.get('source') == 'GRAPH':
                from .graph_codec import encode as encode_graph
                mask['graph_data'] = encode_graph(mask['node_group'], int(ts.resolution))
            mask['node_group'] = None   # a graph picked before the source changed
            field = mask['influence']
            if mask.get('source') == 'SCENE' and (field['mode'] != 'FROZEN' or not field['image']):
                raise ValueError('Freeze scene-field analysis before saving a portable material; scene references are not portable')
            # Whatever the source: scene pointers picked while the mask was a scene
            # field survive a source change and are not portable either way.
            field.update(object=None, collection=None, ground_object=None, exclude_collection=None,
                         provenance='', error='')
        return encode(values, layer.name)

    def stroke_data(layers):
        """The project's vector strokes the saved layers and Vector Strokes masks render.

        Strokes belong to the project, keyed by the layers that render them: a
        document without them imports a stroke layer that shows nothing (and
        the next paint session retires its idle key for good). Saved in stack
        order under their own keys; the loader re-keys them with the layers.
        """
        from ..painting.strokes import strokes_for
        project = ts.id_data.open_painter
        keys = []
        for layer in layers:
            for key in (layer.stroke_key, *(mask.stroke_key for mask in layer.masks if mask.source == 'STROKES')):
                if key and key not in keys:
                    keys.append(key)
        records = []
        for key in keys:
            # Only what this set may render travels: a stroke another set's layer shares the
            # key with, but that was never permitted here, must not appear in the copy.
            for stroke in strokes_for(project, key, ts):
                values = snapshot(stroke)
                # Identity and UI state are the project's; the loader issues new ids.
                for field in ('id', 'revision', 'selected'):
                    values.pop(field, None)
                records.append(encode(values, f'{stroke.name} (stroke)'))
        return records

    state=json.loads(ts.material_state_data or '{}')
    live={layer.id for layer in ts.layers}
    if state:
        for key in ('baseline','resolved'):
            state[key]={k:v for k,v in state.get(key,{}).items() if k in live}
        state['overrides']=[k for k in state.get('overrides',[]) if k in live]
    if root_id is None:
        layers = [layer_data(layer) for layer in ts.layers]
        strokes = stroke_data(ts.layers)
    else:
        from ..utils.ids import new_id
        from . import modules
        root = next((layer for layer in ts.layers if layer.id == root_id), None)
        if root is None or root.type != 'FOLDER':
            raise ValueError('Save a folder as a module.')
        members = modules.subtree(ts, root_id)
        # A module's id space is its own: an instance nested inside keeps its
        # token in the document, and inserting that collides with the original
        # (and with every other insert). A detached one is no different.
        for layer in members:
            if layer.id != root_id and layer.module_id:
                raise ValueError(f'{layer.name!r} is a module instance inside this folder; '
                                 'move it out before saving the folder as a module.')
        # A legacy layer/mask reference resolves within the set but not within the
        # document: format.read would refuse every later insert or update, and
        # save_module stamps the folder only once this returns.
        by_id = {layer.id: layer for layer in members}
        for layer in members:
            for mask in layer.masks:
                if mask.source != 'REFERENCE' or mask.reference_anchor:
                    continue
                source = by_id.get(mask.reference_layer)
                if source is None or (mask.reference_mask
                                      and not any(m.id == mask.reference_mask for m in source.masks)):
                    raise ValueError(f"{layer.name} reads a published input outside {root.name}; "
                                     "use an anchor or move the source into the folder")
        # Filter items merge by id across versions: every effect needs one.
        for layer in members:
            for channel in layer.channels:
                for effect in channel.effects:
                    if not effect.id:
                        effect.id = new_id()
        layers = [layer_data(layer) for layer in members]
        strokes = stroke_data(members)
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
        "surface": {key:getattr(ts,key) for key in SURFACE_FIELDS},
        'authoring_state': state,
    }
    if root_id is not None:
        data['module'] = module
    if strokes:
        data['strokes'] = strokes
    if root_id is None and ts.aging.folder_id and any(layer.id == ts.aging.folder_id for layer in ts.layers):
        # Material Aging travels with the whole set: its history and the driven-value
        # record, so the import re-simulates from the same settings and keeps overrides.
        from ..aging import engine as aging_engine
        data['aging'] = aging_engine.export_settings(ts.aging)
    temp_scene = bpy.data.scenes.new("OP Asset Encoding")
    try:
        fmt = temp_scene.render.image_settings
        fmt.file_format = "OPEN_EXR"
        fmt.color_mode = "RGBA"
        fmt.exr_codec = "ZIP"
        with TemporaryDirectory(prefix="open-painter-") as directory:
            document = json.dumps(data, indent=2, allow_nan=False)
            encoded = {}
            for key, image in assets.items():
                # Half floats hold every 8-bit step of a byte-sourced texture;
                # float canvases keep their full precision.
                fmt.color_depth = "32" if image.is_float else "16"
                asset = Path(directory) / Path(key).name
                image.save_render(str(asset), scene=temp_scene)
                encoded[key] = asset
            # The reader enforces the budget on the stored entry sizes (the EXRs
            # are compressed internally, so the outer deflate does not shrink
            # them): refuse here rather than write a file nothing can import.
            size = len(document.encode()) + sum(asset.stat().st_size for asset in encoded.values())
            if size > material_format.MAX_BYTES or len(encoded) + 1 > material_format.MAX_FILES:
                raise ValueError(
                    f"Smart material would bundle {size / 2 ** 20:.0f} MiB in {len(encoded)} textures; "
                    f"the import limit is {material_format.MAX_BYTES // 2 ** 20} MiB / {material_format.MAX_FILES} files. "
                    "Lower the resolution or save a smaller folder as a module.")
            staged = Path(directory) / "material.openpaintmaterial"
            with ZipFile(staged, "w", ZIP_DEFLATED) as archive:
                archive.writestr("material.json", document)
                for key, asset in encoded.items():
                    archive.write(asset, key)
            # Exclusive destination avoids accidental overwrites.
            with path.open("xb") as destination:
                destination.write(staged.read_bytes())
    finally:
        bpy.data.scenes.remove(temp_scene)
    return path
