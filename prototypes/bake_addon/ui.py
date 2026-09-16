# --- START OF FILE ui.py --- [MODIFIED]

# bakeaddon/ui.py
import blf
import bmesh
import bpy
import gpu
import math
import traceback
from bpy.types import Operator, Panel
from gpu_extras.batch import batch_for_shader
import os

# --- Import shared state ---
from . import shared_state

# Import operator identifiers
from . import operators_bevel_shader
from . import utils
from .operators_general import (
    OBJECT_OT_select_factor_loop, MESH_OT_surface_select,
    BAKEADDON_OT_copy_email
)
from .operators_cleanup import OBJECT_OT_BooleanCleanup
from .operators_offset import OBJECT_OT_bakeaddon_offset
# --- Import Baking Operators --- <<< --- MODIFIED --- <<<
from .operators_baking import (
    BAKER_OT_AutoRename, OBJECT_OT_create_cages_from_list,
    BAKER_OT_Bake, # Original Bake
    BAKER_OT_visualize_ray_distance, BAKER_OT_remove_ray_distance_visualization,
    BAKER_OT_output_settings,
    update_max_ray_distance
)
# --- Import Bevel Shader Operators ---
from .operators_bevel_shader import (
    BAKEADDON_OT_bevel_global_cleanup,
    BAKEADDON_OT_bevel_material_preview_toggle,
    BAKEADDON_OT_bevel_materials_repair,
    BAKEADDON_OT_bevel_object_cleanup,
    BAKEADDON_OT_bevel_preset_add,
    BAKEADDON_OT_bevel_preset_remove,
    BAKEADDON_OT_bevel_preview_toggle,
    BAKEADDON_OT_bevel_set_radius,
)


BEVEL_PRESET_SWATCH_COLORS = (
    (0.95, 0.25, 0.25, 1.0),
    (0.68, 0.32, 0.95, 1.0),
    (0.25, 0.55, 1.0, 1.0),
    (0.26, 0.78, 0.42, 1.0),
    (0.97, 0.85, 0.25, 1.0),
    (1.0, 0.58, 0.18, 1.0),
)


# --- Helper Functions ---

# --- Import LOD Operator ---
from .operators_lod import OBJECT_OT_generate_lods
# --- Import Matrix Bake Operator ---
from .operators_matrix_bake import MATRIXBAKE_OT_matrix_bake
# --- Import Vertex Color Operators ---
from .operators_vertex_color import (
    OBJECT_OT_set_custom_vertex_color_ops,
    OBJECT_OT_set_random_vertex_color_ops,
    OBJECT_OT_select_by_vertex_color
)
# --- Import UnBevel Operators ---
from .operators_unbevel import (
    OBJECT_OT_unbevel_modal_op
)
from .operators_auto_uv import (
    MOFProperties,
    AutoUVOperator,
    AverageIslandsScaleOperator,
    PackUVOperator,
    MOFSeamsOperator,
    UnWrapOperator,
    SelectStretchingModalOperator,
    draw_mof_auto_uv_settings
)
# --- Import Bake Explode Operators ---
from .operators_bake_explode import (
    BAKE_EXPLODE_OT_Store,
    BAKE_EXPLODE_OT_Reset,
    BAKE_EXPLODE_OT_Clear,
    BAKE_EXPLODE_OT_HideLinkedDuplicates,
    BAKE_EXPLODE_OT_UnhideLinkedDuplicates,
    BAKE_EXPLODE_OT_ToggleVisualization,
    has_bake_explode_data,
    get_stored_transform,
    ORIGINAL_TRANSFORM_PROP,
    transforms_match,
    get_object_matrix_values
)
from .operators_export import (
    BAKEADDON_OT_export_model,
    BAKEADDON_OT_open_export_dir,
    BAKEADDON_OT_origin_z_minimum,
)
from .operators_smart_apply import BAKEADDON_OT_smart_apply_transforms


# --- Helper function to safely get icon ID ---
# [ ... keep existing get_icon_id function ... ]
def get_icon_id(icon_name):
    """Safely retrieves the icon ID from shared_state.custom_icons, returning 0 if not found or error."""
    default_icon_id = 0
    if not shared_state.custom_icons:
        return default_icon_id
    try:
        if icon_name in shared_state.custom_icons:
            icon_item = shared_state.custom_icons[icon_name]
            if hasattr(icon_item, 'icon_id'):
                return icon_item.icon_id
            else:
                print(f"WARNING (get_icon_id): Icon item '{icon_name}' loaded but has no 'icon_id'.")
                return default_icon_id
        else:
            # If icon not found in custom collection, return 0 (will show no icon or default)
            # print(f"DEBUG (get_icon_id): Custom icon '{icon_name}' not found.") # Optional debug
            return default_icon_id
    except Exception as e:
        print(f"ERROR (get_icon_id): Error getting icon ID for '{icon_name}': {e}")
        return default_icon_id
# --- End helper function ---


def _set_blf_size(font_id, size):
    try:
        blf.size(font_id, size)
    except TypeError:
        blf.size(font_id, size, bpy.context.preferences.system.dpi or 72)


def _get_uniform_shader():
    for name in ("UNIFORM_COLOR", "2D_UNIFORM_COLOR"):
        try:
            return gpu.shader.from_builtin(name)
        except Exception:
            continue
    raise RuntimeError("No compatible uniform color shader found.")


_RADIAL_IMAGE_SHADER = None
_RADIAL_ICON_TEXTURES = {}
RADIAL_ICON_DISPLAY_SCALE = 3.168


def _soften_radial_icon_pixels(pixels, width, height):
    original_alpha = [pixels[idx + 3] for idx in range(0, len(pixels), 4)]
    softened_alpha = original_alpha[:]
    weights = (
        (-1, -1, 1.0), (0, -1, 2.0), (1, -1, 1.0),
        (-1, 0, 2.0), (0, 0, 4.0), (1, 0, 2.0),
        (-1, 1, 1.0), (0, 1, 2.0), (1, 1, 1.0),
    )

    for y in range(height):
        for x in range(width):
            pixel_index = y * width + x
            alpha = original_alpha[pixel_index]
            neighbor_values = []
            weighted_sum = 0.0
            weight_sum = 0.0

            for offset_x, offset_y, weight in weights:
                sample_x = x + offset_x
                sample_y = y + offset_y
                if sample_x < 0 or sample_x >= width or sample_y < 0 or sample_y >= height:
                    continue

                sample_alpha = original_alpha[sample_y * width + sample_x]
                neighbor_values.append(sample_alpha)
                weighted_sum += sample_alpha * weight
                weight_sum += weight

            if not neighbor_values:
                continue

            is_edge = min(neighbor_values) < 0.98 and max(neighbor_values) > 0.02
            if not is_edge:
                continue

            blurred_alpha = weighted_sum / weight_sum if weight_sum else alpha
            softened_alpha[pixel_index] = min(alpha, max(blurred_alpha, 0.72)) if alpha > 0.72 else blurred_alpha

    for y in range(height):
        for x in range(width):
            pixel_index = y * width + x
            rgba_index = pixel_index * 4
            alpha = softened_alpha[pixel_index]

            if alpha <= 0.002:
                pixels[rgba_index] = 0.0
                pixels[rgba_index + 1] = 0.0
                pixels[rgba_index + 2] = 0.0
                pixels[rgba_index + 3] = 0.0
                continue

            if original_alpha[pixel_index] <= 0.04:
                red = green = blue = total = 0.0
                for offset_x, offset_y, weight in weights:
                    sample_x = x + offset_x
                    sample_y = y + offset_y
                    if sample_x < 0 or sample_x >= width or sample_y < 0 or sample_y >= height:
                        continue

                    sample_index = sample_y * width + sample_x
                    sample_alpha = original_alpha[sample_index]
                    if sample_alpha <= 0.04:
                        continue

                    sample_rgba = sample_index * 4
                    weighted_alpha = sample_alpha * weight
                    red += pixels[sample_rgba] * weighted_alpha
                    green += pixels[sample_rgba + 1] * weighted_alpha
                    blue += pixels[sample_rgba + 2] * weighted_alpha
                    total += weighted_alpha

                if total > 0.0:
                    pixels[rgba_index] = red / total
                    pixels[rgba_index + 1] = green / total
                    pixels[rgba_index + 2] = blue / total

            pixels[rgba_index + 3] = alpha


def _compensate_radial_icon_pixels_for_blender_5(pixels):
    if bpy.app.version < (5, 0, 0):
        return
    for index in range(0, len(pixels), 4):
        alpha = pixels[index + 3]
        if alpha <= 0.002:
            continue
        pixels[index] = max(0.0, min(1.0, pixels[index])) ** (1.0 / 2.2)
        pixels[index + 1] = max(0.0, min(1.0, pixels[index + 1])) ** (1.0 / 2.2)
        pixels[index + 2] = max(0.0, min(1.0, pixels[index + 2])) ** (1.0 / 2.2)


def _get_image_shader():
    global _RADIAL_IMAGE_SHADER

    if _RADIAL_IMAGE_SHADER:
        return _RADIAL_IMAGE_SHADER

    vertex_shader = '''
        uniform mat4 ModelViewProjectionMatrix;
        in vec2 pos;
        in vec2 texCoord;
        out vec2 uv_interp;

        void main()
        {
            uv_interp = texCoord;
            gl_Position = ModelViewProjectionMatrix * vec4(pos.xy, 0.0, 1.0);
        }
    '''
    fragment_shader = '''
        uniform sampler2D image;
        in vec2 uv_interp;
        out vec4 fragColor;

        void main()
        {
            vec4 color = texture(image, uv_interp);
            float alpha = color.a;
            if (alpha <= 0.002) {
                discard;
            }
            fragColor = vec4(color.rgb, alpha);
        }
    '''

    try:
        _RADIAL_IMAGE_SHADER = gpu.types.GPUShader(vertex_shader, fragment_shader)
        return _RADIAL_IMAGE_SHADER
    except Exception:
        for name in ("IMAGE", "2D_IMAGE"):
            try:
                _RADIAL_IMAGE_SHADER = gpu.shader.from_builtin(name)
                return _RADIAL_IMAGE_SHADER
            except Exception:
                continue

    return None


