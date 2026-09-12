"""Small GPU drawing primitives, scoped to the painter workspace only."""

import math
from functools import lru_cache

import blf
import gpu
from gpu_extras.batch import batch_for_shader

BG = (0.105, 0.108, 0.115, 1)
HEADER = (0.075, 0.078, 0.084, 1)
CARD = (0.145, 0.15, 0.16, 1)
LINE = (0.22, 0.23, 0.245, 1)
TEXT = (0.82, 0.84, 0.87, 1)
MUTED = (0.48, 0.51, 0.56, 1)
ACCENT = (0.37, 0.56, 0.73, 1)
SELECT = (0.21, 0.31, 0.41, 1)
_shader = None
WIDGETS = {}
FONT_SIZE = 11
UI_SCALE = 1
PANEL_BACK = BG
PANEL_OUTLINE = LINE
PANEL_ROUNDNESS = 0.4
PANEL_HEADER = HEADER
PANEL_TITLE = TEXT


def rgba(value):
    value = tuple(value)
    return value if len(value) == 4 else value + (1,)


def over(foreground, background):
    """Resolve translucent panel colors against their UI surface, not the model."""
    foreground = rgba(foreground)
    return tuple(
        foreground[i] * foreground[3] + background[i] * (1 - foreground[3]) for i in range(3)
    ) + (1,)


def sync_theme(context):
    """Read the user's live Blender theme; never change global preferences."""
    global BG, HEADER, CARD, LINE, TEXT, MUTED, ACCENT, SELECT, FONT_SIZE, UI_SCALE
    global PANEL_HEADER, PANEL_TITLE, PANEL_BACK, PANEL_OUTLINE, PANEL_ROUNDNESS
    theme = context.preferences.themes[0]
    ui = theme.user_interface
    HEADER = tuple(theme.view_3d.space.header[:3]) + (1,)
    BG = rgba(theme.properties.space.back)
    PANEL_BACK = over(ui.panel_back, BG)
    PANEL_OUTLINE = over(ui.panel_outline, PANEL_BACK)
    PANEL_ROUNDNESS = ui.panel_roundness
    PANEL_HEADER = over(ui.panel_header, PANEL_BACK)
    PANEL_TITLE = rgba(ui.panel_title)
    CARD = rgba(ui.wcol_regular.inner)
    LINE = rgba(ui.wcol_regular.outline)
    TEXT = rgba(ui.panel_text)
    MUTED = tuple(TEXT[i] * 0.65 + BG[i] * 0.35 for i in range(3)) + (1,)
    ACCENT = rgba(ui.wcol_numslider.item)
    SELECT = rgba(ui.wcol_list_item.inner_sel)
    FONT_SIZE = context.preferences.ui_styles[0].widget.points
    UI_SCALE = context.preferences.system.ui_scale
    for kind in ("regular", "tool", "radio", "numslider", "text", "list_item", "tooltip", "tab"):
        widget = getattr(ui, "wcol_" + kind)
        WIDGETS[kind] = {
            key: rgba(getattr(widget, key))
            for key in ("inner", "inner_sel", "outline", "outline_sel", "text", "text_sel", "item")
        }
        WIDGETS[kind]["roundness"] = widget.roundness
        for key in ("show_shaded", "shadetop", "shadedown"):
            WIDGETS[kind][key] = getattr(widget, key)


@lru_cache(maxsize=1024)
def rounded_batch(x, y, w, h, radius):
    global _shader
    points = []
    for cx, cy, start in (
        (x + w - radius, y + h - radius, 0),
        (x + radius, y + h - radius, 90),
        (x + radius, y + radius, 180),
        (x + w - radius, y + radius, 270),
    ):
        for step in range(9):
            angle = math.radians(start + step * 90 / 8)
            points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    if _shader is None:
        _shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(_shader, "TRI_FAN", {"pos": points})
    return batch


