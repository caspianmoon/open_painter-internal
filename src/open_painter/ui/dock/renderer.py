"""Assets / Viewport / Texture Sets / Layers / Properties docks."""

from time import monotonic

import bpy
import gpu

from ...constants import CHANNEL_LABELS, VERSION
from ...core.layer_stack import active, channel_for, visible_rows
from ...core.project_manager import active_set
from . import assets, state
from . import drawing as d


class Canvas:
    def __init__(self, context):
        d.sync_theme(context)
        self.context = context
        self.state = state.get(context.area)
        self.scale = context.preferences.system.ui_scale
        self.w = context.region.width / self.scale
        inset = sum(
            r.height
            for r in context.area.regions
            if r.type in {"HEADER", "TOOL_HEADER"} and r.height > 1
        )
        self.h = (context.region.height - inset) / self.scale
        self.left = 38
        self.bottom = min(260, max(170, self.h * 0.30)) if self.state.show_assets else 22
        self.right = min(346, max(286, self.w * 0.25))
        if not self.state.show_properties:
            self.right = 0
        self.state.hits = []
        self.clip = None
        self.state.width = context.region.width
        self.state.height = self.h * self.scale
        self.state.left = self.left * self.scale
        self.state.right = self.right * self.scale
        self.state.bottom = self.bottom * self.scale
        self.state.ui_scale = self.scale

    def rect(self, x, y, w, h, color):
        s = self.scale
        d.rect(x * s, y * s, w * s, h * s, color)

    def text(self, value, x, y, size=11, color=None, width=None):
        s = self.scale
        d.text(
            value, x * s, y * s, size * d.FONT_SIZE / 11 * s, color, width * s if width else None
        )

    def widget(self, x, y, w, h, kind="tool", selected=False, hover=False):
        s = self.scale
        d.widget(x * s, y * s, w * s, h * s, kind, selected, hover)

    def hit(self, x, y, w, h, action, payload=None):
        if self.clip:
            low, high = self.clip
            high_y = min(y + h, high)
            y = max(y, low)
            h = high_y - y
            if h <= 0:
                return
        s = self.scale
        self.state.hits.append((x * s, y * s, w * s, h * s, action, payload))

    def button(self, label, x, y, w, h, action, payload=None, selected=False):
        hover = self.state.hover == (action, payload)
        # Blender widgets use a 20-unit row. Keep the surrounding layout cell
        # and hit target while matching the native control's visible height.
        control_h = min(h, 20)
        control_y = y + (h - control_h) / 2
        kind = "radio" if action in {"VIEW", "ASSET_TAB"} else "tool"
        self.widget(x, control_y, w, control_h, kind, selected=selected, hover=hover)
        color = d.WIDGETS[kind]["text_sel" if selected else "text"]
        width = d.text_width(label, d.FONT_SIZE * self.scale) / self.scale
        self.text(label, x + max(4, (w - width) / 2), y + (h - 11) / 2 + 1, 11, color, w - 8)
        self.hit(x, y, w, h, action, payload)

    def panel(self, x, y, w, h):
        s = self.scale
        radius = 8 * d.PANEL_ROUNDNESS * s
        d.rounded_rect(x * s, y * s, w * s, h * s, radius, d.PANEL_OUTLINE)
        d.rounded_rect(
            (x + 0.5) * s,
            (y + 0.5) * s,
            (w - 1) * s,
            (h - 1) * s,
            max(0, radius - 0.5 * s),
            d.PANEL_BACK,
        )

    def header(self, label, x, y, w, detail=""):
        self.rect(x + 6, y + 2, w - 12, 25, d.PANEL_HEADER)
        self.text(label.title(), x + 12, y + 10, 11, d.PANEL_TITLE)

    def slider(self, label, x, y, w, value, action, payload, minimum=0, maximum=1):
        # Blender-style compact numeric field: label and value share one row.
        data = (payload, minimum, maximum, x * self.scale, w * self.scale)
        self.widget(x, y, w, 22, "numslider", hover=self.state.hover == (action, data))
        fraction = max(0, min(1, (value - minimum) / (maximum - minimum)))
        self.rect(x + 3, y + 2, (w - 6) * fraction, 18, d.ACCENT)
        self.text(label, x + 8, y + 7, 11)
        number = f"{value * 100:.0f}" if payload[0] == "ROW_CHANNEL" else f"{value:.3f}" if maximum <= 1 else f"{value:.0f}"
        self.text(number, x + w - 54, y + 7, 11)
        self.hit(x, y, w, 22, action, data)


