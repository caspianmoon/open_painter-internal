"""Clickable material and brush catalog for the docked asset browser."""

from pathlib import Path
from time import monotonic

_catalog = {}
_previews = {}


def clear_cache():
    _catalog.clear()
    _previews.clear()


def preview(name):
    import bpy

    if name not in _previews:
        path = (
            Path(__file__).resolve().parents[2]
            / "assets/previews"
            / (name.replace(" ", "_") + ".png")
        )
        _previews[name] = (
            bpy.data.images.load(str(path), check_existing=True) if path.exists() else None
        )
        if _previews[name]:
            # Rendered PNG thumbnails already contain the display transform.
            # The UI shader needs these display values, not a second linearization.
            _previews[name].colorspace_settings.name = "Non-Color"
    image = _previews[name]
    try:
        return image if image and image.name in bpy.data.images else None
    except ReferenceError:
        _previews.pop(name, None)
        return None


MATERIALS = [
    ("Fine Wood", "FILL", None, (0.4, 0.2, 0.08), 0.5, "wood"),
    ("Weathered Paint", "FILL", None, (0.2, 0.25, 0.2), 0.7, "plain"),
    ("Painted Steel", "SMART", "Painted_Steel", (0.08, 0.35, 0.6), 0.3, "steel"),
    ("Dirty Plastic", "SMART", "Dirty_Plastic", (0.36, 0.16, 0.055), 0.7, "dirt"),
    ("Rough Wood", "SMART", "Rough_Wood", (0.45, 0.23, 0.08), 0.9, "wood"),
    ("Brushed Steel", "FILL", (0.28, 0.31, 0.34, 0.28, 1), (0.55, 0.58, 0.62), 0.28, "plain"),
    ("Copper", "FILL", (0.55, 0.22, 0.09, 0.25, 1), (0.75, 0.38, 0.18), 0.25, "plain"),
    ("Matte Rubber", "FILL", (0.018, 0.02, 0.025, 0.88, 0), (0.12, 0.13, 0.15), 0.88, "plain"),
    ("Porcelain", "FILL", (0.78, 0.76, 0.70, 0.18, 0), (0.84, 0.82, 0.76), 0.18, "plain"),
    ("Red Paint", "FILL", (0.48, 0.025, 0.018, 0.35, 0), (0.7, 0.12, 0.09), 0.35, "plain"),
]
BRUSHES = [
    ("Paint Hard", "BRUSH", "Paint Hard", (0.8, 0.8, 0.8), 0.2, "plain"),
    ("Paint Soft", "BRUSH", "Paint Soft", (0.65, 0.65, 0.65), 0.9, "plain"),
    ("Airbrush", "BRUSH", "Airbrush", (0.7, 0.7, 0.7), 1, "plain"),
    ("Erase Soft", "BRUSH", "Erase Soft", (0.3, 0.3, 0.3), 0.8, "plain"),
    ("Fill", "BRUSH", "Fill", (0.9, 0.9, 0.9), 0.5, "plain"),
    ("Blur", "BRUSH", "Blur", (0.45, 0.45, 0.45), 1, "plain"),
]


def items(context, state):
    if state.tab == "BRUSHES":
        values = BRUSHES
    elif state.tab == "MASKS":
        from ...smart_masks.library import PRESETS

        values = [(p[0], "SMART_MASK", p[0], (.5, .5, .5), .7, "dirt") for p in PRESETS]
    elif state.tab == "ALPHAS":
        import bpy

        values = [
            (texture.name, "ALPHA", texture.name, (0.65, 0.65, 0.65), 0.9, "dirt")
            for texture in bpy.data.textures
        ]
        values.insert(0, ("No Alpha", "ALPHA", "", (0.8, 0.8, 0.8), 0.4, "plain"))
    else:
        values = [entry for entry in MATERIALS if state.tab == "MATERIALS" or entry[1] == "SMART"]
        from ...smart_materials.library import directory

        key = "files"
        stamp, files = _catalog.get(key, (0, []))
        if monotonic() - stamp > 2:
            folder = directory(context)
            files = sorted(folder.glob("*.openpaintmaterial"))
            _catalog[key] = (monotonic(), files)
        values = values + [
            (p.stem.replace("_", " "), "FILE", str(p), (0.3, 0.4, 0.5), 0.5, "plain") for p in files
        ]
    return [item for item in values if state.query.lower() in item[0].lower()]


def apply(context, entry):
    import bpy

    from ...core.layer_stack import add, channel_for
    from ...core.project_manager import require_set
    from ...painting.brush_bridge import ensure_brush
    from ...smart_materials.loader import apply as load

    name, kind, value, *_ = entry
    if kind == "SMART_MASK":
        from ...smart_masks.library import apply as apply_mask
        apply_mask(require_set(context), name)
        return
    if (
        name
        in {
            "Painted Steel",
            "Dirty Plastic",
            "Rough Wood",
            "Fine Wood",
            "Weathered Paint",
            "Brushed Steel",
            "Copper",
            "Matte Rubber",
            "Porcelain",
            "Red Paint",
        }
        and kind != "FILE"
    ):
        from ...core.surface_presets import apply as apply_surface
        from ...aging import recipes

        apply_surface(require_set(context), name)
        recipes.adopt(require_set(context), name)      # the aging knows what it is (0.10.1)
    elif kind in {"SMART", "FILE"}:
        ts = require_set(context)
        path = (
            Path(__file__).resolve().parents[2]
            / "assets/smart_materials"
            / f"{value}.openpaintmaterial"
            if kind == "SMART"
            else Path(value)
        )
        before = {item.id for item in ts.layers}
        load(ts, path)
        from ...core.layer_stack import group

        group(ts, {item.id for item in ts.layers if item.id not in before}, name)
        if not ts.aging.folder_id:
            # A document saved with aging restored its own settings; otherwise its name says what it is.
            from ...aging import recipes
            recipes.adopt(ts, name.replace("_", " "))
    elif kind == "FILL":
        layer = add(require_set(context), "FILL", name)
        channel_for(layer, "BASE_COLOR").color = (*value[:3], 1)
        channel_for(layer, "ROUGHNESS").value = value[3]
        channel_for(layer, "METALLIC").value = value[4]
    elif kind == "BRUSH":
        if context.object is None or context.object.mode != "TEXTURE_PAINT":
            # A brush paints whatever the artist selected: the mask button in
            # the layer row is UI state, not a project property the operator sees.
            from ...core.layer_stack import active
            from . import state

            ui = state.get(context.area) if context.area else None
            layer = active(require_set(context))
            bpy.ops.open_painter.paint(
                mask=bool(ui and ui.mask_selected and layer is not None and layer.masks)
            )
        bpy.ops.brush.asset_activate(
            asset_library_type="ESSENTIALS",
            relative_asset_identifier=f"brushes/essentials_brushes-mesh_texture.blend/Brush/{value}",
        )
        ensure_brush(context)
    else:
        brush = ensure_brush(context)
        brush.mask_texture = bpy.data.textures.get(value) if value else None
        if brush.mask_texture:
            brush.mask_texture_slot.mask_map_mode = "VIEW_PLANE"