def rounded_rect(x, y, w, h, radius, color):
    if w <= 0 or h <= 0:
        return
    radius = min(radius, w / 2, h / 2)
    if radius < 0.5:
        return rect(x, y, w, h, color)
    batch = rounded_batch(x, y, w, h, radius)
    _shader.bind()
    _shader.uniform_float("color", color)
    batch.draw(_shader)


def widget(x, y, w, h, kind="tool", selected=False, hover=False):
    theme = WIDGETS[kind]
    fill = theme["inner_sel" if selected else "inner"]
    if kind == "list_item" and not selected:
        # Blender leaves inactive list rows on the recessed list background;
        # wcol_list_item.inner is not their normal-state background color.
        fill = WIDGETS["text"]["inner"]
    if hover:
        fill = tuple(min(1, v + 0.045) for v in fill[:3]) + (fill[3],)
    radius = min(h, 20 * UI_SCALE) * 0.5 * theme["roundness"]
    if kind == "list_item":
        rounded_rect(x, y, w, h, radius, fill)
        return
    rounded_rect(x, y, w, h, radius, theme["outline_sel" if selected else "outline"])
    border = max(1, UI_SCALE * 0.5)
    if theme["show_shaded"]:
        shaded_rect(
            x + border,
            y + border,
            w - 2 * border,
            h - 2 * border,
            max(0, radius - border),
            fill,
            theme["shadetop"],
            theme["shadedown"],
        )
    else:
        rounded_rect(
            x + border, y + border, w - 2 * border, h - 2 * border, max(0, radius - border), fill
        )


@lru_cache(maxsize=512)
def shaded_batch(x, y, w, h, radius, fill, top, bottom):
    shader = gpu.shader.from_builtin("SMOOTH_COLOR")
    points, colors = [], []
    for cx, cy, start in (
        (x + w - radius, y + h - radius, 0),
        (x + radius, y + h - radius, 90),
        (x + radius, y + radius, 180),
        (x + w - radius, y + radius, 270),
    ):
        for step in range(9):
            angle = math.radians(start + step * 90 / 8)
            px, py = cx + math.cos(angle) * radius, cy + math.sin(angle) * radius
            shade = (bottom + (top - bottom) * (py - y) / h) / 255
            points.append((px, py))
            colors.append(tuple(max(0, min(1, v + shade)) for v in fill[:3]) + (fill[3],))
    return shader, batch_for_shader(shader, "TRI_FAN", {"pos": points, "color": colors})


def shaded_rect(x, y, w, h, radius, fill, top, bottom):
    if w <= 0 or h <= 0:
        return
    shader, batch = shaded_batch(x, y, w, h, radius, tuple(fill), top, bottom)
    shader.bind()
    batch.draw(shader)


def text_width(value, size):
    blf.size(0, size)
    return blf.dimensions(0, str(value))[0]


@lru_cache(maxsize=2048)
def rectangle_batch(x, y, w, h):
    global _shader
    if _shader is None:
        _shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    return batch_for_shader(_shader, "TRI_FAN", {"pos": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]})


def rect(x, y, w, h, color):
    if w <= 0 or h <= 0:
        return
    batch = rectangle_batch(x, y, w, h)
    _shader.bind()
    _shader.uniform_float("color", color)
    batch.draw(_shader)


def text(value, x, y, size=12, color=None, max_width=None):
    color = TEXT if color is None else color
    value = str(value)
    blf.size(0, size)
    if max_width is not None:
        if blf.dimensions(0, value)[0] > max_width:
            while value and blf.dimensions(0, value + "…")[0] > max_width:
                value = value[:-1]
            value += "…"
    blf.position(0, x, y, 0)
    blf.color(0, *color)
    blf.draw(0, value)


def line(x, y, w):
    rect(x, y, w, 1, LINE)


