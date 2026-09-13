# --- START OF FILE operators_baking.py --- [MODIFIED]

# gready/operators_baking.py

import bpy
import os
import re
import math
import tempfile
import json
import subprocess
import shutil
import uuid
from mathutils import Vector
from bpy.props import (
    StringProperty,
    FloatProperty,
    EnumProperty,
    PointerProperty,
    CollectionProperty,
    BoolProperty,
    IntProperty
)
from bpy.types import Operator, Panel, PropertyGroup # Panel, PropertyGroup not used here, but harmless

# Import helper functions from the main utils module
from . import utils

# --- Helper Function for Auto Rename ---
def _clean_name(name):
    """Removes common suffixes like _low, _high, _cage, BUT KEEPS .###"""
    # Remove specific suffixes first
    name = re.sub(r'_low$', '', name)
    name = re.sub(r'_high(\.\d+)?$', '', name) # Remove _high and optional .### after it
    name = re.sub(r'_cage(\.\d+)?$', '', name) # Remove _cage and optional .### after it
    # DO NOT remove trailing .### like name = re.sub(r'\.\d+$', '', name)
    return name
# --- End Helper Function ---

# --- Helper Function for UV Map Selection ---
def set_active_uv_layer(obj, uv_map_name, report_func=None):
    """
    Set the active UV layer on an object for baking.
    Returns True if successful, False otherwise.
    """
    if not obj or obj.type != 'MESH' or not obj.data:
        if report_func:
            report_func({'WARNING'}, f"Invalid object or not a mesh: {obj}")
        return False
    
    if not uv_map_name:
        # No UV map specified, use current active
        if report_func:
            report_func({'INFO'}, f"No UV map specified for {obj.name}, using current active UV")
        return True
    
    uv_layers = obj.data.uv_layers
    
    # Check if UV map exists
    if not uv_layers or uv_map_name not in uv_layers:
        available = [uv.name for uv in uv_layers] if uv_layers else []
        if report_func:
            report_func({'WARNING'}, f"UV map '{uv_map_name}' not found on '{obj.name}'. Available: {available}")
        return False
    
    # Set the active UV layer
    uv_layers.active = uv_layers[uv_map_name]
    
    if report_func:
        report_func({'INFO'}, f"Active UV set to '{uv_map_name}' for '{obj.name}'")
    
    return True

def ensure_bake_image_selected(obj, report_func=None):
    """
    Make sure BakeTargetImage node is selected and active in all materials.
    This ensures Blender bakes to the correct image.
    Returns True if successful, False otherwise.
    """
    if not obj or obj.type != 'MESH':
        return False
    
    success_count = 0
    for mat_slot in obj.material_slots:
        mat = mat_slot.material
        if not mat or not mat.use_nodes or not mat.node_tree:
            continue
        
        nodes = mat.node_tree.nodes
        
        # Find BakeTargetImage node
        bake_image_node = nodes.get("BakeTargetImage")
        if not bake_image_node:
            if report_func:
                report_func({'WARNING'}, f"BakeTargetImage node not found in material '{mat.name}'")
            continue
        
        # Deselect all nodes
        for node in nodes:
            node.select = False
        
        # Select and activate BakeTargetImage
        bake_image_node.select = True
        nodes.active = bake_image_node
        
        success_count += 1
    
    return success_count > 0
# --- End Helper Functions ---


BAKE_MAP_DISPLAY_NAMES = {
    'COMBINED': 'Combined',
    'AO': 'AmbientOcclusion',
    'SHADOW': 'Shadow',
    'POSITION': 'Position',
    'NORMAL': 'Normal',
    'UV': 'UV',
    'ROUGHNESS': 'Roughness',
    'EMIT': 'Emit',
    'ENVIRONMENT': 'Environment',
    'DIFFUSE': 'Diffuse',
    'GLOSSY': 'Glossy',
    'TRANSMISSION': 'Transmission',
}

BAKE_PRESET_CUSTOM = "CUSTOM"
BAKE_PRESET_PUREPLAY_ORD = "PUREPLAY_ORD"
PUREPLAY_INTERNAL_TAG = "_gready_pureplay_bake_active"
PUREPLAY_RUN_TAG = "_gready_pureplay_bake_run"
PUREPLAY_MAP_NAME_TAG = "_gready_bake_map_name_override"
PUREPLAY_LAST_SUCCESS_TAG = "_gready_last_bake_success"
PUREPLAY_EXPORT_NAME_TAG = "gready_bake_export_name"

IMAGE_FORMAT_EXTENSIONS = {
    'JPEG': 'jpg',
    'TIFF': 'tif',
    'TARGA': 'tga',
    'OPEN_EXR': 'exr',
    'BMP': 'bmp',
}


def sanitize_bake_name_part(name, fallback="Material"):
    """Return a compact filesystem-safe token for bake image and file names."""
    cleaned = str(name or "").strip()
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._ ")
    return cleaned or fallback


def bake_map_display_name(bake_type):
    return BAKE_MAP_DISPLAY_NAMES.get(bake_type, sanitize_bake_name_part(bake_type.title(), "Map"))


def bake_material_export_name(material):
    return sanitize_bake_name_part(
        material.get(PUREPLAY_EXPORT_NAME_TAG, material.name),
        "Material",
    )


def iter_unique_slot_materials(objects):
    seen = set()
    for obj in objects:
        if not obj or obj.type != 'MESH':
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if not mat:
                continue
            if mat.get("gready_bake_temp"):
                continue
            mat_ptr = mat.as_pointer()
            if mat_ptr in seen:
                continue
            seen.add(mat_ptr)
            yield mat


def get_or_create_bake_image(image_name, width, height, report_func=None, data_name=None):
    data_name = data_name or image_name
    image = bpy.data.images.get(data_name)
    if not image:
        image = bpy.data.images.new(name=data_name, width=width, height=height, alpha=True)
        image.source = 'GENERATED'
        if report_func:
            report_func({'INFO'}, f"Created bake image: '{data_name}' ({width}x{height}) [source=GENERATED]")
        return image

    if image.size[0] != width or image.size[1] != height:
        try:
            image.scale(width, height)
            if report_func:
                report_func({'INFO'}, f"Resized existing bake image '{image_name}' to {width}x{height}.")
        except Exception as e:
            if report_func:
                report_func({'WARNING'}, f"Reusing existing image '{image_name}' but could not resize it: {e}")
    elif report_func:
        report_func({'INFO'}, f"Reusing existing bake image: '{image_name}'")

    if image.source != 'GENERATED':
        try:
            image.source = 'GENERATED'
        except Exception as e:
            if report_func:
                report_func({'WARNING'}, f"Could not set source for existing image '{image_name}': {e}")
    return image


def configure_pureplay_image_colorspace(image, map_name):
    colorspace = "sRGB" if map_name == "BaseColor" else "Non-Color"
    try:
        image.colorspace_settings.name = colorspace
    except (TypeError, ValueError):
        pass


def build_bake_outputs(scene, target_meshes, bake_type, width, height, report_func=None):
    map_name = scene.get(PUREPLAY_MAP_NAME_TAG) or bake_map_display_name(bake_type)
    run_id = scene.get(PUREPLAY_RUN_TAG)
    materials = list(iter_unique_slot_materials(target_meshes))
    use_multiple_materials = bool(getattr(scene, "baker_multiple_materials", False))

    outputs = []
    if use_multiple_materials and materials:
        for mat in materials:
            image_name = f"{bake_material_export_name(mat)}_{map_name}"
            data_name = f"{image_name}__GReady_{run_id}" if run_id else image_name
            image = get_or_create_bake_image(image_name, width, height, report_func, data_name)
            if run_id:
                image["_gready_pureplay_run"] = run_id
                configure_pureplay_image_colorspace(image, map_name)
            outputs.append({
                "name": image_name,
                "image": image,
                "material": mat,
            })
        return outputs

    if len(materials) == 1:
        image_name = f"{bake_material_export_name(materials[0])}_{map_name}"
    else:
        image_name = f"Combined_{map_name}"
    data_name = f"{image_name}__GReady_{run_id}" if run_id else image_name
    image = get_or_create_bake_image(image_name, width, height, report_func, data_name)
    if run_id:
        image["_gready_pureplay_run"] = run_id
        configure_pureplay_image_colorspace(image, map_name)

    outputs.append({
        "name": image_name,
        "image": image,
        "material": None,
    })
    return outputs


def build_material_image_map(bake_outputs):
    return {
        output["material"].as_pointer(): output["image"]
        for output in bake_outputs
        if output.get("material") is not None and output.get("image") is not None
    }


def bake_output_label(bake_outputs):
    if len(bake_outputs) == 1:
        return bake_outputs[0]["name"]
    return f"{len(bake_outputs)} material images"


def apply_image_settings(scene, img_format):
    img_settings = scene.render.image_settings
    img_settings.file_format = img_format
    if img_format == 'PNG':
        img_settings.color_mode = scene.baker_png_color_mode
        img_settings.color_depth = scene.baker_png_bit_depth
        img_settings.compression = scene.baker_png_compression
    elif img_format == 'JPEG':
        img_settings.quality = scene.baker_jpeg_quality
        img_settings.color_mode = scene.baker_jpeg_color_mode
    elif img_format == 'TIFF':
        img_settings.color_mode = scene.baker_tiff_color_mode
        img_settings.color_depth = scene.baker_tiff_bit_depth
        img_settings.tiff_codec = scene.baker_tiff_codec
    elif img_format == 'TARGA':
        img_settings.color_mode = scene.baker_targa_color_mode
        img_settings.color_depth = '8'
    elif img_format == 'BMP':
        img_settings.color_mode = 'RGB'
        img_settings.color_depth = '8'
    elif img_format == 'OPEN_EXR':
        img_settings.color_mode = scene.baker_exr_color_mode
        img_settings.color_depth = scene.baker_exr_bit_depth
        img_settings.exr_codec = scene.baker_exr_codec
        img_settings.use_preview = scene.baker_exr_use_preview


def save_single_bake_image(context, bake_image, image_name, img_format, width, height, report_func):
    scene = context.scene
    output_dir_base = scene.bake_output_dir or "//"
    abs_output_dir_base = bpy.path.abspath(output_dir_base)
    target_subdir_path = os.path.join(abs_output_dir_base, "Baked Maps")

    if not os.path.isdir(abs_output_dir_base):
        report_func({'ERROR'}, f"Base output directory does not exist: '{abs_output_dir_base}'. Image not saved.")
        return False

    try:
        os.makedirs(target_subdir_path, exist_ok=True)
    except OSError as e:
        report_func({'ERROR'}, f"Could not create output subdir '{target_subdir_path}': {e}. Image not saved.")
        return False

    file_ext = IMAGE_FORMAT_EXTENSIONS.get(img_format, img_format.lower())
    file_name = f"{image_name}.{file_ext}"
    file_path = os.path.join(target_subdir_path, file_name)

    try:
        apply_image_settings(scene, img_format)
        report_func({'INFO'}, f"  Applying {img_format} save settings.")

        use_compositor_aa = scene.baker_use_compositor_aa
        compositor_tree = getattr(scene, "node_tree", None)
        if use_compositor_aa and not hasattr(scene, "node_tree"):
            report_func({'WARNING'}, "Compositor AA is unavailable in this Blender version/context. Direct save was used instead.")
            use_compositor_aa = False
        existing_compositor_setup = False
        if use_compositor_aa and getattr(scene, "use_nodes", False) and compositor_tree:
            existing_compositor_setup = len(compositor_tree.nodes) > 0 or len(compositor_tree.links) > 0
        if use_compositor_aa and existing_compositor_setup:
            report_func({'WARNING'}, "Skipping Compositor AA because the scene already has a compositor setup. Direct save was used instead.")
            use_compositor_aa = False

        if use_compositor_aa:
            report_func({'INFO'}, "Applying Anti-Aliasing via Compositor...")
            original_use_nodes = scene.use_nodes
            original_render_filepath = scene.render.filepath
            original_frame_current = scene.frame_current
            original_frame_start = scene.frame_start
            original_frame_end = scene.frame_end
            original_resolution_x = scene.render.resolution_x
            original_resolution_y = scene.render.resolution_y
            original_resolution_percentage = scene.render.resolution_percentage
            try:
                scene.use_nodes = True
                tree = getattr(scene, "node_tree", None)
                if not tree:
                    raise RuntimeError("Scene compositor node tree is unavailable after enabling compositor nodes.")
                tree.nodes.clear()
                image_node = tree.nodes.new(type='CompositorNodeImage')
                image_node.image = bake_image
                image_node.location = (0, 0)
                aa_node = tree.nodes.new(type='CompositorNodeAntiAliasing')
                aa_node.threshold = 0.1
                aa_node.location = (image_node.location.x + image_node.width + 50, 0)
                composite_node = tree.nodes.new(type='CompositorNodeComposite')
                composite_node.location = (aa_node.location.x + aa_node.width + 50, 0)
                tree.links.new(image_node.outputs['Image'], aa_node.inputs['Image'])
                tree.links.new(aa_node.outputs['Image'], composite_node.inputs['Image'])
                scene.render.filepath = file_path
                scene.render.resolution_x = width
                scene.render.resolution_y = height
                scene.render.resolution_percentage = 100
                scene.frame_start = 1
                scene.frame_end = 1
                scene.frame_set(1)
                bpy.ops.render.render(write_still=True, use_viewport=False)
                report_func({'INFO'}, f"Saved Anti-Aliased image via Compositor to: {file_path}")
            finally:
                scene.render.filepath = original_render_filepath
                scene.frame_current = original_frame_current
                scene.frame_start = original_frame_start
                scene.frame_end = original_frame_end
                scene.render.resolution_x = original_resolution_x
                scene.render.resolution_y = original_resolution_y
                scene.render.resolution_percentage = original_resolution_percentage
                tree = getattr(scene, "node_tree", None)
                if tree:
                    tree.nodes.clear()
                scene.use_nodes = original_use_nodes
        else:
            bake_image.filepath_raw = file_path
            bake_image.save()
            report_func({'INFO'}, f"Saved baked image directly to: {file_path}")
        return True
    except Exception as e:
        report_func({'ERROR'}, f"Failed to save image '{file_path}': {e}")
        return False


