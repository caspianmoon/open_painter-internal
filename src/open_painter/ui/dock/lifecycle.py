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

    for window in bpy.context.window_manager.windows:
        editor.scan(window)
        mirror.sync(window)
        if window.scene.open_painter.paint_image:
            from ...painting.paint_session import finish, synchronize
            from ...utils.logging import logger

            with bpy.context.temp_override(window=window):
                try:
                    synchronize(bpy.context)
                except Exception as exc:
                    finish(bpy.context, evaluate=False)
                    window.scene.open_painter.status = str(exc)
                    logger.error("Paint target: %s", exc)
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
    return 0.15


@persistent
def on_load(_):
    from .assets import clear_cache

    clear_cache()
    from ..native import _focus_pending
    _focus_pending.clear()
    _display.clear()
    state.reset()


def register():
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
    from ..native import _focus_pending
    _focus_pending.clear()
    _display.clear()
    state.reset()