def _get_radial_icon_texture(icon_name):
    if not icon_name:
        return None

    if icon_name in _RADIAL_ICON_TEXTURES:
        return _RADIAL_ICON_TEXTURES[icon_name]

    icon_path = os.path.join(os.path.dirname(__file__), "icons", f"{icon_name}.png")
    if not os.path.exists(icon_path):
        return None

    try:
        image = bpy.data.images.load(icon_path, check_existing=True)
        cleaned_image = image.copy()
        cleaned_image.name = f"BakeAddon_Radial_{icon_name}"

        pixels = list(cleaned_image.pixels)
        width, height = cleaned_image.size
        _soften_radial_icon_pixels(pixels, width, height)
        _compensate_radial_icon_pixels_for_blender_5(pixels)
        cleaned_image.pixels.foreach_set(pixels)
        if bpy.app.version >= (5, 0, 0):
            try:
                cleaned_image.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
        cleaned_image.update()

        texture = gpu.texture.from_image(cleaned_image)
        dimensions = tuple(cleaned_image.size)
    except Exception:
        return None

    _RADIAL_ICON_TEXTURES[icon_name] = (texture, dimensions)
    return _RADIAL_ICON_TEXTURES[icon_name]


def _draw_icon_texture(center_x, center_y, size, icon_name):
    shader = _get_image_shader()
    icon_data = _get_radial_icon_texture(icon_name)
    if not shader or not icon_data:
        return False

    texture, dimensions = icon_data
    image_width, image_height = dimensions
    if image_width <= 0 or image_height <= 0:
        return False

    scale = size / max(image_width, image_height)
    draw_width = image_width * scale
    draw_height = image_height * scale
    half_width = draw_width * 0.5
    half_height = draw_height * 0.5
    positions = (
        (center_x - half_width, center_y - half_height),
        (center_x + half_width, center_y - half_height),
        (center_x + half_width, center_y + half_height),
        (center_x - half_width, center_y - half_height),
        (center_x + half_width, center_y + half_height),
        (center_x - half_width, center_y + half_height),
    )
    tex_coords = (
        (0.0, 0.0),
        (1.0, 0.0),
        (1.0, 1.0),
        (0.0, 0.0),
        (1.0, 1.0),
        (0.0, 1.0),
    )

    batch = batch_for_shader(shader, "TRIS", {"pos": positions, "texCoord": tex_coords})
    shader.bind()
    shader.uniform_sampler("image", texture)
    try:
        gpu.state.blend_set('ALPHA')
        batch.draw(shader)
    finally:
        gpu.state.blend_set('ALPHA')
    return True


def _circle_triangles(center_x, center_y, radius, segments=48):
    vertices = []
    for idx in range(segments):
        a1 = (idx / segments) * math.tau
        a2 = ((idx + 1) / segments) * math.tau
        vertices.extend([
            (center_x, center_y),
            (center_x + math.cos(a1) * radius, center_y + math.sin(a1) * radius),
            (center_x + math.cos(a2) * radius, center_y + math.sin(a2) * radius),
        ])
    return vertices


def _draw_filled_circle(shader, center_x, center_y, radius, color):
    batch = batch_for_shader(shader, "TRIS", {"pos": _circle_triangles(center_x, center_y, radius)})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _circle_line_segments(center_x, center_y, radius, segments=72):
    vertices = []
    for idx in range(segments):
        a1 = (idx / segments) * math.tau
        a2 = ((idx + 1) / segments) * math.tau
        vertices.extend([
            (center_x + math.cos(a1) * radius, center_y + math.sin(a1) * radius),
            (center_x + math.cos(a2) * radius, center_y + math.sin(a2) * radius),
        ])
    return vertices


def _arc_line_segments(center_x, center_y, radius, start_angle, end_angle, segments=24):
    vertices = []
    for idx in range(segments):
        t1 = idx / segments
        t2 = (idx + 1) / segments
        a1 = start_angle + (end_angle - start_angle) * t1
        a2 = start_angle + (end_angle - start_angle) * t2
        vertices.extend([
            (center_x + math.cos(a1) * radius, center_y + math.sin(a1) * radius),
            (center_x + math.cos(a2) * radius, center_y + math.sin(a2) * radius),
        ])
    return vertices


def _annular_sector_triangles(center_x, center_y, inner_radius, outer_radius, start_angle, end_angle, segments=32):
    vertices = []
    for idx in range(segments):
        t1 = idx / segments
        t2 = (idx + 1) / segments
        a1 = start_angle + (end_angle - start_angle) * t1
        a2 = start_angle + (end_angle - start_angle) * t2
        inner_1 = (center_x + math.cos(a1) * inner_radius, center_y + math.sin(a1) * inner_radius)
        inner_2 = (center_x + math.cos(a2) * inner_radius, center_y + math.sin(a2) * inner_radius)
        outer_1 = (center_x + math.cos(a1) * outer_radius, center_y + math.sin(a1) * outer_radius)
        outer_2 = (center_x + math.cos(a2) * outer_radius, center_y + math.sin(a2) * outer_radius)
        vertices.extend([inner_1, outer_1, outer_2, inner_1, outer_2, inner_2])
    return vertices


def _draw_annular_sector(shader, center_x, center_y, inner_radius, outer_radius, start_angle, end_angle, color):
    batch = batch_for_shader(
        shader,
        "TRIS",
        {"pos": _annular_sector_triangles(center_x, center_y, inner_radius, outer_radius, start_angle, end_angle)},
    )
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_sector_outline(shader, center_x, center_y, inner_radius, outer_radius, start_angle, end_angle, color, width=1.0):
    vertices = []
    vertices.extend(_arc_line_segments(center_x, center_y, inner_radius, start_angle, end_angle))
    vertices.extend(_arc_line_segments(center_x, center_y, outer_radius, start_angle, end_angle))
    vertices.extend([
        (center_x + math.cos(start_angle) * inner_radius, center_y + math.sin(start_angle) * inner_radius),
        (center_x + math.cos(start_angle) * outer_radius, center_y + math.sin(start_angle) * outer_radius),
        (center_x + math.cos(end_angle) * inner_radius, center_y + math.sin(end_angle) * inner_radius),
        (center_x + math.cos(end_angle) * outer_radius, center_y + math.sin(end_angle) * outer_radius),
    ])
    _draw_line_segments(shader, vertices, color, width=width)


def _draw_line_segments(shader, vertices, color, width=1.0):
    if not vertices:
        return

    try:
        gpu.state.line_width_set(width)
    except Exception:
        pass

    batch = batch_for_shader(shader, "LINES", {"pos": vertices})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)

    try:
        gpu.state.line_width_set(1.0)
    except Exception:
        pass


def _draw_circle_outline(shader, center_x, center_y, radius, color, width=1.0):
    _draw_line_segments(shader, _circle_line_segments(center_x, center_y, radius), color, width=width)


def _draw_line(shader, start, end, color, width=1.0):
    _draw_line_segments(shader, [start, end], color, width=width)


def _draw_text_centered(text, x, y, size, color):
    font_id = 0
    _set_blf_size(font_id, size)
    width, height = blf.dimensions(font_id, text)
    blf.color(font_id, 0.0, 0.0, 0.0, 0.55)
    blf.position(font_id, x - width / 2 + 1, y - height / 2 - 1, 0)
    blf.draw(font_id, text)
    blf.color(font_id, *color)
    blf.position(font_id, x - width / 2, y - height / 2, 0)
    blf.draw(font_id, text)


