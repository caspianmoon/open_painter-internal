"""Apply validated stacks with fresh IDs and independent packed image assets."""

from pathlib import Path
from tempfile import TemporaryDirectory

import bpy

from ..constants import OWNER
from ..core.layer_stack import invalidate, restore
from ..utils.ids import new_id
from .format import read


def apply(ts, path: str | Path) -> None:
    data, assets = read(Path(path))
    mapping = {layer["id"]: new_id() for layer in data["layers"]}
    loaded = []
    start = len(ts.layers)
    old_surface = {key:getattr(ts,key) for key in ("anisotropy","coat_weight","coat_roughness")}
    try:
        with TemporaryDirectory(prefix="open-painter-import-") as directory:

            def decode(value):
                if isinstance(value, dict) and "asset" in value:
                    key = value["asset"]
                    if key not in assets:
                        raise ValueError(f"Missing smart material texture: {key}")
                    dest = Path(directory) / f"{new_id()}.exr"
                    dest.write_bytes(assets[key])
                    image = bpy.data.images.load(str(dest), check_existing=False)
                    loaded.append(image)
                    image[OWNER] = True
                    image.colorspace_settings.name = value.get("colorspace", "Non-Color")
                    image.pack()
                    image.use_fake_user = True
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
                for mask in values.get("masks", []):
                    mask["id"] = new_id()
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
        for key, value in data.get("surface", {}).items():
            if key in old_surface:
                setattr(ts,key,value)
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
