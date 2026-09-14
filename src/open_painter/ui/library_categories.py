"""Asset types and small texture previews for the native Painter shelf."""

import bpy
import numpy as np

from ..constants import OWNER

# Keep the first three numeric values compatible with existing .blend files.
CATEGORY_ITEMS = [
    ("ALL", "All Types", "Show every Painter asset type", 0),
    ("MATERIALS", "Materials", "Single material fills", 1),
    ("SMART_MATERIALS", "Smart Materials", "Editable material layer stacks", 3),
    ("MASKS", "Smart Masks", "Mesh-aware mask presets", 2),
    ("FILTERS", "Filters", "Apply a filter to the selected channel or mask", 4),
    ("BRUSHES", "Brushes", "Texture painting brushes", 5),
    ("ALPHAS", "Alphas", "Brush alpha textures", 6),
    ("TEXTURES", "Textures", "Procedural and imported texture inputs", 7),
]
KIND_CATEGORY = {
    "FILL": "MATERIALS", "SMART": "SMART_MATERIALS",
    "SMART_RECIPE": "SMART_MATERIALS", "SMART_MASK": "MASKS",
    "FILTER": "FILTERS", "BRUSH": "BRUSHES", "ALPHA": "ALPHAS",
    "PROCEDURAL": "TEXTURES", "TEXTURE": "TEXTURES",
}
ASSET_CATEGORY_KEY = "open_painter_asset_category"
SOURCE_KEY = "open_painter_asset_source"


def key(entry):
    return entry[0] if entry[1] in {"FILL", "SMART", "SMART_RECIPE", "SMART_MASK"} else entry[1] + ":" + str(entry[2])


def extras():
    from ..texture_engine.filters import CATALOG as filters
    from ..texture_engine.procedurals import CATALOG as procedurals
    from .dock.assets import BRUSHES

    def item(label, kind, value):
        return (label, kind, value, (.5, .5, .5), .5, "")

    result = [item(name.replace("_", " ").title(), "FILTER", name) for name in filters]
    result += BRUSHES
    result += [item("No Alpha", "ALPHA", "")]
    result += [item(texture.name, "ALPHA", texture.name) for texture in bpy.data.textures
               if texture.type == "IMAGE" and texture.image and all(texture.image.size)]
    result += [item(name.replace("_", " ").title(), "PROCEDURAL", name) for name in procedurals]
    result += [item(image.name, "TEXTURE", image.name) for image in bpy.data.images
               if not image.library and not image.get("open_painter_catalog_entry")
               and not image.get(OWNER) and image.type in {"IMAGE", "UV_TEST"}
               and all(image.size)
               and (image.filepath or image.get("open_painter_library_texture"))]
    return result


def make_preview(entry, size=48):
    """Render actual procedural/filter pixels; brushes use a stroke diagram."""
    from ..texture_engine import procedurals, filters

    label, kind, value, *_ = entry
    y, x = np.mgrid[:size, :size].astype(np.float32)
    u, v = (x + .5) / size, (y + .5) / size
    if kind == "PROCEDURAL":
        if value == "FINGERPRINTS":
            # A recognizable single print needs enough samples per ridge.
            # This only antialiases the shelf preview; authored inputs retain
            # their full resolution, seed, density and numerical behavior.
            samples = 4
            yy, xx = np.mgrid[:size*samples, :size*samples].astype(np.float32)
            rgb = procedurals.evaluate(value, (xx+.5)/(size*samples),
                (yy+.5)/(size*samples), seed=7, scale=1, density=1)
            rgb = rgb.reshape(size, samples, size, samples).mean(axis=(1,3))
        else:
            rgb = procedurals.evaluate(value, u, v, seed=7, scale=6)
    elif kind == "FILTER":
        gray = .2 + .6 * ((np.sin(u * 16) * np.cos(v * 12)) > 0)
        source = np.stack((u, gray, v), axis=-1).astype(np.float32)
        params = {"field": procedurals.evaluate("FBM", u, v, scale=4),
                  "detail": np.broadcast_to((.5, .5, 1), source.shape)}
        if value in {"VECTOR_WARP", "UV_DISTORTION"}:
            params["field"] = np.stack((params["field"], 1 - params["field"]), axis=-1)
        filtered = filters.apply(source, value, **params)
        if filtered.ndim == 2:
            filtered = np.repeat(filtered[..., None], 3, axis=-1)
        rgb = np.where((u < .5)[..., None], source, filtered)
    elif kind == "BRUSH":
        width = .035 if value == "Paint Hard" else .065
        rgb = np.exp(-((v - .5 - .22 * np.sin(u * 8)) / width) ** 2)
    else:
        rgb = ((np.floor(u * 8) + np.floor(v * 8)) % 2) * .18 + .24
    if rgb.ndim == 2:
        rgb = np.repeat(rgb[..., None], 3, axis=-1)
    return np.concatenate((np.clip(rgb[..., :3], 0, 1), np.ones((size, size, 1))), axis=-1).astype(np.float32)


def refresh(self, context, *, refresh_sources=False):
    # This callback is an explicit UI action, never part of draw/asset polling.
    from .native import prepare_assets
    prepare_assets(refresh_sources=refresh_sources)
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "IMAGE_EDITOR"}:
                area.tag_redraw()


def activate(context, entry, source):
    from ..core.layer_stack import active, channel_for, invalidate
    from ..core.project_manager import require_set
    from .dock import state
    from .texture_operations import _mask

    ts = require_set(context)
    layer = active(ts)
    target = "MASK" if state.get(context.area).mask_selected else "CHANNEL"
    kind, value = entry[1:3]
    if kind == "FILTER":
        bpy.ops.open_painter.choose_filter(choice=value, target=target, create=True)
    elif kind == "PROCEDURAL":
        bpy.ops.open_painter.choose_procedural(choice=value, target=target, create=target == "MASK")
    elif kind == "TEXTURE":
        if not layer or not source:
            raise ValueError("Select a fill layer or mask and an available image.")
        if target == "MASK":
            prop = _mask(layer, create=True, source="FILL")
            prop.name = source.name
        else:
            if layer.type != "FILL":
                raise ValueError("Select a fill layer to assign a texture input.")
            prop = channel_for(layer, context.scene.open_painter.properties_channel)
            prop.enabled = True
            # Shelf activation replaces the fill source. Direct RNA image
            # assignment can still combine a base texture with scalar detail.
            prop.detail_image = None
            prop.detail_image_name = ""
        prop.image = source
        prop.procedural = ""
        invalidate(ts)
    elif kind == "ALPHA":
        from ..painting.brush_bridge import ensure_brush
        brush = ensure_brush(context)
        brush.mask_texture = source
        if source:
            brush.mask_texture_slot.mask_map_mode = "VIEW_PLANE"
    else:
        return False
    return True
