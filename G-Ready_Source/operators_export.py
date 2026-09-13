import bmesh
import bpy
import math
import os
import re
import subprocess
import sys
from datetime import datetime

from .symmetric_triangulate import build_substance_triangulated_mesh


# Ported from:
# C:\Users\Farid\Desktop\act_2025_2_1\import_export\operators.py
# C:\Users\Farid\Desktop\act_2025_2_1\import_export\utils.py
# C:\Users\Farid\Desktop\act_2025_2_1\origin\operators.py
# C:\Users\Farid\Desktop\act_2025_2_1\origin\utils.py
# C:\Users\USER\Desktop\export_helper_pro_v2.0\materials.py


def _settings():
    return bpy.context.scene.gready_export_settings


def show_message_box(message="", title="Message Box", icon="INFO"):
    def draw(self, _context):
        self.layout.label(text=message)

    bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)


def prefilter_export_name(name):
    return re.sub(r"[#%&{}<>\\*?/'\":`|]", "_", name)


LOD_OBJECT_NAME_RE = re.compile(r"^(.+)_LOD(\d+)$")


def parse_lod_object_name(name):
    """Match object names ending with _LOD0, _LOD1, etc. Returns (base_name, level) or (None, None)."""
    match = LOD_OBJECT_NAME_RE.match(name)
    if not match:
        return None, None
    return match.group(1), int(match.group(2))


def export_name_without_lod_suffix(name):
    """Export file base name with any trailing _LOD<number> suffix removed."""
    base_name, _level = parse_lod_object_name(name)
    if base_name is not None:
        return base_name
    return name


def collect_lod_family_meshes(base_name):
    """All mesh objects in the file named {base_name}_LOD{level}."""
    members = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        obj_base, level = parse_lod_object_name(obj.name)
        if obj_base == base_name:
            members.append((level, obj))
    members.sort(key=lambda item: item[0])
    return [obj for _level, obj in members]


def include_object_with_parent_chain(objects, obj):
    while obj is not None:
        if obj not in objects:
            objects.append(obj)
        obj = obj.parent


def has_armature_modifier(obj):
    return obj.type == "MESH" and any(
        modifier.type == "ARMATURE" for modifier in obj.modifiers
    )


def collect_animation_export_dependencies(objects, export_settings):
    if export_settings.export_format != "FBX" or not export_settings.export_animation:
        return list(objects)

    export_objects = []
    for obj in objects:
        include_object_with_parent_chain(export_objects, obj)

    for obj in objects:
        if obj.type != "MESH":
            continue

        if obj.parent and obj.parent.type == "ARMATURE":
            include_object_with_parent_chain(export_objects, obj.parent)

        for modifier in obj.modifiers:
            if modifier.type != "ARMATURE":
                continue
            armature_obj = getattr(modifier, "object", None)
            if armature_obj:
                include_object_with_parent_chain(export_objects, armature_obj)

    return export_objects


def lod_bases_in_selection(objects):
    bases = set()
    for obj in objects:
        base_name, _level = parse_lod_object_name(obj.name)
        if base_name is not None:
            bases.add(base_name)
    return bases


def print_execution_time(function_name, start_time):
    export_settings = _settings()

    if export_settings.debug:
        finish_time = datetime.now()
        execution_time = finish_time - start_time
        seconds = execution_time.total_seconds()
        milliseconds = round(seconds * 1000)
        print(function_name + " finished in " + str(seconds) + "s (" + str(milliseconds) + "ms)")


def add_configured_triangulate_modifier(obj):
    mod = obj.modifiers.new(name="Triangulate", type="TRIANGULATE")
    mod.quad_method = "FIXED"
    mod.ngon_method = "CLIP"
    mod.min_vertices = 5
    if hasattr(mod, "keep_custom_normals"):
        mod.keep_custom_normals = True
    return mod


def apply_substance_triangulation(obj, export_settings):
    bpy.context.view_layer.update()
    obj.data.update()

    old_mesh = obj.data
    source_mesh = obj.data.copy()
    source_mesh.name = obj.name + "_SOURCE_COPY"
    source_mesh.update()
    tri_mesh, _axes, _quad_count = build_substance_triangulated_mesh(
        src_mesh=source_mesh,
        triangulate_quads=export_settings.substance_triangulate_quads,
        keep_normals=True,
        symmetry_tolerance=0.001,
    )
    obj.data = tri_mesh

    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)

    if source_mesh.users == 0:
        bpy.data.meshes.remove(source_mesh)


def is_smooth_by_angle_modifier(modifier):
    if modifier.type != "NODES":
        return False

    node_group = getattr(modifier, "node_group", None)
    node_group_name = node_group.name if node_group else ""

    def normalize_name(name):
        return re.sub(r"[^a-z0-9]+", "", name.casefold())

    smooth_by_angle_key = "smoothbyangle"
    return (
        normalize_name(modifier.name).startswith(smooth_by_angle_key)
        or normalize_name(node_group_name).startswith(smooth_by_angle_key)
    )


def is_substance_normal_modifier(modifier):
    return modifier.type == "WEIGHTED_NORMAL" or is_smooth_by_angle_modifier(modifier)


def substance_normal_modifier_order(modifier):
    if is_smooth_by_angle_modifier(modifier):
        return 0

    if modifier.type == "WEIGHTED_NORMAL":
        return 1

    return None


def is_mirror_with_object_modifier(modifier):
    return modifier.type == "MIRROR" and getattr(modifier, "mirror_object", None) is not None


