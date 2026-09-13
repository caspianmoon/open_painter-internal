"""Versioned ZIP/JSON format validation before Blender data mutation."""

import json
from zipfile import ZipFile, is_zipfile

VERSION = 2
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
    if data.get("format_version") not in {1, VERSION}:
        raise ValueError("Unsupported smart material version.")
    surface = data.get("surface", {})
    if not isinstance(surface, dict) or any(
        key not in {"anisotropy","coat_weight","coat_roughness"}
        or not isinstance(value,(float,int)) or not 0 <= value <= 1
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
    edges = {identifier: set() for identifier in by_id}
    for layer in layers:
        for mask in layer.get('masks',[]):
            if mask.get('source') != 'REFERENCE':
                continue
            target = by_id.get(mask.get('reference_layer'))
            if target is None:
                raise ValueError('Published source layer is missing from material')
            selected = mask.get('reference_mask')
            if selected and not any(m.get('id') == selected for m in target.get('masks',[])):
                raise ValueError('Published mask effect is missing from material')
            edges[layer['id']].add(target['id'])
    visiting, done = set(), set()
    def visit(identifier):
        if identifier in visiting:
            raise ValueError('Material published inputs contain a reference cycle')
        if identifier in done:
            return
        visiting.add(identifier)
        for target in edges[identifier]:
            visit(target)
        visiting.remove(identifier); done.add(identifier)
    for identifier in edges:
        visit(identifier)
    return data, assets
