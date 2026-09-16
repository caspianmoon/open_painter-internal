import math
import os
import re
import time
import uuid
from dataclasses import dataclass

import bpy
import bmesh
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

from . import shared_state


OUTPUT_UV_NAME = "UVMap"
SOURCE_UV_NAME = "BakeAddon_SourceTileUV"
SOURCE_MATERIAL_ATTRIBUTE_NAME = "BakeAddon_SourceMaterialIndex"
BACKUP_COLLECTION_NAME = "BeforeTrimBackup"
BACKUP_SUFFIX = "_BeforeTrimBackup"
GENERATED_MESH_SUFFIX = "_BakeAddonTrim"

TRIM_PRIORITY_MIN_SHARE = 1.0 / 5.0
TRIM_PRIORITY_MAX_SHARE = 5.0
MAX_GENERATED_PIECES = 250_000

RUN_TAG = "bakeaddon_trim_run_id"
SOURCE_ID_TAG = "bakeaddon_trim_source_id"
BACKUP_TAG = "bakeaddon_trim_is_backup"
GENERATED_TAG = "bakeaddon_trim_is_generated"
BACKUP_OBJECT_TAG = "bakeaddon_trim_backup_object"
LEGACY_MANUAL_TRIM_PRIORITY_TAG = "bakeaddon_trim_manual_priority"
MANUAL_TRIM_PRIORITY_TAG = "bakeaddon_trim_priority_value"
MANUAL_TRIM_PRIORITY_PROPERTY = "bakeaddon_trim_priority"
MANUAL_TRIM_PRIORITY_MIN = -5.0
MANUAL_TRIM_PRIORITY_MAX = 5.0
MANUAL_TRIM_PRIORITY_PRECISION = 1

MAP_BASE_COLOR = "BASE_COLOR"
MAP_ROUGHNESS = "ROUGHNESS"
MAP_NORMAL = "NORMAL"
MAP_METALLIC = "METALLIC"
MAP_AO = "AO"
MAP_DISPLACEMENT = "DISPLACEMENT"
MAP_PUREPLAY_ORD = "PUREPLAY_ORD"
MAP_OPACITY = "OPACITY"

EXPORT_PRESET_CUSTOM = "CUSTOM"
EXPORT_PRESET_PUREPLAY_ORD = "PUREPLAY_ORD"

MAP_DEFS = {
    MAP_BASE_COLOR: {
        "label": "Base Color",
        "suffix": "BaseColor",
        "colorspace": "sRGB",
        "default": (0.8, 0.8, 0.8, 1.0),
    },
    MAP_ROUGHNESS: {
        "label": "Roughness",
        "suffix": "Roughness",
        "colorspace": "Non-Color",
        "default": (0.5, 0.5, 0.5, 1.0),
    },
    MAP_NORMAL: {
        "label": "Normal",
        "suffix": "Normal",
        "colorspace": "Non-Color",
        "default": (0.5, 0.5, 1.0, 1.0),
    },
    MAP_METALLIC: {
        "label": "Metallic",
        "suffix": "Metallic",
        "colorspace": "Non-Color",
        "default": (0.0, 0.0, 0.0, 1.0),
    },
    MAP_AO: {
        "label": "AO",
        "suffix": "AO",
        "colorspace": "Non-Color",
        "default": (1.0, 1.0, 1.0, 1.0),
    },
    MAP_DISPLACEMENT: {
        "label": "Displacement",
        "suffix": "Displacement",
        "colorspace": "Non-Color",
        "default": (0.0, 0.0, 0.0, 1.0),
    },
    MAP_PUREPLAY_ORD: {
        "label": "PurePlay ORD",
        "suffix": "OcclusionRoughnessDisplacement",
        "colorspace": "Non-Color",
        "default": (1.0, 0.5, 0.0, 1.0),
    },
    MAP_OPACITY: {
        "label": "Opacity",
        "suffix": "Opacity",
        "colorspace": "Non-Color",
        "default": (1.0, 1.0, 1.0, 1.0),
    },
}


@dataclass
class MaterialInfo:
    material: bpy.types.Material
    images: dict
    color: tuple
    label: str
    base_color_aspect: float = 1.0
    has_transparency: bool = False
    warnings: tuple = ()


@dataclass
class AtlasSection:
    material_name: str
    x: int
    y: int
    w: int
    h: int
    u0: float
    v0: float
    u1: float
    v1: float


@dataclass
class MeshBuildResult:
    mesh: bpy.types.Mesh
    trim_uv_name: str
    processed_faces: int
    created_pieces: int
    skipped_faces: int
    topology_changed: bool
    vertex_group_weights: dict


@dataclass
class TrimLayoutResult:
    sections: dict
    effective_padding: int | None
    used_weighted_layout: bool
    warnings: tuple


_TRIM_PREVIEW_DEBOUNCE_SECONDS = 0.12
_TRIM_PREVIEW_PENDING = {}
_TRIM_PREVIEW_ANALYSIS_CACHE = {}
_TRIM_PREVIEW_TIMER_REGISTERED = False
_TRIM_PREVIEW_RUNTIME_CLEARING = False
_TRIM_PREVIEW_OVERLAY_STATES = {}


def trim_preview_image_editor_spaces(context):
    spaces = []
    seen = set()
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None)
    if screen is not None:
        for area in screen.areas:
            if area.type != "IMAGE_EDITOR":
                continue
            space = area.spaces.active
            if getattr(space, "type", None) != "IMAGE_EDITOR":
                continue
            pointer = space.as_pointer()
            if pointer in seen:
                continue
            seen.add(pointer)
            spaces.append(space)

    space = getattr(context, "space_data", None)
    if (
        getattr(space, "type", None) == "IMAGE_EDITOR"
        and space.as_pointer() not in seen
    ):
        spaces.append(space)
    return spaces


def hide_trim_preview_overlays(context):
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    scene_pointer = scene.as_pointer()
    for space in trim_preview_image_editor_spaces(context):
        overlay = getattr(space, "overlay", None)
        if overlay is None or not hasattr(overlay, "show_overlays"):
            continue
        space_pointer = space.as_pointer()
        state = _TRIM_PREVIEW_OVERLAY_STATES.get(space_pointer)
        if state is None:
            state = {
                "space": space,
                "show_overlays": bool(overlay.show_overlays),
                "scene_owners": set(),
            }
            _TRIM_PREVIEW_OVERLAY_STATES[space_pointer] = state
        state["scene_owners"].add(scene_pointer)
        overlay.show_overlays = False


def restore_trim_preview_overlays(scene=None, *, force=False):
    scene_pointer = scene.as_pointer() if scene is not None else None
    for space_pointer, state in list(
        _TRIM_PREVIEW_OVERLAY_STATES.items()
    ):
        owners = state["scene_owners"]
        if force:
            owners.clear()
        elif scene_pointer is not None:
            owners.discard(scene_pointer)
        if owners:
            continue
        try:
            overlay = state["space"].overlay
            overlay.show_overlays = state["show_overlays"]
        except (AttributeError, ReferenceError, RuntimeError):
            pass
        _TRIM_PREVIEW_OVERLAY_STATES.pop(space_pointer, None)


def tag_trim_preview_image_editors():
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return
    for window in window_manager.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == "IMAGE_EDITOR":
                area.tag_redraw()


def trim_preview_selection_signature(context):
    selected = tuple(
        sorted(
            obj.as_pointer()
            for obj in getattr(context, "selected_objects", ())
            if obj.type == "MESH"
        )
    )
    active = getattr(context, "active_object", None)
    active_pointer = (
        active.as_pointer()
        if active is not None and active.type == "MESH"
        else 0
    )
    return selected, active_pointer


def trim_preview_layout_signature(settings):
    return (
        str(settings.atlas_size),
        int(settings.padding_px),
        str(settings.layout_mode),
    )


def trim_preview_entry(context):
    key = shared_state.trim_layout_preview_context_key(context)
    if key is None:
        return None
    return shared_state.trim_layout_preview_cache.get(key)


def _unregister_trim_preview_timer_if_idle():
    global _TRIM_PREVIEW_TIMER_REGISTERED
    if _TRIM_PREVIEW_PENDING or not _TRIM_PREVIEW_TIMER_REGISTERED:
        return
    try:
        if bpy.app.timers.is_registered(_process_trim_preview_requests):
            bpy.app.timers.unregister(_process_trim_preview_requests)
    except (RuntimeError, ValueError):
        pass
    _TRIM_PREVIEW_TIMER_REGISTERED = False


def clear_trim_layout_preview_scene(context):
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    restore_trim_preview_overlays(scene)
    scene_pointer = scene.as_pointer()
    keys = {
        key
        for key in (
            set(_TRIM_PREVIEW_PENDING)
            | set(_TRIM_PREVIEW_ANALYSIS_CACHE)
            | set(shared_state.trim_layout_preview_cache)
        )
        if key[1] == scene_pointer
    }
    for key in keys:
        _TRIM_PREVIEW_PENDING.pop(key, None)
        _TRIM_PREVIEW_ANALYSIS_CACHE.pop(key, None)
        shared_state.trim_layout_preview_cache.pop(key, None)
    _unregister_trim_preview_timer_if_idle()
    tag_trim_preview_image_editors()


def schedule_trim_layout_preview(
    context,
    *,
    invalidate_analysis=False,
    immediate=False,
):
    global _TRIM_PREVIEW_TIMER_REGISTERED
    if context is None or _TRIM_PREVIEW_RUNTIME_CLEARING:
        return
    scene = getattr(context, "scene", None)
    settings = getattr(scene, "bakeaddon_texture_to_trim_settings", None)
    if settings is None or not settings.layout_preview_enabled:
        return
    key = shared_state.trim_layout_preview_context_key(context)
    if key is None:
        return

    signature = trim_preview_selection_signature(context)
    layout_signature = trim_preview_layout_signature(settings)
    existing_request = _TRIM_PREVIEW_PENDING.get(key)
    invalidate_analysis = bool(
        invalidate_analysis
        or (
            existing_request
            and existing_request.get("invalidate_analysis", False)
        )
    )
    deadline = (
        time.monotonic()
        if immediate
        else time.monotonic() + _TRIM_PREVIEW_DEBOUNCE_SECONDS
    )
    _TRIM_PREVIEW_PENDING[key] = {
        "key": key,
        "deadline": deadline,
        "invalidate_analysis": invalidate_analysis,
        "selection_signature": signature,
        "layout_signature": layout_signature,
    }

    previous = shared_state.trim_layout_preview_cache.get(key, {})
    shared_state.trim_layout_preview_cache[key] = {
        "status": "PENDING",
        "selection_signature": signature,
        "layout_signature": layout_signature,
        "sections": (),
        "warning": "",
        "highlight_material_names": previous.get(
            "highlight_material_names",
            (),
        ),
    }
    tag_trim_preview_image_editors()

    if not _TRIM_PREVIEW_TIMER_REGISTERED:
        bpy.app.timers.register(
            _process_trim_preview_requests,
            first_interval=0.0 if immediate else _TRIM_PREVIEW_DEBOUNCE_SECONDS,
        )
        _TRIM_PREVIEW_TIMER_REGISTERED = True


def _preview_request_context(request):
    window_pointer, scene_pointer, view_layer_pointer = request["key"]
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_pointer and window_manager is not None:
        window = next(
            (
                candidate
                for candidate in window_manager.windows
                if candidate.as_pointer() == window_pointer
            ),
            None,
        )
        if window is None:
            return None
        scene = window.scene
        view_layer = window.view_layer
        if (
            scene.as_pointer() != scene_pointer
            or view_layer.as_pointer() != view_layer_pointer
        ):
            return None
        return {
            "window": window,
            "scene": scene,
            "view_layer": view_layer,
        }

    scene = next(
        (
            candidate
            for candidate in bpy.data.scenes
            if candidate.as_pointer() == scene_pointer
        ),
        None,
    )
    if scene is None:
        return None
    view_layer = next(
        (
            candidate
            for candidate in scene.view_layers
            if candidate.as_pointer() == view_layer_pointer
        ),
        None,
    )
    if view_layer is None:
        return None
    return {"scene": scene, "view_layer": view_layer}


def _process_trim_preview_requests():
    global _TRIM_PREVIEW_TIMER_REGISTERED
    if not _TRIM_PREVIEW_PENDING:
        _TRIM_PREVIEW_TIMER_REGISTERED = False
        return None

    now = time.monotonic()
    ready_keys = [
        key
        for key, request in _TRIM_PREVIEW_PENDING.items()
        if request["deadline"] <= now
    ]
    if not ready_keys:
        next_deadline = min(
            request["deadline"]
            for request in _TRIM_PREVIEW_PENDING.values()
        )
        return max(0.01, next_deadline - now)

    for key in ready_keys:
        request = _TRIM_PREVIEW_PENDING.pop(key, None)
        if request is None:
            continue
        override = _preview_request_context(request)
        if override is None:
            _TRIM_PREVIEW_ANALYSIS_CACHE.pop(key, None)
            shared_state.trim_layout_preview_cache.pop(key, None)
            continue
        try:
            with bpy.context.temp_override(**override):
                _build_trim_layout_preview(bpy.context, request)
        except Exception as exc:
            shared_state.trim_layout_preview_cache[key] = {
                "status": "ERROR",
                "selection_signature": request[
                    "selection_signature"
                ],
                "layout_signature": request[
                    "layout_signature"
                ],
                "sections": (),
                "warning": str(exc),
                "highlight_material_names": (),
            }
        tag_trim_preview_image_editors()

    if not _TRIM_PREVIEW_PENDING:
        _TRIM_PREVIEW_TIMER_REGISTERED = False
        return None
    next_deadline = min(
        request["deadline"]
        for request in _TRIM_PREVIEW_PENDING.values()
    )
    return max(0.01, next_deadline - time.monotonic())


def update_trim_layout_preview_toggle(self, context):
    if _TRIM_PREVIEW_RUNTIME_CLEARING:
        return
    if self.layout_preview_enabled:
        hide_trim_preview_overlays(context)
        schedule_trim_layout_preview(
            context,
            invalidate_analysis=True,
            immediate=True,
        )
    else:
        clear_trim_layout_preview_scene(context)


def update_trim_layout_preview_layout(_self, context):
    schedule_trim_layout_preview(context, invalidate_analysis=False)


def clear_all_trim_layout_preview_state(disable_scenes=True):
    global _TRIM_PREVIEW_RUNTIME_CLEARING
    global _TRIM_PREVIEW_TIMER_REGISTERED
    _TRIM_PREVIEW_RUNTIME_CLEARING = True
    try:
        if _TRIM_PREVIEW_TIMER_REGISTERED:
            try:
                if bpy.app.timers.is_registered(
                    _process_trim_preview_requests
                ):
                    bpy.app.timers.unregister(
                        _process_trim_preview_requests
                    )
            except (RuntimeError, ValueError):
                pass
        _TRIM_PREVIEW_TIMER_REGISTERED = False
        _TRIM_PREVIEW_PENDING.clear()
        _TRIM_PREVIEW_ANALYSIS_CACHE.clear()
        shared_state.trim_layout_preview_cache.clear()
        restore_trim_preview_overlays(force=True)
        if disable_scenes:
            for scene in bpy.data.scenes:
                settings = getattr(
                    scene,
                    "bakeaddon_texture_to_trim_settings",
                    None,
                )
                if settings is not None:
                    settings.layout_preview_enabled = False
    finally:
        _TRIM_PREVIEW_RUNTIME_CLEARING = False
    tag_trim_preview_image_editors()


@persistent
def clear_trim_layout_preview_on_load(_unused):
    clear_all_trim_layout_preview_state(disable_scenes=True)


def register_trim_layout_preview_runtime():
    if clear_trim_layout_preview_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(
            clear_trim_layout_preview_on_load
        )
    clear_all_trim_layout_preview_state(disable_scenes=True)


def unregister_trim_layout_preview_runtime():
    if clear_trim_layout_preview_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(
            clear_trim_layout_preview_on_load
        )
    clear_all_trim_layout_preview_state(disable_scenes=True)


class BAKEADDON_PG_TextureToTrimSettings(PropertyGroup):
    atlas_size: EnumProperty(
        name="Trim Sheet Size",
        items=(
            ("512", "512", ""),
            ("1024", "1024", ""),
            ("2048", "2048", ""),
            ("4096", "4096", ""),
            ("8192", "8192", ""),
        ),
        default="2048",
        update=update_trim_layout_preview_layout,
    )
    layout_mode: EnumProperty(
        name="Layout Mode",
        items=(
            ("GRID", "Grid", ""),
            ("VERTICAL", "Vertical Rows", ""),
            ("HORIZONTAL", "Horizontal Rows", ""),
        ),
        default="GRID",
        update=update_trim_layout_preview_layout,
    )
    padding_px: IntProperty(
        name="Padding",
        default=8,
        min=0,
        max=512,
        update=update_trim_layout_preview_layout,
    )
    layout_preview_enabled: BoolProperty(
        name="PREVIEW",
        description=(
            "Show the packed trim layout before baking. Layout settings and "
            "the priority slider update automatically. Temporarily hides Image "
            "Editor overlays and restores their previous state when disabled"
        ),
        default=False,
        options={"SKIP_SAVE"},
        update=update_trim_layout_preview_toggle,
    )
    output_prefix: StringProperty(name="Output Prefix", default="Trim")
    export_dir: StringProperty(name="Export Folder", default="//", subtype="DIR_PATH")
    export_preset: EnumProperty(
        name="Export Preset",
        description="Choose the texture set and channel convention to export",
        items=(
            (
                EXPORT_PRESET_CUSTOM,
                "Custom",
                "Use the map checkboxes and selected normal convention",
            ),
            (
                EXPORT_PRESET_PUREPLAY_ORD,
                "PurePlay ORD",
                (
                    "Export BaseColor, Metallic, DirectX Normal, and packed "
                    "Occlusion/Roughness/Displacement"
                ),
            ),
        ),
        default=EXPORT_PRESET_CUSTOM,
    )

    bake_base_color: BoolProperty(name="Base Color", default=True)
    bake_roughness: BoolProperty(name="Roughness", default=True)
    bake_normal: BoolProperty(name="Normal", default=True)
    bake_metallic: BoolProperty(name="Metallic", default=True)
    bake_ao: BoolProperty(name="AO", default=False)
    pureplay_bake_ao: BoolProperty(
        name="AO Map",
        description=(
            "Bake AO into the red channel of PurePlay ORD. "
            "When disabled, the red channel is filled with white"
        ),
        default=True,
    )
    normal_map_format: EnumProperty(
        name="Normal Format",
        description="Normal map green channel convention. DirectX exports the normal map with inverted green channel",
        items=(
            ("OPENGL", "OpenGL", "Use OpenGL normal maps with positive green/Y"),
            ("DIRECTX", "DirectX", "Use DirectX normal maps with inverted green/Y"),
        ),
        default="OPENGL",
    )

    cycles_samples: IntProperty(name="Samples", default=32, min=1, soft_max=4096)
    cycles_margin_px: IntProperty(name="Bake Margin", default=4, min=0, max=256)
    cycles_use_gpu: BoolProperty(name="Use GPU", default=True)

    triangulate_ngons: BoolProperty(
        name="Triangulate",
        description="Triangulate only polygons that must be split across UV tile boundaries",
        default=True,
    )
    uv_epsilon: FloatProperty(
        name="UV Tile Border Tolerance",
        description="Bigger number = Less smaller/closer cuts. Increase number if you want cuts not to be closer to each other",
        default=0.0001,
        min=0.00000001,
        max=0.25,
        precision=6,
    )
    max_tiles_per_axis: IntProperty(name="Max Tiles Per Axis", default=32, min=1, max=512)
    min_piece_area: FloatProperty(
        name="Minimum Piece Area",
        default=0.0000000001,
        min=0.0,
        precision=10,
        options={"HIDDEN"},
    )
    create_backup: BoolProperty(name="Create Hidden Backup", default=True)
    merge_vertices_translate: BoolProperty(
        name="Merge vertices",
        description=(
            "Reuse compatible cut vertices from the same source topology "
            "without welding unrelated overlapping shells"
        ),
        default=True,
    )
    apply_generated_material: BoolProperty(
        name="Apply Generated Trim Material",
        default=True,
        options={"HIDDEN"},
    )
    show_in_uv_editor: BoolProperty(
        name="Show in UV Editor",
        default=True,
        options={"HIDDEN"},
    )


def enabled_map_keys(settings):
    if settings.export_preset == EXPORT_PRESET_PUREPLAY_ORD:
        return [
            MAP_BASE_COLOR,
            MAP_METALLIC,
            MAP_NORMAL,
            MAP_PUREPLAY_ORD,
        ]
    keys = []
    if settings.bake_base_color:
        keys.append(MAP_BASE_COLOR)
    if settings.bake_roughness:
        keys.append(MAP_ROUGHNESS)
    if settings.bake_normal:
        keys.append(MAP_NORMAL)
    if settings.bake_metallic:
        keys.append(MAP_METALLIC)
    if settings.bake_ao:
        keys.append(MAP_AO)
    return keys


def expanded_bake_map_keys(map_keys):
    keys = []
    for map_key in map_keys:
        components = (
            (MAP_AO, MAP_ROUGHNESS, MAP_DISPLACEMENT)
            if map_key == MAP_PUREPLAY_ORD
            else (map_key,)
        )
        for component in components:
            if component not in keys:
                keys.append(component)
    return keys


def effective_normal_map_format(settings):
    if settings.export_preset == EXPORT_PRESET_PUREPLAY_ORD:
        return "DIRECTX"
    return settings.normal_map_format


def sanitize_output_prefix(
    value,
    fallback="Trim",
    strip_extension=True,
    strip_path=True,
):
    prefix = (value or fallback).strip() or fallback
    if strip_path:
        prefix = os.path.basename(prefix)
    if strip_extension:
        prefix = os.path.splitext(prefix)[0]
    prefix = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", prefix)
    prefix = prefix.strip(" .")
    return (prefix or fallback)[:128]


def output_prefix(settings):
    return sanitize_output_prefix(settings.output_prefix, "Trim")


def trim_material_name_for_object(context, objects):
    active = context.view_layer.objects.active if context and context.view_layer else None
    source = active if active in objects else (objects[0] if objects else None)
    return clean_trim_output_base_name(
        source.name if source else "Object"
    ).strip() or "Object"


