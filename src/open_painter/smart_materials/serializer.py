"""Serialize metadata plus lossless linear EXR texture assets."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

import bpy

from ..core.layer_stack import snapshot
from .format import VERSION


def save(ts, path: str | Path) -> Path:
    path = Path(path)
    if path.exists():
        raise ValueError("A material already exists at this path. Choose a new filename.")
    path.parent.mkdir(parents=True, exist_ok=True)
    assets = {}

    def encode(value):
        if isinstance(value, bpy.types.Image):
            key = f"assets/{len(assets):04d}.exr"
            assets[key] = value
            return {"asset": key, "colorspace": value.colorspace_settings.name}
        if isinstance(value, dict):
            return {
                k: encode(v)
                for k, v in value.items()
                if k not in {"dirty", "revision", "image_name"}
            }
        if isinstance(value, list):
            return [encode(v) for v in value]
        return value

    data = {
        "format_version": VERSION,
        "name": ts.name,
        "layers": [encode(snapshot(layer)) for layer in ts.layers],
        "surface": {key:getattr(ts,key) for key in ("anisotropy","coat_weight","coat_roughness")},
    }
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
