"""Native paint targeting, material-slot isolation and raster change detection."""

from time import monotonic

import bpy
import numpy as np

from ..core.image_manager import pixels
from ..core.layer_stack import active, channel_for
from ..core.project_manager import active_set
from .brush_bridge import ensure_brush
from .channel_paint import mark_changed, target

_checks = {}
_rasters = {}
_scratch = {}


def restore_faces(project) -> None:
    """Restore the artist's face selection, including after a save/reload."""
    obj = project.paint_object
    if obj and obj.type == "MESH":
        mesh = obj.data
        saved = project.paint_face_selection
        if len(saved) == len(mesh.polygons):
            mesh.polygons.foreach_set("select", [value == "1" for value in saved])
        mesh.use_paint_mask = project.paint_original_face_mask
        mesh.update()
    project.paint_face_selection = ""
    project.paint_object = None


def restrict_faces(project, obj, ts) -> None:
    """Only assigned material-slot faces may receive a 3D stroke."""
    restore_faces(project)
    mesh = obj.data
    project.paint_object = obj
    project.paint_original_face_mask = mesh.use_paint_mask
    project.paint_face_selection = "".join("1" if p.select else "0" for p in mesh.polygons)
    slots = {r.slot_index for r in ts.assignments if r.object == obj}
    mesh.polygons.foreach_set("select", [p.material_index in slots for p in mesh.polygons])
    mesh.use_paint_mask = True
    mesh.update()


def paintable(ts, layer, mask: bool) -> bool:
    if layer is None or not layer.visible:
        return False
    by_id = {item.id: item for item in ts.layers}
    cursor, seen = layer.parent_id, {layer.id}
    while cursor:
        parent = by_id.get(cursor)
        if parent is None or cursor in seen or not parent.visible:
            return False
        seen.add(cursor)
        cursor = parent.parent_id
    if mask:
        return bool(
            layer.masks
            and layer.masks[min(max(0, layer.active_mask), len(layer.masks) - 1)].source == "PAINT"
        )
    return layer.type == "PAINT"


def update_editors(context, image, painting: bool = True) -> None:
    """Update painter areas, without changing unrelated image editors or viewports."""
    from ..ui.editor import is_area

    for area in context.screen.areas if context.screen else []:
        if area.type == "IMAGE_EDITOR" and (is_area(area) or area == context.area):
            area.spaces.active.image = image
            area.spaces.active.ui_mode = "PAINT" if painting else "VIEW"
            area.tag_redraw()


def begin(context: bpy.types.Context, ts, channel: str, mask: bool = False) -> bpy.types.Image:
    layer = active(ts)
    if not paintable(ts, layer, mask):
        raise ValueError("Select a visible paint layer or painted mask first.")
    obj = context.object
    assigned = [r.object for r in ts.assignments if r.object]
    if obj not in assigned:
        obj = next(iter(assigned), None)
    if obj is None:
        raise ValueError("Assign a UV-unwrapped mesh to this texture set first.")
    if not obj.data.uv_layers:
        raise ValueError(f"No UV map found on {obj.name}.")
    if obj.name not in context.view_layer.objects or not obj.visible_get():
        raise ValueError(f"Show {obj.name} in the active view layer before painting.")
    refs = [r for r in ts.assignments if r.object == obj]
    if any(
        r.slot_index >= len(obj.material_slots)
        or obj.material_slots[r.slot_index].material != ts.material
        for r in refs
    ):
        raise ValueError("Texture-set material slots changed. Repair or reassign the mesh first.")
    project = context.scene.open_painter
    # Flush the old target before changing channel or mask identity.
    detect(context.scene, force=True)
    image = target(ts, layer, channel, mask)
    if context.object and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)
    obj.active_material_index = refs[0].slot_index
    paint = context.scene.tool_settings.image_paint
    paint.mode = "IMAGE"
    paint.canvas = image
    try:
        bpy.ops.object.mode_set(mode="TEXTURE_PAINT")
        ensure_brush(context)
        restrict_faces(project, obj, ts)
    except Exception:
        finish(context, evaluate=False)
        raise
    project.paint_image = image
    project.paint_set_id = ts.id
    project.paint_layer_id = layer.id
    project.paint_channel = channel
    project.paint_target_channel = channel
    project.paint_mask = mask
    project.paint_mask_id = (
        layer.masks[min(max(0, layer.active_mask), len(layer.masks) - 1)].id if mask else ""
    )
    _checks[context.scene.as_pointer()] = (monotonic(), None)
    _rasters[context.scene.as_pointer()] = (image.as_pointer(), pixels(image))
    update_editors(context, image)
    return image