def is_mirror_without_object_modifier(modifier):
    return modifier.type == "MIRROR" and getattr(modifier, "mirror_object", None) is None


def is_substance_deferred_modifier(modifier):
    return modifier.type == "ARRAY" or is_mirror_with_object_modifier(modifier)


def has_flat_shaded_faces(obj):
    mesh = obj.data

    if hasattr(mesh, "attributes"):
        sharp_face_attr = mesh.attributes.get("sharp_face")

        if sharp_face_attr is not None and sharp_face_attr.domain == 'FACE':
            return any(bool(getattr(item, "value", False)) for item in sharp_face_attr.data)

    return any(not poly.use_smooth for poly in mesh.polygons)


def ensure_smooth_by_angle_for_flat_shading(obj, angle_degrees=0.1):
    if any(is_smooth_by_angle_modifier(modifier) for modifier in obj.modifiers):
        return False

    if not has_flat_shaded_faces(obj):
        return False

    try:
        view_layer = bpy.context.view_layer

        with bpy.context.temp_override(
            object=obj,
            active_object=obj,
            selected_objects=[obj],
            selected_editable_objects=[obj],
            view_layer=view_layer,
        ):
            bpy.ops.object.shade_auto_smooth(angle=math.radians(angle_degrees))

        print(f"Added Smooth by Angle {angle_degrees} deg for flat-shaded Substance export: {obj.name}")
        return True
    except Exception as err:
        print(f"Can't add Smooth by Angle for flat-shaded Substance export: {obj.name}", err)
        return False


def apply_modifier_or_remove(obj, modifier):
    try:
        view_layer = bpy.context.view_layer

        with bpy.context.temp_override(
            object=obj,
            active_object=obj,
            selected_objects=[obj],
            selected_editable_objects=[obj],
            view_layer=view_layer,
        ):
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        return True
    except Exception as err:
        print(f"Can't apply modifier: {modifier.name} on {obj.name}", err)
        bpy.ops.object.modifier_remove(modifier=modifier.name)
        return False


def move_modifier_to_top(obj, modifier):
    try:
        from_index = obj.modifiers.find(modifier.name)
        if from_index > 0:
            obj.modifiers.move(from_index, 0)
    except Exception:
        try:
            bpy.ops.object.modifier_move_to_index(modifier=modifier.name, index=0)
        except Exception as err:
            print(f"Can't move modifier: {modifier.name} on {obj.name}", err)


def apply_substance_modifiers(obj, export_settings):
    deferred_modifiers = []

    for modifier_name in [modifier.name for modifier in obj.modifiers]:
        modifier = obj.modifiers.get(modifier_name)

        if not modifier:
            continue

        if is_substance_deferred_modifier(modifier):
            deferred_modifiers.append(modifier_name)
            continue

        if is_substance_normal_modifier(modifier):
            continue

        apply_modifier_or_remove(obj, modifier)

    ensure_smooth_by_angle_for_flat_shading(obj, angle_degrees=0.1)

    normal_modifiers = [
        {
            "name": modifier.name,
            "normal_order": substance_normal_modifier_order(modifier),
        }
        for modifier in obj.modifiers
        if is_substance_normal_modifier(modifier)
    ]

    normal_modifiers.sort(key=lambda item: item["normal_order"])

    for modifier in normal_modifiers:
        modifier_name = modifier["name"]

        if obj.modifiers.get(modifier_name):
            move_modifier_to_top(obj, obj.modifiers[modifier_name])
            if apply_modifier_or_remove(obj, obj.modifiers[modifier_name]):
                print(f"Applied normal modifier before Substance triangulation: {modifier_name} on {obj.name}")

    apply_substance_triangulation(obj, export_settings)

    for modifier_name in deferred_modifiers:
        if obj.modifiers.get(modifier_name):
            apply_modifier_or_remove(obj, obj.modifiers[modifier_name])


def material_has_principled_bsdf(material):
    """Check if material has a Principled BSDF node."""
    if not material or not material.use_nodes:
        return False
    for node in material.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            return True
    return False


def prepare_materials_for_export(objects, convert_viewport=True):
    """Convert Viewport Display colours to Principled BSDF materials.

    Returns a list of material info dicts for restoration after export.
    """
    if not convert_viewport:
        return []

    modified_materials = []

    for obj in objects:
        if obj.type != 'MESH':
            continue

        for slot in obj.material_slots:
            mat = slot.material
            if not mat:
                continue

            if material_has_principled_bsdf(mat):
                continue

            original_use_nodes = mat.use_nodes
            viewport_color = mat.diffuse_color[:3]

            if not mat.use_nodes:
                mat.use_nodes = True
                nodes = mat.node_tree.nodes
                links = mat.node_tree.links
                nodes.clear()

                bsdf = nodes.new('ShaderNodeBsdfPrincipled')
                bsdf.location = (0, 0)
                bsdf.inputs['Base Color'].default_value = (*viewport_color, 1.0)

                output = nodes.new('ShaderNodeOutputMaterial')
                output.location = (300, 0)

                links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])

                modified_materials.append({
                    'material': mat,
                    'original_use_nodes': original_use_nodes,
                    'was_converted': True,
                })
            else:
                nodes = mat.node_tree.nodes
                links = mat.node_tree.links

                output_node = None
                for node in nodes:
                    if node.type == 'OUTPUT_MATERIAL':
                        output_node = node
                        break

                if output_node:
                    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
                    bsdf.name = "_TempPrincipledBSDF"
                    bsdf.location = (output_node.location.x - 300, output_node.location.y)
                    bsdf.inputs['Base Color'].default_value = (*viewport_color, 1.0)

                    old_link = None
                    if output_node.inputs['Surface'].is_linked:
                        old_link = output_node.inputs['Surface'].links[0].from_socket

                    links.new(bsdf.outputs['BSDF'], output_node.inputs['Surface'])

                    modified_materials.append({
                        'material': mat,
                        'original_use_nodes': original_use_nodes,
                        'was_converted': False,
                        'temp_node_name': "_TempPrincipledBSDF",
                        'old_link_socket': old_link,
                    })

    return modified_materials


