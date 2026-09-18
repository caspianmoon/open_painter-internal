"""Editor Type selector entry and per-area native painting adapter.

Blender exposes Python editor subtypes through NodeTree registration. Selecting
this subtype mounts the native paint viewport in the same area; no workspace or
screen layout is replaced. Native brush behavior and document data are retained.
"""

import bpy
from bpy.app.handlers import persistent

EDITOR_ID = "OpenPainterEditor"
_active = {}
_previous = {}
_spaces = {}


class OP_EditorType(bpy.types.NodeTree):
    """Open the Open Painter PBR texturing interface in this editor area."""

    bl_idname = EDITOR_ID
    bl_label = "Open Painter"
    bl_icon = "BRUSH_DATA"


def is_area(area):
    return (
        area is not None
        and area.as_pointer() in _active
        and area.type in {"VIEW_3D", "IMAGE_EDITOR"}
    )


def mount(window, area):
    """Adapt only the chosen area to Blender's native painting backend."""
    key = area.as_pointer()
    # Re-picking the editor type from the 2D backend must land on the 3D
    # backend, or scan's type check drops the area it just mounted.
    _active[key] = "VIEW_3D"
    _previous.setdefault(key, "VIEW_3D")
    area.type = "VIEW_3D"
    _spaces[key] = area.spaces.active.as_pointer()
    area.spaces.active.shading.type = "MATERIAL"
    # Overlays (grid, outlines, gizmos, the brush cursor's helpers) stay as the artist
    # left them: the editor only asks for material shading.
    from .native import prepare_assets, request_focus

    prepare_assets()
    request_focus(area)

    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    if region:
        with bpy.context.temp_override(window=window, area=area, region=region):
            for prop in (
                "show_region_ui",
                "show_region_toolbar",
                "show_region_tool_header",
                "show_region_asset_shelf",
            ):
                if hasattr(area.spaces.active, prop):
                    setattr(area.spaces.active, prop, True)

    if window.scene.open_painter.paint_image is None:
        area.spaces.active.show_region_toolbar = False
    window.screen["open_painter_ui_revision"] = 14
    window.screen["open_painter_native_ui"] = True
    persist(window.screen)


def close(context):
    from ..painting.paint_session import finish

    if context.scene.open_painter.paint_image:
        finish(context)
    area = context.area
    key = area.as_pointer()
    _active.pop(key, None)
    _spaces.pop(key, None)
    area.type = _previous.pop(key, "VIEW_3D")
    space = area.spaces.active
    for prop in ("show_region_header", "show_region_toolbar"):
        if hasattr(space, prop):
            setattr(space, prop, True)
    persist(context.screen)
    area.tag_redraw()


def set_view(area, backend):
    _active[area.as_pointer()] = backend
    # set_view is called before the area changes; scan records the new space.
    _spaces.pop(area.as_pointer(), None)


def persist(screen):
    screen["open_painter_areas"] = [
        {"index": index, "backend": _active[area.as_pointer()]}
        for index, area in enumerate(screen.areas)
        if area.as_pointer() in _active
    ]


def prune():
    """Forget areas Blender freed behind the add-on's back.

    Close Area and Join never go through close(); a later Split reuses the
    freed ScrArea and Space addresses, and a stale key would make the new,
    ordinary viewport a Painter area. Inactive workspaces keep their entries:
    save() persists every screen, not only the ones a window shows.
    """
    from .dock import state

    live = {area.as_pointer() for screen in bpy.data.screens for area in screen.areas}
    for key in [key for key in _active if key not in live]:
        _active.pop(key, None)
        _previous.pop(key, None)
        _spaces.pop(key, None)
        state.states.pop(key, None)