def finish(context, evaluate: bool = True) -> None:
    """Flush the actual session target even if the active texture set changed."""
    scene = context.scene
    project = scene.open_painter
    detect(scene, force=True)
    identifier = project.paint_set_id
    image = project.paint_image
    restore_faces(project)
    if context.object and context.object.mode == "TEXTURE_PAINT":
        bpy.ops.object.mode_set(mode="OBJECT")
    paint = scene.tool_settings.image_paint
    if paint.canvas == image:
        paint.canvas = None
    project.paint_image = None
    project.paint_set_id = ""
    project.paint_layer_id = ""
    project.paint_mask_id = ""
    project.paint_target_channel = ""
    project.paint_mask = False
    _checks.pop(scene.as_pointer(), None)
    _rasters.pop(scene.as_pointer(), None)
    _scratch.pop(scene.as_pointer(), None)
    if evaluate and any(s.id == identifier for s in project.texture_sets):
        from ..evaluation.evaluator import LayerEvaluator

        LayerEvaluator(scene).evaluate_texture_set(identifier)
    ts = active_set(context)
    preview = (
        next((out.image for out in ts.outputs if out.channel == project.paint_channel), None)
        if ts
        else None
    )
    update_editors(context, preview, painting=False)


def synchronize(context, mask: bool | None = None) -> bool:
    """Follow selection before a new stroke; stop on non-paintable content."""
    project = context.scene.open_painter
    if not project.paint_image:
        return False
    if not context.object or context.object.mode != "TEXTURE_PAINT":
        finish(context, evaluate=False)
        return False
    ts = active_set(context)
    layer = active(ts) if ts else None
    mask = project.paint_mask if mask is None else mask
    if not ts or not paintable(ts, layer, mask):
        finish(context, evaluate=False)
        return False
    selected_mask = (
        layer.masks[min(max(0, layer.active_mask), len(layer.masks) - 1)] if mask else None
    )
    channel = channel_for(layer, project.paint_channel) if not mask else None
    expected = selected_mask.image if mask else channel.image if channel else None
    mask_id = selected_mask.id if mask else ""
    if (
        project.paint_set_id,
        project.paint_layer_id,
        project.paint_target_channel,
        project.paint_mask_id,
    ) == (ts.id, layer.id, project.paint_channel, mask_id):
        if not expected or (channel and not channel.enabled):
            finish(context, evaluate=False)
            return False
        if expected == project.paint_image and context.object == project.paint_object:
            return True
    begin(context, ts, project.paint_channel, mask)
    return True


def detect(scene: bpy.types.Scene, force: bool = False) -> bool:
    project = scene.open_painter
    image = project.paint_image
    if not image:
        return False
    key = scene.as_pointer()
    now = monotonic()
    last, _ = _checks.get(key, (0, None))
    if not force and (now - last < 0.75 or not image.is_dirty):
        return False
    raster = pixels(image, _scratch.pop(key, None))
    previous = _rasters.get(key)
    if previous and previous[0] == image.as_pointer():
        _scratch[key] = previous[1]
    _rasters[key] = (image.as_pointer(), raster)
    _checks[key] = (now, None)
    rows = None
    if previous and previous[0] == image.as_pointer() and previous[1].shape == raster.shape:
        # Bound temporary comparison storage to 256 rows rather than another
        # full-resolution RGBA raster on every live paint check.
        changed = np.zeros(raster.shape[0], dtype=bool)
        for start in range(0, raster.shape[0], 256):
            stop = start + 256
            changed[start:stop] = np.any(raster[start:stop] != previous[1][start:stop], axis=(1, 2))
        rows = np.flatnonzero(changed)
        if not len(rows):
            return False
    ts = next((s for s in project.texture_sets if s.id == project.paint_set_id), None)
    layer = (
        next((item for item in ts.layers if item.id == project.paint_layer_id), None)
        if ts
        else None
    )
    if layer:
        from ..evaluation import cache

        channel = project.paint_target_channel or project.paint_channel
        output = next((out for out in ts.outputs if out.channel == channel), None)
        old_region = cache._regions.get(cache.key_for(scene, ts))
        regional = (
            rows is not None
            and image.colorspace_settings.name != "sRGB"
            and output
            and not project.paint_mask_id
            and not project.paint_mask
            and raster.shape[:2] == (int(ts.resolution), int(ts.resolution))
            and (
                not output.dirty
                or (old_region and old_region[0] == ts.edit_serial and old_region[1] == channel)
            )
        )
        mark_changed(
            ts,
            layer,
            project.paint_target_channel or project.paint_channel,
            bool(project.paint_mask_id) or project.paint_mask,
        )
        if regional:
            low, high = int(rows[0]), int(rows[-1]) + 1
            if old_region and old_region[1] == channel:
                low, high = min(low, old_region[2]), max(high, old_region[3])
            cache._regions[cache.key_for(scene, ts)] = (ts.edit_serial, channel, low, high, image.as_pointer(), raster)
        else:
            cache._regions.pop(cache.key_for(scene, ts), None)
        return True
    return False


def reset():
    _checks.clear()
    _rasters.clear()
    _scratch.clear()