def draw():
    context = bpy.context
    if not state.is_painter(context) or context.region.type != "WINDOW":
        return
    canvas = Canvas(context)
    gpu.state.blend_set("ALPHA")
    try:
        toolbar(canvas)
        tools(canvas)
        if canvas.state.show_assets:
            browser(canvas)
        if canvas.state.show_properties:
            right_docks(canvas)
        status(canvas)
        tooltip(canvas)
    finally:
        gpu.state.blend_set("NONE")


def toolbar(c):
    w, h = c.w, c.h
    ts = active_set(c.context)
    c.rect(0, h - 43, w, 43, d.HEADER)

    x = c.left + 6
    paint = c.context.scene.tool_settings.image_paint
    brush = paint.brush
    if brush:
        unified = paint.unified_paint_settings
        for i, (key, label, maximum) in enumerate((("size", "Size", 500), ("strength", "Flow", 1), ("spacing", "Spacing", 100))):
            owner = unified if getattr(unified, "use_unified_" + key, False) else brush
            c.slider(label, x + i * 112, h - 33, 106, getattr(owner, key), "SLIDER", ("BRUSH", key), 0 if key == "strength" else 1, maximum)
        x += 150
    else:
        c.button("Paint", x, h - 34, 57, 25, "PAINT")
        c.button("Mask", x + 63, h - 34, 54, 25, "PAINT_MASK")
        c.button("Finish", x + 123, h - 34, 56, 25, "FINISH")
    c.button("3D", x + 194, h - 34, 35, 25, "VIEW", "VIEW_3D", c.context.area.type == "VIEW_3D")
    c.button(
        "2D", x + 233, h - 34, 35, 25, "VIEW", "IMAGE_EDITOR", c.context.area.type == "IMAGE_EDITOR"
    )
    if w - c.right - x > 460:
        c.text("Material", x + 294, h - 25, 11, d.MUTED)
        c.hit(x + 284, h - 35, 85, 28, "POPUP", "VIEWPORT")
    action_right = max(c.right, 300)
    action_width = (action_right - 46) / 2
    c.button("Bake Mesh Maps", w - action_right + 10, h - 34, action_width, 25, "POPUP", "BAKE")
    c.button(
        "Export Textures",
        w - action_right + 14 + action_width,
        h - 34,
        action_width,
        25,
        "POPUP",
        "EXPORT",
    )
    c.button("×", w - 31, h - 34, 23, 25, "EXIT")
    # Keep Blender's native viewport unobstructed between the docks.
    if c.context.area.type == "VIEW_3D":
        c.text(
            ts.name if ts else "No texture set",
            c.left + 18,
            h - 67,
            11,
            d.rgba(c.context.preferences.themes[0].view_3d.space.text),
        )
        c.text(
            "3D VIEW",
            c.left + 18,
            c.bottom + 12,
            10,
            d.rgba(c.context.preferences.themes[0].view_3d.space.text),
        )
    else:
        c.text(
            "2D TEXTURE VIEW",
            c.left + 18,
            c.bottom + 12,
            10,
            d.rgba(c.context.preferences.themes[0].view_3d.space.text),
        )


def tools(c):
    c.rect(0, 22, c.left, c.h - 65, d.HEADER)
    for i, (label, action, payload) in enumerate([
        ("P", "PAINT", None), ("E", "BRUSH_ACTION", "ERASE"),
        ("M", "PAINT_MASK", None), ("F", "FINISH", None),
        ("3D", "VIEW", "VIEW_3D"), ("2D", "VIEW", "IMAGE_EDITOR"),
    ]):
        y = c.h - 79 - i * 32
        c.button("", 5, y, 28, 27, action, payload)
        d.tool_icon(10 * c.scale, (y + 5) * c.scale, 18 * c.scale, label)