def cleanup_materials_after_export(modified_materials):
    """Restore materials to their original state after export."""
    for info in modified_materials:
        mat = info['material']

        if info.get('was_converted'):
            mat.use_nodes = info['original_use_nodes']
            if not info['original_use_nodes']:
                mat.node_tree.nodes.clear()
        else:
            temp_node_name = info.get('temp_node_name')
            if temp_node_name and temp_node_name in mat.node_tree.nodes:
                temp_node = mat.node_tree.nodes[temp_node_name]

                old_socket = info.get('old_link_socket')
                if old_socket:
                    output_node = None
                    for node in mat.node_tree.nodes:
                        if node.type == 'OUTPUT_MATERIAL':
                            output_node = node
                            break
                    if output_node:
                        mat.node_tree.links.new(old_socket, output_node.inputs['Surface'])

                mat.node_tree.nodes.remove(temp_node)


def prepare_selected_materials_for_export(export_settings):
    return prepare_materials_for_export(
        list(bpy.context.selected_objects),
        export_settings.convert_viewport_colors,
    )


def export_model(path, name):
    export_settings = _settings()

    if export_settings.export_format == "FBX":
        export_fbx(path, name)

    if export_settings.export_format == "OBJ":
        export_obj(path, name)

    if export_settings.export_format == "GLTF":
        export_gltf(path, name)


def _snapshot_nla(armature_obj):
    snapshot = []
    if armature_obj.animation_data is None:
        return snapshot
    for track in armature_obj.animation_data.nla_tracks:
        strips = []
        for strip in track.strips:
            strips.append({
                'name': strip.name,
                'action': strip.action,
                'frame_start': strip.frame_start,
                'frame_end': strip.frame_end,
                'mute': strip.mute,
                'select': strip.select,
            })
        snapshot.append({
            'name': track.name,
            'mute': track.mute,
            'strips': strips,
        })
    return snapshot

def _clear_nla(armature_obj):
    if armature_obj.animation_data is None:
        return
    while armature_obj.animation_data.nla_tracks:
        armature_obj.animation_data.nla_tracks.remove(armature_obj.animation_data.nla_tracks[0])

def _push_actions_to_nla(armature_obj, actions):
    _clear_nla(armature_obj)
    for action in actions:
        if action is None:
            continue
        track = armature_obj.animation_data.nla_tracks.new()
        track.name = action.name
        strip = track.strips.new(action.name, int(action.frame_range[0]), action)
        strip.frame_end = int(action.frame_range[1])

def _restore_nla(armature_obj, snapshot):
    _clear_nla(armature_obj)
    if not snapshot:
        return
    for track_data in snapshot:
        track = armature_obj.animation_data.nla_tracks.new()
        track.name = track_data['name']
        track.mute = track_data['mute']
        for strip_data in track_data['strips']:
            if strip_data['action'] is None:
                continue
            strip = track.strips.new(strip_data['name'], strip_data['frame_start'], strip_data['action'])
            strip.frame_end = strip_data['frame_end']
            strip.mute = strip_data['mute']
            strip.select = strip_data['select']
        track.mute = track_data['mute']


