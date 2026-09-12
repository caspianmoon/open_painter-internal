"""Original PBR surface presets with consistent color, roughness and microheight maps."""

import numpy as np
import json
from pathlib import Path
from functools import lru_cache
import bpy

from ..generators.noise import noise
from . import image_manager, layer_stack

# Linear reflectance values; metalness separates conductor and dielectric behavior.
SURFACES = {
    "Brushed Steel": ((0.56, 0.57, 0.58), 0.29, 1.0),
    "Copper": ((0.90, 0.48, 0.28), 0.27, 1.0),
    "Matte Rubber": ((0.012, 0.014, 0.016), 0.74, 0.0),
    "Porcelain": ((0.72, 0.70, 0.65), 0.15, 0.0),
    "Red Paint": ((0.32, 0.012, 0.008), 0.29, 0.0),
    "Rough Wood": ((0.22, 0.085, 0.028), 0.56, 0.0),
}


def apply(ts, name):
    if name in {"Rough Wood", "Fine Wood", "Weathered Paint"}:
        return scanned(ts, name)
    if name in {"Painted Steel", "Dirty Plastic"}:
        return layered(ts, name)
    color, roughness, metal = SURFACES[name]
    previous = layer_stack.active(ts)
    parent = (
        previous.id
        if previous and previous.type == "FOLDER"
        else previous.parent_id
        if previous
        else ""
    )
    layer = layer_stack.add(ts, "FILL", name)
    layer.parent_id = parent
    n = 512
    u, v = np.meshgrid(
        (np.arange(n, dtype=np.float32) + 0.5) / n, (np.arange(n, dtype=np.float32) + 0.5) / n
    )
    fine = noise(u, v, 170, 17) - 0.5
    broad = noise(u, v, 7, 31) - 0.5
    detail = fine
    variation = np.ones_like(u)
    height = fine * 0.001
    if name == "Brushed Steel":
        detail = noise(u * 0.035, v, 220, 7) - 0.5
        height = detail * 0.0005
    elif name == "Rough Wood":
        warp = noise(u, v, 5, 11) * 0.035
        grain = noise((u + warp) * 5, v * 0.08, 35, 23)
        rings = np.sin((u + warp) * np.pi * 16) * 0.5 + 0.5
        detail = grain * 0.6 + noise(u * 18, v, 12, 5) * 0.4 - 0.5
        variation = 0.80 + 0.22 * grain + 0.08 * rings + broad * 0.12
        height = detail * 0.003
    elif name == "Porcelain":
        height = fine * 0.0015
    elif name == "Red Paint":
        height = (fine * 0.6 + broad * 0.4) * 0.005
    layer_stack.channel_for(layer, "METALLIC").value = metal
    for key, data in (
        ("BASE_COLOR", variation[..., None] * np.asarray(color)),
        (
            "ROUGHNESS",
            np.clip(roughness + detail * (0.06 if metal else 0.08), 0.04, 0.95)[..., None],
        ),
        ("HEIGHT", (0.5 + height)[..., None]),
    ):
        rgba = np.ones((n, n, 4), dtype=np.float32)
        rgba[..., :3] = data
        prop = layer_stack.channel_for(layer, key)
        prop.enabled = True
        prop.image = image_manager.create(
            f"{ts.name}_{name}_{key}", n, color_data=key == "BASE_COLOR"
        )
        image_manager.write(prop.image, rgba)
    # A surface preset starts with a flat tangent normal, preventing unrelated
    # earlier materials' normal details from leaking into the selected surface.
    layer_stack.channel_for(layer, "NORMAL").enabled = True
    layer_stack.channel_for(layer, "AO").enabled = True
    return layer


def layered(ts, name):
    from ..utils.ids import new_id

    previous = layer_stack.active(ts)
    parent_id = (
        previous.id
        if previous and previous.type == "FOLDER"
        else previous.parent_id
        if previous
        else ""
    )
    group = layer_stack.group(ts, set(), name)
    identifier = group.id
    group.parent_id = parent_id
    base = apply(ts, "Brushed Steel" if name == "Painted Steel" else "Matte Rubber")
    base.name = "Metal substrate" if name == "Painted Steel" else "Plastic substrate"
    coat = layer_stack.add(ts, "FILL", "Paint coat" if name == "Painted Steel" else "Surface dust")
    coat.parent_id = identifier
    color = (0.018, 0.12, 0.24, 1) if name == "Painted Steel" else (0.075, 0.068, 0.057, 1)
    layer_stack.channel_for(coat, "BASE_COLOR").color = color
    layer_stack.channel_for(coat, "ROUGHNESS").value = 0.32 if name == "Painted Steel" else 0.76
    layer_stack.channel_for(coat, "METALLIC").value = 0
    n = 512
    u, v = np.meshgrid(
        (np.arange(n, dtype=np.float32) + 0.5) / n, (np.arange(n, dtype=np.float32) + 0.5) / n
    )
    pattern = noise(u, v, 18, 19) * 0.7 + noise(u, v, 83, 5) * 0.3
    coverage = (
        np.clip((pattern - 0.24) * 18, 0, 1)
        if name == "Painted Steel"
        else np.clip((pattern - 0.58) * 1.4, 0, 0.35)
    )
    mask = coat.masks.add()
    mask.id, mask.name, mask.source = new_id(), "Coating coverage", "FILL"
    mask.projection = "TRIPLANAR"
    mask.image = image_manager.create(name + "_Coverage", n)
    rgba = np.ones((n, n, 4), dtype=np.float32)
    rgba[..., :3] = coverage[..., None]
    image_manager.write(mask.image, rgba)
    ts.active_layer = next(i for i, item in enumerate(ts.layers) if item.id == identifier)
    return ts.layers[ts.active_layer]


@lru_cache(maxsize=1)
def surface_catalog():
    root = Path(__file__).resolve().parents[1] / "assets/surfaces"
    return {entry["name"]: entry for entry in json.loads((root / "catalog.json").read_text())}


def scanned(ts, name):
    """CC0 maps, bundled offline and packed into the artist's project on use."""
    root = Path(__file__).resolve().parents[1] / "assets/surfaces"
    entry = surface_catalog()[name]
    previous = layer_stack.active(ts)
    parent = previous.id if previous and previous.type == "FOLDER" else previous.parent_id if previous else ""
    layer = layer_stack.add(ts, "FILL", name, parent_id=parent)
    for channel in layer.channels:
        channel.enabled = True
    for key, record in entry["maps"].items():
        image = bpy.data.images.load(str(root / record["file"]), check_existing=True)
        image.colorspace_settings.name = "sRGB" if key == "BASE_COLOR" else "Non-Color"
        if not image.packed_file:image.pack()
        layer_stack.channel_for(layer, key).image = image
    layer_stack.channel_for(layer, "METALLIC").value = 0
    return layer