def _angle_distance_degrees(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


class BAKEADDON_OT_radial_menu(Operator):
    bl_idname = "bakeaddon.radial_menu"
    bl_label = "Bake Addon 2.1 Radial Menu"
    bl_description = "Open Bake Addon 2.1's modeling radial menu"
    bl_options = {'INTERNAL', 'BLOCKING'}

    radial_scale = 0.8
    radius = 118.0 * radial_scale
    button_radius = 16.0 * radial_scale
    center_radius = 70.0 * radial_scale
    outer_radius = 154.0 * radial_scale
    _handle = None
    _center = (0.0, 0.0)
    _hover_id = None

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'MESH' and context.mode == 'EDIT_MESH')

    def _items(self):
        return (
            {
                "id": "cleanup",
                "label": "Boolean",
                "subtitle": "Cleanup",
                "operator": OBJECT_OT_BooleanCleanup.bl_idname,
                "angle": 90.0,
                "color": (0.12, 0.58, 0.48, 0.95),
                "icon": "BC",
                "icon_name": "BooleanCleanup",
            },
            {
                "id": "auto_unwrap",
                "label": "Auto",
                "subtitle": "Unwrap",
                "action": "auto_unwrap",
                "angle": 38.5714285714,
                "color": (0.20, 0.56, 0.88, 0.95),
                "icon": "AU",
                "icon_name": "AutoUnwrap",
            },
            {
                "id": "easy_loop",
                "label": "Easy",
                "subtitle": "Loop",
                "operator": OBJECT_OT_select_factor_loop.bl_idname,
                "angle": -12.8571428571,
                "color": (0.15, 0.38, 0.78, 0.95),
                "icon": "EL",
                "icon_name": "EasyLoop",
            },
            {
                "id": "offset",
                "label": "Offset",
                "subtitle": "Width",
                "action": "offset_interactive",
                "angle": -64.2857142857,
                "color": (0.78, 0.42, 0.13, 0.95),
                "icon": "OF",
                "icon_name": "Offset",
            },
            {
                "id": "surface_select",
                "label": "Surface",
                "subtitle": "Select",
                "operator": MESH_OT_surface_select.bl_idname,
                "angle": -115.7142857143,
                "color": (0.22, 0.50, 0.84, 0.95),
                "icon": "SS",
                "icon_name": "SelectSurface",
            },
            {
                "id": "trim_scale",
                "label": "Trim",
                "subtitle": "Scale",
                "operator": "object.bakeaddon_trim_scale",
                "angle": -167.1428571429,
                "color": (0.44, 0.74, 0.28, 0.95),
                "icon": "TS",
                "icon_name": "TrimScale",
            },
            {
                "id": "unbevel",
                "label": "UnBevel",
                "subtitle": "",
                "operator": OBJECT_OT_unbevel_modal_op.bl_idname,
                "operator_props": {"edge_slide": False},
                "angle": 141.4285714286,
                "color": (0.64, 0.20, 0.30, 0.95),
                "icon": "UB",
                "icon_name": "UnBevel",
            },
        )

    def invoke(self, context, event):
        if not self.poll(context):
            self.report({'WARNING'}, "Bake Addon 2.1 radial menu is available in Edit Mesh mode.")
            return {'CANCELLED'}

        self._center = (float(event.mouse_region_x), float(event.mouse_region_y))
        self._hover_id = None
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback, (context,), 'WINDOW', 'POST_PIXEL')
        shared_state.radial_draw_handles.append(self._handle)

        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'MIDDLEMOUSE'} or event.type.startswith('NDOF'):
            return {'PASS_THROUGH'}

        if event.type == 'MOUSEMOVE':
            self._hover_id = self.hit_test(event.mouse_region_x, event.mouse_region_y)
            if context.area:
                context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self.finish(context)
            return {'CANCELLED'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            hit_id = self.hit_test(event.mouse_region_x, event.mouse_region_y)
            if not hit_id:
                self.finish(context)
                return {'CANCELLED'}

            item = next((entry for entry in self._items() if entry["id"] == hit_id), None)
            if not item:
                self.finish(context)
                return {'CANCELLED'}

            action = item.get("action")

            if action == "offset_interactive":
                self.finish(context)
                return self.invoke_operator(OBJECT_OT_bakeaddon_offset.bl_idname, interactive=True)

            if action == "auto_unwrap":
                self.finish(context)
                return self.invoke_auto_unwrap(context)

            operator_id = item.get("operator")
            self.finish(context)
            return self.invoke_operator(operator_id, **item.get("operator_props", {}))

        return {'RUNNING_MODAL'}

    def finish(self, context):
        if self._handle:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            except ValueError:
                pass
            if self._handle in shared_state.radial_draw_handles:
                shared_state.radial_draw_handles.remove(self._handle)
            self._handle = None

        if context.area:
            context.area.tag_redraw()

    def invoke_operator(self, operator_id, **properties):
        if not operator_id:
            return {'CANCELLED'}

        try:
            module_name, operator_name = operator_id.split(".", 1)
            operator = getattr(getattr(bpy.ops, module_name), operator_name)
            result = operator('INVOKE_DEFAULT', **properties)
            if result and 'CANCELLED' in result:
                return {'CANCELLED'}
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, f"Could not run {operator_id}: {exc}")
            return {'CANCELLED'}

    def invoke_auto_unwrap(self, context):
        addon = context.preferences.addons.get(__package__)
        prefs = addon.preferences if addon else None
        zip_path = bpy.path.abspath(prefs.executable_path) if prefs and prefs.executable_path else ""

        if not zip_path or not os.path.exists(zip_path):
            return self.invoke_operator("wm.bakeaddon_missing_mof_popup")

        return self.invoke_operator(AutoUVOperator.bl_idname)

    def hit_test(self, mouse_x, mouse_y):
        cx, cy = self._center
        distance = math.hypot(mouse_x - cx, mouse_y - cy)
        for item in self._items():
            x, y = self.item_position(item)
            if math.hypot(mouse_x - x, mouse_y - y) <= self.button_radius:
                return item["id"]

        inner_radius = self.center_radius + 5 * self.radial_scale
        outer_radius = self.outer_radius
        if distance < inner_radius or distance > outer_radius:
            return None

        items = self._items()
        mouse_angle = math.degrees(math.atan2(mouse_y - cy, mouse_x - cx))
        nearest = min(items, key=lambda entry: _angle_distance_degrees(mouse_angle, entry["angle"]))
        slot_count = len(items)
        slot_width = 360.0 / max(slot_count, 1)
        if _angle_distance_degrees(mouse_angle, nearest["angle"]) <= slot_width * 0.48:
            return nearest["id"]
        return None

    def item_position(self, item):
        angle = math.radians(item["angle"])
        cx, cy = self._center
        return (
            cx + math.cos(angle) * self.radius,
            cy + math.sin(angle) * self.radius,
        )

    def draw_callback(self, _context):
        try:
            shader = _get_uniform_shader()
            gpu.state.blend_set('ALPHA')

            cx, cy = self._center
            items = self._items()
            ui_scale = self.radial_scale
            inner_radius = self.center_radius + 6 * ui_scale
            outer_radius = self.outer_radius
            slot_count = len(items)
            slot_width = math.tau / slot_count
            gap = math.radians(1.6)

            _draw_filled_circle(shader, cx + 8 * ui_scale, cy - 10 * ui_scale, outer_radius + 2 * ui_scale, (0.0, 0.0, 0.0, 0.20))
            _draw_circle_outline(shader, cx, cy, outer_radius + 1 * ui_scale, (0.0, 0.0, 0.0, 0.48), width=2.2 * ui_scale)

            for item in items:
                mid_angle = math.radians(item["angle"])
                start_angle = mid_angle - slot_width * 0.5 + gap
                end_angle = mid_angle + slot_width * 0.5 - gap
                _draw_annular_sector(
                    shader,
                    cx,
                    cy,
                    inner_radius,
                    outer_radius,
                    start_angle,
                    end_angle,
                    (0.035, 0.037, 0.040, 0.58),
                )
                _draw_sector_outline(
                    shader,
                    cx,
                    cy,
                    inner_radius,
                    outer_radius,
                    start_angle,
                    end_angle,
                    (0.32, 0.34, 0.36, 0.26),
                    width=0.8 * ui_scale,
                )

            hovered_item = next((entry for entry in items if entry["id"] == self._hover_id), None)
            if hovered_item:
                hover_angle = math.radians(hovered_item["angle"])
                start_angle = hover_angle - slot_width * 0.5 + gap
                end_angle = hover_angle + slot_width * 0.5 - gap
                color = hovered_item["color"]
                _draw_annular_sector(
                    shader,
                    cx,
                    cy,
                    inner_radius,
                    outer_radius,
                    start_angle,
                    end_angle,
                    (color[0], color[1], color[2], 0.22),
                )
                _draw_sector_outline(
                    shader,
                    cx,
                    cy,
                    inner_radius,
                    outer_radius + 1 * ui_scale,
                    start_angle,
                    end_angle,
                    (1.0, 0.48, 0.54, 0.92),
                    width=2.2 * ui_scale,
                )

            _draw_circle_outline(shader, cx, cy, outer_radius, (0.12, 0.13, 0.14, 0.78), width=1.4 * ui_scale)
            _draw_circle_outline(shader, cx, cy, inner_radius, (0.38, 0.40, 0.42, 0.34), width=1.2 * ui_scale)
            _draw_filled_circle(shader, cx, cy, self.center_radius, (0.040, 0.042, 0.044, 0.88))
            _draw_circle_outline(shader, cx, cy, self.center_radius, (0.40, 0.42, 0.44, 0.42), width=1.4 * ui_scale)

            if hovered_item:
                hub_lines = [hovered_item["label"]]
                if hovered_item.get("subtitle"):
                    hub_lines.append(hovered_item["subtitle"])
            else:
                hub_lines = ["Bake Addon 2.1", "Modeling"]

            title_size = max(1, round(11 * ui_scale))
            hint_size = max(1, round(8 * ui_scale))
            line_height = 15 * ui_scale
            first_line_y = cy + ((len(hub_lines) - 1) * line_height * 0.5) + 6 * ui_scale
            for index, line in enumerate(hub_lines):
                _draw_text_centered(line, cx, first_line_y - index * line_height, title_size, (0.96, 0.96, 0.94, 0.98))

            hint_color = (0.58, 0.60, 0.63, 0.86)
            _draw_text_centered("[L] Apply", cx, cy - 31 * ui_scale, hint_size, hint_color)
            _draw_text_centered("[R] Close", cx, cy - 45 * ui_scale, hint_size, hint_color)

            for item in items:
                x, y = self.item_position(item)
                is_hovered = item["id"] == self._hover_id
                scale = 1.18 if is_hovered else 1.0
                icon_size = self.button_radius * RADIAL_ICON_DISPLAY_SCALE * scale

                if not _draw_icon_texture(x, y, icon_size, item.get("icon_name")):
                    text_size = max(1, round((8 if len(item.get("icon", "")) > 2 else 10) * ui_scale))
                    _draw_text_centered(item.get("icon", ""), x, y + 1 * ui_scale, text_size, (0.98, 1.0, 1.0, 1.0))

        finally:
            try:
                gpu.state.blend_set('NONE')
            except Exception:
                pass