def export_fbx(path, name, target_engine_override=None, substance_painter_mode=False):
    export_settings = _settings()
    target_engine = target_engine_override or export_settings.export_target_engine

    # --- Selective Actions (NLA push) ---
    selective_actions = []
    armatures = []
    nla_snapshots = {}
    created_anim_data = []  # track armatures where we created animation_data
    use_selective = False
    if (export_settings.export_animation
            and export_settings.export_format == "FBX"
            and export_settings.use_selective_actions):
        selective_actions = [act for act in export_settings.filtered_actions if act.use_export]
        if selective_actions:
            use_selective = True
            for obj in bpy.context.selected_objects:
                if obj.type == 'ARMATURE':
                    armatures.append(obj)
                elif obj.type == 'MESH':
                    for mod in obj.modifiers:
                        if mod.type == 'ARMATURE' and mod.object:
                            if mod.object not in armatures:
                                armatures.append(mod.object)
            for arm in armatures:
                if arm.animation_data is not None:
                    nla_snapshots[arm] = _snapshot_nla(arm)
                else:
                    created_anim_data.append(arm)
            action_objs = []
            for a in selective_actions:
                action = bpy.data.actions.get(a.name)
                if action is not None:
                    action_objs.append(action)
            if armatures and action_objs:
                for arm in armatures:
                    if arm.animation_data is None:
                        arm.animation_data_create()
                    _push_actions_to_nla(arm, action_objs)
    # --- End Selective Actions ---

    export_fbx_params = dict(
        filepath=str(path + name + ".fbx"),
        use_selection=True,
        apply_scale_options="FBX_SCALE_ALL",
        add_leaf_bones=False,
        colors_type="LINEAR",
        use_custom_props=True,
        use_mesh_modifiers=True,
        mesh_smooth_type="OFF",
        use_mesh_edges=False,
        use_tspace=False,
        use_armature_deform_only=True,
        global_scale=1.0,
        path_mode='COPY' if export_settings.embed_textures else 'AUTO',
        embed_textures=export_settings.embed_textures,
        axis_forward="-Z",
        axis_up="Y",
        use_space_transform=True,
        bake_anim=export_settings.export_animation,
        bake_anim_use_all_bones=export_settings.export_anim_key_all_bones,
        bake_anim_use_nla_strips=True,
        bake_anim_use_all_actions=False if use_selective else export_settings.export_anim_all_actions,
        bake_anim_force_startend_keying=export_settings.export_anim_force_start_end_keying,
        bake_anim_step=export_settings.export_anim_sampling_rate,
        bake_anim_simplify_factor=export_settings.export_anim_simplify,
    )

    if target_engine == "UNITY":
        export_fbx_params["apply_scale_options"] = "FBX_SCALE_UNITS"
        export_fbx_params["axis_forward"] = "-Y"
        export_fbx_params["axis_up"] = "Z"
        export_fbx_params["use_space_transform"] = False
    elif target_engine == "UNREAL":
        export_fbx_params["apply_scale_options"] = "FBX_SCALE_NONE"

    if export_settings.export_custom_options:
        if export_settings.use_custom_export_scale:
            export_fbx_params["apply_scale_options"] = "FBX_SCALE_CUSTOM"
            export_fbx_params["global_scale"] = export_settings.custom_export_scale_value

        if export_settings.use_custom_export_axes:
            export_fbx_params["axis_forward"] = export_settings.custom_export_forward_axis
            export_fbx_params["axis_up"] = export_settings.custom_export_up_axis
            export_fbx_params["use_space_transform"] = False

        export_fbx_params["use_mesh_edges"] = export_settings.export_loose_edges
        export_fbx_params["use_tspace"] = export_settings.export_tangent_space
        export_fbx_params["mesh_smooth_type"] = export_settings.export_smoothing
        export_fbx_params["use_custom_props"] = export_settings.export_custom_props
        export_fbx_params["add_leaf_bones"] = export_settings.export_add_leaf_bones
        export_fbx_params["use_armature_deform_only"] = export_settings.export_only_deform_bones
        export_fbx_params["colors_type"] = export_settings.export_vc_color_space

    if substance_painter_mode:
        export_fbx_params["axis_forward"] = "-Z"
        export_fbx_params["axis_up"] = "Y"
        export_fbx_params["use_space_transform"] = True

    allowed_args = bpy.ops.export_scene.fbx.get_rna_type().properties.keys()
    export_params = {k: v for k, v in export_fbx_params.items() if k in allowed_args}
    modified_materials = prepare_selected_materials_for_export(export_settings)
    try:
        bpy.ops.export_scene.fbx(**export_params)
    finally:
        cleanup_materials_after_export(modified_materials)
        if use_selective:
            for arm, snap in nla_snapshots.items():
                _restore_nla(arm, snap)
            for arm in created_anim_data:
                _clear_nla(arm)
                if arm.animation_data is not None:
                    arm.animation_data_clear()


def export_obj(path, name):
    version = bpy.app.version
    export_settings = _settings()

    if version >= (4, 0, 0):
        export_obj_params = dict(
            filepath=str(path + name + ".obj"),
            export_selected_objects=True,
            apply_modifiers=True,
            export_smooth_groups=True,
            export_normals=True,
            export_uv=True,
            export_materials=True,
            export_triangulated_mesh=False,
            export_object_groups=True,
            export_material_groups=True,
            global_scale=1.0,
            path_mode="AUTO",
            forward_axis="NEGATIVE_Z",
            up_axis="Y",
        )
    else:
        export_obj_params = dict(
            filepath=str(path + name + ".obj"),
            use_selection=True,
            use_mesh_modifiers=True,
            use_edges=True,
            use_smooth_groups=True,
            use_normals=True,
            use_uvs=True,
            use_materials=True,
            use_triangles=False,
            group_by_object=True,
            group_by_material=True,
            keep_vertex_order=True,
            global_scale=1,
            path_mode="AUTO",
            axis_forward="-Z",
            axis_up="Y",
        )

    if export_settings.export_custom_options:
        if export_settings.use_custom_export_scale:
            export_obj_params["global_scale"] = export_settings.custom_export_scale_value

        if export_settings.use_custom_export_axes:
            forward_axis = export_settings.custom_export_forward_axis
            up_axis = export_settings.custom_export_up_axis

            if version >= (4, 0, 0):
                export_obj_params["forward_axis"] = forward_axis.replace("-", "NEGATIVE_")
                export_obj_params["up_axis"] = up_axis.replace("-", "NEGATIVE_")
            else:
                export_obj_params["axis_forward"] = forward_axis
                export_obj_params["axis_up"] = up_axis

        if version >= (4, 0, 0):
            export_obj_params["export_smooth_groups"] = export_settings.obj_export_smooth_groups
            export_obj_params["export_material_groups"] = export_settings.obj_separate_by_materials
        else:
            export_obj_params["use_smooth_groups"] = export_settings.obj_export_smooth_groups
            export_obj_params["group_by_material"] = export_settings.obj_separate_by_materials

    if version >= (4, 0, 0):
        allowed_args = bpy.ops.wm.obj_export.get_rna_type().properties.keys()
        export_params = {k: v for k, v in export_obj_params.items() if k in allowed_args}
        modified_materials = prepare_selected_materials_for_export(export_settings)
        try:
            bpy.ops.wm.obj_export(**export_params)
        finally:
            cleanup_materials_after_export(modified_materials)
    else:
        allowed_args = bpy.ops.export_scene.obj.get_rna_type().properties.keys()
        export_params = {k: v for k, v in export_obj_params.items() if k in allowed_args}
        modified_materials = prepare_selected_materials_for_export(export_settings)
        try:
            bpy.ops.export_scene.obj(**export_params)
        finally:
            cleanup_materials_after_export(modified_materials)