def browser(c):
    s = c.state
    x0, width, top = c.left, c.w - c.left - c.right, c.bottom
    c.rect(x0, 22, width, top - 22, d.BG)
    c.panel(x0 + 4, 25, width - 8, top - 28)
    c.header("ASSETS · SMART MASKS" if s.tab == "MASKS" else "ASSETS", x0, top - 31, width)
    search_width = max(70, min(220, width - 340))
    c.widget(x0 + 12, top - 58, search_width, 20, "text", selected=s.search)
    c.text(s.query or "Search assets", x0 + 20, top - 53, width=search_width - 16)
    c.hit(x0 + 12, top - 60, search_width, 24, "SEARCH")
    for i, (label, key) in enumerate([("Mat", "MATERIALS"), ("Smart", "SMART"), ("Masks", "MASKS"), ("Brushes", "BRUSHES"), ("Alphas", "ALPHAS")]):
        c.button(label, x0 + search_width + 22 + i * 61, top - 60, 58, 24, "ASSET_TAB", key, s.tab == key)
    entries = assets.items(c.context, s)
    gap, card = 8, min(70, max(36, top - 150))
    columns = max(1, int((width - 24) / (card + gap)))
    rows = max(1, int((top - 97) / (card + 27)))
    capacity = columns * rows
    s.asset_columns = columns
    s.asset_scroll = max(0, min(s.asset_scroll, max(0, len(entries) - capacity)))
    c.hit(x0, 52, width, top - 116, "SCROLL_ASSETS")
    for index, entry in enumerate(entries[s.asset_scroll:s.asset_scroll + capacity]):
        col, row = index % columns, index // columns
        x = x0 + 12 + col * (card + gap)
        y = top - 68 - (row + 1) * (card + 27)
        c.widget(x, y + 23, card, card, "text", hover=s.hover == ("ASSET", entry))
        texture = bpy.data.textures.get(entry[2]) if entry[1] == "ALPHA" and entry[2] else None
        if texture and texture.type == "IMAGE" and texture.image and texture.image.has_data:
            d.image_preview(
                texture.image,
                (x + 3) * c.scale,
                (y + 26) * c.scale,
                (card - 6) * c.scale,
                (card - 6) * c.scale,
            )
        elif entry[1] in {"ALPHA", "BRUSH"}:
            d.circle(
                (x + card / 2) * c.scale,
                (y + 23 + card / 2) * c.scale,
                card * 0.3 * c.scale,
                d.TEXT,
            )
            if entry[1] == "ALPHA" and not entry[2]:
                c.text("None", x + card / 2 - 13, y + 21 + card / 2, 10, d.BG)
        else:
            preview = assets.preview(entry[0])
            if preview:
                d.image_preview(
                    preview,
                    (x + 3) * c.scale,
                    (y + 26) * c.scale,
                    (card - 6) * c.scale,
                    (card - 6) * c.scale,
                )
            else:
                c.rect(x + 8, y + 31, card - 16, card - 16, (*entry[3], 1))
        c.text(entry[0], x + 2, y + 6, 10, d.TEXT, card)
        c.hit(x, y, card, card + 24, "ASSET", entry)
    c.text(f"{len(entries)} assets", x0 + 12, 36, 10, d.MUTED)
    c.button("Library", x0 + width - 300, 29, 70, 24, "POPUP", "LIBRARY")
    c.button("Import Alpha" if s.tab == "ALPHAS" else "Import Assets", x0 + width - 225, 29, 108, 24, "IMPORT_ALPHA" if s.tab == "ALPHAS" else "IMPORT")
    c.button("Save Material", x0 + width - 112, 29, 100, 24, "SAVE")