def trim_output_prefix_for_object(context, objects):
    return sanitize_output_prefix(
        trim_material_name_for_object(context, objects),
        "Object",
        strip_extension=False,
        strip_path=False,
    )


def output_path_for_map(settings, map_key, prefix=None):
    abs_dir = bpy.path.abspath(settings.export_dir or "//")
    if not abs_dir:
        abs_dir = bpy.path.abspath("//")
    filename = f"{prefix or output_prefix(settings)}_{MAP_DEFS[map_key]['suffix']}.png"
    return os.path.join(abs_dir, filename)


def set_image_colorspace(image, colorspace):
    try:
        image.colorspace_settings.name = colorspace
    except Exception:
        pass


def create_filled_image(name, size, fill_color, colorspace, alpha=False):
    """Create a run-owned image without allocating an atlas-sized Python buffer."""
    image = bpy.data.images.new(
        name=name,
        width=size,
        height=size,
        alpha=bool(alpha),
        float_buffer=False,
    )
    image.file_format = "PNG"
    set_image_colorspace(image, colorspace)
    try:
        image.generated_type = "BLANK"
        image.generated_color = tuple(fill_color)
        image.alpha_mode = "STRAIGHT" if alpha else "NONE"
    except Exception:
        pass
    image.update()
    return image


def save_image(image, path, color_mode="RGB"):
    image.file_format = "PNG"
    image.filepath_raw = path
    scene = bpy.context.scene
    image_settings = scene.render.image_settings if scene else None
    old_format = getattr(image_settings, "file_format", None)
    old_color_mode = getattr(image_settings, "color_mode", None)
    try:
        if image_settings:
            image_settings.file_format = "PNG"
            image_settings.color_mode = color_mode
        image.save()
    finally:
        if image_settings:
            try:
                image_settings.file_format = old_format
                image_settings.color_mode = old_color_mode
            except Exception:
                pass
    return path


def material_display_color(material):
    if material:
        try:
            color = material.diffuse_color
            return (float(color[0]), float(color[1]), float(color[2]), float(color[3]))
        except Exception:
            pass
    return MAP_DEFS[MAP_BASE_COLOR]["default"]


def active_material_output(node_tree):
    if not node_tree:
        return None
    outputs = [
        node
        for node in node_tree.nodes
        if node.bl_idname == "ShaderNodeOutputMaterial"
    ]
    return next(
        (node for node in outputs if getattr(node, "is_active_output", False)),
        outputs[0] if outputs else None,
    )


def upstream_nodes(start_node):
    ordered = []
    stack = [start_node] if start_node else []
    visited = set()
    while stack:
        node = stack.pop()
        pointer = node.as_pointer()
        if pointer in visited:
            continue
        visited.add(pointer)
        ordered.append(node)
        for socket in getattr(node, "inputs", ()):
            for link in getattr(socket, "links", ()):
                stack.append(link.from_node)
    return ordered


def find_principled(material):
    """Prefer the Principled shader feeding the active Material Output."""
    if not material or not material.use_nodes or not material.node_tree:
        return None
    output = active_material_output(material.node_tree)
    if output:
        surface = output.inputs.get("Surface")
        if surface and surface.is_linked:
            for node in upstream_nodes(surface.links[0].from_node):
                if node.bl_idname == "ShaderNodeBsdfPrincipled":
                    return node
    for node in material.node_tree.nodes:
        if node.bl_idname == "ShaderNodeBsdfPrincipled":
            return node
    return None


def iter_node_trees(node_tree, visited=None):
    if node_tree is None:
        return
    if visited is None:
        visited = set()
    pointer = node_tree.as_pointer()
    if pointer in visited:
        return
    visited.add(pointer)
    yield node_tree
    for node in node_tree.nodes:
        child = getattr(node, "node_tree", None)
        if child:
            yield from iter_node_trees(child, visited)


def iter_nodes_recursive(material):
    if not material or not material.use_nodes or not material.node_tree:
        return
    for node_tree in iter_node_trees(material.node_tree):
        yield from node_tree.nodes


def socket_default_as_color(socket, fallback):
    if socket is None or not hasattr(socket, "default_value"):
        return fallback
    value = socket.default_value
    try:
        if isinstance(value, (float, int)):
            v = float(value)
            return (v, v, v, 1.0)
        values = list(value)
    except Exception:
        return fallback
    if len(values) >= 4:
        return (float(values[0]), float(values[1]), float(values[2]), float(values[3]))
    if len(values) == 3:
        return (float(values[0]), float(values[1]), float(values[2]), 1.0)
    if len(values) == 1:
        v = float(values[0])
        return (v, v, v, 1.0)
    return fallback


def principled_input_default(material, input_name, fallback):
    bsdf = find_principled(material)
    if not bsdf:
        return fallback
    return socket_default_as_color(bsdf.inputs.get(input_name), fallback)


def trace_image_from_node(node, visited=None):
    if visited is None:
        visited = set()
    if node is None or node in visited:
        return None
    visited.add(node)

    if getattr(node, "bl_idname", "") == "ShaderNodeTexImage":
        return getattr(node, "image", None)

    for inp in getattr(node, "inputs", []):
        if not inp.is_linked:
            continue
        for link in inp.links:
            image = trace_image_from_node(link.from_node, visited)
            if image:
                return image
    return None


def trace_image_from_socket(socket):
    if not socket or not socket.is_linked:
        return None
    for link in socket.links:
        image = trace_image_from_node(link.from_node)
        if image:
            return image
    return None


def find_named_image(material, keywords):
    if not material or not material.use_nodes or not material.node_tree:
        return None
    for node in iter_nodes_recursive(material):
        if node.bl_idname != "ShaderNodeTexImage" or not node.image:
            continue
        haystack = " ".join(
            str(value).lower()
            for value in (node.name, node.label, node.image.name)
            if value
        )
        if any(keyword in haystack for keyword in keywords):
            return node.image
    return None


def find_any_image(material):
    if not material or not material.use_nodes or not material.node_tree:
        return None
    for node in iter_nodes_recursive(material):
        if node.bl_idname == "ShaderNodeTexImage" and node.image:
            return node.image
    return None


def find_unambiguous_ao_image(material):
    candidates = []
    if not material or not material.use_nodes or not material.node_tree:
        return None, "has no node-based AO source"
    for node in iter_nodes_recursive(material):
        if node.bl_idname != "ShaderNodeTexImage" or not node.image:
            continue
        haystack = " ".join(
            str(value).lower()
            for value in (node.name, node.label, node.image.name)
            if value
        )
        if (
            re.search(
                r"(^|[^a-z0-9])"
                r"(ao|ambient.?occlusion|occlusion)"
                r"([^a-z0-9]|$)",
                haystack,
            )
            or is_pureplay_ord_search_text(haystack)
        ):
            candidates.append(node.image)
    unique = []
    for image in candidates:
        if image not in unique:
            unique.append(image)
    if len(unique) == 1:
        return unique[0], ""
    if not unique:
        return None, "has no unambiguous standalone AO image"
    return None, "has multiple possible AO images"


def image_node_search_text(node):
    return " ".join(
        str(value).lower()
        for value in (
            getattr(node, "name", ""),
            getattr(node, "label", ""),
            getattr(getattr(node, "image", None), "name", ""),
            getattr(getattr(node, "image", None), "filepath", ""),
        )
        if value
    )


def is_pureplay_ord_search_text(value):
    normalized = re.sub(r"[^a-z0-9]+", "", value.lower())
    if "occlusionroughnessdisplacement" in normalized:
        return True
    return bool(re.search(r"(^|[^a-z0-9])ord([^a-z0-9]|$)", value.lower()))


def is_pureplay_ord_image(material, image):
    if image is None:
        return False
    for node in iter_nodes_recursive(material):
        if node.bl_idname != "ShaderNodeTexImage" or node.image != image:
            continue
        if is_pureplay_ord_search_text(image_node_search_text(node)):
            return True
    return False


def find_upstream_node_from_socket(socket, node_type, visited=None):
    if socket is None or not socket.is_linked:
        return None
    if visited is None:
        visited = set()
    for link in socket.links:
        node = link.from_node
        pointer = node.as_pointer()
        if pointer in visited:
            continue
        visited.add(pointer)
        if node.bl_idname == node_type:
            return node
        for input_socket in node.inputs:
            found = find_upstream_node_from_socket(
                input_socket,
                node_type,
                visited,
            )
            if found:
                return found
    return None


def linked_displacement_image(material):
    if not material or not material.use_nodes or not material.node_tree:
        return None
    output = active_material_output(material.node_tree)
    displacement = output.inputs.get("Displacement") if output else None
    image = trace_image_from_socket(displacement)
    if image:
        return image

    bsdf = find_principled(material)
    normal = bsdf.inputs.get("Normal") if bsdf else None
    bump = find_upstream_node_from_socket(normal, "ShaderNodeBump")
    if bump:
        return trace_image_from_socket(bump.inputs.get("Height"))
    return None


def find_unambiguous_displacement_image(material):
    linked = linked_displacement_image(material)
    if linked:
        return linked, ""
    if not material or not material.use_nodes or not material.node_tree:
        return None, "has no node-based displacement source"

    candidates = []
    pattern = re.compile(
        r"(^|[^a-z0-9])(height|displacement|disp)([^a-z0-9]|$)"
    )
    for node in iter_nodes_recursive(material):
        if node.bl_idname != "ShaderNodeTexImage" or not node.image:
            continue
        search_text = image_node_search_text(node)
        if (
            pattern.search(search_text)
            or is_pureplay_ord_search_text(search_text)
        ):
            candidates.append(node.image)
    unique = []
    for image in candidates:
        if image not in unique:
            unique.append(image)
    if len(unique) == 1:
        return unique[0], ""
    if not unique:
        return None, "has no unambiguous displacement or height image"
    return None, "has multiple possible displacement or height images"


def material_uses_transparency(material):
    bsdf = find_principled(material)
    if not bsdf:
        return False
    socket = bsdf.inputs.get("Alpha")
    if socket is None:
        return False
    if socket.is_linked:
        return True
    try:
        return float(socket.default_value) < 0.999999
    except Exception:
        return False


def find_image_for_map(material, map_key):
    bsdf = find_principled(material)
    if bsdf:
        input_name = {
            MAP_BASE_COLOR: "Base Color",
            MAP_ROUGHNESS: "Roughness",
            MAP_NORMAL: "Normal",
            MAP_METALLIC: "Metallic",
        }.get(map_key)
        if input_name:
            image = trace_image_from_socket(bsdf.inputs.get(input_name))
            if image:
                return image

    if map_key == MAP_BASE_COLOR:
        return find_any_image(material)
    if map_key == MAP_ROUGHNESS:
        return find_named_image(material, ("rough", "rgh", "gloss"))
    if map_key == MAP_NORMAL:
        return find_named_image(material, ("normal", "nrm", "bump"))
    if map_key == MAP_METALLIC:
        return find_named_image(material, ("metal", "metallic"))
    if map_key == MAP_AO:
        return find_unambiguous_ao_image(material)[0]
    if map_key == MAP_DISPLACEMENT:
        return find_unambiguous_displacement_image(material)[0]
    return None


def ensure_material_slots(objects):
    for obj in objects:
        mesh = obj.data
        if len(mesh.materials) == 0:
            material = bpy.data.materials.new(f"{obj.name}_BakeAddon_SourceMaterial")
            material.diffuse_color = (0.8, 0.8, 0.8, 1.0)
            mesh.materials.append(material)
            continue
        for index, material in enumerate(mesh.materials):
            if material is not None:
                continue
            replacement = bpy.data.materials.new(f"{obj.name}_Slot{index}_BakeAddon_SourceMaterial")
            replacement.diffuse_color = (0.8, 0.8, 0.8, 1.0)
            mesh.materials[index] = replacement


def used_materials_in_order(objects):
    materials = []
    seen = set()
    for obj in objects:
        mesh = obj.data
        used_indices = {poly.material_index for poly in mesh.polygons}
        for slot_index, material in enumerate(mesh.materials):
            if slot_index not in used_indices or not material:
                continue
            pointer = material.as_pointer()
            if pointer in seen:
                continue
            seen.add(pointer)
            materials.append(material)
    return materials


def material_manual_trim_priority(material):
    if material is None:
        return 0.0
    if MANUAL_TRIM_PRIORITY_TAG not in material:
        legacy_value = material.get(
            LEGACY_MANUAL_TRIM_PRIORITY_TAG,
            None,
        )
        if legacy_value is None:
            return 0.0
        try:
            legacy_value = float(legacy_value)
        except (TypeError, ValueError):
            legacy_value = 1.0
        if not math.isfinite(legacy_value):
            legacy_value = 1.0
        legacy_value = max(-3.0, min(3.0, legacy_value))
        legacy_weight = (
            legacy_value
            if legacy_value >= 1.0
            else 2.0 / (3.0 - legacy_value)
        )
        centered_priority = (
            5.0 * math.log(legacy_weight) / math.log(5.0)
        )
        # Property getters are evaluated while Blender draws the UI. ID
        # datablocks cannot be modified from a panel draw callback, so keep
        # legacy conversion read-only here. The setter removes the legacy tag
        # when the user next changes the slider in a write-safe context.
        return max(
            MANUAL_TRIM_PRIORITY_MIN,
            min(
                MANUAL_TRIM_PRIORITY_MAX,
                round(
                    centered_priority,
                    MANUAL_TRIM_PRIORITY_PRECISION,
                ),
            ),
        )
    try:
        value = float(material.get(MANUAL_TRIM_PRIORITY_TAG, 0.0))
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value):
        value = 0.0
    value = round(value, MANUAL_TRIM_PRIORITY_PRECISION)
    return max(MANUAL_TRIM_PRIORITY_MIN, min(MANUAL_TRIM_PRIORITY_MAX, value))


def set_material_manual_trim_priority(material, priority):
    priority = float(priority)
    if not math.isfinite(priority):
        priority = 0.0
    priority = round(priority, MANUAL_TRIM_PRIORITY_PRECISION)
    priority = max(
        MANUAL_TRIM_PRIORITY_MIN,
        min(MANUAL_TRIM_PRIORITY_MAX, priority),
    )
    if LEGACY_MANUAL_TRIM_PRIORITY_TAG in material:
        del material[LEGACY_MANUAL_TRIM_PRIORITY_TAG]
    if abs(priority) <= 1e-9:
        if MANUAL_TRIM_PRIORITY_TAG in material:
            del material[MANUAL_TRIM_PRIORITY_TAG]
    else:
        material[MANUAL_TRIM_PRIORITY_TAG] = priority
    return priority


def get_material_trim_priority(material):
    return material_manual_trim_priority(material)


def set_material_trim_priority(material, value):
    set_material_manual_trim_priority(material, value)


def update_material_trim_priority(material, context):
    priority = material_manual_trim_priority(material)
    material_names = selected_trim_source_material_names(
        context,
        fallback_material=material,
    )
    for material_name in material_names:
        selected_material = bpy.data.materials.get(material_name)
        if selected_material is None or selected_material == material:
            continue
        set_material_manual_trim_priority(
            selected_material,
            priority,
        )
    schedule_trim_layout_preview(
        context,
        invalidate_analysis=False,
    )


def register_material_trim_priority_property():
    if hasattr(bpy.types.Material, MANUAL_TRIM_PRIORITY_PROPERTY):
        delattr(bpy.types.Material, MANUAL_TRIM_PRIORITY_PROPERTY)
    setattr(
        bpy.types.Material,
        MANUAL_TRIM_PRIORITY_PROPERTY,
        FloatProperty(
            name="Trim Priority",
            description=(
                "Trim pixel-area priority: 0 is normal, -5 is one-fifth "
                "size, and 5 is five times size. Applies to every material "
                "represented by the selected faces"
            ),
            default=0.0,
            min=MANUAL_TRIM_PRIORITY_MIN,
            max=MANUAL_TRIM_PRIORITY_MAX,
            soft_min=MANUAL_TRIM_PRIORITY_MIN,
            soft_max=MANUAL_TRIM_PRIORITY_MAX,
            step=10,
            precision=MANUAL_TRIM_PRIORITY_PRECISION,
            get=get_material_trim_priority,
            set=set_material_trim_priority,
            update=update_material_trim_priority,
        ),
    )


def unregister_material_trim_priority_property():
    if hasattr(bpy.types.Material, MANUAL_TRIM_PRIORITY_PROPERTY):
        delattr(bpy.types.Material, MANUAL_TRIM_PRIORITY_PROPERTY)


def active_face_and_material_index(context, obj):
    if context.mode == "EDIT_MESH" and obj.mode == "EDIT":
        try:
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            active = bm.select_history.active
            if not isinstance(active, bmesh.types.BMFace):
                active = getattr(bm.faces, "active", None)
            if (
                isinstance(active, bmesh.types.BMFace)
                and active.is_valid
                and active.select
            ):
                return active.index, active.material_index
            for face in bm.faces:
                if face.select and face.is_valid:
                    return face.index, face.material_index
        except Exception:
            pass
    elif obj.mode == "OBJECT":
        selected_faces = [
            polygon for polygon in obj.data.polygons if polygon.select
        ]
        if len(selected_faces) == 1:
            polygon = selected_faces[0]
            return polygon.index, polygon.material_index
    return None, obj.active_material_index


def generated_face_source_material_index(context, obj, face_index):
    if face_index is None:
        return None
    if context.mode == "EDIT_MESH" and obj.mode == "EDIT":
        try:
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            layer = bm.faces.layers.int.get(
                SOURCE_MATERIAL_ATTRIBUTE_NAME
            )
            if layer is not None and face_index < len(bm.faces):
                return int(bm.faces[face_index][layer])
        except Exception:
            return None
        return None
    attribute = obj.data.attributes.get(SOURCE_MATERIAL_ATTRIBUTE_NAME)
    if (
        attribute is not None
        and attribute.data_type == "INT"
        and attribute.domain == "FACE"
        and face_index < len(attribute.data)
    ):
        return int(attribute.data[face_index].value)
    return None


def active_trim_source_material(context):
    selected = getattr(context, "active_object", None)
    if selected is None or selected.type != "MESH":
        return None
    source_pairs, errors, _selection_replacements = resolve_trim_sources(
        [selected]
    )
    if errors:
        return None
    if not source_pairs:
        return None
    _target, source = source_pairs[0]
    face_index, material_index = active_face_and_material_index(
        context,
        selected,
    )
    if selected.get(GENERATED_TAG, False):
        source_material_index = generated_face_source_material_index(
            context,
            selected,
            face_index,
        )
        if source_material_index is not None:
            material_index = source_material_index
        elif source is not None:
            material_index = source.active_material_index
    if (
        source is None
        or material_index < 0
        or material_index >= len(source.data.materials)
    ):
        return None
    return source.data.materials[material_index]


def selected_trim_source_material_names(
    context,
    fallback_material=None,
):
    names = []
    seen = set()
    selected_objects = [
        obj
        for obj in getattr(context, "selected_objects", ())
        if obj.type == "MESH"
    ]
    for selected in selected_objects:
        source_pairs, errors, _selection_replacements = (
            resolve_trim_sources([selected])
        )
        if errors or not source_pairs:
            continue
        _target, source = source_pairs[0]
        if source is None:
            continue

        material_indices = []
        if context.mode == "EDIT_MESH" and selected.mode == "EDIT":
            try:
                bm = bmesh.from_edit_mesh(selected.data)
                source_layer = (
                    bm.faces.layers.int.get(
                        SOURCE_MATERIAL_ATTRIBUTE_NAME
                    )
                    if selected.get(GENERATED_TAG, False)
                    else None
                )
                for face in bm.faces:
                    if not face.is_valid or not face.select:
                        continue
                    material_indices.append(
                        int(face[source_layer])
                        if source_layer is not None
                        else int(face.material_index)
                    )
            except Exception:
                continue
        else:
            source_attribute = (
                selected.data.attributes.get(
                    SOURCE_MATERIAL_ATTRIBUTE_NAME
                )
                if selected.get(GENERATED_TAG, False)
                else None
            )
            for polygon in selected.data.polygons:
                if not polygon.select:
                    continue
                if (
                    source_attribute is not None
                    and source_attribute.data_type == "INT"
                    and source_attribute.domain == "FACE"
                    and polygon.index < len(source_attribute.data)
                ):
                    material_indices.append(
                        int(
                            source_attribute.data[
                                polygon.index
                            ].value
                        )
                    )
                else:
                    material_indices.append(
                        int(polygon.material_index)
                    )

        for material_index in material_indices:
            if (
                material_index < 0
                or material_index >= len(source.data.materials)
            ):
                continue
            material = source.data.materials[material_index]
            if material is None:
                continue
            pointer = material.as_pointer()
            if pointer in seen:
                continue
            seen.add(pointer)
            names.append(material.name_full)

    if not names:
        material = (
            fallback_material
            if fallback_material is not None
            else active_trim_source_material(context)
        )
        if material is not None:
            names.append(material.name_full)
    return tuple(names)


def manual_trim_priority_weight(priority):
    priority = max(
        MANUAL_TRIM_PRIORITY_MIN,
        min(MANUAL_TRIM_PRIORITY_MAX, float(priority)),
    )
    return math.pow(
        TRIM_PRIORITY_MAX_SHARE,
        priority / MANUAL_TRIM_PRIORITY_MAX,
    )


def mix_rgb(color_a, color_b, factor):
    factor = max(0.0, min(1.0, float(factor)))
    return tuple(
        start + (end - start) * factor
        for start, end in zip(color_a, color_b)
    )


def manual_trim_priority_dot_color(priority):
    priority = max(
        MANUAL_TRIM_PRIORITY_MIN,
        min(MANUAL_TRIM_PRIORITY_MAX, float(priority)),
    )
    blue = (0.04, 0.22, 1.0)
    green = (0.04, 0.9, 0.18)
    red = (1.0, 0.04, 0.02)
    if priority < 0.0:
        return mix_rgb(
            green,
            blue,
            -priority / abs(MANUAL_TRIM_PRIORITY_MIN),
        ) + (1.0,)
    return mix_rgb(
        green,
        red,
        priority / MANUAL_TRIM_PRIORITY_MAX,
    ) + (1.0,)