def sphere(x, y, r, color, roughness=0.4, style="plain"):
    """Compact lit material swatches rendered as vertex-colored geometry."""
    vertices = []
    colors = []
    indices = []
    rings, segments = 14, 40
    for ring in range(rings + 1):
        radius = ring / rings
        z = math.sqrt(max(0, 1 - radius * radius))
        for index in range(segments):
            angle = index / segments * math.tau
            nx, ny = math.cos(angle) * radius, math.sin(angle) * radius
            light = max(0, nx * -0.35 + ny * 0.5 + z * 0.8)
            spec = max(0, nx * -0.25 + ny * 0.4 + z * 0.88) ** (10 + 70 * (1 - roughness))
            variation = 1
            if style == "wood":
                variation = 0.7 + 0.3 * math.sin(nx * 25 + math.sin(ny * 5) * 2) ** 2
            elif style == "dirt":
                variation = 0.75 + 0.25 * math.sin(nx * 31 + ny * 67) ** 2
            elif style == "steel" and radius > 0.88:
                variation = 0.6
            vertices.append((x + nx * r, y + ny * r))
            colors.append(
                tuple(min(1, c * (0.22 + 0.78 * light) * variation + spec * 0.4) for c in color[:3])
                + (1,)
            )
            if ring:
                prev = (ring - 1) * segments + index
                current = ring * segments + index
                nxt = ring * segments + (index + 1) % segments
                prev_next = (ring - 1) * segments + (index + 1) % segments
                indices.extend([(prev, current, nxt), (prev, nxt, prev_next)])
    shader = gpu.shader.from_builtin("SMOOTH_COLOR")
    batch = batch_for_shader(shader, "TRIS", {"pos": vertices, "color": colors}, indices=indices)
    shader.bind()
    batch.draw(shader)


def circle(x, y, r, color):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    vertices = [(x, y)] + [
        (x + math.cos(i / 24 * math.tau) * r, y + math.sin(i / 24 * math.tau) * r)
        for i in range(25)
    ]
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": vertices})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


@lru_cache(maxsize=128)
def image_batch(x, y, w, h):
    """Draw the actual imported alpha image instead of a material swatch."""
    shader = gpu.shader.from_builtin("IMAGE")
    batch = batch_for_shader(
        shader,
        "TRI_FAN",
        {
            "pos": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
            "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1)],
        },
    )
    return shader, batch


def image_preview(image, x, y, w, h):
    shader, batch = image_batch(x, y, w, h)
    shader.bind()
    shader.uniform_sampler("image", gpu.texture.from_image(image))
    batch.draw(shader)


@lru_cache(maxsize=64)
def tool_batch(x, y, size, name):
    global _shader
    if _shader is None:_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shapes = {
        "P": [[(.2,.2),(.35,.48),(.77,.88),(.9,.75),(.48,.34),(.2,.2)],[(.4,.43),(.55,.3)]],
        "E": [[(.12,.4),(.55,.87),(.9,.56),(.49,.13),(.3,.13),(.12,.4)],[(.3,.58),(.66,.3)]],
        "M": [[(.15,.15),(.85,.15),(.85,.85),(.15,.85),(.15,.15)],[(.5,.15),(.5,.85)]],
        "F": [[(.16,.5),(.4,.25),(.87,.8)]],
        "3D": [[(.15,.28),(.5,.1),(.86,.28),(.86,.72),(.5,.92),(.15,.72),(.15,.28)],[(.15,.72),(.5,.5),(.86,.72)],[(.5,.5),(.5,.1)]],
        "2D": [[(.15,.15),(.85,.15),(.85,.85),(.15,.85),(.15,.15)],[(.15,.15),(.85,.85)]],
    }
    vertices=[]
    for path in shapes[name]:
        for a,b in zip(path,path[1:]):vertices.extend([(x+a[0]*size,y+a[1]*size),(x+b[0]*size,y+b[1]*size)])
    return batch_for_shader(_shader,"LINES",{"pos":vertices})


def tool_icon(x,y,size,name):
    batch=tool_batch(x,y,size,name)
    _shader.bind();_shader.uniform_float("color",TEXT);gpu.state.line_width_set(1.5)
    try:batch.draw(_shader)
    finally:gpu.state.line_width_set(1)