def right_docks(c):
    ts = active_set(c.context)
    x = c.w - c.right
    w = c.right
    h = c.h
    c.rect(x, 22, w, h - 65, d.BG)
    c.rect(x, 22, 1, h - 65, d.LINE)
    c.panel(x + 6, 28, w - 12, h - 77)
    top = h - 73
    c.header("TEXTURE SET LIST", x, top, w)
    project = c.context.scene.open_painter
    sets = list(project.texture_sets)
    c.state.texture_scroll = max(0, min(c.state.texture_scroll, max(0, len(sets) - 3)))
    for i, item in enumerate(sets[c.state.texture_scroll : c.state.texture_scroll + 3]):
        y = top - 28 * (i + 1)
        c.widget(
            x + 8,
            y,
            w - 16,
            26,
            "list_item",
            selected=item == ts,
            hover=c.state.hover == ("SET", i + c.state.texture_scroll),
        )
        c.text(
            item.name,
            x + 18,
            y + 8,
            11,
            d.WIDGETS["list_item"]["text_sel" if item == ts else "text"],
            w - 108,
        )
        c.text(item.resolution, x + w - 55, y + 8, 10)
        c.hit(x + 8, y, w - 16, 26, "SET", i + c.state.texture_scroll)
    c.hit(x, top - max(1, min(3, len(sets))) * 28, w, max(1, min(3, len(sets))) * 28, "SCROLL_SETS")
    y = top - max(1, min(3, len(sets))) * 28 - 32
    available = w - 20
    widths = [0.2, 0.2, 0.25, 0.2, 0.15]
    offset = x + 10
    for label, fraction, action, payload in zip(
        ("+ New", "Assign", "Settings", "Copy", "−"),
        widths,
        ("NEW_SET", "ASSIGN", "POPUP", "COPY_SET", "DELETE_SET"),
        (None, None, "TEXTURE", None, None),
    ):
        width = available * fraction
        c.button(label, offset, y, width - 3, 23, action, payload)
        offset += width
    y -= 37
    c.header("LAYERS", x, y, w, "STACK")
    c.hit(x, y, w, 29, "GROUP_ROOT")
    y -= 25
    c.button(CHANNEL_LABELS[project.blend_channel] + " ▾", x + 10, y, w - 20, 22, "POPUP", "STACK_CHANNEL")
    y -= 30
    c.button("+ Paint", x + 10, y, 65, 25, "LAYER", "PAINT")
    c.button("+ Fill", x + 81, y, 55, 25, "LAYER", "FILL")
    c.button("Folder", x + 142, y, 60, 25, "LAYER", "FOLDER")
    c.button("Mask", x + 208, y, 52, 25, "POPUP", "ADD_MASK")
    list_top = y - 10
    list_height = min(220, max(100, min(h * 0.235, len(ts.layers) * 48 + 6 if ts else 100)))
    c.rect(x + 7, list_top - list_height, w - 14, list_height, d.HEADER)
    if ts and ts.layers:
        entries = visible_rows(ts)
        capacity = max(1, int(list_height / 48))
        c.state.layer_scroll = max(0, min(c.state.layer_scroll, max(0, len(entries) - capacity)))
        for row, (index, depth) in enumerate(
            entries[c.state.layer_scroll : c.state.layer_scroll + capacity]
        ):
            layer = ts.layers[index]
            ly = list_top - (row + 1) * 48
            selected = layer.id in c.state.selected_layers or index == ts.active_layer
            c.widget(
                x + 8,
                ly,
                w - 16,
                47,
                "list_item",
                selected=selected,
                hover=c.state.hover == ("SELECT_LAYER", index),
            )
            c.hit(x + 8, ly, w - 16, 47, "SELECT_LAYER", index)
            c.text(
                "●" if layer.visible else "○",
                x + 15,
                ly + 11,
                11,
                d.TEXT if layer.visible else d.MUTED,
            )
            c.hit(x + 9, ly, 25, 47, "VISIBILITY", index)
            indent = min(depth * 16, w - 185)
            color = channel_for(layer, project.blend_channel)
            swatch = (
                (tuple(color.color[:3]) if project.blend_channel in {"BASE_COLOR", "EMISSION", "NORMAL"}
                 else (color.value,) * 3) + (1,) if color else d.CARD
            )
            c.rect(x + 39 + indent, ly + 5, 22, 22, swatch if layer.type == "FILL" else d.CARD)
            if layer.type != "FILL":
                c.text(
                    "P" if layer.type == "PAINT" else ("▾" if layer.expanded else "▸"),
                    x + 47 + indent,
                    ly + 11,
                    11,
                )
                if layer.type == "FOLDER":
                    c.hit(x + 36 + indent, ly + 2, 30, 28, "GROUP_TOGGLE", layer.id)
            c.text(
                layer.name,
                x + 76 + indent,
                ly + 11,
                11,
                d.WIDGETS["list_item"]["text_sel" if selected else "text"],
                w - 208 - indent,
            )
            if layer.masks:
                c.rect(
                    x + w - 113,
                    ly + 5,
                    20,
                    22,
                    d.TEXT if not (selected and c.state.mask_selected) else d.ACCENT,
                )
                d.circle((x + w - 103) * c.scale, (ly + 16) * c.scale, 6 * c.scale, d.HEADER)
                c.hit(x + w - 116, ly + 2, 27, 28, "SELECT_MASK", index)
            channel = channel_for(layer, project.blend_channel)
            opacity = channel.opacity if channel else 1.0
            mode = channel.blend_mode if channel and channel.blend_mode != "INHERIT" else layer.blend_mode
            label = "Pass" if layer.type == "FOLDER" and layer.group_mode == "PASSTHROUGH" else mode.title()
            c.button(label + " ▾", x + w - 83, ly + 25, 73, 20, "ROW_BLEND", index)
            if channel:
                c.slider("", x + w - 83, ly + 2, 73, opacity, "SLIDER", ("ROW_CHANNEL", layer.id))
        c.hit(x, list_top - list_height, w, list_height, "SCROLL_LAYERS")
        # Scroll target must not shadow row clicks; event routing handles wheel separately.
    else:
        c.text("Add a paint or fill layer", x + 25, list_top - 40, 12, d.MUTED)
        c.text("to start texturing.", x + 25, list_top - 60, 11, d.MUTED)
    y = list_top - list_height - 32
    for index, (label, action) in enumerate(
        [
            ("↑", "UP"),
            ("↓", "DOWN"),
            ("Duplicate", "DUPLICATE"),
            ("Delete", "DELETE"),
            ("Parent", "PARENT"),
        ]
    ):
        widths = [24, 24, (w - 93) * 0.40, (w - 93) * 0.3, (w - 93) * 0.3]
        offset = sum(widths[:index]) + index * 4
        c.button(label, x + 10 + offset, y, widths[index], 23, "LAYER", action)
    prop_top = y - 37
    properties(c, ts, x, prop_top, w)
    c.hit(x, 22, w, max(0, prop_top - 22), "SCROLL_PROPERTIES")