def export_gltf(path, name):
    export_settings = _settings()

    export_gltf_params = dict(
        filepath=str(path + name + ".glb"),
        check_existing=False,
        export_image_format="AUTO",
        export_tangents=False,
        export_attributes=False,
        use_selection=True,
        export_extras=True,
        export_def_bones=False,
    )

    if export_settings.export_custom_options:
        export_gltf_params["export_image_format"] = export_settings.gltf_export_image_format
        export_gltf_params["export_tangents"] = export_settings.export_tangent_space
        export_gltf_params["export_attributes"] = export_settings.gltf_export_attributes
        export_gltf_params["export_extras"] = export_settings.export_custom_props
        export_gltf_params["export_def_bones"] = export_settings.export_only_deform_bones

    allowed_args = bpy.ops.export_scene.gltf.get_rna_type().properties.keys()
    export_params = {k: v for k, v in export_gltf_params.items() if k in allowed_args}
    modified_materials = prepare_selected_materials_for_export(export_settings)
    try:
        bpy.ops.export_scene.gltf(**export_params)
    finally:
        cleanup_materials_after_export(modified_materials)


class GREADY_OT_export_model(bpy.types.Operator):
    """Export FBXs/OBJs/GLTFs to Unity/UE/Godot"""
    bl_idname = "gready.export_model"
    bl_label = "Export FBXs/OBJs/GLTFs"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.object is not None
            and context.active_object is not None
            and context.mode == "OBJECT"
            and hasattr(context.scene, "gready_export_settings")
        )

    def execute(self, context):
        start_time = datetime.now()
        export_settings = context.scene.gready_export_settings
        export_settings.export_dir = ""
        incorrect_names = []

        start_active_obj = context.active_object
        current_selected_obj = context.selected_objects
        name = context.active_object.name
        effective_export_format = "FBX" if export_settings.export_mode == "SUBSTANCE_PAINTER" else export_settings.export_format

        if export_settings.export_mode == "ALL" and export_settings.set_custom_fbx_name and not export_settings.custom_fbx_name:
            show_message_box("Custom Name can't be empty", "Saving Error", "ERROR")
            return {"CANCELLED"}

        if not bpy.data.filepath and not export_settings.custom_export_path:
            show_message_box(
                "Blend file is not saved. Try use Custom Export Path",
                "Saving Error",
                "ERROR",
            )
            return {"CANCELLED"}

        if export_settings.custom_export_path:
            if not export_settings.export_path:
                show_message_box("Export Path can't be empty", "Saving Error", "ERROR")
                return {"CANCELLED"}

            export_path = os.path.realpath(bpy.path.abspath(export_settings.export_path))

            if not os.path.exists(export_path):
                show_message_box("Directory for export not exist", "Saving Error", "ERROR")
                return {"CANCELLED"}

            path = export_path + os.sep
        else:
            path = bpy.path.abspath(f"//{effective_export_format}s/")

        os.makedirs(path, exist_ok=True)

        if export_settings.export_mode == "SUBSTANCE_PAINTER":
            return self.execute_substance_painter_export(context, path, start_time)

        for x in current_selected_obj:
            if x.type not in {"MESH", "EMPTY", "ARMATURE", "CURVE", "FONT"}:
                x.select_set(False)
        current_selected_obj = collect_animation_export_dependencies(
            list(context.selected_objects),
            export_settings,
        )
        bpy.ops.object.select_all(action="DESELECT")
        for obj in current_selected_obj:
            obj.select_set(True)

        bpy.ops.ed.undo_push(message="FBX Bridge Export Prepare")

        allow_multi_users = (
            export_settings.export_format == "FBX"
            and export_settings.export_target_engine == "UNITY"
            and not export_settings.export_combine_meshes
            and not export_settings.export_mode == "INDIVIDUAL"
        )

        if not allow_multi_users:
            bpy.ops.object.make_single_user(type="SELECTED_OBJECTS", object=True, obdata=True)

        bpy.ops.object.select_all(action="DESELECT")

        for obj in current_selected_obj:
            obj.select_set(True)
            context.view_layer.objects.active = obj

            if obj.type == "EMPTY":
                continue

            if obj.type == "ARMATURE":
                obj.select_set(False)
                continue

            if export_settings.delete_mats_before_export:
                if obj.type == "MESH" and obj.data.materials:
                    obj.data.materials.clear()

            if export_settings.triangulate_before_export and obj.type in {"MESH", "CURVE", "FONT"}:
                add_configured_triangulate_modifier(obj)

            for modifier in reversed(obj.modifiers):
                if not (modifier.show_viewport and modifier.show_render):
                    obj.modifiers.remove(modifier)

            if obj.type == "MESH":
                if obj.data.users < 2 and not obj.data.shape_keys:
                    for modifier in obj.modifiers:
                        if modifier.type == "ARMATURE":
                            continue
                        try:
                            bpy.ops.object.modifier_apply(modifier=modifier.name)
                        except Exception as err:
                            print(f"Can't apply modifier: {modifier.name} on {obj.name}", err)
                            bpy.ops.object.modifier_remove(modifier=modifier.name)
                elif not has_armature_modifier(obj):
                    bpy.ops.object.convert(target="MESH")
            else:
                bpy.ops.object.convert(target="MESH")

            if (
                export_settings.export_format == "FBX"
                and export_settings.export_target_engine == "UNITY"
            ) or export_settings.export_format == "GLTF":
                if not (obj.type == "MESH" and obj.data.users > 1):
                    bpy.ops.object.transform_apply(
                        location=False,
                        rotation=export_settings.apply_rot,
                        scale=export_settings.apply_scale,
                    )
            else:
                bpy.ops.object.transform_apply(location=False, rotation=False, scale=export_settings.apply_scale)

                if export_settings.apply_rot:
                    context.scene.tool_settings.transform_pivot_point = "MEDIAN_POINT"

                    if obj.parent is None:
                        child_rotated = False
                        bpy.ops.object.select_grouped(extend=True, type="CHILDREN_RECURSIVE")
                        for x in context.selected_objects:
                            if abs(x.rotation_euler.x) + abs(x.rotation_euler.y) + abs(x.rotation_euler.z) > 0.017:
                                child_rotated = True

                        bpy.ops.object.select_all(action="DESELECT")
                        obj.select_set(True)

                        rotate_params = dict(
                            value=(math.pi * -90 / 180),
                            orient_axis="X",
                            orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                            orient_type="GLOBAL",
                            constraint_axis=(True, False, False),
                            orient_matrix_type="GLOBAL",
                            mirror=False,
                            use_proportional_edit=False,
                        )

                        if (
                            export_settings.export_format == "FBX"
                            and (
                                export_settings.apply_rot_rotated
                                or (not export_settings.apply_rot_rotated and not child_rotated)
                                or not export_settings.export_mode == "PARENT"
                            )
                        ):
                            bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
                            bpy.ops.transform.rotate(**rotate_params)
                            bpy.ops.object.select_grouped(extend=True, type="CHILDREN_RECURSIVE")
                            bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
                            bpy.ops.object.select_all(action="DESELECT")
                            obj.select_set(True)
                            rotate_params["value"] = (math.pi * 90 / 180)
                            bpy.ops.transform.rotate(**rotate_params)

            obj.select_set(False)

        for obj in current_selected_obj:
            obj.select_set(True)

        if export_settings.export_mode == "ALL":
            if export_settings.export_combine_meshes:
                if start_active_obj.type == "MESH":
                    context.view_layer.objects.active = start_active_obj
                    bpy.ops.object.join()
                else:
                    current_active = context.view_layer.objects.active
                    for obj in current_selected_obj:
                        if obj.type == "MESH":
                            context.view_layer.objects.active = obj
                    bpy.ops.object.join()
                    context.view_layer.objects.active = current_active

                current_selected_obj = context.selected_objects

            prefilter_name = export_settings.custom_fbx_name if export_settings.set_custom_fbx_name else name
            name = prefilter_export_name(prefilter_name)

            if name != prefilter_name:
                incorrect_names.append(prefilter_name)

            export_model(path, name)

        if export_settings.export_mode in {"PARENT", "INDIVIDUAL"}:
            if export_settings.export_mode == "INDIVIDUAL":
                bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")

            selection_for_lod = list(current_selected_obj)
            lod_bases_to_export = (
                lod_bases_in_selection(selection_for_lod)
                if export_settings.export_mode == "PARENT"
                else set()
            )
            lod_family_exported = set()

            bpy.ops.object.select_all(action="DESELECT")

            for obj in current_selected_obj:
                if not obj.parent:
                    obj.select_set(True)

            current_selected_obj = context.selected_objects

            def export_parent_selection(work_obj, export_base_name, extra_members=None):
                bpy.ops.object.select_all(action="DESELECT")
                context.view_layer.objects.active = work_obj
                work_obj.select_set(True)

                if export_settings.export_combine_meshes:
                    if work_obj.type == "MESH":
                        bpy.ops.object.select_grouped(extend=True, type="CHILDREN_RECURSIVE")
                        bpy.ops.object.join()

                        selected_objects_for_cleanup = context.selected_objects
                        for x in selected_objects_for_cleanup:
                            if x.type == "EMPTY" and len(x.children) == 0:
                                bpy.data.objects.remove(x, do_unlink=True)

                    else:
                        current_active = context.view_layer.objects.active
                        parent_loc = current_active.location.copy()
                        parent_name = current_active.name

                        bpy.ops.object.select_grouped(extend=False, type="CHILDREN_RECURSIVE")
                        group_selected_objects = context.selected_objects

                        for x in group_selected_objects:
                            if x.type == "MESH":
                                context.view_layer.objects.active = x
                        bpy.ops.object.join()

                        context.view_layer.objects.active.name = parent_name + "_Mesh"

                        current_active.select_set(True)
                        context.view_layer.objects.active = current_active
                        bpy.ops.object.parent_set(type="OBJECT", keep_transform=True)

                        selected_objects_for_cleanup = context.selected_objects

                        context.scene.tool_settings.transform_pivot_point = "MEDIAN_POINT"
                        context.scene.cursor.location = parent_loc
                        bpy.ops.object.origin_set(type="ORIGIN_CURSOR", center="MEDIAN")

                        for x in selected_objects_for_cleanup:
                            if x.type == "EMPTY" and len(x.children) == 0:
                                bpy.data.objects.remove(x, do_unlink=True)

                        context.view_layer.objects.active = current_active

                current_parent = context.view_layer.objects.active

                bpy.ops.object.select_all(action="DESELECT")

                current_parent.select_set(True)
                context.view_layer.objects.active = current_parent

                object_loc = work_obj.location.copy()

                if export_settings.apply_loc:
                    work_obj.location = (0, 0, 0)

                if extra_members:
                    for member in extra_members:
                        member_obj = bpy.data.objects.get(member.name)
                        if member_obj and member_obj != current_parent:
                            member_obj.select_set(True)

                prefilter_name = export_base_name
                name = prefilter_export_name(prefilter_name)

                bpy.ops.object.select_grouped(extend=True, type="CHILDREN_RECURSIVE")

                if name != prefilter_name:
                    incorrect_names.append(prefilter_name)

                export_model(path, name)

                if export_settings.apply_loc:
                    work_obj.location = object_loc

            if export_settings.export_mode == "PARENT":
                for lod_base in sorted(lod_bases_to_export):
                    if lod_base in lod_family_exported:
                        continue
                    lod_family = collect_lod_family_meshes(lod_base)
                    if not lod_family:
                        continue
                    if not any(
                        member in selection_for_lod
                        for member in lod_family
                    ):
                        continue

                    lod_family_exported.add(lod_base)
                    export_parent_selection(
                        lod_family[0],
                        export_name_without_lod_suffix(lod_base),
                        extra_members=lod_family,
                    )

            for obj in current_selected_obj:
                obj_base, _lod_level = parse_lod_object_name(obj.name)
                if (
                    export_settings.export_mode == "PARENT"
                    and obj_base is not None
                    and obj_base in lod_family_exported
                ):
                    continue

                export_base_name = (
                    export_name_without_lod_suffix(obj.name)
                    if export_settings.export_mode == "PARENT"
                    else obj.name
                )
                export_parent_selection(obj, export_base_name)

        if export_settings.export_mode == "COLLECTION":
            used_collections = []
            origin_loc = (0.0, 0.0, 0.0)
            context.scene.tool_settings.transform_pivot_point = "MEDIAN_POINT"
            obj_col_dict = {}

            for obj in current_selected_obj:
                collection_in_list = False

                for c in used_collections:
                    if obj.users_collection[0].name == c:
                        collection_in_list = True

                if not collection_in_list:
                    used_collections.append(obj.users_collection[0].name)

                obj_col_dict[obj] = obj.users_collection[0].name

            for c in used_collections:
                bpy.ops.object.select_all(action="DESELECT")

                set_active_mesh = False
                for obj, col_name in obj_col_dict.items():
                    if col_name == c:
                        obj.select_set(True)
                        if obj.type == "MESH" and not set_active_mesh:
                            context.view_layer.objects.active = obj
                            if export_settings.export_combine_meshes:
                                obj.name = c
                            set_active_mesh = True

                if export_settings.export_combine_meshes and set_active_mesh:
                    bpy.ops.object.join()

                    context.scene.cursor.location = origin_loc
                    bpy.ops.object.origin_set(type="ORIGIN_CURSOR", center="MEDIAN")

                    selected_objects_for_cleanup = context.selected_objects
                    for obj in selected_objects_for_cleanup:
                        if obj.type == "EMPTY" and len(obj.children) == 0:
                            bpy.data.objects.remove(obj, do_unlink=True)

                name = prefilter_export_name(c)

                if name != c:
                    incorrect_names.append(c)

                export_model(path, name)

            bpy.ops.object.select_all(action="DESELECT")

        if len(incorrect_names) > 0:
            show_message_box(
                "Object(s) has invalid characters in name. Some chars in export name have been replaced",
                "Incorrect Export Names",
            )

        bpy.ops.ed.undo_push(message="")
        bpy.ops.ed.undo()
        bpy.ops.ed.undo_push(message="ACT Export")

        context.scene.gready_export_settings.export_dir = path

        print_execution_time("FBX/OBJ/GLTF Export", start_time)
        return {"FINISHED"}

    def execute_substance_painter_export(self, context, path, start_time):
        export_settings = context.scene.gready_export_settings
        original_active = context.view_layer.objects.active
        original_selection = list(context.selected_objects)
        export_targets = [obj for obj in original_selection if obj and obj.type == "MESH"]

        if not export_targets:
            show_message_box("Select at least one mesh object", "Export Error", "ERROR")
            return {"CANCELLED"}

        base_obj = original_active if original_active in export_targets else export_targets[0]
        base_name = prefilter_export_name(base_obj.name)
        export_name = base_name if base_name.lower().endswith("_texturing") else f"{base_name}_texturing"
        temp_collection = None
        temp_objects = []

        try:
            temp_collection = bpy.data.collections.new("_GReady_Substance_Export_Temp")
            context.scene.collection.children.link(temp_collection)

            for obj in export_targets:
                matrix_world = obj.matrix_world.copy()
                temp_obj = obj.copy()
                temp_obj.data = obj.data.copy()
                temp_obj.parent = None
                temp_collection.objects.link(temp_obj)
                temp_obj.matrix_world = matrix_world
                temp_objects.append(temp_obj)

            bpy.ops.object.select_all(action="DESELECT")
            for temp_obj in temp_objects:
                temp_obj.select_set(True)
            context.view_layer.objects.active = temp_objects[0]

            for obj in temp_objects:
                bpy.ops.object.select_all(action="DESELECT")
                obj.select_set(True)
                context.view_layer.objects.active = obj

                if export_settings.delete_mats_before_export and obj.data.materials:
                    obj.data.materials.clear()

                for modifier in reversed(obj.modifiers):
                    if not (modifier.show_viewport and modifier.show_render):
                        obj.modifiers.remove(modifier)

                apply_substance_modifiers(obj, export_settings)

                bpy.ops.object.transform_apply(
                    location=False,
                    rotation=export_settings.apply_rot,
                    scale=export_settings.apply_scale,
                )

            bpy.ops.object.select_all(action="DESELECT")
            for temp_obj in temp_objects:
                temp_obj.select_set(True)
            context.view_layer.objects.active = temp_objects[0]

            bpy.ops.object.make_single_user(
                object=True,
                obdata=True,
                material=False,
                animation=False,
                obdata_animation=False,
            )

            bpy.ops.object.select_all(action="DESELECT")
            for temp_obj in temp_objects:
                if temp_obj.name in bpy.data.objects:
                    temp_obj.select_set(True)

            context.view_layer.objects.active = temp_objects[0]
            if len(temp_objects) > 1:
                bpy.ops.object.join()

            export_obj = context.view_layer.objects.active
            export_obj.name = export_name
            export_obj.data.name = export_name

            bpy.ops.object.select_all(action="DESELECT")
            export_obj.select_set(True)
            context.view_layer.objects.active = export_obj
            export_fbx(path, export_name, target_engine_override="UNITY", substance_painter_mode=True)

            context.scene.gready_export_settings.export_dir = path
            print_execution_time("Substance Painter Export", start_time)
            return {"FINISHED"}
        finally:
            bpy.ops.object.select_all(action="DESELECT")
            if temp_collection:
                for obj in list(temp_collection.objects):
                    mesh = obj.data if obj.type == "MESH" else None
                    bpy.data.objects.remove(obj, do_unlink=True)
                    if mesh and mesh.users == 0:
                        bpy.data.meshes.remove(mesh)
                try:
                    bpy.data.collections.remove(temp_collection)
                except RuntimeError:
                    pass

            for obj in original_selection:
                if obj and obj.name in bpy.data.objects:
                    obj.select_set(True)
            if original_active and original_active.name in bpy.data.objects:
                context.view_layer.objects.active = original_active