def collect_material_infos(
    objects,
    map_keys,
    include_pureplay_ao=True,
):
    infos = {}
    image_keys = set(expanded_bake_map_keys(map_keys))
    if MAP_PUREPLAY_ORD in map_keys and not include_pureplay_ao:
        image_keys.discard(MAP_AO)
    image_keys.add(MAP_BASE_COLOR)
    for material in used_materials_in_order(objects):
        images = {key: find_image_for_map(material, key) for key in image_keys}
        base_color_image = images.get(MAP_BASE_COLOR)
        warnings = []
        if find_principled(material) is None:
            warnings.append(
                f"{material.name_full}: no top-level Principled shader feeds "
                "the active Material Output; supported maps use defaults"
            )
        if MAP_AO in image_keys and not images.get(MAP_AO):
            _image, reason = find_unambiguous_ao_image(material)
            if reason:
                warnings.append(f"{material.name_full}: {reason}; AO will be white")
        elif MAP_AO in image_keys and images.get(MAP_AO):
            top_level_ao = any(
                node.bl_idname == "ShaderNodeTexImage"
                and node.image == images[MAP_AO]
                for node in material.node_tree.nodes
            )
            if not top_level_ao:
                images[MAP_AO] = None
                warnings.append(
                    f"{material.name_full}: AO inside a node group cannot be "
                    "isolated reliably; AO will be white"
                )
        if (
            MAP_DISPLACEMENT in image_keys
            and not images.get(MAP_DISPLACEMENT)
        ):
            _image, reason = find_unambiguous_displacement_image(material)
            if reason:
                warnings.append(
                    f"{material.name_full}: {reason}; displacement will be black"
                )
        elif (
            MAP_DISPLACEMENT in image_keys
            and images.get(MAP_DISPLACEMENT)
            and linked_displacement_image(material) is None
        ):
            top_level_displacement = any(
                node.bl_idname == "ShaderNodeTexImage"
                and node.image == images[MAP_DISPLACEMENT]
                for node in material.node_tree.nodes
            )
            if not top_level_displacement:
                images[MAP_DISPLACEMENT] = None
                warnings.append(
                    f"{material.name_full}: displacement inside a node group "
                    "cannot be isolated reliably; displacement will be black"
                )
        if material.use_nodes and material.node_tree:
            output = active_material_output(material.node_tree)
            upstream = []
            if output and output.inputs.get("Surface"):
                surface = output.inputs["Surface"]
                if surface.is_linked:
                    upstream = upstream_nodes(surface.links[0].from_node)
            if any(
                node.bl_idname == "ShaderNodeBsdfTransparent"
                for node in upstream
            ):
                warnings.append(
                    f"{material.name_full}: Transparent BSDF mixing uses "
                    "Principled Alpha or opaque fallback"
                )
        infos[material.name_full] = MaterialInfo(
            material=material,
            images=images,
            color=material_display_color(material),
            label=material.name_full,
            base_color_aspect=image_aspect_ratio(base_color_image),
            has_transparency=material_uses_transparency(material),
            warnings=tuple(warnings),
        )
    return infos


def collect_layout_material_infos(objects):
    """Collect only the material data that can affect atlas rectangle layout."""
    infos = {}
    for material in used_materials_in_order(objects):
        base_color_image = find_image_for_map(
            material,
            MAP_BASE_COLOR,
        )
        infos[material.name_full] = MaterialInfo(
            material=material,
            images={MAP_BASE_COLOR: base_color_image},
            color=material_display_color(material),
            label=material.name_full,
            base_color_aspect=image_aspect_ratio(base_color_image),
        )
    return infos


def median_positive(values, fallback=1.0):
    ordered = sorted(
        float(value)
        for value in values
        if math.isfinite(float(value)) and float(value) > 0.0
    )
    if not ordered:
        return float(fallback)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) * 0.5


def normalize_bounded_priority_shares(material_infos, priorities):
    material_names = list(material_infos.keys())
    count = len(material_names)
    if count == 0:
        return {}
    if count == 1:
        return {material_names[0]: 1.0}

    raw_values = [
        float(priorities.get(material_name, 1.0))
        for material_name in material_names
    ]
    reference = median_positive(raw_values, fallback=1.0)
    relative_values = [
        max(1e-12, min(1e12, value / reference))
        if math.isfinite(value) and value > 0.0
        else 1.0
        for value in raw_values
    ]

    def bounded_total(multiplier):
        return sum(
            max(
                TRIM_PRIORITY_MIN_SHARE,
                min(TRIM_PRIORITY_MAX_SHARE, multiplier * value),
            )
            for value in relative_values
        )

    target_total = float(count)
    low = 0.0
    high = 1.0
    while bounded_total(high) < target_total:
        high *= 2.0

    for _iteration in range(80):
        middle = (low + high) * 0.5
        if bounded_total(middle) < target_total:
            low = middle
        else:
            high = middle

    multiplier = (low + high) * 0.5
    shares = [
        max(
            TRIM_PRIORITY_MIN_SHARE,
            min(TRIM_PRIORITY_MAX_SHARE, multiplier * value),
        )
        for value in relative_values
    ]
    return dict(zip(material_names, shares))


def manual_material_priorities(material_infos):
    return {
        material_name: manual_trim_priority_weight(
            material_manual_trim_priority(info.material)
        )
        for material_name, info in material_infos.items()
    }


def image_aspect_ratio(image):
    if image is None:
        return 1.0
    try:
        width, height = image.size
        width = float(width)
        height = float(height)
    except Exception:
        return 1.0
    if width <= 0.0 or height <= 0.0:
        return 1.0
    return max(0.001, min(1000.0, width / height))


