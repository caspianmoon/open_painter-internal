"""Versioned ZIP/JSON format validation before Blender data mutation."""

import json
import math
from zipfile import ZipFile, is_zipfile

VERSION = 3  # 3: anchor points (anchor ids on masks, channels and filter items)
MODULE_VERSION = 4  # 4: optional "module" block (a versioned folder subtree with stable ids)
MAX_BYTES = 512 * 1024 * 1024


def read(path) -> tuple[dict, dict[str, bytes]]:
    assets = {}
    if is_zipfile(path):
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if sum(i.file_size for i in entries) > MAX_BYTES or len(entries) > 2048:
                raise ValueError("Smart material exceeds the 512 MiB / 2048 file import limit.")
            data = json.loads(archive.read("material.json"))
            for entry in entries:
                name = entry.filename
                if (
                    name.startswith("assets/")
                    and name.endswith(".exr")
                    and name.count("/") == 1
                    and ".." not in name
                ):
                    assets[name] = archive.read(name)
    else:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("Smart material JSON is too large.")
        data = json.loads(path.read_text())
    if data.get("format_version") not in {1, 2, VERSION, MODULE_VERSION}:
        raise ValueError("Unsupported smart material version.")

    def validate_assets(value):
        # Optional channel detail images use the existing version-2 image
        # reference format. Reject a broken archive before allocating layers.
        if isinstance(value, dict):
            if "asset" in value:
                key = value["asset"]
                if not isinstance(key, str) or key not in assets:
                    raise ValueError(f"Missing smart material texture: {key}")
                if not isinstance(value.get("colorspace", "Non-Color"), str):
                    raise ValueError("Invalid smart material image colorspace.")
            else:
                for child in value.values():
                    validate_assets(child)
        elif isinstance(value, list):
            for child in value:
                validate_assets(child)

    validate_assets(data)
    surface = data.get("surface", {})
    if not isinstance(surface, dict) or any(
        key not in {"anisotropy","coat_weight","coat_roughness","height_distance"}
        or not isinstance(value,(float,int)) or not math.isfinite(value)
        or value < 0 or (key != "height_distance" and value > 1)
        for key,value in surface.items()
    ):
        raise ValueError("Invalid smart material surface values.")
    layers = data.get("layers")
    if not isinstance(layers, list) or len(layers) > 256:
        raise ValueError("Smart material must contain at most 256 layers.")
    ids = {layer.get("id") for layer in layers}
    if None in ids or len(ids) != len(layers):
        raise ValueError("Layer IDs are missing or duplicated.")
    by_id = {item["id"]: item for item in layers}
    state=data.get('authoring_state',{})
    if not isinstance(state,dict) or (state and state.get('version') != 1):
        raise ValueError('Unsupported material state metadata')
    for key in ('baseline','resolved'):
        values=state.get(key,{})
        if not isinstance(values,dict) or any(k not in ids or not isinstance(v,(float,int)) or not 0<=v<=1 for k,v in values.items()):
            raise ValueError('Invalid material state values')
    if not isinstance(state.get('overrides',[]),list) or any(k not in ids for k in state.get('overrides',[])):
        raise ValueError('Invalid material state overrides')
    for layer in layers:
        if layer.get("type") not in {"FILL", "PAINT", "FOLDER"}:
            raise ValueError("Unknown layer type.")
        seen = {layer["id"]}
        parent = layer.get("parent_id", "")
        while parent:
            if parent in seen or parent not in by_id or by_id[parent]["type"] != "FOLDER":
                raise ValueError("Invalid smart material folder hierarchy.")
            seen.add(parent)
            parent = by_id[parent].get("parent_id", "")
    module = data.get("module")
    if module is not None:
        if (not isinstance(module, dict) or not isinstance(module.get("id"), str) or not module["id"]
                or not isinstance(module.get("version"), int) or module["version"] < 1
                or not isinstance(module.get("name", ""), str)):
            raise ValueError("Invalid module metadata.")
        module_root = module.get("root")
        if module_root not in by_id or by_id[module_root].get("type") != "FOLDER":
            raise ValueError("Module root must be a folder in the document.")
        for layer in layers:
            cursor = layer["id"]
            while cursor and cursor != module_root:
                cursor = by_id[cursor].get("parent_id", "")
            if cursor != module_root:
                raise ValueError("Every module layer must descend from the module root.")
    # Reference cycles are item-granular and checked by references.issues once
    # the layers exist; the loader rolls back on failure. Anchor ids that do
    # not resolve import verbatim and report as missing rather than rejecting.
    for layer in layers:
        for mask in layer.get('masks',[]):
            if mask.get('source') != 'REFERENCE' or mask.get('reference_anchor'):
                continue
            target = by_id.get(mask.get('reference_layer'))
            if target is None:
                raise ValueError('Published source layer is missing from material')
            selected = mask.get('reference_mask')
            if selected and not any(m.get('id') == selected for m in target.get('masks',[])):
                raise ValueError('Published mask effect is missing from material')
    return data, assets