def save_bake_outputs(context, bake_outputs, img_format, width, height, report_func):
    saved_count = 0
    seen_images = set()
    for output in bake_outputs:
        image = output.get("image")
        image_name = output.get("name")
        if not image or not image_name:
            continue
        image_ptr = image.as_pointer()
        if image_ptr in seen_images:
            continue
        seen_images.add(image_ptr)
        if save_single_bake_image(context, image, image_name, img_format, width, height, report_func):
            saved_count += 1
    return saved_count


def _pureplay_related_bake_objects(context):
    """Return selected lows plus matching high/cage sources used by the bake operator."""
    result = []
    seen = set()
    targets = [
        obj for obj in context.selected_objects
        if obj.type == 'MESH' and obj.name.endswith("_low")
    ]
    for target in targets:
        base_name = target.name[:-4]
        pattern = re.compile(rf"^{re.escape(base_name)}_(?:high(?:\.\d+)?|cage)$")
        for obj in (target, *tuple(bpy.data.objects)):
            if (
                obj.type == 'MESH'
                and (obj is target or pattern.match(obj.name))
                and obj.as_pointer() not in seen
            ):
                seen.add(obj.as_pointer())
                result.append(obj)
    return result


def _isolate_pureplay_materials(objects, run_id):
    """Assign temporary material copies and return enough state for full restoration."""
    assignments = []
    copies_by_pointer = {}
    copies = []
    for obj in objects:
        for slot in obj.material_slots:
            original = slot.material
            if not original:
                continue
            original_ptr = original.as_pointer()
            material_copy = copies_by_pointer.get(original_ptr)
            if material_copy is None:
                material_copy = original.copy()
                material_copy.name = f"{original.name}__GReadyPurePlay_{run_id}"
                material_copy[PUREPLAY_EXPORT_NAME_TAG] = original.get(
                    PUREPLAY_EXPORT_NAME_TAG,
                    original.name,
                )
                material_copy["_gready_pureplay_temp"] = run_id
                copies_by_pointer[original_ptr] = material_copy
                copies.append(material_copy)
            assignments.append((slot, original))
            slot.material = material_copy
    return assignments, copies


def _restore_pureplay_materials(assignments, copies):
    for slot, original in assignments:
        try:
            slot.material = original
        except (ReferenceError, RuntimeError):
            pass
    for material in copies:
        try:
            if material and material.name in bpy.data.materials and material.users == 0:
                bpy.data.materials.remove(material)
        except (ReferenceError, RuntimeError):
            pass


def _active_material_output(node_tree):
    outputs = [
        node for node in node_tree.nodes
        if node.type == 'OUTPUT_MATERIAL'
    ]
    return next((node for node in outputs if node.is_active_output), outputs[0] if outputs else None)


def _find_reachable_principled(output_node):
    if not output_node:
        return None
    surface = output_node.inputs.get("Surface")
    pending = [link.from_node for link in surface.links] if surface else []
    visited = set()
    while pending:
        node = pending.pop(0)
        node_ptr = node.as_pointer()
        if node_ptr in visited:
            continue
        visited.add(node_ptr)
        if node.type == 'BSDF_PRINCIPLED':
            return node
        for socket in node.inputs:
            pending.extend(link.from_node for link in socket.links)
    return None


def _emission_source_for_channel(node_tree, output_node, channel):
    if channel == "Displacement":
        displacement = output_node.inputs.get("Displacement") if output_node else None
        if displacement and displacement.is_linked:
            source = displacement.links[0].from_socket
            source_node = source.node
            if source_node.type == 'DISPLACEMENT':
                height = source_node.inputs.get("Height")
                if height:
                    if height.is_linked:
                        return height.links[0].from_socket, None
                    return None, height.default_value
            return source, None
        return None, 0.0

    principled = _find_reachable_principled(output_node)
    socket = principled.inputs.get(channel) if principled else None
    if not socket:
        if channel == "Base Color":
            return None, 0.8
        if channel == "Alpha":
            return None, 1.0
        return None, 0.0
    if socket.is_linked:
        return socket.links[0].from_socket, None
    return None, socket.default_value


def _value_as_emission_color(value):
    try:
        values = tuple(value)
    except TypeError:
        scalar = float(value)
        return (scalar, scalar, scalar, 1.0)
    if len(values) >= 4:
        return values[:4]
    if len(values) == 3:
        return (*values, 1.0)
    scalar = float(values[0]) if values else 0.0
    return (scalar, scalar, scalar, 1.0)


def _route_materials_to_emission(materials, channel, report_func):
    """Temporarily expose one material channel through emission for data baking."""
    restorations = []
    warned = set()
    for material in materials:
        if not material or not material.use_nodes or not material.node_tree:
            continue
        tree = material.node_tree
        output = _active_material_output(tree)
        surface = output.inputs.get("Surface") if output else None
        if not output or not surface:
            if material.name not in warned:
                report_func({'WARNING'}, f"'{material.name}' has no active Material Output; {channel} uses its fallback value.")
                warned.add(material.name)
            continue

        original_source = surface.links[0].from_socket if surface.is_linked else None
        source_socket, default_value = _emission_source_for_channel(
            tree,
            output,
            channel,
        )
        if source_socket is None and channel != "Displacement" and _find_reachable_principled(output) is None:
            if material.name not in warned:
                report_func(
                    {'WARNING'},
                    f"'{bake_material_export_name(material)}' has no reachable Principled shader; {channel} uses a safe fallback.",
                )
                warned.add(material.name)

        emission = tree.nodes.new(type='ShaderNodeEmission')
        emission.name = f"GReady_PurePlay_{channel}"
        emission.label = f"Temporary PurePlay {channel}"
        emission.inputs["Strength"].default_value = 1.0
        if source_socket is not None:
            tree.links.new(source_socket, emission.inputs["Color"])
        else:
            emission.inputs["Color"].default_value = _value_as_emission_color(default_value)

        for link in tuple(surface.links):
            tree.links.remove(link)
        tree.links.new(emission.outputs["Emission"], surface)
        restorations.append((tree, surface, original_source, emission))
    return restorations


def _restore_emission_routes(restorations):
    for tree, surface, original_source, emission in reversed(restorations):
        try:
            for link in tuple(surface.links):
                tree.links.remove(link)
            if original_source is not None:
                tree.links.new(original_source, surface)
            tree.nodes.remove(emission)
        except (ReferenceError, RuntimeError):
            pass


def compose_bake_pureplay_ord(ao_path, roughness_path, displacement_path, output_path):
    """Write PurePlay R=AO, G=Roughness, B=Displacement without Python pixel arrays."""
    try:
        import OpenImageIO as oiio
    except Exception as exc:
        raise RuntimeError("Blender's OpenImageIO module is required to pack PurePlay ORD.") from exc

    roughness = oiio.ImageBuf(roughness_path)
    displacement = oiio.ImageBuf(displacement_path)
    if roughness.has_error:
        raise RuntimeError(roughness.geterror() or "Could not read Roughness bake.")
    if displacement.has_error:
        raise RuntimeError(displacement.geterror() or "Could not read Displacement bake.")
    sources = [roughness, displacement]
    ao = None
    if ao_path:
        ao = oiio.ImageBuf(ao_path)
        if ao.has_error:
            raise RuntimeError(ao.geterror() or "Could not read AO bake.")
        sources.append(ao)
    dimensions = {(source.spec().width, source.spec().height) for source in sources}
    if len(dimensions) != 1:
        raise RuntimeError("PurePlay component map dimensions do not match.")

    if ao is not None:
        red = oiio.ImageBufAlgo.channels(ao, (0,), ("R",))
    else:
        spec = roughness.spec()
        red = oiio.ImageBuf(oiio.ImageSpec(spec.width, spec.height, 1, oiio.FLOAT))
        if not oiio.ImageBufAlgo.fill(red, (1.0,)):
            raise RuntimeError(red.geterror() or "Could not create the white AO channel.")
    green = oiio.ImageBufAlgo.channels(roughness, (0,), ("G",))
    blue = oiio.ImageBufAlgo.channels(displacement, (0,), ("B",))
    red_green = oiio.ImageBufAlgo.channel_append(red, green)
    packed = oiio.ImageBufAlgo.channel_append(red_green, blue)
    if not packed.write(output_path):
        raise RuntimeError(packed.geterror() or "Could not write packed PurePlay ORD.")


def compose_bake_basecolor_alpha(base_color_path, opacity_path, output_path):
    """Embed the material Alpha channel into the PurePlay BaseColor PNG."""
    try:
        import OpenImageIO as oiio
    except Exception as exc:
        raise RuntimeError("Blender's OpenImageIO module is required to compose BaseColor alpha.") from exc

    base_color = oiio.ImageBuf(base_color_path)
    opacity = oiio.ImageBuf(opacity_path)
    if base_color.has_error:
        raise RuntimeError(base_color.geterror() or "Could not read BaseColor bake.")
    if opacity.has_error:
        raise RuntimeError(opacity.geterror() or "Could not read Opacity bake.")
    if (
        base_color.spec().width != opacity.spec().width
        or base_color.spec().height != opacity.spec().height
    ):
        raise RuntimeError("BaseColor and Opacity bake dimensions do not match.")

    rgb = oiio.ImageBufAlgo.channels(base_color, (0, 1, 2), ("R", "G", "B"))
    alpha = oiio.ImageBufAlgo.channels(opacity, (0,), ("A",))
    rgba = oiio.ImageBufAlgo.channel_append(rgb, alpha)
    # PNG stores straight (unassociated) alpha. Without this metadata OIIO
    # treats the RGB input as premultiplied and unpremultiplies it on write.
    rgba.specmod().attribute("oiio:UnassociatedAlpha", 1)
    if not rgba.write(output_path):
        raise RuntimeError(rgba.geterror() or "Could not write PurePlay BaseColor alpha.")


def _install_pureplay_outputs_transactionally(staged_files, output_root, run_id):
    if not os.path.isdir(output_root):
        raise RuntimeError(f"Base output directory does not exist: '{output_root}'.")
    target_dir = os.path.join(output_root, "Baked Maps")
    os.makedirs(target_dir, exist_ok=True)
    backup_dir = tempfile.mkdtemp(prefix=f"gready_pureplay_backup_{run_id}_")
    installed = []
    backups = []
    try:
        for source_path in staged_files:
            file_name = os.path.basename(source_path)
            target_path = os.path.join(target_dir, file_name)
            if os.path.exists(target_path):
                backup_path = os.path.join(backup_dir, file_name)
                os.replace(target_path, backup_path)
                backups.append((backup_path, target_path))
            temp_target = f"{target_path}.{run_id}.tmp"
            shutil.copy2(source_path, temp_target)
            os.replace(temp_target, target_path)
            installed.append(target_path)
        return installed
    except Exception:
        for target_path in installed:
            try:
                if os.path.exists(target_path):
                    os.remove(target_path)
            except OSError:
                pass
        for backup_path, target_path in reversed(backups):
            try:
                if os.path.exists(backup_path):
                    os.replace(backup_path, target_path)
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)