# --- Main UI Panel ---
# [ ... keep existing BakeAddonPanel ... ]
class BakeAddonPanel(Panel):
    bl_label = "Bake Addon 2.1"; bl_idname = "OBJECT_PT_bakeaddon"; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = "Bake Addon 2.1"

    @classmethod
    def poll(cls, context):
        return True

    def draw(self, context):
        layout = self.layout
        obj = context.object
        selected_objects = context.selected_objects
        active_obj = context.active_object
        mode = context.mode
        is_object_mode = (mode == 'OBJECT')
        is_edit_mode = (mode == 'EDIT_MESH')
        is_active_mesh = bool(active_obj and active_obj.type == 'MESH')
        selected_meshes = [o for o in selected_objects if o.type == 'MESH']
        are_selected_meshes = bool(selected_meshes)
        scene = context.scene # Get scene for properties

        shortcut_box = layout.box()
        shortcut_col = shortcut_box.column(align=True)
        shortcut_col.label(text="Radial Menu - Alt + G (in Edit Mode)", icon='EVENT_G')

        # Remove all drawing code for individual sections:
        # --- SubD Modeling Section ---
        # --- Object Controls Section (OBJECT MODE) ---
        # --- UnBevel Tools Section (EDIT MODE ONLY) ---
        # --- Selection Section (EDIT MODE - Modified) ---
        # --- Boolean Cleanup Section (EDIT MODE - MODIFIED) ---

        # Keep this method empty or add a simple separator if desired,
        # as child panels will draw themselves.
        # layout.separator() # Optional: add a separator at the top if needed


# --- Bevel Shader Panel ---
class MATERIAL_PT_bevel_shading_panel(Panel):
    bl_label = "Bevel Shader"
    bl_idname = "MATERIAL_PT_bevel_shading_bakeaddon"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = BakeAddonPanel.bl_idname
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        try:
            self._draw_content(context)
        except Exception as exc:
            self.layout.label(text="Bevel Shader UI error", icon='ERROR')
            print(f"Bake Addon 2.1 Error: Bevel Shader panel draw failed: {exc}")
            traceback.print_exc()

    def _draw_content(self, context):
        layout = self.layout
        scene = context.scene
        obj = context.active_object

        if hasattr(scene, 'bevel_shading_props'):
            props = scene.bevel_shading_props

            # Auto-add a single default preset only once ever (on first open)
            if not props.presets and not scene.bevel_shader_auto_added:
                # Use timer to defer modification until after draw cycle
                def _add_default_preset():
                    try:
                        scene.bevel_shader_auto_added = True
                        props = scene.bevel_shading_props
                        if not props.presets:
                            preset = props.presets.add()
                            preset.radius = 10.0  # 10 cm
                            preset.units = 'CENTIMETERS'
                            preset.color = BEVEL_PRESET_SWATCH_COLORS[0][:3]
                            preset.last_scene_radius = operators_bevel_shader.display_radius_to_scene(10.0, 'CENTIMETERS')
                            props.presets_index = 0
                            operators_bevel_shader.update_scene_max_bevel(scene)
                            # Force redraw to show the new preset immediately
                            for window in bpy.context.window_manager.windows:
                                for area in window.screen.areas:
                                    if area.type == 'VIEW_3D':
                                        area.tag_redraw()
                    except Exception as e:
                        print(f"Bake Addon 2.1: Could not auto-add default preset: {e}")
                    return None  # Don't repeat
                bpy.app.timers.register(_add_default_preset)

            render_row = layout.row(align=True)
            render_row.scale_y = 1.45
            render_row.operator(BAKEADDON_OT_bevel_preview_toggle.bl_idname, text="Render Preview", icon='SHADING_RENDERED')

            preset_box = layout.box()
            for index, preset in enumerate(props.presets):
                row = preset_box.row(align=True)
                swatch = row.row(align=True)
                swatch.scale_x = 0.6
                swatch.template_node_socket(color=tuple(preset.color) + (1.0,))
                row.prop(preset, "radius", text="")
                row.prop(preset, "units", text="")
                assign_op = row.operator(BAKEADDON_OT_bevel_set_radius.bl_idname, text="", icon_value=get_icon_id("GreenCheck"))
                assign_op.radius = preset.radius
                assign_op.units = preset.units
                remove_op = row.operator(BAKEADDON_OT_bevel_preset_remove.bl_idname, text="", icon='X')
                remove_op.index = index

            # All four buttons side by side in one horizontal row
            buttons_row = layout.row(align=True)

            # Plus button (scaled) - highlighted with spacing around it
            add_col = buttons_row.column(align=True)
            add_col.scale_x = 1.3
            add_col.scale_y = 1.3
            add_col.operator(BAKEADDON_OT_bevel_preset_add.bl_idname, text="", icon_value=get_icon_id("plus"))

            # Spacer to visually separate the add button
            buttons_row.separator(factor=2.5)

            # Repair button
            repair_col = buttons_row.column(align=True)
            repair_col.operator(BAKEADDON_OT_bevel_materials_repair.bl_idname, text="Repair", icon='FILE_REFRESH')

            # Clean Selected button
            clean_sel_col = buttons_row.column(align=True)
            clean_sel_col.operator(BAKEADDON_OT_bevel_object_cleanup.bl_idname, text="Clean Selected", icon='TRASH')

            # Clean All button
            clean_all_col = buttons_row.column(align=True)
            clean_all_col.operator(BAKEADDON_OT_bevel_global_cleanup.bl_idname, text="Clean All", icon='ORPHAN_DATA')

            # Boolean Radius panel below buttons
            if obj and obj.type == 'MESH' and hasattr(obj, operators_bevel_shader.OBJ_DISPLAY_RADIUS_PROP):
                obj_box = layout.box()
                header = obj_box.row(align=True)
                header.prop(
                    props,
                    "boolean_radius_expanded",
                    text="Boolean Radius",
                    icon='TRIA_DOWN' if props.boolean_radius_expanded else 'TRIA_RIGHT',
                    emboss=False,
                )
                if props.boolean_radius_expanded:
                    obj_box.prop(obj, operators_bevel_shader.OBJ_DISPLAY_RADIUS_PROP, text="Radius")
                    obj_box.prop(obj, operators_bevel_shader.OBJ_DISPLAY_UNITS_PROP, text="Units")
        else:
            layout.label(text="Bevel Props Error!", icon='ERROR')


# --- Auto Rename Panel ---
class OBJECT_PT_auto_rename_panel(Panel):
    bl_label = "Auto Rename Tools"
    bl_idname = "OBJECT_PT_auto_rename_bakeaddon" # Unique ID
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = BakeAddonPanel.bl_idname # Parent is the main panel
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        row_header = layout.row(align=True)
        rename_icon_id = get_icon_id("AutoRename") # Use existing icon
        row_header.label(text="Auto Rename Tools", icon_value=rename_icon_id)

        # --- Moved Rename Section ---
        box_rename = layout.box() # Use layout directly, no need for extra box label
        box_rename.operator(BAKER_OT_AutoRename.bl_idname, icon_value=rename_icon_id)

        # --- ADDED RENAME MODE ---
        box_rename.prop(scene, "baker_rename_mode")
        # --- END ADDED RENAME MODE ---

        row_rename_opts = box_rename.row(align=True)
        row_rename_opts.prop(scene, "baker_rename_use_bbox", text="By BBox", toggle=True)
        row_rename_opts.prop(scene, "baker_rename_use_pivot", text="By Pivot", toggle=True)
        row_bbox_thresh = box_rename.row()
        row_bbox_thresh.prop(scene, "baker_rename_bbox_threshold")
        row_bbox_thresh.enabled = scene.baker_rename_use_bbox
        row_pivot_thresh = box_rename.row()
        row_pivot_thresh.prop(scene, "baker_rename_pivot_threshold")
        row_pivot_thresh.enabled = scene.baker_rename_use_pivot


