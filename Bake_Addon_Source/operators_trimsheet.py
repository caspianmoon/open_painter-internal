# bakeaddon/operators_trimsheet.py

import json
import math
import os
import random
import uuid

import blf
import bmesh
import bpy
import gpu
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)
from bpy_extras.io_utils import ExportHelper, ImportHelper
from bpy.types import Operator, Panel, PropertyGroup, UIList
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

from . import shared_state


MIN_TRIM_SIZE = 0.001
HANDLE_PX = 8.0
TRIM_FILL_ALPHA = 0.0
ASSIGN_OPERATOR = None
DRAW_OPERATOR = None
EDIT_OPERATOR = None
EYEDROPPER_OPERATOR = None
MAIN_DRAW_HANDLE = None
TRIM_SCALE_FACE_LAYER = "bakeaddon_trim_patch"
TRIM_SCALE_EDGE_PATCH_LAYER = "bakeaddon_trim_edge_patch"
TRIM_SCALE_EDGE_ROLE_LAYER = "bakeaddon_trim_edge_role"
TRIM_SCALE_EDGE_ID_LAYER = "bakeaddon_trim_edge_id"
TRIM_SCALE_EDGE_GENERATED = 1 << 0
TRIM_SCALE_EDGE_SOURCE = 1 << 1
TRIM_SCALE_EDGE_USER_PROTECTED = 1 << 2
TRIM_SCALE_STASH_EPS = 1e-5
TRIM_SCALE_BOUNDARY_MERGE_DIST = 1e-8
TRIM_SCALE_INTERNAL_MERGE_DIST = 1e-6
TRIM_SCALE_FINAL_MERGE_DIST = 0.00001


def set_blf_size(font_id, size):
    try:
        blf.size(font_id, size)
    except TypeError:
        blf.size(font_id, size, bpy.context.preferences.system.dpi or 72)


def draw_shadowed_text(font_id, x, y, text, size, color=(1.0, 1.0, 1.0, 1.0)):
    set_blf_size(font_id, size)
    blf.color(font_id, 0.0, 0.0, 0.0, 0.68)
    blf.position(font_id, x + 1, y - 1, 0)
    blf.draw(font_id, text)
    blf.color(font_id, *color)
    blf.position(font_id, x, y, 0)
    blf.draw(font_id, text)


def draw_escape_cancel_hint(font_id=0, x=16, y=18):
    draw_shadowed_text(font_id, x, y, "Press ESC to cancel", 16, (1.0, 0.12, 0.08, 1.0))


def get_uniform_shader():
    for name in ("UNIFORM_COLOR", "2D_UNIFORM_COLOR"):
        try:
            return gpu.shader.from_builtin(name)
        except Exception:
            continue
    return None


def active_image_from_context(context):
    space = getattr(context, "space_data", None)
    if space and getattr(space, "type", None) == "IMAGE_EDITOR":
        return getattr(space, "image", None)
    return None


def trim_settings(context):
    return getattr(context.scene, "bakeaddon_trim_settings", None)


def random_trim_color():
    hue = random.random()
    saturation = 0.55 + random.random() * 0.35
    value = 0.80 + random.random() * 0.18
    r, g, b = hsv_to_rgb(hue, saturation, value)
    return (r, g, b, 0.5)


def hsv_to_rgb(h, s, v):
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i %= 6
    if i == 0:
        return v, t, p
    if i == 1:
        return q, v, p
    if i == 2:
        return p, v, t
    if i == 3:
        return p, q, v
    if i == 4:
        return t, p, v
    return v, p, q


def normalized_rect(trim):
    left = min(trim.left, trim.right)
    right = max(trim.left, trim.right)
    bottom = min(trim.bottom, trim.top)
    top = max(trim.bottom, trim.top)
    return left, bottom, right, top


def set_trim_rect(trim, left, bottom, right, top):
    left, right = sorted((max(0.0, min(1.0, left)), max(0.0, min(1.0, right))))
    bottom, top = sorted((max(0.0, min(1.0, bottom)), max(0.0, min(1.0, top))))

    if right - left < MIN_TRIM_SIZE:
        center = (left + right) * 0.5
        left = center - MIN_TRIM_SIZE * 0.5
        right = center + MIN_TRIM_SIZE * 0.5
    if top - bottom < MIN_TRIM_SIZE:
        center = (bottom + top) * 0.5
        bottom = center - MIN_TRIM_SIZE * 0.5
        top = center + MIN_TRIM_SIZE * 0.5

    if left < 0.0:
        right -= left
        left = 0.0
    if right > 1.0:
        left -= right - 1.0
        right = 1.0
    if bottom < 0.0:
        top -= bottom
        bottom = 0.0
    if top > 1.0:
        bottom -= top - 1.0
        top = 1.0

    trim.left = max(0.0, min(1.0 - MIN_TRIM_SIZE, left))
    trim.right = max(trim.left + MIN_TRIM_SIZE, min(1.0, right))
    trim.bottom = max(0.0, min(1.0 - MIN_TRIM_SIZE, bottom))
    trim.top = max(trim.bottom + MIN_TRIM_SIZE, min(1.0, top))


def copy_trim_data(dst, src):
    dst.name = getattr(src, "name", "Trim") or "Trim"
    dst.uuid = getattr(src, "uuid", "") or str(uuid.uuid4())
    dst.left = getattr(src, "left", 0.25)
    dst.bottom = getattr(src, "bottom", 0.25)
    dst.right = getattr(src, "right", 0.75)
    dst.top = getattr(src, "top", 0.75)
    dst.color = getattr(src, "color", (0.2, 0.7, 1.0, 0.5))
    dst.selected = getattr(src, "selected", False)


def trim_to_preset_dict(trim):
    left, bottom, right, top = normalized_rect(trim)
    return {
        "name": trim.name,
        "uuid": trim.uuid or str(uuid.uuid4()),
        "rect": {"left": left, "bottom": bottom, "right": right, "top": top},
        "color": [float(value) for value in trim.color],
        "selected": bool(trim.selected),
    }


def apply_preset_dict_to_trim(trim, data):
    if not isinstance(data, dict):
        raise ValueError("Trim entry must be an object.")

    trim.name = str(data.get("name") or "Trim")
    trim.uuid = str(data.get("uuid") or uuid.uuid4())

    rect = data.get("rect", {})
    if isinstance(rect, dict):
        left = rect.get("left", data.get("left", 0.25))
        bottom = rect.get("bottom", data.get("bottom", 0.25))
        right = rect.get("right", data.get("right", 0.75))
        top = rect.get("top", data.get("top", 0.75))
    else:
        values = list(rect) if isinstance(rect, (list, tuple)) else [0.25, 0.25, 0.75, 0.75]
        values = (values + [0.25, 0.25, 0.75, 0.75])[:4]
        left, bottom, right, top = values
    set_trim_rect(trim, float(left), float(bottom), float(right), float(top))

    color = data.get("color", (0.2, 0.7, 1.0, 0.5))
    if not isinstance(color, (list, tuple)):
        color = (0.2, 0.7, 1.0, 0.5)
    values = list(color)[:4]
    while len(values) < 4:
        values.append(0.5 if len(values) == 3 else 1.0)
    trim.color = tuple(max(0.0, min(1.0, float(value))) for value in values)
    trim.selected = bool(data.get("selected", False))


def active_face_material_index(context, obj):
    if not obj or obj.type != "MESH":
        return -1
    if context.mode == "EDIT_MESH":
        try:
            bm = bmesh.from_edit_mesh(obj.data)
            active = bm.select_history.active
            if isinstance(active, bmesh.types.BMFace) and active.is_valid:
                return active.material_index
            for face in bm.faces:
                if face.select and face.is_valid:
                    return face.material_index
        except Exception:
            pass
    return obj.active_material_index


def active_trim_material(context, create=False):
    obj = getattr(context, "active_object", None)
    if not obj or obj.type != "MESH":
        return None

    index = active_face_material_index(context, obj)
    if index < 0:
        index = obj.active_material_index

    if not obj.material_slots:
        if not create:
            return None
        material = bpy.data.materials.new("Bake Addon Trim Material")
        obj.data.materials.append(material)
        obj.active_material_index = 0
        return material

    index = max(0, min(index, len(obj.material_slots) - 1))
    material = obj.material_slots[index].material
    if material is None and create:
        material = bpy.data.materials.new("Bake Addon Trim Material")
        obj.material_slots[index].material = material
    return material


def copy_trims_to_material(material, trims, active_index=0):
    if material is None or not hasattr(material, "bakeaddon_trim_sections"):
        return 0
    if len(material.bakeaddon_trim_sections) > 0:
        return 0
    for old_trim in trims:
        new_trim = material.bakeaddon_trim_sections.add()
        copy_trim_data(new_trim, old_trim)
    if len(material.bakeaddon_trim_sections) == 0:
        material.bakeaddon_trim_index = -1
    else:
        material.bakeaddon_trim_index = max(0, min(active_index, len(material.bakeaddon_trim_sections) - 1))
    return len(material.bakeaddon_trim_sections)


def migrate_legacy_trims_to_material(context, material):
    if material is None or len(material.bakeaddon_trim_sections) > 0:
        return 0

    scene = getattr(context, "scene", None)
    if scene and hasattr(scene, "bakeaddon_trim_sections") and len(scene.bakeaddon_trim_sections) > 0:
        migrated = copy_trims_to_material(
            material,
            scene.bakeaddon_trim_sections,
            getattr(scene, "bakeaddon_trim_index", 0),
        )
        if migrated:
            scene.bakeaddon_trim_sections.clear()
            scene.bakeaddon_trim_index = -1
        return migrated

    image = active_image_from_context(context)
    if image and hasattr(image, "bakeaddon_trim_sections") and len(image.bakeaddon_trim_sections) > 0:
        return copy_trims_to_material(
            material,
            image.bakeaddon_trim_sections,
            getattr(image, "bakeaddon_trim_index", 0),
        )

    legacy_images = [
        image for image in bpy.data.images
        if hasattr(image, "bakeaddon_trim_sections") and len(image.bakeaddon_trim_sections) > 0
    ]
    if len(legacy_images) == 1:
        image = legacy_images[0]
        return copy_trims_to_material(
            material,
            image.bakeaddon_trim_sections,
            getattr(image, "bakeaddon_trim_index", 0),
        )
    return 0


def trim_owner(context, create=False, migrate=True):
    material = active_trim_material(context, create=create)
    if material and migrate:
        migrate_legacy_trims_to_material(context, material)
    return material


def trim_sections(context, create=False):
    material = trim_owner(context, create=create)
    return material.bakeaddon_trim_sections if material else None


def ensure_trim_index(material, write=True):
    if material is None:
        return -1
    count = len(material.bakeaddon_trim_sections)
    if count == 0:
        if write:
            material.bakeaddon_trim_index = -1
        return -1
    index = material.bakeaddon_trim_index
    if index < 0 or index >= count:
        index = count - 1
        if write:
            material.bakeaddon_trim_index = index
    return index


def set_active_trim_index(context, index):
    material = trim_owner(context)
    if material is None:
        return False
    material.bakeaddon_trim_index = index
    return True


def trim_material_to_preset(context):
    material = trim_owner(context)
    if material is None:
        raise RuntimeError("Select a mesh material before exporting trims.")
    return {
        "format": "Bake Addon 2.1 Trim Sheet Preset",
        "extension": ".gtrim",
        "version": 1,
        "name": material.name,
        "active_index": ensure_trim_index(material),
        "trims": [trim_to_preset_dict(trim) for trim in material.bakeaddon_trim_sections],
    }


def import_trims_from_preset(context, data, replace=True):
    if not isinstance(data, dict):
        raise ValueError("Preset file must contain a JSON object.")
    trims = data.get("trims")
    if not isinstance(trims, list):
        raise ValueError("Preset file does not contain a trims list.")

    material = trim_owner(context, create=True, migrate=False)
    if material is None:
        raise ValueError("Select a mesh object or material before importing trims.")
    if replace:
        material.bakeaddon_trim_sections.clear()

    for trim_data in trims:
        trim = material.bakeaddon_trim_sections.add()
        apply_preset_dict_to_trim(trim, trim_data)

    active_index = int(data.get("active_index", 0 if trims else -1))
    if len(material.bakeaddon_trim_sections) == 0:
        material.bakeaddon_trim_index = -1
    else:
        material.bakeaddon_trim_index = max(0, min(active_index, len(material.bakeaddon_trim_sections) - 1))
    tag_image_editors(context)
    return len(trims)


def active_trim(context, write=False):
    material = trim_owner(context)
    index = ensure_trim_index(material, write=write)
    if index == -1:
        return None
    return material.bakeaddon_trim_sections[index]


def active_trim_rect(context):
    trim = active_trim(context)
    if trim:
        return normalized_rect(trim)
    return 0.0, 0.0, 1.0, 1.0


def window_region_for_area(area):
    if not area:
        return None
    for region in area.regions:
        if region.type == "WINDOW":
            return region
    return None


def event_local_xy(area, event):
    region = window_region_for_area(area)
    if not region:
        return None
    x = event.mouse_x - region.x
    y = event.mouse_y - region.y
    if x < 0 or y < 0 or x > region.width or y > region.height:
        return None
    return region, x, y


def uv_from_event(area, event):
    local = event_local_xy(area, event)
    if not local:
        return None
    region, x, y = local
    return Vector(region.view2d.region_to_view(x, y))


def event_in_non_window_region(area, event):
    if not area:
        return False
    if not (area.x <= event.mouse_x <= area.x + area.width and area.y <= event.mouse_y <= area.y + area.height):
        return False
    for region in area.regions:
        if region.type == "WINDOW":
            continue
        if region.x <= event.mouse_x <= region.x + region.width and region.y <= event.mouse_y <= region.y + region.height:
            return True
    return False


def trim_screen_rect(region, trim):
    left, bottom, right, top = normalized_rect(trim)
    x1, y1 = region.view2d.view_to_region(left, bottom, clip=False)
    x2, y2 = region.view2d.view_to_region(right, top, clip=False)
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def point_in_trim(trim, uv):
    left, bottom, right, top = normalized_rect(trim)
    return left <= uv.x <= right and bottom <= uv.y <= top


def image_area_under_mouse(context, event):
    screen = getattr(context.window, "screen", None)
    if not screen:
        return None, None

    for area in screen.areas:
        if area.type != "IMAGE_EDITOR":
            continue
        if not (area.x <= event.mouse_x <= area.x + area.width and area.y <= event.mouse_y <= area.y + area.height):
            continue
        region = window_region_for_area(area)
        if not region:
            continue
        x = event.mouse_x - region.x
        y = event.mouse_y - region.y
        if x < 0 or y < 0 or x > region.width or y > region.height:
            continue
        return area, region
    return None, None


def uv_under_mouse(context, event):
    area, region = image_area_under_mouse(context, event)
    if not area or not region:
        return None, None
    local_x = event.mouse_x - region.x
    local_y = event.mouse_y - region.y
    return area, Vector(region.view2d.region_to_view(local_x, local_y))


def uv_under_mouse_in_image_area(context, event):
    screen = getattr(context.window, "screen", None)
    if not screen:
        return None, None

    for area in screen.areas:
        if area.type != "IMAGE_EDITOR":
            continue
        if not (area.x <= event.mouse_x <= area.x + area.width and area.y <= event.mouse_y <= area.y + area.height):
            continue
        region = window_region_for_area(area)
        if not region:
            continue

        local_y = event.mouse_y - region.y
        if local_y < 0 or local_y > region.height:
            continue

        local_x = event.mouse_x - region.x
        return area, Vector(region.view2d.region_to_view(local_x, local_y))

    return None, None


def tag_image_editors(context):
    screen = getattr(context.window, "screen", None)
    if not screen:
        return
    for area in screen.areas:
        if area.type == "IMAGE_EDITOR":
            area.tag_redraw()


