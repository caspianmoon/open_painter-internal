"""View Mask: a temporary display of one layer's mask coverage (UI plan phase 2).

The display material shows a *mask snapshot* — the layer's mask stack evaluated once
by the CPU reference at a bounded resolution — as flat coverage (white where the
layer shows through), and *Back to Material* restores the shaded material. Nothing is
rasterised into the mask itself: the snapshot is a display image that says when it is
out of date and can be refreshed. The view is presentation state (per scene, never
saved); the texture set node's group shows the snapshot while a view is active
(``texture_set_node``), so the material itself never changes.
"""
import bpy
import numpy as np

from ..constants import OWNER

ROLE = "Mask View"
MAX_SIDE = 1024
_views = {}   # scene pointer -> dict(set_id, layer_id, image name, serial)


def _key(ts):
    return ts.id_data.as_pointer()


def active(ts):
    """The view record when ``ts`` is showing a mask snapshot, else None."""
    view = _views.get(_key(ts))
    if view is None or view["set_id"] != ts.id:
        return None
    return view


def viewed_layer(ts):
    view = active(ts)
    if view is None:
        return None
    return next((layer for layer in ts.layers if layer.id == view["layer_id"]), None)


def image_of(view):
    return bpy.data.images.get(view["image"]) if view else None


def shown_image(ts):
    """The snapshot the surface of ``ts`` shows (None without a view; a view whose image is gone ends)."""
    view = active(ts)
    if view is None:
        return None
    image = image_of(view)
    if image is None:
        _views.pop(_key(ts), None)
    return image


def stale(ts, view=None):
    view = view or active(ts)
    return view is not None and view["serial"] != ts.edit_serial


def _evaluate(ts, layer):
    """The mask stack as a display image at a bounded resolution (never the authored mask)."""
    from ..core import image_manager as images
    from ..evaluation.mask_eval import evaluate_masks
    from ..evaluation.references import issues
    errors = issues(ts)
    if errors:
        raise ValueError(" | ".join(errors))
    n = min(int(ts.resolution), MAX_SIDE)
    data = np.ones((n, n, 4), dtype=np.float32)
    cache, geometry_cache = {}, {}
    for y0 in range(0, n, 256):
        y1 = min(n, y0 + 256)
        coverage = evaluate_masks(ts, layer, n, y0, y1, cache, geometry_cache)
        data[y0:y1, :, :3] = coverage[..., None]
    name = f"OP · Mask View · {ts.name}"
    image = bpy.data.images.get(name)
    if image is None or tuple(image.size) != (n, n):
        if image is not None:
            bpy.data.images.remove(image)
        image = images.create(name, n)
        image.colorspace_settings.name = "Non-Color"
    image[OWNER] = True
    image["open_painter_mask_view"] = True
    images.write(image, data, pack=False)
    return image


def _show_2d(context, ts, image):
    """Painter 2D areas of this screen show the mask snapshot (or the composite again)."""
    from . import editor
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    project = context.scene.open_painter
    for area in screen.areas:
        if area.type == "IMAGE_EDITOR" and editor.is_area(area):
            space = area.spaces.active
            if image is None:
                from ..evaluation.composite_snapshot import for_view
                image = for_view(context.scene, ts, project.display_channel)
            if not project.paint_image:
                space.image = image
                space.ui_mode = "VIEW"
            area.tag_redraw()


def _repair(ts):
    from ..core import material_manager
    material_manager.repair(ts)


def enter(context, ts, layer):
    """Show ``layer``'s mask coverage on the model; returns the snapshot image."""
    if not layer.masks:
        raise ValueError(f"{layer.name} has no mask to view.")
    image = _evaluate(ts, layer)
    _views[_key(ts)] = dict(set_id=ts.id, layer_id=layer.id, image=image.name, serial=ts.edit_serial)
    _repair(ts)
    _show_2d(context, ts, image)
    _redraw(context)
    return image


def refresh(context, ts):
    view = active(ts)
    layer = viewed_layer(ts)
    if view is None or layer is None:
        raise ValueError("No mask is being viewed.")
    image = _evaluate(ts, layer)
    view["image"], view["serial"] = image.name, ts.edit_serial
    _repair(ts)
    _show_2d(context, ts, image)
    _redraw(context)
    return image


def leave(context, ts):
    """Back to Material: the shaded material returns, the snapshot image is released."""
    view = _views.pop(_key(ts), None)
    if view is None or view["set_id"] != ts.id:
        if view is not None:
            _views[_key(ts)] = view
        return False
    _repair(ts)
    _show_2d(context, ts, None)
    image = image_of(view)
    if image is not None and not image.users:
        bpy.data.images.remove(image)
    _redraw(context)
    return True


def _redraw(context):
    screen = getattr(context, "screen", None)
    if screen is not None:
        for area in screen.areas:
            area.tag_redraw()


def on_layer_removed(ts):
    """A viewed layer that is gone ends the view (a valid fallback, not a stale image)."""
    if active(ts) is not None and viewed_layer(ts) is None:
        _views.pop(_key(ts), None)
        _repair(ts)


def resync(scene):
    """After undo/redo: the document's links must agree with the view state."""
    from ..core.texture_set_node import showing_mask
    for ts in scene.open_painter.texture_sets:
        view = active(ts)
        if ts.material is None:
            continue
        linked = showing_mask(ts)
        if (view is not None) != linked or (view is not None and viewed_layer(ts) is None):
            if view is not None and viewed_layer(ts) is None:
                _views.pop(_key(ts), None)
            _repair(ts)


def clear():
    """File load: no view survives; materials saved mid-view are repaired on their next use."""
    _views.clear()
    from ..core.texture_set_node import showing_mask
    for scene in bpy.data.scenes:
        project = getattr(scene, "open_painter", None)
        if project is None:
            continue
        for ts in project.texture_sets:
            if ts.material is None:
                continue
            if showing_mask(ts):
                try:
                    _repair(ts)
                except Exception:
                    from ..utils.logging import logger
                    logger.debug("Mask view could not be cleared on load", exc_info=True)


class OP_OT_view_mask(bpy.types.Operator):
    bl_idname = "open_painter.view_mask"
    bl_label = "View Mask"
    bl_description = "Show the selected layer's mask coverage on the model as a snapshot; Back to Material restores the shaded view"
    bl_options = {"REGISTER"}
    action: bpy.props.EnumProperty(items=[
        ("VIEW", "View Mask", "Show the mask coverage as a snapshot"),
        ("BACK", "Back to Material", "Show the shaded material again"),
        ("REFRESH", "Refresh Mask Snapshot", "Evaluate the mask stack again")], default="VIEW")

    @classmethod
    def description(cls, context, properties):
        return {"VIEW": "Show the layer's mask coverage on the model (a snapshot: edits mark it out of date)",
                "BACK": "Show the shaded material again",
                "REFRESH": "Evaluate the mask stack again for the snapshot"}[properties.action]

    def execute(self, context):
        from ..core.layer_stack import active as active_layer
        from ..core.project_manager import active_set
        ts = active_set(context)
        if ts is None:
            self.report({"ERROR"}, "Create a texture set first.")
            return {"CANCELLED"}
        try:
            if self.action == "BACK":
                leave(context, ts)
            elif self.action == "REFRESH":
                refresh(context, ts)
            else:
                layer = active_layer(ts)
                if layer is None:
                    raise ValueError("Select a layer with a mask first.")
                enter(context, ts, layer)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


CLASSES = (OP_OT_view_mask,)
