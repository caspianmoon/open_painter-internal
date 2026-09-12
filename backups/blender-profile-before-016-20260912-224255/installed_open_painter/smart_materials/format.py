"""Versioned ZIP/JSON format validation before Blender data mutation."""

import json
from zipfile import ZipFile, is_zipfile

VERSION = 1
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
    if data.get("format_version") != VERSION:
        raise ValueError("Unsupported smart material version.")
    layers = data.get("layers")
    if not isinstance(layers, list) or len(layers) > 256:
        raise ValueError("Smart material must contain at most 256 layers.")
    ids = {layer.get("id") for layer in layers}
    if None in ids or len(ids) != len(layers):
        raise ValueError("Layer IDs are missing or duplicated.")
    by_id = {item["id"]: item for item in layers}
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
    return data, assets