def fit_aspect_rect_in_cell(cell_x, cell_y, cell_w, cell_h, aspect):
    cell_w = max(1, int(cell_w))
    cell_h = max(1, int(cell_h))
    aspect = max(0.001, min(1000.0, float(aspect or 1.0)))
    cell_aspect = cell_w / max(1.0, float(cell_h))

    if cell_aspect > aspect:
        rect_h = cell_h
        rect_w = max(1, min(cell_w, int(round(rect_h * aspect))))
    else:
        rect_w = cell_w
        rect_h = max(1, min(cell_h, int(round(rect_w / aspect))))

    rect_x = int(cell_x) + max(0, (cell_w - rect_w) // 2)
    rect_y = int(cell_y) + max(0, (cell_h - rect_h) // 2)
    return rect_x, rect_y, rect_w, rect_h


def build_sections(material_infos, atlas_size, padding_px, layout_mode):
    infos = list(material_infos.values())
    if not infos:
        raise RuntimeError("No materials found on selected mesh objects.")

    if layout_mode == "GRID":
        return build_compact_grid_sections(
            material_infos,
            atlas_size,
            padding_px,
        )

    count = len(infos)
    if layout_mode == "VERTICAL":
        columns, rows = 1, count
    else:
        columns, rows = count, 1

    cell_w = max(1, atlas_size // columns)
    cell_h = max(1, atlas_size // rows)

    max_pad = max(0, min(cell_w, cell_h) // 3)
    pad = max(0, min(int(padding_px), max_pad))
    inner_w = max(1, cell_w - pad * 2)
    inner_h = max(1, cell_h - pad * 2)

    sections = {}
    for index, info in enumerate(infos):
        col = index % columns
        row_from_top = index // columns
        row = rows - 1 - row_from_top
        cell_x = col * cell_w + pad
        cell_y = row * cell_h + pad
        x, y, section_w, section_h = fit_aspect_rect_in_cell(
            cell_x,
            cell_y,
            inner_w,
            inner_h,
            info.base_color_aspect,
        )
        section = AtlasSection(
            material_name=info.material.name_full,
            x=x,
            y=y,
            w=section_w,
            h=section_h,
            u0=x / atlas_size,
            v0=y / atlas_size,
            u1=(x + section_w) / atlas_size,
            v1=(y + section_h) / atlas_size,
        )
        sections[info.material.name_full] = section
    return sections


def weighted_section_specs(material_infos, priority_shares):
    specs = []
    for order, info in enumerate(material_infos.values()):
        material_name = info.material.name_full
        share = max(
            TRIM_PRIORITY_MIN_SHARE,
            min(
                TRIM_PRIORITY_MAX_SHARE,
                float(priority_shares.get(material_name, 1.0)),
            ),
        )
        aspect = max(
            0.001,
            min(1000.0, float(info.base_color_aspect or 1.0)),
        )
        specs.append(
            {
                "material_name": material_name,
                "order": order,
                "unit_w": math.sqrt(share * aspect),
                "unit_h": math.sqrt(share / aspect),
            }
        )
    return specs


def scaled_weighted_rects(specs, scale, padding_px):
    pad = max(0, int(padding_px))
    rects = []
    for spec in specs:
        width = max(1, int(math.floor(spec["unit_w"] * scale)))
        height = max(1, int(math.floor(spec["unit_h"] * scale)))
        rects.append(
            {
                **spec,
                "w": width,
                "h": height,
                "outer_w": width + pad * 2,
                "outer_h": height + pad * 2,
            }
        )
    return rects


def rectangles_intersect(first, second):
    return not (
        first["x"] + first["w"] <= second["x"]
        or second["x"] + second["w"] <= first["x"]
        or first["y"] + first["h"] <= second["y"]
        or second["y"] + second["h"] <= first["y"]
    )


def rectangle_contains(outer, inner):
    return (
        inner["x"] >= outer["x"]
        and inner["y"] >= outer["y"]
        and inner["x"] + inner["w"] <= outer["x"] + outer["w"]
        and inner["y"] + inner["h"] <= outer["y"] + outer["h"]
    )


def prune_free_rectangles(rectangles):
    valid = [
        rect
        for rect in rectangles
        if rect["w"] > 0 and rect["h"] > 0
    ]
    pruned = []
    for index, rect in enumerate(valid):
        if any(
            index != other_index
            and rectangle_contains(other, rect)
            and (
                other["x"] != rect["x"]
                or other["y"] != rect["y"]
                or other["w"] != rect["w"]
                or other["h"] != rect["h"]
                or other_index < index
            )
            for other_index, other in enumerate(valid)
        ):
            continue
        if rect not in pruned:
            pruned.append(rect)
    pruned.sort(
        key=lambda rect: (
            rect["y"],
            rect["x"],
            rect["w"] * rect["h"],
            rect["w"],
            rect["h"],
        )
    )
    return pruned


def split_free_rectangles(free_rectangles, placed):
    split = []
    placed_right = placed["x"] + placed["w"]
    placed_top = placed["y"] + placed["h"]
    for free in free_rectangles:
        if not rectangles_intersect(free, placed):
            split.append(free)
            continue

        free_right = free["x"] + free["w"]
        free_top = free["y"] + free["h"]
        if placed["x"] > free["x"]:
            split.append(
                {
                    "x": free["x"],
                    "y": free["y"],
                    "w": placed["x"] - free["x"],
                    "h": free["h"],
                }
            )
        if placed_right < free_right:
            split.append(
                {
                    "x": placed_right,
                    "y": free["y"],
                    "w": free_right - placed_right,
                    "h": free["h"],
                }
            )
        if placed["y"] > free["y"]:
            split.append(
                {
                    "x": free["x"],
                    "y": free["y"],
                    "w": free["w"],
                    "h": placed["y"] - free["y"],
                }
            )
        if placed_top < free_top:
            split.append(
                {
                    "x": free["x"],
                    "y": placed_top,
                    "w": free["w"],
                    "h": free_top - placed_top,
                }
            )
    return prune_free_rectangles(split)


def maxrects_pack_ordered(ordered_rects, atlas_size):
    free_rectangles = [
        {"x": 0, "y": 0, "w": atlas_size, "h": atlas_size}
    ]
    packed = []
    for rect in ordered_rects:
        best = None
        for free in free_rectangles:
            if (
                rect["outer_w"] > free["w"]
                or rect["outer_h"] > free["h"]
            ):
                continue
            leftover_w = free["w"] - rect["outer_w"]
            leftover_h = free["h"] - rect["outer_h"]
            score = (
                min(leftover_w, leftover_h),
                max(leftover_w, leftover_h),
                free["y"],
                free["x"],
            )
            if best is None or score < best[0]:
                best = (score, free)
        if best is None:
            return None

        free = best[1]
        placed = {
            **rect,
            "outer_x": free["x"],
            "outer_y": free["y"],
        }
        packed.append(placed)
        free_rectangles = split_free_rectangles(
            free_rectangles,
            {
                "x": placed["outer_x"],
                "y": placed["outer_y"],
                "w": rect["outer_w"],
                "h": rect["outer_h"],
            },
        )
    return packed


def center_maxrects_pack(packed, atlas_size, padding_px):
    if not packed:
        return []
    min_x = min(rect["outer_x"] for rect in packed)
    min_y = min(rect["outer_y"] for rect in packed)
    max_x = max(
        rect["outer_x"] + rect["outer_w"]
        for rect in packed
    )
    max_y = max(
        rect["outer_y"] + rect["outer_h"]
        for rect in packed
    )
    offset_x = (atlas_size - (max_x - min_x)) // 2 - min_x
    offset_y = (atlas_size - (max_y - min_y)) // 2 - min_y
    pad = max(0, int(padding_px))
    centered = []
    for rect in packed:
        centered.append(
            {
                **rect,
                "x": rect["outer_x"] + offset_x + pad,
                "y": rect["outer_y"] + offset_y + pad,
            }
        )
    centered.sort(key=lambda rect: rect["order"])
    return centered


def maxrects_pack_grid(rects, atlas_size, padding_px):
    orderings = (
        lambda rect: (
            -(rect["outer_w"] * rect["outer_h"]),
            -max(rect["outer_w"], rect["outer_h"]),
            rect["order"],
            rect["material_name"],
        ),
        lambda rect: (
            -max(rect["outer_w"], rect["outer_h"]),
            -(rect["outer_w"] * rect["outer_h"]),
            rect["order"],
            rect["material_name"],
        ),
        lambda rect: (
            -rect["outer_h"],
            -rect["outer_w"],
            rect["order"],
            rect["material_name"],
        ),
        lambda rect: (
            -rect["outer_w"],
            -rect["outer_h"],
            rect["order"],
            rect["material_name"],
        ),
    )
    candidates = []
    for ordering_index, ordering in enumerate(orderings):
        packed = maxrects_pack_ordered(
            sorted(rects, key=ordering),
            atlas_size,
        )
        if packed is None:
            continue
        min_x = min(rect["outer_x"] for rect in packed)
        min_y = min(rect["outer_y"] for rect in packed)
        max_x = max(
            rect["outer_x"] + rect["outer_w"]
            for rect in packed
        )
        max_y = max(
            rect["outer_y"] + rect["outer_h"]
            for rect in packed
        )
        bounding_w = max_x - min_x
        bounding_h = max_y - min_y
        candidates.append(
            (
                (
                    bounding_w * bounding_h,
                    max(bounding_w, bounding_h),
                    bounding_w + bounding_h,
                    ordering_index,
                ),
                packed,
            )
        )
    if not candidates:
        return None
    _score, packed = min(candidates, key=lambda item: item[0])
    return center_maxrects_pack(
        packed,
        atlas_size,
        padding_px,
    )


def try_pack_weighted_rects(specs, atlas_size, padding_px, layout_mode, scale):
    atlas_size = max(1, int(atlas_size))
    pad = max(0, int(padding_px))
    rects = scaled_weighted_rects(specs, scale, pad)
    if not rects:
        return []

    if any(
        rect["outer_w"] > atlas_size or rect["outer_h"] > atlas_size
        for rect in rects
    ):
        return None

    packed = []
    if layout_mode == "HORIZONTAL":
        total_width = sum(rect["outer_w"] for rect in rects)
        if total_width > atlas_size:
            return None
        cursor_x = (atlas_size - total_width) // 2
        for rect in rects:
            outer_y = (atlas_size - rect["outer_h"]) // 2
            packed.append(
                {
                    **rect,
                    "x": cursor_x + pad,
                    "y": outer_y + pad,
                }
            )
            cursor_x += rect["outer_w"]
        return packed

    if layout_mode == "VERTICAL":
        total_height = sum(rect["outer_h"] for rect in rects)
        if total_height > atlas_size:
            return None
        cursor_top = (atlas_size + total_height) // 2
        for rect in rects:
            outer_y = cursor_top - rect["outer_h"]
            packed.append(
                {
                    **rect,
                    "x": (atlas_size - rect["outer_w"]) // 2 + pad,
                    "y": outer_y + pad,
                }
            )
            cursor_top = outer_y
        return packed

    return maxrects_pack_grid(rects, atlas_size, pad)


def largest_feasible_padding(specs, atlas_size, requested_padding, layout_mode):
    requested = max(0, int(requested_padding))
    if try_pack_weighted_rects(
        specs,
        atlas_size,
        requested,
        layout_mode,
        0.0,
    ) is not None:
        return requested

    if try_pack_weighted_rects(
        specs,
        atlas_size,
        0,
        layout_mode,
        0.0,
    ) is None:
        return None

    low = 0
    high = requested
    while low < high:
        middle = (low + high + 1) // 2
        if try_pack_weighted_rects(
            specs,
            atlas_size,
            middle,
            layout_mode,
            0.0,
        ) is None:
            high = middle - 1
        else:
            low = middle
    return low


def largest_weighted_pack(specs, atlas_size, padding_px, layout_mode):
    if try_pack_weighted_rects(
        specs,
        atlas_size,
        padding_px,
        layout_mode,
        0.0,
    ) is None:
        return None

    low = 0.0
    high = max(1.0, float(atlas_size))
    for _iteration in range(32):
        if try_pack_weighted_rects(
            specs,
            atlas_size,
            padding_px,
            layout_mode,
            high,
        ) is None:
            break
        high *= 2.0

    for _iteration in range(48):
        middle = (low + high) * 0.5
        if try_pack_weighted_rects(
            specs,
            atlas_size,
            padding_px,
            layout_mode,
            middle,
        ) is None:
            high = middle
        else:
            low = middle

    return try_pack_weighted_rects(
        specs,
        atlas_size,
        padding_px,
        layout_mode,
        low,
    )


def sections_from_packed_rects(
    packed,
    material_infos,
    atlas_size,
):
    sections = {}
    for rect in packed:
        x = int(rect["x"])
        y = int(rect["y"])
        width = int(rect["w"])
        height = int(rect["h"])
        if (
            x < 0
            or y < 0
            or x + width > atlas_size
            or y + height > atlas_size
        ):
            raise RuntimeError(
                "A packed trim rectangle exceeded the atlas bounds."
            )
        section = AtlasSection(
            material_name=rect["material_name"],
            x=x,
            y=y,
            w=width,
            h=height,
            u0=x / atlas_size,
            v0=y / atlas_size,
            u1=(x + width) / atlas_size,
            v1=(y + height) / atlas_size,
        )
        sections[rect["material_name"]] = section
    if len(sections) != len(material_infos):
        raise RuntimeError(
            "Trim packing did not place every material."
        )
    return sections


def adaptive_row_grid_pack(specs, atlas_size, padding_px):
    """Pack fixed-aspect specs into shelves using one common pixel scale."""
    if not specs:
        return []
    atlas_size = max(1, int(atlas_size))
    pad = max(0, int(padding_px))
    orderings = (
        lambda spec: (spec["order"], spec["material_name"]),
        lambda spec: (
            -(spec["unit_h"] / max(spec["unit_w"], 1e-12)),
            spec["order"],
            spec["material_name"],
        ),
        lambda spec: (
            -max(spec["unit_w"], spec["unit_h"]),
            -(spec["unit_w"] * spec["unit_h"]),
            spec["order"],
            spec["material_name"],
        ),
    )
    candidates = []
    count = len(specs)
    for ordering_index, ordering in enumerate(orderings):
        ordered = sorted(specs, key=ordering)
        for columns in range(1, count + 1):
            rows = [
                ordered[start:start + columns]
                for start in range(0, count, columns)
            ]
            available_content_widths = [
                atlas_size - pad * 2 * len(row)
                for row in rows
            ]
            if any(width <= 0 for width in available_content_widths):
                continue

            row_unit_widths = []
            row_unit_heights = []
            for row, available_width in zip(
                rows,
                available_content_widths,
            ):
                unit_width = sum(spec["unit_w"] for spec in row)
                if unit_width <= 1e-12:
                    row_unit_widths = []
                    break
                row_unit_widths.append(unit_width)
                row_unit_heights.append(
                    max(spec["unit_h"] for spec in row)
                )
            if not row_unit_widths:
                continue

            available_content_height = (
                atlas_size - pad * 2 * len(rows)
            )
            if available_content_height <= 0:
                continue
            width_scale = min(
                available_width / unit_width
                for available_width, unit_width in zip(
                    available_content_widths,
                    row_unit_widths,
                )
            )
            height_scale = (
                available_content_height
                / max(sum(row_unit_heights), 1e-12)
            )
            scale = min(width_scale, height_scale)
            if not math.isfinite(scale) or scale <= 0.0:
                continue

            packed_rows = []
            for row in rows:
                row_rects = []
                for spec in row:
                    width = max(
                        1,
                        int(math.floor(spec["unit_w"] * scale)),
                    )
                    height = max(
                        1,
                        int(math.floor(spec["unit_h"] * scale)),
                    )
                    row_rects.append(
                        {
                            **spec,
                            "w": width,
                            "h": height,
                            "outer_w": width + pad * 2,
                            "outer_h": height + pad * 2,
                        }
                    )
                packed_rows.append(row_rects)

            row_heights = [
                max(rect["outer_h"] for rect in row)
                for row in packed_rows
            ]
            total_height = sum(row_heights)
            if total_height > atlas_size:
                continue
            cursor_y = (atlas_size - total_height) // 2
            packed = []
            valid = True
            for row, row_height in zip(
                packed_rows,
                row_heights,
            ):
                total_width = sum(rect["outer_w"] for rect in row)
                if total_width > atlas_size:
                    valid = False
                    break
                cursor_x = (atlas_size - total_width) // 2
                for rect in row:
                    packed.append(
                        {
                            **rect,
                            "x": cursor_x + pad,
                            "y": (
                                cursor_y
                                + (row_height - rect["outer_h"]) // 2
                                + pad
                            ),
                        }
                    )
                    cursor_x += rect["outer_w"]
                cursor_y += row_height
            if not valid:
                continue
            packed.sort(key=lambda rect: rect["order"])
            used_area = sum(
                rect["w"] * rect["h"]
                for rect in packed
            )
            candidates.append(
                (
                    (
                        -used_area,
                        len(rows),
                        columns,
                        ordering_index,
                    ),
                    packed,
                )
            )
    if not candidates:
        return None
    _score, packed = min(candidates, key=lambda item: item[0])
    return packed


def best_aspect_preserving_grid_pack(
    specs,
    atlas_size,
    padding_px,
):
    """Choose the fullest deterministic packing without reshaping sections."""
    candidates = []
    adaptive = adaptive_row_grid_pack(
        specs,
        atlas_size,
        padding_px,
    )
    if adaptive is not None:
        candidates.append((adaptive, 0))
    maxrects = largest_weighted_pack(
        specs,
        atlas_size,
        padding_px,
        "GRID",
    )
    if maxrects is not None:
        candidates.append((maxrects, 1))
    if not candidates:
        return None

    def candidate_score(candidate):
        packed, algorithm_order = candidate
        used_area = sum(rect["w"] * rect["h"] for rect in packed)
        return (-used_area, algorithm_order)

    packed, _algorithm_order = min(candidates, key=candidate_score)
    return packed


def aspect_preserving_grid_pack_with_safe_padding(
    specs,
    atlas_size,
    requested_padding,
):
    effective_padding = largest_feasible_padding(
        specs,
        atlas_size,
        requested_padding,
        "GRID",
    )
    if effective_padding is None:
        return None, None
    packed = best_aspect_preserving_grid_pack(
        specs,
        atlas_size,
        effective_padding,
    )
    return packed, effective_padding


def build_compact_grid_sections(
    material_infos,
    atlas_size,
    padding_px,
):
    priority_shares = {
        material_name: 1.0
        for material_name in material_infos
    }
    specs = weighted_section_specs(
        material_infos,
        priority_shares,
    )
    packed, effective_padding = (
        aspect_preserving_grid_pack_with_safe_padding(
            specs,
            atlas_size,
            padding_px,
        )
    )
    if packed is None or effective_padding is None:
        raise RuntimeError(
            "The trim rectangles cannot fit in the atlas."
        )
    return sections_from_packed_rects(
        packed,
        material_infos,
        atlas_size,
    )


def build_weighted_sections(
    material_infos,
    atlas_size,
    padding_px,
    layout_mode,
    priority_shares,
):
    if not material_infos:
        raise RuntimeError("No materials found on selected mesh objects.")

    priorities_are_equal = all(
        abs(float(priority_shares.get(material_name, 1.0)) - 1.0) <= 1e-9
        for material_name in material_infos
    )
    if layout_mode == "GRID":
        specs = weighted_section_specs(
            material_infos,
            priority_shares,
        )
        packed, effective_padding = (
            aspect_preserving_grid_pack_with_safe_padding(
                specs,
                atlas_size,
                padding_px,
            )
        )
        if packed is None or effective_padding is None:
            raise RuntimeError(
                "The weighted trim rectangles cannot fit in the atlas."
            )
        return (
            sections_from_packed_rects(
                packed,
                material_infos,
                atlas_size,
            ),
            effective_padding,
            not priorities_are_equal,
        )

    if priorities_are_equal:
        return (
            build_sections(
                material_infos,
                atlas_size,
                padding_px,
                layout_mode,
            ),
            None,
            False,
        )

    specs = weighted_section_specs(material_infos, priority_shares)
    effective_padding = largest_feasible_padding(
        specs,
        atlas_size,
        padding_px,
        layout_mode,
    )
    if effective_padding is None:
        raise RuntimeError("The weighted trim rectangles cannot fit in the atlas.")

    packed = largest_weighted_pack(
        specs,
        atlas_size,
        effective_padding,
        layout_mode,
    )
    if not packed:
        raise RuntimeError("The weighted trim rectangles cannot fit in the atlas.")

    sections = sections_from_packed_rects(
        packed,
        material_infos,
        atlas_size,
    )
    return sections, effective_padding, True


def calculate_trim_layout(
    material_infos,
    atlas_size,
    padding_px,
    layout_mode,
):
    warnings = []
    manual_priority_active = any(
        abs(
            material_manual_trim_priority(info.material)
        )
        > 1e-9
        for info in material_infos.values()
    )
    if manual_priority_active:
        try:
            priorities = manual_material_priorities(material_infos)
            priority_shares = normalize_bounded_priority_shares(
                material_infos,
                priorities,
            )
            (
                sections,
                effective_padding,
                used_weighted_layout,
            ) = build_weighted_sections(
                material_infos,
                atlas_size,
                padding_px,
                layout_mode,
                priority_shares,
            )
            if (
                effective_padding is not None
                and effective_padding < int(padding_px)
            ):
                warnings.append(
                    "Trim priority packing reduced padding from "
                    f"{padding_px}px to {effective_padding}px so every trim "
                    "could fit."
                )
            return TrimLayoutResult(
                sections=sections,
                effective_padding=effective_padding,
                used_weighted_layout=used_weighted_layout,
                warnings=tuple(warnings),
            )
        except Exception as weighted_exc:
            warnings.append(
                "Trim priority sizing could not build a weighted layout; "
                f"equal layout was used instead ({weighted_exc})."
            )

    return TrimLayoutResult(
        sections=build_sections(
            material_infos,
            atlas_size,
            padding_px,
            layout_mode,
        ),
        effective_padding=None,
        used_weighted_layout=False,
        warnings=tuple(warnings),
    )


def create_bake_plane(section, material_name, uv_map_name, collection=None):
    mesh = bpy.data.meshes.new(f"BakeAddon_TrimBakePlane_{material_name}")
    mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update(calc_edges=True)

    source_uv = mesh.uv_layers.new(name=SOURCE_UV_NAME)
    target_uv = mesh.uv_layers.new(name=uv_map_name)
    source_values = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    target_values = ((section.u0, section.v0), (section.u1, section.v0), (section.u1, section.v1), (section.u0, section.v1))

    for loop_index, uv in enumerate(source_values):
        source_uv.data[loop_index].uv = uv
    for loop_index, uv in enumerate(target_values):
        target_uv.data[loop_index].uv = uv

    mesh.uv_layers.active = target_uv
    try:
        mesh.uv_layers.active_render = target_uv
    except Exception:
        pass

    obj = bpy.data.objects.new(f"BakeAddon_TrimBakePlane_{material_name}", mesh)
    (collection or bpy.context.collection).objects.link(obj)
    return obj


def clone_reachable_node_groups(node_tree, owned_node_groups, memo=None):
    """Deep-copy groups before any bake-only UV redirection."""
    if node_tree is None:
        return
    if memo is None:
        memo = {}
    for node in node_tree.nodes:
        source_tree = getattr(node, "node_tree", None)
        if source_tree is None:
            continue
        pointer = source_tree.as_pointer()
        clone = memo.get(pointer)
        if clone is None:
            clone = source_tree.copy()
            clone.name = f"BakeAddon_BakeGroup_{source_tree.name}_{uuid.uuid4().hex[:8]}"
            memo[pointer] = clone
            owned_node_groups.append(clone)
            clone_reachable_node_groups(clone, owned_node_groups, memo)
        node.node_tree = clone


def force_node_tree_source_uv(node_tree, source_uv_name, visited=None):
    if node_tree is None:
        return
    if visited is None:
        visited = set()
    pointer = node_tree.as_pointer()
    if pointer in visited:
        return
    visited.add(pointer)

    nodes = node_tree.nodes
    links = node_tree.links
    uv_node = nodes.new("ShaderNodeUVMap")
    uv_node.name = "BakeAddon Source Tile UV"
    uv_node.label = "BakeAddon Source Tile UV"
    uv_node.uv_map = source_uv_name
    uv_node.location = (-900, 240)

    for node in list(nodes):
        if node is uv_node:
            continue
        if node.bl_idname == "ShaderNodeUVMap":
            try:
                node.uv_map = source_uv_name
            except Exception:
                pass
        if node.bl_idname == "ShaderNodeTexImage":
            vector_input = node.inputs.get("Vector")
            if vector_input and not vector_input.is_linked:
                try:
                    links.new(uv_node.outputs["UV"], vector_input)
                except Exception:
                    pass

    for node in list(nodes):
        if node.bl_idname == "ShaderNodeTexCoord":
            uv_output = node.outputs.get("UV")
            if uv_output:
                for link in list(uv_output.links):
                    to_socket = link.to_socket
                    try:
                        links.remove(link)
                        links.new(uv_node.outputs["UV"], to_socket)
                    except Exception:
                        pass
        child = getattr(node, "node_tree", None)
        if child:
            force_node_tree_source_uv(child, source_uv_name, visited)


def force_material_source_uv(material, source_uv_name):
    if not material or not material.use_nodes or not material.node_tree:
        return
    force_node_tree_source_uv(material.node_tree, source_uv_name)


def get_or_create_output_node(nodes):
    outputs = [
        node
        for node in nodes
        if node.bl_idname == "ShaderNodeOutputMaterial"
    ]
    for node in outputs:
        if getattr(node, "is_active_output", False):
            return node
    if outputs:
        return outputs[0]
    return nodes.new("ShaderNodeOutputMaterial")


def new_combine_color_node(nodes):
    try:
        node = nodes.new("ShaderNodeCombineColor")
        try:
            node.mode = "RGB"
        except Exception:
            pass
        return node
    except Exception:
        return nodes.new("ShaderNodeCombineRGB")


def first_socket(collection, preferred_names=()):
    for name in preferred_names:
        socket = collection.get(name)
        if socket:
            return socket
    return collection[0] if len(collection) else None


def link_socket_to_color(nodes, links, source_socket, color_socket):
    if source_socket is None or color_socket is None:
        return False
    try:
        links.new(source_socket, color_socket)
        return True
    except Exception:
        pass

    try:
        combine = new_combine_color_node(nodes)
        for input_name in ("Red", "Green", "Blue", "R", "G", "B"):
            socket = combine.inputs.get(input_name)
            if socket:
                try:
                    links.new(source_socket, socket)
                except Exception:
                    pass
        alpha_socket = combine.inputs.get("Alpha") or combine.inputs.get("A")
        if alpha_socket:
            alpha_socket.default_value = 1.0
        output = first_socket(combine.outputs, ("Color", "Image"))
        if output:
            links.new(output, color_socket)
            return True
    except Exception:
        pass
    return False


def route_input_to_emission(material, input_name, fallback_color):
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    output = get_or_create_output_node(nodes)
    surface_input = output.inputs.get("Surface")
    if surface_input:
        for link in list(surface_input.links):
            links.remove(link)

    emission = nodes.new("ShaderNodeEmission")
    emission.location = (160, 120)
    emission.inputs["Color"].default_value = fallback_color
    emission.inputs["Strength"].default_value = 1.0
    if surface_input:
        links.new(emission.outputs["Emission"], surface_input)

    bsdf = find_principled(material)
    socket = bsdf.inputs.get(input_name) if bsdf else None
    if not socket:
        return
    if socket.is_linked and socket.links:
        source_socket = socket.links[0].from_socket
        link_socket_to_color(nodes, links, source_socket, emission.inputs["Color"])
    else:
        emission.inputs["Color"].default_value = socket_default_as_color(socket, fallback_color)


def route_image_to_emission(
    material,
    source_image,
    fallback_color,
    channel_name=None,
):
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    output = get_or_create_output_node(nodes)
    surface_input = output.inputs.get("Surface")
    if surface_input:
        for link in list(surface_input.links):
            links.remove(link)

    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = fallback_color
    emission.inputs["Strength"].default_value = 1.0
    if surface_input:
        links.new(emission.outputs["Emission"], surface_input)
    if source_image is None:
        return False
    for node in nodes:
        if node.bl_idname == "ShaderNodeTexImage" and node.image == source_image:
            source_socket = node.outputs["Color"]
            if channel_name:
                separate = nodes.new("ShaderNodeSeparateColor")
                separate.mode = "RGB"
                links.new(source_socket, separate.inputs["Color"])
                source_socket = (
                    separate.outputs.get(channel_name)
                    or separate.outputs[0]
                )
            link_socket_to_color(
                nodes,
                links,
                source_socket,
                emission.inputs["Color"],
            )
            return True
    return False


def displacement_source_socket(material):
    """Return the most meaningful scalar displacement source in a copied graph."""
    if not material or not material.use_nodes or not material.node_tree:
        return None, None

    output = active_material_output(material.node_tree)
    displacement = output.inputs.get("Displacement") if output else None
    if displacement and displacement.is_linked:
        source_socket = displacement.links[0].from_socket
        source_node = displacement.links[0].from_node
        if source_node.bl_idname == "ShaderNodeDisplacement":
            height = source_node.inputs.get("Height")
            if height:
                if height.is_linked:
                    return height.links[0].from_socket, None
                try:
                    return None, float(height.default_value)
                except Exception:
                    return None, None
        return source_socket, None

    bsdf = find_principled(material)
    normal = bsdf.inputs.get("Normal") if bsdf else None
    bump = find_upstream_node_from_socket(normal, "ShaderNodeBump")
    if bump:
        height = bump.inputs.get("Height")
        if height:
            if height.is_linked:
                return height.links[0].from_socket, None
            try:
                return None, float(height.default_value)
            except Exception:
                return None, None
    return None, None


def route_displacement_to_emission(material, source_material):
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    source_socket, scalar_default = displacement_source_socket(material)
    source_image = find_unambiguous_displacement_image(source_material)[0]
    fallback = MAP_DEFS[MAP_DISPLACEMENT]["default"]
    if scalar_default is not None:
        fallback = (
            scalar_default,
            scalar_default,
            scalar_default,
            1.0,
        )

    output = get_or_create_output_node(nodes)
    surface_input = output.inputs.get("Surface")
    if surface_input:
        for link in list(surface_input.links):
            links.remove(link)
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = fallback
    emission.inputs["Strength"].default_value = 1.0
    if surface_input:
        links.new(emission.outputs["Emission"], surface_input)

    if source_socket is not None:
        if (
            source_socket.node.bl_idname == "ShaderNodeTexImage"
            and is_pureplay_ord_image(
                source_material,
                source_socket.node.image,
            )
        ):
            separate = nodes.new("ShaderNodeSeparateColor")
            separate.mode = "RGB"
            links.new(source_socket, separate.inputs["Color"])
            source_socket = separate.outputs.get("Blue") or separate.outputs[2]
        link_socket_to_color(
            nodes,
            links,
            source_socket,
            emission.inputs["Color"],
        )
        return True

    if source_image is None:
        return False
    for node in nodes:
        if node.bl_idname != "ShaderNodeTexImage" or node.image != source_image:
            continue
        source_socket = node.outputs["Color"]
        if is_pureplay_ord_image(source_material, source_image):
            separate = nodes.new("ShaderNodeSeparateColor")
            separate.mode = "RGB"
            links.new(source_socket, separate.inputs["Color"])
            source_socket = separate.outputs.get("Blue") or separate.outputs[2]
        link_socket_to_color(
            nodes,
            links,
            source_socket,
            emission.inputs["Color"],
        )
        return True
    return False


def prepare_bake_material(source_material, map_key, owned_node_groups=None):
    owned_node_groups = owned_node_groups if owned_node_groups is not None else []
    if source_material:
        material = source_material.copy()
        material.name = f"BakeAddon_Bake_{source_material.name}_{map_key}"
    else:
        material = bpy.data.materials.new(f"BakeAddon_Bake_Default_{map_key}")
    material.use_nodes = True

    clone_reachable_node_groups(material.node_tree, owned_node_groups)
    force_material_source_uv(material, SOURCE_UV_NAME)

    if map_key == MAP_BASE_COLOR:
        route_input_to_emission(material, "Base Color", material_display_color(source_material))
    elif map_key == MAP_ROUGHNESS:
        route_input_to_emission(material, "Roughness", principled_input_default(source_material, "Roughness", MAP_DEFS[map_key]["default"]))
    elif map_key == MAP_METALLIC:
        route_input_to_emission(material, "Metallic", principled_input_default(source_material, "Metallic", MAP_DEFS[map_key]["default"]))
    elif map_key == MAP_OPACITY:
        route_input_to_emission(
            material,
            "Alpha",
            principled_input_default(
                source_material,
                "Alpha",
                MAP_DEFS[map_key]["default"],
            ),
        )
    elif map_key == MAP_AO:
        ao_image = find_unambiguous_ao_image(source_material)[0]
        route_image_to_emission(
            material,
            ao_image,
            MAP_DEFS[map_key]["default"],
            (
                "Red"
                if is_pureplay_ord_image(source_material, ao_image)
                else None
            ),
        )
    elif map_key == MAP_DISPLACEMENT:
        route_displacement_to_emission(material, source_material)

    return material


def add_bake_target_node(material, image):
    material.use_nodes = True
    nodes = material.node_tree.nodes
    target = nodes.new("ShaderNodeTexImage")
    target.name = "BakeAddon_TrimBakeTarget"
    target.label = "BakeAddon Trim Bake Target"
    target.image = image
    target.location = (-260, -360)
    for node in nodes:
        node.select = False
    target.select = True
    nodes.active = target
    return target


def compute_safe_bake_margin(sections, requested_margin):
    requested = max(0, int(requested_margin))
    values = list(sections.values())
    if len(values) < 2:
        return requested
    safe = requested
    for index, first in enumerate(values):
        first_right = first.x + first.w
        first_top = first.y + first.h
        for second in values[index + 1:]:
            second_right = second.x + second.w
            second_top = second.y + second.h
            gap_x = max(second.x - first_right, first.x - second_right, 0)
            gap_y = max(second.y - first_top, first.y - second_top, 0)
            separating_gap = max(gap_x, gap_y)
            safe = min(
                safe,
                max(0, (separating_gap - 1) // 2),
            )
    return safe


def configure_cycles_for_trim_bake(scene, settings, bake_margin):
    scene.render.engine = "CYCLES"
    cycles = scene.cycles
    cycles.samples = settings.cycles_samples
    cycles.preview_samples = settings.cycles_samples
    cycles.use_denoising = False
    try:
        cycles.device = "GPU" if settings.cycles_use_gpu else "CPU"
    except Exception:
        pass
    try:
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
    except Exception:
        pass

    bake = scene.render.bake
    bake.margin = int(bake_margin)
    bake.use_clear = False
    bake.use_selected_to_active = False
    bake.use_cage = False
    try:
        bake.margin_type = "EXTEND"
    except Exception:
        pass
    try:
        bake.normal_r = "POS_X"
        bake.normal_g = (
            "NEG_Y"
            if effective_normal_map_format(settings) == "DIRECTX"
            else "POS_Y"
        )
        bake.normal_b = "POS_Z"
    except Exception:
        pass


def bake_type_for_map(map_key):
    if map_key in {
        MAP_BASE_COLOR,
        MAP_ROUGHNESS,
        MAP_METALLIC,
        MAP_AO,
        MAP_OPACITY,
    }:
        return "EMIT"
    if map_key == MAP_NORMAL:
        return "NORMAL"
    return "EMIT"


def run_isolated_bake(context, scene, plane, bake_type):
    view_layer = scene.view_layers[0]
    view_layer.update()
    for candidate in view_layer.objects:
        if candidate is not None:
            candidate.select_set(False, view_layer=view_layer)
    plane.select_set(True, view_layer=view_layer)
    view_layer.objects.active = plane
    override = {
        "scene": scene,
        "view_layer": view_layer,
        "active_object": plane,
        "object": plane,
        "selected_objects": [plane],
        "selected_editable_objects": [plane],
    }
    with context.temp_override(**override):
        result = bpy.ops.object.bake(type=bake_type)
    if "FINISHED" not in result:
        raise RuntimeError(
            f"Blender bake returned {sorted(result)} for {bake_type}."
        )


def compose_base_color_alpha(base_path, opacity_path, run_id):
    """Embed opacity through OpenImageIO's C-managed image buffers."""
    try:
        import OpenImageIO as oiio
    except Exception as exc:
        raise RuntimeError(
            "Blender's OpenImageIO module is required to embed Base Color alpha."
        ) from exc

    base = oiio.ImageBuf(base_path)
    opacity = oiio.ImageBuf(opacity_path)
    if base.has_error:
        raise RuntimeError(base.geterror())
    if opacity.has_error:
        raise RuntimeError(opacity.geterror())
    if (
        base.spec().width != opacity.spec().width
        or base.spec().height != opacity.spec().height
    ):
        raise RuntimeError("Base Color and opacity atlas sizes do not match.")

    rgb = oiio.ImageBufAlgo.channels(
        base,
        (0, 1, 2),
        ("R", "G", "B"),
    )
    alpha = oiio.ImageBufAlgo.channels(
        opacity,
        (0,),
        ("A",),
    )
    rgba = oiio.ImageBufAlgo.channel_append(rgb, alpha)
    composed_path = f"{base_path}.{run_id}.rgba.png"
    try:
        if not rgba.write(composed_path):
            raise RuntimeError(
                rgba.geterror() or "Could not write RGBA Base Color."
            )
        os.replace(composed_path, base_path)
    finally:
        if os.path.exists(composed_path):
            try:
                os.remove(composed_path)
            except OSError:
                pass


def compose_pureplay_ord(
    ao_path,
    roughness_path,
    displacement_path,
    output_path,
    run_id,
):
    """Pack PurePlay's R=AO, G=Roughness, B=Displacement texture."""
    try:
        import OpenImageIO as oiio
    except Exception as exc:
        raise RuntimeError(
            "Blender's OpenImageIO module is required to pack PurePlay ORD."
        ) from exc

    sources = [
        oiio.ImageBuf(ao_path),
        oiio.ImageBuf(roughness_path),
        oiio.ImageBuf(displacement_path),
    ]
    labels = ("AO", "Roughness", "Displacement")
    for label, source in zip(labels, sources):
        if source.has_error:
            raise RuntimeError(
                source.geterror() or f"Could not read staged {label} map."
            )
    dimensions = {
        (source.spec().width, source.spec().height)
        for source in sources
    }
    if len(dimensions) != 1:
        raise RuntimeError(
            "AO, Roughness, and Displacement atlas sizes do not match."
        )

    red = oiio.ImageBufAlgo.channels(sources[0], (0,), ("R",))
    green = oiio.ImageBufAlgo.channels(sources[1], (0,), ("G",))
    blue = oiio.ImageBufAlgo.channels(sources[2], (0,), ("B",))
    red_green = oiio.ImageBufAlgo.channel_append(red, green)
    packed = oiio.ImageBufAlgo.channel_append(red_green, blue)
    composed_path = f"{output_path}.{run_id}.packed.png"
    try:
        if not packed.write(composed_path):
            raise RuntimeError(
                packed.geterror() or "Could not write packed PurePlay ORD."
            )
        os.replace(composed_path, output_path)
    finally:
        if os.path.exists(composed_path):
            try:
                os.remove(composed_path)
            except OSError:
                pass


def build_cycles_bake_maps(
    context,
    material_infos,
    sections,
    map_keys,
    settings,
    report,
    image_prefix,
    run_id,
    staged_paths,
):
    atlas_size = int(settings.atlas_size)
    has_transparency = any(
        info.has_transparency for info in material_infos.values()
    )
    bake_keys = expanded_bake_map_keys(map_keys)
    pureplay_ao_disabled = (
        MAP_PUREPLAY_ORD in map_keys
        and not settings.pureplay_bake_ao
    )
    if has_transparency and MAP_OPACITY not in bake_keys:
        bake_keys.append(MAP_OPACITY)

    images = {}
    safe_margin = compute_safe_bake_margin(
        sections,
        settings.cycles_margin_px,
    )
    if safe_margin < int(settings.cycles_margin_px):
        report(
            {"WARNING"},
            (
                f"Bake Margin was clamped from {settings.cycles_margin_px}px "
                f"to {safe_margin}px to prevent neighboring trims from overlapping."
            ),
        )

    bake_scene = bpy.data.scenes.new(f"BakeAddon_TrimBake_{run_id}")
    configure_cycles_for_trim_bake(bake_scene, settings, safe_margin)
    opacity_path = (
        f"{staged_paths[MAP_BASE_COLOR]}.{run_id}.opacity.png"
        if has_transparency
        else None
    )
    component_paths = {}
    if MAP_PUREPLAY_ORD in map_keys:
        ord_path = staged_paths[MAP_PUREPLAY_ORD]
        component_paths = {
            map_key: (
                f"{ord_path}.{run_id}."
                f"{MAP_DEFS[map_key]['suffix']}.component.png"
            )
            for map_key in (
                MAP_AO,
                MAP_ROUGHNESS,
                MAP_DISPLACEMENT,
            )
        }
    success = False
    current_image = None
    current_object = None
    current_mesh = None
    current_material = None
    current_node_groups = []

    try:
        # Keep only one atlas-sized bake target resident at a time. This avoids
        # multiplying 8K memory use by the number of enabled output maps.
        for map_key in bake_keys:
            definition = MAP_DEFS[map_key]
            current_image = create_filled_image(
                f"BakeAddon_{run_id}_{image_prefix}_{definition['suffix']}",
                atlas_size,
                definition["default"],
                definition["colorspace"],
                alpha=map_key == MAP_BASE_COLOR,
            )

            material_bake_infos = (
                ()
                if pureplay_ao_disabled and map_key == MAP_AO
                else material_infos.values()
            )
            if pureplay_ao_disabled and map_key == MAP_AO:
                report(
                    {"INFO"},
                    "PurePlay AO Map is off; ORD red is filled with white.",
                )
            for info in material_bake_infos:
                section = sections[info.material.name_full]
                current_object = create_bake_plane(
                    section,
                    info.material.name,
                    OUTPUT_UV_NAME,
                    collection=bake_scene.collection,
                )
                try:
                    current_mesh = current_object.data
                    current_node_groups = []
                    current_material = prepare_bake_material(
                        info.material,
                        map_key,
                        owned_node_groups=current_node_groups,
                    )
                    add_bake_target_node(current_material, current_image)
                    current_mesh.materials.clear()
                    current_mesh.materials.append(current_material)

                    current_mesh.uv_layers.active = current_mesh.uv_layers.get(
                        OUTPUT_UV_NAME
                    )
                    try:
                        current_mesh.uv_layers.active_render = (
                            current_mesh.uv_layers.active
                        )
                    except Exception:
                        pass

                    bake_type = bake_type_for_map(map_key)
                    if bake_type == "NORMAL":
                        bake_scene.render.bake.normal_space = "TANGENT"
                    report(
                        {"INFO"},
                        f"Baking {definition['label']}: {info.label}",
                    )
                    try:
                        run_isolated_bake(
                            context,
                            bake_scene,
                            current_object,
                            bake_type,
                        )
                    except Exception:
                        if (
                            settings.cycles_use_gpu
                            and getattr(bake_scene.cycles, "device", "CPU") == "GPU"
                        ):
                            bake_scene.cycles.device = "CPU"
                            report(
                                {"WARNING"},
                                "GPU bake failed; retrying the current map on CPU.",
                            )
                            run_isolated_bake(
                                context,
                                bake_scene,
                                current_object,
                                bake_type,
                            )
                        else:
                            raise
                finally:
                    if current_object is not None:
                        try:
                            bpy.data.objects.remove(current_object, do_unlink=True)
                        except Exception:
                            pass
                        current_object = None
                    if current_mesh is not None and current_mesh.users == 0:
                        try:
                            bpy.data.meshes.remove(current_mesh)
                        except Exception:
                            pass
                        current_mesh = None
                    if (
                        current_material is not None
                        and current_material.users == 0
                    ):
                        try:
                            bpy.data.materials.remove(current_material)
                        except Exception:
                            pass
                        current_material = None
                    for node_group in reversed(current_node_groups):
                        if node_group.users == 0:
                            try:
                                bpy.data.node_groups.remove(node_group)
                            except Exception:
                                pass
                    current_node_groups = []

                current_image.update()

            target_path = (
                opacity_path
                if map_key == MAP_OPACITY
                else staged_paths.get(map_key) or component_paths.get(map_key)
            )
            if not target_path:
                raise RuntimeError(
                    f"No staged output was assigned for {definition['label']}."
                )
            save_image(current_image, target_path, color_mode="RGB")
            if current_image.users == 0:
                bpy.data.images.remove(current_image)
            current_image = None

        if has_transparency:
            compose_base_color_alpha(
                staged_paths[MAP_BASE_COLOR],
                opacity_path,
                run_id,
            )
        if MAP_PUREPLAY_ORD in map_keys:
            compose_pureplay_ord(
                component_paths[MAP_AO],
                component_paths[MAP_ROUGHNESS],
                component_paths[MAP_DISPLACEMENT],
                staged_paths[MAP_PUREPLAY_ORD],
                run_id,
            )

        # Reload the compact on-disk results only after every sequential bake
        # target has been released.
        for map_key in map_keys:
            definition = MAP_DEFS[map_key]
            image = bpy.data.images.load(
                staged_paths[map_key],
                check_existing=False,
            )
            image.name = (
                f"BakeAddon_{run_id}_{image_prefix}_"
                f"{definition['suffix']}"
            )
            if map_key == MAP_BASE_COLOR and has_transparency:
                image.alpha_mode = "STRAIGHT"
            set_image_colorspace(image, definition["colorspace"])
            images[map_key] = image

        success = True

    finally:
        if current_object is not None:
            try:
                bpy.data.objects.remove(current_object, do_unlink=True)
            except Exception:
                pass
        if current_mesh is not None and current_mesh.users == 0:
            try:
                bpy.data.meshes.remove(current_mesh)
            except Exception:
                pass
        if current_material is not None and current_material.users == 0:
            try:
                bpy.data.materials.remove(current_material)
            except Exception:
                pass
        for node_group in reversed(current_node_groups):
            if node_group.users == 0:
                try:
                    bpy.data.node_groups.remove(node_group)
                except Exception:
                    pass
        if current_image is not None and current_image.users == 0:
            try:
                bpy.data.images.remove(current_image)
            except Exception:
                pass
        try:
            bpy.data.scenes.remove(bake_scene)
        except Exception:
            pass
        if opacity_path and os.path.exists(opacity_path):
            try:
                os.remove(opacity_path)
            except OSError:
                pass
        for component_path in component_paths.values():
            if os.path.exists(component_path):
                try:
                    os.remove(component_path)
                except OSError:
                    pass
        if not success:
            for image in list(images.values()):
                if image and image.users == 0:
                    try:
                        bpy.data.images.remove(image)
                    except Exception:
                        pass

    return images


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
        return t, p, q
    return v, p, q


def store_sections_on_material(material, sections):
    if not material or not hasattr(material, "bakeaddon_trim_sections"):
        return
    try:
        material.bakeaddon_trim_sections.clear()
        ordered = sorted(sections.values(), key=lambda section: (section.y, section.x))
        total = max(1, len(ordered))
        for index, section in enumerate(ordered):
            trim = material.bakeaddon_trim_sections.add()
            trim.name = section.material_name
            trim.uuid = section.material_name
            trim.left = section.u0
            trim.bottom = section.v0
            trim.right = section.u1
            trim.top = section.v1
            r, g, b = hsv_to_rgb(index / total, 0.65, 0.92)
            trim.color = (r, g, b, 0.5)
        material.bakeaddon_trim_index = 0 if ordered else -1
    except Exception:
        pass


def connect_scalar_map(
    nodes,
    links,
    tex_node,
    bsdf,
    input_name,
    channel_name="Red",
):
    separate = nodes.new("ShaderNodeSeparateColor")
    separate.location = (tex_node.location.x + 230, tex_node.location.y - 40)
    links.new(tex_node.outputs["Color"], separate.inputs["Color"])
    output = separate.outputs.get(channel_name) or separate.outputs[0]
    links.new(output, bsdf.inputs[input_name])


def create_trim_material(
    map_images,
    uv_map_name,
    sections,
    normal_map_format="OPENGL",
    has_transparency=False,
    material_name="TrimSheet",
    run_id="",
):
    material = bpy.data.materials.new(
        str(material_name).strip() or "TrimSheet"
    )
    if run_id:
        material[RUN_TAG] = run_id
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    bsdf = nodes.get("Principled BSDF") or nodes.new("ShaderNodeBsdfPrincipled")
    uv_node = nodes.new("ShaderNodeUVMap")
    uv_node.name = "BakeAddon Trim UV"
    uv_node.label = "BakeAddon Trim UV"
    uv_node.uv_map = uv_map_name
    uv_node.location = (-780, 120)

    def add_texture_node(map_key, y):
        image = map_images.get(map_key)
        if not image:
            return None
        tex = nodes.new("ShaderNodeTexImage")
        tex.name = f"BakeAddon {MAP_DEFS[map_key]['label']}"
        tex.label = f"BakeAddon {MAP_DEFS[map_key]['label']}"
        tex.image = image
        tex.extension = "CLIP"
        tex.interpolation = "Smart"
        tex.location = (-540, y)
        links.new(uv_node.outputs["UV"], tex.inputs["Vector"])
        return tex

    base_tex = add_texture_node(MAP_BASE_COLOR, 220)
    if base_tex and "Base Color" in bsdf.inputs:
        links.new(base_tex.outputs["Color"], bsdf.inputs["Base Color"])
        if "Alpha" in bsdf.inputs:
            if has_transparency and "Alpha" in base_tex.outputs:
                links.new(base_tex.outputs["Alpha"], bsdf.inputs["Alpha"])
            else:
                bsdf.inputs["Alpha"].default_value = 1.0
        if has_transparency:
            if hasattr(material, "surface_render_method"):
                material.surface_render_method = "DITHERED"
            elif hasattr(material, "blend_method"):
                material.blend_method = "HASHED"
        elif hasattr(material, "blend_method"):
            material.blend_method = "OPAQUE"

    roughness_tex = add_texture_node(MAP_ROUGHNESS, -10)
    if roughness_tex and "Roughness" in bsdf.inputs:
        connect_scalar_map(nodes, links, roughness_tex, bsdf, "Roughness")

    ord_tex = add_texture_node(MAP_PUREPLAY_ORD, -10)
    if ord_tex:
        ord_tex.label = (
            "BakeAddon PurePlay ORD "
            "(R=AO, G=Roughness, B=Displacement)"
        )
        if "Roughness" in bsdf.inputs:
            connect_scalar_map(
                nodes,
                links,
                ord_tex,
                bsdf,
                "Roughness",
                channel_name="Green",
            )

    metallic_tex = add_texture_node(MAP_METALLIC, -240)
    if metallic_tex and "Metallic" in bsdf.inputs:
        connect_scalar_map(nodes, links, metallic_tex, bsdf, "Metallic")

    normal_tex = add_texture_node(MAP_NORMAL, -470)
    if normal_tex and "Normal" in bsdf.inputs:
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.location = (-250, -470)
        normal_map.space = "TANGENT"
        normal_color_output = normal_tex.outputs["Color"]
        if normal_map_format == "DIRECTX":
            separate = nodes.new("ShaderNodeSeparateColor")
            separate.location = (-250, -560)
            invert_green = nodes.new("ShaderNodeMath")
            invert_green.operation = "SUBTRACT"
            invert_green.inputs[0].default_value = 1.0
            invert_green.location = (-40, -600)
            combine = nodes.new("ShaderNodeCombineColor")
            combine.location = (140, -560)
            links.new(normal_tex.outputs["Color"], separate.inputs["Color"])
            links.new(separate.outputs["Red"], combine.inputs["Red"])
            links.new(separate.outputs["Green"], invert_green.inputs[1])
            links.new(invert_green.outputs["Value"], combine.inputs["Green"])
            links.new(separate.outputs["Blue"], combine.inputs["Blue"])
            normal_color_output = combine.outputs["Color"]
        links.new(normal_color_output, normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])

    ao_tex = add_texture_node(MAP_AO, -700)
    if ao_tex:
        ao_tex.label = "BakeAddon AO (Export Map - Principled has no AO input)"
    store_sections_on_material(material, sections)
    return material


def material_for_polygon(mesh, polygon):
    if polygon.material_index < len(mesh.materials):
        return mesh.materials[polygon.material_index]
    return None


def uv_tile_bounds(face_uvs, epsilon):
    min_u = min(uv.x for uv in face_uvs)
    max_u = max(uv.x for uv in face_uvs)
    min_v = min(uv.y for uv in face_uvs)
    max_v = max(uv.y for uv in face_uvs)
    start_u = math.floor(min_u + epsilon)
    end_u = max(start_u + 1, math.ceil(max_u - epsilon))
    start_v = math.floor(min_v + epsilon)
    end_v = max(start_v + 1, math.ceil(max_v - epsilon))
    return start_u, end_u, start_v, end_v


def polygon_requires_uv_cut(face_uvs, epsilon):
    start_u, end_u, start_v, end_v = uv_tile_bounds(face_uvs, epsilon)
    return (end_u - start_u) > 1 or (end_v - start_v) > 1


def object_material_has_cut_faces(obj, material, epsilon):
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return False
    for polygon in mesh.polygons:
        if material_for_polygon(mesh, polygon) != material:
            continue
        face_uvs = [
            uv_layer.data[loop_index].uv
            for loop_index in polygon.loop_indices
        ]
        if polygon_requires_uv_cut(face_uvs, epsilon):
            return True
    return False


def material_mapping_warnings(material, objects, epsilon):
    if not any(
        object_material_has_cut_faces(obj, material, epsilon)
        for obj in objects
    ):
        return []

    warnings = []
    uv_names = set()
    non_uv_coordinates = set()
    unsafe_mapping = False
    nonlinear = False
    non_repeat = False

    for node in iter_nodes_recursive(material):
        if node.bl_idname == "ShaderNodeUVMap":
            uv_names.add(getattr(node, "uv_map", "") or "<active>")
        elif node.bl_idname == "ShaderNodeTexCoord":
            for output in node.outputs:
                if output.name != "UV" and output.is_linked:
                    non_uv_coordinates.add(output.name)
        elif node.bl_idname == "ShaderNodeMapping":
            scale = node.inputs.get("Scale")
            rotation = node.inputs.get("Rotation")
            if (
                (scale and scale.is_linked)
                or (rotation and rotation.is_linked)
            ):
                unsafe_mapping = True
                continue
            try:
                scale_values = tuple(float(value) for value in scale.default_value)
                rotation_values = tuple(float(value) for value in rotation.default_value)
                integer_xy = all(
                    abs(value - round(value)) <= 1e-6
                    for value in scale_values[:2]
                )
                no_rotation = all(
                    abs(value) <= 1e-6
                    for value in rotation_values
                )
                if not integer_xy or not no_rotation:
                    unsafe_mapping = True
            except Exception:
                unsafe_mapping = True
        elif node.bl_idname == "ShaderNodeVectorMath":
            nonlinear = True
        elif node.bl_idname == "ShaderNodeTexImage":
            if getattr(node, "extension", "REPEAT") != "REPEAT":
                non_repeat = True

    if len(uv_names) > 1:
        warnings.append(
            f"{material.name_full}: multiple named UV inputs are approximated "
            "with the normalized source tile"
        )
    if non_uv_coordinates:
        warnings.append(
            f"{material.name_full}: {', '.join(sorted(non_uv_coordinates))} "
            "coordinates are geometry-dependent and may differ in the trim bake"
        )
    if unsafe_mapping:
        warnings.append(
            f"{material.name_full}: non-periodic Mapping combined with tiled "
            "mesh UVs may change phase between tiles"
        )
    if nonlinear:
        warnings.append(
            f"{material.name_full}: nonlinear Vector Math combined with tiled "
            "mesh UVs is baked best-effort"
        )
    if non_repeat:
        warnings.append(
            f"{material.name_full}: non-Repeat image extension combined with "
            "tiled mesh UVs is baked best-effort"
        )
    return warnings


def validate_trim_inputs(objects, settings, map_keys):
    errors = []
    warnings = []
    estimated_pieces = 0
    epsilon = float(settings.uv_epsilon)
    max_axis = int(settings.max_tiles_per_axis)

    if not settings.bake_base_color:
        for material in used_materials_in_order(objects):
            if material_uses_transparency(material):
                errors.append(
                    "Transparent materials require Base Color baking so alpha "
                    "can be embedded."
                )
                break

    for obj in objects:
        mesh = obj.data
        uv_layer = mesh.uv_layers.active
        if uv_layer is None:
            errors.append(f"{obj.name}: active UV map is missing")
            continue
        if mesh.shape_keys and len(mesh.shape_keys.key_blocks):
            errors.append(f"{obj.name}: shape keys must be removed or applied first")
        for modifier in obj.modifiers:
            is_bound = bool(
                getattr(modifier, "is_bound", False)
                or getattr(modifier, "is_bind", False)
            )
            if modifier.type == "MULTIRES" or is_bound:
                errors.append(
                    f"{obj.name}: {modifier.name} contains topology-bound data"
                )

        used_indices = {poly.material_index for poly in mesh.polygons}
        for material_index in used_indices:
            if (
                material_index >= len(mesh.materials)
                or mesh.materials[material_index] is None
            ):
                errors.append(
                    f"{obj.name}: used material slot {material_index + 1} is empty"
                )

        for polygon in mesh.polygons:
            face_uvs = [
                uv_layer.data[loop_index].uv
                for loop_index in polygon.loop_indices
            ]
            start_u, end_u, start_v, end_v = uv_tile_bounds(
                face_uvs,
                epsilon,
            )
            tiles_u = end_u - start_u
            tiles_v = end_v - start_v
            if tiles_u > max_axis or tiles_v > max_axis:
                errors.append(
                    f"{obj.name}: face {polygon.index} spans {tiles_u}×{tiles_v} "
                    f"tiles, above Max Tiles {max_axis}"
                )
            estimated_pieces += max(1, tiles_u * tiles_v)

        for attribute in mesh.attributes:
            if attribute.domain not in {"POINT", "EDGE", "FACE", "CORNER"}:
                errors.append(
                    f"{obj.name}: attribute '{attribute.name}' uses unsupported "
                    f"domain {attribute.domain}"
                )

    if estimated_pieces > MAX_GENERATED_PIECES:
        errors.append(
            f"Estimated generated pieces ({estimated_pieces:,}) exceed the "
            f"safety limit ({MAX_GENERATED_PIECES:,})."
        )

    for material in used_materials_in_order(objects):
        warnings.extend(material_mapping_warnings(material, objects, epsilon))

    return errors, warnings, estimated_pieces


def poly_area_2d(poly):
    if len(poly) < 3:
        return 0.0
    area = 0.0
    for index, point in enumerate(poly):
        next_point = poly[(index + 1) % len(poly)]
        area += point.x * next_point.y - next_point.x * point.y
    return area * 0.5


def remove_duplicate_uvs(poly, eps=1e-7):
    if not poly:
        return []
    cleaned = []
    for point in poly:
        if not cleaned or (point - cleaned[-1]).length > eps:
            cleaned.append(point)
    if len(cleaned) > 1 and (cleaned[0] - cleaned[-1]).length <= eps:
        cleaned.pop()
    return cleaned


def clip_poly_axis(poly, axis, value, keep_greater, eps=1e-9):
    if not poly:
        return []

    def inside(point):
        coord = point.x if axis == 0 else point.y
        return coord >= value - eps if keep_greater else coord <= value + eps

    def intersect(a, b):
        ac = a.x if axis == 0 else a.y
        bc = b.x if axis == 0 else b.y
        denom = bc - ac
        if abs(denom) < eps:
            return Vector((a.x, a.y))
        t = (value - ac) / denom
        return Vector((a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t))

    result = []
    previous = poly[-1]
    previous_inside = inside(previous)
    for current in poly:
        current_inside = inside(current)
        if current_inside:
            if not previous_inside:
                result.append(intersect(previous, current))
            result.append(Vector((current.x, current.y)))
        elif previous_inside:
            result.append(intersect(previous, current))
        previous = current
        previous_inside = current_inside
    return remove_duplicate_uvs(result)


def clip_poly_rect(poly, u0, v0, u1, v1, eps=1e-9):
    out = [Vector((point.x, point.y)) for point in poly]
    out = clip_poly_axis(out, 0, u0, True, eps)
    out = clip_poly_axis(out, 0, u1, False, eps)
    out = clip_poly_axis(out, 1, v0, True, eps)
    out = clip_poly_axis(out, 1, v1, False, eps)
    return remove_duplicate_uvs(out)


def barycentric_2d(point, a, b, c, eps=1e-12):
    denominator = ((b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y))
    if abs(denominator) < eps:
        return None
    w0 = ((b.y - c.y) * (point.x - c.x) + (c.x - b.x) * (point.y - c.y)) / denominator
    w1 = ((c.y - a.y) * (point.x - c.x) + (a.x - c.x) * (point.y - c.y)) / denominator
    w2 = 1.0 - w0 - w1
    return w0, w1, w2


def uv_to_3d(point, face_uvs, face_coords, eps=1e-5):
    count = len(face_uvs)
    if count == 3:
        weights = barycentric_2d(point, face_uvs[0], face_uvs[1], face_uvs[2])
        if weights:
            w0, w1, w2 = weights
            return face_coords[0] * w0 + face_coords[1] * w1 + face_coords[2] * w2

    best = None
    best_score = -1e18
    for index in range(1, count - 1):
        weights = barycentric_2d(point, face_uvs[0], face_uvs[index], face_uvs[index + 1])
        if not weights:
            continue
        w0, w1, w2 = weights
        min_weight = min(w0, w1, w2)
        if min_weight >= -eps:
            return face_coords[0] * w0 + face_coords[index] * w1 + face_coords[index + 1] * w2
        if min_weight > best_score:
            best_score = min_weight
            best = face_coords[0] * w0 + face_coords[index] * w1 + face_coords[index + 1] * w2

    if best is not None:
        return best
    nearest = min(range(count), key=lambda index: (face_uvs[index] - point).length_squared)
    return face_coords[nearest].copy()


def normal_from_coords(coords):
    if len(coords) < 3:
        return Vector((0.0, 0.0, 1.0))
    normal = Vector((0.0, 0.0, 0.0))
    for index, coord in enumerate(coords):
        next_coord = coords[(index + 1) % len(coords)]
        normal.x += (coord.y - next_coord.y) * (coord.z + next_coord.z)
        normal.y += (coord.z - next_coord.z) * (coord.x + next_coord.x)
        normal.z += (coord.x - next_coord.x) * (coord.y + next_coord.y)
    if normal.length_squared > 1e-14:
        normal.normalize()
    return normal


def remap_uv_to_section(old_uv, cell_u0, cell_v0, section):
    local_u = max(0.0, min(1.0, old_uv.x - cell_u0))
    local_v = max(0.0, min(1.0, old_uv.y - cell_v0))
    return Vector((
        section.u0 + local_u * (section.u1 - section.u0),
        section.v0 + local_v * (section.v1 - section.v0),
    ))


def remap_fractional_uv_to_section(old_uv, section):
    frac_u = old_uv.x - math.floor(old_uv.x)
    frac_v = old_uv.y - math.floor(old_uv.y)
    return Vector((
        section.u0 + frac_u * (section.u1 - section.u0),
        section.v0 + frac_v * (section.v1 - section.v0),
    ))


def source_material_attribute(mesh, material_indices):
    existing = mesh.attributes.get(SOURCE_MATERIAL_ATTRIBUTE_NAME)
    if existing and (existing.data_type != "INT" or existing.domain != "FACE"):
        mesh.attributes.remove(existing)
        existing = None
    attribute = existing or mesh.attributes.new(
        name=SOURCE_MATERIAL_ATTRIBUTE_NAME,
        type="INT",
        domain="FACE",
    )
    for index, material_index in enumerate(material_indices):
        attribute.data[index].value = int(material_index)


def assign_generated_material(mesh, atlas_material, source_materials, settings):
    mesh.materials.clear()
    if settings.apply_generated_material:
        mesh.materials.append(atlas_material)
        for polygon in mesh.polygons:
            polygon.material_index = 0
    else:
        for material in source_materials:
            mesh.materials.append(material)


def create_single_output_uv_layer(mesh):
    while len(mesh.uv_layers):
        mesh.uv_layers.remove(mesh.uv_layers[-1])
    trim_layer = mesh.uv_layers.new(name=OUTPUT_UV_NAME)
    if trim_layer.name != OUTPUT_UV_NAME:
        raise RuntimeError(
            f"Could not create the required '{OUTPUT_UV_NAME}' UV channel."
        )
    mesh.uv_layers.active = trim_layer
    try:
        mesh.uv_layers.active_render = trim_layer
    except Exception:
        pass
    return trim_layer


def assert_single_output_uv_layer(mesh):
    names = [layer.name for layer in mesh.uv_layers]
    active = mesh.uv_layers.active
    try:
        active_render = mesh.uv_layers.active_render
    except Exception:
        active_render = active
    if (
        names != [OUTPUT_UV_NAME]
        or active is None
        or active.name != OUTPUT_UV_NAME
        or active_render is None
        or active_render.name != OUTPUT_UV_NAME
    ):
        raise RuntimeError(
            "Generated trim mesh must contain exactly one active/render UV "
            f"channel named '{OUTPUT_UV_NAME}'."
        )


def direct_remapped_mesh(obj, atlas_material, sections, settings):
    source = obj.data
    source_uv = source.uv_layers.active
    result = source.copy()
    result.name = clean_generated_mesh_name(source.name)
    source_materials = list(source.materials)
    material_indices = [polygon.material_index for polygon in source.polygons]
    trim_layer = create_single_output_uv_layer(result)

    epsilon = float(settings.uv_epsilon)
    for polygon in source.polygons:
        material = material_for_polygon(source, polygon)
        section = sections.get(material.name_full if material else "")
        if section is None:
            raise RuntimeError(
                f"{obj.name}: face {polygon.index} has no atlas section."
            )
        face_uvs = [
            source_uv.data[loop_index].uv
            for loop_index in polygon.loop_indices
        ]
        start_u, _end_u, start_v, _end_v = uv_tile_bounds(
            face_uvs,
            epsilon,
        )
        for loop_index in polygon.loop_indices:
            trim_layer.data[loop_index].uv = remap_uv_to_section(
                source_uv.data[loop_index].uv,
                start_u,
                start_v,
                section,
            )

    source_material_attribute(result, material_indices)
    assign_generated_material(
        result,
        atlas_material,
        source_materials,
        settings,
    )
    result.update()
    assert_single_output_uv_layer(result)
    return MeshBuildResult(
        mesh=result,
        trim_uv_name=OUTPUT_UV_NAME,
        processed_faces=0,
        created_pieces=0,
        skipped_faces=0,
        topology_changed=False,
        vertex_group_weights=capture_object_vertex_group_weights(obj),
    )


def loop_triangles_by_polygon(mesh):
    mesh.calc_loop_triangles()
    result = {}
    for triangle in mesh.loop_triangles:
        result.setdefault(triangle.polygon_index, []).append(
            tuple(triangle.loops)
        )
    return result


def point_segment_parameter(point, first, second, epsilon):
    direction = second - first
    length_squared = direction.length_squared
    if length_squared <= epsilon * epsilon:
        return None
    parameter = (point - first).dot(direction) / length_squared
    if parameter < -epsilon or parameter > 1.0 + epsilon:
        return None
    closest = first + direction * parameter
    if (closest - point).length > epsilon:
        return None
    return max(0.0, min(1.0, parameter))


def sample_source_at_uv(
    mesh,
    polygon,
    point,
    active_uv_layer,
    triangle_loops,
    epsilon=1e-6,
):
    polygon_loops = list(polygon.loop_indices)
    for loop_index in polygon_loops:
        uv = active_uv_layer.data[loop_index].uv
        if (uv - point).length <= epsilon:
            vertex_index = mesh.loops[loop_index].vertex_index
            return {
                "coord": mesh.vertices[vertex_index].co.copy(),
                "source_poly": polygon.index,
                "loops": (loop_index,),
                "weights": (1.0,),
                "source_loop": loop_index,
                "source_vertex": vertex_index,
                "source_edge": None,
                "active_uv": point.copy(),
                "provenance": ("VERT", vertex_index),
            }

    best = None
    best_score = -1e30
    for loops in triangle_loops:
        uvs = [active_uv_layer.data[index].uv for index in loops]
        weights = barycentric_2d(point, uvs[0], uvs[1], uvs[2])
        if weights is None:
            continue
        score = min(weights)
        if score > best_score:
            best_score = score
            best = (loops, weights)
        if score >= -epsilon:
            best = (loops, weights)
            break
    if best is None:
        nearest_loop = min(
            polygon_loops,
            key=lambda index: (
                active_uv_layer.data[index].uv - point
            ).length_squared,
        )
        best = ((nearest_loop,), (1.0,))

    loops, weights = best
    coordinate = Vector((0.0, 0.0, 0.0))
    for loop_index, weight in zip(loops, weights):
        vertex_index = mesh.loops[loop_index].vertex_index
        coordinate += mesh.vertices[vertex_index].co * weight

    source_edge = None
    edge_parameter = None
    for index, loop_index in enumerate(polygon_loops):
        next_loop = polygon_loops[(index + 1) % len(polygon_loops)]
        first_uv = active_uv_layer.data[loop_index].uv
        second_uv = active_uv_layer.data[next_loop].uv
        parameter = point_segment_parameter(
            point,
            first_uv,
            second_uv,
            epsilon * 10.0,
        )
        if parameter is None:
            continue
        source_edge = mesh.loops[loop_index].edge_index
        first_vertex = mesh.loops[loop_index].vertex_index
        second_vertex = mesh.loops[next_loop].vertex_index
        edge_parameter = (
            parameter
            if first_vertex <= second_vertex
            else 1.0 - parameter
        )
        break

    if source_edge is not None:
        provenance = (
            "EDGE",
            source_edge,
            round(edge_parameter, 8),
        )
    else:
        provenance = (
            "FACE",
            polygon.index,
            round(float(point.x), 8),
            round(float(point.y), 8),
        )
    dominant = max(range(len(weights)), key=lambda index: weights[index])
    return {
        "coord": coordinate,
        "source_poly": polygon.index,
        "loops": tuple(loops),
        "weights": tuple(weights),
        "source_loop": loops[dominant],
        "source_vertex": None,
        "source_edge": source_edge,
        "active_uv": point.copy(),
        "provenance": provenance,
    }


def sample_vertex_weights(mesh, sample):
    if sample["source_vertex"] is not None:
        return {sample["source_vertex"]: 1.0}
    result = {}
    for loop_index, weight in zip(sample["loops"], sample["weights"]):
        vertex_index = mesh.loops[loop_index].vertex_index
        result[vertex_index] = result.get(vertex_index, 0.0) + float(weight)
    return result


def interpolate_values(values, weights):
    first = values[0]
    if isinstance(first, (float, int)):
        return sum(float(value) * weight for value, weight in zip(values, weights))
    try:
        size = len(first)
    except Exception:
        return first
    return tuple(
        sum(float(value[index]) * weight for value, weight in zip(values, weights))
        for index in range(size)
    )


def attribute_value(item):
    for field in ("value", "vector", "color"):
        if hasattr(item, field):
            value = getattr(item, field)
            try:
                return field, value.copy()
            except Exception:
                try:
                    return field, tuple(value)
                except Exception:
                    return field, value
    raise RuntimeError("Unsupported mesh attribute storage.")


def assign_attribute_value(item, field, value):
    setattr(item, field, value)


def sampled_attribute_value(attribute, indices, weights):
    dominant = max(range(len(weights)), key=lambda index: weights[index])
    field, nearest = attribute_value(attribute.data[indices[dominant]])
    if attribute.data_type in {
        "FLOAT",
        "FLOAT_VECTOR",
        "FLOAT_COLOR",
        "BYTE_COLOR",
        "FLOAT2",
    }:
        values = [attribute_value(attribute.data[index])[1] for index in indices]
        return field, interpolate_values(values, weights)
    return field, nearest


def copy_generic_attributes(
    source,
    result,
    vertex_samples,
    face_records,
    edge_sources,
):
    uv_names = {layer.name for layer in source.uv_layers}
    for attribute in source.attributes:
        if (
            attribute.name in uv_names
            or attribute.name == SOURCE_MATERIAL_ATTRIBUTE_NAME
            or attribute.is_required
        ):
            continue
        try:
            target = result.attributes.new(
                name=attribute.name,
                type=attribute.data_type,
                domain=attribute.domain,
            )
        except Exception as exc:
            if attribute.is_internal:
                continue
            raise RuntimeError(
                f"Could not preserve attribute '{attribute.name}'."
            ) from exc

        try:
            if attribute.domain == "POINT":
                for index, sample in enumerate(vertex_samples):
                    contributors = sample_vertex_weights(source, sample)
                    indices = tuple(contributors)
                    weights = tuple(contributors.values())
                    field, value = sampled_attribute_value(
                        attribute,
                        indices,
                        weights,
                    )
                    assign_attribute_value(target.data[index], field, value)
            elif attribute.domain == "FACE":
                for index, record in enumerate(face_records):
                    field, value = attribute_value(
                        attribute.data[record["source_poly"]]
                    )
                    assign_attribute_value(target.data[index], field, value)
            elif attribute.domain == "CORNER":
                for polygon, record in zip(result.polygons, face_records):
                    for loop_index, sample in zip(
                        polygon.loop_indices,
                        record["samples"],
                    ):
                        field, value = sampled_attribute_value(
                            attribute,
                            sample["loops"],
                            sample["weights"],
                        )
                        assign_attribute_value(
                            target.data[loop_index],
                            field,
                            value,
                        )
            elif attribute.domain == "EDGE":
                for edge in result.edges:
                    source_edge = edge_sources.get(edge.index)
                    if source_edge is None:
                        continue
                    field, value = attribute_value(
                        attribute.data[source_edge]
                    )
                    assign_attribute_value(target.data[edge.index], field, value)
        except Exception as exc:
            raise RuntimeError(
                f"Could not transfer attribute '{attribute.name}'."
            ) from exc


def create_cut_trim_uv_layer(
    source,
    result,
    face_records,
):
    source_active = source.uv_layers.active
    trim_layer = create_single_output_uv_layer(result)

    for polygon, record in zip(result.polygons, face_records):
        for target_loop, sample in zip(
            polygon.loop_indices,
            record["samples"],
        ):
            dominant_loop = sample["source_loop"]
            trim_layer.data[target_loop].uv = sample["trim_uv"]
            source_item = source_active.data[dominant_loop]
            target_item = trim_layer.data[target_loop]
            for field in ("pin_uv", "select", "select_edge"):
                if hasattr(source_item, field) and hasattr(target_item, field):
                    setattr(target_item, field, getattr(source_item, field))
    return trim_layer


def build_vertex_group_weights(obj, vertex_samples):
    source = obj.data
    source_weights = []
    for vertex in source.vertices:
        source_weights.append(
            {element.group: element.weight for element in vertex.groups}
        )
    result = {}
    for vertex_index, sample in enumerate(vertex_samples):
        contributors = sample_vertex_weights(source, sample)
        group_weights = {}
        for source_vertex, factor in contributors.items():
            for group_index, weight in source_weights[source_vertex].items():
                group_weights[group_index] = (
                    group_weights.get(group_index, 0.0)
                    + float(weight) * float(factor)
                )
        for group_index, weight in group_weights.items():
            if weight > 1e-8:
                result.setdefault(group_index, {})[vertex_index] = max(
                    0.0,
                    min(1.0, weight),
                )
    return result


def source_edge_for_result_edge(
    source,
    result_edge,
    vertex_samples,
    edge_lookup,
):
    first_sample = vertex_samples[result_edge.vertices[0]]
    second_sample = vertex_samples[result_edge.vertices[1]]
    first_vertex = first_sample["source_vertex"]
    second_vertex = second_sample["source_vertex"]

    if first_vertex is not None and second_vertex is not None:
        source_edge = edge_lookup.get(
            tuple(sorted((first_vertex, second_vertex)))
        )
        if source_edge is not None:
            return source_edge

    candidates = {
        sample["source_edge"]
        for sample in (first_sample, second_sample)
        if sample["source_edge"] is not None
    }
    for edge_index in candidates:
        edge_vertices = set(source.edges[edge_index].vertices)
        compatible = True
        for sample in (first_sample, second_sample):
            if sample["source_edge"] == edge_index:
                continue
            if (
                sample["source_vertex"] is None
                or sample["source_vertex"] not in edge_vertices
            ):
                compatible = False
                break
        if compatible:
            return edge_index
    return None


def cut_remapped_mesh(obj, atlas_material, sections, settings):
    source = obj.data
    source_uv = source.uv_layers.active
    epsilon = float(settings.uv_epsilon)
    min_area = float(settings.min_piece_area)
    triangle_map = loop_triangles_by_polygon(source)
    source_materials = list(source.materials)

    vertices = [vertex.co.copy() for vertex in source.vertices]
    first_loop_by_vertex = {}
    for loop in source.loops:
        first_loop_by_vertex.setdefault(loop.vertex_index, loop.index)
    vertex_samples = []
    for vertex in source.vertices:
        source_loop = first_loop_by_vertex.get(vertex.index, 0)
        vertex_samples.append(
            {
                "coord": vertex.co.copy(),
                "source_poly": -1,
                "loops": (source_loop,),
                "weights": (1.0,),
                "source_loop": source_loop,
                "source_vertex": vertex.index,
                "source_edge": None,
                "active_uv": Vector((0.0, 0.0)),
                "provenance": ("VERT", vertex.index),
            }
        )
    vertex_cache = {}
    faces = []
    face_records = []
    processed_faces = 0
    created_pieces = 0

    def vertex_index_for_sample(sample):
        if sample["source_vertex"] is not None:
            return sample["source_vertex"]
        key = sample["provenance"] if settings.merge_vertices_translate else None
        if key is not None and key in vertex_cache:
            return vertex_cache[key]
        index = len(vertices)
        vertices.append(sample["coord"].copy())
        vertex_samples.append(sample)
        if key is not None:
            vertex_cache[key] = index
        return index

    for polygon in source.polygons:
        material = material_for_polygon(source, polygon)
        section = sections.get(material.name_full if material else "")
        if section is None:
            raise RuntimeError(
                f"{obj.name}: face {polygon.index} has no atlas section."
            )
        polygon_loops = list(polygon.loop_indices)
        face_uvs = [
            source_uv.data[loop_index].uv.copy()
            for loop_index in polygon_loops
        ]
        start_u, end_u, start_v, end_v = uv_tile_bounds(
            face_uvs,
            epsilon,
        )
        needs_cut = (end_u - start_u) > 1 or (end_v - start_v) > 1

        if not needs_cut:
            samples = []
            for loop_index in polygon_loops:
                vertex_index = source.loops[loop_index].vertex_index
                samples.append(
                    {
                        "coord": source.vertices[vertex_index].co.copy(),
                        "source_poly": polygon.index,
                        "loops": (loop_index,),
                        "weights": (1.0,),
                        "source_loop": loop_index,
                        "source_vertex": vertex_index,
                        "source_edge": None,
                        "active_uv": source_uv.data[loop_index].uv.copy(),
                        "trim_uv": remap_uv_to_section(
                            source_uv.data[loop_index].uv,
                            start_u,
                            start_v,
                            section,
                        ),
                        "provenance": ("VERT", vertex_index),
                    }
                )
            faces.append([sample["source_vertex"] for sample in samples])
            face_records.append(
                {"source_poly": polygon.index, "samples": samples}
            )
            continue

        if settings.triangulate_ngons and len(polygon_loops) >= 4:
            source_pieces = [
                [source_uv.data[index].uv.copy() for index in loops]
                for loops in triangle_map.get(polygon.index, ())
            ]
        else:
            source_pieces = [face_uvs]

        face_piece_count = 0
        for source_piece in source_pieces:
            for tile_u in range(start_u, end_u):
                for tile_v in range(start_v, end_v):
                    clipped = clip_poly_rect(
                        source_piece,
                        tile_u,
                        tile_v,
                        tile_u + 1.0,
                        tile_v + 1.0,
                        eps=epsilon,
                    )
                    if (
                        len(clipped) < 3
                        or abs(poly_area_2d(clipped)) < min_area
                    ):
                        continue
                    samples = []
                    for point in clipped:
                        sample = sample_source_at_uv(
                            source,
                            polygon,
                            point,
                            source_uv,
                            triangle_map.get(polygon.index, ()),
                        )
                        sample["trim_uv"] = remap_uv_to_section(
                            point,
                            tile_u,
                            tile_v,
                            section,
                        )
                        samples.append(sample)
                    coords = [sample["coord"] for sample in samples]
                    if normal_from_coords(coords).dot(polygon.normal) < 0.0:
                        samples.reverse()
                    indices = [vertex_index_for_sample(sample) for sample in samples]
                    cleaned_indices = []
                    cleaned_samples = []
                    for index, sample in zip(indices, samples):
                        if cleaned_indices and cleaned_indices[-1] == index:
                            continue
                        cleaned_indices.append(index)
                        cleaned_samples.append(sample)
                    if (
                        len(cleaned_indices) > 1
                        and cleaned_indices[0] == cleaned_indices[-1]
                    ):
                        cleaned_indices.pop()
                        cleaned_samples.pop()
                    if len(set(cleaned_indices)) < 3:
                        continue
                    faces.append(cleaned_indices)
                    face_records.append(
                        {
                            "source_poly": polygon.index,
                            "samples": cleaned_samples,
                        }
                    )
                    face_piece_count += 1
                    created_pieces += 1
        if face_piece_count == 0:
            raise RuntimeError(
                f"{obj.name}: face {polygon.index} could not be cut reliably."
            )
        processed_faces += 1

    result = bpy.data.meshes.new(clean_generated_mesh_name(source.name))
    result.from_pydata([tuple(vertex) for vertex in vertices], [], faces)
    result.update(calc_edges=True)

    material_indices = []
    for result_polygon, record in zip(result.polygons, face_records):
        source_polygon = source.polygons[record["source_poly"]]
        material_indices.append(source_polygon.material_index)
        result_polygon.use_smooth = source_polygon.use_smooth
        result_polygon.hide = source_polygon.hide
        result_polygon.select = source_polygon.select
        result_polygon.material_index = source_polygon.material_index

    create_cut_trim_uv_layer(
        source,
        result,
        face_records,
    )

    edge_lookup = {
        tuple(sorted(edge.vertices)): edge.index
        for edge in source.edges
    }
    edge_sources = {}
    for edge in result.edges:
        source_edge = source_edge_for_result_edge(
            source,
            edge,
            vertex_samples,
            edge_lookup,
        )
        edge_sources[edge.index] = source_edge
        if source_edge is not None:
            source_item = source.edges[source_edge]
            edge.use_seam = source_item.use_seam
            edge.use_edge_sharp = source_item.use_edge_sharp
            edge.hide = source_item.hide
            edge.select = source_item.select

    for index, vertex in enumerate(result.vertices):
        sample = vertex_samples[index]
        contributors = sample_vertex_weights(source, sample)
        dominant = max(contributors, key=contributors.get)
        vertex.hide = source.vertices[dominant].hide
        vertex.select = source.vertices[dominant].select

    copy_generic_attributes(
        source,
        result,
        vertex_samples,
        face_records,
        edge_sources,
    )

    if source.has_custom_normals:
        source_normals = [
            normal.vector.copy() for normal in source.corner_normals
        ]
        custom_normals = []
        for polygon, record in zip(result.polygons, face_records):
            for _loop_index, sample in zip(
                polygon.loop_indices,
                record["samples"],
            ):
                vector = Vector(
                    interpolate_values(
                        [source_normals[index] for index in sample["loops"]],
                        sample["weights"],
                    )
                )
                if vector.length_squared > 1e-16:
                    vector.normalize()
                custom_normals.append(vector)
        result.normals_split_custom_set(custom_normals)

    try:
        active_color = source.attributes.active_color_name
        if active_color and result.attributes.get(active_color):
            result.attributes.active_color_name = active_color
        render_index = source.attributes.render_color_index
        if render_index >= 0:
            source_render = source.color_attributes[render_index]
            target_render = result.color_attributes.get(source_render.name)
            if target_render:
                result.attributes.render_color_index = list(
                    result.color_attributes
                ).index(target_render)
    except Exception:
        pass

    source_material_attribute(result, material_indices)
    assign_generated_material(
        result,
        atlas_material,
        source_materials,
        settings,
    )
    result.update()
    assert_single_output_uv_layer(result)
    return MeshBuildResult(
        mesh=result,
        trim_uv_name=OUTPUT_UV_NAME,
        processed_faces=processed_faces,
        created_pieces=created_pieces,
        skipped_faces=0,
        topology_changed=True,
        vertex_group_weights=build_vertex_group_weights(
            obj,
            vertex_samples,
        ),
    )


def build_remapped_mesh(obj, atlas_material, sections, settings):
    source = obj.data
    source_uv = source.uv_layers.active
    epsilon = float(settings.uv_epsilon)
    needs_cut = any(
        polygon_requires_uv_cut(
            [
                source_uv.data[loop_index].uv
                for loop_index in polygon.loop_indices
            ],
            epsilon,
        )
        for polygon in source.polygons
    )
    if not needs_cut:
        return direct_remapped_mesh(
            obj,
            atlas_material,
            sections,
            settings,
        )
    return cut_remapped_mesh(
        obj,
        atlas_material,
        sections,
        settings,
    )


def split_blender_numeric_suffix(name):
    match = re.match(r"^(.*?)(\.\d{3})$", name or "")
    if match:
        return match.group(1), match.group(2)
    return name or "", ""


def strip_repeated_suffix(base_name, suffix):
    while base_name.endswith(suffix):
        base_name = base_name[:-len(suffix)]
    return base_name


def clean_backup_base_name(name):
    base_name, numeric_suffix = split_blender_numeric_suffix(name)
    base_name = strip_repeated_suffix(base_name, BACKUP_SUFFIX)
    return (base_name or "Object") + numeric_suffix


def clean_trim_output_base_name(name):
    base_name, numeric_suffix = split_blender_numeric_suffix(name)
    while True:
        cleaned = strip_repeated_suffix(base_name, BACKUP_SUFFIX)
        cleaned = strip_repeated_suffix(cleaned, GENERATED_MESH_SUFFIX)
        if cleaned == base_name:
            break
        base_name = cleaned
    return (base_name or "Object") + numeric_suffix


def clean_generated_mesh_name(name):
    base_name, numeric_suffix = split_blender_numeric_suffix(name)
    base_name = strip_repeated_suffix(base_name, BACKUP_SUFFIX)
    base_name = strip_repeated_suffix(base_name, GENERATED_MESH_SUFFIX)
    return f"{base_name or 'Mesh'}{numeric_suffix}{GENERATED_MESH_SUFFIX}"


def find_layer_collection(layer_collection, collection):
    if layer_collection.collection == collection:
        return layer_collection
    for child in layer_collection.children:
        found = find_layer_collection(child, collection)
        if found:
            return found
    return None


def ensure_backup_collection(context):
    collection = bpy.data.collections.get(BACKUP_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(BACKUP_COLLECTION_NAME)

    scene_collection = context.scene.collection if context and context.scene else bpy.context.scene.collection
    if not any(child == collection for child in scene_collection.children):
        try:
            scene_collection.children.link(collection)
        except RuntimeError:
            pass

    collection.hide_viewport = False
    collection.hide_render = True

    view_layer = context.view_layer if context else bpy.context.view_layer
    layer_collection = find_layer_collection(view_layer.layer_collection, collection)
    if layer_collection:
        layer_collection.hide_viewport = True

    return collection


def link_object_only_to_collection(obj, collection):
    if not any(existing == obj for existing in collection.objects):
        collection.objects.link(obj)
    for existing_collection in list(obj.users_collection):
        if existing_collection != collection:
            existing_collection.objects.unlink(obj)


def object_is_in_collection(obj, collection):
    return bool(obj and collection and any(existing_collection == collection for existing_collection in obj.users_collection))


def get_working_collection(context):
    backup_collection = bpy.data.collections.get(BACKUP_COLLECTION_NAME)
    active_collection = getattr(context, "collection", None)
    if active_collection and active_collection != backup_collection:
        return active_collection
    return context.scene.collection if context and context.scene else bpy.context.scene.collection


def is_backup_object(obj):
    return bool(obj and obj.get(BACKUP_TAG, False))


def duplicate_backup_for_trim_generation(context, obj):
    working_collection = get_working_collection(context)
    source_id = obj.get(SOURCE_ID_TAG) or uuid.uuid4().hex
    obj[SOURCE_ID_TAG] = source_id
    duplicate = obj.copy()
    duplicate.data = obj.data.copy()
    duplicate.name = clean_backup_base_name(obj.name)
    duplicate.data.name = clean_backup_base_name(obj.data.name)
    link_object_only_to_collection(duplicate, working_collection)
    duplicate.hide_viewport = False
    duplicate.hide_render = False
    duplicate[BACKUP_TAG] = False
    duplicate[GENERATED_TAG] = True
    duplicate[SOURCE_ID_TAG] = source_id
    duplicate[BACKUP_OBJECT_TAG] = obj.name
    duplicate.select_set(True)
    return duplicate


def prepare_selected_trim_objects(context, selected_objects):
    backup_collection = ensure_backup_collection(context)
    prepared = []
    skip_backup = set()
    duplicated = 0

    bpy.ops.object.select_all(action="DESELECT")

    for obj in selected_objects:
        if is_backup_object(obj) or object_is_in_collection(obj, backup_collection):
            if not is_backup_object(obj):
                obj.select_set(True)
                prepared.append(obj)
                continue
            obj.select_set(False)
            duplicate = duplicate_backup_for_trim_generation(context, obj)
            prepared.append(duplicate)
            skip_backup.add(duplicate)
            duplicated += 1
        else:
            obj.select_set(True)
            prepared.append(obj)

    if prepared:
        context.view_layer.objects.active = prepared[0]

    return prepared, skip_backup, duplicated


def create_backup_object(context, obj, exclude_objects=None, run_id=""):
    backup_collection = ensure_backup_collection(context)
    source_id = obj.get(SOURCE_ID_TAG) or uuid.uuid4().hex
    obj[SOURCE_ID_TAG] = source_id
    backup = obj.copy()
    backup.data = obj.data.copy()
    backup.name = clean_backup_base_name(obj.name) + BACKUP_SUFFIX
    backup.data.name = clean_backup_base_name(obj.data.name) + BACKUP_SUFFIX
    link_object_only_to_collection(backup, backup_collection)
    backup.hide_viewport = False
    backup.hide_render = False
    backup.select_set(False)
    backup[BACKUP_TAG] = True
    backup[GENERATED_TAG] = False
    backup[SOURCE_ID_TAG] = source_id
    if run_id:
        backup[RUN_TAG] = run_id
    obj[BACKUP_OBJECT_TAG] = backup.name
    return backup


def hide_preserved_trim_sources(
    context,
    source_pairs,
    created_backups,
):
    view_layer = context.view_layer
    target_pointers = {
        target.as_pointer()
        for target, _source in source_pairs
        if target is not None
    }
    candidates = list(created_backups)
    candidates.extend(
        source
        for target, source in source_pairs
        if (
            source is not None
            and source is not target
            and is_backup_object(source)
        )
    )

    visibility_states = []
    seen = set()
    for source in candidates:
        pointer = source.as_pointer()
        if pointer in seen or pointer in target_pointers:
            continue
        seen.add(pointer)
        visibility_states.append(
            (
                source,
                source.hide_get(view_layer=view_layer),
            )
        )
        source.hide_set(True, view_layer=view_layer)
    return visibility_states


def restore_trim_source_visibility(context, visibility_states):
    view_layer = context.view_layer
    for source, was_hidden in reversed(visibility_states):
        try:
            if source.name in bpy.data.objects:
                source.hide_set(was_hidden, view_layer=view_layer)
        except (ReferenceError, RuntimeError):
            pass


def show_image_in_uv_editors(image):
    screen = getattr(bpy.context, "screen", None)
    if not screen:
        return
    for area in screen.areas:
        if area.type != "IMAGE_EDITOR":
            continue
        for space in area.spaces:
            if space.type == "IMAGE_EDITOR":
                space.image = image


def view3d_operator_override(context):
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None) if window else getattr(context, "screen", None)
    if not screen:
        return {}

    area = context.area if context.area and context.area.type == "VIEW_3D" else None
    if area is None:
        area = next((candidate for candidate in screen.areas if candidate.type == "VIEW_3D"), None)
    if area is None:
        return {}

    region = next((candidate for candidate in area.regions if candidate.type == "WINDOW"), None)
    if region is None:
        return {}

    space = area.spaces.active
    override = {
        "area": area,
        "region": region,
        "space_data": space,
    }
    if window:
        override["window"] = window
    if screen:
        override["screen"] = screen
    region_data = getattr(space, "region_3d", None)
    if region_data:
        override["region_data"] = region_data
    return override


def run_g_z_zero_translate_in_view3d(context):
    translate_kwargs = {
        "value": (0, 0, 0),
        "orient_type": 'GLOBAL',
        "orient_matrix": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
        "orient_matrix_type": 'GLOBAL',
        "constraint_axis": (False, False, True),
        "mirror": True,
        "use_proportional_edit": False,
        "proportional_edit_falloff": 'SMOOTH',
        "proportional_size": 1,
        "use_proportional_connected": False,
        "use_proportional_projected": False,
        "snap": False,
        "snap_elements": {'INCREMENT'},
        "use_snap_project": False,
        "snap_target": 'CLOSEST',
        "use_snap_self": True,
        "use_snap_edit": True,
        "use_snap_nonedit": True,
        "use_snap_selectable": False,
    }
    override = view3d_operator_override(context)
    if override:
        with context.temp_override(**override):
            return bpy.ops.transform.translate(**translate_kwargs)

    area = getattr(context, "area", None)
    if area and area.type != "VIEW_3D":
        original_area_type = area.type
        try:
            area.type = "VIEW_3D"
            override = view3d_operator_override(context)
            if override:
                with context.temp_override(**override):
                    return bpy.ops.transform.translate(**translate_kwargs)
        except Exception:
            pass
        finally:
            try:
                area.type = original_area_type
            except Exception:
                pass

    return bpy.ops.transform.translate(**translate_kwargs)


def uv_inside_trim_section(uv, section, epsilon=1e-5):
    return (
        section.u0 - epsilon <= uv.x <= section.u1 + epsilon
        and section.v0 - epsilon <= uv.y <= section.v1 + epsilon
    )


def face_inside_trim_section(face, uv_layer, section):
    if not face.is_valid or uv_layer is None:
        return False
    u_sum = 0.0
    v_sum = 0.0
    loop_count = 0
    for loop in face.loops:
        uv = loop[uv_layer].uv
        if not uv_inside_trim_section(uv, section):
            return False
        u_sum += uv.x
        v_sum += uv.y
        loop_count += 1
    if loop_count == 0:
        return False
    center_uv = Vector((u_sum / loop_count, v_sum / loop_count))
    return uv_inside_trim_section(center_uv, section)


def select_edit_mesh_faces_for_trim_section(obj, section, uv_map_name):
    if obj is None or obj.type != "MESH" or bpy.context.mode != "EDIT_MESH":
        return False
    bm = bmesh.from_edit_mesh(obj.data)
    uv_layer = bm.loops.layers.uv.get(uv_map_name) if uv_map_name else None
    if uv_layer is None:
        uv_layer = bm.loops.layers.uv.active
    if uv_layer is None:
        return False

    for vert in bm.verts:
        vert.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for face in bm.faces:
        face.select_set(False)

    selected_any = False
    for face in bm.faces:
        if not face_inside_trim_section(face, uv_layer, section):
            continue
        face.select_set(True)
        selected_any = True

    try:
        bm.select_flush_mode()
    except Exception:
        pass
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return selected_any


def translate_merge_vertices_on_object(context, obj, sections, uv_map_name):
    if obj is None or obj.name not in bpy.data.objects or obj.type != "MESH":
        return False
    if not sections:
        return False

    previous_mode = context.mode
    previous_active = context.view_layer.objects.active
    previous_selection = list(context.selected_objects)
    tool_settings = context.scene.tool_settings if context.scene else bpy.context.scene.tool_settings
    previous_automerge = getattr(tool_settings, "use_mesh_automerge", None)
    previous_automerge_split = getattr(tool_settings, "use_mesh_automerge_and_split", None)
    previous_mesh_select_mode = tuple(getattr(tool_settings, "mesh_select_mode", (False, False, True)))

    try:
        if previous_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj

        bpy.ops.object.mode_set(mode="EDIT")
        if hasattr(tool_settings, "use_mesh_automerge"):
            tool_settings.use_mesh_automerge = True
        if hasattr(tool_settings, "use_mesh_automerge_and_split"):
            tool_settings.use_mesh_automerge_and_split = True
        tool_settings.mesh_select_mode = (False, False, True)
        try:
            bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type="FACE")
        except Exception:
            pass
        try:
            bpy.ops.mesh.reveal(select=False)
        except Exception:
            pass
        trim_sections = list(sections.values()) if hasattr(sections, "values") else list(sections)
        bpy.ops.mesh.select_all(action="SELECT")

        for section in trim_sections:
            tool_settings.mesh_select_mode = (False, False, True)
            try:
                bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type="FACE")
            except Exception:
                pass
            if not select_edit_mesh_faces_for_trim_section(obj, section, uv_map_name):
                continue
            bpy.ops.mesh.select_all(action="INVERT")
            bpy.ops.mesh.hide(unselected=False)
            if hasattr(tool_settings, "use_mesh_automerge"):
                tool_settings.use_mesh_automerge = True
            if hasattr(tool_settings, "use_mesh_automerge_and_split"):
                tool_settings.use_mesh_automerge_and_split = True
            tool_settings.mesh_select_mode = (True, False, False)
            try:
                bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type="VERT")
            except Exception:
                pass
            bpy.ops.mesh.select_all(action="SELECT")
            tool_settings.mesh_select_mode = (False, False, True)
            try:
                bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type="FACE")
            except Exception:
                pass
            bpy.ops.mesh.select_all(action="SELECT")
            run_g_z_zero_translate_in_view3d(context)
            bpy.ops.mesh.reveal(select=True)
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.object.mode_set(mode="OBJECT")
        return True
    finally:
        try:
            if context.mode == "EDIT_MESH":
                try:
                    bpy.ops.mesh.reveal(select=True)
                except Exception:
                    pass
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            if previous_automerge is not None and hasattr(tool_settings, "use_mesh_automerge"):
                tool_settings.use_mesh_automerge = previous_automerge
            if previous_automerge_split is not None and hasattr(tool_settings, "use_mesh_automerge_and_split"):
                tool_settings.use_mesh_automerge_and_split = previous_automerge_split
            try:
                tool_settings.mesh_select_mode = previous_mesh_select_mode
            except Exception:
                pass
            bpy.ops.object.select_all(action="DESELECT")
            for selected_obj in previous_selection:
                if selected_obj and selected_obj.name in bpy.data.objects:
                    selected_obj.select_set(True)
            if previous_active and previous_active.name in bpy.data.objects:
                context.view_layer.objects.active = previous_active
            if previous_mode != "OBJECT" and previous_active and previous_active.name in bpy.data.objects:
                restore_mode = "EDIT" if previous_mode.startswith("EDIT") else previous_mode
                bpy.ops.object.mode_set(mode=restore_mode)
        except Exception:
            pass


def tagged_working_objects_for_backup(backup):
    source_id = backup.get(SOURCE_ID_TAG, "") if backup else ""
    if not source_id:
        return []
    matches = []
    for candidate in bpy.data.objects:
        if candidate == backup or candidate.type != "MESH":
            continue
        if is_backup_object(candidate):
            continue
        if not candidate.get(GENERATED_TAG, False):
            continue
        if candidate.get(SOURCE_ID_TAG, "") != source_id:
            continue
        if candidate.get(BACKUP_OBJECT_TAG, "") != backup.name:
            continue
        matches.append(candidate)
    return matches


def resolve_trim_sources(target_objects):
    pairs = []
    errors = []
    selection_replacements = {}
    resolved_targets = set()
    resolved_backups = set()
    for selected in target_objects:
        target = selected
        source = selected
        if is_backup_object(selected):
            matches = tagged_working_objects_for_backup(selected)
            if len(matches) > 1:
                errors.append(
                    f"{selected.name}: multiple tagged working objects exist; "
                    "select the intended working object directly"
                )
                continue
            target = matches[0] if matches else None
            source = selected
            selection_replacements[selected] = target
        elif selected.get(GENERATED_TAG, False):
            backup_name = selected.get(BACKUP_OBJECT_TAG, "")
            backup = bpy.data.objects.get(backup_name) if backup_name else None
            if (
                backup
                and is_backup_object(backup)
                and backup.get(SOURCE_ID_TAG) == selected.get(SOURCE_ID_TAG)
            ):
                source = backup
            else:
                errors.append(
                    f"{selected.name}: its tagged original backup is missing"
                )
                continue

        if target is not None:
            pointer = target.as_pointer()
            if pointer in resolved_targets:
                continue
            resolved_targets.add(pointer)
        else:
            backup_pointer = source.as_pointer()
            if backup_pointer in resolved_backups:
                continue
            resolved_backups.add(backup_pointer)
        pairs.append((target, source))
    return pairs, errors, selection_replacements


def _build_trim_layout_preview(context, request):
    key = request["key"]
    settings = getattr(
        context.scene,
        "bakeaddon_texture_to_trim_settings",
        None,
    )
    if settings is None or not settings.layout_preview_enabled:
        _TRIM_PREVIEW_ANALYSIS_CACHE.pop(key, None)
        shared_state.trim_layout_preview_cache.pop(key, None)
        return

    current_signature = trim_preview_selection_signature(context)
    current_layout_signature = trim_preview_layout_signature(
        settings
    )
    if (
        current_signature != request["selection_signature"]
        or current_layout_signature
        != request["layout_signature"]
    ):
        schedule_trim_layout_preview(
            context,
            invalidate_analysis=(
                current_signature
                != request["selection_signature"]
            ),
        )
        return

    target_objects = [
        obj
        for obj in context.selected_objects
        if obj.type == "MESH"
    ]
    if not target_objects:
        raise RuntimeError(
            "Select at least one mesh object to preview its trim layout."
        )

    source_pairs, errors, _selection_replacements = (
        resolve_trim_sources(target_objects)
    )
    if errors:
        raise RuntimeError(errors[0])
    source_objects = [
        source
        for _target, source in source_pairs
        if source is not None
    ]
    if not source_objects:
        raise RuntimeError("No valid trim source objects were found.")

    source_signature = tuple(
        source.as_pointer()
        for source in source_objects
    )
    analysis = _TRIM_PREVIEW_ANALYSIS_CACHE.get(key)
    analysis_is_valid = bool(
        not request["invalidate_analysis"]
        and analysis
        and analysis["selection_signature"] == current_signature
        and analysis["source_signature"] == source_signature
    )
    if not analysis_is_valid:
        for source in source_objects:
            if source.mode == "EDIT":
                source.update_from_editmode()
        material_infos = collect_layout_material_infos(
            source_objects
        )
        if not material_infos:
            raise RuntimeError(
                "No materials used by selected polygons were found."
            )

        analysis = {
            "selection_signature": current_signature,
            "source_signature": source_signature,
            "material_infos": material_infos,
        }
        _TRIM_PREVIEW_ANALYSIS_CACHE[key] = analysis

    layout_result = calculate_trim_layout(
        analysis["material_infos"],
        int(settings.atlas_size),
        settings.padding_px,
        settings.layout_mode,
    )

    ordered = sorted(
        layout_result.sections.values(),
        key=lambda section: (section.y, section.x),
    )
    total = max(1, len(ordered))
    preview_sections = []
    for index, section in enumerate(ordered):
        red, green, blue = hsv_to_rgb(
            index / total,
            0.65,
            0.92,
        )
        preview_sections.append(
            {
                "name": section.material_name,
                "left": section.u0,
                "bottom": section.v0,
                "right": section.u1,
                "top": section.v1,
                "color": (red, green, blue, 0.5),
            }
        )

    active_material = active_trim_source_material(context)
    highlight_material_names = (
        selected_trim_source_material_names(
            context,
            fallback_material=active_material,
        )
    )
    warning = ""
    if layout_result.warnings:
        warning = layout_result.warnings[0]
        if len(layout_result.warnings) > 1:
            warning += (
                f" (+{len(layout_result.warnings) - 1} more)"
            )
    shared_state.trim_layout_preview_cache[key] = {
        "status": "READY",
        "selection_signature": current_signature,
        "layout_signature": current_layout_signature,
        "priority_signature": tuple(
            (
                material_name,
                material_manual_trim_priority(info.material),
            )
            for material_name, info in analysis[
                "material_infos"
            ].items()
        ),
        "sections": tuple(preview_sections),
        "warning": warning,
        "highlight_material_names": highlight_material_names,
        "effective_padding": layout_result.effective_padding,
    }


def ensure_trim_layout_preview_current(context):
    settings = getattr(
        context.scene,
        "bakeaddon_texture_to_trim_settings",
        None,
    )
    if settings is None or not settings.layout_preview_enabled:
        return
    hide_trim_preview_overlays(context)
    signature = trim_preview_selection_signature(context)
    layout_signature = trim_preview_layout_signature(settings)
    entry = trim_preview_entry(context)
    key = shared_state.trim_layout_preview_context_key(context)
    analysis = (
        _TRIM_PREVIEW_ANALYSIS_CACHE.get(key)
        if key is not None
        else None
    )
    priority_changed = False
    if entry and entry.get("status") == "READY" and analysis:
        current_priorities = tuple(
            (
                material_name,
                material_manual_trim_priority(info.material),
            )
            for material_name, info in analysis[
                "material_infos"
            ].items()
        )
        priority_changed = (
            entry.get("priority_signature")
            != current_priorities
        )
    if (
        entry is None
        or entry.get("selection_signature") != signature
        or entry.get("layout_signature") != layout_signature
        or priority_changed
    ):
        selection_changed = bool(
            entry is None
            or entry.get("selection_signature") != signature
        )
        schedule_trim_layout_preview(
            context,
            invalidate_analysis=selection_changed,
        )


def set_trim_layout_preview_selected_materials(
    context,
    fallback_material,
):
    entry = trim_preview_entry(context)
    if entry is None:
        return
    material_names = selected_trim_source_material_names(
        context,
        fallback_material=fallback_material,
    )
    if (
        tuple(entry.get("highlight_material_names", ()))
        != material_names
    ):
        entry["highlight_material_names"] = material_names
        tag_trim_preview_image_editors()


def materialize_backup_working_objects(
    context,
    source_pairs,
    selection_replacements,
):
    materialized = []
    created = []
    for target, source in source_pairs:
        if target is None:
            target = duplicate_backup_for_trim_generation(context, source)
            created.append(target)
            selection_replacements[source] = target
        materialized.append((target, source))
    return materialized, created


def remap_operator_context_state(state, replacements):
    remapped_selection = []
    seen = set()
    for obj in state["selection"]:
        replacement = replacements.get(obj, obj)
        if replacement is None or replacement.name not in bpy.data.objects:
            continue
        pointer = replacement.as_pointer()
        if pointer in seen:
            continue
        seen.add(pointer)
        remapped_selection.append(replacement)
    active = replacements.get(state["active"], state["active"])
    if active is None or active.name not in bpy.data.objects:
        active = remapped_selection[0] if remapped_selection else None
    return {
        "mode": state["mode"],
        "selection": remapped_selection,
        "active": active,
    }


def capture_operator_context(context):
    return {
        "mode": context.mode,
        "selection": list(context.selected_objects),
        "active": context.view_layer.objects.active,
    }


def restore_operator_context(context, state):
    try:
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    try:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in state["selection"]:
            if obj and obj.name in bpy.data.objects:
                obj.select_set(True)
        active = state["active"]
        if active and active.name in bpy.data.objects:
            context.view_layer.objects.active = active
        previous_mode = state["mode"]
        if (
            previous_mode != "OBJECT"
            and active
            and active.name in bpy.data.objects
        ):
            restore_mode = (
                "EDIT" if previous_mode.startswith("EDIT") else previous_mode
            )
            bpy.ops.object.mode_set(mode=restore_mode)
    except Exception:
        pass


def remove_datablock_if_unused(collection, datablock):
    if datablock and datablock.users == 0:
        try:
            collection.remove(datablock)
        except Exception:
            pass


def write_vertex_group_weights(obj, group_weights):
    if not group_weights:
        return
    all_indices = list(range(len(obj.data.vertices)))
    for group in obj.vertex_groups:
        try:
            group.remove(all_indices)
        except Exception:
            pass
    groups_by_index = {group.index: group for group in obj.vertex_groups}
    for group_index, weights in group_weights.items():
        group = groups_by_index.get(group_index)
        if group is None:
            raise RuntimeError(
                f"{obj.name}: vertex group {group_index} no longer exists."
            )
        for vertex_index, weight in weights.items():
            group.add([vertex_index], float(weight), "REPLACE")


def apply_vertex_group_weights(obj, group_weights):
    write_vertex_group_weights(obj, group_weights)


def capture_object_vertex_group_weights(obj):
    result = {}
    for vertex in obj.data.vertices:
        for element in vertex.groups:
            result.setdefault(element.group, {})[vertex.index] = (
                float(element.weight)
            )
    return result


def capture_vertex_group_definitions(obj):
    return [
        {
            "name": group.name,
            "lock_weight": bool(getattr(group, "lock_weight", False)),
        }
        for group in obj.vertex_groups
    ]


def restore_vertex_group_definitions(obj, definitions):
    for group in list(obj.vertex_groups):
        obj.vertex_groups.remove(group)
    for definition in definitions:
        group = obj.vertex_groups.new(name=definition["name"])
        if hasattr(group, "lock_weight"):
            group.lock_weight = definition["lock_weight"]


class OutputFileTransaction:
    def __init__(self, staged_paths, final_paths, run_id):
        self.staged_paths = dict(staged_paths)
        self.final_paths = dict(final_paths)
        self.run_id = run_id
        self.backups = {}
        self.installed = []
        self.committed = False

    def commit(self):
        try:
            for map_key, final_path in self.final_paths.items():
                staged_path = self.staged_paths[map_key]
                if not os.path.isfile(staged_path):
                    raise RuntimeError(
                        f"Staged output is missing: {staged_path}"
                    )
                if os.path.exists(final_path):
                    backup_path = (
                        f"{final_path}.bakeaddon-{self.run_id}.backup"
                    )
                    os.replace(final_path, backup_path)
                    self.backups[final_path] = backup_path
                os.replace(staged_path, final_path)
                self.installed.append(final_path)
            self.committed = True
        except Exception:
            self.rollback()
            raise

    def rollback(self):
        for final_path in reversed(self.installed):
            try:
                if os.path.exists(final_path):
                    os.remove(final_path)
            except OSError:
                pass
        self.installed.clear()
        for final_path, backup_path in self.backups.items():
            try:
                if os.path.exists(backup_path):
                    os.replace(backup_path, final_path)
            except OSError:
                pass
        self.backups.clear()
        self.committed = False

    def finalize(self):
        for backup_path in self.backups.values():
            try:
                if os.path.exists(backup_path):
                    os.remove(backup_path)
            except OSError:
                pass
        self.backups.clear()

    def cleanup_staging(self):
        for staged_path in self.staged_paths.values():
            try:
                if os.path.exists(staged_path):
                    os.remove(staged_path)
            except OSError:
                pass


class BAKEADDON_OT_trim_layout_preview_toggle(Operator):
    bl_idname = "bakeaddon.trim_layout_preview_toggle"
    bl_label = "PREVIEW"
    bl_description = (
        "Toggle the cached pre-bake trim layout preview. This draws packed "
        "rectangles only and does not bake or modify mesh data"
    )

    def execute(self, context):
        settings = context.scene.bakeaddon_texture_to_trim_settings
        settings.layout_preview_enabled = (
            not settings.layout_preview_enabled
        )
        return {"FINISHED"}


class BAKEADDON_OT_texture_to_trim_sheet(Operator):
    bl_idname = "bakeaddon.texture_to_trim_sheet"
    bl_label = "Generate Trim Sheet"
    bl_description = "Convert tiled material UVs into a generated trim sheet with UV-aware face cutting"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.scene and any(obj.type == "MESH" for obj in context.selected_objects))

    def execute(self, context):
        settings = context.scene.bakeaddon_texture_to_trim_settings
        map_keys = enabled_map_keys(settings)
        if not map_keys:
            self.report({"ERROR"}, "Enable at least one bake map.")
            return {"CANCELLED"}

        target_objects = [
            obj for obj in context.selected_objects if obj.type == "MESH"
        ]
        if not target_objects:
            self.report({"ERROR"}, "Select at least one mesh object.")
            return {"CANCELLED"}
        image_prefix = trim_output_prefix_for_object(
            context,
            target_objects,
        )
        material_name = trim_material_name_for_object(
            context,
            target_objects,
        )

        (
            source_pairs,
            resolution_errors,
            selection_replacements,
        ) = resolve_trim_sources(target_objects)
        if resolution_errors:
            self.report({"ERROR"}, resolution_errors[0])
            return {"CANCELLED"}
        source_objects = [source for _target, source in source_pairs]
        source_face_count = sum(
            len(source.data.polygons) for source in source_objects
        )

        validation_errors, validation_warnings, _estimated = (
            validate_trim_inputs(source_objects, settings, map_keys)
        )
        if validation_errors:
            for message in validation_errors:
                print(f"Bake Addon Trim preflight error: {message}")
            self.report({"ERROR"}, validation_errors[0])
            return {"CANCELLED"}

        context_state = capture_operator_context(context)
        context_state_to_restore = context_state
        run_id = uuid.uuid4().hex
        map_images = {}
        atlas_material = None
        build_results = []
        created_backups = []
        created_working_objects = []
        source_visibility_states = []
        old_meshes = []
        superseded_generated_materials = []
        tag_snapshots = {}
        file_transaction = None
        success = False

        try:
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")

            source_pairs, created_working_objects = (
                materialize_backup_working_objects(
                    context,
                    source_pairs,
                    selection_replacements,
                )
            )

            material_infos = collect_material_infos(
                source_objects,
                map_keys,
                include_pureplay_ao=(
                    settings.export_preset != EXPORT_PRESET_PUREPLAY_ORD
                    or settings.pureplay_bake_ao
                ),
            )
            if not material_infos:
                raise RuntimeError(
                    "No used materials were found on the selected faces."
                )

            all_warnings = list(validation_warnings)
            for info in material_infos.values():
                all_warnings.extend(info.warnings)
            for message in all_warnings:
                print(f"Bake Addon Trim warning: {message}")
            if all_warnings:
                self.report(
                    {"WARNING"},
                    (
                        all_warnings[0]
                        if len(all_warnings) == 1
                        else f"{all_warnings[0]} (+{len(all_warnings) - 1} more)"
                    ),
                )

            atlas_size = int(settings.atlas_size)
            layout_result = calculate_trim_layout(
                material_infos,
                atlas_size,
                settings.padding_px,
                settings.layout_mode,
            )
            sections = layout_result.sections
            for message in layout_result.warnings:
                print(f"Bake Addon Trim warning: {message}")
                self.report({"WARNING"}, message)

            final_paths = {
                map_key: output_path_for_map(
                    settings,
                    map_key,
                    image_prefix,
                )
                for map_key in map_keys
            }
            output_directory = os.path.dirname(next(iter(final_paths.values())))
            os.makedirs(output_directory, exist_ok=True)
            if not os.path.isdir(output_directory):
                raise RuntimeError(
                    f"Output folder is unavailable: {output_directory}"
                )
            staged_paths = {
                map_key: (
                    f"{final_path}.bakeaddon-{run_id}.staged.png"
                )
                for map_key, final_path in final_paths.items()
            }
            file_transaction = OutputFileTransaction(
                staged_paths,
                final_paths,
                run_id,
            )

            map_images = build_cycles_bake_maps(
                context,
                material_infos,
                sections,
                map_keys,
                settings,
                self.report,
                image_prefix,
                run_id,
                staged_paths,
            )

            has_transparency = any(
                info.has_transparency for info in material_infos.values()
            )
            atlas_material = create_trim_material(
                map_images,
                OUTPUT_UV_NAME,
                sections,
                effective_normal_map_format(settings),
                has_transparency=has_transparency,
                material_name=material_name,
                run_id=run_id,
            )

            total_processed = 0
            total_pieces = 0
            total_skipped = 0
            for target, source in source_pairs:
                result = build_remapped_mesh(
                    source,
                    atlas_material,
                    sections,
                    settings,
                )
                build_results.append((target, source, result))
                total_processed += result.processed_faces
                total_pieces += result.created_pieces
                total_skipped += result.skipped_faces

            for _target, _source, result in build_results:
                assert_single_output_uv_layer(result.mesh)
            file_transaction.commit()

            for target, source, result in build_results:
                group_definitions = capture_vertex_group_definitions(target)
                old_group_weights = capture_object_vertex_group_weights(target)
                for old_material in target.data.materials:
                    if (
                        old_material
                        and old_material is not atlas_material
                        and old_material.get(RUN_TAG)
                        and old_material not in superseded_generated_materials
                    ):
                        superseded_generated_materials.append(
                            old_material
                        )
                old_meshes.append(
                    (
                        target,
                        target.data,
                        group_definitions,
                        old_group_weights,
                    )
                )
                tag_snapshots[target] = {
                    key: (key in target, target.get(key))
                    for key in (
                        RUN_TAG,
                        SOURCE_ID_TAG,
                        GENERATED_TAG,
                        BACKUP_OBJECT_TAG,
                    )
                }
                if (
                    settings.create_backup
                    and target is source
                    and not target.get(BACKUP_OBJECT_TAG)
                ):
                    backup = create_backup_object(
                        context,
                        target,
                        exclude_objects=target_objects,
                        run_id=run_id,
                    )
                    created_backups.append(backup)
                target.data = result.mesh
                restore_vertex_group_definitions(
                    target,
                    group_definitions,
                )
                apply_vertex_group_weights(
                    target,
                    result.vertex_group_weights,
                )
                target[RUN_TAG] = run_id
                target[GENERATED_TAG] = True
                if not target.get(SOURCE_ID_TAG):
                    target[SOURCE_ID_TAG] = uuid.uuid4().hex

            for map_key, image in map_images.items():
                final_path = final_paths.get(map_key)
                if final_path:
                    image.filepath_raw = final_path

            if settings.show_in_uv_editor:
                show_image_in_uv_editors(map_images.get(MAP_BASE_COLOR) or next(iter(map_images.values())))

            context_state_to_restore = remap_operator_context_state(
                context_state,
                selection_replacements,
            )
            source_visibility_states = hide_preserved_trim_sources(
                context,
                source_pairs,
                created_backups,
            )
            file_transaction.finalize()
            success = True
            for _target, old_mesh, _groups, _weights in old_meshes:
                remove_datablock_if_unused(bpy.data.meshes, old_mesh)
            for old_material in superseded_generated_materials:
                remove_datablock_if_unused(
                    bpy.data.materials,
                    old_material,
                )
            if (
                bpy.data.materials.get(material_name) is None
                or bpy.data.materials.get(material_name) is atlas_material
            ):
                atlas_material.name = material_name
            if settings.layout_preview_enabled:
                settings.layout_preview_enabled = False

            first_path = (
                final_paths.get(MAP_BASE_COLOR)
                or next(iter(final_paths.values()))
            )
            self.report(
                {"INFO"},
                (
                    f"Trim maps saved to {os.path.dirname(first_path)} | "
                    f"Faces cut: {total_processed} | Pieces: {total_pieces} | "
                    f"Unchanged faces: "
                    f"{source_face_count - total_processed}"
                ),
            )
            return {"FINISHED"}

        except Exception as exc:
            if file_transaction:
                file_transaction.rollback()
            restore_trim_source_visibility(
                context,
                source_visibility_states,
            )
            for (
                target,
                old_mesh,
                group_definitions,
                old_group_weights,
            ) in reversed(old_meshes):
                try:
                    target.data = old_mesh
                    restore_vertex_group_definitions(
                        target,
                        group_definitions,
                    )
                    write_vertex_group_weights(
                        target,
                        old_group_weights,
                    )
                except Exception:
                    pass
                for key, (existed, value) in tag_snapshots.get(
                    target,
                    {},
                ).items():
                    try:
                        if existed:
                            target[key] = value
                        elif key in target:
                            del target[key]
                    except Exception:
                        pass
            for backup in created_backups:
                backup_mesh = backup.data
                try:
                    bpy.data.objects.remove(backup, do_unlink=True)
                except Exception:
                    pass
                remove_datablock_if_unused(bpy.data.meshes, backup_mesh)
            for working in created_working_objects:
                working_mesh = working.data
                try:
                    bpy.data.objects.remove(working, do_unlink=True)
                except Exception:
                    pass
                remove_datablock_if_unused(
                    bpy.data.meshes,
                    working_mesh,
                )
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            if file_transaction and not success:
                file_transaction.cleanup_staging()
            if not success:
                for _target, _source, result in build_results:
                    remove_datablock_if_unused(
                        bpy.data.meshes,
                        result.mesh,
                    )
                if atlas_material:
                    remove_datablock_if_unused(
                        bpy.data.materials,
                        atlas_material,
                    )
                for image in map_images.values():
                    remove_datablock_if_unused(
                        bpy.data.images,
                        image,
                    )
            restore_operator_context(context, context_state_to_restore)