def execute_pureplay_bake(operator, context):
    """Run the standard bake operator as a staged PurePlay multipass export."""
    scene = context.scene
    run_id = uuid.uuid4().hex[:10]
    output_root = bpy.path.abspath(scene.bake_output_dir or "//")
    if not os.path.isdir(output_root):
        operator.report({'ERROR'}, f"Base output directory does not exist: '{output_root}'.")
        return {'CANCELLED'}
    affected_objects = _pureplay_related_bake_objects(context)
    if not affected_objects:
        operator.report({'ERROR'}, "PurePlay Bake requires at least one selected mesh ending with '_low'.")
        return {'CANCELLED'}

    stage_root = tempfile.mkdtemp(prefix=f"gready_pureplay_{run_id}_")
    assignments = []
    material_copies = []
    state = {
        "bake_output_dir": scene.bake_output_dir,
        "bake_image_format": scene.bake_image_format,
        "bake_map_type": scene.bake_map_type,
        "png_color_mode": scene.baker_png_color_mode,
        "png_bit_depth": scene.baker_png_bit_depth,
        "use_aa": scene.baker_use_compositor_aa,
        "normal_g": scene.render.bake.normal_g,
    }
    passes = [
        ("BaseColor", 'EMIT', "Base Color"),
        ("Opacity", 'EMIT', "Alpha"),
        ("Metallic", 'EMIT', "Metallic"),
        ("Normal", 'NORMAL', None),
    ]
    if scene.baker_pureplay_bake_ao:
        passes.append(("AO", 'AO', None))
    passes.extend([
        ("Roughness", 'ROUGHNESS', None),
        ("Displacement", 'EMIT', "Displacement"),
    ])

    success = False
    try:
        assignments, material_copies = _isolate_pureplay_materials(
            affected_objects,
            run_id,
        )
        scene[PUREPLAY_INTERNAL_TAG] = True
        scene[PUREPLAY_RUN_TAG] = run_id
        scene.bake_output_dir = stage_root
        scene.bake_image_format = 'PNG'
        scene.baker_png_color_mode = 'RGB'
        scene.baker_png_bit_depth = '8'
        scene.baker_use_compositor_aa = False

        for map_name, bake_type, emission_channel in passes:
            routes = []
            try:
                scene[PUREPLAY_MAP_NAME_TAG] = map_name
                scene.bake_map_type = bake_type
                if bake_type == 'NORMAL':
                    scene.render.bake.normal_g = 'NEG_Y'
                routes = _route_materials_to_emission(
                    material_copies,
                    emission_channel,
                    operator.report,
                ) if emission_channel else []
                operator.report({'INFO'}, f"PurePlay: baking {map_name}...")
                result = bpy.ops.baker.bake()
                if 'FINISHED' not in result or not scene.get(PUREPLAY_LAST_SUCCESS_TAG, False):
                    raise RuntimeError(f"{map_name} pass did not finish successfully.")
            finally:
                _restore_emission_routes(routes)

        component_dir = os.path.join(stage_root, "Baked Maps")
        if not os.path.isdir(component_dir):
            raise RuntimeError("PurePlay component output folder was not created.")
        base_files = sorted(
            name for name in os.listdir(component_dir)
            if name.endswith("_BaseColor.png")
        )
        if not base_files:
            raise RuntimeError("PurePlay BaseColor output was not produced.")

        final_dir = os.path.join(stage_root, "PurePlay Final")
        os.makedirs(final_dir, exist_ok=True)
        staged_final = []
        for base_file in base_files:
            prefix = base_file[:-len("_BaseColor.png")]
            required = {
                "BaseColor": os.path.join(component_dir, f"{prefix}_BaseColor.png"),
                "Opacity": os.path.join(component_dir, f"{prefix}_Opacity.png"),
                "Metallic": os.path.join(component_dir, f"{prefix}_Metallic.png"),
                "Normal": os.path.join(component_dir, f"{prefix}_Normal.png"),
                "Roughness": os.path.join(component_dir, f"{prefix}_Roughness.png"),
                "Displacement": os.path.join(component_dir, f"{prefix}_Displacement.png"),
            }
            missing = [label for label, path in required.items() if not os.path.isfile(path)]
            if missing:
                raise RuntimeError(f"'{prefix}' is missing PurePlay component(s): {', '.join(missing)}.")
            base_color_destination = os.path.join(
                final_dir,
                os.path.basename(required["BaseColor"]),
            )
            compose_bake_basecolor_alpha(
                required["BaseColor"],
                required["Opacity"],
                base_color_destination,
            )
            staged_final.append(base_color_destination)
            for label in ("Metallic", "Normal"):
                destination = os.path.join(final_dir, os.path.basename(required[label]))
                shutil.copy2(required[label], destination)
                staged_final.append(destination)
            ao_path = os.path.join(component_dir, f"{prefix}_AO.png")
            if scene.baker_pureplay_bake_ao and not os.path.isfile(ao_path):
                raise RuntimeError(f"'{prefix}' is missing its AO component.")
            ord_path = os.path.join(
                final_dir,
                f"{prefix}_OcclusionRoughnessDisplacement.png",
            )
            compose_bake_pureplay_ord(
                ao_path if scene.baker_pureplay_bake_ao else None,
                required["Roughness"],
                required["Displacement"],
                ord_path,
            )
            staged_final.append(ord_path)

        installed = _install_pureplay_outputs_transactionally(
            staged_final,
            output_root,
            run_id,
        )
        operator.report(
            {'INFO'},
            f"PurePlay export finished: {len(installed)} PNG texture(s) saved to '{os.path.join(output_root, 'Baked Maps')}'.",
        )
        success = True
        return {'FINISHED'}
    except Exception as exc:
        operator.report({'ERROR'}, f"PurePlay export failed: {exc}")
        return {'CANCELLED'}
    finally:
        _restore_pureplay_materials(assignments, material_copies)
        scene.bake_output_dir = state["bake_output_dir"]
        scene.bake_image_format = state["bake_image_format"]
        scene.bake_map_type = state["bake_map_type"]
        scene.baker_png_color_mode = state["png_color_mode"]
        scene.baker_png_bit_depth = state["png_bit_depth"]
        scene.baker_use_compositor_aa = state["use_aa"]
        scene.render.bake.normal_g = state["normal_g"]
        for key in (
            PUREPLAY_INTERNAL_TAG,
            PUREPLAY_RUN_TAG,
            PUREPLAY_MAP_NAME_TAG,
            PUREPLAY_LAST_SUCCESS_TAG,
        ):
            try:
                if key in scene:
                    del scene[key]
            except (KeyError, RuntimeError):
                pass
        for image in tuple(bpy.data.images):
            if image.get("_gready_pureplay_run") == run_id:
                try:
                    bpy.data.images.remove(image)
                except RuntimeError:
                    pass
        shutil.rmtree(stage_root, ignore_errors=True)
        if not success:
            operator.report({'INFO'}, "Existing PurePlay output files were left unchanged.")


# --- Operators ---