# --- Baking Panel --- <<< --- MODIFIED --- <<<
class OBJECT_PT_bake_panel(Panel):
    bl_label = "Bake"
    bl_idname = "OBJECT_PT_map_baking_tools_bakeaddon"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = BakeAddonPanel.bl_idname
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        render_bake = scene.render.bake
        is_normal_bake = (scene.bake_map_type == 'NORMAL')
        is_pureplay = scene.baker_export_preset == 'PUREPLAY_ORD'

        # ═══════════════════════════════════════════════════════════
        # SECTION 1: MAP TYPE & RESOLUTION (Side by Side, Compact)
        # ═══════════════════════════════════════════════════════════
        box_config = layout.box()
        box_config.label(text="Bake Configuration", icon='IMAGE_DATA')
        box_config.prop(scene, "baker_export_preset", text="Preset")
        
        # Map Type and Resolution side by side
        split_config = box_config.split(factor=0.5, align=True)
        
        # Left: Map Type
        col_left = split_config.column(align=True)
        if is_pureplay:
            col_left.label(text="PurePlay Maps:")
            col_left.prop(scene, "baker_pureplay_bake_ao", text="AO Map")
        else:
            col_left.label(text="Map Type:")
            col_left.prop(scene, "bake_map_type", text="")
        
        # Right: Resolution
        col_right = split_config.column(align=True)
        col_right.label(text="Resolution:")
        col_right.prop(scene, "baker_resolution_preset", text="")
        
        # Custom Resolution (if selected)
        if scene.baker_resolution_preset == 'CUSTOM':
            row_custom = box_config.row(align=True)
            row_custom.prop(scene, "baker_custom_width", text="Width")
            row_custom.prop(scene, "baker_custom_height", text="Height")
        
        # Normal Format (if Normal map selected)
        if is_pureplay:
            box_config.separator(factor=0.5)
            box_config.label(text="PNG • DirectX Normal • ORD: R AO, G Roughness, B Displacement", icon='INFO')
        elif is_normal_bake:
            box_config.separator(factor=0.5)
            box_config.label(text="Normal Format:")
            box_config.prop(scene, "baker_normal_format", expand=True)

        # ═══════════════════════════════════════════════════════════
        # SECTION 3: QUALITY SETTINGS (Compact)
        # ═══════════════════════════════════════════════════════════
        box_quality = layout.box()
        box_quality.label(text="Quality Settings", icon='SHADING_RENDERED')
        
        # Samples and Margin side by side
        split_quality = box_quality.split(factor=0.5, align=True)
        col_left = split_quality.column(align=True)
        col_right = split_quality.column(align=True)
        
        col_left.label(text="Samples:")
        col_left.prop(scene, "baker_samples", text="")
        
        col_right.label(text="Margin:")
        col_right.prop(render_bake, "margin", text="")
        
        # Performance toggles
        row_perf = box_quality.row(align=True)
        row_perf.prop(scene, "baker_use_gpu", text="Use GPU", toggle=True)
        row_perf.prop(scene, "baker_use_compositor_aa", text="Anti-Alias", toggle=True)

        # ═══════════════════════════════════════════════════════════
        # SECTION 3.5: UV CHANNEL OVERRIDE (Optional)
        # ═══════════════════════════════════════════════════════════
        box_uv = layout.box()
        row_uv_header = box_uv.row(align=True)
        row_uv_header.prop(scene, "baker_use_uv_override", 
                          text="UV Transfer",
                          icon='DISCLOSURE_TRI_DOWN' if scene.baker_use_uv_override else 'DISCLOSURE_TRI_RIGHT',
                          emboss=False)
        
        if scene.baker_use_uv_override:
            col_uv = box_uv.column(align=True)
            col_uv.separator(factor=0.3)
            
            # Get active mesh object for UV layer list
            active_obj = context.active_object
            has_uv_layers = active_obj and active_obj.type == 'MESH' and active_obj.data.uv_layers
            
            # Always HIGH_TO_LOW mode now
            col_uv.label(text="Source UV (High Poly):")
            if has_uv_layers:
                col_uv.prop_search(scene, "baker_source_uv_map", active_obj.data, "uv_layers", text="", icon='UV')
            else:
                col_uv.prop(scene, "baker_source_uv_map", text="", icon='UV')
                col_uv.label(text="Select mesh to see UV list", icon='INFO')
            
            col_uv.separator(factor=0.3)
            col_uv.label(text="Target UV (Low Poly):")
            if has_uv_layers:
                col_uv.prop_search(scene, "baker_target_uv_map", active_obj.data, "uv_layers", text="", icon='UV')
            else:
                col_uv.prop(scene, "baker_target_uv_map", text="", icon='UV')
                col_uv.label(text="Select mesh to see UV list", icon='INFO')
            
            col_uv.separator(factor=0.3)
            box_info = col_uv.box()
            box_info.label(text="Sets active UV layer before baking", icon='INFO')
            box_info.label(text="Leave empty to keep current active UV")

        # ═══════════════════════════════════════════════════════════
        # SECTION 4: HIGH-TO-LOW SETTINGS (Always shown now)
        # ═══════════════════════════════════════════════════════════
        box_htl = layout.box()
        box_htl.label(text="High-to-Low Settings", icon='MODIFIER_ON')
        
        col_htl = box_htl.column(align=True)
        
        # Cage Creation
        col_htl.label(text="Cage Setup:")
        row_cage_create = col_htl.row(align=True)
        row_cage_create.operator(OBJECT_OT_create_cages_from_list.bl_idname, 
                                text="Create Cages", 
                                icon_value=get_icon_id("CreateCage"))
        row_cage_create.prop(scene, "baker_cage_offset", text="Offset")
        
        col_htl.separator(factor=0.5)
        
        # Cage Usage
        col_htl.label(text="Projection:")
        row_cage_use = col_htl.row(align=True)
        row_cage_use.prop(render_bake, "use_cage", text="Use Cage Object", toggle=True)
        
        if not render_bake.use_cage:
            col_htl.prop(render_bake, "cage_extrusion", text="Extrusion", slider=True)
        
        col_htl.separator(factor=0.5)
        
        # Ray Distance
        col_htl.label(text="Ray Distance:")
        row_ray = col_htl.row(align=True)
        row_ray.prop(render_bake, "max_ray_distance", text="Max")
        row_ray.prop(scene, "baker_show_ray_distance", 
                    text="", 
                    icon='HIDE_OFF' if scene.baker_show_ray_distance else 'HIDE_ON', 
                    toggle=True)

        # ═══════════════════════════════════════════════════════════
        # SECTION 4: NO SKEW (Advanced, Collapsible)
        # ═══════════════════════════════════════════════════════════
        box_skew = layout.box()
        row_skew_header = box_skew.row(align=True)
        row_skew_header.prop(scene, "baker_use_no_skew", 
                            text="No Skew (Advanced)", 
                            icon='DISCLOSURE_TRI_DOWN' if scene.baker_use_no_skew else 'DISCLOSURE_TRI_RIGHT',
                            emboss=False)
        
        if scene.baker_use_no_skew:
            col_skew = box_skew.column(align=True)
            col_skew.separator(factor=0.3)
            
            row_angle = col_skew.row(align=True)
            row_angle.prop(scene, "baker_no_skew_use_angle", text="Use Angle Threshold", toggle=True)
            if scene.baker_no_skew_use_angle:
                row_angle.prop(scene, "baker_no_skew_angle", text="")
            
            col_skew.prop(scene, "baker_no_skew_use_sharps", text="Use Sharp Edges", toggle=True)

        # ═══════════════════════════════════════════════════════════
        # SECTION 5: OUTPUT
        # ═══════════════════════════════════════════════════════════
        row_output = layout.row(align=True)
        row_output.scale_y = 1.0
        split_output = row_output.split(factor=0.35, align=True)
        split_output.operator(BAKER_OT_output_settings.bl_idname, text="Output", icon='FILE_FOLDER')
        # Show current save location
        output_path = bpy.path.abspath(scene.bake_output_dir) if scene.bake_output_dir else bpy.path.abspath("//")
        split_output.label(text=output_path)

        # ═══════════════════════════════════════════════════════════
        # SECTION 6: BAKE ACTIONS (Matrix Bake as Primary)
        # ═══════════════════════════════════════════════════════════
        layout.separator()
        
        box_actions = layout.box()
        col_actions = box_actions.column(align=True)
        
        # Large Matrix Bake icon on top (same scale as LOD icon)
        row_icon = col_actions.row(align=True)
        bake_icon_id = get_icon_id("Bake")
        row_icon.template_icon(bake_icon_id, scale=3.0)
        
        # PRIMARY: Matrix Bake Button (Prominent, no icon)
        btn_row = col_actions.row(align=True)
        btn_row.scale_y = 1.25
        btn_row.scale_x = 0.6
        btn_row.operator(MATRIXBAKE_OT_matrix_bake.bl_idname, text="MATRIX BAKE")

        # Primary bake options directly under Matrix Bake for better visibility
        row_matrix_options = col_actions.row(align=True)
        row_matrix_options.scale_y = 0.9
        row_matrix_options.prop(scene, "baker_matrix_bake_selected",
                                text="Bake Selected")
        row_matrix_options.prop(scene, "baker_multiple_materials",
                                text="Multiple Materials")

        col_actions.separator(factor=0.5)