class GREADY_OT_open_export_dir(bpy.types.Operator):
    """Open Export Directory in OS"""
    bl_idname = "gready.open_export_dir"
    bl_label = "Open Export Directory"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        start_time = datetime.now()
        export_settings = context.scene.gready_export_settings

        if not os.path.exists(os.path.realpath(bpy.path.abspath(export_settings.export_dir))):
            export_settings.export_dir = ""
            show_message_box("Directory not exist", "Wrong Path", "ERROR")
            return {"CANCELLED"}

        if len(export_settings.export_dir) > 0:
            try:
                if sys.platform.startswith("win"):
                    os.startfile(export_settings.export_dir)
                elif sys.platform.startswith("linux"):
                    subprocess.Popen(["xdg-open", export_settings.export_dir])
                elif sys.platform.startswith("darwin"):
                    subprocess.Popen(["open", export_settings.export_dir])
            except Exception as e:
                print(f"Can't open folder: {e}")
        else:
            show_message_box("Export FBX's before", "Info")
            return {"FINISHED"}

        print_execution_time("Open Export Directory", start_time)
        return {"FINISHED"}


def find_min_max_verts(obj, coord_index):
    bpy.ops.mesh.reveal()

    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()

    if len(bm.verts) == 0:
        return None

    min_co = (obj.matrix_world @ bm.verts[0].co)[coord_index]
    max_co = (obj.matrix_world @ bm.verts[0].co)[coord_index]

    for v in bm.verts:
        world_value = (obj.matrix_world @ v.co)[coord_index]
        if world_value < min_co:
            min_co = world_value
        if world_value > max_co:
            max_co = world_value

    return (min_co, max_co)