def properties(c, ts, x, top, w):
    layer = active(ts) if ts else None
    title = "Properties"
    if layer:
        title += " · " + ("MASK" if c.state.mask_selected and layer.masks else layer.type)
    c.header(title, x, top, w)
    if not layer:
        c.text("Select a layer to edit its properties.", x + 14, top - 28, 11, d.MUTED, w - 25)
        return
    origin = gpu.state.viewport_get()
    gpu.state.scissor_test_set(True)
    gpu.state.scissor_set(
        int(origin[0] + x * c.scale),
        int(origin[1] + 22 * c.scale),
        int(w * c.scale),
        max(1, int((top - 22) * c.scale)),
    )
    c.clip = (22, top)
    try:
        bottom = property_fields(c, ts, layer, x, top, w)
        c.state.properties_limit = max(0, 42 - (bottom - c.state.properties_scroll))
        c.state.properties_scroll = min(c.state.properties_scroll, c.state.properties_limit)
    finally:
        c.clip = None
        gpu.state.scissor_test_set(False)


def property_fields(c, ts, layer, x, top, w):
    y = top - 30 + c.state.properties_scroll
    c.text(layer.name, x + 14, y + 5, 11, d.TEXT, w - 82)
    c.button("Edit", x + w - 55, y, 43, 22, "POPUP", "LAYER")
    y -= 29
    if c.state.mask_selected and layer.masks:
        return mask_fields(c, layer, x, y, w)
    mode = (
        "Passthrough"
        if layer.type == "FOLDER" and layer.group_mode == "PASSTHROUGH"
        else layer.blend_mode.replace("_", " ").title()
    )
    c.button(mode, x + 14, y, w - 28, 22, "POPUP", "BLEND")
    y -= 28
    c.slider("Opacity", x + 14, y, w - 28, layer.opacity, "SLIDER", ("LAYER", "opacity"))
    y -= 28
    if layer.type == "FOLDER":
        c.text("Masks and opacity affect all children.", x + 14, y, 10, d.MUTED, w - 26)
        return y
    chip_width = (w - 28) / 6
    project = c.context.scene.open_painter
    for i, (key, label) in enumerate(
        zip(
            ("BASE_COLOR", "ROUGHNESS", "METALLIC", "NORMAL", "HEIGHT", "AO"),
            ("BC", "R", "M", "N", "H", "AO"),
        )
    ):
        channel = channel_for(layer, key)
        selected = (
            project.paint_channel == key
            if layer.type == "PAINT"
            else bool(channel and channel.enabled)
        )
        c.button(label, x + 14 + i * chip_width, y, chip_width - 3, 22, "CHANNEL", key, selected)
    y -= 30
    if layer.type == "PAINT":
        c.text("Paint: " + CHANNEL_LABELS[project.paint_channel], x + 14, y + 7, 11, d.MUTED)
        return brush_fields(c, x, y - 28, w)
    for key in ("BASE_COLOR", "ROUGHNESS", "METALLIC", "NORMAL", "HEIGHT", "AO"):
        channel = channel_for(layer, key)
        if not channel or not channel.enabled:
            continue
        if channel.image:
            c.button(
                CHANNEL_LABELS[key] + ": " + channel.image.name,
                x + 14,
                y,
                w - 28,
                23,
                "POPUP",
                "LAYER",
            )
        elif key in {"BASE_COLOR", "NORMAL"}:
            c.text(CHANNEL_LABELS[key], x + 14, y + 7, 11, d.MUTED)
            c.rect(x + w * 0.5, y, w * 0.5 - 14, 23, tuple(channel.color))
            c.hit(x + 14, y, w - 28, 25, "POPUP", "COLOR" if key == "BASE_COLOR" else "LAYER")
        else:
            c.slider(
                CHANNEL_LABELS[key], x + 14, y, w - 28, channel.value, "SLIDER", ("CHANNEL", key)
            )
        y -= 28
    return y


