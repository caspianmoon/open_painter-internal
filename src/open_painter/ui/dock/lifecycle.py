"""Synchronize native Painter regions and paint targets without drawing overlays."""

from time import monotonic

import bpy
from bpy.app.handlers import persistent

from . import state

_handlers = []
_display = {}


def tick():
    from .. import editor
    from ..native import focus_area

    from ...graph import mirror
    from ...utils.logging import logger

    windows = list(bpy.context.window_manager.windows)
    for window in windows:
        # Blender drops a timer whose callback raises; this one is registered
        # once, so a failing window must not take editor mounting and paint
        # target following down with it.
        try:
            editor.scan(window)
            mirror.sync(window)
            if window is _paint_window(windows, window.scene):
                _synchronize(window)
            for area in window.screen.areas:
                if editor.is_area(area):
                    focus_area(window, area)
                    ui = state.get(area)
                    project = window.scene.open_painter
                    signature = (
                        project.active_texture_set,
                        project.status,
                        tuple(
                            (ts.id, ts.edit_serial, ts.active_layer, ts.dirty)
                            for ts in project.texture_sets
                        ),
                    )
                    key = area.as_pointer()
                    if _display.get(key) != signature or (
                        ui.hover and 0.7 < monotonic() - ui.hover_since < 0.95
                    ):
                        _display[key] = signature
                        area.tag_redraw()
        except Exception:
            logger.exception("Painter tick")
    return 0.15


def _paint_window(windows, scene):
    """The window that judges the scene's paint session, or None without one.

    Object mode is per object, so a second window on another view layer of the
    same scene would see the paint object as absent and end the session the
    artist is running next door. Prefer the window whose view layer has the
    paint object active; the first window on the scene still follows selection
    when none does.
    """
    project = scene.open_painter
    if not project.paint_image:
        return None
    shown = [window for window in windows if window.scene == scene]
    obj = project.paint_object
    return next(
        (window for window in shown if obj and window.view_layer.objects.active == obj),
        shown[0] if shown else None,
    )


def _synchronize(window):
    from ...painting.paint_session import finish, synchronize
    from ...utils.logging import logger

    project = window.scene.open_painter
    with bpy.context.temp_override(window=window):
        try:
            synchronize(bpy.context)
        except Exception as exc:
            try:
                finish(bpy.context, evaluate=False)
            except Exception:
                # finish() flushes the canvas that just failed to read; a canvas
                # without pixel data has nothing to flush, so drop the record and
                # tear down the rest rather than fail again on every tick.
                logger.exception("Paint target teardown")
                project.paint_image = None
                for entry in project.paint_canvases:
                    entry.image = None
                finish(bpy.context, evaluate=False)
            project.status = str(exc)
            logger.error("Paint target: %s", exc)


@persistent
def on_load(_):
    from .assets import clear_cache

    clear_cache()
    from ..native import _focus_pending
    _focus_pending.clear()
    _display.clear()
    state.reset()
    from .. import companion, mask_view
    from ...baking import history
    mask_view.clear()
    companion.forget()
    history.clear()


@persistent
def on_undo(_):
    # Memfile undo puts the stack back without update callbacks: a selection
    # or mask flag left pointing at an undone layer or mask would otherwise
    # group nothing or turn the next shelf click into a mask.
    from ...core.layer_stack import active

    from .. import mask_view
    for window in bpy.context.window_manager.windows:
        if not hasattr(window.scene, "open_painter"):
            continue
        try:
            mask_view.resync(window.scene)
        except Exception:
            from ...utils.logging import logger
            logger.debug("Mask view resync after undo", exc_info=True)
        ts = _active_set(window.scene)
        layer = active(ts) if ts else None
        existing = {item.id for item in ts.layers} if ts else set()
        for area in window.screen.areas:
            ui = state.states.get(area.as_pointer())
            if ui is None:
                continue
            ui.selected_layers = {identifier for identifier in ui.selected_layers if identifier in existing} \
                or ({layer.id} if layer else set())
            if layer is None or not layer.masks:
                ui.mask_selected = False


def _active_set(scene):
    project = scene.open_painter
    index = project.active_texture_set
    return project.texture_sets[index] if 0 <= index < len(project.texture_sets) else None


def register():
    # Handlers are worth having in background too: scripts undo like artists do.
    bpy.app.handlers.undo_post.append(on_undo)
    bpy.app.handlers.redo_post.append(on_undo)
    if bpy.app.background:
        return
    bpy.app.timers.register(tick, first_interval=0.5, persistent=True)
    bpy.app.handlers.load_post.append(on_load)


def unregister():
    from . import assets, drawing

    assets.clear_cache()
    drawing.rectangle_batch.cache_clear()
    drawing.tool_batch.cache_clear()
    drawing.rounded_batch.cache_clear()
    drawing.shaded_batch.cache_clear()
    drawing.image_batch.cache_clear()
    for space, handler in _handlers:
        space.draw_handler_remove(handler, "WINDOW")
    _handlers.clear()
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    if on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        while on_undo in handlers:
            handlers.remove(on_undo)
    from ..native import _focus_pending
    _focus_pending.clear()
    _display.clear()
    state.reset()
