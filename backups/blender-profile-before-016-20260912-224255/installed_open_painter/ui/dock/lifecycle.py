"""Draw handlers and input lifecycle, removable without modifying other workspaces."""

from time import monotonic

import bpy
from bpy.app.handlers import persistent

from . import renderer, state

_handlers = []
_display = {}


def tick():
    from .. import editor
    from .interaction import configure

    for window in bpy.context.window_manager.windows:
        editor.scan(window)
        # Blender can remove an area-bound modal handler when the editor changes
        # or a screen is replaced. A Python set is not proof it is still alive.
        running = any(
            operator.bl_idname == "OPEN_PAINTER_OT_dock_events"
            for operator in window.modal_operators
        )
        if running:
            state.windows.add(window.as_pointer())
        else:
            state.windows.discard(window.as_pointer())
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
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region:
                    with bpy.context.temp_override(window=window, area=area, region=region):
                        # Native regions must never cover our docks. configure
                        # only writes changed flags; custom dock visibility lives
                        # separately and is not reset here.
                        configure(area)
                        if window.as_pointer() not in state.windows:
                            bpy.ops.open_painter.dock_events("INVOKE_DEFAULT")
    return 0.15


@persistent
def on_load(_):
    from .assets import clear_cache

    clear_cache()
    _display.clear()
    state.reset()


def register():
    if bpy.app.background:
        return
    for space in (bpy.types.SpaceView3D, bpy.types.SpaceImageEditor):
        _handlers.append((space, space.draw_handler_add(renderer.draw, (), "WINDOW", "POST_PIXEL")))
    bpy.app.timers.register(tick, first_interval=0.5, persistent=True)
    bpy.app.handlers.load_post.append(on_load)


def unregister():
    from . import assets, drawing

    assets.clear_cache()
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
    _display.clear()
    state.reset()