# --- Export Panel ---
class OBJECT_PT_export_bakeaddon(Panel):
    bl_label = "Export"
    bl_idname = "OBJECT_PT_export_bakeaddon"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = BakeAddonPanel.bl_idname
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        if not hasattr(scene, "bakeaddon_export_settings"):
            layout.label(text="Initializing...", icon='INFO')
            return

        export_settings = scene.bakeaddon_export_settings

        row = layout.row(align=True)
        row.label(text="Method:")
        row.prop(export_settings, "export_mode", expand=False)

        row = layout.row(align=True)
        row.label(text="Format:")
        row.enabled = export_settings.export_mode != "SUBSTANCE_PAINTER"
        row.prop(export_settings, "export_format", expand=False)

        if export_settings.export_format == "FBX" and export_settings.export_mode != "SUBSTANCE_PAINTER":
            row = layout.row(align=True)
            row.label(text="Engine:")
            row.prop(export_settings, "export_target_engine", expand=False)

            row = layout.row(align=True)
            row.prop(export_settings, "embed_textures", text="Embed Textures")

            if export_settings.export_target_engine == "UNITY" and export_settings.show_export_axis_tooltip:
                box = layout.box()
                row = box.row()
                row.label(text="Unity Axis Note", icon="INFO")
                row = box.row()
                row.label(text="Enable Bake Axis Conversion after import")

            if export_settings.export_target_engine == "UNREAL" and export_settings.show_export_axis_tooltip:
                box = layout.box()
                row = box.row()
                row.label(text="Unreal Axis Note", icon="INFO")
                row = box.row()
                row.label(text="Enable Force Front X Axis after import")

        show_apply_options = not (
            export_settings.export_format == "OBJ"
            and export_settings.export_mode in {"ALL", "COLLECTION"}
        )
        show_apply_rot_scale = show_apply_options and export_settings.export_format in {"FBX", "GLTF"}
        show_apply_loc = show_apply_options and export_settings.export_mode in {
            "INDIVIDUAL",
            "PARENT",
        }

        grid = layout.column(align=True)

        row = grid.row(align=True)
        split = row.split(factor=0.52, align=True)
        left = split.row(align=True)
        right = split.row(align=True)
        left.prop(export_settings, "delete_mats_before_export", text="Delete Materials")
        if show_apply_rot_scale:
            if export_settings.apply_rot:
                right.prop(export_settings, "apply_rot", text="Rot", icon="CHECKBOX_HLT")
            else:
                right.prop(export_settings, "apply_rot", text="Rot", icon="CHECKBOX_DEHLT")

        row = grid.row(align=True)
        split = row.split(factor=0.52, align=True)
        left = split.row(align=True)
        right = split.row(align=True)
        if export_settings.export_mode != "INDIVIDUAL":
            left.prop(export_settings, "export_combine_meshes", text="Merge Meshes")
        else:
            left.label(text="")
        if show_apply_rot_scale:
            if export_settings.apply_scale:
                right.prop(export_settings, "apply_scale", text="Scale", icon="CHECKBOX_HLT")
            else:
                right.prop(export_settings, "apply_scale", text="Scale", icon="CHECKBOX_DEHLT")

        row = grid.row(align=True)
        split = row.split(factor=0.52, align=True)
        left = split.row(align=True)
        right = split.row(align=True)
        if export_settings.export_mode == "SUBSTANCE_PAINTER":
            left.prop(export_settings, "substance_triangulate_quads", text="Triangulate Quads")
        else:
            left.prop(export_settings, "triangulate_before_export", text="Triangulate")
        if show_apply_loc:
            if export_settings.apply_loc:
                right.prop(export_settings, "apply_loc", text="Loc", icon="CHECKBOX_HLT")
            else:
                right.prop(export_settings, "apply_loc", text="Loc", icon="CHECKBOX_DEHLT")

        row = grid.row(align=True)
        split = row.split(factor=0.52, align=True)
        left = split.row(align=True)
        right = split.row(align=True)
        left.prop(export_settings, "convert_viewport_colors", text="Convert Colors")
        right.label(text="")

        if export_settings.export_format == "FBX":
            if (
                export_settings.apply_rot
                and export_settings.export_mode == "PARENT"
                and export_settings.export_target_engine != "UNITY"
            ):
                row = layout.row()
                row.prop(export_settings, "apply_rot_rotated", text="Rotated Children")

        if export_settings.export_mode == "ALL":
            box = layout.box()
            row = box.row()
            row.prop(export_settings, "set_custom_fbx_name", text="Custom Name")
            if export_settings.set_custom_fbx_name:
                row = box.row(align=True)
                row.label(text="Name:")
                row.prop(export_settings, "custom_fbx_name")

        box = layout.box()
        row = box.row()
        row.prop(export_settings, "export_custom_options", text="Advanced Options")
        if export_settings.export_custom_options:
            if export_settings.export_format == "FBX":
                row = box.row(align=True)
                row.label(text=" Smoothing")
                row.prop(export_settings, "export_smoothing", expand=False)

                row = box.row(align=True)
                row.label(text=" Loose Edges")
                row.prop(export_settings, "export_loose_edges")

                row = box.row(align=True)
                row.label(text=" Tangents")
                row.prop(export_settings, "export_tangent_space")

                row = box.row(align=True)
                row.label(text=" Deform Bones")
                row.prop(export_settings, "export_only_deform_bones")

                row = box.row(align=True)
                row.label(text=" Leaf Bones")
                row.prop(export_settings, "export_add_leaf_bones")

                row = box.row(align=True)
                row.label(text=" VC Space")
                row.prop(export_settings, "export_vc_color_space", expand=False)

                row = box.row(align=True)
                row.label(text=" Custom Props")
                row.prop(export_settings, "export_custom_props")

                box.separator(factor=0.5)

                row = box.row(align=True)
                row.prop(export_settings, "export_animation", text="Animation")

                anim_col = box.column(align=True)
                anim_col.enabled = export_settings.export_animation

                row = anim_col.row(align=True)
                row.label(text="")
                row.prop(export_settings, "export_anim_key_all_bones", text="Key All Bones")

                row = anim_col.row(align=True)
                row.label(text="")
                row.prop(export_settings, "export_anim_nla_strips", text="NLA Strips")

                row = anim_col.row(align=True)
                row.label(text="")
                row.enabled = not export_settings.use_selective_actions
                row.prop(export_settings, "export_anim_all_actions", text="All Actions")

                # --- Selective Actions Filtering ---
                row = anim_col.row(align=True)
                if export_settings.use_selective_actions:
                    row.operator("bakeaddon.export_toggle_selective_actions", text="Filter Actions: ON", depress=True)
                else:
                    row.operator("bakeaddon.export_toggle_selective_actions", text="Filter Actions")

                if export_settings.use_selective_actions:
                    try:
                        action_box = anim_col.box()
                        action_box.label(text=f"Actions: {len(export_settings.filtered_actions)}", icon='ACTION')

                        if len(export_settings.filtered_actions) > 0:
                            row = action_box.row(align=True)
                            row.operator("bakeaddon.export_refresh_actions", text="Refresh", icon='FILE_REFRESH')
                            row.operator("bakeaddon.export_select_all_actions", text="All", icon='CHECKBOX_HLT')
                            row.operator("bakeaddon.export_deselect_all_actions", text="None", icon='CHECKBOX_DEHLT')

                            list_col = action_box.column(align=True)
                            for idx, act in enumerate(export_settings.filtered_actions):
                                row = list_col.row(align=True)
                                row.prop(act, "use_export", text=act.name)
                        else:
                            action_box.label(text="No actions in file", icon='INFO')
                            action_box.operator("bakeaddon.export_refresh_actions", text="Refresh List", icon='FILE_REFRESH')
                    except Exception as e:
                        print(f"Bake Addon Error drawing action list: {e}")
                        import traceback
                        traceback.print_exc()
                        anim_col.label(text=f"Error: {str(e)}", icon='ERROR')
                # --- End Selective Actions ---

                row = anim_col.row(align=True)
                row.label(text="")
                row.prop(export_settings, "export_anim_force_start_end_keying", text="Force Start/End Keying")

                row = anim_col.row(align=True)
                row.label(text=" Sampling Rate")
                row.prop(export_settings, "export_anim_sampling_rate", text="")

                row = anim_col.row(align=True)
                row.label(text=" Simplify")
                row.prop(export_settings, "export_anim_simplify", text="")

            if export_settings.export_format == "OBJ":
                row = box.row(align=True)
                row.label(text=" Split by Mat")
                row.prop(export_settings, "obj_separate_by_materials")

                row = box.row(align=True)
                row.label(text=" Smooth Groups")
                row.prop(export_settings, "obj_export_smooth_groups")

            if export_settings.export_format in {"FBX", "OBJ"}:
                row = box.row(align=True)
                row.label(text="Custom Scale")
                row.prop(export_settings, "use_custom_export_scale")
                if export_settings.use_custom_export_scale:
                    row = box.row(align=True)
                    row.prop(export_settings, "custom_export_scale_value", text="Scale")
                row = box.row(align=True)
                row.label(text="Custom Axes")
                row.prop(export_settings, "use_custom_export_axes")
                if export_settings.use_custom_export_axes:
                    row = box.row(align=True)
                    row.label(text=" Forward")
                    row.prop(export_settings, "custom_export_forward_axis", expand=False)
                    row = box.row(align=True)
                    row.label(text=" Up")
                    row.prop(export_settings, "custom_export_up_axis", expand=False)

            if export_settings.export_format == "GLTF":
                row = box.row(align=True)
                row.label(text=" Images")
                row.prop(export_settings, "gltf_export_image_format")

                row = box.row(align=True)
                row.label(text=" Deform Bones")
                row.prop(export_settings, "export_only_deform_bones")

                row = box.row(align=True)
                row.label(text=" Custom Props")
                row.prop(export_settings, "export_custom_props")

                row = box.row(align=True)
                row.label(text=" Tangents")
                row.prop(export_settings, "export_tangent_space")

                row = box.row(align=True)
                row.label(text=" Attributes")
                row.prop(export_settings, "gltf_export_attributes")

        box = layout.box()
        row = box.row()
        row.prop(export_settings, "custom_export_path", text="Custom Folder")
        if export_settings.custom_export_path:
            row = box.row(align=True)
            row.label(text="Folder:")
            row.prop(export_settings, "export_path")

        row = layout.row()
        row.operator(BAKEADDON_OT_origin_z_minimum.bl_idname, text="Origin to Floor")

        row = layout.row()
        if export_settings.export_format == "FBX":
            row.operator(BAKEADDON_OT_export_model.bl_idname, text="Export")
        if export_settings.export_format in {"OBJ", "GLTF"}:
            row.operator(BAKEADDON_OT_export_model.bl_idname, text="Export")

        if len(export_settings.export_dir) > 0:
            row = layout.row()
            row.operator(BAKEADDON_OT_open_export_dir.bl_idname, text="Open Folder")


class OBJECT_PT_smart_apply_export_bakeaddon(Panel):
    bl_label = "Smart Apply"
    bl_idname = "OBJECT_PT_smart_apply_export_bakeaddon"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = OBJECT_PT_export_bakeaddon.bl_idname
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        if not hasattr(context.scene, "bakeaddon_smart_apply_props"):
            layout.label(text="Initializing...", icon='INFO')
            return

        props = context.scene.bakeaddon_smart_apply_props

        col = layout.column(align=True)
        col.label(text="Apply:")
        col.prop(props, "apply_location")
        col.prop(props, "apply_rotation")
        col.prop(props, "apply_scale")

        layout.separator()
        layout.prop(props, "compensate_modifiers", icon='MOD_ARRAY')

        layout.separator()
        layout.operator(BAKEADDON_OT_smart_apply_transforms.bl_idname, text="Apply Transforms", icon='CHECKMARK')