class BAKER_OT_AutoRename(Operator):
    # --- (AutoRename Operator remains unchanged from previous version) ---
    """Rename selected objects numerically or based on pairs, MOVE to collections, and match/rename cages."""
    bl_idname = "baker.auto_rename"
    bl_label = "Auto Rename Selected"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context):
        scene = context.scene
        rename_mode = scene.baker_rename_mode # <-- Get selected mode
        initial_selection = context.selected_objects[:]
        cage_pattern = re.compile(r"_cage(\.\d+)?$")
        selected_meshes = []
        selected_cages = []
        original_names = {} # Store original names before temp rename

        for o in initial_selection:
            if o.type == 'MESH':
                original_names[o.name] = o.name # Store original name
                if cage_pattern.search(o.name):
                    selected_cages.append(o)
                else:
                    selected_meshes.append(o)

        if not selected_meshes and not selected_cages:
            self.report({'WARNING'}, "No non-cage mesh objects or cage objects selected.")
            return {'CANCELLED'}
        if not selected_meshes and selected_cages:
             self.report({'INFO'}, "Only cage objects selected. Cannot perform low/high pairing. Select non-cage meshes too.")
             return {'CANCELLED'}

        selected_meshes.sort(key=lambda o: o.name)
        numbered_objects = []
        temp_name_to_original = {} # Map temp name back to original

        self.report({'INFO'}, f"Processing {len(selected_meshes)} non-cage mesh object(s)...")
        for i, obj in enumerate(selected_meshes):
            original_name = obj.name # Get original name before temp rename
            temp_name = f"temp_{i}"
            try:
                obj.name = temp_name
                numbered_objects.append(obj)
                temp_name_to_original[temp_name] = original_name # Store mapping
            except Exception as e:
                self.report({'ERROR'}, f"Failed initial rename '{original_name}' to '{temp_name}': {e}. Skipping.")
                try:
                    obj.name = original_name # Try to revert name
                except: pass
                continue

        if not numbered_objects:
            self.report({'ERROR'}, "Could not rename any selected non-cage mesh objects.")
            # Revert names if possible
            for temp_n, orig_n in temp_name_to_original.items():
                obj = bpy.data.objects.get(temp_n)
                if obj:
                    try: obj.name = orig_n
                    except: pass
            return {'CANCELLED'}

        use_bbox = scene.baker_rename_use_bbox
        use_pivot = scene.baker_rename_use_pivot
        bbox_threshold_m = scene.baker_rename_bbox_threshold / 100.0
        pivot_threshold_m = scene.baker_rename_pivot_threshold / 100.0

        low_poly_pairs = {} # temp_name: object
        high_poly_pairs = {} # temp_name: object
        processed_indices = set()
        final_rename_map = {} # temp_name: final_name
        final_low_objects_map = {} # temp_name: final_low_object (after rename)
        final_base_names = {} # temp_name_low: base_name

        if (use_bbox or use_pivot) and len(numbered_objects) >= 2:
            num_numbered = len(numbered_objects)
            pair_index = 0 # For numerical mode

            for i in range(num_numbered):
                if i in processed_indices: continue
                obj_i = numbered_objects[i]
                temp_name_i = obj_i.name
                potential_pair_j = -1 # Index of the matched object j
                match_found = False

                # --- Refined Pairing Logic ---
                if use_bbox and use_pivot:
                    # 1. Find all BBox matches
                    bbox_matches_indices = []
                    for j in range(i + 1, num_numbered):
                        if j in processed_indices: continue
                        obj_j = numbered_objects[j]
                        if all(abs(d1 - d2) < bbox_threshold_m for d1, d2 in zip(sorted(obj_i.dimensions), sorted(obj_j.dimensions))):
                            bbox_matches_indices.append(j)

                    # 2. Process BBox matches
                    if len(bbox_matches_indices) == 1:
                        # Unique BBox match found
                        potential_pair_j = bbox_matches_indices[0]
                        match_found = True
                        self.report({'DEBUG'}, f"Unique BBox match for {i}: {potential_pair_j}")
                    elif len(bbox_matches_indices) > 1:
                        # Multiple BBox matches, filter by pivot
                        self.report({'DEBUG'}, f"Multiple BBox matches for {i}: {bbox_matches_indices}. Checking pivots...")
                        closest_j = -1
                        min_pivot_dist_sq = pivot_threshold_m * pivot_threshold_m
                        found_unique_closest = False
                        multiple_equally_close = False

                        for j_bbox in bbox_matches_indices:
                            obj_j_bbox = numbered_objects[j_bbox]
                            pivot_dist_sq = (obj_i.matrix_world.translation - obj_j_bbox.matrix_world.translation).length_squared
                            if pivot_dist_sq < min_pivot_dist_sq:
                                min_pivot_dist_sq = pivot_dist_sq
                                closest_j = j_bbox
                                found_unique_closest = True
                                multiple_equally_close = False # Reset flag
                            elif math.isclose(pivot_dist_sq, min_pivot_dist_sq, rel_tol=1e-9, abs_tol=1e-9):
                                # Found another object equally close
                                multiple_equally_close = True
                                found_unique_closest = False # No longer unique

                        if found_unique_closest and not multiple_equally_close:
                            potential_pair_j = closest_j
                            match_found = True
                            self.report({'DEBUG'}, f"  Found unique closest pivot match: {potential_pair_j}")
                        elif multiple_equally_close:
                             self.report({'DEBUG'}, f"  Multiple pivots equally close for BBox matches of {i}. No pair.")
                        else:
                             self.report({'DEBUG'}, f"  No pivot match within threshold for BBox matches of {i}.")
                    # else: No BBox matches found

                elif use_bbox: # Only BBox enabled
                    for j in range(i + 1, num_numbered):
                        if j in processed_indices: continue
                        obj_j = numbered_objects[j]
                        if all(abs(d1 - d2) < bbox_threshold_m for d1, d2 in zip(sorted(obj_i.dimensions), sorted(obj_j.dimensions))):
                            potential_pair_j = j
                            match_found = True
                            break # Take the first BBox match

                elif use_pivot: # Only Pivot enabled
                    for j in range(i + 1, num_numbered):
                        if j in processed_indices: continue
                        obj_j = numbered_objects[j]
                        if (obj_i.matrix_world.translation - obj_j.matrix_world.translation).length < pivot_threshold_m:
                            potential_pair_j = j
                            match_found = True
                            break # Take the first Pivot match
                # --- End Refined Pairing Logic ---

                if match_found and potential_pair_j != -1:
                    obj_j = numbered_objects[potential_pair_j]
                    temp_name_j = obj_j.name

                    poly_i, poly_j = utils.get_poly_count(obj_i), utils.get_poly_count(obj_j)
                    low_obj, high_obj = (obj_i, obj_j) if poly_i <= poly_j else (obj_j, obj_i)
                    temp_name_low = low_obj.name
                    temp_name_high = high_obj.name

                    if poly_i == poly_j:
                        self.report({'WARNING'}, f"Objects '{temp_name_to_original.get(temp_name_i, temp_name_i)}' & '{temp_name_to_original.get(temp_name_j, temp_name_j)}' same poly count ({poly_i}), assigned arbitrarily.")

                    # Determine Base Name based on Mode (using original names)
                    base_name = ""
                    if rename_mode == 'NUMERICAL':
                        base_name = str(pair_index)
                        pair_index += 1
                    elif rename_mode == 'HIGH_TO_LOW':
                        original_high_name = temp_name_to_original.get(temp_name_high, temp_name_high)
                        base_name = _clean_name(original_high_name)
                    elif rename_mode == 'LOW_TO_HIGH':
                        original_low_name = temp_name_to_original.get(temp_name_low, temp_name_low)
                        base_name = _clean_name(original_low_name)

                    if not base_name:
                        self.report({'WARNING'}, f"Could not determine valid base name for pair ({temp_name_low}, {temp_name_high}). Skipping.")
                        continue # Skip to next i

                    # Check for internal conflicts before assigning
                    if temp_name_low in low_poly_pairs or temp_name_high in high_poly_pairs:
                         self.report({'WARNING'}, f"Internal naming conflict detected for base '{base_name}'. Skipping pair ({temp_name_low}, {temp_name_high}).")
                    else:
                        low_poly_pairs[temp_name_low] = low_obj
                        high_poly_pairs[temp_name_high] = high_obj
                        processed_indices.add(i)
                        processed_indices.add(potential_pair_j) # Mark j as processed too
                        final_rename_map[temp_name_low] = f"{base_name}_low"
                        final_rename_map[temp_name_high] = f"{base_name}_high"
                        final_base_names[temp_name_low] = base_name

        # --- Rest of the function (Renaming, Moving, Cage Handling, Reporting) remains the same ---
        low_col = utils.get_collection("Low")
        high_col = utils.get_collection("High")
        renamed_count, moved_count = 0, 0
        processed_temp_names = set()
        processed_final_high_names = set()

        # Rename and move pairs
        for temp_name, low_obj in low_poly_pairs.items():
            if temp_name in final_rename_map:
                try:
                    new_name = final_rename_map[temp_name]
                    low_obj.name = new_name
                    utils.add_object_to_collection(low_obj, low_col)
                    final_low_objects_map[temp_name] = low_obj # Store final object mapped from temp name
                    renamed_count += 1
                    moved_count += 1
                    processed_temp_names.add(temp_name)
                except Exception as e:
                    self.report({'ERROR'}, f"Failed rename/move low '{temp_name}'->'{new_name}': {e}")
                    # Try to revert name if possible
                    try: low_obj.name = temp_name
                    except: pass

        for temp_name, high_obj in high_poly_pairs.items():
             if temp_name in final_rename_map:
                try:
                    new_name = final_rename_map[temp_name]
                    high_obj.name = new_name
                    utils.add_object_to_collection(high_obj, high_col)
                    renamed_count += 1
                    moved_count += 1
                    processed_temp_names.add(temp_name)
                    processed_final_high_names.add(new_name)
                except Exception as e:
                    self.report({'ERROR'}, f"Failed rename/move high '{temp_name}'->'{new_name}': {e}")
                    # Try to revert name if possible
                    try: high_obj.name = temp_name
                    except: pass

        # Handle suffixed high-polys (move only)
        suffixed_high_moved_count = 0
        if processed_final_high_names:
            self.report({'INFO'}, "Checking for suffixed high-poly meshes (_high.###) to move...")
            suffixed_high_pattern = re.compile(r"^(.*_high)\.(\d+)$")
            all_scene_objects = list(bpy.data.objects) # Use a copy

            for obj in all_scene_objects:
                # Check if object still exists and is a mesh
                if not obj or obj.name not in bpy.data.objects or obj.type != 'MESH':
                    continue
                match = suffixed_high_pattern.match(obj.name)
                if match:
                    base_high_name = match.group(1)
                    if base_high_name in processed_final_high_names:
                        try:
                            if high_col not in obj.users_collection:
                                utils.add_object_to_collection(obj, high_col)
                                suffixed_high_moved_count += 1
                        except Exception as e:
                            self.report({'WARNING'}, f"Failed to move suffixed high-poly '{obj.name}': {e}")

        # Rename and move cages
        renamed_cages_count = 0
        skipped_cages_count = 0
        moved_cages_count = 0
        if selected_cages:
            self.report({'INFO'}, f"Processing {len(selected_cages)} selected cage object(s) (including .### suffixes)...")
            cage_col = utils.get_collection("Cage")
            processed_cage_names = set()
            assigned_target_cage_names = set()
            selected_cages.sort(key=lambda o: o.name)

            for cage_obj in selected_cages:
                if cage_obj.name in processed_cage_names: continue
                poly_cage = utils.get_poly_count(cage_obj)
                pivot_cage = cage_obj.matrix_world.translation
                match_found_for_cage = False

                # Match cage to the *final* low poly object based on polycount/pivot
                for temp_low_name, final_low_obj in final_low_objects_map.items():
                    try:
                        # Ensure final_low_obj is still valid
                        if not final_low_obj or final_low_obj.name not in bpy.data.objects:
                            continue

                        poly_low = utils.get_poly_count(final_low_obj)
                        pivot_low = final_low_obj.matrix_world.translation

                        if poly_cage == poly_low and (pivot_cage - pivot_low).length < pivot_threshold_m:
                            base_name = final_base_names.get(temp_low_name) # Get base name using the temp_low_name key
                            if base_name is None:
                                self.report({'WARNING'}, f"Could not find base name for low poly {final_low_obj.name}. Skipping cage match for {cage_obj.name}.")
                                continue

                            new_cage_name = f"{base_name}_cage"

                            # Check if target name already assigned in this run
                            if new_cage_name in assigned_target_cage_names:
                                self.report({'INFO'}, f"Target cage name '{new_cage_name}' already assigned in this run. Skipping rename for {cage_obj.name}.")
                                skipped_cages_count += 1; match_found_for_cage = True; break

                            # Check if target name exists as a *different* object
                            existing_obj = bpy.data.objects.get(new_cage_name)
                            if existing_obj and existing_obj != cage_obj:
                                self.report({'WARNING'}, f"Target cage name '{new_cage_name}' already exists and is a different object. Skipping rename for {cage_obj.name}.")
                                skipped_cages_count += 1; match_found_for_cage = True; break

                            # Perform rename and move
                            try:
                                original_cage_name = cage_obj.name
                                cage_obj.name = new_cage_name
                                renamed_cages_count += 1
                                if cage_col not in cage_obj.users_collection:
                                    utils.add_object_to_collection(cage_obj, cage_col)
                                    moved_cages_count += 1
                                cage_obj.display_type = 'SOLID'
                                self.report({'INFO'}, f"Matched and renamed cage '{original_cage_name}' to '{new_cage_name}' based on '{final_low_obj.name}'.")
                                processed_cage_names.add(original_cage_name) # Mark original name as processed
                                assigned_target_cage_names.add(new_cage_name)
                                match_found_for_cage = True
                                break # Found match for this cage
                            except Exception as e:
                                self.report({'ERROR'}, f"Failed to rename/move cage '{cage_obj.name}' to '{new_cage_name}': {e}")
                                try: cage_obj.name = original_cage_name # Attempt revert
                                except: pass
                                skipped_cages_count += 1; match_found_for_cage = True; break # Stop trying for this cage
                    except ReferenceError:
                         self.report({'WARNING'}, f"Low poly object associated with {temp_low_name} no longer exists. Cannot compare with cage {cage_obj.name}.")
                         continue

                # Handle cages that didn't find a match
                if not match_found_for_cage:
                    if cage_col not in cage_obj.users_collection:
                         try:
                             utils.add_object_to_collection(cage_obj, cage_col)
                             moved_cages_count += 1
                             self.report({'INFO'}, f"Moved unmatched cage '{cage_obj.name}' to 'Cage' collection.")
                         except Exception as e:
                             self.report({'WARNING'}, f"Failed to move unmatched cage '{cage_obj.name}' to 'Cage' collection: {e}")
                    else:
                         self.report({'INFO'}, f"No matching low poly object found for cage '{cage_obj.name}'. It was not renamed.")
                    skipped_cages_count += 1
                    processed_cage_names.add(cage_obj.name) # Mark as processed even if not matched/renamed

        # Handle unpaired objects
        unpaired_count = 0
        reverted_count = 0
        numerically_renamed_unpaired = 0
        next_unpaired_num = 0 # Keep track of the next number for unpaired numerical rename
        for obj in numbered_objects:
            # Check if object still exists and has the temp name
            if obj and obj.name in bpy.data.objects and obj.name.startswith("temp_") and obj.name not in processed_temp_names:
                unpaired_count += 1
                original_name = temp_name_to_original.get(obj.name, obj.name) # Get original name
                try:
                    if rename_mode == 'NUMERICAL':
                        # Find the next available number for unpaired objects
                        while True:
                            final_unpaired_name = str(next_unpaired_num)
                            if bpy.data.objects.get(final_unpaired_name) is None:
                                break
                            next_unpaired_num += 1
                        obj.name = final_unpaired_name
                        numerically_renamed_unpaired += 1
                    else:
                        # Revert to original name for non-numerical modes
                        obj.name = original_name
                        reverted_count += 1
                except Exception as e:
                    self.report({'WARNING'}, f"Failed to rename/revert unpaired object '{obj.name}' (original: '{original_name}'): {e}")
                    # Leave with temp name if revert fails

        # Final Report
        report_parts = []
        if renamed_count > 0: report_parts.append(f"Renamed {renamed_count} low/high objects ({len(low_poly_pairs)} pairs)")
        if moved_count > 0: report_parts.append(f"moved {moved_count} low/high objects to collections")
        if suffixed_high_moved_count > 0: report_parts.append(f"moved {suffixed_high_moved_count} suffixed high-poly meshes to 'High' collection")
        if unpaired_count > 0:
            if numerically_renamed_unpaired > 0:
                report_parts.append(f"{numerically_renamed_unpaired} unpaired objects renamed numerically")
            elif reverted_count > 0:
                 report_parts.append(f"{reverted_count} unpaired objects reverted to original names")
            else:
                 report_parts.append(f"{unpaired_count} unpaired objects kept temporary names due to errors")
        if selected_cages: report_parts.append(f"Processed {len(selected_cages)} cages (Renamed: {renamed_cages_count}, Moved: {moved_cages_count}, Skipped Rename: {skipped_cages_count})")
        if not report_parts:
             report_parts.append("No significant renaming or moving occurred.")

        self.report({'INFO'}, ". ".join(report_parts) + ".")
        return {'FINISHED'}