def scan(window):
    from .dock import state

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
    # Un-maximizing frees the temp area and hands the Space back in one
    # operator: the follow above must see that owner before it is forgotten.
    prune()
    for area in window.screen.areas:
        if area.type == "NODE_EDITOR" and area.spaces.active.tree_type == EDITOR_ID:
            mount(window, area)
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
    prepared = False
    for screen in bpy.data.screens:
        for record in screen.get("open_painter_areas", []):
            index = record.get("index", -1)
            if 0 <= index < len(screen.areas):
                area = screen.areas[index]
                if area.type == record.get("backend"):
                    _active[area.as_pointer()] = area.type
                    _previous[area.as_pointer()] = "VIEW_3D"
                    _spaces[area.as_pointer()] = area.spaces.active.as_pointer()
                    if not bpy.app.background:
                        from .native import prepare_assets, request_focus

                        if not prepared:
                            # Asset handles are per file, not per area: one pass per load.
                            prepare_assets()
                            prepared = True
                        request_focus(area)
                        if (
                            screen.get("open_painter_ui_revision", 0) < 14
                            and area.type == "VIEW_3D"
                            and not bpy.context.scene.open_painter.paint_image
                        ):
                            area.spaces.active.show_region_toolbar = False
                        if not screen.get("open_painter_native_ui"):
                            for prop in (
                                "show_region_ui",
                                "show_region_toolbar",
                                "show_region_tool_header",
                                "show_region_asset_shelf",
                            ):
                                if hasattr(area.spaces.active, prop):
                                    setattr(area.spaces.active, prop, True)

        if not bpy.app.background:
            screen["open_painter_native_ui"] = True
            screen["open_painter_ui_revision"] = 14


def header(self, context):
    if is_area(context.area):
        self.layout.label(text="Open Painter", icon="BRUSH_DATA")
        for backend, label in (("VIEW_3D", "3D"), ("IMAGE_EDITOR", "2D")):
            self.layout.operator(
                "open_painter.native_view", text=label, depress=context.area.type == backend
            ).view = backend
        # Nodes: the same material as a graph, opened at the selected item (UI plan phase 7).
        self.layout.operator("open_painter.open_graph", text="Nodes", icon="NODETREE")
        # The way back to a plain viewport lives next to the backend toggles: the
        # editor-type menu re-mounts the Painter, so it cannot serve as the exit.
        self.layout.operator("open_painter.close_editor", text="", icon="X")


class OP_OT_open_editor(bpy.types.Operator):
    bl_idname = "open_painter.open_editor"
    bl_label = "Open Painter"
    bl_description = "Open Open Painter in this editor area"

    def execute(self, context):
        area = context.area or next((a for a in context.screen.areas if a.type == "VIEW_3D"), None)
        if area is None:
            self.report({"ERROR"}, "Choose an editor area first.")
            return {"CANCELLED"}
        mount(context.window, area)
        return {"FINISHED"}


class OP_OT_close_editor(bpy.types.Operator):
    bl_idname = "open_painter.close_editor"
    bl_label = "Close Open Painter"
    bl_description = "Return this area to the standard 3D Viewport"

    def execute(self, context):
        close(context)
        return {"FINISHED"}


def register():
    from .native import remark_handles, unmark_handles
    bpy.app.handlers.load_post.append(load)
    bpy.app.handlers.save_pre.append(save)
    bpy.app.handlers.save_pre.append(unmark_handles)
    bpy.app.handlers.save_post.append(remark_handles)
    # A write that fails (read-only folder, full or unmounted disk) ends in
    # save_post_fail, not save_post: the shelf must come back either way.
    bpy.app.handlers.save_post_fail.append(remark_handles)
    bpy.types.VIEW3D_HT_header.prepend(header)
    bpy.types.IMAGE_HT_header.prepend(header)
    bpy.app.timers.register(restore_initial, first_interval=0.1)


def restore_initial():
    load(None)
    return None


def unregister():
    if bpy.app.timers.is_registered(restore_initial):
        bpy.app.timers.unregister(restore_initial)
    from .native import remark_handles, unmark_handles
    for handlers, fn in ((bpy.app.handlers.load_post, load), (bpy.app.handlers.save_pre, save),
                         (bpy.app.handlers.save_pre, unmark_handles), (bpy.app.handlers.save_post, remark_handles),
                         (bpy.app.handlers.save_post_fail, remark_handles)):
        if fn in handlers:
            handlers.remove(fn)
    bpy.types.VIEW3D_HT_header.remove(header)
    bpy.types.IMAGE_HT_header.remove(header)
    _active.clear()
    _previous.clear()
    _spaces.clear()