# --- Vertex Color Tools Panel ---
class VIEW3D_PT_vertex_color_ops_tools_bakeaddon(Panel):
    """Creates a Panel in the Bake Addon 2.1 Tab for the Vertex Color Ops Tools"""
    bl_label = "Vertex Color Tools"
    bl_idname = "VIEW3D_PT_vertex_color_ops_tools_bakeaddon" # Unique ID
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "OBJECT_PT_modeling_bakeaddon" # Parent is the Modeling panel
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Use a unique property name to avoid potential conflicts if user had old addon
        if not hasattr(scene, 'bakeaddon_vcolor_ops_settings'):
             layout.label(text="Initializing...", icon='INFO')
             return

        ops_settings = scene.bakeaddon_vcolor_ops_settings
        obj = context.active_object

        is_edit_mesh = (obj and obj.type == 'MESH' and hasattr(obj, 'data') and obj.data is not None and context.mode == 'EDIT_MESH')

        # Use an overall box for consistency with other Bake Addon 2.1 panels
        box = layout.box()
        col = box.column(align=True)

        if not is_edit_mesh:
            col.label(text="Use in Edit Mode", icon='INFO')
            col.enabled = False
        else:
            col.enabled = True

        row1 = col.row(align=True)
        row1.prop(ops_settings, "custom_color", text="")
        # Use the existing operator IDs
        row1.operator(OBJECT_OT_set_custom_vertex_color_ops.bl_idname,
                     text="Apply", icon='BRUSH_DATA')

        row2 = col.row(align=True)
        row2.operator(OBJECT_OT_set_random_vertex_color_ops.bl_idname,
                      text="Random", icon='RNDCURVE')
        row2.operator(OBJECT_OT_select_by_vertex_color.bl_idname,
                      text="Select by Color", icon='RESTRICT_SELECT_OFF')


# --- LOD Generator Panel ---
# [ ... keep existing LOD_PT_generator ... ]
class LOD_PT_generator(Panel):
    bl_label = "LOD Generator"
    bl_idname = "LOD_PT_generator_bakeaddon" # Unique ID
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "OBJECT_PT_modeling_bakeaddon" # Parent is the Modeling panel
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Large header icon (template_icon is the reliable way to scale custom icons)
        row_header = layout.row(align=True)
        lod_icon_id = get_icon_id("lod")
        row_header.template_icon(lod_icon_id, scale=3.0)
        text_col = row_header.column(align=True)
        text_col.label(text="")
        text_col.label(text="LOD Generator")
        text_col.label(text="")

        col = layout.column(align=True)

        if hasattr(scene, "lod_generator_levels"):
            col.prop(scene, "lod_generator_levels")
            col.prop(scene, "lod_mode", expand=True)
            col.separator()
            # Add sliders/inputs for each LOD level based on mode
            if scene.lod_mode == 'RATIO':
                for i in range(1, scene.lod_generator_levels + 1):
                    col.prop(scene, f"lod_ratio_{i}", slider=True)
            else:  # TARGET_TRIS
                for i in range(1, scene.lod_generator_levels + 1):
                    col.prop(scene, f"lod_tris_{i}")
            col.separator()
            # Generate LODs button (no icon)
            row_btn = col.row(align=True)
            row_btn.scale_y = 2.25
            row_btn.operator(OBJECT_OT_generate_lods.bl_idname, text="Generate LODs")
        else:
            col.label(text="LOD Property Error!", icon='ERROR')


# --- Contact Panel ---
# [ ... keep existing CONTACT_PT_bakeaddon ... ]
class CONTACT_PT_bakeaddon(Panel):
    bl_label = "Contact"
    bl_idname = "CONTACT_PT_bakeaddon"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = BakeAddonPanel.bl_idname
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)

        op_yt = col.operator("wm.url_open", text="YouTube", icon='URL')
        op_yt.url = ""

        op_x = col.operator("wm.url_open", text="X (Twitter)", icon='URL')
        op_x.url = ""

        col.separator()

        col.label(text="Contact Email:")
        row = col.row(align=True)
        row.label(text="")
        row.operator(BAKEADDON_OT_copy_email.bl_idname, text="", icon='COPYDOWN')



# --- Auto UV Panel ---
class AUTO_UV_PT_panel(Panel):
    """Creates a Panel in the Bake Addon 2.1 Tab for the Auto UV Tools"""
    bl_label = "Auto UV Tools"
    bl_idname = "VIEW3D_PT_auto_uv_tools_bakeaddon"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = BakeAddonPanel.bl_idname
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        obj = context.object
        selected_objects = context.selected_objects
        active_obj = context.active_object
        mode = context.mode
        is_object_mode = (mode == 'OBJECT')
        is_edit_mode = (mode == 'EDIT_MESH')
        is_active_mesh = bool(active_obj and active_obj.type == 'MESH')
        selected_meshes = [o for o in selected_objects if o.type == 'MESH']
        are_selected_meshes = bool(selected_meshes)
        
        # Check if addon is registered before accessing preferences
        addon = context.preferences.addons.get(__package__)
        if addon is None:
            return
        prefs = addon.preferences

        # MinistryOfFlat zip file settings
        box = layout.box()
        box.label(text="MinistryOfFlat Settings")
        col = box.column(align=True)
        zip_path = bpy.path.abspath(prefs.executable_path) if prefs.executable_path else ""
        if not prefs.executable_path or not os.path.exists(zip_path):
            col.label(text="MinistryOfFlat zip file not set", icon='ERROR')
            col.label(text="Please download the MinistryOfFlat zip file from the official site.", icon='INFO')
            row = col.row(align=True)
            row.prop(prefs, "executable_path", text="Executable Zip Path:")
            row.operator("wm.clearmofzippath", text="", icon='X')
            col.separator()
            col.operator("wm.downloadmofzip", text="Download MinistryOfFlat", icon='IMPORT')
        else:
            row = col.row(align=True)
            row.prop(prefs, "executable_path", text="Executable Zip Path:")
            row.operator("wm.clearmofzippath", text="", icon='X')
            col.separator()
            col.operator("wm.downloadmofzip", text="Download MinistryOfFlat", icon='IMPORT')

        if os.name != "nt":
            col.label(text="UV Unwrapping is only available on Windows", icon='ERROR')
            return

        # Main Auto UV Operator
        box = layout.box()
        auto_uv_icon_id = get_icon_id("AutoUnwrap")
        # Large icon on top (same scale as LOD icon)
        row_icon = box.row(align=True)
        row_icon.template_icon(auto_uv_icon_id, scale=3.0)
        text_col = row_icon.column(align=True)
        text_col.label(text="")
        text_col.label(text="Auto UV Unwrap")
        text_col.label(text="")
        col = box.column(align=True)
        if not is_object_mode:
            col.label(text="Switch to Object Mode", icon='INFO')
        elif not are_selected_meshes:
            col.label(text="Select mesh object(s)", icon='INFO')
        col.operator(AutoUVOperator.bl_idname, text="Auto UV Unwrap")

        # UV Tools Box
        box = layout.box()
        box.label(text="UV Tools")
        col = box.column(align=True)
        if not is_edit_mode:
            col.label(text="Switch to Edit Mode", icon='INFO')
        elif not is_active_mesh:
            col.label(text="Select a mesh object", icon='INFO')
        else:
            # Pack section
            pack_box = box.box()
            pack_box.label(text="Pack", icon='PACKAGE')
            pack_col = pack_box.column(align=True)
            pack_col.operator(AverageIslandsScaleOperator.bl_idname, icon='UV_SYNC_SELECT')
            pack_col.operator(PackUVOperator.bl_idname, icon='UV_SYNC_SELECT')

            # Other UV tools
            col.operator(MOFSeamsOperator.bl_idname, icon='UV_SYNC_SELECT')
            col.operator(UnWrapOperator.bl_idname, icon='UV_SYNC_SELECT')
            col.operator(SelectStretchingModalOperator.bl_idname, icon='UV_SYNC_SELECT')

        draw_mof_auto_uv_settings(layout, context)


# --- Bake Explode Panel ---
class BAKE_EXPLODE_PT_panel(Panel):
    bl_label = "Bake Explode"
    bl_idname = "BAKE_EXPLODE_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = BakeAddonPanel.bl_idname
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        be = context.scene.bake_explode_settings

        # Transform Storage Section
        layout.label(text="Transform Storage", icon='MOD_EXPLODE')
        row = layout.row(align=True)
        row.operator(BAKE_EXPLODE_OT_Store.bl_idname, text="Store", icon='PACKAGE')
        viz_icon = 'HIDE_OFF' if be.visualization_enabled else 'HIDE_ON'
        row.operator(BAKE_EXPLODE_OT_ToggleVisualization.bl_idname, text="", icon=viz_icon)
        row = layout.row(align=True)
        row.operator(BAKE_EXPLODE_OT_Reset.bl_idname, text="Reset", icon='LOOP_BACK')
        row.operator(BAKE_EXPLODE_OT_Clear.bl_idname, text="", icon='TRASH')

        layout.separator()

        # Linked Duplicate Section
        layout.label(text="Linked Duplicates", icon='LINKED')
        split = layout.split(factor=0.5, align=True)
        col_left = split.column(align=True)
        col_right = split.column(align=True)
        col_left.operator(BAKE_EXPLODE_OT_HideLinkedDuplicates.bl_idname, text="Hide Same Data", icon='HIDE_ON')
        col_right.operator(BAKE_EXPLODE_OT_UnhideLinkedDuplicates.bl_idname, text="Unhide All", icon='HIDE_OFF')

        layout.separator()

        # Stored Objects List
        layout.prop(be, "show_stored_objects", text="Stored Objects", icon='DISCLOSURE_TRI_DOWN' if be.show_stored_objects else 'DISCLOSURE_TRI_RIGHT')
        if be.show_stored_objects:
            # Scan all scene objects for Bake Explode data
            stored_objects = []
            for obj in bpy.data.objects:
                if obj.type == 'MESH' and has_bake_explode_data(obj):
                    stored_objects.append(obj)

            if stored_objects:
                for i, obj in enumerate(sorted(stored_objects, key=lambda o: o.name), 1):
                    original = get_stored_transform(obj, ORIGINAL_TRANSFORM_PROP)
                    current = get_object_matrix_values(obj)

                    # Check if object has moved from original position
                    if original:
                        changed = not transforms_match(current, original)
                        if changed:
                            layout.label(text=f"{i}. {obj.name}", icon='MOD_EXPLODE')
                        else:
                            layout.label(text=f"{i}. {obj.name}")
                    else:
                        layout.label(text=f"{i}. {obj.name}")
            else:
                layout.label(text="No stored transforms", icon='BLANK1')