class OBJECT_OT_create_cages_from_list(Operator):
    """Create Cage meshes, apply offset, COLOR Low/High/Cage objects, and set viewport shading."""
    bl_idname = "object.create_cages_from_list"
    bl_label = "Create Cages"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Create cage objects for the selected mesh objects only"

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context):
        scene = context.scene
        low_col = utils.get_collection("Low")
        high_col = utils.get_collection("High")
        cage_col = utils.get_collection("Cage")

        # Create cages only for the current explicit mesh selection.
        selected_meshes = [o for o in context.selected_objects if o.type == 'MESH']
        if selected_meshes:
            for mesh in selected_meshes:
                # Add _low suffix if not already present
                if not mesh.name.endswith("_low"):
                    original_name = mesh.name
                    mesh.name = f"{mesh.name}_low"
                    self.report({'INFO'}, f"Added '_low' suffix to '{original_name}' -> '{mesh.name}'")
                if low_col not in mesh.users_collection:
                    utils.add_object_to_collection(mesh, low_col)
            valid_low_poly_objects = selected_meshes
            self.report({'INFO'}, f"Using {len(valid_low_poly_objects)} selected mesh object(s) for cage creation.")
        else:
            self.report({'ERROR'}, "No selected mesh objects found.")
            return {'CANCELLED'}

        if not valid_low_poly_objects:
            self.report({'ERROR'}, "No valid selected mesh objects found.")
            return {'CANCELLED'}

        cage_offset_cm = scene.baker_cage_offset
        cage_offset_m = cage_offset_cm / 100.0
        low_poly_color = (1.0, 1.0, 1.0, 1.0)
        high_poly_color = (1.0, 0.251, 0.290, 1.0)
        created_cages = []
        created_count, failed_count, skipped_count = 0, 0, 0
        colored_low_count, colored_high_count = 0, 0
        edge_split_applied_count = 0

        self.report({'INFO'}, "Applying colors to Low and High poly objects...")
        high_poly_pattern = re.compile(r"_high(\.\d+)?$")
        processed_low_names = set()

        for low_obj in valid_low_poly_objects:
            if low_obj.name in processed_low_names: continue
            processed_low_names.add(low_obj.name)
            try:
                if tuple(low_obj.color) != low_poly_color:
                    low_obj.color = low_poly_color
                    colored_low_count += 1
                low_obj.show_transparent = (low_poly_color[3] < 1.0)
                if low_obj.name.endswith("_low"):
                    base_name = low_obj.name[:-4]
                    high_base_name = f"{base_name}_high"
                    specific_high_pattern = re.compile(rf"^{re.escape(high_base_name)}(\.\d+)?$")
                    high_polys_found_for_this_low = 0
                    for obj in bpy.data.objects:
                        if obj.type == 'MESH' and specific_high_pattern.match(obj.name):
                            if tuple(obj.color) != high_poly_color:
                                obj.color = high_poly_color
                                high_polys_found_for_this_low += 1
                            obj.show_transparent = (high_poly_color[3] < 1.0)
                            if high_col not in obj.users_collection:
                                utils.add_object_to_collection(obj, high_col)
                    if high_polys_found_for_this_low > 0:
                        colored_high_count += high_polys_found_for_this_low
            except Exception as e:
                self.report({'WARNING'}, f"Error applying color to {low_obj.name} or its high-polys: {e}")

        self.report({'INFO'}, "Creating cage objects...")
        for low_obj in valid_low_poly_objects:
             base_name = low_obj.name
             if base_name.endswith("_low"): base_name = base_name[:-4]
             cage_name = f"{base_name}_cage"
             if cage_name in bpy.data.objects:
                 self.report({'INFO'}, f"Cage '{cage_name}' exists. Skipping creation.")
                 skipped_count += 1
                 continue
             try:
                 new_cage = utils.create_cage_from_duplicate(low_obj, cage_name, collection=cage_col)
                 if new_cage:
                     created_cages.append(new_cage)
                     created_count += 1
                 else:
                     self.report({'WARNING'}, f"Failed create cage base for {low_obj.name}.")
                     failed_count += 1
             except Exception as e:
                 self.report({'ERROR'}, f"Unexpected error creating cage base for {low_obj.name}: {e}")
                 failed_count += 1

        if scene.baker_use_no_skew and created_cages:
            self.report({'INFO'}, "Applying 'No Skew' Edge Split modifier...")
            original_active_no_skew = context.view_layer.objects.active
            original_selection_no_skew = context.selected_objects[:]
            original_mode_no_skew = context.mode
            if original_mode_no_skew != 'OBJECT':
                try: bpy.ops.object.mode_set(mode='OBJECT')
                except RuntimeError: self.report({'ERROR'}, "Could not switch to Object mode for Edge Split.")
            for cage in created_cages:
                try:
                    bpy.ops.object.select_all(action='DESELECT')
                    cage.select_set(True)
                    context.view_layer.objects.active = cage
                    mod = cage.modifiers.new(name="TempEdgeSplit", type='EDGE_SPLIT')
                    mod.use_edge_angle = scene.baker_no_skew_use_angle
                    mod.split_angle = scene.baker_no_skew_angle
                    mod.use_edge_sharp = scene.baker_no_skew_use_sharps
                    self.report({'INFO'}, f"Added Edge Split to {cage.name} (Angle: {math.degrees(mod.split_angle):.1f} deg [{mod.use_edge_angle}], Sharps: {mod.use_edge_sharp})")
                    bpy.ops.object.modifier_apply(modifier=mod.name)
                    edge_split_applied_count += 1
                    self.report({'INFO'}, f"Applied Edge Split to {cage.name}")
                except Exception as e:
                    self.report({'WARNING'}, f"Failed to apply Edge Split to {cage.name}: {e}")
                    mod_check = cage.modifiers.get("TempEdgeSplit")
                    if mod_check:
                        try: cage.modifiers.remove(mod_check)
                        except: pass
            try:
                bpy.ops.object.select_all(action='DESELECT')
                for obj in original_selection_no_skew:
                    if obj and obj.name in bpy.data.objects: obj.select_set(True)
                if original_active_no_skew and original_active_no_skew.name in bpy.data.objects:
                    context.view_layer.objects.active = original_active_no_skew
                if original_mode_no_skew != 'OBJECT' and context.mode == 'OBJECT':
                     bpy.ops.object.mode_set(mode=original_mode_no_skew)
            except Exception as e:
                 self.report({'WARNING'}, f"Could not fully restore state after Edge Split: {e}")

        if created_cages:
            self.report({'INFO'}, f"Applying cage offset ({cage_offset_cm} cm) to {len(created_cages)} created cage(s)...")
            original_mode = bpy.context.object.mode if bpy.context.object else 'OBJECT'
            original_active = context.view_layer.objects.active
            original_selection = context.selected_objects[:]
            original_automerge_state = context.scene.tool_settings.use_mesh_automerge
            try:
                if original_mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
                bpy.ops.object.select_all(action='DESELECT')
                valid_cages_for_offset = []
                for cage in created_cages:
                    try:
                        if cage and cage.name in bpy.data.objects:
                            cage.select_set(True)
                            valid_cages_for_offset.append(cage)
                    except ReferenceError: self.report({'WARNING'}, f"Cage {cage.name} seems to have been deleted before offset.")
                if not valid_cages_for_offset: raise ValueError("No valid cages left to apply offset.")
                context.view_layer.objects.active = valid_cages_for_offset[0]
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                if context.scene.tool_settings.use_mesh_automerge:
                    context.scene.tool_settings.use_mesh_automerge = False
                    self.report({'INFO'}, "Temporarily disabled Auto Merge Vertices.")
                bpy.ops.transform.shrink_fatten(value=cage_offset_m, use_even_offset=False)
                bpy.ops.object.mode_set(mode='OBJECT')
                self.report({'INFO'}, "Cage offset applied successfully.")
            except Exception as e:
                self.report({'ERROR'}, f"Failed to apply cage offset: {e}")
                if bpy.context.object and bpy.context.object.mode != 'OBJECT':
                    try: bpy.ops.object.mode_set(mode='OBJECT')
                    except RuntimeError: pass
            finally:
                context.scene.tool_settings.use_mesh_automerge = original_automerge_state
                self.report({'INFO'}, f"Restored Auto Merge Vertices to: {original_automerge_state}")
                try:
                    if bpy.context.object and bpy.context.object.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
                    bpy.ops.object.select_all(action='DESELECT')
                    for obj in original_selection:
                        if obj and obj.name in bpy.data.objects: obj.select_set(True)
                    if original_active and original_active.name in bpy.data.objects: context.view_layer.objects.active = original_active
                except (RuntimeError, ReferenceError): pass

        action_performed = (colored_low_count > 0 or colored_high_count > 0 or created_count > 0 or skipped_count > 0 or edge_split_applied_count > 0)
        if action_performed and context.space_data and hasattr(context.space_data, 'shading'):
            try:
                if context.space_data.shading.color_type != 'OBJECT':
                    context.space_data.shading.color_type = 'OBJECT'
                    self.report({'INFO'}, "Set viewport shading color type to 'Object'.")
            except Exception as e: self.report({'ERROR'}, f"Failed to set viewport shading: {e}")
        elif action_performed:
             self.report({'WARNING'}, "Could not set viewport shading (not in 3D View context?).")

        if created_count > 0 or skipped_count > 0:
            try:
                bake_settings = context.scene.render.bake
                if not bake_settings.use_cage:
                    bake_settings.use_cage = True
                    self.report({'INFO'}, "Enabled 'Use Cage Object' in Bake settings.")
                if bake_settings.cage_extrusion != 0.001:
                    bake_settings.cage_extrusion = 0.001
                    self.report({'INFO'}, "Set Cage Extrusion to 0.001 (1mm).")
            except Exception as e:
                 self.report({'WARNING'}, f"Could not enable 'Use Cage Object' or set extrusion: {e}")

        report_parts = []
        if colored_low_count > 0: report_parts.append(f"Colored {colored_low_count} low-poly objects.")
        if colored_high_count > 0: report_parts.append(f"Colored {colored_high_count} high-poly objects.")
        if created_count > 0: report_parts.append(f"Created {created_count} cages.")
        if skipped_count > 0: report_parts.append(f"Skipped creating {skipped_count} existing cages.")
        if failed_count > 0: report_parts.append(f"Failed to create {failed_count} cages.")
        if edge_split_applied_count > 0: report_parts.append(f"Applied 'No Skew' Edge Split to {edge_split_applied_count} cages.")
        if not report_parts: report_message, report_level = "No low-poly objects found or no actions performed.", 'WARNING'
        else: report_message, report_level = " ".join(report_parts), 'WARNING' if failed_count > 0 else 'INFO'
        self.report({report_level}, report_message)
        return {'FINISHED'}