def brush_fields(c, x, y, w):
    from ...painting.brush_bridge import color_owner

    project = c.context.scene.open_painter
    paint = c.context.scene.tool_settings.image_paint
    brush = paint.brush
    if not brush:
        c.button(
            "Start painting",
            x + 14,
            y,
            w - 28,
            24,
            "PAINT_MASK" if c.state.mask_selected else "PAINT",
        )
        return y - 28
    c.button(brush.name + " · Settings", x + 14, y, w - 28, 22, "POPUP", "BRUSH")
    y -= 28
    if c.state.mask_selected or project.paint_channel not in {"BASE_COLOR", "NORMAL"}:
        c.slider(
            "Paint Value",
            x + 14,
            y,
            w - 28,
            project.paint_value,
            "SLIDER",
            ("PROJECT", "paint_value"),
        )
    else:
        owner = color_owner(c.context.scene)
        c.text("Brush Color", x + 14, y + 7, 11, d.MUTED)
        c.rect(x + w * 0.5, y, w * 0.5 - 14, 22, tuple(owner.color) + (1,))
        c.hit(x + 14, y, w - 28, 23, "POPUP", "BRUSH_COLOR")
    y -= 28
    if c.state.mask_selected:
        c.button("Black · Hide", x + 14, y, (w - 32) / 2, 22, "BRUSH_ACTION", "BLACK")
        c.button("White · Reveal", x + w / 2 + 2, y, (w - 32) / 2, 22, "BRUSH_ACTION", "WHITE")
        y -= 28
    unified = paint.unified_paint_settings
    for key, label, maximum in (
        ("size", "Size", 500),
        ("strength", "Strength", 1),
        ("spacing", "Spacing", 100),
    ):
        owner = unified if getattr(unified, "use_unified_" + key, False) else brush
        c.slider(
            label,
            x + 14,
            y,
            w - 28,
            getattr(owner, key),
            "SLIDER",
            ("BRUSH", key),
            0 if key == "strength" else 1,
            maximum,
        )
        y -= 28
    c.button(
        "Erase" if brush.blend == "ERASE_ALPHA" else "Paint",
        x + 14,
        y,
        w - 28,
        22,
        "BRUSH_ACTION",
        "ERASE",
        brush.blend == "ERASE_ALPHA",
    )
    y -= 28
    c.text(
        "Alpha: " + (brush.mask_texture.name if brush.mask_texture else "None"),
        x + 14,
        y + 7,
        11,
        d.MUTED,
        w - 28,
    )
    y -= 28
    c.button("Import Alpha", x + 14, y, (w - 32) / 2, 22, "IMPORT_ALPHA")
    c.button("Clear Alpha", x + w / 2 + 2, y, (w - 32) / 2, 22, "BRUSH_ACTION", "CLEAR_ALPHA")
    return y - 28


