"""Dock controls route to real project operators and native property popovers."""

from time import monotonic

import bpy

from ...core.layer_stack import active, channel_for
from ...core.project_manager import active_set, require_set
from ..operators import SafeOperator
from . import assets, state


class OP_OT_dock_popup(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.dock_popup"
    bl_label = "Open Painter Properties"
    section: bpy.props.StringProperty()
    owner_kind: bpy.props.StringProperty()
    property_key: bpy.props.StringProperty()

    def invoke(self, context, event):
        if self.section in {"CHANNEL_BLEND", "LAYER"}:
            from ...core.layer_stack import ensure_channels

            ts = active_set(context)
            layer = active(ts) if ts else None
            if layer and layer.type == "FOLDER" and not layer.channels:
                ensure_channels(layer)
                bpy.ops.ed.undo_push(message="Enable Folder Channel Settings")
        return context.window_manager.invoke_popup(self, width=390)

    def run(self, context):
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        if self.section == "NUMBER":
            owner, key = slider_owner(context, (self.owner_kind, self.property_key))
            layout.prop(owner, key)
            return
        if self.section == "ROW_BLEND":
            layer = active(require_set(context))
            if layer.type == "FOLDER" and layer.group_mode == "PASSTHROUGH":
                layout.prop(layer, "group_mode", expand=True)
            else:
                layout.prop(channel_for(layer, context.scene.open_painter.blend_channel), "blend_mode", expand=True)
            return
        layout.label(text=self.section.replace("_", " ").title(), icon="PREFERENCES")
        layout.separator()
        if self.section == "STACK_CHANNEL":
            layout.prop(context.scene.open_painter, "blend_channel", text="View channel")
            return
        if self.section == "BRUSH":
            from ...painting.brush_bridge import draw

            draw(layout, context)
            return
        if self.section == "BRUSH_COLOR":
            from ...painting.brush_bridge import color_owner

            owner = color_owner(context.scene)
            if owner:
                layout.template_color_picker(owner, "color", value_slider=True)
                layout.prop(owner, "color")
            return
        if self.section == "LIBRARY":
            from ..assets_panel import OP_PT_assets

            OP_PT_assets.draw(self, context)
            return
        if self.section in {"BAKE", "EXPORT", "TEXTURE", "MASK"}:
            from ..baking_panel import OP_PT_bake
            from ..export_panel import OP_PT_export
            from ..panels import OP_PT_texture_set
            from ..properties_panel import OP_PT_masks

            {
                "BAKE": OP_PT_bake,
                "EXPORT": OP_PT_export,
                "TEXTURE": OP_PT_texture_set,
                "MASK": OP_PT_masks,
            }[self.section].draw(self, context)
            return
        if self.section == "VIEWPORT":
            if context.area.type == "VIEW_3D":
                layout.prop(context.space_data.shading, "type")
                layout.prop(context.space_data.overlay, "show_overlays")
                layout.prop(
                    context.space_data.shading, "studiolight_rotate_z", text="Environment Rotation"
                )
            return
        ts = active_set(context)
        layer = active(ts) if ts else None
        if not layer:
            layout.label(text="Select a layer first.")
            return
        if self.section == "NAME":
            layout.prop(layer, "name", text="Name")
        elif self.section == "ADD_MASK":
            for source in ("PAINT", "FILL", "BAKED", "GENERATOR", "NOISE"):
                op = layout.operator(
                    "open_painter.mask", text=source.title() + " Mask", icon="MOD_MASK"
                )
                op.action = "ADD"
                op.source = source
            layout.operator("open_painter.anchor", text="Anchor Point", icon="ANCHOR_CENTER").action = "ADD_MASK"
        elif self.section == "BLEND":
            if layer.type == "FOLDER":
                layout.prop(layer, "group_mode")
            if layer.type != "FOLDER" or layer.group_mode == "ISOLATED":
                layout.prop(layer, "blend_mode")
        elif self.section == "CHANNEL_BLEND":
            project = context.scene.open_painter
            layout.prop(project, "blend_channel")
            channel = channel_for(layer, project.blend_channel)
            if channel:
                layout.prop(channel, "enabled")
                layout.prop(channel, "opacity")
                if layer.type != "FOLDER" or layer.group_mode == "ISOLATED":
                    layout.prop(channel, "blend_mode")
                else:
                    layout.label(text="Child blends pass through this folder.")
                layout.label(text=f"Overall layer opacity: {layer.opacity:.0%}")
        elif self.section == "COLOR":
            channel = channel_for(layer, "BASE_COLOR")
            if channel:
                layout.template_color_picker(channel, "color", value_slider=True)
                layout.prop(channel, "color", text="Base Color")
        else:
            layout.prop(layer, "name", text="Name")
            layout.prop(layer, "opacity")
            if layer.type == "FOLDER":
                layout.prop(layer, "group_mode")
            if layer.type != "FOLDER" or layer.group_mode == "ISOLATED":
                layout.prop(layer, "blend_mode")
            for channel in layer.channels:
                box = layout.box()
                box.prop(channel, "enabled", text=channel.channel.replace("_", " ").title())
                box.prop(channel, "opacity")
                if layer.type != "FOLDER" or layer.group_mode == "ISOLATED":
                    box.prop(channel, "blend_mode")
                if channel.enabled and layer.type == "FILL":
                    box.prop(
                        channel, "color" if channel.channel in {"BASE_COLOR", "NORMAL"} else "value"
                    )
                    box.template_ID(channel, "image", open="image.open")
                    if channel.image:
                        for key in (
                            "projection",
                            "filtering",
                            "tiling",
                            "scale",
                            "rotation",
                            "offset",
                        ):
                            box.prop(channel, key)
                        if channel.projection == "TRIPLANAR":
                            box.prop(channel, "triplanar_hardness")


def route(context, ui, action, payload):
    from ...painting.paint_session import synchronize

    _route(context, ui, action, payload)
    mask = (
        True
        if action == "SELECT_MASK"
        else False
        if action in {"SELECT_LAYER", "SET", "LAYER"}
        else None
    )
    synchronize(context, mask=mask)


def _route(context, ui, action, payload):
    ts = active_set(context)
    if action == "SEARCH":
        ui.search = True
        return
    ui.search = False
    if action == "ASSET_TAB":
        ui.tab = payload
        ui.asset_scroll = 0
        return
    if action == "ASSET":
        assets.apply(context, payload)
        if payload[1] not in {"BRUSH", "ALPHA"}:
            # Brushes and alphas change the tool, not the stack: the selected
            # mask stays the paint target.
            ui.mask_selected = payload[1] == "SMART_MASK"
        ui.properties_scroll = 0
        ts = active_set(context)
        if ts and active(ts):
            ui.selected_layers = {active(ts).id}
        bpy.ops.ed.undo_push(message="Apply Open Painter Asset")
        ui.message = "Applied " + payload[0]
        return
    if action == "NEW_SET":
        bpy.ops.open_painter.texture_set(action="ADD")
        if context.object and context.object.type == "MESH" and context.object.data.uv_layers:
            bpy.ops.open_painter.texture_set(action="ASSIGN")
        return
    if action in {"ASSIGN", "COPY_SET", "DELETE_SET"}:
        bpy.ops.open_painter.texture_set(
            action={"ASSIGN": "ASSIGN", "COPY_SET": "DUPLICATE", "DELETE_SET": "DELETE"}[action]
        )
        return
    if action == "SET":
        context.scene.open_painter.active_texture_set = payload
        ui.layer_scroll = 0
        return
    if action == "LAYER":
        if payload == "PARENT":
            bpy.ops.open_painter.layer("INVOKE_DEFAULT", action=payload)
        else:
            bpy.ops.open_painter.layer(action=payload)
        ui.mask_selected = False
        ui.layer_scroll = 0
        if ts and active(ts):
            ui.selected_layers = {active(ts).id}
        return
    if action == "ROW_BLEND":
        ts.active_layer = payload
        ui.selected_layers = {ts.layers[payload].id}
        bpy.ops.open_painter.dock_popup("INVOKE_DEFAULT", section="ROW_BLEND")
        return
    if action == "SELECT_LAYER":
        ts.active_layer = payload
        ui.selected_layers = {ts.layers[payload].id}
        ui.mask_selected = False
        ui.properties_scroll = 0
        return
    if action == "GROUP_TOGGLE":
        layer = next(item for item in ts.layers if item.id == payload)
        layer.expanded = not layer.expanded
        return
    if action == "SELECT_MASK":
        ts.active_layer = payload
        ui.mask_selected = True
        return
    if action == "VISIBILITY":
        ts.layers[payload].visible = not ts.layers[payload].visible
        bpy.ops.ed.undo_push(message="Layer Visibility")
        return
    if action == "CHANNEL":
        layer = active(require_set(context))
        channel = channel_for(layer, payload)
        if channel:
            if layer.type == "PAINT":
                context.scene.open_painter.paint_channel = payload
                bpy.ops.open_painter.paint(channel=payload)
            else:
                channel.enabled = not channel.enabled
                bpy.ops.ed.undo_push(message="Layer Channel")
        return
    if action == "INVERT_MASK":
        layer = active(require_set(context))
        mask = layer.masks[min(layer.active_mask, len(layer.masks) - 1)]
        mask.invert = not mask.invert
        bpy.ops.ed.undo_push(message="Invert Mask")
        return
    if action == "MASK_SELECT":
        active(require_set(context)).active_mask = payload
        ui.mask_selected = True
        return
    if action == "MASK_EDIT":
        bpy.ops.open_painter.mask(action=payload)
        return
    if action == "MASK_ENABLE":
        layer = active(require_set(context))
        mask = layer.masks[min(max(0, layer.active_mask), len(layer.masks) - 1)]
        mask.enabled = not mask.enabled
        bpy.ops.ed.undo_push(message="Mask Visibility")
        return
    if action == "BRUSH_ACTION":
        bpy.ops.open_painter.brush_action(action=payload)
        return
    if action == "IMPORT_ALPHA":
        bpy.ops.open_painter.import_alpha("INVOKE_DEFAULT")
        return
    if action in {"PAINT", "PAINT_MASK", "FINISH"}:
        bpy.ops.open_painter.paint(mask=action == "PAINT_MASK", stop=action == "FINISH")
        return
    if action == "POPUP":
        bpy.ops.open_painter.dock_popup("INVOKE_DEFAULT", section=payload)
        return
    if action == "IMPORT":
        bpy.ops.open_painter.import_material("INVOKE_DEFAULT")
        return
    if action == "SAVE":
        bpy.ops.open_painter.save_material("INVOKE_DEFAULT")
        return
    if action == "VIEW":
        from ..editor import set_view

        set_view(context.area, payload)
        context.area.type = payload
        if payload == "IMAGE_EDITOR":
            project = context.scene.open_painter
            context.space_data.ui_mode = "PAINT" if project.paint_image else "VIEW"
            from ...evaluation.composite_snapshot import for_view
            context.space_data.image = for_view(context.scene, ts, project.paint_channel)
        configure(context.area)
        return
    if action == "EXIT":
        from ..editor import close

        close(context)


def configure(area):
    space = area.spaces.active
    for key in (
        "show_region_ui",
        "show_region_toolbar",
        "show_region_tool_header",
    ):
        if hasattr(space, key) and getattr(space, key):
            # A current-area RNA update starts a region fade animation. Repeated
            # enforcement during that fade can reverse it. Configure the owning
            # space without an active area to apply visibility immediately.
            with bpy.context.temp_override(area=None, region=None):
                setattr(space, key, False)
    if getattr(space, "show_region_asset_shelf", False) and any(
        r.type == "ASSET_SHELF" and r.height > 2 for r in area.regions
    ):
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region:
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.screen.region_toggle(region_type="ASSET_SHELF")
    if hasattr(space, "show_region_header") and not space.show_region_header:
        space.show_region_header = True


def slider_owner(context, payload):
    kind, key = payload
    if kind == "PROJECT":
        return context.scene.open_painter, key
    layer = active(require_set(context))
    if kind == "ROW_CHANNEL":
        layer = next(item for item in require_set(context).layers if item.id == key)
        return channel_for(layer, context.scene.open_painter.blend_channel), "opacity"
    if kind == "LAYER":
        return layer, key
    if kind == "CHANNEL":
        return channel_for(layer, key), "value"
    if kind == "MASK":
        return layer.masks[min(layer.active_mask, len(layer.masks) - 1)], key
    paint = context.scene.tool_settings.image_paint
    unified = paint.unified_paint_settings
    owner = unified if getattr(unified, "use_unified_" + key, False) else paint.brush
    return owner, key


class OP_OT_dock_events(bpy.types.Operator):
    """Consume dock input while letting native viewport navigation and painting through."""

    bl_idname = "open_painter.dock_events"
    bl_label = "Open Painter Dock Controls"
    _window = 0

    def invoke(self, context, event):
        self._window = context.window.as_pointer()
        if self._window in state.windows:
            return {"CANCELLED"}
        state.windows.add(self._window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if not hasattr(bpy.types.Scene, "open_painter"):
            state.windows.discard(self._window)
            return {"CANCELLED"}
        from ..editor import is_area

        if not any(is_area(area) for area in context.screen.areas):
            state.windows.discard(self._window)
            return {"CANCELLED"}
        area = next(
            (
                a
                for a in context.screen.areas
                if is_area(a)
                and a.x <= event.mouse_x < a.x + a.width
                and a.y <= event.mouse_y < a.y + a.height
            ),
            None,
        )
        if area is None:
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                from ...evaluation.cache import paint_input

                paint_input(context.scene, False)
            for ui in state.states.values():
                ui.hover = None
            return {"PASS_THROUGH"}
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region is None:
            return {"PASS_THROUGH"}
        ui = state.get(area)
        x, y = event.mouse_x - region.x, event.mouse_y - region.y
        if (
            not ui.search
            and not ui.drag
            and event.value == "PRESS"
            and not (event.ctrl or event.alt or event.shift or event.oskey)
        ):
            if event.type in {"T", "N"}:
                if event.is_repeat:
                    return {"RUNNING_MODAL"}
                configure(area)
                name = "show_assets" if event.type == "T" else "show_properties"
                setattr(ui, name, not getattr(ui, name))
                ui.hover = None
                area.tag_redraw()
                return {"RUNNING_MODAL"}
        if y > ui.height:
            return {"PASS_THROUGH"}
        dock = (
            x < ui.left
            or x > ui.width - ui.right
            or y > ui.height - 43 * ui.ui_scale
            or (y < ui.bottom and x < ui.width - ui.right)
        )
        matches = [
            hit
            for hit in reversed(ui.hits)
            if hit[0] <= x <= hit[0] + hit[2] and hit[1] <= y <= hit[1] + hit[3]
        ]
        if context.scene.open_painter.paint_image and event.type == "LEFTMOUSE":
            from ...evaluation.cache import paint_input

            if event.value == "RELEASE":
                paint_input(context.scene, False)
            elif event.value == "PRESS" and not dock:
                paint_input(context.scene, True)
        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"} and dock:
            delta = -1 if event.type == "WHEELUPMOUSE" else 1
            if any(hit[4] == "SCROLL_LAYERS" for hit in matches):
                ui.layer_scroll = max(0, ui.layer_scroll + delta)
            elif any(hit[4] == "SCROLL_SETS" for hit in matches):
                ui.texture_scroll = max(0, ui.texture_scroll + delta)
            elif any(hit[4] == "SCROLL_PROPERTIES" for hit in matches):
                ui.properties_scroll = max(
                    0, min(ui.properties_limit, ui.properties_scroll + delta * 25)
                )
            elif any(hit[4] == "SCROLL_ASSETS" for hit in matches) or y < ui.bottom:
                ui.asset_scroll = max(0, ui.asset_scroll + delta * ui.asset_columns)
            area.tag_redraw()
            return {"RUNNING_MODAL"}
        hit = next((item for item in matches if not item[4].startswith("SCROLL_")), None)
        if event.type == "MOUSEMOVE":
            hover = (hit[4], hit[5]) if hit and dock else None
            if hover != ui.hover:
                ui.hover = hover
                ui.hover_since = monotonic()
            ui.mouse = (x, y)
            area.tag_redraw()
        with context.temp_override(area=area, region=region):
            try:
                if not ui.search and event.type == "G" and event.ctrl and event.value == "PRESS":
                    bpy.ops.open_painter.layer(action="UNGROUP" if event.alt else "GROUP")
                    area.tag_redraw()
                    return {"RUNNING_MODAL"}
                if ui.layer_drag:
                    if event.type == "ESC":
                        ui.layer_drag = None
                        return {"RUNNING_MODAL"}
                    if event.type == "MOUSEMOVE":
                        ui.layer_drag_moved |= (
                            abs(x - ui.layer_drag_start[0]) + abs(y - ui.layer_drag_start[1]) > 8
                        )
                        if ui.layer_drag_moved:
                            ui.message = "Drop on a group to move inside; drop on the Layers header to move out"
                        return {"RUNNING_MODAL"}
                    if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                        ids, moved = ui.layer_drag, ui.layer_drag_moved
                        ui.layer_drag = None
                        if moved:
                            from ...core.layer_stack import move_above, reparent_many

                            ts = require_set(context)
                            if hit and hit[4] in {"SELECT_LAYER", "GROUP_TOGGLE"}:
                                target = (
                                    ts.layers[hit[5]]
                                    if hit[4] == "SELECT_LAYER"
                                    else next(item for item in ts.layers if item.id == hit[5])
                                )
                                folder_id = (
                                    target.id if target.type == "FOLDER" else target.parent_id
                                )
                                target_id, target_kind = target.id, target.type
                                reparent_many(ts, ids, folder_id)
                                if target_kind != "FOLDER":
                                    move_above(ts, ids, target_id)
                            elif hit and hit[4] == "GROUP_ROOT":
                                reparent_many(ts, ids, "")
                            bpy.ops.ed.undo_push(message="Move Layers to Group")
                            ui.message = ""
                        area.tag_redraw()
                        return {"RUNNING_MODAL"}
                if (
                    dock
                    and hit
                    and hit[4] == "SELECT_LAYER"
                    and event.type == "LEFTMOUSE"
                    and event.value == "PRESS"
                ):
                    from ...core.layer_stack import visible_rows

                    ts = require_set(context)
                    index = hit[5]
                    old_index = ts.active_layer
                    old_selection = set(ui.selected_layers)
                    if event.shift:
                        indices = [i for i, _ in visible_rows(ts)]
                        a, b = (
                            indices.index(old_index) if old_index in indices else 0,
                            indices.index(index),
                        )
                        chosen = {ts.layers[i].id for i in indices[min(a, b) : max(a, b) + 1]}
                    elif event.ctrl:
                        chosen = old_selection ^ {ts.layers[index].id}
                    else:
                        chosen = (
                            old_selection
                            if ts.layers[index].id in old_selection
                            else {ts.layers[index].id}
                        )
                    route(context, ui, "SELECT_LAYER", index)
                    ui.selected_layers = chosen
                    ui.layer_drag = tuple(chosen)
                    ui.layer_drag_start = (x, y)
                    ui.layer_drag_moved = False
                    area.tag_redraw()
                    return {"RUNNING_MODAL"}
                if not dock and event.type == "LEFTMOUSE" and event.value == "PRESS":
                    from ...painting.paint_session import synchronize

                    synchronize(context)
                if ui.search and event.value == "PRESS":
                    if event.type in {"RET", "NUMPAD_ENTER", "ESC"}:
                        ui.search = False
                    elif event.type == "BACK_SPACE":
                        ui.query = ui.query[:-1]
                    elif event.unicode and event.unicode.isprintable():
                        ui.query += event.unicode
                    elif event.type == "LEFTMOUSE":
                        ui.search = False
                    else:
                        return {"PASS_THROUGH"}
                    area.tag_redraw()
                    if event.type != "LEFTMOUSE":
                        return {"RUNNING_MODAL"}
                if ui.drag:
                    if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
                        owner, key = slider_owner(context, ui.drag[0])
                        setattr(owner, key, ui.drag_original)
                        ui.drag = None
                        area.tag_redraw()
                        return {"RUNNING_MODAL"}
                    if event.type == "MOUSEMOVE":
                        payload, minimum, maximum, start, width = ui.drag
                        owner, key = slider_owner(context, payload)
                        value = minimum + max(0, min(1, (x - start) / width)) * (maximum - minimum)
                        if event.shift:
                            value = (
                                ui.drag_original
                                + (x - ui.drag_mouse) / width * (maximum - minimum) * 0.1
                            )
                            value = max(minimum, min(maximum, value))
                        setattr(owner, key, round(value) if key in {"size", "spacing"} else value)
                        area.tag_redraw()
                        return {"RUNNING_MODAL"}
                    if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                        ui.drag = None
                        bpy.ops.ed.undo_push(message="Open Painter Property")
                        return {"RUNNING_MODAL"}
                if dock and hit and event.type == "LEFTMOUSE" and event.value == "DOUBLE_CLICK":
                    if hit[4] == "SLIDER":
                        kind, key = hit[5][0]
                        bpy.ops.open_painter.dock_popup(
                            "INVOKE_DEFAULT", section="NUMBER", owner_kind=kind, property_key=key
                        )
                        return {"RUNNING_MODAL"}
                    if hit[4] == "SELECT_LAYER":
                        route(context, ui, "SELECT_LAYER", hit[5])
                        bpy.ops.open_painter.dock_popup("INVOKE_DEFAULT", section="NAME")
                        return {"RUNNING_MODAL"}
                if dock and event.type == "RIGHTMOUSE" and event.value == "PRESS":
                    if hit and hit[4] == "SLIDER":
                        kind, key = hit[5][0]
                        bpy.ops.open_painter.dock_popup(
                            "INVOKE_DEFAULT", section="NUMBER", owner_kind=kind, property_key=key
                        )
                    else:
                        if hit and hit[4] in {"SELECT_LAYER", "SELECT_MASK"}:
                            route(context, ui, hit[4], hit[5])
                        bpy.ops.open_painter.dock_popup("INVOKE_DEFAULT", section="LAYER")
                    return {"RUNNING_MODAL"}
                if dock and x > ui.width - ui.right and event.value == "PRESS":
                    if event.type == "F2":
                        bpy.ops.open_painter.dock_popup("INVOKE_DEFAULT", section="NAME")
                        return {"RUNNING_MODAL"}
                    if event.type in {"DEL", "X"} and not ui.search:
                        bpy.ops.open_painter.layer("INVOKE_DEFAULT", action="DELETE")
                        return {"RUNNING_MODAL"}
                if event.type == "LEFTMOUSE" and event.value == "PRESS" and dock:
                    if hit:
                        action, payload = hit[4:]
                        if action == "SLIDER":
                            bpy.ops.ed.undo_push(message="Before Open Painter Property")
                            ui.drag = payload
                            owner, key = slider_owner(context, payload[0])
                            ui.drag_original = getattr(owner, key)
                            ui.drag_mouse = x
                        else:
                            route(context, ui, action, payload)
                    area.tag_redraw()
                    return {"RUNNING_MODAL"}
                if event.type in {"LEFTMOUSE", "RIGHTMOUSE", "MIDDLEMOUSE"} and dock:
                    return {"RUNNING_MODAL"}
            except Exception as exc:
                ui.message = str(exc)
                self.report({"WARNING"}, str(exc))
                area.tag_redraw()
                return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def cancel(self, context):
        state.windows.discard(self._window)