# --- Bake Operator (Original "Closest Hit" Logic) ---
class BAKER_OT_Bake(Operator):
    """Sets up bake environment then bakes maps based on current settings (Closest Hit). High->Low mode requires _low object selection."""
    bl_idname = "baker.bake"
    bl_label = "Bake Selected (Closest)" # Updated label for clarity
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Rename objects with '_low', '_high', and '_cage' suffixes if you're baking from High to Low. Select Low poly meshes and bake."

    @classmethod
    def poll(cls, context):
        scene = context.scene
        selected_objects = context.selected_objects
        if not any(o.type == 'MESH' for o in selected_objects): return False
        # Always require _low suffix in HIGH_TO_LOW mode
        return any(o.type == 'MESH' and o.name.endswith("_low") for o in selected_objects)

    def execute(self, context):
        # Check if in Local view mode
        for area in (context.screen.areas if context.screen else ()):
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        if space.local_view:
                            self.report({'ERROR'}, "Cannot bake in Local View mode! Press '/' to exit Local View before baking.")
                            return {'CANCELLED'}
        
        scene = context.scene
        if (
            getattr(scene, "baker_export_preset", BAKE_PRESET_CUSTOM)
            == BAKE_PRESET_PUREPLAY_ORD
            and not scene.get(PUREPLAY_INTERNAL_TAG, False)
        ):
            return execute_pureplay_bake(self, context)

        if scene.get(PUREPLAY_INTERNAL_TAG, False):
            scene[PUREPLAY_LAST_SUCCESS_TAG] = False
        bake_settings = scene.render.bake
        img_settings = scene.render.image_settings
        output_dir_base = scene.bake_output_dir or "//"
        img_format = scene.bake_image_format
        bake_type = scene.bake_map_type
        bake_mode = scene.baker_bake_mode
        user_wants_cage = bake_settings.use_cage
        user_max_ray_distance = bake_settings.max_ray_distance
        # Ensure default margin is 4 instead of Blender's default 16
        if bake_settings.margin == 16:
            bake_settings.margin = 4
        user_margin = bake_settings.margin

        # --- Setup Bake Environment (Same as before) ---
        self.report({'INFO'}, "Performing automatic bake environment setup...")
        view_settings = scene.view_settings
        use_gpu_preference = scene.baker_use_gpu
        user_samples = scene.baker_samples
        if scene.render.engine != 'CYCLES':
            scene.render.engine = 'CYCLES'
            self.report({'INFO'}, "  Render Engine set to Cycles.")
        else:
            self.report({'INFO'}, "  Render Engine is already Cycles.")
        cycles_settings = scene.cycles
        target_device = 'GPU' if use_gpu_preference else 'CPU'
        if cycles_settings.device != target_device:
            cycles_settings.device = target_device
            self.report({'INFO'}, f"  Cycles device set to {target_device} based on preference.")
        else:
             self.report({'INFO'}, f"  Cycles device already set to {target_device}.")
        if cycles_settings.use_adaptive_sampling:
            cycles_settings.use_adaptive_sampling = False
            self.report({'INFO'}, "  Adaptive Sampling disabled.")
        else:
            self.report({'INFO'}, "  Adaptive Sampling already disabled.")
        if cycles_settings.samples != user_samples:
            cycles_settings.samples = user_samples
            self.report({'INFO'}, f"  Samples set to {user_samples} (from UI).")
        if cycles_settings.preview_samples != user_samples:
            cycles_settings.preview_samples = user_samples
        if cycles_settings.use_preview_denoising != True:
            cycles_settings.use_preview_denoising = True
        if cycles_settings.use_denoising != False:
            cycles_settings.use_denoising = False
            self.report({'INFO'}, "  Render Denoising OFF.")
        if view_settings.view_transform != 'Standard':
            view_settings.view_transform = 'Standard'
            self.report({'INFO'}, "  Color Management set to Standard.")
        # Set margin from user setting
        bake_settings.margin = user_margin
        self.report({'INFO'}, f"  Margin set to {user_margin}px (from UI).")
        self.report({'INFO'}, "Automatic bake environment setup complete.")
        # --- End Setup ---

        # --- Determine Target Meshes (Same as before) ---
        # Always use HIGH_TO_LOW mode - target is _low suffix objects
        selected_target_meshes = [o for o in context.selected_objects if o.type == 'MESH' and o.name.endswith("_low")]
        if not selected_target_meshes:
            self.report({'ERROR'}, "High->Low mode: No selected mesh objects ending with '_low' found.")
            return {'CANCELLED'}

        # --- Gather Material Info for Naming (Same as before) ---
        unique_material_names = set()
        for target_obj in selected_target_meshes:
            if target_obj.material_slots:
                for slot in target_obj.material_slots:
                    if slot.material:
                        unique_material_names.add(slot.material.name)
        num_objects = len(selected_target_meshes)
        num_materials = len(unique_material_names)
        self.report({'INFO'}, f"Found {num_objects} target object(s) with {num_materials} unique material(s).")
        # --- End Material Info Gathering ---

        # --- Determine Scenario (Low only / High to Low / Hybrid) for direct bake (if provided by matrix bake, reuse) ---
        scenario_hint = scene.get('_gready_scenario_hint') if hasattr(scene, 'keys') and '_gready_scenario_hint' in scene.keys() else None
        if not scenario_hint:
            # Recompute scenario from selected targets
            has_any_high = False
            missing_any_high = False
            for t in selected_target_meshes:
                base = t.name[:-4] if t.name.endswith('_low') else t.name
                pattern = re.compile(rf"^{re.escape(base)}_high(\\.\\d+)?$")
                found = any(obj.type == 'MESH' and obj != t and pattern.match(obj.name) for obj in bpy.data.objects)
                if found:
                    has_any_high = True
                else:
                    missing_any_high = True
            scenario_hint = ('Low only' if not has_any_high else ('Hybrid' if missing_any_high else 'High to Low'))
        else:
            if scenario_hint not in {'Low only','Hybrid','High to Low'}:
                scenario_hint = 'Unknown'
        self.report({'INFO'}, f"Bake Scenario: {scenario_hint}")

        # --- Progress Bar Setup ---
        temp_dir = tempfile.gettempdir()
        progress_suffix = f"-{scene.get(PUREPLAY_RUN_TAG)}" if scene.get(PUREPLAY_RUN_TAG) else ""
        progress_file_path = os.path.join(temp_dir, f'g-ready_baking_progress{progress_suffix}.json')
        script_file_path = os.path.join(temp_dir, f'g-ready_baking_progress{progress_suffix}.py')
        
        progress_data = {'total': num_objects, 'current': 0, 'scenario': scenario_hint}
        with open(progress_file_path, 'w') as f:
            json.dump(progress_data, f)

        # Create progress display script
        script_content = f'''
import json
import time
import sys
import os
progress_file = r"{progress_file_path}"
try:
    with open(progress_file, 'r') as f:
        data = json.load(f)
    total = data['total']
    scenario = data.get('scenario', 'Unknown')
    print(f"Scenario: {{scenario}}")
    print(f"Total objects to bake: {{total}}")
    while True:
        try:
            if not os.path.exists(progress_file):
                break
            with open(progress_file, 'r') as f:
                data = json.load(f)
            current = data['current']
            progress = int((current / total) * 50)
            bar = '#' * progress + '-' * (50 - progress)
            print(f"\\r[{{bar}}] {{current}}/{{total}}", end='', flush=True)
            if current >= total:
                print("\\nBaking completed!")
                print("Window will close in 5 seconds...")
                time.sleep(5)
                sys.exit(0)
            time.sleep(0.5)
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.5)
            continue
finally:
    try:
        if os.path.exists(progress_file):
            os.unlink(progress_file)
    except:
        pass
'''
        with open(script_file_path, 'w') as f:
            f.write(script_content)

        # Launch progress window
        if not scene.get(PUREPLAY_INTERNAL_TAG, False):
            subprocess.Popen(['cmd.exe', '/c', 'start', 'cmd.exe', '/c', 'python', script_file_path], shell=True)
        # --- End Progress Bar Setup ---

        self.report({'INFO'}, f"Starting Bake (Closest Hit) in '{bake_mode}' mode for {num_objects} selected target object(s)...")
        initial_selection = context.selected_objects[:]
        initial_active = context.active_object
        baked_count, skipped_count, failed_count = 0, 0, 0

        resolution_preset = scene.baker_resolution_preset
        width, height = 2048, 2048
        if resolution_preset == 'CUSTOM':
            width, height = scene.baker_custom_width, scene.baker_custom_height
        else:
            width = height = int(resolution_preset)
        if width <= 0 or height <= 0:
            self.report({'ERROR'}, f"Invalid custom resolution ({width}x{height}).")
            return {'CANCELLED'}

        bake_outputs = build_bake_outputs(scene, selected_target_meshes, bake_type, width, height, self.report)
        shared_bake_image = bake_outputs[0]["image"] if bake_outputs else None
        shared_img_name = bake_output_label(bake_outputs)
        material_image_map = build_material_image_map(bake_outputs)

        if False and not shared_bake_image:
            # Create new image - MUST be GENERATED for baking to work correctly
            shared_bake_image = bpy.data.images.new(name=shared_img_name, width=width, height=height, alpha=True)
            shared_bake_image.source = 'GENERATED'  # CRITICAL: Set to GENERATED immediately!
            self.report({'INFO'}, f"Created bake image: '{shared_img_name}' ({width}x{height}) [source=GENERATED]")
        elif False:
             if shared_bake_image.size[0] != width or shared_bake_image.size[1] != height:
                 self.report({'WARNING'}, f"Reusing existing image '{shared_img_name}' but requested size differs.")
             else:
                 self.report({'INFO'}, f"Reusing existing shared bake image: '{shared_img_name}'")
             if shared_bake_image.source != 'GENERATED':
                 try:
                     shared_bake_image.source = 'GENERATED'
                     self.report({'INFO'}, f"Set source for existing image '{shared_img_name}' to GENERATED.")
                 except Exception as e:
                     self.report({'WARNING'}, f"Could not set source for existing image '{shared_img_name}': {e}")

        first_object_baked_overall = True # Track if any bake has happened in this run

        for target_obj in selected_target_meshes:
            self.report({'INFO'}, f"Processing: {target_obj.name}")
            if not target_obj or target_obj.name not in bpy.data.objects:
                self.report({'WARNING'}, f"Target object {target_obj.name} seems to have been deleted. Skipping.")
                skipped_count += 1
                continue

            # --- Set Active UV Layer (if UV override is enabled) ---
            if scene.baker_use_uv_override:
                target_uv = scene.baker_target_uv_map.strip()
                if target_uv:
                    set_active_uv_layer(target_obj, target_uv, self.report)
            # --- End UV Layer Selection ---

            # --- Material Setup (Same as before) ---
            material_setup_successful_for_object = True
            if not target_obj.material_slots:
                 self.report({'WARNING'}, f"Target object '{target_obj.name}' has no material slots. Creating one.")
                 try:
                     mat = bpy.data.materials.new(name=f"{target_obj.name}_BakeMat")
                     mat["gready_bake_temp"] = True
                     target_obj.data.materials.append(mat)
                     self.report({'INFO'}, f"Created and assigned material '{mat.name}'.")
                 except Exception as e:
                     self.report({'ERROR'}, f"Failed to create material for '{target_obj.name}': {e}")
                     failed_count += 1
                     continue

            for slot_index, slot in enumerate(target_obj.material_slots):
                mat = slot.material
                if not mat:
                    mat = bpy.data.materials.new(name=f"{target_obj.name}_Slot{slot_index}_BakeMat")
                    mat["gready_bake_temp"] = True
                    slot.material = mat
                    self.report({'INFO'}, f"Created material '{mat.name}' for slot {slot_index} on '{target_obj.name}'.")
                if not mat:
                    self.report({'ERROR'}, f"Failed to get or create material for slot {slot_index} on '{target_obj.name}'. Skipping this slot.")
                    material_setup_successful_for_object = False
                    continue
                mat.use_nodes = True
                if not mat.node_tree:
                    self.report({'ERROR'}, f"Material '{mat.name}' in slot {slot_index} does not expose a node tree after enabling nodes. Skipping this slot.")
                    material_setup_successful_for_object = False
                    continue
                nodes = mat.node_tree.nodes
                
                # ALWAYS look for or create BakeTargetImage node (ignore active/selected nodes)
                tex_node = nodes.get("BakeTargetImage")
                if tex_node and tex_node.type == 'TEX_IMAGE':
                    self.report({'INFO'}, f"Using existing BakeTargetImage node in material '{mat.name}'.")
                else:
                    # Create new BakeTargetImage node
                    tex_node = nodes.new(type='ShaderNodeTexImage')
                    tex_node.name = "BakeTargetImage"
                    tex_node.label = "BakeTargetImage"
                    # Position at specific location in shader editor
                    tex_node.location = (721.097, -4.47769)
                    self.report({'INFO'}, f"Created BakeTargetImage node in material '{mat.name}'.")
                
                if not tex_node:
                     self.report({'ERROR'}, f"Failed to find or create TEX_IMAGE node in material '{mat.name}'. Skipping slot.")
                     material_setup_successful_for_object = False
                     continue
                if material_image_map:
                    tex_node.image = material_image_map.get(mat.as_pointer(), shared_bake_image)
                else:
                    tex_node.image = shared_bake_image
                nodes.active = tex_node
                for n in nodes: n.select = False
                tex_node.select = True
            # --- End Material Setup ---

            if not material_setup_successful_for_object:
                 self.report({'ERROR'}, f"Material setup failed for one or more slots on '{target_obj.name}'. Skipping bake for this object.")
                 failed_count += 1
                 continue

            # Ensure BakeTargetImage is selected in all materials (CRITICAL for correct baking)
            if not ensure_bake_image_selected(target_obj, self.report):
                self.report({'WARNING'}, f"Could not select BakeTargetImage for '{target_obj.name}'")

            # --- Baking setup (Engine, Bake Type) ---
            if scene.render.engine != 'CYCLES':
                self.report({'WARNING'}, "Setting Render Engine to Cycles.")
                scene.render.engine = 'CYCLES'
            cycles_settings.bake_type = bake_type

            # --- Bake Type Specific Settings ---
            if bake_type == 'NORMAL':
                bake_settings.normal_space = 'TANGENT'
            if bake_type == 'DIFFUSE':
                if bake_settings.use_pass_direct: bake_settings.use_pass_direct = False
                if bake_settings.use_pass_indirect: bake_settings.use_pass_indirect = False

            # --- High/Low/Cage selection & Bake Execution (ORIGINAL LOGIC WITH LOW-ONLY FALLBACK) ---
            if bake_mode == 'HIGH_TO_LOW':
                base_name = target_obj.name[:-4]
                high_poly_pattern = re.compile(rf"^{re.escape(base_name)}_high(\.\d+)?$")
                high_poly_sources = [
                    obj for obj in bpy.data.objects
                    if obj.type == 'MESH' and obj != target_obj and high_poly_pattern.match(obj.name)
                ]

                if high_poly_sources:
                    if not bake_settings.use_selected_to_active:
                        bake_settings.use_selected_to_active = True
                        self.report({'INFO'}, f"Enabled 'Selected to Active' for '{target_obj.name}' (high poly sources detected).")

                    # --- Cage setup (Revised Logic v2) ---
                    cage_name = f"{base_name}_cage"
                    cage_obj = bpy.data.objects.get(cage_name)
                    will_use_cage_object = user_wants_cage and cage_obj is not None
                    bake_settings.use_cage = will_use_cage_object
                    bake_settings.cage_object = cage_obj if will_use_cage_object else None
                    bake_settings.max_ray_distance = user_max_ray_distance # Always set user's ray distance first
                    if will_use_cage_object:
                        self.report({'INFO'}, f"Using cage object: '{cage_obj.name}'. Max Ray Distance set to {bake_settings.max_ray_distance:.4f} (Note: Blender might ignore this when using a cage object).")
                    else:
                        if user_wants_cage and not cage_obj:
                            self.report({'WARNING'}, f"Cage object '{cage_name}' not found, but 'Use Cage Object' is enabled! Using Max Ray Distance: {bake_settings.max_ray_distance:.4f}")
                        else:
                             self.report({'INFO'}, f"Using Max Ray Distance: {bake_settings.max_ray_distance:.4f} (Cage Object disabled or not found).")
                    if not will_use_cage_object and bake_settings.cage_extrusion <= 0.0:
                        bake_settings.cage_extrusion = 0.001
                        self.report({'INFO'}, f"Set Cage Extrusion to default {bake_settings.cage_extrusion:.3f} as cage object is not used.")
                    # --- End Cage Setup ---

                    # Select ALL high poly sources and make low poly active
                    bpy.ops.object.select_all(action='DESELECT')
                    try:
                        for high_obj in high_poly_sources: # Select ALL found high-polys
                            if high_obj and high_obj.name in bpy.data.objects:
                                 high_obj.select_set(True)
                        if target_obj and target_obj.name in bpy.data.objects:
                            context.view_layer.objects.active = target_obj
                            target_obj.select_set(True)
                        else: raise ReferenceError("Target object became invalid")
                        self.report({'INFO'}, f"Selected {len(high_poly_sources)} high poly source(s) for {target_obj.name}.")
                    except ReferenceError:
                         self.report({'ERROR'}, f"One or more high/low objects became invalid before baking '{target_obj.name}'. Skipping.")
                         failed_count += 1
                         continue
                else:
                    if bake_settings.use_selected_to_active:
                        self.report({'WARNING'}, f"No high poly sources found matching pattern '{base_name}_high(.###)?' for '{target_obj.name}'. Skipping.")
                        skipped_count += 1
                        continue

                    # Low-only fallback: bake current mesh without Selected-to-Active
                    bake_settings.use_selected_to_active = False
                    bake_settings.use_cage = False
                    bpy.ops.object.select_all(action='DESELECT')
                    try:
                        if target_obj and target_obj.name in bpy.data.objects:
                            context.view_layer.objects.active = target_obj
                            target_obj.select_set(True)
                            self.report({'INFO'}, f"Baking '{target_obj.name}' without high poly sources (Selected to Active disabled).")
                        else:
                            raise ReferenceError("Target object became invalid")
                    except ReferenceError:
                        self.report({'ERROR'}, f"Target object became invalid before baking '{target_obj.name}'. Skipping.")
                        failed_count += 1
                        continue

            # Set clear flag only for the very first bake overall
            bake_settings.use_clear = first_object_baked_overall
            self.report({'INFO'}, f"  use_clear set to: {bake_settings.use_clear}")

            # --- Check for Local View Mode ---
            if context.space_data and context.space_data.local_view:
                self.report({'ERROR'}, f"Bake failed for {target_obj.name}: Viewport is in local view mode. Press '/' to exit local view and try again.")
                failed_count += 1
                continue

            # --- Perform the SINGLE Bake ---
            self.report({'INFO'}, f"Baking {bake_type} for {target_obj.name} onto '{shared_img_name}'...")
            try:
                bpy.ops.object.bake(type=bake_type) # Single bake call
                baked_count += 1
                first_object_baked_overall = False # Mark that a bake happened
                # Update progress
                progress_data['current'] += 1
                with open(progress_file_path, 'w') as f:
                    json.dump(progress_data, f)
            except RuntimeError as e:
                self.report({'ERROR'}, f"Bake failed for {target_obj.name}: {e}")
                failed_count += 1
                continue # Skip to next object if bake fails
            # --- End Original Bake Logic ---

        # --- Post Bake & Save ---
        saved_count = 0
        if baked_count > 0 and bake_outputs:
            saved_count = save_bake_outputs(context, bake_outputs, img_format, width, height, self.report)
            if saved_count == 0:
                self.report({'ERROR'}, "No baked images were saved.")

        if False and baked_count > 0 and output_dir_base and shared_bake_image:
            target_subdir_name = "Baked Maps"
            abs_output_dir_base = bpy.path.abspath(output_dir_base)
            target_subdir_path = os.path.join(abs_output_dir_base, target_subdir_name)

            if not os.path.isdir(abs_output_dir_base):
                 self.report({'ERROR'}, f"Base output directory does not exist: '{abs_output_dir_base}'. Image not saved.")
                 shared_bake_image = None
            elif not os.path.exists(target_subdir_path):
                try:
                    os.makedirs(target_subdir_path, exist_ok=True)
                    self.report({'INFO'}, f"Created output subdirectory: '{target_subdir_path}'")
                except OSError as e:
                    self.report({'ERROR'}, f"Could not create output subdir '{target_subdir_path}': {e}. Image not saved.")
                    shared_bake_image = None

            if shared_bake_image:
                file_name = f"{shared_img_name}.{img_format.lower()}"
                file_path = os.path.join(target_subdir_path, file_name)
                try:
                    # Apply Format Settings
                    img_settings.file_format = img_format
                    if img_format == 'PNG': img_settings.color_mode, img_settings.color_depth, img_settings.compression = scene.baker_png_color_mode, scene.baker_png_bit_depth, scene.baker_png_compression
                    elif img_format == 'JPEG': img_settings.quality, img_settings.color_mode = scene.baker_jpeg_quality, scene.baker_jpeg_color_mode
                    elif img_format == 'TIFF': img_settings.color_mode, img_settings.color_depth, img_settings.tiff_codec = scene.baker_tiff_color_mode, scene.baker_tiff_bit_depth, scene.baker_tiff_codec
                    elif img_format == 'TARGA': img_settings.color_mode = scene.baker_targa_color_mode
                    elif img_format == 'OPEN_EXR': img_settings.color_mode, img_settings.color_depth, img_settings.exr_codec, img_settings.use_preview = scene.baker_exr_color_mode, scene.baker_exr_bit_depth, scene.baker_exr_codec, scene.baker_exr_use_preview
                    self.report({'INFO'}, f"  Applying {img_format} save settings.")

                    # Conditional Compositor AA (remains the same)
                    use_compositor_aa = scene.baker_use_compositor_aa
                    existing_compositor_setup = (
                        scene.use_nodes and scene.node_tree and
                        (len(scene.node_tree.nodes) > 0 or len(scene.node_tree.links) > 0)
                    )
                    if use_compositor_aa and existing_compositor_setup:
                        self.report({'WARNING'}, "Skipping Compositor AA because the scene already has a compositor setup. Direct save was used instead.")
                        use_compositor_aa = False

                    if use_compositor_aa:
                        self.report({'INFO'}, "Applying Anti-Aliasing via Compositor...")
                        original_use_nodes = scene.use_nodes; original_render_filepath = scene.render.filepath; original_frame_current = scene.frame_current; original_frame_start = scene.frame_start; original_frame_end = scene.frame_end; original_resolution_x = scene.render.resolution_x; original_resolution_y = scene.render.resolution_y; original_resolution_percentage = scene.render.resolution_percentage
                        original_nodes_data = None; original_tree_ref = scene.node_tree
                        if original_use_nodes and original_tree_ref:
                            original_nodes_data = { "nodes": [(n.name, n.type, n.location.x, n.location.y) for n in original_tree_ref.nodes], "links": [(l.from_node.name, l.from_socket.name, l.to_node.name, l.to_socket.name) for l in original_tree_ref.links] }
                        tree = None
                        try:
                            scene.use_nodes = True
                            tree = scene.node_tree
                            if not tree:
                                raise RuntimeError("Scene compositor node tree is unavailable after enabling compositor nodes.")
                            tree.nodes.clear()
                            image_node = tree.nodes.new(type='CompositorNodeImage'); image_node.image = shared_bake_image; image_node.location = (0, 0)
                            aa_node = tree.nodes.new(type='CompositorNodeAntiAliasing'); aa_node.threshold = 0.1; aa_node.location = (image_node.location.x + image_node.width + 50, 0)
                            composite_node = tree.nodes.new(type='CompositorNodeComposite'); composite_node.location = (aa_node.location.x + aa_node.width + 50, 0)
                            tree.links.new(image_node.outputs['Image'], aa_node.inputs['Image']); tree.links.new(aa_node.outputs['Image'], composite_node.inputs['Image'])
                            scene.render.filepath = file_path; scene.render.resolution_x = width; scene.render.resolution_y = height; scene.render.resolution_percentage = 100; scene.frame_start = 1; scene.frame_end = 1; scene.frame_set(1)
                            bpy.ops.render.render(write_still=True, use_viewport=False)
                            self.report({'INFO'}, f"Saved Anti-Aliased image via Compositor to: {file_path}")
                        except Exception as e: self.report({'ERROR'}, f"Compositor AA failed: {e}")
                        finally:
                            scene.render.filepath = original_render_filepath; scene.frame_current = original_frame_current; scene.frame_start = original_frame_start; scene.frame_end = original_frame_end; scene.render.resolution_x = original_resolution_x; scene.render.resolution_y = original_resolution_y; scene.render.resolution_percentage = original_resolution_percentage
                            if scene.node_tree:
                                scene.node_tree.nodes.clear()
                                if original_nodes_data:
                                    nodes_map = {}
                                    for name, ntype, loc_x, loc_y in original_nodes_data["nodes"]:
                                        try:
                                            node = scene.node_tree.nodes.new(type=ntype)
                                            try: node.name = name
                                            except Exception as name_err: print(f"Warning: Could not restore node name '{name}': {name_err}. Using default.")
                                            node.location = (loc_x, loc_y); nodes_map[name] = node
                                        except Exception as e_node: print(f"Warning: Could not restore node '{name}' ({ntype}): {e_node}")
                                    for from_node_orig_name, from_sock_name, to_node_orig_name, to_sock_name in original_nodes_data["links"]:
                                        try:
                                            from_node = nodes_map.get(from_node_orig_name); to_node = nodes_map.get(to_node_orig_name)
                                            if from_node and to_node:
                                                from_sock = from_node.outputs.get(from_sock_name); to_sock = to_node.inputs.get(to_sock_name)
                                                if from_sock and to_sock: scene.node_tree.links.new(from_sock, to_sock)
                                                else: print(f"Warning: Could not find sockets for link {from_node_orig_name}.{from_sock_name} -> {to_node_orig_name}.{to_sock_name}")
                                            else: print(f"Warning: Could not find nodes for link {from_node_orig_name} -> {to_node_orig_name}")
                                        except Exception as e_link: print(f"Warning: Could not restore link {from_node_orig_name}.{from_sock_name} -> {to_node_orig_name}.{to_sock_name}: {e_link}")
                                elif not original_tree_ref and scene.node_tree.name == "__GReady_TempTree":
                                     if scene.node_tree.users <= 1: bpy.data.node_groups.remove(scene.node_tree); print("Removed temporary compositor node tree.")
                                     else: print("Warning: Temporary node tree has other users, not removing.")
                            scene.use_nodes = original_use_nodes; print("Compositor settings restored.")
                    else:
                        # Direct Save
                        shared_bake_image.filepath_raw = file_path
                        shared_bake_image.save()
                        self.report({'INFO'}, f"Saved baked image directly to: {file_path}")

                except Exception as e:
                    self.report({'ERROR'}, f"Failed to save image '{file_path}': {e}")
        elif baked_count > 0 and not output_dir_base: self.report({'WARNING'}, f"Output dir not set. Baked image '{shared_img_name}' not saved.")
        elif baked_count > 0 and not shared_bake_image: self.report({'ERROR'}, f"Shared bake image '{shared_img_name}' not found after baking. Cannot save.")

        # Restore selection and active object
        bpy.ops.object.select_all(action='DESELECT')
        for obj in initial_selection:
            try:
                if obj and obj.name in bpy.data.objects: obj.select_set(True)
            except ReferenceError: pass
        try:
            if initial_active and initial_active.name in bpy.data.objects: context.view_layer.objects.active = initial_active
        except ReferenceError: pass

        # Restore original margin setting after all baking is done
        bake_settings.margin = user_margin

        # Ensure progress is at 100%
        progress_data['current'] = progress_data['total']
        with open(progress_file_path, 'w') as f:
            json.dump(progress_data, f)

        self.report({'INFO'}, f"Bake process finished. Baked: {baked_count}, Skipped: {skipped_count}, Failed: {failed_count}.")
        if scene.get(PUREPLAY_INTERNAL_TAG, False):
            scene[PUREPLAY_LAST_SUCCESS_TAG] = (
                baked_count > 0
                and skipped_count == 0
                and failed_count == 0
                and saved_count == len(bake_outputs)
            )
        # Don't delete temp files - let the progress script handle it
        return {'FINISHED'}