class BAKEADDON_PT_texture_to_trim_sheet(Panel):
    bl_label = "Materials to TrimSheet"
    bl_idname = "BAKEADDON_PT_texture_to_trim_sheet"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Bake Addon 2.1"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.bakeaddon_texture_to_trim_settings
        ensure_trim_layout_preview_current(context)

        box = layout.box()
        col = box.column(align=True)
        row = col.row(align=True)
        row.prop(settings, "atlas_size", text="Size")
        row.prop(settings, "padding_px", text="Padding")
        col.prop(settings, "layout_mode", text="Layout")
        preview_row = col.row(align=True)
        preview_row.scale_y = 1.25
        swatch = preview_row.row(align=True)
        swatch.scale_x = 0.55
        swatch.template_node_socket(
            color=(
                (0.04, 0.9, 0.45, 1.0)
                if settings.layout_preview_enabled
                else (0.22, 0.24, 0.27, 1.0)
            )
        )
        preview_row.separator(factor=0.35)
        preview_row.operator(
            BAKEADDON_OT_trim_layout_preview_toggle.bl_idname,
            text="PREVIEW",
            icon=(
                "HIDE_OFF"
                if settings.layout_preview_enabled
                else "HIDE_ON"
            ),
            depress=settings.layout_preview_enabled,
        )

        preview_entry = trim_preview_entry(context)
        if settings.layout_preview_enabled and preview_entry:
            trim_tools = getattr(
                context.scene,
                "bakeaddon_trim_settings",
                None,
            )
            preview_paused = bool(
                trim_tools
                and (
                    trim_tools.assign_enabled
                    or trim_tools.edit_enabled
                    or trim_tools.draw_trim_active
                )
            )
            status = preview_entry.get("status")
            if preview_paused:
                col.label(
                    text="Preview paused during Trim Sheet editing.",
                    icon="INFO",
                )
            elif status == "ERROR":
                col.label(
                    text=preview_entry.get(
                        "warning",
                        "Preview failed.",
                    ),
                    icon="ERROR",
                )
            elif preview_entry.get("warning"):
                col.label(
                    text=preview_entry["warning"],
                    icon="INFO",
                )

        box = layout.box()
        box.label(text="Active Material Trim Priority")
        material = active_trim_source_material(context)
        set_trim_layout_preview_selected_materials(
            context,
            material,
        )
        if material is not None:
            priority = material_manual_trim_priority(material)
            row = box.row(align=True)
            row.label(text=material.name, icon="MATERIAL")
            swatch = row.row(align=True)
            swatch.scale_x = 0.6
            swatch.template_node_socket(
                color=manual_trim_priority_dot_color(priority)
            )
            slider = row.row(align=True)
            slider.prop(
                material,
                MANUAL_TRIM_PRIORITY_PROPERTY,
                text="",
                slider=True,
            )
        else:
            box.label(text="Select a mesh face with a material.")

        box = layout.box()
        col = box.column(align=True)
        col.prop(settings, "export_preset", text="Preset")
        if settings.export_preset == EXPORT_PRESET_CUSTOM:
            grid = col.grid_flow(
                row_major=True,
                columns=2,
                even_columns=True,
                even_rows=False,
                align=True,
            )
            grid.prop(settings, "bake_base_color")
            grid.prop(settings, "bake_roughness")
            grid.prop(settings, "bake_metallic")
            grid.prop(settings, "bake_normal")
            grid.prop(settings, "bake_ao")
            row = col.row(align=True)
            row.enabled = settings.bake_normal
            row.prop(settings, "normal_map_format", text="Normal")
        else:
            col.label(text="BaseColor + Metallic + Normal (DirectX)")
            col.prop(settings, "pureplay_bake_ao")
            col.label(
                text=(
                    "ORD: R Occlusion, G Roughness, B Displacement"
                    if settings.pureplay_bake_ao
                    else "ORD: R White, G Roughness, B Displacement"
                )
            )
        row = col.row(align=True)
        row.prop(settings, "cycles_samples", text="Samples")
        row.prop(settings, "cycles_margin_px", text="Bake Margin")
        col.prop(settings, "cycles_use_gpu")

        box = layout.box()
        col = box.column(align=True)
        row = col.row(align=True)
        row.prop(settings, "triangulate_ngons")
        row.prop(settings, "merge_vertices_translate")
        row = col.row(align=True)
        row.prop(settings, "uv_epsilon", text="UV Border Tol")
        row.prop(settings, "max_tiles_per_axis", text="Max Tiles")
        col.label(text=f"Output UV: {OUTPUT_UV_NAME}", icon="GROUP_UVS")

        box = layout.box()
        col = box.column(align=True)
        col.prop(settings, "export_dir", text="Path")
        selected_meshes = [
            obj for obj in context.selected_objects if obj.type == "MESH"
        ]
        file_prefix = trim_output_prefix_for_object(
            context,
            selected_meshes,
        )
        col.label(text=f"Files: {file_prefix}_<Map>.png")

        row = layout.row()
        row.scale_y = 1.4
        row.operator(BAKEADDON_OT_texture_to_trim_sheet.bl_idname, icon="TEXTURE")
