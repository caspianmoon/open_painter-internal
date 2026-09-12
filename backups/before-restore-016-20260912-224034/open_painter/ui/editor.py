"""Dedicated Blender window hosting the Painter interface and native renderer."""

import bpy
from bpy.app.handlers import persistent

EDITOR_ID = "OpenPainterEditor"
_active = {}
_previous = {}
_spaces = {}


def is_area(area):
    return (
        area is not None
        and area.as_pointer() in _active
        and area.type in {"VIEW_3D", "IMAGE_EDITOR"}
    )


def mount(window, area):
    """Adapt only the chosen area to Blender's native painting backend."""
    key = area.as_pointer()
    if key not in _active:
        _active[key] = "VIEW_3D"
        _previous[key] = "VIEW_3D"
    area.type = "VIEW_3D"
    _spaces[key] = area.spaces.active.as_pointer()
    area.spaces.active.shading.type = "MATERIAL"
    area.spaces.active.overlay.show_overlays = False
    from .dock.interaction import configure

    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    if region:
        with bpy.context.temp_override(window=window, area=area, region=region):
            configure(area)
    persist(window.screen)


def close(context):
    from ..painting.paint_session import finish

    if context.scene.open_painter.paint_image:
        finish(context)
    window = context.window
    area = context.area
    from .paired_views import members, single

    if members(context.window, area):
        area = single(context, "VIEW_3D")
    key = area.as_pointer()
    _active.pop(key, None)
    _spaces.pop(key, None)
    area.type = _previous.pop(key, "VIEW_3D")
    space = area.spaces.active
    for prop in ("show_region_header", "show_region_toolbar"):
        if hasattr(space, prop):
            setattr(space, prop, True)
    persist(window.screen)
    area.tag_redraw()


def set_view(area, backend):
    _active[area.as_pointer()] = backend
    # set_view is called before the area changes; scan records the new space.
    _spaces.pop(area.as_pointer(), None)


def persist(screen):
    from .dock import state

    screen["open_painter_areas"] = [
        {
            "index": index,
            "backend": _active[area.as_pointer()],
            "pair_id": state.get(area).pair_id,
            "view_role": state.get(area).view_role,
            "show_assets": state.get(area).show_assets,
            "show_properties": state.get(area).show_properties,
        }
        for index, area in enumerate(screen.areas)
        if area.as_pointer() in _active
    ]


def scan(window):
    from .dock import state

    if not window.screen.get("open_painter_window"):
        return
    # Maximizing swaps the native Space into another Area. Follow the Space,
    # carrying panel state, rather than abandoning the editor at the old pointer.
    for area in window.screen.areas:
        key = area.as_pointer()
        owner = next(
            (
                old
                for old, space in _spaces.items()
                if space == area.spaces.active.as_pointer() and old != key
            ),
            None,
        )
        if owner is not None:
            _active[key] = _active.pop(owner)
            _previous[key] = _previous.pop(owner, "VIEW_3D")
            _spaces[key] = _spaces.pop(owner)
            if owner in state.states:
                state.states[key] = state.states.pop(owner)
    for area in list(window.screen.areas):
        key = area.as_pointer()
        if key in _active and area.type != _active[key]:
            _active.pop(key, None)
            _previous.pop(key, None)
            _spaces.pop(key, None)
            persist(window.screen)
        elif key in _active:
            _spaces[key] = area.spaces.active.as_pointer()


@persistent
def save(_):
    for screen in bpy.data.screens:
        persist(screen)


@persistent
def load(_):
    _active.clear()
    _previous.clear()
    _spaces.clear()
    for screen in bpy.data.screens:
        if not screen.get("open_painter_window"):
            # Retire older in-area ownership without mounting anything in the main window.
            for record in screen.get("open_painter_areas", []):
                i = record.get("index", -1)
                if 0 <= i < len(screen.areas):
                    space = screen.areas[i].spaces.active
                    for flag in ("show_region_toolbar", "show_region_ui"):
                        if hasattr(space, flag):
                            setattr(space, flag, True)
            if "open_painter_areas" in screen:
                del screen["open_painter_areas"]
            continue
        for record in screen.get("open_painter_areas", []):
            index = record.get("index", -1)
            if 0 <= index < len(screen.areas):
                area = screen.areas[index]
                if area.type == record.get("backend"):
                    _active[area.as_pointer()] = area.type
                    _previous[area.as_pointer()] = "VIEW_3D"
                    _spaces[area.as_pointer()] = area.spaces.active.as_pointer()
                    from .dock import state

                    ui = state.get(area)
                    for key in ("pair_id", "view_role", "show_assets", "show_properties"):
                        if key in record:
                            setattr(ui, key, record[key])


def header(self, context):
    if is_area(context.area):
        self.layout.label(text="Open Painter", icon="BRUSH_DATA")


class OP_OT_open_editor(bpy.types.Operator):
    bl_idname = "open_painter.open_editor"
    bl_label = "Open Painter Window"
    bl_description = "Open the Painter interface in a separate window"
    view_mode: bpy.props.EnumProperty(
        items=[("BOTH", "3D + 2D", ""), ("SINGLE", "3D", "")], default="BOTH"
    )

    def execute(self, context):
        if bpy.app.background:
            self.report({"ERROR"}, "Open Painter requires a Blender window.")
            return {"CANCELLED"}
        area = context.area or next(iter(context.screen.areas), None)
        if area is None:
            return {"CANCELLED"}
        before = {w.as_pointer() for w in context.window_manager.windows}
        with context.temp_override(area=area):
            bpy.ops.screen.area_dupli("INVOKE_DEFAULT")
        window = next(
            (w for w in context.window_manager.windows if w.as_pointer() not in before), None
        )
        if window is None:
            self.report({"ERROR"}, "Blender did not create the Painter window.")
            return {"CANCELLED"}
        window.screen["open_painter_window"] = True
        target = window.screen.areas[0]
        with context.temp_override(window=window, area=target):
            mount(window, target)
        # Window geometry settles after creation; do not split the launching area.
        if self.view_mode == "BOTH":
            pointer = window.as_pointer()

            def arrange():
                current = next(
                    (w for w in bpy.context.window_manager.windows if w.as_pointer() == pointer),
                    None,
                )
                if current:
                    from .paired_views import both

                    area = next((a for a in current.screen.areas if is_area(a)), None)
                    if area:
                        with bpy.context.temp_override(window=current, area=area):
                            both(bpy.context)
                return None

            bpy.app.timers.register(arrange, first_interval=0.2)
        return {"FINISHED"}


def window_menu(self, context):
    self.layout.operator("open_painter.open_editor", icon="BRUSH_DATA")


def register():
    bpy.app.handlers.load_post.append(load)
    bpy.app.handlers.save_pre.append(save)
    bpy.types.TOPBAR_MT_window.append(window_menu)
    bpy.types.VIEW3D_HT_header.prepend(header)
    bpy.types.IMAGE_HT_header.prepend(header)
    bpy.app.timers.register(restore_initial, first_interval=0.1)


def restore_initial():
    load(None)
    return None


def unregister():
    if bpy.app.timers.is_registered(restore_initial):
        bpy.app.timers.unregister(restore_initial)
    for handlers, fn in ((bpy.app.handlers.load_post, load), (bpy.app.handlers.save_pre, save)):
        if fn in handlers:
            handlers.remove(fn)
    bpy.types.TOPBAR_MT_window.remove(window_menu)
    bpy.types.VIEW3D_HT_header.remove(header)
    bpy.types.IMAGE_HT_header.remove(header)
    _active.clear()
    _previous.clear()
    _spaces.clear()
