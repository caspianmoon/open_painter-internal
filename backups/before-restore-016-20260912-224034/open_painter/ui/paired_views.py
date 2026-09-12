"""Paired native 3D/image areas with shared Painter docks and document state."""

import bpy

from ..utils.ids import new_id
from .dock import state


def members(window, area):
    from .editor import is_area

    identifier = state.get(area).pair_id
    return [
        a
        for a in window.screen.areas
        if is_area(a) and identifier and state.get(a).pair_id == identifier
    ]


def image_for(context, area):
    from ..core.project_manager import active_set

    project = context.scene.open_painter
    ts = active_set(context)
    image = project.paint_image or (
        next((o.image for o in ts.outputs if o.channel == project.paint_channel), None)
        if ts
        else None
    )
    space = area.spaces.active
    if space.image != image:
        space.image = image
    mode = "PAINT" if project.paint_image else "VIEW"
    if space.ui_mode != mode:
        space.ui_mode = mode
    ui = state.get(area)
    if image and ts and not ts.dirty and ui.needs_frame and ui.width > 0 and area.width > 10:
        region = context.region
        scale = context.preferences.system.ui_scale
        dock = min(346, max(286, region.width / scale * 0.25)) * scale
        ratio = min(
            max(50, region.width - dock - 24 * scale) / image.size[0],
            max(50, region.height - 80 * scale) / image.size[1],
        )
        bpy.ops.image.view_all(fit_view=True)
        bpy.ops.image.view_zoom_ratio(ratio=ratio)
        bpy.ops.image.view_pan(offset=(dock / (2 * ratio), 0))
        ui.needs_frame = False


def both(context):
    from . import editor
    from .dock.interaction import configure

    window, area = context.window, context.area
    if members(window, area):
        return
    before = {a.as_pointer() for a in window.screen.areas}
    if area.type != "VIEW_3D":
        editor.set_view(area, "VIEW_3D")
        area.type = "VIEW_3D"
    region = next(r for r in area.regions if r.type == "WINDOW")
    with context.temp_override(area=area, region=region):
        bpy.ops.screen.area_split(direction="VERTICAL", factor=0.48)
    added = next(a for a in window.screen.areas if a.as_pointer() not in before)
    left, right = sorted((area, added), key=lambda a: a.x)
    identifier = new_id()
    editor._active[left.as_pointer()] = "VIEW_3D"
    editor._previous.setdefault(left.as_pointer(), "VIEW_3D")
    right.type = "IMAGE_EDITOR"
    editor._active[right.as_pointer()] = "IMAGE_EDITOR"
    editor._previous[right.as_pointer()] = "VIEW_3D"
    for target, role in ((left, "LEFT"), (right, "RIGHT")):
        ui = state.get(target)
        ui.pair_id, ui.view_role = identifier, role
        ui.needs_frame = role == "RIGHT"
        editor._spaces[target.as_pointer()] = target.spaces.active.as_pointer()
        with context.temp_override(
            window=window, area=target, region=next(r for r in target.regions if r.type == "WINDOW")
        ):
            configure(target)
            if target.type == "IMAGE_EDITOR":
                image_for(bpy.context, target)
        target.tag_redraw()
    editor.persist(window.screen)


def single(context, backend):
    from . import editor
    from .dock.interaction import configure

    window, area = context.window, context.area
    pair = members(window, area)
    if state.get(area).pair_id and len(pair) != 2:
        raise ValueError("Restore both Painter views before combining them.")
    if len(pair) == 2:
        left, right = sorted(pair, key=lambda a: a.x)
        if abs(left.y - right.y) > 2 or abs(left.height - right.height) > 2:
            raise ValueError(
                "Restore both Painter views from maximized mode before combining them."
            )
        old = right.as_pointer()
        left_ui, right_ui = state.get(left), state.get(right)
        left_ui.show_properties = right_ui.show_properties
        left_ui.right_tab = right_ui.right_tab
        with context.temp_override(area=left):
            bpy.ops.screen.area_join(
                source_xy=(left.x + left.width // 2, left.y + left.height // 2),
                target_xy=(right.x + right.width // 2, right.y + right.height // 2),
            )
        editor._active.pop(old, None)
        editor._previous.pop(old, None)
        editor._spaces.pop(old, None)
        state.states.pop(old, None)
        area = left
    ui = state.get(area)
    ui.pair_id, ui.view_role = "", "SINGLE"
    editor.set_view(area, backend)
    area.type = backend
    editor._spaces[area.as_pointer()] = area.spaces.active.as_pointer()
    with context.temp_override(
        window=window, area=area, region=next(r for r in area.regions if r.type == "WINDOW")
    ):
        configure(area)
        if backend == "IMAGE_EDITOR":
            image_for(bpy.context, area)
    editor.persist(window.screen)
    area.tag_redraw()
    return area