class BAKER_OT_visualize_ray_distance(Operator):
    """Visualize Max Ray Distance by creating a visual representation"""
    bl_idname = "baker.visualize_ray_distance"
    bl_label = "Visualize Max Ray Distance"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        bake_settings = scene.render.bake
        
        # Find meshes with _cage suffix first, then _low if no cages found
        target_meshes = []
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                if obj.name.endswith("_cage"):
                    target_meshes.append(obj)
        
        if not target_meshes:
            for obj in bpy.data.objects:
                if obj.type == 'MESH' and obj.name.endswith("_low"):
                    target_meshes.append(obj)
        
        if not target_meshes:
            self.report({'WARNING'}, "No meshes with '_cage' or '_low' suffix found!")
            return {'CANCELLED'}
        
        # Create duplicates and set them up
        for obj in target_meshes:
            # Create duplicate
            new_name = obj.name.replace("_cage", "_maxraydistance").replace("_low", "_maxraydistance")
            new_obj = obj.copy()
            new_obj.data = obj.data.copy()
            new_obj.name = new_name
            bpy.context.scene.collection.objects.link(new_obj)
            
            # Enable "In front"
            new_obj.show_in_front = True
            
            # Set viewport color to orange
            new_obj.color = (1, 0.510714, 0.0257853, 1)
            
            # Apply scale
            bpy.context.view_layer.objects.active = new_obj
            new_obj.select_set(True)
            bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
            new_obj.select_set(False)
            
            # Add Solidify modifier
            solidify = new_obj.modifiers.new(name="Solidify", type='SOLIDIFY')
            solidify.use_flip_normals = True
            solidify.offset = -1
            solidify.thickness = bake_settings.max_ray_distance
            solidify.use_rim_only = True
            
            # Set up driver for thickness
            driver = solidify.driver_add("thickness").driver
            driver.type = 'SCRIPTED'
            
            # Create variable
            var = driver.variables.new()
            var.name = "max_ray_distance"
            var.type = 'SINGLE_PROP'
            
            # Set target
            target = var.targets[0]
            target.id_type = 'SCENE'
            target.id = context.scene
            target.data_path = "render.bake.max_ray_distance"
            
            # Set expression
            driver.expression = "max_ray_distance"
            
            # Ensure driver is enabled
            driver.use_self = True
        
        # Set viewport shading to Vertex
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.shading.color_type = 'VERTEX'
        
        return {'FINISHED'}