def _opposite_quad_edge(face, edge):
    if len(face.verts) != 4:
        return None
    edge_verts = set(edge.verts)
    for candidate in face.edges:
        if candidate == edge:
            continue
        if not edge_verts.intersection(candidate.verts):
            return candidate
    return None


class MESH_OT_bakeaddon_bevel_select(Operator):
    bl_idname = "mesh.bakeaddon_bevel_select"
    bl_label = "Bevel Select"
    bl_description = "Select an edge ring for UnBevel, with a Blender 5.1-safe fallback"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.mode == 'EDIT_MESH' and context.edit_object and context.edit_object.type == 'MESH')

    def execute(self, context):
        obj = context.edit_object
        try:
            bm = bmesh.from_edit_mesh(obj.data)
        except Exception as exc:
            self.report({'ERROR'}, f"Could not read edit mesh: {exc}")
            return {'CANCELLED'}

        selected_edges = [edge for edge in bm.edges if edge.select and edge.is_valid]
        if not selected_edges:
            self.report({'WARNING'}, "Select at least one edge first.")
            return {'CANCELLED'}

        try:
            context.tool_settings.mesh_select_mode = (False, True, False)
        except Exception:
            pass

        visited = set(selected_edges)
        queue = list(selected_edges)
        selected_count = 0

        while queue:
            edge = queue.pop(0)
            if not edge.is_valid:
                continue
            edge.select_set(True)
            selected_count += 1
            for face in edge.link_faces:
                opposite = _opposite_quad_edge(face, edge)
                if opposite and opposite.is_valid and opposite not in visited:
                    visited.add(opposite)
                    queue.append(opposite)

        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        self.report({'INFO'}, f"Bevel Select: selected {selected_count} ring edges.")
        return {'FINISHED'}


# --- Modeling Panel ---
class MODELING_PT_panel(Panel):
    """Creates a Panel in the Bake Addon 2.1 Tab for Modeling Tools"""
    bl_label = "Modeling"
    bl_idname = "OBJECT_PT_modeling_bakeaddon"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = BakeAddonPanel.bl_idname
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        obj = context.object
        selected_objects = context.selected_objects
        active_obj = context.active_object
        mode = context.mode
        is_object_mode = (mode == 'OBJECT')
        is_edit_mode = (mode == 'EDIT_MESH')
        is_active_mesh = bool(active_obj and active_obj.type == 'MESH')
        selected_meshes = [o for o in selected_objects if o.type == 'MESH']
        are_selected_meshes = bool(selected_meshes)
        scene = context.scene # Get scene for properties

        grid_row_top = layout.row(align=True)
        grid_col_unbevel = grid_row_top.column(align=True)
        grid_col_offset = grid_row_top.column(align=True)
        grid_row_bottom = layout.row(align=True)
        grid_col_select = grid_row_bottom.column(align=True)
        grid_col_cleanup = grid_row_bottom.column(align=True)

        # --- UnBevel Tools Section (EDIT MODE ONLY) ---
        box_unbevel = grid_col_unbevel.box()
        unbevel_icon_id = get_icon_id("UnBevel")
        col_unbevel = box_unbevel.column()
        col_unbevel.enabled = is_edit_mode # Enable box only in Edit Mode

        if not is_edit_mode:
            col_unbevel.label(text="Switch to Edit Mode", icon='INFO')
        else:
            # Replicate layout from original panel inside this 'else'
            col_unbevel_inner = col_unbevel.column(align=True) # Inner column for alignment

            row_bevel_select = col_unbevel_inner.row(align=True)
            row_bevel_select.operator(MESH_OT_bakeaddon_bevel_select.bl_idname, text="Bevel Select")

            # Row for UnBevel and Edge Slide
            row_unbevel_ops = col_unbevel_inner.row(align=True) # Renamed row variable
            # Enable UnBevel button based on poll method (use imported operator)
            row_unbevel_ops.enabled = OBJECT_OT_unbevel_modal_op.poll(context)
            row_unbevel_ops.operator(OBJECT_OT_unbevel_modal_op.bl_idname, text="UnBevel", icon_value=unbevel_icon_id)
            # Access scene property directly
            row_unbevel_ops.prop(scene, "unbevel_edge_slide", text="", toggle=True, icon='IPO_EASE_IN_OUT')

        # --- Offset Section (EDIT MODE ONLY) ---
        box_offset = grid_col_offset.box()
        offset_icon_id = get_icon_id("Offset")
        col_offset = box_offset.column()
        col_offset.enabled = is_edit_mode
        if not is_edit_mode:
            col_offset.label(text="Switch to Edit Mode", icon='INFO')
        else:
            if not is_active_mesh:
                col_offset.label(text="Active object must be Mesh.", icon='ERROR')
            else:
                col_offset.operator(OBJECT_OT_bakeaddon_offset.bl_idname, text="Offset", icon_value=offset_icon_id)

        # --- Selection Section (EDIT MODE - Modified) ---
        box_select = grid_col_select.box()
        select_icon_id = get_icon_id("Select")
        col_select = box_select.column()
        col_select.enabled = is_edit_mode
        if not is_edit_mode:
            col_select.label(text="Switch to Edit Mode", icon='INFO')
        else:
            if not is_active_mesh:
                col_select.label(text="Active object must be Mesh.", icon='ERROR')
            else:
                # scene = context.scene # Already defined above
                if hasattr(scene, 'factor_select_props'):
                    props_factor = scene.factor_select_props
                    col_select_inner_factor = col_select.column(align=True)
                    col_select_inner_factor.operator(OBJECT_OT_select_factor_loop.bl_idname, text="Easy Loop", icon_value=get_icon_id("EasyLoop"))
                else: col_select.label(text="Factor Props Error!", icon='ERROR')

                if hasattr(scene, 'surface_select_props'):
                    props_surface = scene.surface_select_props
                    col_select_inner_surface = col_select.column(align=True)
                    col_select_inner_surface.operator(MESH_OT_surface_select.bl_idname, text="Select Surface", icon_value=get_icon_id("SelectSurface"))
                else: col_select.label(text="Surface Props Error!", icon='ERROR')

        # --- Boolean Cleanup Section (EDIT MODE - MODIFIED) ---
        box_cleanup = grid_col_cleanup.box()
        col_cleanup = box_cleanup.column()
        col_cleanup.enabled = is_edit_mode
        if not is_edit_mode:
            col_cleanup.label(text="Switch to Edit Mode", icon='INFO')
        else:
            if not is_active_mesh:
                col_cleanup.label(text="Active object must be Mesh.", icon='ERROR')
            else:
                col = col_cleanup.column(align=True)
                row_cleanup_op = col.row()
                row_cleanup_op.operator(OBJECT_OT_BooleanCleanup.bl_idname, text="Boolean Cleanup", icon_value=get_icon_id("BooleanCleanup"))
        layout.separator()


# --- Selective Actions UI Operators ---

class BAKEADDON_OT_export_toggle_selective_actions(Operator):
    bl_idname = "bakeaddon.export_toggle_selective_actions"
    bl_label = "Toggle Selective Actions"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        export_settings = context.scene.bakeaddon_export_settings
        export_settings.use_selective_actions = not export_settings.use_selective_actions
        if export_settings.use_selective_actions:
            export_settings.export_anim_all_actions = False
        from .properties import update_selective_actions
        update_selective_actions(export_settings, context)
        return {'FINISHED'}


class BAKEADDON_OT_export_refresh_actions(Operator):
    bl_idname = "bakeaddon.export_refresh_actions"
    bl_label = "Refresh Action List"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        from .properties import update_selective_actions
        update_selective_actions(context.scene.bakeaddon_export_settings, context)
        return {'FINISHED'}


class BAKEADDON_OT_export_select_all_actions(Operator):
    bl_idname = "bakeaddon.export_select_all_actions"
    bl_label = "Select All Actions"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        export_settings = context.scene.bakeaddon_export_settings
        for act in export_settings.filtered_actions:
            act.use_export = True
        return {'FINISHED'}


class BAKEADDON_OT_export_deselect_all_actions(Operator):
    bl_idname = "bakeaddon.export_deselect_all_actions"
    bl_label = "Deselect All Actions"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        export_settings = context.scene.bakeaddon_export_settings
        for act in export_settings.filtered_actions:
            act.use_export = False
        return {'FINISHED'}

# --- END OF FILE ui.py ---