class GREADY_OT_origin_z_minimum(bpy.types.Operator):
    """Set selected mesh origins to their minimum Z"""
    bl_idname = "gready.origin_z_minimum"
    bl_label = "Origin - Z Minimum"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.active_object is not None and context.mode == "OBJECT"

    def execute(self, context):
        start_time = datetime.now()

        current_selected_obj = context.selected_objects
        current_active_obj = context.active_object
        saved_cursor_loc = context.scene.cursor.location.copy()

        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")

        axis_index = 2

        for obj in current_selected_obj:
            if obj.type != "MESH":
                continue

            obj.select_set(True)
            context.view_layer.objects.active = obj
            saved_origin_loc = obj.location.copy()

            bpy.ops.object.mode_set(mode="EDIT")

            min_max = find_min_max_verts(obj, axis_index)
            target_co = min_max[0] if min_max else saved_origin_loc[axis_index]

            bpy.ops.object.mode_set(mode="OBJECT")

            new_cursor = list(saved_origin_loc)
            new_cursor[axis_index] = target_co
            context.scene.cursor.location = new_cursor

            bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
            context.scene.cursor.location = saved_cursor_loc

            obj.select_set(False)

        for obj in current_selected_obj:
            obj.select_set(True)

        context.view_layer.objects.active = current_active_obj

        print_execution_time("Align To ...", start_time)
        return {"FINISHED"}