def draw_rect(shader, x1, y1, x2, y2, color):
    verts = ((x1, y1), (x2, y1), (x2, y2), (x1, y1), (x2, y2), (x1, y2))
    batch = batch_for_shader(shader, "TRIS", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def draw_outline(shader, x1, y1, x2, y2, color, width=1.0):
    verts = ((x1, y1), (x2, y1), (x2, y1), (x2, y2), (x2, y2), (x1, y2), (x1, y2), (x1, y1))
    try:
        gpu.state.line_width_set(width)
    except Exception:
        pass
    batch = batch_for_shader(shader, "LINES", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    try:
        gpu.state.line_width_set(1.0)
    except Exception:
        pass


def preview_screen_rect(region, section):
    x1, y1 = region.view2d.view_to_region(
        section["left"],
        section["bottom"],
        clip=False,
    )
    x2, y2 = region.view2d.view_to_region(
        section["right"],
        section["top"],
        clip=False,
    )
    return (
        min(x1, x2),
        min(y1, y2),
        max(x1, x2),
        max(y1, y2),
    )


def fitted_preview_label(name, max_width, max_height):
    if not name or max_width < 22.0 or max_height < 14.0:
        return None
    font_id = 0
    for size in range(13, 7, -1):
        set_blf_size(font_id, size)
        width, height = blf.dimensions(font_id, name)
        if width <= max_width and height <= max_height:
            return name, size, width, height

    size = 8
    ellipsis = "\u2026"
    set_blf_size(font_id, size)
    ellipsis_width, ellipsis_height = blf.dimensions(
        font_id,
        ellipsis,
    )
    if (
        ellipsis_width > max_width
        or ellipsis_height > max_height
    ):
        return None
    low = 0
    high = len(name)
    best = ellipsis
    best_dimensions = (ellipsis_width, ellipsis_height)
    while low <= high:
        middle = (low + high) // 2
        candidate = name[:middle].rstrip() + ellipsis
        width, height = blf.dimensions(font_id, candidate)
        if width <= max_width and height <= max_height:
            best = candidate
            best_dimensions = (width, height)
            low = middle + 1
        else:
            high = middle - 1
    return best, size, best_dimensions[0], best_dimensions[1]


def draw_trim_layout_preview(region, shader, preview):
    atlas_x1, atlas_y1 = region.view2d.view_to_region(
        0.0,
        0.0,
        clip=False,
    )
    atlas_x2, atlas_y2 = region.view2d.view_to_region(
        1.0,
        1.0,
        clip=False,
    )
    draw_outline(
        shader,
        min(atlas_x1, atlas_x2),
        min(atlas_y1, atlas_y2),
        max(atlas_x1, atlas_x2),
        max(atlas_y1, atlas_y2),
        (0.92, 0.95, 1.0, 0.62),
        width=1.2,
    )

    highlighted_materials = set(
        preview.get("highlight_material_names", ())
    )
    highlight_color = (1.0, 0.28, 0.035)
    neutral_color = (0.38, 0.41, 0.46)
    for section in preview.get("sections", ()):
        x1, y1, x2, y2 = preview_screen_rect(
            region,
            section,
        )
        is_highlighted = (
            section["name"] in highlighted_materials
        )
        draw_rect(
            shader,
            x1,
            y1,
            x2,
            y2,
            (
                (
                    highlight_color[0],
                    highlight_color[1],
                    highlight_color[2],
                    0.30,
                )
                if is_highlighted
                else (
                    neutral_color[0],
                    neutral_color[1],
                    neutral_color[2],
                    0.075,
                )
            ),
        )
        draw_outline(
            shader,
            x1,
            y1,
            x2,
            y2,
            (
                (
                    highlight_color[0],
                    highlight_color[1],
                    highlight_color[2],
                    1.0,
                )
                if is_highlighted
                else (
                    neutral_color[0],
                    neutral_color[1],
                    neutral_color[2],
                    0.68,
                )
            ),
            width=2.8 if is_highlighted else 1.1,
        )
        fitted = fitted_preview_label(
            section["name"],
            max(0.0, x2 - x1 - 12.0),
            max(0.0, y2 - y1 - 8.0),
        )
        if fitted is None:
            continue
        text, size, width, height = fitted
        draw_shadowed_text(
            0,
            x1 + (x2 - x1 - width) * 0.5,
            y1 + (y2 - y1 - height) * 0.5,
            text,
            size,
            (
                (1.0, 0.94, 0.88, 1.0)
                if is_highlighted
                else (0.72, 0.75, 0.80, 0.88)
            ),
        )


def draw_trim_overlay():
    context = bpy.context
    area = getattr(context, "area", None)
    if not area or area.type != "IMAGE_EDITOR":
        return

    settings = trim_settings(context)
    preview_settings = getattr(
        context.scene,
        "bakeaddon_texture_to_trim_settings",
        None,
    )
    preview_enabled = bool(
        preview_settings
        and preview_settings.layout_preview_enabled
    )
    interactive_trim_mode = bool(
        settings
        and (
            settings.assign_enabled
            or settings.edit_enabled
            or settings.draw_trim_active
            or ASSIGN_OPERATOR is not None
            or DRAW_OPERATOR is not None
            or EDIT_OPERATOR is not None
            or EYEDROPPER_OPERATOR is not None
        )
    )
    preview = None
    if preview_enabled and not interactive_trim_mode:
        preview_key = (
            shared_state.trim_layout_preview_context_key(
                context
            )
        )
        if preview_key is not None:
            preview = shared_state.trim_layout_preview_cache.get(
                preview_key
            )

    if preview_enabled and not interactive_trim_mode:
        if not preview or preview.get("status") != "READY":
            return
        region = window_region_for_area(area)
        shader = get_uniform_shader()
        if not region or not shader:
            return
        try:
            gpu.state.blend_set("ALPHA")
            draw_trim_layout_preview(
                region,
                shader,
                preview,
            )
        finally:
            gpu.state.blend_set("NONE")
        return

    if not settings or not settings.draw_enabled:
        return

    material = trim_owner(context)
    trims = material.bakeaddon_trim_sections if material else ()
    active_index = ensure_trim_index(material, write=False)
    region = window_region_for_area(area)
    shader = get_uniform_shader()
    if not region or not shader:
        return

    hover_index = -1
    if ASSIGN_OPERATOR is not None:
        hover_index = ASSIGN_OPERATOR.hover_index

    try:
        gpu.state.blend_set("ALPHA")
        for idx, trim in enumerate(trims):
            x1, y1, x2, y2 = trim_screen_rect(region, trim)
            is_active = idx == active_index
            is_hovered = idx == hover_index

            fill = trim.color[:]
            alpha = 0.018 if is_hovered else TRIM_FILL_ALPHA
            if alpha > 0.0:
                draw_rect(shader, x1, y1, x2, y2, (fill[0], fill[1], fill[2], alpha))

            border_color = (1.0, 0.82, 0.22, 1.0) if is_active else (fill[0], fill[1], fill[2], 0.9)
            if is_hovered:
                border_color = (0.35, 0.95, 1.0, 1.0)
            draw_outline(shader, x1, y1, x2, y2, border_color, width=2.4 if is_active or is_hovered else 1.2)

            if settings.edit_enabled and is_active:
                for hx, hy in ((x1, y1), (x1, y2), (x2, y1), (x2, y2)):
                    draw_rect(shader, hx - 3, hy - 3, hx + 3, hy + 3, (1.0, 1.0, 1.0, 0.86))

            if trim.name:
                draw_shadowed_text(0, x1 + 6, y2 - 18, trim.name, 11, (1.0, 1.0, 1.0, 0.95))

        if settings.assign_enabled:
            draw_shadowed_text(0, 16, 118, "Assign to Trim", 16, (0.96, 0.98, 1.0, 1.0))
            draw_shadowed_text(0, 16, 96, "Click trim to assign selected faces", 13, (0.4, 0.95, 1.0, 1.0))
            draw_shadowed_text(0, 16, 74, "Toggle Assign to Trim to stop", 12, (0.78, 0.82, 0.86, 1.0))

        if settings.draw_trim_active or DRAW_OPERATOR is not None:
            draw_shadowed_text(0, 16, 96, "Draw Trim", 16, (0.96, 0.98, 1.0, 1.0))
            draw_shadowed_text(0, 16, 74, "LMB drag to create trim sections", 13, (0.4, 0.95, 1.0, 1.0))

        if settings.assign_enabled or settings.edit_enabled or settings.draw_trim_active or DRAW_OPERATOR is not None or EYEDROPPER_OPERATOR is not None:
            draw_escape_cancel_hint()

    finally:
        gpu.state.blend_set("NONE")


def ensure_trimsheet_draw_handler():
    global MAIN_DRAW_HANDLE
    if MAIN_DRAW_HANDLE is not None:
        return
    MAIN_DRAW_HANDLE = bpy.types.SpaceImageEditor.draw_handler_add(draw_trim_overlay, (), "WINDOW", "POST_PIXEL")
    shared_state.trimsheet_draw_handles.append(MAIN_DRAW_HANDLE)


def remove_trimsheet_draw_handlers():
    global MAIN_DRAW_HANDLE, ASSIGN_OPERATOR, DRAW_OPERATOR, EDIT_OPERATOR, EYEDROPPER_OPERATOR
    if ASSIGN_OPERATOR is not None:
        ASSIGN_OPERATOR.request_stop = True
    if DRAW_OPERATOR is not None:
        DRAW_OPERATOR.request_stop = True
    if EDIT_OPERATOR is not None:
        EDIT_OPERATOR.request_stop = True
    if EYEDROPPER_OPERATOR is not None:
        EYEDROPPER_OPERATOR.request_stop = True
    for scene in bpy.data.scenes:
        settings = getattr(scene, "bakeaddon_trim_settings", None)
        if settings:
            settings.assign_enabled = False
            settings.edit_enabled = False
            settings.draw_trim_active = False
    handles = list(shared_state.trimsheet_draw_handles)
    shared_state.trimsheet_draw_handles.clear()
    for handle in handles:
        try:
            bpy.types.SpaceImageEditor.draw_handler_remove(handle, "WINDOW")
        except ValueError:
            pass
    MAIN_DRAW_HANDLE = None
    ASSIGN_OPERATOR = None
    DRAW_OPERATOR = None
    EDIT_OPERATOR = None
    EYEDROPPER_OPERATOR = None


class BAKEADDON_PG_TrimSection(PropertyGroup):
    name: StringProperty(name="Name", default="Trim")
    uuid: StringProperty(name="UUID", default="")
    left: FloatProperty(name="Left", default=0.25, min=0.0, max=1.0, precision=5)
    bottom: FloatProperty(name="Bottom", default=0.25, min=0.0, max=1.0, precision=5)
    right: FloatProperty(name="Right", default=0.75, min=0.0, max=1.0, precision=5)
    top: FloatProperty(name="Top", default=0.75, min=0.0, max=1.0, precision=5)
    color: FloatVectorProperty(
        name="Color",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(0.2, 0.7, 1.0, 0.5),
    )
    selected: BoolProperty(name="Selected", default=False)


class BAKEADDON_PG_TrimSettings(PropertyGroup):
    edit_enabled: BoolProperty(name="Edit Trim Sheet", default=False)
    draw_enabled: BoolProperty(name="Show Trims", default=True)
    draw_trim_active: BoolProperty(name="Draw Trim Active", default=False, options={"HIDDEN"})
    assign_enabled: BoolProperty(name="Assign to Trim", default=False)
    assign_settings_expanded: BoolProperty(name="Assignment Settings", default=True)
    assign_uv_unwrap: BoolProperty(name="UV Unwrap", default=False)
    assign_world_orient: BoolProperty(name="World Orient", default=False)
    assign_stretch_fill: BoolProperty(name="Stretch Fill", default=False)
    assign_islands_to_trim: BoolProperty(name="Islands to Trim", default=False)
    assign_faces_to_trim: BoolProperty(name="Faces to Trim", default=False)
    padding: FloatProperty(name="Padding", default=0.0, min=0.0, max=0.25, precision=4)
    migrated_image_trims: BoolProperty(name="Migrated Image Trims", default=False, options={"HIDDEN", "SKIP_SAVE"})


class BAKEADDON_UL_trim_sections(UIList):
    def draw_item(self, _context, layout, _data, item, icon, _active_data, _active_propname, _index):
        row = layout.row(align=True)
        row.prop(item, "color", text="")
        row.prop(item, "name", text="", emboss=False, icon_value=icon)


class BAKEADDON_OT_trim_add(Operator):
    bl_idname = "image.bakeaddon_trim_add"
    bl_label = "Add Trim"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return active_trim_material(context, create=False) is not None or bool(
            getattr(context, "active_object", None) and context.active_object.type == "MESH"
        )

    def execute(self, context):
        material = trim_owner(context, create=True)
        if material is None:
            self.report({"WARNING"}, "Select a mesh material first.")
            return {"CANCELLED"}
        trim = material.bakeaddon_trim_sections.add()
        trim.name = f"Trim {len(material.bakeaddon_trim_sections)}"
        trim.uuid = str(uuid.uuid4())
        trim.color = random_trim_color()
        set_trim_rect(trim, 0.25, 0.25, 0.75, 0.75)
        material.bakeaddon_trim_index = len(material.bakeaddon_trim_sections) - 1
        tag_image_editors(context)
        return {"FINISHED"}


class BAKEADDON_OT_trim_remove(Operator):
    bl_idname = "image.bakeaddon_trim_remove"
    bl_label = "Remove Trim"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        trims = trim_sections(context)
        return bool(trims and len(trims) > 0)

    def execute(self, context):
        material = trim_owner(context)
        index = ensure_trim_index(material)
        if index == -1:
            return {"CANCELLED"}
        material.bakeaddon_trim_sections.remove(index)
        material.bakeaddon_trim_index = min(index, len(material.bakeaddon_trim_sections) - 1)
        tag_image_editors(context)
        return {"FINISHED"}


class BAKEADDON_OT_trim_export_preset(Operator, ExportHelper):
    bl_idname = "uv.bakeaddon_trim_export_preset"
    bl_label = "Export Trim Preset"
    bl_description = "Export current trim sections to a .gtrim preset"
    filename_ext = ".gtrim"
    filter_glob: StringProperty(default="*.gtrim", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        trims = trim_sections(context)
        return bool(trims and len(trims) > 0)

    def invoke(self, context, event):
        if not self.filepath:
            name = context.scene.name if context.scene else "trim_sheet"
            self.filepath = bpy.path.clean_name(name) + self.filename_ext
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        filepath = self.filepath
        if filepath and not filepath.lower().endswith(self.filename_ext):
            filepath += self.filename_ext

        try:
            data = trim_material_to_preset(context)
            with open(filepath, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=False)
                handle.write("\n")
        except Exception as exc:
            self.report({"ERROR"}, f"Could not export trim preset: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Exported {len(data['trims'])} trims to {os.path.basename(filepath)}.")
        return {"FINISHED"}


class BAKEADDON_OT_trim_import_preset(Operator, ImportHelper):
    bl_idname = "uv.bakeaddon_trim_import_preset"
    bl_label = "Import Trim Preset"
    bl_description = "Import trim sections from a .gtrim preset"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".gtrim"
    filter_glob: StringProperty(default="*.gtrim", options={"HIDDEN"})
    replace_existing: BoolProperty(name="Replace Existing", default=True)

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def draw(self, _context):
        self.layout.prop(self, "replace_existing")

    def execute(self, context):
        try:
            with open(self.filepath, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            count = import_trims_from_preset(context, data, replace=self.replace_existing)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not import trim preset: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Imported {count} trims from {os.path.basename(self.filepath)}.")
        return {"FINISHED"}


class BAKEADDON_OT_trim_draw(Operator):
    bl_idname = "uv.bakeaddon_trim_draw"
    bl_label = "Draw Trim"
    bl_description = "Drag in the UV Editor to create a trim section"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    created_index = -1
    start_uv = None
    dragging = False
    request_stop = False

    @classmethod
    def poll(cls, context):
        return bool(context.area and context.area.type == "IMAGE_EDITOR" and context.scene)

    def invoke(self, context, _event):
        global DRAW_OPERATOR
        settings = trim_settings(context)
        if DRAW_OPERATOR is not None and (not settings or settings.draw_trim_active):
            if settings:
                settings.draw_trim_active = False
            DRAW_OPERATOR.request_stop = True
            tag_image_editors(context)
            return {"FINISHED"}
        DRAW_OPERATOR = None
        material = trim_owner(context, create=True)
        if material is None:
            self.report({"WARNING"}, "Select a mesh material first.")
            return {"CANCELLED"}
        self.created_index = -1
        self.start_uv = None
        self.dragging = False
        self.request_stop = False
        if settings:
            settings.draw_enabled = True
            settings.draw_trim_active = True
        ensure_trimsheet_draw_handler()
        DRAW_OPERATOR = self
        try:
            context.window.cursor_modal_set("CROSSHAIR")
        except Exception:
            pass
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Draw Trim: LMB drag in UV space | Esc or Draw Trim button to stop")
        tag_image_editors(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if self.request_stop:
            return self.finish(context, cancelled=False)

        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"} or event.type.startswith("NDOF"):
            return {"PASS_THROUGH"}

        if event.type == "RIGHTMOUSE" and event.alt:
            return {"PASS_THROUGH"}

        material = trim_owner(context)
        if not material:
            return self.finish(context, cancelled=True)
        trims = material.bakeaddon_trim_sections

        if event.type == "ESC":
            if self.dragging and self.created_index != -1 and self.created_index < len(trims):
                trims.remove(self.created_index)
            return self.finish(context, cancelled=False)

        if event.type == "RIGHTMOUSE":
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            _area, uv = uv_under_mouse_in_image_area(context, event)
            if uv is None:
                return {"PASS_THROUGH"}
            trim = trims.add()
            trim.name = f"Trim {len(trims)}"
            trim.uuid = str(uuid.uuid4())
            trim.color = random_trim_color()
            set_trim_rect(trim, uv.x, uv.y, uv.x + MIN_TRIM_SIZE, uv.y + MIN_TRIM_SIZE)
            material.bakeaddon_trim_index = len(trims) - 1
            self.created_index = material.bakeaddon_trim_index
            self.start_uv = uv
            self.dragging = True
            tag_image_editors(context)
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and self.dragging and self.created_index != -1:
            _area, uv = uv_under_mouse_in_image_area(context, event)
            if uv is not None and self.created_index < len(trims):
                set_trim_rect(trims[self.created_index], self.start_uv.x, self.start_uv.y, uv.x, uv.y)
                tag_image_editors(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE" and self.dragging:
            if self.created_index != -1 and self.created_index < len(trims):
                trim = trims[self.created_index]
                left, bottom, right, top = normalized_rect(trim)
                if right - left <= MIN_TRIM_SIZE * 2 or top - bottom <= MIN_TRIM_SIZE * 2:
                    trims.remove(self.created_index)
                    material.bakeaddon_trim_index = min(self.created_index, len(trims) - 1)
                    self.report({"WARNING"}, "Trim was too small and was removed.")
            self.created_index = -1
            self.start_uv = None
            self.dragging = False
            tag_image_editors(context)
            return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    def finish(self, context, cancelled=False):
        global DRAW_OPERATOR
        if DRAW_OPERATOR is self:
            DRAW_OPERATOR = None
        settings = trim_settings(context)
        if settings:
            settings.draw_trim_active = False
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass
        if context.area:
            context.area.header_text_set(None)
            context.area.tag_redraw()
        self.dragging = False
        return {"CANCELLED"} if cancelled else {"FINISHED"}


def hit_test_trim(area, material, event):
    local = event_local_xy(area, event)
    if not local:
        return -1, None, None
    region, x, y = local
    uv = Vector(region.view2d.region_to_view(x, y))
    trims = material.bakeaddon_trim_sections if material else ()

    for idx in reversed(range(len(trims))):
        trim = trims[idx]
        sx1, sy1, sx2, sy2 = trim_screen_rect(region, trim)
        if x < sx1 - HANDLE_PX or x > sx2 + HANDLE_PX or y < sy1 - HANDLE_PX or y > sy2 + HANDLE_PX:
            continue

        mode = []
        if abs(x - sx1) <= HANDLE_PX:
            mode.append("LEFT")
        elif abs(x - sx2) <= HANDLE_PX:
            mode.append("RIGHT")
        if abs(y - sy1) <= HANDLE_PX:
            mode.append("BOTTOM")
        elif abs(y - sy2) <= HANDLE_PX:
            mode.append("TOP")

        if mode or (sx1 <= x <= sx2 and sy1 <= y <= sy2):
            return idx, "+".join(mode) if mode else "MOVE", uv

    return -1, None, uv


class BAKEADDON_OT_trim_toggle_edit(Operator):
    bl_idname = "uv.bakeaddon_trim_toggle_edit"
    bl_label = "Edit Trim Sheet"
    bl_description = "Enable interactive trim moving and resizing in the UV Editor"
    bl_options = {"REGISTER", "BLOCKING"}

    dragging = False
    drag_mode = ""
    drag_index = -1
    start_uv = None
    start_rect = None
    request_stop = False

    @classmethod
    def poll(cls, context):
        return bool(context.area and context.area.type == "IMAGE_EDITOR" and context.scene)

    def invoke(self, context, _event):
        global EDIT_OPERATOR
        material = trim_owner(context)
        if material is None:
            self.report({"WARNING"}, "Select a mesh material first.")
            return {"CANCELLED"}
        settings = trim_settings(context)
        if not settings:
            return {"CANCELLED"}
        if settings.edit_enabled:
            settings.edit_enabled = False
            if EDIT_OPERATOR is not None:
                EDIT_OPERATOR.request_stop = True
            tag_image_editors(context)
            return {"FINISHED"}

        settings.edit_enabled = True
        settings.draw_enabled = True
        self.request_stop = False
        ensure_trimsheet_draw_handler()
        EDIT_OPERATOR = self
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Edit Trim Sheet: Drag inside to move, edges/corners to resize | Esc or Stop Editing button")
        tag_image_editors(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        settings = trim_settings(context)
        material = trim_owner(context)
        trims = material.bakeaddon_trim_sections if material else ()
        if not settings or not settings.edit_enabled or not material or self.request_stop:
            return self.finish(context)

        if event_in_non_window_region(context.area, event):
            return {"PASS_THROUGH"}

        if event.type in {"MIDDLEMOUSE"} or event.type.startswith("NDOF"):
            return {"PASS_THROUGH"}

        if event.type == "ESC" or (event.type == "RIGHTMOUSE" and event.value == "PRESS"):
            settings.edit_enabled = False
            return self.finish(context)

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            idx, mode, uv = hit_test_trim(context.area, material, event)
            if idx != -1:
                material.bakeaddon_trim_index = idx
                self.dragging = True
                self.drag_index = idx
                self.drag_mode = mode
                self.start_uv = uv
                self.start_rect = normalized_rect(trims[idx])
                tag_image_editors(context)
                return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and self.dragging:
            uv = uv_from_event(context.area, event)
            if uv is not None and self.drag_index < len(trims):
                self.update_dragged_trim(trims[self.drag_index], uv)
                tag_image_editors(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self.dragging = False
            self.drag_index = -1
            self.drag_mode = ""
            return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    def update_dragged_trim(self, trim, uv):
        left, bottom, right, top = self.start_rect
        dx = uv.x - self.start_uv.x
        dy = uv.y - self.start_uv.y

        if self.drag_mode == "MOVE":
            width = right - left
            height = top - bottom
            left = max(0.0, min(1.0 - width, left + dx))
            bottom = max(0.0, min(1.0 - height, bottom + dy))
            set_trim_rect(trim, left, bottom, left + width, bottom + height)
            return

        if "LEFT" in self.drag_mode:
            left = left + dx
        if "RIGHT" in self.drag_mode:
            right = right + dx
        if "BOTTOM" in self.drag_mode:
            bottom = bottom + dy
        if "TOP" in self.drag_mode:
            top = top + dy

        set_trim_rect(trim, left, bottom, right, top)

    def finish(self, context):
        global EDIT_OPERATOR
        if EDIT_OPERATOR is self:
            EDIT_OPERATOR = None
        settings = trim_settings(context)
        if settings:
            settings.edit_enabled = False
        self.dragging = False
        if context.area:
            context.area.header_text_set(None)
            context.area.tag_redraw()
        return {"FINISHED"}


def selected_face_islands(faces):
    remaining = set(faces)
    islands = []
    while remaining:
        start = remaining.pop()
        island = {start}
        stack = [start]
        while stack:
            face = stack.pop()
            for edge in face.edges:
                for linked in edge.link_faces:
                    if linked in remaining:
                        remaining.remove(linked)
                        island.add(linked)
                        stack.append(linked)
        islands.append(island)
    return islands


def uv_coords_for_faces(faces, uv_layer):
    return [loop[uv_layer].uv for face in faces for loop in face.loops]


def uv_bbox_for_faces(faces, uv_layer):
    coords = uv_coords_for_faces(faces, uv_layer)
    if not coords:
        return None
    min_u = min(uv.x for uv in coords)
    max_u = max(uv.x for uv in coords)
    min_v = min(uv.y for uv in coords)
    max_v = max(uv.y for uv in coords)
    return min_u, min_v, max_u, max_v


def bbox_center(bbox):
    left, bottom, right, top = bbox
    return Vector(((left + right) * 0.5, (bottom + top) * 0.5))


def bbox_size(bbox):
    left, bottom, right, top = bbox
    return max(right - left, 1e-8), max(top - bottom, 1e-8)


def initialize_island_uvs_from_geometry(faces, uv_layer):
    verts = {loop.vert for face in faces for loop in face.loops}
    if not verts:
        return False

    normal = Vector()
    for face in faces:
        normal += face.normal
    if normal.length > 0.0:
        normal.normalize()
        drop_axis = max(range(3), key=lambda axis: abs(normal[axis]))
    else:
        drop_axis = 2
    axes = [axis for axis in range(3) if axis != drop_axis]

    values = {vert: Vector((vert.co[axes[0]], vert.co[axes[1]])) for vert in verts}
    min_x = min(value.x for value in values.values())
    max_x = max(value.x for value in values.values())
    min_y = min(value.y for value in values.values())
    max_y = max(value.y for value in values.values())
    width = max_x - min_x
    height = max_y - min_y
    if width <= 1e-8 or height <= 1e-8:
        return False

    for face in faces:
        for loop in face.loops:
            value = values[loop.vert]
            loop[uv_layer].uv = Vector(((value.x - min_x) / width, (value.y - min_y) / height))
    return True


def rotate_island_uvs(faces, uv_layer, angle, pivot):
    sin_a = math.sin(angle)
    cos_a = math.cos(angle)
    for face in faces:
        for loop in face.loops:
            uv = loop[uv_layer].uv
            offset = uv - pivot
            loop[uv_layer].uv = Vector((pivot.x + offset.x * cos_a - offset.y * sin_a, pivot.y + offset.x * sin_a + offset.y * cos_a))


def dominant_uv_angle_for_faces(faces, uv_layer):
    longest = 0.0
    angle = None
    for face in faces:
        loops = list(face.loops)
        for loop in loops:
            uv_a = loop[uv_layer].uv
            uv_b = loop.link_loop_next[uv_layer].uv
            delta = uv_b - uv_a
            length = delta.length
            if length > longest and length > 1e-8:
                longest = length
                angle = math.atan2(delta.y, delta.x)
    return angle


def half_turn_angle_delta(target_angle, current_angle):
    delta = target_angle - current_angle
    while delta <= -math.pi * 0.5:
        delta += math.pi
    while delta > math.pi * 0.5:
        delta -= math.pi
    return delta


def restore_uv_angle_for_faces(faces, uv_layer, target_angle):
    if target_angle is None:
        return False
    current_angle = dominant_uv_angle_for_faces(faces, uv_layer)
    bbox = uv_bbox_for_faces(faces, uv_layer)
    if current_angle is None or bbox is None:
        return False
    rotate_island_uvs(faces, uv_layer, half_turn_angle_delta(target_angle, current_angle), bbox_center(bbox))
    return True


def move_island_uvs(faces, uv_layer, delta):
    for face in faces:
        for loop in face.loops:
            loop[uv_layer].uv += delta


def scale_island_uvs(faces, uv_layer, scale, pivot):
    for face in faces:
        for loop in face.loops:
            uv = loop[uv_layer].uv
            offset = uv - pivot
            loop[uv_layer].uv = Vector((pivot.x + offset.x * scale.x, pivot.y + offset.y * scale.y))


def clamp_island_to_rect(faces, uv_layer, rect):
    bbox = uv_bbox_for_faces(faces, uv_layer)
    if not bbox:
        return
    left, bottom, right, top = bbox
    target_left, target_bottom, target_right, target_top = rect
    delta = Vector((0.0, 0.0))
    if left < target_left:
        delta.x = target_left - left
    elif right > target_right:
        delta.x = target_right - right
    if bottom < target_bottom:
        delta.y = target_bottom - bottom
    elif top > target_top:
        delta.y = target_top - top
    if delta.length_squared:
        move_island_uvs(faces, uv_layer, delta)


def fit_island_to_rect(faces, uv_layer, rect, keep_proportion=True, padding=0.0):
    bbox = uv_bbox_for_faces(faces, uv_layer)
    if bbox is None:
        return False
    src_width, src_height = bbox_size(bbox)
    left, bottom, right, top = rect
    padding = max(0.0, min(0.49, padding))
    target_left = min(right - MIN_TRIM_SIZE, left + padding)
    target_right = max(target_left + MIN_TRIM_SIZE, right - padding)
    target_bottom = min(top - MIN_TRIM_SIZE, bottom + padding)
    target_top = max(target_bottom + MIN_TRIM_SIZE, top - padding)
    target_width = target_right - target_left
    target_height = target_top - target_bottom
    target_center = Vector(((target_left + target_right) * 0.5, (target_bottom + target_top) * 0.5))
    src_center = bbox_center(bbox)

    if keep_proportion:
        uniform = min(target_width / src_width, target_height / src_height)
        scale = Vector((uniform, uniform))
    else:
        scale = Vector((target_width / src_width, target_height / src_height))

    for face in faces:
        for loop in face.loops:
            uv = loop[uv_layer].uv
            offset = uv - src_center
            loop[uv_layer].uv = Vector((target_center.x + offset.x * scale.x, target_center.y + offset.y * scale.y))
    return True


def uv_operator_context(context, preferred_area_types=("IMAGE_EDITOR", "VIEW_3D")):
    area = None
    region = None

    if context.area and context.area.type in preferred_area_types:
        area = context.area
        region = context.region if context.region and context.region.type == "WINDOW" else None

    if not area and context.window and context.window.screen:
        for area_type in preferred_area_types:
            for candidate in context.window.screen.areas:
                if candidate.type == area_type:
                    area = candidate
                    break
            if area:
                break

    if area and not region:
        region = next((candidate for candidate in area.regions if candidate.type == "WINDOW"), None)

    if area and region:
        return {"area": area, "region": region, "space_data": area.spaces.active}
    return {}


def select_face_indices_for_uv_op(context, face_indices):
    obj = context.active_object
    context.tool_settings.mesh_select_mode = (False, False, True)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    uv_layer = bm.loops.layers.uv.verify()
    indices = set(face_indices)
    for face in bm.faces:
        selected = face.index in indices
        face.select_set(selected)
        for loop in face.loops:
            uv_data = loop[uv_layer]
            try:
                uv_data.select = selected
            except Exception:
                pass
            try:
                uv_data.select_edge = selected
            except Exception:
                pass
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def run_uv_op_with_override(context, operator_callable, preferred_area_types=("IMAGE_EDITOR", "VIEW_3D"), **properties):
    override = uv_operator_context(context, preferred_area_types=preferred_area_types)
    if override:
        with context.temp_override(**override):
            return operator_callable(**properties)
    return operator_callable(**properties)


def native_unwrap_face_indices(context, face_indices):
    unwrap_kwargs = {
        "method": "CONFORMAL",
        "fill_holes": True,
        "correct_aspect": True,
        "use_subsurf_data": False,
        "margin": 0.001,
        "no_flip": False,
        "iterations": 10,
        "use_weights": False,
        "weight_group": "uv_importance",
        "weight_factor": 1,
    }
    attempts = (
        None,
        ("VIEW_3D",),
        ("IMAGE_EDITOR",),
    )
    errors = []

    for preferred_area_types in attempts:
        select_face_indices_for_uv_op(context, face_indices)
        try:
            if preferred_area_types is None:
                result = bpy.ops.uv.unwrap(**unwrap_kwargs)
            else:
                result = run_uv_op_with_override(
                    context,
                    bpy.ops.uv.unwrap,
                    preferred_area_types=preferred_area_types,
                    **unwrap_kwargs,
                )
            if result and "FINISHED" in result:
                return True
            errors.append(f"{preferred_area_types or 'current'} returned {sorted(result) if result else result}")
        except Exception as exc:
            errors.append(f"{preferred_area_types or 'current'}: {exc}")

    raise RuntimeError("Conformal unwrap failed: " + " | ".join(errors))


def orient_face_indices_to_world(context, face_indices):
    select_face_indices_for_uv_op(context, face_indices)
    try:
        result = run_uv_op_with_override(
            context,
            bpy.ops.uv.align_rotation,
            preferred_area_types=("IMAGE_EDITOR", "VIEW_3D"),
            method="GEOMETRY",
            axis="Z",
        )
        return bool(result and "FINISHED" in result)
    except Exception:
        return False


def selected_mesh_data(context):
    obj = context.active_object
    if not obj or obj.type != "MESH" or context.mode != "EDIT_MESH":
        raise RuntimeError("Select a mesh object in Edit Mode.")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    selected_faces = [face for face in bm.faces if face.select and face.is_valid]
    if not selected_faces:
        raise RuntimeError("Select one or more faces first.")
    uv_layer = bm.loops.layers.uv.verify()
    return obj, bm, uv_layer, selected_faces


def loops_for_face_edge(face, edge):
    return [loop for loop in face.loops if loop.edge == edge]


def faces_share_uv_edge(face_a, face_b, edge, uv_layer):
    loops_a = loops_for_face_edge(face_a, edge)
    loops_b = loops_for_face_edge(face_b, edge)
    if not loops_a or not loops_b:
        return False
    loop_a = loops_a[0]
    loop_b = loops_b[0]
    a1 = loop_a[uv_layer].uv
    a2 = loop_a.link_loop_next[uv_layer].uv
    b1 = loop_b[uv_layer].uv
    b2 = loop_b.link_loop_next[uv_layer].uv
    return (a1 - b2).length <= 1e-5 and (a2 - b1).length <= 1e-5


def selected_assignment_islands(bm, uv_layer, selected_faces, include_full_islands, faces_to_trim=False):
    if faces_to_trim:
        return [{face} for face in selected_faces]

    if not include_full_islands:
        return selected_face_islands(selected_faces)

    seed_faces = set(selected_faces)
    remaining = set(seed_faces)
    islands = []
    while remaining:
        start = remaining.pop()
        island = {start}
        stack = [start]
        while stack:
            face = stack.pop()
            for edge in face.edges:
                for linked in edge.link_faces:
                    if linked in island or linked is face:
                        continue
                    if faces_share_uv_edge(face, linked, edge, uv_layer):
                        island.add(linked)
                        stack.append(linked)
                        remaining.discard(linked)
        islands.append(island)
    return islands


def assignment_group_indices(bm, uv_layer, selected_faces, islands_to_trim=False, faces_to_trim=False):
    groups = selected_assignment_islands(bm, uv_layer, selected_faces, islands_to_trim, faces_to_trim=faces_to_trim)
    return [[face.index for face in group if face.is_valid] for group in groups]


def restore_assignment_groups(bm, group_indices):
    bm.faces.ensure_lookup_table()
    groups = []
    for indices in group_indices:
        group = {bm.faces[index] for index in indices if 0 <= index < len(bm.faces) and bm.faces[index].is_valid}
        if group:
            groups.append(group)
    return groups


def assign_selected_faces_to_trim(
    context,
    trim,
    uv_unwrap=False,
    world_orient=False,
    stretch_fill=False,
    padding=0.0,
    islands_to_trim=False,
    faces_to_trim=False,
):
    obj = context.active_object
    if not obj or obj.type != "MESH" or context.mode != "EDIT_MESH":
        raise RuntimeError("Select a mesh object in Edit Mode.")

    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.normal_update()
    uv_layer = bm.loops.layers.uv.verify()
    selected_faces = [face for face in bm.faces if face.select and face.is_valid]
    if not selected_faces:
        raise RuntimeError("Select one or more faces first.")

    group_indices = assignment_group_indices(
        bm,
        uv_layer,
        selected_faces,
        islands_to_trim=islands_to_trim,
        faces_to_trim=faces_to_trim,
    )
    original_selected_indices = [face.index for face in selected_faces]
    original_group_angles = {}
    if uv_unwrap and not world_orient:
        for group_index_list in group_indices:
            restored_groups = restore_assignment_groups(bm, [group_index_list]) if group_index_list else []
            island = restored_groups[0] if restored_groups else set()
            original_group_angles[tuple(group_index_list)] = dominant_uv_angle_for_faces(island, uv_layer) if island else None

    if uv_unwrap:
        if not native_unwrap_face_indices(context, original_selected_indices):
            raise RuntimeError("Conformal unwrap was cancelled for selected faces.")
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        uv_layer = bm.loops.layers.uv.verify()

    rect = normalized_rect(trim)
    for group_index_list in group_indices:
        restored_groups = restore_assignment_groups(bm, [group_index_list]) if group_index_list else []
        island = restored_groups[0] if restored_groups else set()
        if not island:
            continue
        bbox = uv_bbox_for_faces(island, uv_layer)
        needs_projection = bbox is None
        if bbox:
            src_width, src_height = bbox_size(bbox)
            needs_projection = src_width <= 1e-8 or src_height <= 1e-8
        if needs_projection and not initialize_island_uvs_from_geometry(island, uv_layer):
            raise RuntimeError("Selected faces have degenerate geometry and cannot be assigned.")
        if uv_unwrap and not world_orient:
            restore_uv_angle_for_faces(island, uv_layer, original_group_angles.get(tuple(group_index_list)))
        if world_orient:
            if not orient_face_indices_to_world(context, group_index_list):
                raise RuntimeError("World Orient failed for selected faces.")
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            bm.normal_update()
            uv_layer = bm.loops.layers.uv.verify()
            restored_groups = restore_assignment_groups(bm, [group_index_list])
            island = restored_groups[0] if restored_groups else set()
            if not island:
                continue
        fit_island_to_rect(island, uv_layer, rect, keep_proportion=not stretch_fill, padding=padding)

    select_face_indices_for_uv_op(context, original_selected_indices)
    bmesh.update_edit_mesh(obj.data)


def trim_index_at_uv(context, uv):
    trims = trim_sections(context)
    if not trims:
        return -1
    for idx in reversed(range(len(trims))):
        if point_in_trim(trims[idx], uv):
            return idx
    return -1


def find_trim_under_mouse(context, event):
    area, region = image_area_under_mouse(context, event)
    if not area or not region:
        return -1
    local_x = event.mouse_x - region.x
    local_y = event.mouse_y - region.y
    uv = Vector(region.view2d.region_to_view(local_x, local_y))
    return trim_index_at_uv(context, uv)


class BAKEADDON_OT_assign_to_trim(Operator):
    bl_idname = "uv.bakeaddon_assign_to_trim"
    bl_label = "Assign to Trim"
    bl_description = "Toggle persistent mode: click trims in the UV Editor to assign selected faces"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    hover_index = -1
    request_stop = False

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def invoke(self, context, _event):
        global ASSIGN_OPERATOR
        settings = trim_settings(context)
        if not settings:
            return {"CANCELLED"}

        if settings.assign_enabled:
            settings.assign_enabled = False
            if ASSIGN_OPERATOR is not None:
                ASSIGN_OPERATOR.request_stop = True
            tag_image_editors(context)
            return {"FINISHED"}

        material = trim_owner(context)
        if material is None or len(material.bakeaddon_trim_sections) == 0:
            self.report({"WARNING"}, "Create at least one trim section first.")
            return {"CANCELLED"}

        settings.assign_enabled = True
        settings.draw_enabled = True
        self.hover_index = -1
        self.request_stop = False
        ASSIGN_OPERATOR = self
        ensure_trimsheet_draw_handler()
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set("Assign to Trim: select faces in View3D, click a trim in UV Editor | Toggle button to stop")
        tag_image_editors(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        settings = trim_settings(context)
        if not settings or not settings.assign_enabled or self.request_stop:
            return self.finish(context)

        if event_in_non_window_region(context.area, event):
            return {"PASS_THROUGH"}

        if event.type in {"MIDDLEMOUSE"} or event.type.startswith("NDOF"):
            return {"PASS_THROUGH"}

        if event.type == "ESC" and event.value == "PRESS":
            settings.assign_enabled = False
            self.request_stop = True
            return self.finish(context)

        if event.type == "RIGHTMOUSE":
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            self.hover_index = find_trim_under_mouse(context, event)
            tag_image_editors(context)
            return {"PASS_THROUGH"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            index = find_trim_under_mouse(context, event)
            if index == -1:
                return {"PASS_THROUGH"}

            material = trim_owner(context)
            if material is None or index >= len(material.bakeaddon_trim_sections):
                return {"PASS_THROUGH"}
            material.bakeaddon_trim_index = index
            trim = material.bakeaddon_trim_sections[index]
            try:
                assign_selected_faces_to_trim(
                    context,
                    trim,
                    uv_unwrap=settings.assign_uv_unwrap,
                    world_orient=settings.assign_world_orient,
                    stretch_fill=settings.assign_stretch_fill,
                    padding=settings.padding,
                    islands_to_trim=settings.assign_islands_to_trim,
                    faces_to_trim=settings.assign_faces_to_trim,
                )
            except Exception as exc:
                self.report({"WARNING"}, f"Assign to Trim failed: {exc}")
                return {"RUNNING_MODAL"}
            self.report({"INFO"}, f"Assigned selected faces to {trim.name}.")
            tag_image_editors(context)
            return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

    def finish(self, context):
        global ASSIGN_OPERATOR
        if ASSIGN_OPERATOR is self:
            ASSIGN_OPERATOR = None
        settings = trim_settings(context)
        if settings and self.request_stop:
            settings.assign_enabled = False
        self.hover_index = -1
        context.workspace.status_text_set(None)
        tag_image_editors(context)
        return {"FINISHED"}


def padded_trim_rect(rect, padding):
    left, bottom, right, top = rect
    padding = max(0.0, min(0.49, padding))
    target_left = min(right - MIN_TRIM_SIZE, left + padding)
    target_right = max(target_left + MIN_TRIM_SIZE, right - padding)
    target_bottom = min(top - MIN_TRIM_SIZE, bottom + padding)
    target_top = max(target_bottom + MIN_TRIM_SIZE, top - padding)
    return target_left, target_bottom, target_right, target_top


def trim_index_for_face_group(context, faces, uv_layer):
    bbox = uv_bbox_for_faces(faces, uv_layer)
    if bbox:
        index = trim_index_at_uv(context, bbox_center(bbox))
        if index != -1:
            return index
    return ensure_trim_index(trim_owner(context))


def trim_for_stash(context, stash):
    trim_uuid = stash.get("trim_uuid", "")
    trim_name = stash.get("trim_name", "")
    trims = trim_sections(context) or ()
    for trim in trims:
        if trim_uuid and trim.uuid == trim_uuid:
            return trim
    for trim in trims:
        if trim_name and trim.name == trim_name:
            return trim
    return None


def collect_trim_scale_groups(context, bm, uv_layer, face_patch_ids=None, stashes=None):
    trims = trim_sections(context)
    if not trims or len(trims) == 0:
        raise RuntimeError("Create at least one trim section first.")

    selected_faces = [face for face in bm.faces if face.select and face.is_valid]
    if not selected_faces:
        raise RuntimeError("Select one or more faces first.")

    groups = []
    face_patch_ids = face_patch_ids or {}
    stashes = stashes or {}

    if face_patch_ids:
        faces_by_patch = {}
        for face in selected_faces:
            patch_id = face_patch_ids.get(face, 0)
            faces_by_patch.setdefault(patch_id, []).append(face)
        grouped_sources = []
        for patch_id, faces in faces_by_patch.items():
            for island in selected_face_islands(faces):
                grouped_sources.append((island, patch_id))
    else:
        grouped_sources = [(island, 0) for island in selected_face_islands(selected_faces)]

    for face_group, patch_id in grouped_sources:
        trim = None
        stash = stashes.get(str(patch_id)) if patch_id else None
        if stash:
            trim = trim_for_stash(context, stash)
        if trim is None:
            trim_index = trim_index_for_face_group(context, face_group, uv_layer)
            if trim_index == -1:
                raise RuntimeError("No active trim section could be resolved.")
            trim = trims[trim_index]
        groups.append((face_group, trim, patch_id))
    return groups


def trim_local_uv(uv, rect):
    left, bottom, right, top = rect
    width = max(right - left, MIN_TRIM_SIZE)
    height = max(top - bottom, MIN_TRIM_SIZE)
    return Vector(((uv.x - left) / width, (uv.y - bottom) / height))


def trim_uv_from_local(local_uv, rect):
    left, bottom, right, top = rect
    width = max(right - left, MIN_TRIM_SIZE)
    height = max(top - bottom, MIN_TRIM_SIZE)
    return Vector((left + local_uv.x * width, bottom + local_uv.y * height))


def trim_scale_vector(scale, scale_axis="UNIFORM"):
    value = max(float(scale), 0.001)
    if scale_axis == "X":
        return Vector((value, 1.0))
    if scale_axis == "Y":
        return Vector((1.0, value))
    return Vector((value, value))


def closest_point_on_triangle(point, a, b, c):
    ab = b - a
    ac = c - a
    ap = point - a
    d1 = ab.dot(ap)
    d2 = ac.dot(ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return a.copy()

    bp = point - b
    d3 = ab.dot(bp)
    d4 = ac.dot(bp)
    if d3 >= 0.0 and d4 <= d3:
        return b.copy()

    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        return a + ab * (d1 / max(d1 - d3, 1e-12))

    cp = point - c
    d5 = ab.dot(cp)
    d6 = ac.dot(cp)
    if d6 >= 0.0 and d5 <= d6:
        return c.copy()

    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        return a + ac * (d2 / max(d2 - d6, 1e-12))

    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        return b + (c - b) * ((d4 - d3) / max((d4 - d3) + (d5 - d6), 1e-12))

    denominator = max(va + vb + vc, 1e-12)
    v = vb / denominator
    w = vc / denominator
    return a + ab * v + ac * w


def barycentric_weights(point, a, b, c):
    v0 = b - a
    v1 = c - a
    v2 = point - a
    d00 = v0.dot(v0)
    d01 = v0.dot(v1)
    d11 = v1.dot(v1)
    d20 = v2.dot(v0)
    d21 = v2.dot(v1)
    denominator = d00 * d11 - d01 * d01
    if abs(denominator) <= 1e-12:
        distances = [(point - a).length_squared, (point - b).length_squared, (point - c).length_squared]
        index = distances.index(min(distances))
        return (1.0, 0.0, 0.0) if index == 0 else ((0.0, 1.0, 0.0) if index == 1 else (0.0, 0.0, 1.0))
    v = (d11 * d20 - d01 * d21) / denominator
    w = (d00 * d21 - d01 * d20) / denominator
    u = 1.0 - v - w
    return u, v, w


def origin_center_trim_scale_pivot(faces, uv_layer, rect):
    origin = Vector((0.0, 0.0, 0.0))
    best_uv = None
    best_distance = None

    for face in faces:
        if not face.is_valid or len(face.loops) < 3:
            continue
        loops = list(face.loops)
        coords = [[loop.vert.co.copy() for loop in loops]]
        try:
            triangles = tessellate_polygon(coords)
        except Exception:
            triangles = []
        if not triangles and len(loops) == 3:
            triangles = [(0, 1, 2)]

        for triangle in triangles:
            tri_loops = [loops[index] for index in triangle]
            tri_coords = [loop.vert.co for loop in tri_loops]
            closest = closest_point_on_triangle(origin, tri_coords[0], tri_coords[1], tri_coords[2])
            distance = (closest - origin).length_squared
            if best_distance is not None and distance >= best_distance:
                continue
            weights = barycentric_weights(closest, tri_coords[0], tri_coords[1], tri_coords[2])
            tri_uvs = [loop[uv_layer].uv for loop in tri_loops]
            best_uv = tri_uvs[0] * weights[0] + tri_uvs[1] * weights[1] + tri_uvs[2] * weights[2]
            best_distance = distance

    return trim_local_uv(best_uv, rect) if best_uv is not None else None


def virtual_face_polygon(face, uv_layer, rect, pivot, scale):
    scale_vector = scale if isinstance(scale, Vector) else trim_scale_vector(scale)
    polygon = []
    for loop in face.loops:
        local_uv = trim_local_uv(loop[uv_layer].uv, rect)
        offset = local_uv - pivot
        virtual_uv = Vector((pivot.x + offset.x * scale_vector.x, pivot.y + offset.y * scale_vector.y))
        source_edges = {}
        for edge in (loop.link_loop_prev.edge, loop.edge):
            if edge and edge.is_valid:
                source_edges[edge] = 0.0 if edge.verts[0] == loop.vert else 1.0
        polygon.append({
            "co": loop.vert.co.copy(),
            "vuv": virtual_uv,
            "vert": loop.vert,
            "source_edges": source_edges,
        })
    return polygon


def trim_scale_factor_key(factor):
    return round(max(0.0, min(1.0, factor)), 8)


def interpolate_clip_point(start, end, axis, boundary):
    start_value = start["vuv"][axis]
    end_value = end["vuv"][axis]
    denominator = end_value - start_value
    factor = 0.0 if abs(denominator) <= 1e-10 else (boundary - start_value) / denominator
    factor = max(0.0, min(1.0, factor))
    source_edges = {}
    start_sources = start.get("source_edges", {})
    end_sources = end.get("source_edges", {})
    for edge in set(start_sources) & set(end_sources):
        start_factor = start_sources[edge]
        end_factor = end_sources[edge]
        if abs(start_factor - end_factor) > 1e-10:
            source_edges[edge] = start_factor + (end_factor - start_factor) * factor
    return {
        "co": start["co"].lerp(end["co"], factor),
        "vuv": start["vuv"].lerp(end["vuv"], factor),
        "vert": None,
        "source_edges": source_edges,
    }


def clip_polygon_axis(polygon, axis, boundary, keep_greater):
    if not polygon:
        return []

    result = []
    previous = polygon[-1]
    previous_inside = previous["vuv"][axis] >= boundary - 1e-8 if keep_greater else previous["vuv"][axis] <= boundary + 1e-8

    for current in polygon:
        current_inside = current["vuv"][axis] >= boundary - 1e-8 if keep_greater else current["vuv"][axis] <= boundary + 1e-8
        if current_inside != previous_inside:
            result.append(interpolate_clip_point(previous, current, axis, boundary))
        if current_inside:
            result.append(current)
        previous = current
        previous_inside = current_inside

    return result


def clip_polygon_to_cell(polygon, cell_x, cell_y):
    clipped = clip_polygon_axis(polygon, 0, cell_x, True)
    clipped = clip_polygon_axis(clipped, 0, cell_x + 1.0, False)
    clipped = clip_polygon_axis(clipped, 1, cell_y, True)
    clipped = clip_polygon_axis(clipped, 1, cell_y + 1.0, False)
    return dedupe_clip_polygon(clipped)


def dedupe_clip_polygon(polygon):
    result = []
    for point in polygon:
        if not result or (point["vuv"] - result[-1]["vuv"]).length > 1e-7 or (point["co"] - result[-1]["co"]).length > 1e-7:
            result.append(point)
    if len(result) > 1 and (result[0]["vuv"] - result[-1]["vuv"]).length <= 1e-7 and (result[0]["co"] - result[-1]["co"]).length <= 1e-7:
        result.pop()
    return result


def clip_polygon_area(polygon):
    if len(polygon) < 3:
        return 0.0
    area = 0.0
    for index, point in enumerate(polygon):
        next_point = polygon[(index + 1) % len(polygon)]
        area += point["vuv"].x * next_point["vuv"].y - next_point["vuv"].x * point["vuv"].y
    return abs(area) * 0.5


def cell_range_for_polygon(polygon, axis):
    values = [point["vuv"][axis] for point in polygon]
    min_cell = math.floor(min(values) + 1e-8)
    max_cell = math.floor(max(values) - 1e-8)
    return range(min_cell, max_cell + 1)


def trim_scale_polygon_signed_area(polygon):
    area = 0.0
    for index, point in enumerate(polygon):
        next_point = polygon[(index + 1) % len(polygon)]
        area += point["vuv"].x * next_point["vuv"].y - next_point["vuv"].x * point["vuv"].y
    return area * 0.5


def trim_scale_polygon_is_convex(polygon):
    if len(polygon) <= 3:
        return True

    sign = 0
    count = len(polygon)
    for index in range(count):
        prev_uv = polygon[index - 1]["vuv"]
        curr_uv = polygon[index]["vuv"]
        next_uv = polygon[(index + 1) % count]["vuv"]
        cross = (curr_uv.x - prev_uv.x) * (next_uv.y - curr_uv.y) - (curr_uv.y - prev_uv.y) * (next_uv.x - curr_uv.x)
        if abs(cross) <= 1e-9:
            continue
        current_sign = 1 if cross > 0.0 else -1
        if sign and current_sign != sign:
            return False
        sign = current_sign
    return True


def trim_scale_source_polygons(polygon):
    if len(polygon) < 3:
        return []
    if len(polygon) <= 4 and trim_scale_polygon_is_convex(polygon):
        return [polygon]

    coords = [[Vector((point["vuv"].x, point["vuv"].y, 0.0)) for point in polygon]]
    try:
        triangles = tessellate_polygon(coords)
    except Exception:
        triangles = []

    if not triangles:
        return [polygon] if trim_scale_polygon_is_convex(polygon) else []

    result = []
    source_area_sign = 1.0 if trim_scale_polygon_signed_area(polygon) >= 0.0 else -1.0
    for triangle in triangles:
        tri_polygon = [polygon[index] for index in triangle]
        if trim_scale_polygon_signed_area(tri_polygon) * source_area_sign < 0.0:
            tri_polygon.reverse()
        if clip_polygon_area(tri_polygon) > 1e-9:
            result.append(tri_polygon)
    return result


def collect_trim_scale_edge_factors(piece, edge_factors):
    for point in piece:
        if point.get("vert") is not None:
            continue
        for edge, factor in point.get("source_edges", {}).items():
            if edge.is_valid and 1e-7 < factor < 1.0 - 1e-7:
                edge_factors.setdefault(edge, set()).add(trim_scale_factor_key(factor))


def split_trim_scale_boundary_edges(edge_factors, skip_edges=None):
    split_verts = {}
    original_edge_verts = {}
    skip_edges = {edge for edge in skip_edges or set() if edge.is_valid}

    for edge, factors in edge_factors.items():
        if not edge.is_valid:
            continue

        start_vert = edge.verts[0]
        end_vert = edge.verts[1]
        original_edge_verts[edge] = (start_vert, end_vert)
        if edge in skip_edges:
            continue

        split_verts[(edge, trim_scale_factor_key(0.0))] = start_vert
        split_verts[(edge, trim_scale_factor_key(1.0))] = end_vert

        current_start = start_vert
        previous_factor = 0.0
        for factor in sorted(f for f in factors if 1e-7 < f < 1.0 - 1e-7):
            if not edge.is_valid or not current_start.is_valid:
                break
            local_factor = (factor - previous_factor) / max(1.0 - previous_factor, 1e-8)
            local_factor = max(1e-6, min(1.0 - 1e-6, local_factor))
            _new_edge, new_vert = bmesh.utils.edge_split(edge, current_start, local_factor)
            split_verts[(edge, trim_scale_factor_key(factor))] = new_vert
            current_start = new_vert
            previous_factor = factor

    return split_verts


def clone_trim_scale_source_verts(bm, faces):
    source_vert_map = {}
    for face in faces:
        if not face.is_valid:
            continue
        for vert in face.verts:
            if not vert.is_valid or vert in source_vert_map:
                continue
            source_vert_map[vert] = bm.verts.new(vert.co.copy())
    return source_vert_map


def vertex_for_trim_scale_point(bm, point, split_verts, created_verts, source_vert_map=None, coord_verts=None):
    source_vert_map = source_vert_map or {}
    coord_verts = coord_verts if coord_verts is not None else {}
    source_vert = point.get("vert")
    if source_vert is not None and source_vert.is_valid:
        return source_vert_map.get(source_vert, source_vert)

    for edge, factor in point.get("source_edges", {}).items():
        key = (edge, trim_scale_factor_key(factor))
        split_vert = split_verts.get(key)
        if split_vert is not None and split_vert.is_valid:
            return split_vert

    coord_key = trim_scale_coord_key(point["co"])
    coord_vert = coord_verts.get(coord_key)
    if coord_vert is not None and coord_vert.is_valid:
        return coord_vert

    new_vert = bm.verts.new(point["co"])
    created_verts.append(new_vert)
    coord_verts[coord_key] = new_vert
    return new_vert


def create_trim_scale_piece(bm, uv_layer, patch_layer, source_props, piece, cell_x, cell_y, rect, split_verts, source_vert_map=None, coord_verts=None):
    if len(piece) < 3 or clip_polygon_area(piece) <= 1e-9:
        return None, []

    new_verts = []
    face_points = []
    for point in piece:
        vert = vertex_for_trim_scale_point(bm, point, split_verts, new_verts, source_vert_map=source_vert_map, coord_verts=coord_verts)
        if not face_points or face_points[-1][0] != vert:
            face_points.append((vert, point))

    if len(face_points) > 1 and face_points[0][0] == face_points[-1][0]:
        face_points.pop()

    face_verts = [vert for vert, _point in face_points]
    if len(set(face_verts)) < 3:
        return None, new_verts

    try:
        new_face = bm.faces.new(face_verts)
    except ValueError:
        return None, new_verts

    new_face.material_index = source_props["material_index"]
    new_face.smooth = source_props["smooth"]
    if patch_layer is not None:
        new_face[patch_layer] = int(source_props.get("patch_id", 0))
    new_face.select_set(True)

    point_by_vert = {}
    for vert, point in face_points:
        point_by_vert[vert] = point

    for loop in new_face.loops:
        point = point_by_vert.get(loop.vert)
        if point is None:
            continue
        local_uv = Vector((
            max(0.0, min(1.0, point["vuv"].x - cell_x)),
            max(0.0, min(1.0, point["vuv"].y - cell_y)),
        ))
        loop[uv_layer].uv = trim_uv_from_local(local_uv, rect)

    return new_face, new_verts


def build_trim_scale_face_plan(uv_layer, face, rect, pivot, scale, patch_id=0):
    polygon = virtual_face_polygon(face, uv_layer, rect, pivot, scale)
    if len(polygon) < 3:
        return []

    pieces = []
    for source_polygon in trim_scale_source_polygons(polygon):
        for cell_x in cell_range_for_polygon(source_polygon, 0):
            for cell_y in cell_range_for_polygon(source_polygon, 1):
                piece = clip_polygon_to_cell(source_polygon, cell_x, cell_y)
                if len(piece) >= 3 and clip_polygon_area(piece) > 1e-9:
                    pieces.append({
                        "piece": piece,
                        "cell_x": cell_x,
                        "cell_y": cell_y,
                        "rect": rect,
                        "source_props": {
                            "material_index": face.material_index,
                            "smooth": face.smooth,
                            "patch_id": patch_id,
                        },
                    })
    return pieces


def trim_scale_boundary_verts_for_faces(faces):
    face_set = {face for face in faces if face.is_valid}
    boundary_verts = set()
    if not face_set:
        return boundary_verts

    for face in face_set:
        for edge in face.edges:
            if not edge.is_valid:
                continue
            linked_count = sum(1 for linked in edge.link_faces if linked in face_set and linked.is_valid)
            if linked_count <= 1:
                boundary_verts.update(vert for vert in edge.verts if vert.is_valid)
    return boundary_verts


def cleanup_trim_scale_rebuild(bm, old_edges, old_verts, created_faces, created_verts, split_verts, boundary_verts=None, weld_old_verts=True):
    bad_faces = [
        face for face in created_faces
        if face.is_valid and (len(set(face.verts)) < 3 or face.calc_area() <= 1e-10)
    ]
    if bad_faces:
        bmesh.ops.delete(bm, geom=bad_faces, context="FACES")

    boundary_vert_set = {vert for vert in boundary_verts or set() if vert.is_valid}
    internal_old_verts = {vert for vert in old_verts if weld_old_verts and vert.is_valid and vert not in boundary_vert_set}
    internal_weld_verts = [
        vert
        for vert in set(created_verts) | set(split_verts.values()) | internal_old_verts
        if vert.is_valid
    ]
    if internal_weld_verts:
        bmesh.ops.remove_doubles(bm, verts=internal_weld_verts, dist=TRIM_SCALE_INTERNAL_MERGE_DIST)

    if boundary_vert_set:
        patch_boundary_verts = trim_scale_boundary_verts_for_faces(created_faces)
        boundary_weld_verts = [
            vert
            for vert in boundary_vert_set | patch_boundary_verts
            if vert.is_valid
        ]
        if boundary_weld_verts:
            bmesh.ops.remove_doubles(bm, verts=boundary_weld_verts, dist=TRIM_SCALE_BOUNDARY_MERGE_DIST)

    wire_edges = [edge for edge in old_edges if edge.is_valid and len(edge.link_faces) == 0]
    if wire_edges:
        bmesh.ops.delete(bm, geom=wire_edges, context="EDGES")

    loose_created_verts = [vert for vert in set(created_verts) | old_verts if vert.is_valid and len(vert.link_edges) == 0]
    if loose_created_verts:
        bmesh.ops.delete(bm, geom=loose_created_verts, context="VERTS")


def trim_scale_coord_key(co):
    return tuple(round(float(value), 6) for value in co)


def load_trim_scale_stashes(obj):
    raw = getattr(obj, "bakeaddon_trim_scale_stashes", "") or ""
    if not raw:
        return {"version": 1, "patches": {}}
    try:
        data = json.loads(raw)
    except Exception:
        return {"version": 1, "patches": {}}
    if not isinstance(data, dict):
        return {"version": 1, "patches": {}}
    data.setdefault("version", 1)
    data.setdefault("patches", {})
    if not isinstance(data["patches"], dict):
        data["patches"] = {}
    return data


def save_trim_scale_stashes(obj, data):
    obj.bakeaddon_trim_scale_stashes = json.dumps(data, separators=(",", ":"), sort_keys=True)


def next_trim_scale_patch_id(obj):
    patch_id = max(1, int(getattr(obj, "bakeaddon_trim_scale_next_patch_id", 1)))
    obj.bakeaddon_trim_scale_next_patch_id = patch_id + 1
    return patch_id


def next_trim_scale_edge_id(obj):
    edge_id = max(1, int(getattr(obj, "bakeaddon_trim_scale_next_edge_id", 1)))
    obj.bakeaddon_trim_scale_next_edge_id = edge_id + 1
    return edge_id


def trim_scale_face_layer(bm, create=True):
    layer = bm.faces.layers.int.get(TRIM_SCALE_FACE_LAYER)
    if layer is None and create:
        layer = bm.faces.layers.int.new(TRIM_SCALE_FACE_LAYER)
    return layer


def trim_scale_edge_layers(bm, create=True):
    patch_layer = bm.edges.layers.int.get(TRIM_SCALE_EDGE_PATCH_LAYER)
    role_layer = bm.edges.layers.int.get(TRIM_SCALE_EDGE_ROLE_LAYER)
    id_layer = bm.edges.layers.int.get(TRIM_SCALE_EDGE_ID_LAYER)
    if create:
        if patch_layer is None:
            patch_layer = bm.edges.layers.int.new(TRIM_SCALE_EDGE_PATCH_LAYER)
        if role_layer is None:
            role_layer = bm.edges.layers.int.new(TRIM_SCALE_EDGE_ROLE_LAYER)
        if id_layer is None:
            id_layer = bm.edges.layers.int.new(TRIM_SCALE_EDGE_ID_LAYER)
    return patch_layer, role_layer, id_layer


def selected_trim_scale_patch_ids(bm, layer):
    if layer is None:
        return set()
    return {
        int(face[layer])
        for face in bm.faces
        if face.is_valid and face.select and int(face[layer]) > 0
    }


def trim_scale_patch_faces(bm, patch_layer, patch_id):
    if patch_layer is None or patch_id <= 0:
        return []
    return [
        face
        for face in bm.faces
        if face.is_valid and int(face[patch_layer]) == patch_id
    ]


def vector_to_list(vector):
    return [float(vector.x), float(vector.y), float(vector.z)]


def uv_to_list(uv):
    return [float(uv.x), float(uv.y)]


def trim_scale_uv_key(uv):
    return tuple(round(float(value), 6) for value in uv)


def trim_scale_edge_key(edge):
    return tuple(sorted(trim_scale_coord_key(vert.co) for vert in edge.verts))


def trim_scale_source_edge_refs(faces):
    refs = []
    seen = set()
    for face in faces:
        if not face.is_valid:
            continue
        for edge in face.edges:
            if not edge.is_valid:
                continue
            key = trim_scale_edge_key(edge)
            if key in seen:
                continue
            seen.add(key)
            refs.append({
                "key": key,
                "start": trim_scale_coord_key(edge.verts[0].co),
                "end": trim_scale_coord_key(edge.verts[1].co),
            })
    return refs


def trim_scale_point_on_segment_inclusive(point, start, end, eps=TRIM_SCALE_STASH_EPS):
    segment = end - start
    length_squared = segment.length_squared
    if length_squared <= eps * eps:
        return (point - start).length <= eps
    factor = (point - start).dot(segment) / length_squared
    if factor < -eps or factor > 1.0 + eps:
        return False
    projected = start + segment * max(0.0, min(1.0, factor))
    return (point - projected).length <= eps


def trim_scale_edge_matches_source(edge, source_refs):
    edge_key = trim_scale_edge_key(edge)
    if edge_key in source_refs:
        return True
    start = edge.verts[0].co
    end = edge.verts[1].co
    for ref in source_refs:
        if not isinstance(ref, dict):
            continue
        if edge_key == ref.get("key"):
            return True
        ref_start = Vector(ref.get("start", (0.0, 0.0, 0.0)))
        ref_end = Vector(ref.get("end", (0.0, 0.0, 0.0)))
        if (
            trim_scale_point_on_segment_inclusive(start, ref_start, ref_end)
            and trim_scale_point_on_segment_inclusive(end, ref_start, ref_end)
        ):
            return True
    return False


def trim_scale_edge_uv_signature(edge, uv_layer):
    loops = []
    for face in edge.link_faces:
        if not face.is_valid:
            continue
        for loop in face.loops:
            if loop.edge == edge:
                loops.append((
                    list(trim_scale_uv_key(loop[uv_layer].uv)),
                    list(trim_scale_uv_key(loop.link_loop_next[uv_layer].uv)),
                ))
                break
    loops.sort()
    return loops


def trim_scale_edge_signature(edge, uv_layer, edge_layers=None):
    edge_patch_layer, edge_role_layer, edge_id_layer = edge_layers or (None, None, None)
    return {
        "edge_id": int(edge[edge_id_layer]) if edge_id_layer is not None else 0,
        "patch_id": int(edge[edge_patch_layer]) if edge_patch_layer is not None else 0,
        "role": int(edge[edge_role_layer]) if edge_role_layer is not None else 0,
        "verts": [list(coord) for coord in trim_scale_edge_key(edge)],
        "uvs": trim_scale_edge_uv_signature(edge, uv_layer),
        "linked_faces": sum(1 for face in edge.link_faces if face.is_valid),
        "seam": bool(getattr(edge, "seam", False)),
        "smooth": bool(getattr(edge, "smooth", True)),
    }


def generated_trim_scale_edge_signatures(bm, uv_layer, edge_layers, patch_ids=None):
    edge_patch_layer, edge_role_layer, edge_id_layer = edge_layers
    if edge_patch_layer is None or edge_role_layer is None or edge_id_layer is None:
        return {}
    patch_ids = {int(patch_id) for patch_id in patch_ids} if patch_ids is not None else None
    signatures = {}
    for edge in bm.edges:
        if not edge.is_valid:
            continue
        patch_id = int(edge[edge_patch_layer])
        role = int(edge[edge_role_layer])
        edge_id = int(edge[edge_id_layer])
        if patch_id <= 0 or edge_id <= 0 or not (role & TRIM_SCALE_EDGE_GENERATED):
            continue
        if patch_ids is not None and patch_id not in patch_ids:
            continue
        signatures[str(edge_id)] = trim_scale_edge_signature(edge, uv_layer, edge_layers)
    return signatures


def trim_scale_generated_edges_match(bm, uv_layer, edge_layers, patch_id, stash):
    stored = stash.get("generated_edge_signatures")
    if not isinstance(stored, dict):
        return stash.get("version", 1) < 2
    current = generated_trim_scale_edge_signatures(bm, uv_layer, edge_layers, {patch_id})
    return stored == current


def trim_scale_canonical_loop_signature(loop_items):
    if not loop_items:
        return []
    candidates = []
    item_count = len(loop_items)
    for items in (loop_items, list(reversed(loop_items))):
        for offset in range(item_count):
            candidates.append(tuple(items[offset:] + items[:offset]))
    return [list(item) for item in min(candidates)]


def trim_scale_face_signature(face, uv_layer):
    loop_items = [
        (
            list(trim_scale_coord_key(loop.vert.co)),
            list(trim_scale_uv_key(loop[uv_layer].uv)),
        )
        for loop in face.loops
    ]
    return {
        "loops": trim_scale_canonical_loop_signature(loop_items),
        "material_index": int(face.material_index),
        "smooth": bool(face.smooth),
    }


def trim_scale_patch_signature(faces, uv_layer):
    face_signatures = [
        trim_scale_face_signature(face, uv_layer)
        for face in faces
        if face.is_valid
    ]
    face_signatures.sort(key=lambda item: json.dumps(item, separators=(",", ":"), sort_keys=True))
    return {
        "version": 1,
        "faces": face_signatures,
    }


def trim_scale_signature_key(signature):
    return json.dumps(signature, separators=(",", ":"), sort_keys=True)


def trim_scale_stored_generated_face_keys(stash):
    generated_signature = stash.get("generated_signature")
    if not isinstance(generated_signature, dict):
        return set()
    faces = generated_signature.get("faces")
    if not isinstance(faces, list):
        return set()
    return {trim_scale_signature_key(face_signature) for face_signature in faces}


def make_trim_scale_stash(patch_id, obj, face_group, uv_layer, trim, padding, scale, scale_axis="UNIFORM", cut_method="STANDARD"):
    source_faces = []
    boundary_edges = {
        edge
        for face in face_group
        for edge in face.edges
        if any(linked not in face_group for linked in edge.link_faces) or len(edge.link_faces) <= 1
    }
    boundary_coords = {
        trim_scale_coord_key(vert.co): vector_to_list(vert.co)
        for edge in boundary_edges
        for vert in edge.verts
    }

    for face in face_group:
        source_faces.append({
            "verts": [vector_to_list(loop.vert.co) for loop in face.loops],
            "uvs": [uv_to_list(loop[uv_layer].uv) for loop in face.loops],
            "material_index": int(face.material_index),
            "smooth": bool(face.smooth),
        })

    left, bottom, right, top = normalized_rect(trim)
    return {
        "version": 3,
        "patch_id": int(patch_id),
        "scope_id": int(patch_id),
        "mesh_name": obj.data.name,
        "trim_uuid": trim.uuid,
        "trim_name": trim.name,
        "trim_rect": [left, bottom, right, top],
        "padding": float(padding),
        "last_scale": float(scale),
        "last_axis": scale_axis,
        "last_cut_method": cut_method,
        "boundary_coords": list(boundary_coords.values()),
        "faces": source_faces,
        "source_faces": source_faces,
        "source_boundary": list(boundary_coords.values()),
        "generated_signature": {"version": 1, "faces": []},
        "generated_edge_signatures": {},
        "status": "clean",
    }


def trim_scale_stash_source_faces(stash):
    source_faces = stash.get("source_faces")
    if isinstance(source_faces, list):
        return source_faces
    faces = stash.get("faces")
    return faces if isinstance(faces, list) else []


def create_trim_scale_source_faces_from_stash(bm, uv_layer, patch_layer, stash, patch_id):
    coord_verts = {}
    source_faces = []
    for face_data in trim_scale_stash_source_faces(stash):
        coords = [Vector(coord) for coord in face_data.get("verts", [])]
        uvs = [Vector(uv) for uv in face_data.get("uvs", [])]
        if len(coords) < 3 or len(coords) != len(uvs):
            continue

        verts = []
        for co in coords:
            key = trim_scale_coord_key(co)
            vert = coord_verts.get(key)
            if vert is None or not vert.is_valid:
                vert = bm.verts.new(co)
                coord_verts[key] = vert
            verts.append(vert)

        if len(set(verts)) < 3:
            continue
        try:
            face = bm.faces.new(verts)
        except ValueError:
            continue
        face.material_index = int(face_data.get("material_index", 0))
        face.smooth = bool(face_data.get("smooth", False))
        if patch_layer is not None:
            face[patch_layer] = int(patch_id)
        face.select_set(True)
        for loop in face.loops:
            for vert, uv in zip(verts, uvs):
                if loop.vert == vert:
                    loop[uv_layer].uv = uv
                    break
        source_faces.append(face)
    return source_faces


def delete_trim_scale_output_faces(bm, faces):
    faces = [face for face in faces if face.is_valid]
    if not faces:
        return
    old_edges = {edge for face in faces for edge in face.edges if edge.is_valid}
    old_verts = {vert for face in faces for vert in face.verts if vert.is_valid}
    bmesh.ops.delete(bm, geom=faces, context="FACES")
    wire_edges = [edge for edge in old_edges if edge.is_valid and len(edge.link_faces) == 0]
    if wire_edges:
        bmesh.ops.delete(bm, geom=wire_edges, context="EDGES")
    loose_verts = [vert for vert in old_verts if vert.is_valid and len(vert.link_edges) == 0]
    if loose_verts:
        bmesh.ops.delete(bm, geom=loose_verts, context="VERTS")


def point_on_segment_factor(point, start, end, eps=TRIM_SCALE_STASH_EPS):
    segment = end - start
    length_squared = segment.length_squared
    if length_squared <= eps * eps:
        return None
    factor = (point - start).dot(segment) / length_squared
    if factor <= eps or factor >= 1.0 - eps:
        return None
    projected = start + segment * factor
    if (point - projected).length > eps:
        return None
    return factor


def find_existing_vert_near(bm, co, eps=TRIM_SCALE_STASH_EPS):
    best = None
    best_distance = eps
    for vert in bm.verts:
        if not vert.is_valid:
            continue
        distance = (vert.co - co).length
        if distance <= best_distance:
            best = vert
            best_distance = distance
    return best


def dissolve_trim_scale_restore_splits(bm, old_verts, source_coords):
    dissolve_verts = []
    for vert in old_verts:
        if not vert.is_valid or len(vert.link_faces) == 0:
            continue
        if any((vert.co - co).length <= TRIM_SCALE_STASH_EPS for co in source_coords):
            continue
        if len(vert.link_edges) <= 2:
            dissolve_verts.append(vert)
    if dissolve_verts:
        try:
            bmesh.ops.dissolve_verts(bm, verts=dissolve_verts, use_face_split=False, use_boundary_tear=False)
        except Exception:
            pass


def restore_trim_scale_patch(context, bm, uv_layer, patch_layer, obj, stash):
    patch_id = int(stash.get("patch_id", 0))
    if patch_id <= 0:
        raise RuntimeError("Invalid Trim Scale patch stash.")

    patch_faces = [face for face in bm.faces if face.is_valid and int(face[patch_layer]) == patch_id]
    if not patch_faces:
        raise RuntimeError("Could not find generated faces for this Trim Scale patch.")

    source_coords = [
        Vector(coord)
        for face_data in stash.get("faces", [])
        for coord in face_data.get("verts", [])
    ]
    old_edges = {edge for face in patch_faces for edge in face.edges if edge.is_valid}
    old_verts = {vert for face in patch_faces for vert in face.verts if vert.is_valid}

    bmesh.ops.delete(bm, geom=patch_faces, context="FACES")

    wire_edges = [edge for edge in old_edges if edge.is_valid and len(edge.link_faces) == 0]
    if wire_edges:
        bmesh.ops.delete(bm, geom=wire_edges, context="EDGES")

    dissolve_trim_scale_restore_splits(bm, old_verts, source_coords)

    loose_verts = [vert for vert in old_verts if vert.is_valid and len(vert.link_edges) == 0]
    if loose_verts:
        bmesh.ops.delete(bm, geom=loose_verts, context="VERTS")

    bm.verts.ensure_lookup_table()
    coord_verts = {}
    restored_faces = []
    created_verts = []

    for face_data in stash.get("faces", []):
        coords = [Vector(coord) for coord in face_data.get("verts", [])]
        uvs = [Vector(uv) for uv in face_data.get("uvs", [])]
        if len(coords) < 3 or len(coords) != len(uvs):
            continue

        base_verts = []
        for co in coords:
            key = trim_scale_coord_key(co)
            vert = coord_verts.get(key)
            if vert is None or not vert.is_valid:
                vert = find_existing_vert_near(bm, co)
            if vert is None:
                vert = bm.verts.new(co)
                created_verts.append(vert)
            coord_verts[key] = vert
            base_verts.append(vert)

        face_points = []
        for index, start_vert in enumerate(base_verts):
            next_index = (index + 1) % len(base_verts)
            start_co = coords[index]
            end_co = coords[next_index]
            start_uv = uvs[index]
            end_uv = uvs[next_index]
            if not face_points or face_points[-1][0] != start_vert:
                face_points.append((start_vert, start_uv))

            extras = []
            for candidate in bm.verts:
                if not candidate.is_valid or candidate in base_verts:
                    continue
                factor = point_on_segment_factor(candidate.co, start_co, end_co)
                if factor is not None:
                    extras.append((factor, candidate))
            for factor, vert in sorted(extras, key=lambda item: item[0]):
                uv = start_uv.lerp(end_uv, factor)
                if face_points[-1][0] != vert:
                    face_points.append((vert, uv))

        if len(face_points) > 1 and face_points[0][0] == face_points[-1][0]:
            face_points.pop()
        face_verts = [vert for vert, _uv in face_points]
        if len(set(face_verts)) < 3:
            continue

        try:
            face = bm.faces.new(face_verts)
        except ValueError:
            continue
        face.material_index = int(face_data.get("material_index", 0))
        face.smooth = bool(face_data.get("smooth", False))
        face[patch_layer] = 0
        face.select_set(True)
        for loop in face.loops:
            for vert, uv in face_points:
                if loop.vert == vert:
                    loop[uv_layer].uv = uv
                    break
        restored_faces.append(face)

    if not restored_faces:
        raise RuntimeError("Could not restore the original Trim Scale patch.")

    weld_verts = [vert for vert in set(created_verts) | set(coord_verts.values()) if vert.is_valid]
    if weld_verts:
        bmesh.ops.remove_doubles(bm, verts=weld_verts, dist=TRIM_SCALE_BOUNDARY_MERGE_DIST)

    for face in bm.faces:
        if face.is_valid:
            face.select_set(False)
    for face in restored_faces:
        if face.is_valid:
            face.select_set(True)

    return [face for face in restored_faces if face.is_valid]


def trim_index_for_trim(context, target_trim):
    if target_trim is None:
        return -1
    target_uuid = getattr(target_trim, "uuid", "")
    target_name = getattr(target_trim, "name", "")
    trims = trim_sections(context) or ()
    for index, trim in enumerate(trims):
        if target_uuid and trim.uuid == target_uuid:
            return index
    for index, trim in enumerate(trims):
        if target_name and trim.name == target_name:
            return index
    return -1


def face_uv_center(face, uv_layer):
    coords = [loop[uv_layer].uv for loop in face.loops]
    if not coords:
        return None
    return sum((uv.copy() for uv in coords), Vector((0.0, 0.0))) / len(coords)


def trim_index_for_face_center(context, face, uv_layer):
    center = face_uv_center(face, uv_layer)
    return trim_index_at_uv(context, center) if center is not None else -1


def resolve_trim_scale_patch_trim(context, stash, faces, uv_layer):
    trim_index = trim_index_for_face_group(context, faces, uv_layer) if faces else -1
    trims = trim_sections(context) or ()
    if 0 <= trim_index < len(trims):
        return trims[trim_index]
    return trim_for_stash(context, stash)


def collect_dirty_trim_scale_selected_faces(context, uv_layer, faces, trim):
    faces = [face for face in faces if face.is_valid]
    if not faces:
        return []

    target_trim_index = trim_index_for_trim(context, trim)
    if target_trim_index == -1:
        target_trim_index = trim_index_for_face_center(context, faces[0], uv_layer)
    if target_trim_index == -1:
        return faces

    return [
        face
        for face in faces
        if trim_index_for_face_center(context, face, uv_layer) == target_trim_index
    ]


def selected_trim_index_for_faces(context, faces, uv_layer, fallback_trim=None):
    counts = {}
    for face in faces:
        if not face.is_valid:
            continue
        trim_index = trim_index_for_face_center(context, face, uv_layer)
        if trim_index != -1:
            counts[trim_index] = counts.get(trim_index, 0) + 1
    if counts:
        return max(counts, key=lambda index: counts[index])
    return trim_index_for_trim(context, fallback_trim)


def trim_scale_faces_in_trim(context, faces, uv_layer, trim_index):
    if trim_index == -1:
        return []
    return [
        face
        for face in faces
        if face.is_valid and trim_index_for_face_center(context, face, uv_layer) == trim_index
    ]


def trim_scale_face_groups_by_trim(context, faces, uv_layer, fallback_trim_index=-1):
    groups = {}
    fallback_faces = []
    for face in faces:
        if not face.is_valid:
            continue
        trim_index = trim_index_for_face_center(context, face, uv_layer)
        if trim_index == -1:
            fallback_faces.append(face)
        else:
            groups.setdefault(trim_index, set()).add(face)
    if not groups and fallback_trim_index != -1:
        groups[fallback_trim_index] = set(fallback_faces)
    return [(trim_index, face_group) for trim_index, face_group in groups.items() if face_group]


def run_limited_dissolve_for_faces(context, bm, uv_layer, faces):
    faces = [face for face in faces if face.is_valid]
    if not faces:
        return False
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    face_indices = [face.index for face in faces]
    select_face_indices_for_uv_op(context, face_indices)
    result = bpy.ops.mesh.dissolve_limited(angle_limit=math.radians(0.5))
    return bool(result and "FINISHED" in result)


def merge_trim_scale_patch_with_border(context, obj, bm, patch_layer, patch_ids):
    if patch_layer is None:
        return bm, bm.loops.layers.uv.verify(), patch_layer
    patch_ids = {int(patch_id) for patch_id in patch_ids if int(patch_id) > 0}
    if not patch_ids:
        return bm, bm.loops.layers.uv.verify(), patch_layer

    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    face_indices = [
        face.index
        for face in bm.faces
        if face.is_valid and int(face[patch_layer]) in patch_ids
    ]
    if not face_indices:
        return bm, bm.loops.layers.uv.verify(), patch_layer

    select_face_indices_for_uv_op(context, face_indices)
    try:
        bpy.ops.mesh.select_more(use_face_step=True)
    except TypeError:
        bpy.ops.mesh.select_more()
    bpy.ops.mesh.select_mode(type="VERT")
    bpy.ops.mesh.remove_doubles(threshold=TRIM_SCALE_FINAL_MERGE_DIST)
    bpy.ops.mesh.select_mode(type="FACE")

    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.normal_update()
    uv_layer = bm.loops.layers.uv.verify()
    patch_layer = trim_scale_face_layer(bm, create=True)
    return bm, uv_layer, patch_layer


def edge_linked_only_to_patch(edge, patch_layer, patch_id):
    valid_faces = [face for face in edge.link_faces if face.is_valid]
    if len(valid_faces) != 2:
        return False
    return all(int(face[patch_layer]) == patch_id for face in valid_faces)


def tag_trim_scale_patch_edges(obj, bm, patch_layer, edge_layers, patch_ids, source_edge_keys_by_patch=None):
    edge_patch_layer, edge_role_layer, edge_id_layer = edge_layers
    if edge_patch_layer is None or edge_role_layer is None or edge_id_layer is None:
        return
    source_edge_keys_by_patch = source_edge_keys_by_patch or {}
    patch_ids = {int(patch_id) for patch_id in patch_ids if int(patch_id) > 0}
    if not patch_ids:
        return

    for edge in bm.edges:
        if not edge.is_valid:
            continue
        linked_patch_ids = {
            int(face[patch_layer])
            for face in edge.link_faces
            if face.is_valid and int(face[patch_layer]) in patch_ids
        }
        if not linked_patch_ids:
            continue

        patch_id = min(linked_patch_ids)
        source_refs = source_edge_keys_by_patch.get(patch_id, set())
        linked_only_to_patch = len(linked_patch_ids) == 1 and edge_linked_only_to_patch(edge, patch_layer, patch_id)

        if trim_scale_edge_matches_source(edge, source_refs):
            role = TRIM_SCALE_EDGE_SOURCE | TRIM_SCALE_EDGE_USER_PROTECTED
            edge_id = 0
        elif linked_only_to_patch:
            role = TRIM_SCALE_EDGE_GENERATED
            edge_id = int(edge[edge_id_layer])
            if edge_id <= 0:
                edge_id = next_trim_scale_edge_id(obj)
        else:
            role = TRIM_SCALE_EDGE_SOURCE | TRIM_SCALE_EDGE_USER_PROTECTED
            edge_id = 0

        edge[edge_patch_layer] = patch_id
        edge[edge_role_layer] = role
        edge[edge_id_layer] = edge_id


def dissolve_safe_trim_scale_generated_edges(bm, uv_layer, patch_layer, edge_layers, patch_id, stash, allowed_faces=None):
    edge_patch_layer, edge_role_layer, edge_id_layer = edge_layers
    stored = stash.get("generated_edge_signatures")
    if not isinstance(stored, dict) or not stored:
        return 0
    stored_face_keys = trim_scale_stored_generated_face_keys(stash)
    allowed_faces = {face for face in allowed_faces or [] if face.is_valid}

    safe_edges = []
    protected_edges = []
    for edge in bm.edges:
        if not edge.is_valid:
            continue
        if int(edge[edge_patch_layer]) != patch_id:
            continue
        role = int(edge[edge_role_layer])
        if not (role & TRIM_SCALE_EDGE_GENERATED):
            continue

        edge_id = int(edge[edge_id_layer])
        stored_signature = stored.get(str(edge_id))
        current_verts = [list(coord) for coord in trim_scale_edge_key(edge)]
        linked_faces = [face for face in edge.link_faces if face.is_valid]
        if allowed_faces and not all(face in allowed_faces for face in linked_faces):
            continue
        linked_faces_unchanged = (
            len(linked_faces) == 2
            and bool(stored_face_keys)
            and all(trim_scale_signature_key(trim_scale_face_signature(face, uv_layer)) in stored_face_keys for face in linked_faces)
        )
        is_safe = (
            edge_id > 0
            and isinstance(stored_signature, dict)
            and stored_signature.get("verts") == current_verts
            and linked_faces_unchanged
            and edge_linked_only_to_patch(edge, patch_layer, patch_id)
            and not bool(getattr(edge, "seam", False))
        )
        if is_safe:
            safe_edges.append(edge)
        else:
            protected_edges.append(edge)

    for edge in protected_edges:
        if edge.is_valid:
            edge[edge_role_layer] = (int(edge[edge_role_layer]) & ~TRIM_SCALE_EDGE_GENERATED) | TRIM_SCALE_EDGE_SOURCE | TRIM_SCALE_EDGE_USER_PROTECTED

    if not safe_edges:
        return 0
    try:
        bmesh.ops.dissolve_edges(bm, edges=[edge for edge in safe_edges if edge.is_valid], use_verts=True, use_face_split=False)
    except Exception:
        dissolved = 0
        for edge in safe_edges:
            if not edge.is_valid:
                continue
            try:
                bmesh.ops.dissolve_edges(bm, edges=[edge], use_verts=True, use_face_split=False)
                dissolved += 1
            except Exception:
                if edge.is_valid:
                    edge[edge_role_layer] = (int(edge[edge_role_layer]) & ~TRIM_SCALE_EDGE_GENERATED) | TRIM_SCALE_EDGE_SOURCE | TRIM_SCALE_EDGE_USER_PROTECTED
        if not dissolved:
            return 0

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.normal_update()
    return len(safe_edges)


def protect_remaining_trim_scale_generated_edges(bm, edge_layers, patch_id, allowed_faces=None):
    edge_patch_layer, edge_role_layer, _edge_id_layer = edge_layers
    if edge_patch_layer is None or edge_role_layer is None:
        return
    allowed_faces = {face for face in allowed_faces or [] if face.is_valid}
    for edge in bm.edges:
        if not edge.is_valid or int(edge[edge_patch_layer]) != patch_id:
            continue
        if allowed_faces:
            linked_faces = [face for face in edge.link_faces if face.is_valid]
            if not any(face in allowed_faces for face in linked_faces):
                continue
        role = int(edge[edge_role_layer])
        if role & TRIM_SCALE_EDGE_GENERATED:
            edge[edge_role_layer] = (role & ~TRIM_SCALE_EDGE_GENERATED) | TRIM_SCALE_EDGE_SOURCE | TRIM_SCALE_EDGE_USER_PROTECTED


def update_trim_scale_generated_signatures(bm, uv_layer, patch_layer, edge_layers, stashes, patch_ids):
    for patch_id in sorted(patch_id for patch_id in patch_ids if patch_id > 0):
        stash = stashes.get(str(patch_id))
        if not stash:
            continue
        stash["version"] = 3
        stash["generated_signature"] = trim_scale_patch_signature(
            trim_scale_patch_faces(bm, patch_layer, patch_id),
            uv_layer,
        )
        stash["status"] = "clean"
        if edge_layers and all(layer is not None for layer in edge_layers):
            stash["generated_edge_signatures"] = generated_trim_scale_edge_signatures(
                bm,
                uv_layer,
                edge_layers,
                {patch_id},
            )


def apply_trim_scale_to_selection(context, scale=1.0, padding=0.0, scale_axis="UNIFORM", cut_method="STANDARD"):
    obj = context.active_object
    if not obj or obj.type != "MESH" or context.mode != "EDIT_MESH":
        raise RuntimeError("Select a mesh object in Edit Mode.")

    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.normal_update()
    uv_layer = bm.loops.layers.uv.verify()
    patch_layer = trim_scale_face_layer(bm, create=True)
    edge_layers = trim_scale_edge_layers(bm, create=True)
    stash_data = load_trim_scale_stashes(obj)
    stash_data["version"] = 3
    stashes = stash_data["patches"]
    scale_vector = trim_scale_vector(scale, scale_axis)

    selected_patch_ids = selected_trim_scale_patch_ids(bm, patch_layer)
    selected_faces_by_patch = {
        patch_id: [
            face
            for face in trim_scale_patch_faces(bm, patch_layer, patch_id)
            if face.is_valid and face.select
        ]
        for patch_id in selected_patch_ids
    }
    selected_unpatched_faces = [
        face
        for face in bm.faces
        if face.is_valid and face.select and int(face[patch_layer]) <= 0
    ]
    face_patch_ids = {}
    target_names = []

    if selected_patch_ids:
        for patch_id in sorted(selected_patch_ids):
            stash = stashes.get(str(patch_id))
            patch_faces = trim_scale_patch_faces(bm, patch_layer, patch_id)
            selected_seed_faces = selected_faces_by_patch.get(patch_id, [])
            fallback_trim = trim_for_stash(context, stash) if stash else None
            target_trim_index = selected_trim_index_for_faces(context, selected_seed_faces, uv_layer, fallback_trim=fallback_trim)
            selected_patch_faces = trim_scale_faces_in_trim(context, selected_seed_faces, uv_layer, target_trim_index)
            if not selected_patch_faces:
                continue
            whole_patch_selected = len(selected_patch_faces) == len(patch_faces)
            status = "Clean"

            if not stash:
                trim_index = target_trim_index
                if trim_index == -1:
                    raise RuntimeError("No active trim section could be resolved.")
                trims = trim_sections(context)
                trim = trims[trim_index]
                operation_patch_id = next_trim_scale_patch_id(obj)
                for face in selected_patch_faces:
                    face[patch_layer] = operation_patch_id
                    face_patch_ids[face] = operation_patch_id
                stash = make_trim_scale_stash(operation_patch_id, obj, selected_patch_faces, uv_layer, trim, padding, scale, scale_axis=scale_axis, cut_method=cut_method)
                stashes[str(operation_patch_id)] = stash
                status = "New Patch"
                target_names.append(f"{trim.name} {status}")
                continue

            stored_signature = stash.get("generated_signature")
            selected_signature = trim_scale_patch_signature(selected_patch_faces, uv_layer)
            patch_matches_stash = bool(
                stored_signature
                and stored_signature == selected_signature
            )
            trim = resolve_trim_scale_patch_trim(context, stash, selected_patch_faces, uv_layer)

            if patch_matches_stash:
                source_faces = create_trim_scale_source_faces_from_stash(bm, uv_layer, patch_layer, stash, patch_id)
                if not source_faces:
                    patch_matches_stash = False
                else:
                    delete_trim_scale_output_faces(bm, selected_patch_faces)
                    bm.verts.ensure_lookup_table()
                    bm.edges.ensure_lookup_table()
                    bm.faces.ensure_lookup_table()
                    bm.normal_update()
                    uv_layer = bm.loops.layers.uv.verify()
                    patch_layer = trim_scale_face_layer(bm, create=True)
                    edge_layers = trim_scale_edge_layers(bm, create=True)
                for face in source_faces if patch_matches_stash else []:
                    face[patch_layer] = patch_id
                    face_patch_ids[face] = patch_id

            if not patch_matches_stash:
                if trim is None:
                    raise RuntimeError("No active trim section could be resolved.")
                operation_patch_id = patch_id if whole_patch_selected else next_trim_scale_patch_id(obj)
                current_faces = selected_patch_faces
                if not current_faces:
                    current_faces = selected_patch_faces
                    operation_patch_id = next_trim_scale_patch_id(obj)
                    status = "Recovered"
                else:
                    status = "Rebased"
                for face in current_faces:
                    face[patch_layer] = operation_patch_id
                    face_patch_ids[face] = operation_patch_id
                stash = make_trim_scale_stash(operation_patch_id, obj, current_faces, uv_layer, trim, padding, scale, scale_axis=scale_axis, cut_method=cut_method)
                stash["status"] = status.lower()
                stashes[str(operation_patch_id)] = stash

            stash["last_scale"] = float(scale)
            stash["last_axis"] = scale_axis
            stash["padding"] = float(padding)
            stash["last_cut_method"] = cut_method
            target_names.append(f"{trim.name if trim else stash.get('trim_name', 'Trim')} {status}")

        if selected_unpatched_faces:
            trims = trim_sections(context)
            for face_group in selected_face_islands(selected_unpatched_faces):
                face_group = {face for face in face_group if face.is_valid}
                if not face_group:
                    continue
                trim_index = trim_index_for_face_group(context, face_group, uv_layer)
                if trim_index == -1:
                    raise RuntimeError("No active trim section could be resolved.")
                for section_trim_index, section_faces in trim_scale_face_groups_by_trim(context, face_group, uv_layer, fallback_trim_index=trim_index):
                    trim = trims[section_trim_index]
                    patch_id = next_trim_scale_patch_id(obj)
                    stash = make_trim_scale_stash(patch_id, obj, section_faces, uv_layer, trim, padding, scale, scale_axis=scale_axis, cut_method=cut_method)
                    stashes[str(patch_id)] = stash
                    for face in section_faces:
                        face[patch_layer] = patch_id
                        face_patch_ids[face] = patch_id
                    target_names.append(f"{trim.name} New Patch")

        for face in bm.faces:
            if face.is_valid:
                face.select_set(face in face_patch_ids)
        save_trim_scale_stashes(obj, stash_data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        uv_layer = bm.loops.layers.uv.verify()
        if abs(scale - 1.0) <= 1e-6:
            active_patch_ids = set(face_patch_ids.values())
            if active_patch_ids:
                edge_layers = trim_scale_edge_layers(bm, create=True)
                source_edge_keys_by_patch = {
                    patch_id: trim_scale_source_edge_refs([face for face in bm.faces if face.is_valid and face in face_patch_ids and face_patch_ids[face] == patch_id])
                    for patch_id in active_patch_ids
                }
                tag_trim_scale_patch_edges(obj, bm, patch_layer, edge_layers, active_patch_ids, source_edge_keys_by_patch)
                update_trim_scale_generated_signatures(bm, uv_layer, patch_layer, edge_layers, stashes, active_patch_ids)
                save_trim_scale_stashes(obj, stash_data)
                for face in bm.faces:
                    if face.is_valid:
                        face.select_set(int(face[patch_layer]) in active_patch_ids)
            bmesh.update_edit_mesh(obj.data)
            return target_names
    elif abs(scale - 1.0) <= 1e-6:
        groups = collect_trim_scale_groups(context, bm, uv_layer)
        return [trim.name for _faces, trim, _patch_id in groups]
    else:
        trims = trim_sections(context)
        selected_faces = [face for face in bm.faces if face.select and face.is_valid]
        for face_group in selected_face_islands(selected_faces):
            trim_index = trim_index_for_face_group(context, face_group, uv_layer)
            if trim_index == -1:
                raise RuntimeError("No active trim section could be resolved.")
            for section_trim_index, section_faces in trim_scale_face_groups_by_trim(context, face_group, uv_layer, fallback_trim_index=trim_index):
                trim = trims[section_trim_index]
                patch_id = next_trim_scale_patch_id(obj)
                stash = make_trim_scale_stash(patch_id, obj, section_faces, uv_layer, trim, padding, scale, scale_axis=scale_axis, cut_method=cut_method)
                stashes[str(patch_id)] = stash
                for face in section_faces:
                    face_patch_ids[face] = patch_id
                target_names.append(f"{trim.name} New Patch")
        save_trim_scale_stashes(obj, stash_data)

    groups = collect_trim_scale_groups(context, bm, uv_layer, face_patch_ids=face_patch_ids, stashes=stashes)

    old_faces = set()
    created_faces = []
    created_verts = []
    face_plans = []
    edge_factors = {}
    old_edges = set()
    old_verts = set()
    old_boundary_edges = set()
    old_boundary_verts = set()
    source_edge_keys_by_patch = {}

    for face_group, trim, patch_id in groups:
        face_group = {face for face in face_group if face.is_valid}
        rect = padded_trim_rect(normalized_rect(trim), padding)
        bbox = uv_bbox_for_faces(face_group, uv_layer)
        if not bbox:
            continue
        trim_width = max(rect[2] - rect[0], MIN_TRIM_SIZE)
        trim_height = max(rect[3] - rect[1], MIN_TRIM_SIZE)
        if cut_method == "ORIGIN":
            pivot = origin_center_trim_scale_pivot(face_group, uv_layer, rect)
        else:
            pivot = None
        if pivot is None:
            pivot = Vector(((bbox[0] - rect[0]) / trim_width, (bbox[1] - rect[1]) / trim_height))
        group_set = set(face_group)
        if patch_id > 0:
            source_edge_keys_by_patch.setdefault(patch_id, []).extend(trim_scale_source_edge_refs(face_group))

        for face in face_group:
            if not face.is_valid:
                continue
            pieces = build_trim_scale_face_plan(uv_layer, face, rect, pivot, scale_vector, patch_id=patch_id)
            if pieces:
                old_faces.add(face)
                old_edges.update(edge for edge in face.edges if edge.is_valid)
                old_verts.update(vert for vert in face.verts if vert.is_valid)
                for edge in face.edges:
                    if not edge.is_valid:
                        continue
                    linked_in_group = [linked for linked in edge.link_faces if linked in group_set and linked.is_valid]
                    if len(linked_in_group) != len([linked for linked in edge.link_faces if linked.is_valid]) or len(edge.link_faces) <= 1:
                        old_boundary_edges.add(edge)
                        old_boundary_verts.update(vert for vert in edge.verts if vert.is_valid)
                for plan in pieces:
                    collect_trim_scale_edge_factors(plan["piece"], edge_factors)
                face_plans.extend(pieces)

    if not face_plans:
        raise RuntimeError("Trim Scale could not create any valid cut faces.")

    source_vert_map = clone_trim_scale_source_verts(bm, old_faces)
    created_verts.extend(vert for vert in source_vert_map.values() if vert.is_valid)
    coord_verts = {}
    split_verts = split_trim_scale_boundary_edges(edge_factors, skip_edges=old_edges)
    for (edge, _factor), vert in split_verts.items():
        if edge in old_boundary_edges and vert.is_valid:
            old_boundary_verts.add(vert)

    bmesh.ops.delete(bm, geom=[face for face in old_faces if face.is_valid], context="FACES")

    for plan in face_plans:
        new_face, new_verts = create_trim_scale_piece(
            bm,
            uv_layer,
            patch_layer,
            plan["source_props"],
            plan["piece"],
            plan["cell_x"],
            plan["cell_y"],
            plan["rect"],
            split_verts,
            source_vert_map=source_vert_map,
            coord_verts=coord_verts,
        )
        created_verts.extend(new_verts)
        if new_face:
            created_faces.append(new_face)

    cleanup_trim_scale_rebuild(bm, old_edges, old_verts, created_faces, created_verts, split_verts, boundary_verts=None, weld_old_verts=False)

    generated_patch_ids = {patch_id for _face_group, _trim, patch_id in groups if patch_id > 0}

    bm.faces.ensure_lookup_table()
    if generated_patch_ids and patch_layer is not None:
        created_faces = [
            face
            for face in bm.faces
            if face.is_valid and int(face[patch_layer]) in generated_patch_ids
        ]
    else:
        created_faces = [face for face in created_faces if face.is_valid]
    uv_layer = bm.loops.layers.uv.verify()
    patch_layer = trim_scale_face_layer(bm, create=True)
    edge_layers = trim_scale_edge_layers(bm, create=True)
    tag_trim_scale_patch_edges(obj, bm, patch_layer, edge_layers, generated_patch_ids, source_edge_keys_by_patch)
    bm.faces.ensure_lookup_table()
    if generated_patch_ids and patch_layer is not None:
        created_faces = [
            face
            for face in bm.faces
            if face.is_valid and int(face[patch_layer]) in generated_patch_ids
        ]
    else:
        created_faces = [face for face in created_faces if face.is_valid]
    if created_faces:
        bmesh.ops.recalc_face_normals(bm, faces=created_faces)
    update_trim_scale_generated_signatures(bm, uv_layer, patch_layer, edge_layers, stashes, generated_patch_ids)
    save_trim_scale_stashes(obj, stash_data)
    for face in bm.faces:
        face.select_set(False)
    for face in created_faces:
        face.select_set(True)

    bmesh.update_edit_mesh(obj.data)
    return target_names


class BAKEADDON_OT_trim_scale(Operator):
    bl_idname = "object.bakeaddon_trim_scale"
    bl_label = "Trim Scale"
    bl_description = "Interactively scale trim UV density and cut geometry so UVs stay inside the trim"
    bl_options = {"REGISTER", "UNDO"}

    scale: FloatProperty(name="Scale", default=1.0, min=0.05, max=50.0, precision=3)
    padding: FloatProperty(name="Padding", default=0.0, min=0.0, max=0.25, precision=4)
    scale_axis: EnumProperty(
        name="Direction",
        items=(
            ("UNIFORM", "XY", "Scale both trim directions"),
            ("X", "X", "Scale only trim U/X direction"),
            ("Y", "Y", "Scale only trim V/Y direction"),
        ),
        default="UNIFORM",
    )
    cut_method: EnumProperty(
        name="Scale Origin",
        items=(
            ("STANDARD", "Standard", "Use the normal selected-UV trim scaling origin"),
            ("ORIGIN", "Origin Center", "Anchor trim scaling from the object origin"),
        ),
        default="STANDARD",
    )

    init_bm = None
    obj = None
    _handle_draw_2d = None
    last_mouse_x = 0
    last_mouse_y = 0
    region_offset_x = 0
    region_offset_y = 0
    hud_x = 0
    hud_y = 0
    target_label = "Active Trim"
    passthrough = False
    init_stashes = ""
    init_next_patch_id = 1
    init_next_edge_id = 1
    snap_drag_accum = 0.0
    limited_dissolve_active = False

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == "MESH" and context.mode == "EDIT_MESH")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "scale")
        layout.prop(self, "scale_axis", expand=True)
        layout.prop(self, "cut_method", expand=True)
        layout.prop(self, "padding", slider=True)

    def invoke(self, context, event):
        self.obj = context.active_object
        try:
            bm = bmesh.from_edit_mesh(self.obj.data)
            bm.faces.ensure_lookup_table()
            selected_faces = [face for face in bm.faces if face.select and face.is_valid]
            if not selected_faces:
                raise RuntimeError("Select one or more faces first.")
            trims = trim_sections(context)
            if not trims or len(trims) == 0:
                raise RuntimeError("Create at least one trim section first.")
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}

        self._store_snapshot(context)
        self._init_cursor(event)
        try:
            self._evaluate(context, show_errors=True)
        except Exception:
            self._restore_snapshot(context)
            self.finish(context)
            return {"CANCELLED"}

        self._add_draw_handler(context)
        self._set_header(context)
        context.window_manager.modal_handler_add(self)
        if context.area:
            context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def execute(self, context):
        try:
            self.target_label = self._format_target_label(apply_trim_scale_to_selection(context, self.scale, self.padding, self.scale_axis, self.cut_method))
        except Exception as exc:
            self.report({"WARNING"}, f"Trim Scale failed: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Trim Scale applied: {self.scale:.3f}x {self.scale_axis} {self._cut_method_label()}")
        return {"FINISHED"}

    def modal(self, context, event):
        if not context.area:
            self._restore_snapshot(context)
            self.finish(context)
            self.report({"WARNING"}, "Context area lost, cancelled Trim Scale.")
            return {"CANCELLED"}

        context.area.tag_redraw()

        if event.type in {"MIDDLEMOUSE"} or event.type.startswith("NDOF"):
            self.passthrough = True
            return {"PASS_THROUGH"}

        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"} and event.value == "PRESS":
            self._toggle_cut_method()
            if not self._evaluate(context, show_errors=False):
                self._restore_snapshot(context)
                return {"RUNNING_MODAL"}

        elif event.type == "MOUSEMOVE":
            self._update_hud_location(event)
            if self.passthrough:
                self.passthrough = False
            else:
                delta = event.mouse_x - self.last_mouse_x
                if delta:
                    if event.ctrl:
                        self._drag_scale_integer(delta)
                    else:
                        self.snap_drag_accum = 0.0
                        step = 0.0015 if event.shift else 0.01
                        self.scale = max(0.05, min(50.0, self.scale + delta * step))
                    if not self._evaluate(context, show_errors=False):
                        self._restore_snapshot(context)
                        return {"RUNNING_MODAL"}

        elif event.type == "C" and event.value == "PRESS":
            self.limited_dissolve_active = not self.limited_dissolve_active
            if not self._evaluate(context, show_errors=False):
                self._restore_snapshot(context)
                return {"RUNNING_MODAL"}

        elif event.type in {"X", "Y"} and event.value == "PRESS":
            self.scale_axis = "UNIFORM" if self.scale_axis == event.type else event.type
            if not self._evaluate(context, show_errors=False):
                self._restore_snapshot(context)
                return {"RUNNING_MODAL"}

        elif event.type in {"LEFTMOUSE", "SPACE"} and event.value == "PRESS":
            self.finish(context)
            self.report({"INFO"}, f"Trim Scale applied: {self.scale:.3f}x {self.scale_axis} {self._cut_method_label()}")
            return {"FINISHED"}

        elif event.type in {"RIGHTMOUSE", "ESC"}:
            self._restore_snapshot(context)
            self.finish(context)
            self.report({"INFO"}, "Trim Scale cancelled.")
            return {"CANCELLED"}

        self.last_mouse_x = event.mouse_x
        self.last_mouse_y = event.mouse_y
        self._set_header(context)
        return {"RUNNING_MODAL"}

    def _evaluate(self, context, show_errors=False):
        self._restore_snapshot(context)
        try:
            if self.limited_dissolve_active:
                self._limited_dissolve_selected(context)
            targets = apply_trim_scale_to_selection(context, self.scale, self.padding, self.scale_axis, self.cut_method)
            self.target_label = self._format_target_label(targets)
            return True
        except Exception as exc:
            if show_errors:
                self.report({"WARNING"}, f"Trim Scale failed: {exc}")
            return False

    def _format_target_label(self, targets):
        unique_names = []
        for name in targets:
            if name not in unique_names:
                unique_names.append(name)
        if not unique_names:
            return "Active Trim"
        if len(unique_names) == 1:
            return unique_names[0]
        return f"{unique_names[0]} + {len(unique_names) - 1}"

    def _step_scale_integer(self, direction):
        current = float(self.scale)
        if direction > 0:
            snapped = math.floor(current + 1e-6) + 1
            if current < 1.0:
                snapped = 1
        else:
            snapped = math.ceil(current - 1e-6) - 1
            if current <= 1.0:
                snapped = 1
        self.scale = max(1.0, min(50.0, float(snapped)))

    def _drag_scale_integer(self, delta):
        pixels_per_step = 18.0
        self.snap_drag_accum += delta
        steps = int(self.snap_drag_accum / pixels_per_step)
        if steps == 0:
            return
        direction = 1 if steps > 0 else -1
        for _ in range(abs(steps)):
            self._step_scale_integer(direction)
        self.snap_drag_accum -= steps * pixels_per_step

    def _toggle_cut_method(self):
        self.cut_method = "ORIGIN" if self.cut_method == "STANDARD" else "STANDARD"

    def _limited_dissolve_selected(self, context):
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.faces.ensure_lookup_table()
        face_indices = [face.index for face in bm.faces if face.is_valid and face.select]
        if not face_indices:
            return False
        select_face_indices_for_uv_op(context, face_indices)
        result = bpy.ops.mesh.dissolve_limited(angle_limit=math.radians(1.0))
        return bool(result and "FINISHED" in result)

    def _init_cursor(self, event):
        self.last_mouse_x = event.mouse_x
        self.last_mouse_y = event.mouse_y
        self.region_offset_x = event.mouse_x - event.mouse_region_x
        self.region_offset_y = event.mouse_y - event.mouse_region_y
        self.hud_x = event.mouse_region_x + 22
        self.hud_y = event.mouse_region_y + 22

    def _update_hud_location(self, event):
        self.hud_x = event.mouse_x - self.region_offset_x + 22
        self.hud_y = event.mouse_y - self.region_offset_y + 22

    def _add_draw_handler(self, context):
        if self._handle_draw_2d is None:
            self._handle_draw_2d = bpy.types.SpaceView3D.draw_handler_add(self.draw_hud, (context,), "WINDOW", "POST_PIXEL")
            shared_state.trim_scale_draw_handles.append(self._handle_draw_2d)

    def _remove_draw_handler(self):
        if not self._handle_draw_2d:
            return
        if self._handle_draw_2d in shared_state.trim_scale_draw_handles:
            shared_state.trim_scale_draw_handles.remove(self._handle_draw_2d)
        try:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle_draw_2d, "WINDOW")
        except ValueError:
            pass
        self._handle_draw_2d = None

    def _set_header(self, context):
        if context.area:
            context.area.header_text_set("Trim Scale: Mouse L/R Scale | Wheel Scale Origin | Ctrl+Move Whole Steps | C Limited Dissolve | X/Y Direction | Shift Fine | LMB/Space Apply | RMB/Esc Cancel")

    def _store_snapshot(self, context):
        self._free_snapshot()
        self.init_bm = bmesh.new()
        self.init_stashes = getattr(self.obj, "bakeaddon_trim_scale_stashes", "")
        self.init_next_patch_id = getattr(self.obj, "bakeaddon_trim_scale_next_patch_id", 1)
        self.init_next_edge_id = getattr(self.obj, "bakeaddon_trim_scale_next_edge_id", 1)
        previous_mode = context.mode
        try:
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            self.init_bm.from_mesh(self.obj.data)
        finally:
            if previous_mode == "EDIT_MESH" and context.mode != "EDIT_MESH":
                try:
                    bpy.ops.object.mode_set(mode="EDIT")
                except Exception:
                    pass

    def _restore_snapshot(self, context):
        if not self.obj or self.init_bm is None:
            return
        previous_mode = context.mode
        try:
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            self.init_bm.to_mesh(self.obj.data)
            self.obj.data.update()
            self.obj.bakeaddon_trim_scale_stashes = self.init_stashes
            self.obj.bakeaddon_trim_scale_next_patch_id = self.init_next_patch_id
            self.obj.bakeaddon_trim_scale_next_edge_id = self.init_next_edge_id
        finally:
            if previous_mode == "EDIT_MESH" and context.mode != "EDIT_MESH":
                try:
                    bpy.ops.object.mode_set(mode="EDIT")
                except Exception:
                    pass

    def _free_snapshot(self):
        if self.init_bm is not None:
            try:
                self.init_bm.free()
            except Exception:
                pass
            self.init_bm = None

    def finish(self, context):
        self._remove_draw_handler()
        if context.area:
            context.area.header_text_set(None)
            context.area.tag_redraw()
        self._free_snapshot()

    def draw_hud(self, _context):
        font_id = 0
        draw_escape_cancel_hint(font_id)
        draw_shadowed_text(font_id, self.hud_x, self.hud_y + 66, "Trim Scale", 16, (0.95, 0.98, 1.0, 1.0))
        draw_shadowed_text(font_id, self.hud_x, self.hud_y + 44, f"Scale: {self.scale:.3f}x", 14, (0.42, 0.96, 0.52, 1.0))
        draw_shadowed_text(font_id, self.hud_x, self.hud_y + 24, f"Direction: {self.scale_axis}", 14, (0.42, 0.82, 1.0, 1.0))
        draw_shadowed_text(font_id, self.hud_x, self.hud_y + 4, f"Origin: {self._cut_method_label()}", 14, (0.95, 0.72, 1.0, 1.0))
        draw_shadowed_text(font_id, self.hud_x, self.hud_y - 16, f"Trim: {self.target_label}", 14, (0.98, 0.82, 0.42, 1.0))
        dissolve_text = "Limited Dissolve: On" if self.limited_dissolve_active else "Limited Dissolve: Off"
        draw_shadowed_text(font_id, self.hud_x, self.hud_y - 36, dissolve_text, 12, (1.0, 0.95, 0.55, 1.0))
        draw_shadowed_text(
            font_id,
            self.hud_x,
            self.hud_y - 56,
            "C: Limited Dissolve | X/Y Direction | Mouse L/R Scale | Shift Fine | LMB/Space Apply | RMB/Esc Cancel",
            12,
            (0.78, 0.82, 0.86, 1.0),
        )

    def _cut_method_label(self):
        return "Origin Center" if self.cut_method == "ORIGIN" else "Standard"


def islands_for_transform(context):
    obj, bm, uv_layer, selected_faces = selected_mesh_data(context)
    return obj, bm, uv_layer, selected_face_islands(selected_faces)


def island_center_inside_rect(faces, uv_layer, rect):
    bbox = uv_bbox_for_faces(faces, uv_layer)
    if not bbox:
        return False
    center = bbox_center(bbox)
    left, bottom, right, top = rect
    return left <= center.x <= right and bottom <= center.y <= top


class BAKEADDON_OT_trim_move_in_trim(Operator):
    bl_idname = "uv.bakeaddon_trim_move_in_trim"
    bl_label = "Move in Trim"
    bl_description = "Move selected UV islands inside the active trim"
    bl_options = {"REGISTER", "UNDO"}

    move: FloatVectorProperty(name="Move", size=2, default=(0.05, 0.0), subtype="XYZ")
    lock_in_trim: BoolProperty(name="Lock in Trim", default=True)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == "MESH" and context.mode == "EDIT_MESH")

    def execute(self, context):
        try:
            obj, _bm, uv_layer, islands = islands_for_transform(context)
            rect = active_trim_rect(context)
            moved = 0
            for island in islands:
                if not island_center_inside_rect(island, uv_layer, rect):
                    continue
                move_island_uvs(island, uv_layer, Vector(self.move))
                if self.lock_in_trim:
                    clamp_island_to_rect(island, uv_layer, rect)
                moved += 1
            bmesh.update_edit_mesh(obj.data)
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        if moved == 0:
            self.report({"INFO"}, "No selected islands inside active trim.")
        return {"FINISHED"}


class BAKEADDON_OT_trim_rotate_in_trim(Operator):
    bl_idname = "uv.bakeaddon_trim_rotate_in_trim"
    bl_label = "Rotate in Trim"
    bl_description = "Rotate selected UV islands inside the active trim"
    bl_options = {"REGISTER", "UNDO"}

    angle: FloatProperty(name="Angle", default=math.radians(90.0), subtype="ANGLE")
    lock_in_trim: BoolProperty(name="Lock in Trim", default=True)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == "MESH" and context.mode == "EDIT_MESH")

    def execute(self, context):
        try:
            obj, _bm, uv_layer, islands = islands_for_transform(context)
            rect = active_trim_rect(context)
            for island in islands:
                bbox = uv_bbox_for_faces(island, uv_layer)
                if not bbox:
                    continue
                if not island_center_inside_rect(island, uv_layer, rect):
                    continue
                rotate_island_uvs(island, uv_layer, self.angle, bbox_center(bbox))
                if self.lock_in_trim:
                    fit_island_to_rect(island, uv_layer, rect, keep_proportion=True, padding=0.0)
            bmesh.update_edit_mesh(obj.data)
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BAKEADDON_OT_trim_flip_in_trim(Operator):
    bl_idname = "uv.bakeaddon_trim_flip_in_trim"
    bl_label = "Flip in Trim"
    bl_description = "Flip selected UV islands around the active trim center"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(
        name="Direction",
        items=(("HORIZONTAL", "Horizontal", ""), ("VERTICAL", "Vertical", "")),
        default="HORIZONTAL",
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == "MESH" and context.mode == "EDIT_MESH")

    def execute(self, context):
        try:
            obj, _bm, uv_layer, islands = islands_for_transform(context)
            rect = active_trim_rect(context)
            pivot = bbox_center(rect)
            scale = Vector((-1.0, 1.0)) if self.direction == "HORIZONTAL" else Vector((1.0, -1.0))
            for island in islands:
                if island_center_inside_rect(island, uv_layer, rect):
                    scale_island_uvs(island, uv_layer, scale, pivot)
            bmesh.update_edit_mesh(obj.data)
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BAKEADDON_OT_trim_scale_in_trim(Operator):
    bl_idname = "uv.bakeaddon_trim_scale_in_trim"
    bl_label = "Scale in Trim"
    bl_description = "Scale selected UV islands inside the active trim"
    bl_options = {"REGISTER", "UNDO"}

    scale: FloatVectorProperty(name="Scale", size=2, default=(1.1, 1.1), min=0.0, subtype="XYZ")
    lock_in_trim: BoolProperty(name="Lock in Trim", default=True)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == "MESH" and context.mode == "EDIT_MESH")

    def execute(self, context):
        try:
            obj, _bm, uv_layer, islands = islands_for_transform(context)
            rect = active_trim_rect(context)
            for island in islands:
                bbox = uv_bbox_for_faces(island, uv_layer)
                if not bbox or not island_center_inside_rect(island, uv_layer, rect):
                    continue
                scale_island_uvs(island, uv_layer, Vector(self.scale), bbox_center(bbox))
                if self.lock_in_trim:
                    fit_island_to_rect(island, uv_layer, rect, keep_proportion=True, padding=0.0)
            bmesh.update_edit_mesh(obj.data)
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BAKEADDON_OT_trim_align_to_trim(Operator):
    bl_idname = "uv.bakeaddon_trim_align_to_trim"
    bl_label = "Align to Trim"
    bl_description = "Align selected UV islands to the active trim"
    bl_options = {"REGISTER", "UNDO"}

    align: EnumProperty(
        name="Align",
        items=(
            ("CENTER", "Center", ""),
            ("LEFT", "Left", ""),
            ("RIGHT", "Right", ""),
            ("TOP", "Top", ""),
            ("BOTTOM", "Bottom", ""),
        ),
        default="CENTER",
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == "MESH" and context.mode == "EDIT_MESH")

    def execute(self, context):
        try:
            obj, _bm, uv_layer, islands = islands_for_transform(context)
            rect = active_trim_rect(context)
            target_left, target_bottom, target_right, target_top = rect
            target_center = bbox_center(rect)
            for island in islands:
                bbox = uv_bbox_for_faces(island, uv_layer)
                if not bbox:
                    continue
                left, bottom, right, top = bbox
                center = bbox_center(bbox)
                delta = Vector((0.0, 0.0))
                if self.align == "CENTER":
                    delta = target_center - center
                elif self.align == "LEFT":
                    delta.x = target_left - left
                elif self.align == "RIGHT":
                    delta.x = target_right - right
                elif self.align == "TOP":
                    delta.y = target_top - top
                elif self.align == "BOTTOM":
                    delta.y = target_bottom - bottom
                move_island_uvs(island, uv_layer, delta)
            bmesh.update_edit_mesh(obj.data)
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BAKEADDON_OT_trim_hotspot_mapping(Operator):
    bl_idname = "uv.bakeaddon_trim_hotspot_mapping"
    bl_label = "Hotspot Mapping"
    bl_description = "Assign selected UV islands to trims with the closest aspect ratio"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == "MESH" and context.mode == "EDIT_MESH")

    def execute(self, context):
        trims = trim_sections(context)
        if not trims or len(trims) == 0:
            self.report({"WARNING"}, "Create at least one trim first.")
            return {"CANCELLED"}
        settings = trim_settings(context)
        try:
            obj, _bm, uv_layer, islands = islands_for_transform(context)
            trim_aspects = []
            for trim in trims:
                left, bottom, right, top = normalized_rect(trim)
                trim_aspects.append((trim, max(right - left, 1e-8) / max(top - bottom, 1e-8)))
            for island in islands:
                bbox = uv_bbox_for_faces(island, uv_layer)
                if bbox is None:
                    initialize_island_uvs_from_geometry(island, uv_layer)
                    bbox = uv_bbox_for_faces(island, uv_layer)
                width, height = bbox_size(bbox)
                aspect = width / height
                trim = min(trim_aspects, key=lambda item: abs(math.log(max(aspect, 1e-8) / item[1])))[0]
                fit_island_to_rect(
                    island,
                    uv_layer,
                    normalized_rect(trim),
                    keep_proportion=not (settings and settings.assign_stretch_fill),
                    padding=settings.padding if settings else 0.0,
                )
            bmesh.update_edit_mesh(obj.data)
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BAKEADDON_OT_trim_select_by_face(Operator):
    bl_idname = "uv.bakeaddon_trim_select_by_face"
    bl_label = "Trim by Face"
    bl_description = "Set active trim from the selected face UV position"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == "MESH" and context.mode == "EDIT_MESH")

    def execute(self, context):
        try:
            _obj, _bm, uv_layer, selected_faces = selected_mesh_data(context)
            bbox = uv_bbox_for_faces(selected_faces, uv_layer)
            if bbox is None:
                self.report({"WARNING"}, "Selected faces have no readable UVs.")
                return {"CANCELLED"}
            center = bbox_center(bbox)
            index = trim_index_at_uv(context, center)
            if index == -1:
                self.report({"INFO"}, "Selected face UVs are not inside any trim.")
                return {"CANCELLED"}
            set_active_trim_index(context, index)
            tag_image_editors(context)
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BAKEADDON_OT_trim_select_islands_by_trim(Operator):
    bl_idname = "uv.bakeaddon_trim_select_islands_by_trim"
    bl_label = "Islands by Trim"
    bl_description = "Select faces whose UV centers are inside the active trim"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == "MESH" and context.mode == "EDIT_MESH")

    def execute(self, context):
        trim = active_trim(context)
        if not trim:
            self.report({"WARNING"}, "No active trim.")
            return {"CANCELLED"}
        try:
            obj = context.active_object
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            uv_layer = bm.loops.layers.uv.verify()
            rect = normalized_rect(trim)
            selected = 0
            for face in bm.faces:
                coords = [loop[uv_layer].uv for loop in face.loops]
                center = sum((uv.copy() for uv in coords), Vector((0.0, 0.0))) / max(len(coords), 1)
                inside = rect[0] <= center.x <= rect[2] and rect[1] <= center.y <= rect[3]
                face.select_set(inside)
                if inside:
                    selected += 1
            bmesh.update_edit_mesh(obj.data)
            self.report({"INFO"}, f"Selected {selected} faces in active trim.")
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BAKEADDON_OT_trim_eyedropper(Operator):
    bl_idname = "uv.bakeaddon_trim_eyedropper"
    bl_label = "Trim by Eyedropper"
    bl_description = "Click a trim in the UV Editor to make it active"
    bl_options = {"REGISTER", "BLOCKING"}
    request_stop = False

    @classmethod
    def poll(cls, context):
        return bool(context.area and context.area.type == "IMAGE_EDITOR")

    def invoke(self, context, _event):
        global EYEDROPPER_OPERATOR
        if EYEDROPPER_OPERATOR is not None:
            EYEDROPPER_OPERATOR.request_stop = True
            return {"FINISHED"}
        self.request_stop = False
        settings = trim_settings(context)
        if settings:
            settings.draw_enabled = True
        EYEDROPPER_OPERATOR = self
        ensure_trimsheet_draw_handler()
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Trim by Eyedropper: LMB pick trim | RMB/Esc Cancel")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if self.request_stop:
            return self.finish(context, cancelled=True)
        if event.type == "ESC" or (event.type == "RIGHTMOUSE" and event.value == "PRESS"):
            return self.finish(context, cancelled=True)
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            uv = uv_from_event(context.area, event)
            if uv is None:
                return {"RUNNING_MODAL"}
            index = trim_index_at_uv(context, uv)
            if index != -1:
                set_active_trim_index(context, index)
                tag_image_editors(context)
                return self.finish(context, cancelled=False)
            self.report({"INFO"}, "No trim under cursor.")
        return {"RUNNING_MODAL"}

    def finish(self, context, cancelled=False):
        global EYEDROPPER_OPERATOR
        if EYEDROPPER_OPERATOR is self:
            EYEDROPPER_OPERATOR = None
        if context.area:
            context.area.header_text_set(None)
            context.area.tag_redraw()
        return {"CANCELLED"} if cancelled else {"FINISHED"}


class BAKEADDON_PT_trim_sheet_uv(Panel):
    bl_label = "Trim Sheet"
    bl_idname = "BAKEADDON_PT_trim_sheet_uv"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Bake Addon 2.1"

    def draw(self, context):
        ensure_trimsheet_draw_handler()
        layout = self.layout
        settings = trim_settings(context)
        image = active_image_from_context(context)
        material = trim_owner(context)

        if settings.draw_trim_active:
            row = layout.row(align=True)
            row.operator(
                BAKEADDON_OT_trim_draw.bl_idname,
                text="Draw Trim",
                icon="GREASEPENCIL",
                depress=True,
            )
            return

        row = layout.row(align=True)
        if material:
            row.label(text=material.name, icon="MATERIAL")
        elif image:
            row.label(text=image.name, icon="IMAGE_DATA")
        else:
            row.label(text="No Active Material", icon="ERROR")
        row.prop(settings, "draw_enabled", text="", icon="OVERLAY")

        if material is None:
            layout.label(text="Select a mesh material to edit trims.", icon="INFO")
            return

        row = layout.row(align=True)
        row.template_list("BAKEADDON_UL_trim_sections", "", material, "bakeaddon_trim_sections", material, "bakeaddon_trim_index", rows=5)
        col = row.column(align=True)
        col.operator(BAKEADDON_OT_trim_add.bl_idname, text="", icon="ADD")
        col.operator(BAKEADDON_OT_trim_remove.bl_idname, text="", icon="REMOVE")
        col.separator()
        col.operator(BAKEADDON_OT_trim_import_preset.bl_idname, text="", icon="IMPORT")
        col.operator(BAKEADDON_OT_trim_export_preset.bl_idname, text="", icon="EXPORT")

        row = layout.row(align=True)
        row.operator(
            BAKEADDON_OT_trim_draw.bl_idname,
            text="Draw Trim",
            icon="GREASEPENCIL",
            depress=settings.draw_trim_active,
        )
        edit_text = "Stop Edit" if settings.edit_enabled else "Edit Trim Sheet"
        row.operator(BAKEADDON_OT_trim_toggle_edit.bl_idname, text=edit_text, icon="EDITMODE_HLT")

        layout.separator()
        draw_assignment_ui(layout, settings)

        trim = active_trim(context)
        if not trim:
            layout.label(text="No trim selected.", icon="INFO")
            return

        box = layout.box()
        box.prop(trim, "name")
        box.prop(trim, "color")
        grid = box.grid_flow(columns=2, align=True)
        grid.prop(trim, "left")
        grid.prop(trim, "right")
        grid.prop(trim, "bottom")
        grid.prop(trim, "top")


def draw_assignment_ui(layout, settings):
    row = layout.row(align=True)
    row.operator(
        BAKEADDON_OT_assign_to_trim.bl_idname,
        text="Assign to Trim On" if settings.assign_enabled else "Assign to Trim",
        icon="SNAP_FACE",
        depress=settings.assign_enabled,
    )

    row = layout.row(align=True)
    row.prop(
        settings,
        "assign_settings_expanded",
        text="Assignment Settings",
        icon="TRIA_DOWN" if settings.assign_settings_expanded else "TRIA_RIGHT",
        emboss=False,
    )
    if settings.assign_settings_expanded:
        box = layout.box()
        box.prop(settings, "assign_uv_unwrap")
        box.prop(settings, "assign_world_orient")
        box.prop(settings, "assign_stretch_fill")
        box.prop(settings, "assign_islands_to_trim")
        box.prop(settings, "assign_faces_to_trim")
        box.prop(settings, "padding", slider=True)