def mask_fields(c, layer, x, y, w):
    selected = min(max(0, layer.active_mask), len(layer.masks) - 1)
    mask = layer.masks[selected]
    # Every mask is reachable through the scrollable properties area.
    for index, item in enumerate(layer.masks):
        c.button(item.name, x + 14, y, w - 28, 22, "MASK_SELECT", index, index == selected)
        y -= 25
    for i, (label, action) in enumerate((("Up", "UP"), ("Down", "DOWN"), ("Delete", "DELETE"))):
        c.button(label, x + 14 + i * (w - 28) / 3, y, (w - 28) / 3 - 3, 22, "MASK_EDIT", action)
    y -= 28
    label = (
        mask.generator.replace("_", " ").title()
        if mask.source == "GENERATOR"
        else mask.map_type
        if mask.source == "BAKED"
        else mask.source.title()
    )
    c.button(label + " · Settings", x + 14, y, w - 28, 22, "POPUP", "MASK")
    y -= 28
    c.button("Enabled", x + 14, y, (w - 32) / 2, 22, "MASK_ENABLE", selected=mask.enabled)
    c.button("Invert", x + w / 2 + 2, y, (w - 32) / 2, 22, "INVERT_MASK", selected=mask.invert)
    y -= 28
    fields = [("strength", "Mask Strength", 0, 1)]
    if mask.source == "GENERATOR":
        fields.extend((("amount", "Amount", 0, 1), ("contrast", "Contrast", 0.01, 20)))
    if mask.source in {"GENERATOR", "NOISE"}:
        fields.append(("noise_scale", "Noise Scale", 1, 512))
        if mask.source == "GENERATOR":
            fields.append(("noise_strength", "Noise Strength", 0, 1))
    fields.extend((("black", "Black Level", 0, 1), ("white", "White Level", 0, 1)))
    for key, label, minimum, maximum in fields:
        c.slider(
            label, x + 14, y, w - 28, getattr(mask, key), "SLIDER", ("MASK", key), minimum, maximum
        )
        y -= 28
    if mask.source == "PAINT":
        c.button("Paint Mask", x + 14, y, w - 28, 22, "PAINT_MASK")
        return brush_fields(c, x, y - 28, w)
    return y


def status(c):
    project = c.context.scene.open_painter
    c.rect(0, 0, c.w, 22, d.HEADER)
    c.text("●", 13, 7, 10, d.MUTED)
    ts = active_set(c.context)
    detail = (
        f"{ts.resolution} × {ts.resolution}  |  {len(ts.layers)} layers  |  Linear PBR"
        if ts
        else "Create a texture set and assign your mesh"
    )
    c.text(c.state.message or detail, c.left + 16, 7, 10, d.MUTED, c.w - c.left - 160)
    c.text("Open Painter " + VERSION, c.w - 130, 7, 10, d.MUTED)


HELP = {
    "NEW_SET": "Create a texture set for the selected mesh",
    "ASSIGN": "Assign the selected mesh to this texture set",
    "COPY_SET": "Duplicate texture set",
    "DELETE_SET": "Remove texture set",
    "SEARCH": "Search assets by name",
    "SLIDER": "Drag to adjust · Shift for precision · Double-click to enter a value · Esc to cancel",
    "SELECT_LAYER": "Select layer · F2 to rename · Right-click for properties",
    "VISIBILITY": "Toggle layer visibility",
    "SELECT_MASK": "Edit layer mask",
    "PAINT": "Start painting on the active layer",
    "PAINT_MASK": "Paint on the active layer mask",
    "FINISH": "Finish the current paint session",
    "EXIT": "Close Open Painter in this area",
}


def tooltip(c):
    ui = c.state
    if not ui.hover or ui.drag or monotonic() - ui.hover_since < 0.7:
        return
    action, payload = ui.hover
    label = HELP.get(action)
    if action == "CHANNEL":
        label = CHANNEL_LABELS[payload]
    elif action == "BRUSH_ACTION":
        label = "Toggle eraser" if payload == "ERASE" else str(payload).title()
    elif action == "VIEW":
        label = "3D viewport" if payload == "VIEW_3D" else "2D texture view"
    elif action == "ASSET":
        label = "Apply " + payload[0]
    elif action == "POPUP":
        label = payload.replace("_", " ").title()
    if not label:
        return
    width = min(c.w - 16, d.text_width(label, d.FONT_SIZE * c.scale) / c.scale + 20)
    x = min(max(8, ui.mouse[0] / c.scale + 12), c.w - width - 8)
    y = max(25, ui.mouse[1] / c.scale - 32)
    c.widget(x, y, width, 25, "tooltip")
    c.text(label, x + 10, y + 8, 11, d.WIDGETS["tooltip"]["text"], width - 20)