def update_max_ray_distance(self, context):
    """Create driver connection between max_ray_distance and Solidify thickness"""
    bake_settings = context.scene.render.bake
    
    # Find all _maxraydistance objects
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and obj.name.endswith("_maxraydistance"):
            solidify = obj.modifiers.get("Solidify")
            if solidify:
                # Remove any existing drivers
                if solidify.driver_remove("thickness"):
                    pass
                
                # Create new driver
                driver = solidify.driver_add("thickness").driver
                driver.type = 'SCRIPTED'
                
                # Create variable
                var = driver.variables.new()
                var.name = "max_ray_distance"
                var.type = 'SINGLE_PROP'
                
                # Set target
                target = var.targets[0]
                target.id_type = 'SCENE'
                target.id = context.scene
                target.data_path = "render.bake.max_ray_distance"
                
                # Set expression
                driver.expression = "max_ray_distance"
                
                # Ensure driver is enabled
                driver.use_self = True

class BAKER_OT_remove_ray_distance_visualization(Operator):
    """Remove Max Ray Distance visualization"""
    bl_idname = "baker.remove_ray_distance_visualization"
    bl_label = "Remove Max Ray Distance Visualization"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Find and remove all objects with _maxraydistance suffix
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and obj.name.endswith("_maxraydistance"):
                bpy.data.objects.remove(obj, do_unlink=True)
        return {'FINISHED'}

# --- Output Settings Operator (File Browser) ---

class BAKER_OT_output_settings(Operator):
    """Choose output directory and configure image format settings"""
    bl_idname = "baker.output_settings"
    bl_label = "Output Settings"
    bl_description = "Select output directory and configure bake image format"
    bl_options = {'REGISTER'}

    filepath: StringProperty(
        name="Output Directory",
        subtype='DIR_PATH',
        default="//"
    )

    image_format: EnumProperty(
        name="Image Format",
        items=[
            ('PNG', "PNG", ""),
            ('JPEG', "JPEG", ""),
            ('TIFF', "TIFF", ""),
            ('TARGA', "TARGA", ""),
            ('BMP', "BMP", ""),
            ('OPEN_EXR', "OpenEXR", ""),
        ],
        default='PNG'
    )

    # --- PNG Options ---
    png_color_mode: EnumProperty(
        name="Color Mode",
        items=[('BW', "BW", ""), ('RGB', "RGB", ""), ('RGBA', "RGBA", "")],
        default='RGBA'
    )
    png_bit_depth: EnumProperty(
        name="Bit Depth",
        items=[('8', "8", ""), ('16', "16", "")],
        default='8'
    )
    png_compression: IntProperty(
        name="Compression",
        default=15, min=0, max=100
    )

    # --- JPEG Options ---
    jpeg_color_mode: EnumProperty(
        name="Color Mode",
        items=[('BW', "BW", ""), ('RGB', "RGB", "")],
        default='RGB'
    )
    jpeg_quality: IntProperty(
        name="Quality",
        default=90, min=0, max=100
    )

    # --- TIFF Options ---
    tiff_color_mode: EnumProperty(
        name="Color Mode",
        items=[('BW', "BW", ""), ('RGB', "RGB", ""), ('RGBA', "RGBA", "")],
        default='RGBA'
    )
    tiff_bit_depth: EnumProperty(
        name="Bit Depth",
        items=[('8', "8", ""), ('16', "16", "")],
        default='8'
    )
    tiff_codec: EnumProperty(
        name="Compression",
        items=[
            ('NONE', "None", ""),
            ('LZW', "LZW", ""),
            ('PACKBITS', "Pack Bits", ""),
            ('DEFLATE', "Deflate (ZIP)", ""),
        ],
        default='DEFLATE'
    )

    # --- TARGA Options ---
    targa_color_mode: EnumProperty(
        name="Color Mode",
        items=[('BW', "BW", ""), ('RGB', "RGB", ""), ('RGBA', "RGBA", "")],
        default='RGBA'
    )
    # --- OpenEXR Options ---
    exr_color_mode: EnumProperty(
        name="Color Mode",
        items=[('RGB', "RGB", ""), ('RGBA', "RGBA", "")],
        default='RGBA'
    )
    exr_bit_depth: EnumProperty(
        name="Bit Depth",
        items=[('16', "Float (Half)", ""), ('32', "Float (Full)", "")],
        default='16'
    )
    exr_codec: EnumProperty(
        name="Codec",
        items=[
            ('NONE', "None", ""),
            ('PXR24', "PXR24", ""),
            ('ZIP', "ZIP", ""),
            ('PIZ', "PIZ", ""),
            ('RLE', "RLE", ""),
            ('ZIPS', "ZIPS", ""),
            ('B44', "B44", ""),
            ('B44A', "B44A", ""),
            ('DWAA', "DWAA", ""),
            ('DWAB', "DWAB", ""),
        ],
        default='ZIP'
    )
    exr_use_preview: BoolProperty(
        name="Save Preview",
        default=True
    )

    def invoke(self, context, event):
        scene = context.scene
        # Copy current scene settings to operator properties
        self.filepath = getattr(scene, "bake_output_dir", "//")
        self.image_format = getattr(scene, "bake_image_format", 'PNG')
        self.png_color_mode = getattr(scene, "baker_png_color_mode", 'RGBA')
        self.png_bit_depth = getattr(scene, "baker_png_bit_depth", '8')
        self.png_compression = getattr(scene, "baker_png_compression", 15)
        self.jpeg_color_mode = getattr(scene, "baker_jpeg_color_mode", 'RGB')
        self.jpeg_quality = getattr(scene, "baker_jpeg_quality", 90)
        self.tiff_color_mode = getattr(scene, "baker_tiff_color_mode", 'RGBA')
        self.tiff_bit_depth = getattr(scene, "baker_tiff_bit_depth", '8')
        self.tiff_codec = getattr(scene, "baker_tiff_codec", 'DEFLATE')
        self.targa_color_mode = getattr(scene, "baker_targa_color_mode", 'RGBA')
        self.exr_color_mode = getattr(scene, "baker_exr_color_mode", 'RGBA')
        self.exr_bit_depth = getattr(scene, "baker_exr_bit_depth", '16')
        self.exr_codec = getattr(scene, "baker_exr_codec", 'ZIP')
        self.exr_use_preview = getattr(scene, "baker_exr_use_preview", True)

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "image_format")

        fmt = self.image_format
        if fmt == 'PNG':
            box = layout.box()
            box.label(text="PNG Options")
            row = box.row(align=True)
            row.prop(self, "png_color_mode", text="Color")
            row.prop(self, "png_bit_depth", text="Depth")
            box.prop(self, "png_compression", text="Compression", slider=True)
        elif fmt == 'JPEG':
            box = layout.box()
            box.label(text="JPEG Options")
            box.prop(self, "jpeg_color_mode", text="Color Mode")
            box.prop(self, "jpeg_quality", text="Quality", slider=True)
        elif fmt == 'TIFF':
            box = layout.box()
            box.label(text="TIFF Options")
            box.prop(self, "tiff_color_mode", text="Color Mode")
            split = box.split(factor=0.5, align=True)
            split.prop(self, "tiff_bit_depth", text="Depth")
            split.prop(self, "tiff_codec", text="Compression")
        elif fmt == 'TARGA':
            box = layout.box()
            box.label(text="TARGA Options")
            box.prop(self, "targa_color_mode", text="Color Mode")
        elif fmt == 'OPEN_EXR':
            box = layout.box()
            box.label(text="OpenEXR Options")
            box.prop(self, "exr_color_mode", text="Color Mode")
            split = box.split(factor=0.5, align=True)
            split.prop(self, "exr_bit_depth", text="Depth")
            split.prop(self, "exr_codec", text="Codec")
            box.prop(self, "exr_use_preview", text="Save Preview")

    def execute(self, context):
        scene = context.scene
        scene.bake_output_dir = self.filepath
        scene.bake_image_format = self.image_format
        scene.baker_png_color_mode = self.png_color_mode
        scene.baker_png_bit_depth = self.png_bit_depth
        scene.baker_png_compression = self.png_compression
        scene.baker_jpeg_color_mode = self.jpeg_color_mode
        scene.baker_jpeg_quality = self.jpeg_quality
        scene.baker_tiff_color_mode = self.tiff_color_mode
        scene.baker_tiff_bit_depth = self.tiff_bit_depth
        scene.baker_tiff_codec = self.tiff_codec
        scene.baker_targa_color_mode = self.targa_color_mode
        scene.baker_exr_color_mode = self.exr_color_mode
        scene.baker_exr_bit_depth = self.exr_bit_depth
        scene.baker_exr_codec = self.exr_codec
        scene.baker_exr_use_preview = self.exr_use_preview
        self.report({'INFO'}, f"Output directory set to: {self.filepath}")
        return {'FINISHED'}

# --- END OF FILE operators_baking.py ---
