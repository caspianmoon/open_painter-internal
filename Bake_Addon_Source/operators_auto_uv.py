# Blender MOF Wrapper for UV Unwrapper
#
# This file is part of Blender MOF Wrapper for UV Unwrapper.
#
# Blender MOF Wrapper for UV Unwrapper is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3
#
# Blender MOF Wrapper for UV Unwrapper is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Blender MOF Wrapper for UV Unwrapper.

import bpy, os, subprocess, zipfile, tempfile, shutil, re, uuid
import mathutils
import bmesh
from bpy.props import (
    StringProperty, IntProperty, FloatProperty, BoolProperty, PointerProperty, EnumProperty
)
from bpy.types import Operator, Panel, AddonPreferences, PropertyGroup


# Global variable to store the last target UV‐map name
last_target_uv_map = ""

bl_info = {
    "name": "Blender Wrapper for MOF UV Unwrapper",
    "blender": (4, 4, 0),
    "category": "UV",
    "version": (1, 0, 3),
    "author": "",
    "description": "Wrapper that makes the MOF Unwrapper work in Blender",
}


# -------------------------------------------------------------------
# EnumProperty for UV map selection
# -------------------------------------------------------------------

def uv_map_items(self, context):
    # 1) a "none" entry with a valid ID
    items = [
        ('NONE', 'Select UV Map…', 'No UV map selected yet'),
    ]
    obj = context.selected_objects[0] if context.selected_objects else None

    # 2) gather any real UV names
    uv_names = []
    if obj and obj.type == 'MESH':
        uv_names = [uv.name for uv in obj.data.uv_layers]

    # 3) if the stored value is something other than "NONE" but it no longer exists,
    #    show it as "Missing" — but never for "NONE" itself.
    current = getattr(self, 'target_uv_map', 'NONE')
    if current != 'NONE' and current not in uv_names:
        items.append(
            (current,
             f"{current} (Missing)",
             "Previously selected UV map is no longer on this mesh")
        )

    # 4) append all the real ones
    items.extend((name, name, "") for name in uv_names)
    return items



# -------------------------------------------------------------------
# Property Group for operator parameters
# -------------------------------------------------------------------
class MOFProperties(PropertyGroup):
    """
    PropertyGroup that holds all the parameters for the MOF UV Unwrapper.

    Each property corresponds to a parameter used by the external UV unwrapping tool.
    All integer and float properties are clamped with a minimum value of 0.
    """
    force_hard_surface: BoolProperty(
        name="Hard Surface",
        default=False,
        description="Favor rigid, mechanical surfaces when preparing the unwrap"
    )
    separate_by_material: BoolProperty(
        name="By Material",
        default=False,
        description="Unwrap connected material areas as separate working groups"
    )
    separate_sharp_edges: BoolProperty(
        name="Sharp Cuts",
        default=True,
        description="Treat non-smooth edges as temporary cut lines for the unwrap"
    )
    triangulate_mesh: BoolProperty(
        name="Triangulate",
        default=False,
        description="Send a triangulated temporary copy to MOF while keeping the source mesh unchanged"
    )
    keep_existing_seams: BoolProperty(
        name="Keep Seams",
        default=False,
        description="Use your current seam marks as unwrap cuts and restore them after Auto UV"
    )
    mark_seam_edges: BoolProperty(
        name="Mark Seams",
        default=True,
        description="Mark mesh seams where the generated UV islands have borders"
    )
    correct_self_intersecting: BoolProperty(
        name="Fix Crossed UVs",
        default=True,
        description="Fix faces whose generated UV edges fold across themselves"
    )
    use_texel_density: BoolProperty(
        name="Use Density",
        default=False,
        description="Scale the generated UVs toward the pixel density set below"
    )
    resolution: IntProperty(
        name="Map Size",
        default=1024,
        min=1,
        description="Texture resolution used when density matching is enabled"
    )
    texture_size: EnumProperty(
        name="Map Size",
        items=(
            ('128', "128", ""),
            ('256', "256", ""),
            ('512', "512", ""),
            ('1024', "1024", ""),
            ('2048', "2048", ""),
            ('4096', "4096", ""),
            ('8192', "8192", ""),
        ),
        default='1024',
        description="Texture resolution used when density matching is enabled"
    )
    texture_density: FloatProperty(
        name="Px / Meter",
        default=1024.0,
        min=0.001,
        precision=2,
        description="Target pixel density for one meter of mesh surface"
    )
    auto_detect_hard_edges: BoolProperty(
        name="Find Hard Edges",
        default=True,
        description="Let MOF look for hard-surface edge breaks during unwrap"
    )
    use_normal: BoolProperty(
        name="Use Normals",
        default=False,
        description="Use mesh normals as an extra clue when MOF classifies surfaces"
    )
    overlap_identical_parts: BoolProperty(
        name="Stack Same",
        default=False,
        description="Allow repeated matching shapes to share the same UV space"
    )
    overlap_mirrored_parts: BoolProperty(
        name="Stack Mirrored",
        default=False,
        description="Allow mirrored shapes to share texture space when possible"
    )
    world_scale: BoolProperty(
        name="World Scale",
        default=True,
        description="Keep UV scale related to the object's real scene size"
    )
    extra_cmd: StringProperty(
        name="Extra Flags",
        default="",
        description="Optional command line flags sent directly to MinistryOfFlat"
    )
    weld: BoolProperty(
        name="Weld Verts",
        default=False,
        description="Let MOF merge duplicate vertices internally without changing the source mesh"
    )

    # Legacy options kept for blend-file compatibility. New Auto UV uses the current option set above.
    separate_hard_edges: BoolProperty(name="Separate Hard Edges", default=False)
    separate_marked_edges: BoolProperty(name="Separate Marked Edges", default=True)
    aspect: FloatProperty(name="Aspect", default=1.0, min=0.0)
    use_normals: BoolProperty(name="Use Normals", default=False)
    udims: IntProperty(name="UDIMs", default=1, min=0)
    overlap_identical: BoolProperty(name="Overlap Identical Parts", default=False)
    overlap_mirrored: BoolProperty(name="Overlap Mirrored Parts", default=False)
    seam_x: FloatProperty(
        name="Seam X", 
        default=0.0, 
        min=0.0,
        description="Offset the seam in the X direction"
    )
    seam_y: FloatProperty(
        name="Seam Y", 
        default=0.0, 
        min=0.0,
        description="Offset the seam in the Y direction"
    )
    seam_z: FloatProperty(
        name="Seam Z", 
        default=0.0, 
        min=0.0,
        description="Offset the seam in the Z direction"
    )
    suppress_validation: BoolProperty(
        name="Suppress Validation", 
        default=False,
        description="Disable validation checks in the external tool"
    )
    quads: BoolProperty(
        name="Quads", 
        default=True,
        description="Ask MOF to search for triangle pairs that behave like clean quad patches"
    )
    flat_soft_surface: BoolProperty(
        name="Flat Soft Surface", 
        default=True,
        description="Flatten soft surfaces for improved UV layout"
    )
    cones: BoolProperty(
        name="Cones", 
        default=True,
        description="Enable cone calculations for shading correction"
    )
    cone_ratio: FloatProperty(
        name="Cone Ratio", 
        default=0.5, 
        min=0.0,
        description="Set the ratio used for cone calculations"
    )
    grids: BoolProperty(
        name="Grids", 
        default=True,
        description="Enable grid processing for the UV layout"
    )
    strips: BoolProperty(
        name="Strips", 
        default=True,
        description="Enable strip processing for more efficient UV packing"
    )
    patches: BoolProperty(
        name="Patches", 
        default=True,
        description="Use patch-based processing in the UV layout"
    )
    planes: BoolProperty(
        name="Planes", 
        default=True,
        description="Enable detection of planar regions for the UV unwrap"
    )
    flatness: FloatProperty(
        name="Flatness", 
        default=0.9, 
        min=0.0,
        description="Threshold for determining flatness during UV flattening"
    )
    merge: BoolProperty(
        name="Merge", 
        default=True,
        description="Merge similar UV islands after unwrapping"
    )
    merge_limit: FloatProperty(
        name="Merge Limit", 
        default=0.0, 
        min=0.0,
        description="Set the distance threshold for merging UV islands"
    )
    pre_smooth: BoolProperty(
        name="Pre-Smooth", 
        default=True,
        description="Smooth the mesh before unwrapping to improve UV results"
    )
    soft_unfold: BoolProperty(
        name="Soft Unfold", 
        default=True,
        description="Apply a soft unfolding algorithm to reduce distortions"
    )
    tubes: BoolProperty(
        name="Tubes", 
        default=True,
        description="Enable tube-like processing for cylindrical mesh parts"
    )
    junctions: BoolProperty(
        name="Junctions", 
        default=True,
        description="Detect and process junctions in the mesh during unwrapping"
    )
    extra_debug: BoolProperty(
        name="Extra Debug Point", 
        default=False,
        description="Enable extra debugging information for troubleshooting"
    )
    angle_based_flattening: BoolProperty(
        name="Angle-based Flattening", 
        default=True,
        description="Use angle-based flattening for a more natural UV layout"
    )
    smooth: BoolProperty(
        name="Smooth",
        default=False,
        description="Disable for Hard Surface models if you see stretchings"
    )
    repair_smooth: BoolProperty(
        name="Repair Smooth",
        default=True,
        description="Smooth out repair areas in the UV layout for better transitions"
    )
    repair: BoolProperty(
        name="Repair", 
        default=True,
        description="Perform repair operations on the UV layout to fix errors"
    )
    squares: BoolProperty(
        name="Squares", 
        default=False,
        description="Help MOF recognize small right-angled shapes that should stay square"
    )
    relax: BoolProperty(
        name="Relax", 
        default=True,
        description="Relax the UV islands to reduce texture stretching"
    )
    relax_iterations: IntProperty(
        name="Relax Iterations", 
        default=50, 
        min=0,
        description="Number of iterations to run during the UV relaxation process"
    )
    expand: FloatProperty(
        name="Expand", 
        default=0.25, 
        min=0.0,
        description="Factor by which to expand the UV islands before packing"
    )
    cut: BoolProperty(
        name="Extra Cuts", 
        default=False,
        description="Let MOF add more cuts to improve awkward island shapes"
    )
    stretch: BoolProperty(
        name="Stretch", 
        default=False,
        description="Permit wide islands to stretch when MOF needs them to fit"
    )
    match: BoolProperty(
        name="Match", 
        default=True,
        description="Match adjacent UV islands to minimize seams"
    )
    packing: BoolProperty(
        name="Pack", 
        default=True,
        description="Let MOF arrange generated islands into the UV space"
    )
    rasterization: IntProperty(
        name="Rasterization Resolution", 
        default=64, 
        min=0,
        description="Resolution for rasterizing UV islands during packing"
    )
    packing_iterations: IntProperty(
        name="Packing Iterations", 
        default=4, 
        min=0,
        description="Number of iterations to optimize the packing of UV islands"
    )
    scale_to_fit: FloatProperty(
        name="Scale To Fit", 
        default=0.5, 
        min=0.0,
        description="Scale factor to fit the UV islands within the texture space"
    )
    validate: BoolProperty(
        name="Validate", 
        default=False,
        description="Run validation on the final UV layout for errors"
    )
    uv_margin: FloatProperty(
        name="UV Margin", 
        default=0.1, 
        min=0.0,
        description="Margin between UV islands in the texture space"
    )
    pixel_padding: IntProperty(
        name="Pixel Padding", 
        default=2, 
        min=0,
        description="Padding in pixels applied during UV tile scaling"
    )
    keep_original_mesh: BoolProperty(
        name="Keep Original Mesh",
        default=False,
        description="If enabled, duplicate the original mesh before processing and add _original suffix"
    )
    triangulate: BoolProperty(
        name="Triangulate",
        default=False,
        description="If enabled, triangulate the mesh before export (min vertices 5, keep normals)"
    )

    target_uv_map: StringProperty(
        name="Target UV Map",
        description="Name of the UV map to write the unwrapped UVs into",
        default=""
    )

    pack_margin: FloatProperty(
        name="Margin",
        default=0.03,
        min=0.0,
        max=1.0,
        description="Margin between UV islands when packing"
    )



# -------------------------------------------------------------------
# Addon Preferences with version check operator
# -------------------------------------------------------------------

class MOFAddonPreferences(AddonPreferences):
    """
    Addon preferences for the Blender Wrapper for MOF UV Unwrapper.

    This class allows the user to set the executable path for the MinistryOfFlat zip file
    and displays the current version extracted from the zip.
    """
    bl_idname = __package__

    def update_executable_path(self, context):
        import bpy
        try:
            bpy.ops.wm.save_userpref()
        except Exception:
            pass  # In case called from a context where this operator is not available

    executable_path: StringProperty(
        name="Executable Zip Path",
        subtype='FILE_PATH',
        default="",
        description="Path to the MinistryOfFlat zip file",
        update=update_executable_path
    )

    version: StringProperty(
        name="Version",
        default="unknown",
        description="Version of the MinistryOfFlat zip file"
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="No preferences available. Please use the main panel for MOF zip selection.")

    def save_preferences(self):
        # Save the preferences to Blender's configuration
        prefs = bpy.context.preferences.addons[__package__].preferences
        prefs.executable_path = self.executable_path
        prefs.version = self.version

# -------------------------------------------------------------------
# Operator to clear zip path
# -------------------------------------------------------------------
class ClearMOFZipPathOperator(Operator):
    """
    Operator that clears the MinistryOfFlat zip file path.
    """
    bl_idname = "wm.clearmofzippath"
    bl_label = "Clear MOF Zip Path"

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        prefs.executable_path = ""
        prefs.version = "unknown"
        # Save the preferences
        prefs.save_preferences()
        return {'FINISHED'}

# -------------------------------------------------------------------
# Operator to check/extract version from the zip file
# -------------------------------------------------------------------
class CheckMOFZipVersionOperator(Operator):
    """
    Operator that checks the version of the MinistryOfFlat zip file.
    """
    bl_idname = "wm.checkmofzipversion"
    bl_label = "Check MinistryOfFlat Zip Version"

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        zip_path = bpy.path.abspath(prefs.executable_path)
        if not prefs.executable_path or not os.path.exists(zip_path):
            self.report({'ERROR'}, "Zip file not set or not found")
            prefs.version = "unknown"
            prefs.save_preferences()
            return {'CANCELLED'}
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                doc_filename = None
                for name in zf.namelist():
                    if os.path.basename(name).lower() == "documentation.txt":
                        doc_filename = name
                        break
                if doc_filename:
                    with zf.open(doc_filename) as doc_file:
                        content = doc_file.read().decode("utf-8")
                        match = re.search(r"[Vv]ersion[:\s]+([\d]+\.[\d]+(?:\.[\d]+)?)", content)
                        if match:
                            prefs.version = match.group(1)
                        else:
                            prefs.version = "unknown"
                            self.report({'WARNING'}, "Version string not found in Documentation.txt")
                else:
                    prefs.version = "unknown"
                    self.report({'WARNING'}, "Documentation.txt not found in zip")
        except Exception as e:
            prefs.version = "unknown"
            self.report({'ERROR'}, f"Error checking zip version: {e}")
            prefs.save_preferences()
            return {'CANCELLED'}
        self.report({'INFO'}, f"Zip Version: {prefs.version}")
        prefs.save_preferences()
        return {'FINISHED'}

# -------------------------------------------------------------------
# Operator to download MinistryOfFlat zip file
# -------------------------------------------------------------------
class DownloadMOFZipOperator(Operator):
    """
    Operator that downloads the MinistryOfFlat zip file.
    """
    bl_idname = "wm.downloadmofzip"
    bl_label = "Download MinistryOfFlat Zip"

    def execute(self, context):
        import urllib.request
        import os

        # Get the addon directory
        addon_dir = os.path.dirname(os.path.realpath(__file__))
        zip_path = os.path.join(addon_dir, "MinistryOfFlat_Release.zip")
        
        try:
            # Download the zip file
            url = "https://www.quelsolaar.com/MinistryOfFlat_Release.zip"
            urllib.request.urlretrieve(url, zip_path)
            
            # Set the path in preferences
            prefs = context.preferences.addons[__package__].preferences
            prefs.executable_path = zip_path
            prefs.save_preferences()
            
            # Check the version
            bpy.ops.wm.checkmofzipversion()
            
            self.report({'INFO'}, "MinistryOfFlat zip file downloaded successfully")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to download zip file: {e}")
            return {'CANCELLED'}


class BAKEADDON_OT_missing_mof_popup(Operator):
    bl_idname = "wm.bakeaddon_missing_mof_popup"
    bl_label = "MinistryOfFlat Required"

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        addon = context.preferences.addons.get(__package__)
        prefs = addon.preferences if addon else None

        col = layout.column(align=True)
        col.label(text="Auto Unwrap needs MinistryOfFlat before it can run.", icon='INFO')
        col.label(text="Download it now, then run Auto Unwrap again.")
        col.separator()

        if prefs:
            row = col.row(align=True)
            row.prop(prefs, "executable_path", text="MOF Zip Path")
            row.operator("wm.clearmofzippath", text="", icon='X')

        col.operator(DownloadMOFZipOperator.bl_idname, text="Download MinistryOfFlat", icon='IMPORT')

    def execute(self, _context):
        return {'FINISHED'}


def _uv_layer_data(uv_layer):
    return getattr(uv_layer, "uv", None) or uv_layer.data


def _read_uv(uv_data, loop_index):
    item = uv_data[loop_index]
    return item.vector.copy() if hasattr(item, "vector") else item.uv.copy()


def _write_uv(uv_data, loop_index, value):
    item = uv_data[loop_index]
    if hasattr(item, "vector"):
        item.vector = value
    else:
        item.uv = value


def _safe_object_mode():
    try:
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass


def _restore_selection_and_mode(context, selected_names, active_name, restore_mode):
    _safe_object_mode()
    try:
        bpy.ops.object.select_all(action='DESELECT')
    except Exception:
        pass

    for name in selected_names:
        obj = bpy.data.objects.get(name)
        if obj:
            obj.select_set(True)

    active_obj = bpy.data.objects.get(active_name) if active_name else None
    if active_obj:
        context.view_layer.objects.active = active_obj

    if restore_mode == 'EDIT' and active_obj:
        try:
            bpy.ops.object.mode_set(mode='EDIT')
        except Exception:
            pass


def _remove_object_and_mesh(obj):
    if not obj:
        return

    mesh = obj.data if getattr(obj, "type", None) == 'MESH' else None
    if obj.name in bpy.data.objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    if mesh and mesh.users == 0 and mesh.name in bpy.data.meshes:
        bpy.data.meshes.remove(mesh)


def _find_mof_executable(root_dir):
    fallback = None
    for root, _dirs, files in os.walk(root_dir):
        for filename in files:
            path = os.path.join(root, filename)
            lower = filename.lower()
            if os.name == "nt":
                if lower == "unwrapconsole3.exe":
                    return path
                if lower.endswith(".exe") and fallback is None:
                    fallback = path
            elif os.access(path, os.X_OK) and not os.path.isdir(path):
                return path
    return fallback


def _get_mof_executable_from_cache(zip_path):
    addon_dir = os.path.dirname(os.path.realpath(__file__))
    cache_dir = os.path.join(addon_dir, "_mof_cache")
    marker_path = os.path.join(cache_dir, "source.txt")
    stat = os.stat(zip_path)
    signature = f"{os.path.abspath(zip_path)}|{stat.st_mtime_ns}|{stat.st_size}"

    if os.path.exists(marker_path):
        try:
            with open(marker_path, "r", encoding="utf-8") as marker:
                if marker.read() == signature:
                    exe = _find_mof_executable(cache_dir)
                    if exe and os.path.exists(exe):
                        return exe
        except Exception:
            pass

    if os.path.exists(cache_dir):
        cache_abs = os.path.abspath(cache_dir)
        addon_abs = os.path.abspath(addon_dir)
        if os.path.commonpath([cache_abs, addon_abs]) == addon_abs:
            shutil.rmtree(cache_dir)

    os.makedirs(cache_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(cache_dir)

    exe = _find_mof_executable(cache_dir)
    if not exe:
        raise RuntimeError("No executable found in the MinistryOfFlat zip file.")

    with open(marker_path, "w", encoding="utf-8") as marker:
        marker.write(signature)

    return exe


def _ensure_target_uv_layer(obj, props):
    mesh = obj.data
    target_name = getattr(props, "target_uv_map", "")

    if target_name and target_name in mesh.uv_layers:
        mesh.uv_layers.active = mesh.uv_layers[target_name]
        return target_name

    if mesh.uv_layers.active:
        props.target_uv_map = mesh.uv_layers.active.name
        return mesh.uv_layers.active.name

    if mesh.uv_layers:
        mesh.uv_layers.active = mesh.uv_layers[0]
        props.target_uv_map = mesh.uv_layers[0].name
        return mesh.uv_layers[0].name

    new_uv = mesh.uv_layers.new(name="UVMap")
    mesh.uv_layers.active = new_uv
    props.target_uv_map = new_uv.name
    return new_uv.name


def _collect_auto_uv_targets(context, original_mode):
    targets = []
    if original_mode == 'EDIT':
        objects = [obj for obj in getattr(context, "objects_in_mode", []) if obj.type == 'MESH']
        if not objects:
            objects = [obj for obj in context.selected_objects if obj.type == 'MESH']

        for obj in objects:
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            face_indices = [face.index for face in bm.faces if face.select and not face.hide]
            if face_indices:
                targets.append((obj.name, face_indices))
    else:
        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            face_indices = [poly.index for poly in obj.data.polygons]
            if face_indices:
                targets.append((obj.name, face_indices))

    return targets


def _split_face_indices_by_material_connectivity(obj, face_indices):
    selected = set(face_indices)
    if not selected:
        return []

    mesh = obj.data
    edge_to_faces = {}
    for face_index in selected:
        poly = mesh.polygons[face_index]
        for edge_key in poly.edge_keys:
            edge_to_faces.setdefault(tuple(sorted(edge_key)), []).append(face_index)

    neighbors = {face_index: set() for face_index in selected}
    for connected_faces in edge_to_faces.values():
        if len(connected_faces) < 2:
            continue
        for face_index in connected_faces:
            face_material = mesh.polygons[face_index].material_index
            for other_index in connected_faces:
                if other_index != face_index and mesh.polygons[other_index].material_index == face_material:
                    neighbors[face_index].add(other_index)

    groups = []
    remaining = set(selected)
    while remaining:
        seed = min(remaining)
        material_index = mesh.polygons[seed].material_index
        stack = [seed]
        group = []
        remaining.remove(seed)

        while stack:
            face_index = stack.pop()
            group.append(face_index)
            for other_index in neighbors[face_index]:
                if other_index in remaining and mesh.polygons[other_index].material_index == material_index:
                    remaining.remove(other_index)
                    stack.append(other_index)

        groups.append(sorted(group))

    return sorted(groups, key=lambda group: group[0])


def _auto_uv_face_groups(obj, face_indices, props):
    if props.separate_by_material:
        return _split_face_indices_by_material_connectivity(obj, face_indices)
    return [sorted(face_indices)]


def _capture_face_edge_seams(mesh, face_indices):
    seam_states = {}
    edge_lookup = {tuple(sorted(edge.vertices)): edge.index for edge in mesh.edges}
    for face_index in face_indices:
        if face_index >= len(mesh.polygons):
            continue
        for edge_key in mesh.polygons[face_index].edge_keys:
            edge_index = edge_lookup.get(tuple(sorted(edge_key)))
            if edge_index is None:
                continue
            edge = mesh.edges[edge_index]
            seam_states[edge_index] = edge.use_seam
    return seam_states


def _restore_edge_seams(mesh, seam_states):
    for edge_index, use_seam in seam_states.items():
        if edge_index < len(mesh.edges):
            mesh.edges[edge_index].use_seam = use_seam


def _duplicate_original_if_requested(context, obj):
    copy_obj = obj.copy()
    copy_obj.data = obj.data.copy()
    copy_obj.name = f"{obj.name}_original"
    context.collection.objects.link(copy_obj)
    copy_obj.select_set(False)
    return copy_obj


def _create_mof_temp_object(context, source_obj, face_indices, props):
    source_loop_by_face = {
        poly.index: list(poly.loop_indices)
        for poly in source_obj.data.polygons
    }
    bm_src = bmesh.new()
    bm_src.from_mesh(source_obj.data)
    bm_src.faces.ensure_lookup_table()

    temp_name = f"BAKEADDON_MOF_{uuid.uuid4().hex}"
    temp_mesh = bpy.data.meshes.new(temp_name)
    temp_obj = bpy.data.objects.new(temp_name, temp_mesh)
    context.collection.objects.link(temp_obj)

    bm_tmp = bmesh.new()
    source_loop_layer = bm_tmp.loops.layers.int.new("BAKEADDON_SOURCE_LOOP")
    vert_map = {}
    source_face_indices = []
    sharp_edge_indices = set()

    try:
        for face_index in face_indices:
            if face_index >= len(bm_src.faces):
                continue

            src_face = bm_src.faces[face_index]
            if len(src_face.verts) < 3:
                continue

            new_verts = []
            for src_vert in src_face.verts:
                new_vert = vert_map.get(src_vert)
                if new_vert is None:
                    new_vert = bm_tmp.verts.new(src_vert.co)
                    new_vert.normal = src_vert.normal[:]
                    vert_map[src_vert] = new_vert
                new_verts.append(new_vert)

            try:
                new_face = bm_tmp.faces.new(new_verts)
            except ValueError:
                continue

            new_face.smooth = src_face.smooth
            new_face.material_index = src_face.material_index
            source_face_indices.append(face_index)

            source_loop_indices = source_loop_by_face.get(face_index, [])
            if len(source_loop_indices) != len(new_face.loops):
                raise RuntimeError(f"Loop count mismatch while preparing {source_obj.name} face {face_index}.")
            for tmp_loop, source_loop_index in zip(new_face.loops, source_loop_indices):
                tmp_loop[source_loop_layer] = source_loop_index + 1

            src_edges = list(src_face.edges)
            tmp_edges = list(new_face.edges)
            if len(src_edges) == len(tmp_edges):
                for idx, src_edge in enumerate(src_edges):
                    tmp_edges[idx].seam = props.keep_existing_seams and src_edge.seam
                    tmp_edges[idx].smooth = src_edge.smooth
                    if not src_edge.smooth:
                        sharp_edge_indices.add(src_edge.index)

        bm_tmp.verts.ensure_lookup_table()
        bm_tmp.edges.ensure_lookup_table()
        bm_tmp.faces.ensure_lookup_table()

        split_edges = [
            edge for edge in bm_tmp.edges
            if edge.seam or (props.separate_sharp_edges and not edge.smooth)
        ]

        if split_edges:
            bmesh.ops.split_edges(bm_tmp, edges=split_edges)

        if props.force_hard_surface:
            bm_tmp.verts.ensure_lookup_table()
            if bm_tmp.verts:
                min_corner = mathutils.Vector((
                    min(vert.co.x for vert in bm_tmp.verts),
                    min(vert.co.y for vert in bm_tmp.verts),
                    min(vert.co.z for vert in bm_tmp.verts),
                ))
                max_corner = mathutils.Vector((
                    max(vert.co.x for vert in bm_tmp.verts),
                    max(vert.co.y for vert in bm_tmp.verts),
                    max(vert.co.z for vert in bm_tmp.verts),
                ))
                average_span = sum(max_corner - min_corner) / 3.0
            else:
                average_span = 1.0

            cube_side = max(average_span * 0.001, 0.001)
            helper_verts = [
                bm_tmp.verts.new((cube_side, cube_side, cube_side)),
                bm_tmp.verts.new((cube_side, cube_side, -cube_side)),
                bm_tmp.verts.new((cube_side, -cube_side, cube_side)),
                bm_tmp.verts.new((cube_side, -cube_side, -cube_side)),
                bm_tmp.verts.new((-cube_side, cube_side, cube_side)),
                bm_tmp.verts.new((-cube_side, cube_side, -cube_side)),
                bm_tmp.verts.new((-cube_side, -cube_side, cube_side)),
                bm_tmp.verts.new((-cube_side, -cube_side, -cube_side)),
            ]
            bm_tmp.faces.new((helper_verts[0], helper_verts[1], helper_verts[3], helper_verts[2]))
            bm_tmp.faces.new((helper_verts[4], helper_verts[5], helper_verts[7], helper_verts[6]))
            bm_tmp.faces.new((helper_verts[0], helper_verts[1], helper_verts[5], helper_verts[4]))
            bm_tmp.faces.new((helper_verts[2], helper_verts[3], helper_verts[7], helper_verts[6]))
            bm_tmp.faces.new((helper_verts[0], helper_verts[2], helper_verts[6], helper_verts[4]))
            bm_tmp.faces.new((helper_verts[1], helper_verts[3], helper_verts[7], helper_verts[5]))

        if props.triangulate_mesh:
            bm_tmp.faces.ensure_lookup_table()
            bmesh.ops.triangulate(bm_tmp, faces=list(bm_tmp.faces))

        bm_tmp.faces.ensure_lookup_table()
        loop_maps = []
        for face in bm_tmp.faces:
            raw_loop_map = [loop[source_loop_layer] for loop in face.loops]
            if all(loop_index <= 0 for loop_index in raw_loop_map):
                loop_maps.append(None)
                continue

            if any(loop_index <= 0 for loop_index in raw_loop_map):
                raise RuntimeError(f"Could not preserve loop mapping while preparing {source_obj.name}.")

            loop_maps.append([loop_index - 1 for loop_index in raw_loop_map])

        bm_tmp.to_mesh(temp_mesh)
        temp_mesh.update()
    finally:
        bm_src.free()
        bm_tmp.free()

    if not source_face_indices:
        _remove_object_and_mesh(temp_obj)
        raise RuntimeError(f"No valid faces found on {source_obj.name}.")

    return temp_obj, loop_maps, sorted(set(source_face_indices)), sharp_edge_indices


def _mesh_edge_face_map(mesh, face_indices):
    edge_map = {}
    for face_index in face_indices:
        poly = mesh.polygons[face_index]
        vertices = list(poly.vertices)
        loop_indices = list(poly.loop_indices)
        for idx, loop_index in enumerate(loop_indices):
            vertex_a = vertices[idx]
            vertex_b = vertices[(idx + 1) % len(vertices)]
            next_loop_index = loop_indices[(idx + 1) % len(loop_indices)]
            key = tuple(sorted((vertex_a, vertex_b)))
            edge_map.setdefault(key, []).append((face_index, vertex_a, vertex_b, loop_index, next_loop_index))
    return edge_map


def _mark_uv_border_seams(mesh, face_indices, uv_layer_name):
    if uv_layer_name not in mesh.uv_layers:
        return

    uv_data = _uv_layer_data(mesh.uv_layers[uv_layer_name])
    selected_faces = set(face_indices)
    edge_map = _mesh_edge_face_map(mesh, face_indices)

    edge_lookup = {tuple(sorted(edge.vertices)): edge for edge in mesh.edges}
    for entries in edge_map.values():
        edge = edge_lookup.get(tuple(sorted((entries[0][1], entries[0][2]))))
        if edge:
            edge.use_seam = False

    for key, entries in edge_map.items():
        edge = edge_lookup.get(key)
        if not edge:
            continue

        if len(entries) != 2:
            edge.use_seam = True
            continue

        first, second = entries
        if first[0] not in selected_faces or second[0] not in selected_faces:
            edge.use_seam = True
            continue

        first_uvs = {
            first[1]: _read_uv(uv_data, first[3]),
            first[2]: _read_uv(uv_data, first[4]),
        }
        second_uvs = {
            second[1]: _read_uv(uv_data, second[3]),
            second[2]: _read_uv(uv_data, second[4]),
        }

        if any((first_uvs[vertex] - second_uvs[vertex]).length > 1e-5 for vertex in key):
            edge.use_seam = True


def _fit_processed_uvs_to_padding(mesh, face_indices, uv_layer_name, props):
    if not props.resolution or props.pixel_padding <= 0 or uv_layer_name not in mesh.uv_layers:
        return

    uv_data = _uv_layer_data(mesh.uv_layers[uv_layer_name])
    loop_indices = [loop_index for face_index in face_indices for loop_index in mesh.polygons[face_index].loop_indices]
    if not loop_indices:
        return

    coords = [_read_uv(uv_data, loop_index) for loop_index in loop_indices]
    min_u = min(uv.x for uv in coords)
    min_v = min(uv.y for uv in coords)
    max_u = max(uv.x for uv in coords)
    max_v = max(uv.y for uv in coords)
    range_u = max_u - min_u
    range_v = max_v - min_v
    if range_u <= 0.0 or range_v <= 0.0:
        return

    padding = props.pixel_padding / props.resolution
    for loop_index in loop_indices:
        uv = _read_uv(uv_data, loop_index)
        uv.x = padding + ((uv.x - min_u) / range_u) * (1 - 2 * padding)
        uv.y = padding + ((uv.y - min_v) / range_v) * (1 - 2 * padding)
        _write_uv(uv_data, loop_index, uv)


def _orientation_2d(a, b, c):
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _segments_cross_2d(a, b, c, d, epsilon=1e-8):
    ab_c = _orientation_2d(a, b, c)
    ab_d = _orientation_2d(a, b, d)
    cd_a = _orientation_2d(c, d, a)
    cd_b = _orientation_2d(c, d, b)
    return (
        ab_c * ab_d < -epsilon and
        cd_a * cd_b < -epsilon
    )


def _uv_face_self_intersects(uvs):
    if len(uvs) < 4:
        return False

    count = len(uvs)
    for first in range(count):
        first_next = (first + 1) % count
        for second in range(first + 2, count):
            second_next = (second + 1) % count
            if first == 0 and second_next == 0:
                continue
            if _segments_cross_2d(uvs[first], uvs[first_next], uvs[second], uvs[second_next]):
                return True
    return False


def _correct_self_intersecting_uv_faces(mesh, face_indices, uv_layer_name):
    if uv_layer_name not in mesh.uv_layers:
        return 0

    uv_data = _uv_layer_data(mesh.uv_layers[uv_layer_name])
    corrected = 0
    for face_index in face_indices:
        poly = mesh.polygons[face_index]
        loop_indices = list(poly.loop_indices)
        current_uvs = [_read_uv(uv_data, loop_index) for loop_index in loop_indices]
        if not _uv_face_self_intersects(current_uvs):
            continue

        normal = poly.normal
        abs_normal = (abs(normal.x), abs(normal.y), abs(normal.z))
        if abs_normal[2] >= abs_normal[0] and abs_normal[2] >= abs_normal[1]:
            projected = [mathutils.Vector((mesh.vertices[v].co.x, mesh.vertices[v].co.y)) for v in poly.vertices]
        elif abs_normal[1] >= abs_normal[0]:
            projected = [mathutils.Vector((mesh.vertices[v].co.x, mesh.vertices[v].co.z)) for v in poly.vertices]
        else:
            projected = [mathutils.Vector((mesh.vertices[v].co.y, mesh.vertices[v].co.z)) for v in poly.vertices]

        min_proj = mathutils.Vector((min(co.x for co in projected), min(co.y for co in projected)))
        max_proj = mathutils.Vector((max(co.x for co in projected), max(co.y for co in projected)))
        proj_size = max_proj - min_proj
        if proj_size.x <= 1e-8 or proj_size.y <= 1e-8:
            continue

        min_uv = mathutils.Vector((min(uv.x for uv in current_uvs), min(uv.y for uv in current_uvs)))
        max_uv = mathutils.Vector((max(uv.x for uv in current_uvs), max(uv.y for uv in current_uvs)))
        uv_size = max_uv - min_uv
        if uv_size.x <= 1e-8 or uv_size.y <= 1e-8:
            uv_size = mathutils.Vector((0.01, 0.01))

        for loop_index, co in zip(loop_indices, projected):
            normalized = mathutils.Vector(((co.x - min_proj.x) / proj_size.x, (co.y - min_proj.y) / proj_size.y))
            _write_uv(uv_data, loop_index, min_uv + mathutils.Vector((normalized.x * uv_size.x, normalized.y * uv_size.y)))
        corrected += 1

    return corrected


def _copy_imported_uvs_to_source(source_obj, imported_obj, loop_maps, source_face_indices, uv_layer_name, props, uv_scale=0.0, original_seams=None):
    source_mesh = source_obj.data
    imported_mesh = imported_obj.data

    if not imported_mesh.uv_layers.active:
        raise RuntimeError(f"No UV map found on MOF result for {source_obj.name}.")
    if uv_layer_name not in source_mesh.uv_layers:
        raise RuntimeError(f"UV map '{uv_layer_name}' not found on {source_obj.name}.")
    if len(imported_mesh.polygons) < len(loop_maps):
        raise RuntimeError(
            f"MOF result face count mismatch on {source_obj.name}: "
            f"{len(imported_mesh.polygons)} result faces for {len(loop_maps)} exported faces."
        )

    src_uv_data = _uv_layer_data(source_mesh.uv_layers[uv_layer_name])
    imported_uv_data = _uv_layer_data(imported_mesh.uv_layers.active)
    copied_source_loops = set()

    for imported_index, source_loop_indices in enumerate(loop_maps):
        if source_loop_indices is None:
            continue

        imported_poly = imported_mesh.polygons[imported_index]
        imported_loops = list(imported_poly.loop_indices)

        if len(source_loop_indices) != len(imported_loops):
            raise RuntimeError(
                f"Loop count mismatch on {source_obj.name} exported face {imported_index}: "
                f"{len(imported_loops)} result loops for {len(source_loop_indices)} source loops."
            )

        for src_loop, imported_loop in zip(source_loop_indices, imported_loops):
            if src_loop in copied_source_loops:
                continue
            uv = _read_uv(imported_uv_data, imported_loop)
            if uv_scale:
                uv *= uv_scale
            _write_uv(src_uv_data, src_loop, uv)
            copied_source_loops.add(src_loop)

    source_mesh.uv_layers.active = source_mesh.uv_layers[uv_layer_name]
    if props.mark_seam_edges and not props.keep_existing_seams:
        _mark_uv_border_seams(source_mesh, source_face_indices, uv_layer_name)
    if props.keep_existing_seams and original_seams:
        _restore_edge_seams(source_mesh, original_seams)
    if props.correct_self_intersecting:
        _correct_self_intersecting_uv_faces(source_mesh, source_face_indices, uv_layer_name)
    source_mesh.update()


def _build_mof_command(exe, in_path, out_path, props):
    cmd = [exe, in_path, out_path]
    if props.auto_detect_hard_edges:
        cmd.extend(("-SEPARATE", "TRUE"))
    if props.use_normal:
        cmd.extend(("-NORMALS", "TRUE"))
    if props.overlap_identical_parts:
        cmd.extend(("-OVERLAP", "TRUE"))
    if props.overlap_mirrored_parts:
        cmd.extend(("-MIRROR", "TRUE"))
    if props.world_scale:
        cmd.extend(("-WORLDSCALE", "TRUE"))
    if props.use_texel_density:
        cmd.extend(("-DENSITY", f"{round(props.texture_density)}"))
        cmd.extend(("-RESOLUTION", str(props.texture_size)))

    cmd.extend(("-STRETCH", str(props.stretch).upper()))
    cmd.extend(("-PACKING", str(props.packing).upper()))
    cmd.extend(("-CUTDEBUG", str(props.cut).upper()))
    cmd.extend(("-SQUARE", str(props.squares).upper()))
    cmd.extend(("-WELD", str(props.weld).upper()))
    cmd.extend(("-QUAD", str(props.quads).upper()))
    cmd.extend(("-SILENT", "TRUE"))

    extra_args = props.extra_cmd.strip()
    if extra_args:
        cmd.extend(arg for arg in extra_args.split(" ") if arg)
    return cmd


def _mark_sharp_edges_from_smooth(obj, edge_indices):
    mesh = getattr(obj, "data", None)
    if not mesh or not hasattr(mesh, "edges"):
        return
    for index in edge_indices:
        if index < len(mesh.edges):
            mesh.edges[index].use_edge_sharp = True


def _is_smooth_by_angle_modifier(modifier):
    if modifier.type != "NODES":
        return False
    node_group = getattr(modifier, "node_group", None)
    node_group_name = node_group.name if node_group else ""
    normalized_mod_name = re.sub(r"[^a-z0-9]+", "", modifier.name.casefold())
    normalized_ng_name = re.sub(r"[^a-z0-9]+", "", node_group_name.casefold())
    return normalized_mod_name.startswith("smoothbyangle") or normalized_ng_name.startswith("smoothbyangle")


def _apply_smooth_by_angle_to_temp(source_obj, temp_obj, context):
    smooth_mod = None
    for mod in source_obj.modifiers:
        if _is_smooth_by_angle_modifier(mod):
            smooth_mod = mod
            break
    if not smooth_mod:
        return False
    old_active = None
    try:
        new_mod = temp_obj.modifiers.new(name=smooth_mod.name, type='NODES')
        new_mod.node_group = smooth_mod.node_group
        old_active = context.view_layer.objects.active
        temp_obj.select_set(True)
        context.view_layer.objects.active = temp_obj
        bpy.ops.object.modifier_apply(modifier=new_mod.name)
        return True
    except Exception as e:
        print(f"Could not apply Smooth by Angle to temp object {temp_obj.name}: {e}")
        return False
    finally:
        if old_active is not None:
            try:
                context.view_layer.objects.active = old_active
            except Exception:
                pass


def draw_mof_auto_uv_settings(layout, context):
    props = context.scene.mof_properties
    is_open = getattr(context.scene, "show_auto_uv_settings", False)
    layout.prop(
        context.scene,
        "show_auto_uv_settings",
        text="Auto UV Settings",
        icon='DISCLOSURE_TRI_DOWN' if is_open else 'DISCLOSURE_TRI_RIGHT'
    )
    if not is_open:
        return

    input_box = layout.box()
    input_box.label(text="Mesh Input")
    col = input_box.column(align=True)
    col.use_property_split = True
    for attr in ("force_hard_surface", "auto_detect_hard_edges", "separate_sharp_edges", "keep_existing_seams"):
        col.prop(props, attr)

    output_box = layout.box()
    output_box.label(text="UV Result")
    col = output_box.column(align=True)
    col.use_property_split = True
    col.prop(props, "mark_seam_edges")
    col.prop(props, "correct_self_intersecting")
    col.prop(props, "packing")
    col.prop(props, "world_scale")

    split_box = layout.box()
    split_box.label(text="Grouping")
    col = split_box.column(align=True)
    col.use_property_split = True
    col.prop(props, "separate_by_material")
    col.prop(props, "triangulate_mesh")
    col.prop(props, "use_normal")
    col.prop(props, "overlap_identical_parts")
    col.prop(props, "overlap_mirrored_parts")

    texel_box = layout.box()
    texel_box.label(text="Density")
    col = texel_box.column(align=True)
    col.use_property_split = True
    col.prop(props, "use_texel_density")
    texel_col = col.column(align=True)
    texel_col.active = props.use_texel_density
    texel_col.prop(props, "texture_size", text="Map Size")
    texel_col.prop(props, "texture_density", text="Px / Meter")

    advanced_box = layout.box()
    advanced_box.label(text="MOF Tuning")
    col = advanced_box.column(align=True)
    col.use_property_split = True
    for attr in ("quads", "squares", "stretch", "cut", "weld", "extra_cmd"):
        col.prop(props, attr)

    target_box = layout.box()
    target_box.label(text="UV Map")
    if context.object and context.object.type == 'MESH':
        target_box.prop_search(
            props,
            "target_uv_map",
            context.object.data,
            "uv_layers",
            text="Target UV Map"
        )
    else:
        target_box.prop(props, "target_uv_map", text="Target UV Map")


# -------------------------------------------------------------------
# Operator to perform auto UV unwrapping via external tool
# -------------------------------------------------------------------
class AutoUVOperator(Operator):
    bl_idname = "object.auto_uv_operator"
    bl_label = "Auto UV Unwrap"
    bl_description = "Generate UVs for the selected mesh or faces using MinistryOfFlat"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return os.name == "nt"


    def execute(self, context):
        global last_target_uv_map

        props = context.scene.mof_properties
        last_target_uv_map = props.target_uv_map

        active_obj = context.view_layer.objects.active
        restore_mode = active_obj.mode if active_obj else 'OBJECT'
        selected_names = [obj.name for obj in context.selected_objects]
        active_name = active_obj.name if active_obj else None

        temp_objects = []
        imported_objects = []
        cleanup_paths = []

        try:
            targets = _collect_auto_uv_targets(context, restore_mode)
            if not targets:
                self.report({'WARNING'}, "Auto UV: Select mesh faces or mesh objects to unwrap.")
                return {'CANCELLED'}

            prefs = context.preferences.addons[__package__].preferences
            zip_path = bpy.path.abspath(prefs.executable_path) if prefs.executable_path else ""
            if not zip_path or not os.path.exists(zip_path):
                self.report({'ERROR'}, "Auto UV: MinistryOfFlat zip file is not set or not found.")
                try:
                    bpy.ops.wm.bakeaddon_missing_mof_popup('INVOKE_DEFAULT')
                except Exception:
                    pass
                return {'CANCELLED'}

            exe = _get_mof_executable_from_cache(zip_path)

            _safe_object_mode()

            temp_records = []
            for obj_name, face_indices in targets:
                source_obj = bpy.data.objects.get(obj_name)
                if not source_obj or source_obj.type != 'MESH':
                    continue

                uv_layer_name = _ensure_target_uv_layer(source_obj, props)
                for face_group in _auto_uv_face_groups(source_obj, face_indices, props):
                    original_seams = _capture_face_edge_seams(source_obj.data, face_group) if props.keep_existing_seams else None
                    temp_obj, loop_maps, source_face_indices, sharp_edge_indices = _create_mof_temp_object(context, source_obj, face_group, props)
                    temp_objects.append(temp_obj)
                    temp_records.append({
                        "source_name": source_obj.name,
                        "temp_name": temp_obj.name,
                        "loop_maps": loop_maps,
                        "source_face_indices": source_face_indices,
                        "sharp_edge_indices": sharp_edge_indices,
                        "uv_layer_name": uv_layer_name,
                        "original_seams": original_seams,
                    })

            if not temp_records:
                self.report({'WARNING'}, "Auto UV: No valid faces were prepared for MOF.")
                return {'CANCELLED'}

            bpy.ops.object.select_all(action='DESELECT')
            for temp_obj in temp_objects:
                temp_obj.select_set(True)
            context.view_layer.objects.active = temp_objects[0]

            if props.separate_sharp_edges:
                for record in temp_records:
                    source_obj = bpy.data.objects.get(record["source_name"])
                    temp_obj = bpy.data.objects.get(record["temp_name"])
                    if source_obj and temp_obj:
                        _apply_smooth_by_angle_to_temp(source_obj, temp_obj, context)

            job_id = uuid.uuid4().hex
            temp_dir = bpy.app.tempdir or tempfile.gettempdir()
            in_path = os.path.join(temp_dir, f"bakeaddon_mof_{job_id}.obj")
            out_path = os.path.join(temp_dir, f"bakeaddon_mof_{job_id}_unwrapped.obj")
            cleanup_paths.extend([in_path, out_path])

            try:
                export_result = bpy.ops.wm.obj_export(
                    filepath=in_path,
                    export_selected_objects=True,
                    export_materials=False,
                    export_uv=False,
                    export_normals=True,
                    export_smooth_groups=True,
                    forward_axis='Y',
                    up_axis='Z'
                )
            except TypeError:
                export_result = bpy.ops.wm.obj_export(
                    filepath=in_path,
                    export_selected_objects=True,
                    export_materials=False,
                    forward_axis='Y',
                    up_axis='Z'
                )

            if 'FINISHED' not in export_result:
                self.report({'ERROR'}, "Auto UV: OBJ export failed.")
                return {'CANCELLED'}

            for temp_obj in temp_objects[:]:
                _remove_object_and_mesh(temp_obj)
            temp_objects.clear()

            cmd = _build_mof_command(exe, in_path, out_path, props)
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            except subprocess.TimeoutExpired:
                self.report({'ERROR'}, "Auto UV: MinistryOfFlat timed out.")
                return {'CANCELLED'}

            if result.returncode != 0 and not (os.path.exists(out_path) and os.path.getsize(out_path) > 0):
                message = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
                self.report({'ERROR'}, f"Auto UV: MinistryOfFlat failed ({message})")
                return {'CANCELLED'}

            if not os.path.exists(out_path) or os.path.getsize(out_path) <= 0:
                self.report({'ERROR'}, "Auto UV: MinistryOfFlat did not produce an output OBJ.")
                return {'CANCELLED'}

            before_import_names = {obj.name for obj in bpy.data.objects}
            import_result = bpy.ops.wm.obj_import(filepath=out_path, forward_axis='Y', up_axis='Z')
            if 'FINISHED' not in import_result:
                self.report({'ERROR'}, "Auto UV: OBJ import failed.")
                return {'CANCELLED'}

            imported_objects = [obj for obj in bpy.data.objects if obj.name not in before_import_names]
            imported_by_name = {obj.name: obj for obj in imported_objects if obj.type == 'MESH'}
            uv_scale = 0.0
            if not props.use_texel_density:
                max_uv_coord = 0.0
                for imported_obj in imported_objects:
                    if imported_obj.type != 'MESH' or not imported_obj.data.uv_layers.active:
                        continue
                    uv_data = _uv_layer_data(imported_obj.data.uv_layers.active)
                    for uv_item in uv_data:
                        uv = uv_item.vector if hasattr(uv_item, "vector") else uv_item.uv
                        max_uv_coord = max(max_uv_coord, uv.x, uv.y)
                if max_uv_coord:
                    uv_scale = 1.0 / max_uv_coord

            for record in temp_records:
                source_obj = bpy.data.objects.get(record["source_name"])
                imported_obj = imported_by_name.get(record["temp_name"])

                if not imported_obj:
                    matches = [
                        obj for obj in imported_objects
                        if obj.type == 'MESH' and obj.name.startswith(record["temp_name"])
                    ]
                    imported_obj = matches[0] if matches else None

                if not source_obj or not imported_obj:
                    raise RuntimeError(f"Could not match MOF result for {record['source_name']}.")

                _copy_imported_uvs_to_source(
                    source_obj,
                    imported_obj,
                    record["loop_maps"],
                    record["source_face_indices"],
                    record["uv_layer_name"],
                    props,
                    uv_scale,
                    record["original_seams"],
                )

            if props.separate_sharp_edges:
                for record in temp_records:
                    source_obj = bpy.data.objects.get(record["source_name"])
                    if source_obj:
                        _mark_sharp_edges_from_smooth(source_obj, record.get("sharp_edge_indices", set()))

            self.report({'INFO'}, f"Auto UV complete for {len(temp_records)} unwrap group(s).")
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, f"Auto UV failed: {exc}")
            return {'CANCELLED'}
        finally:
            for obj in imported_objects:
                _remove_object_and_mesh(obj)
            for obj in temp_objects:
                _remove_object_and_mesh(obj)
            for path in cleanup_paths:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
            _restore_selection_and_mode(context, selected_names, active_name, restore_mode)


# Helper function to remove temporary objects.
def remove_temp(temp_obj):
    if temp_obj and temp_obj.name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[temp_obj.name], do_unlink=True)
    return None

# -------------------------------------------------------------------
# Main UI Panels
# -------------------------------------------------------------------
class MOFMOFPanel(Panel):
    """
    Main UI Panel for the MOF UV Unwrapper addon.

    Displays general settings and buttons for executing the auto UV unwrap.
    """
    bl_label = "MOF UV unwrapper"
    bl_idname = "VIEW3D_PT_MOF_mof"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Blender MOF'
    
    def draw(self, context):
        layout = self.layout
        prefs = context.preferences.addons[__package__].preferences
        zip_path = bpy.path.abspath(prefs.executable_path) if prefs.executable_path else ""
        if not prefs.executable_path or not os.path.exists(zip_path):
            layout.label(text="MinistryOfFlat zip file not set", icon='ERROR')
            layout.label(text="Please download the MinistryOfFlat zip file from the official site.", icon='INFO')
            row = layout.row(align=True)
            row.prop(prefs, "executable_path", text="Executable Zip Path:")
            row.operator("wm.clearmofzippath", text="", icon='X')
        else:
            row = layout.row(align=True)
            row.prop(prefs, "executable_path", text="Executable Zip Path:")
            row.operator("wm.clearmofzippath", text="", icon='X')
        
        if os.name != "nt":
            layout.label(text="UV Unwrapping is only available on Windows", icon='ERROR')
        else:
            # Auto UV Unwrap button
            layout.operator(AutoUVOperator.bl_idname, icon='MOD_UVPROJECT')
            layout.separator(factor=1.5)
            # Manual Fixes section
            manual_box = layout.box()
            manual_box.label(text="Manual Fixes", icon='TOOL_SETTINGS')
            manual_box.operator(MOFSeamsOperator.bl_idname, icon='MOD_UVPROJECT')
            manual_box.operator(SelectStretchingModalOperator.bl_idname, icon='UV_SYNC_SELECT')
            manual_box.operator(UnWrapOperator.bl_idname, icon='ERROR')

            # Packing section always visible under Manual Fixes
            pack_box = layout.box()
            pack_box.label(text="Packing", icon='PACKAGE')
            pack_box.operator(AverageIslandsScaleOperator.bl_idname, icon='PACKAGE')
            pack_box.operator(PackUVOperator.bl_idname, icon='PACKAGE')

            draw_mof_auto_uv_settings(layout, context)

class AverageIslandsScaleOperator(Operator):
    bl_idname = "uv.average_islands_scale_mof"
    bl_label = "1 - Average Islands Scale"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        bpy.ops.uv.average_islands_scale(scale_uv=True)
        return {'FINISHED'}

class PackUVOperator(Operator):
    bl_idname = "uv.pack_mof"
    bl_label = "2 - Pack"
    bl_options = {"REGISTER", "UNDO"}

    _is_running = False

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        if PackUVOperator._is_running:
            self.report({'WARNING'}, "Pack is already running.")
            return {'CANCELLED'}

        PackUVOperator._is_running = True
        try:
            if bpy.ops.uv.pack_islands.poll():
                bpy.ops.uv.pack_islands(rotate_method='CARDINAL', margin=0.03)
                return {'FINISHED'}

            for area in context.screen.areas:
                if area.type != 'IMAGE_EDITOR':
                    continue
                region = next((region for region in area.regions if region.type == 'WINDOW'), None)
                if not region:
                    continue
                space = area.spaces.active
                with context.temp_override(area=area, region=region, space_data=space):
                    if bpy.ops.uv.pack_islands.poll():
                        bpy.ops.uv.pack_islands(rotate_method='CARDINAL', margin=0.03)
                        return {'FINISHED'}

            self.report({'ERROR'}, "Pack requires selected UV islands in Edit Mode.")
            return {'CANCELLED'}
        finally:
            PackUVOperator._is_running = False

class MOFSeamsOperator(Operator):
    bl_idname = "object.mof_seams"
    bl_label = "MOF Seams"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(obj.type == 'MESH' for obj in context.selected_objects)

    def execute(self, context):
        import bmesh
        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            current_mode = obj.mode
            if current_mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            mesh = obj.data
            bm = bmesh.new()
            bm.from_mesh(mesh)
            uv_layer = bm.loops.layers.uv.verify()
            for e in bm.edges:
                e.seam = False
            for e in bm.edges:
                if not e.is_manifold:
                    continue
                linked_faces = e.link_faces
                if len(linked_faces) != 2:
                    continue
                f1, f2 = linked_faces
                l1 = [l for l in f1.loops if l.edge == e][0]
                l2 = [l for l in f2.loops if l.edge == e][0]
                uv1_1 = l1[uv_layer].uv
                uv1_2 = l1.link_loop_next[uv_layer].uv
                uv2_1 = l2[uv_layer].uv
                uv2_2 = l2.link_loop_next[uv_layer].uv
                if (uv1_1 - uv2_2).length > 1e-5 or (uv1_2 - uv2_1).length > 1e-5:
                    e.seam = True
            bm.to_mesh(mesh)
            bm.free()
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode=current_mode)
        self.report({'INFO'}, "MOF Seams: Marked seams from UV borders for selected meshes.")
        return {'FINISHED'}

class UnWrapOperator(Operator):
    bl_idname = "object.unwrap_mof"
    bl_label = "UnWrap"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(obj.type == 'MESH' for obj in context.selected_objects)

    def execute(self, context):
        import bmesh
        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            current_mode = obj.mode
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='EDIT')
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.verify()
            bm.faces.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            # Only clear and mark seams for selected faces/islands
            selected_faces = [f for f in bm.faces if f.select]
            # Clear seams only on edges of selected faces
            selected_edges = set(e for f in selected_faces for e in f.edges)
            for e in selected_edges:
                e.seam = False
            # Mark seams where UVs differ across adjacent faces, but only for selected faces
            for e in selected_edges:
                linked_faces = [f for f in e.link_faces if f in selected_faces]
                if len(linked_faces) != 2:
                    continue
                f1, f2 = linked_faces
                l1 = [l for l in f1.loops if l.edge == e][0]
                l2 = [l for l in f2.loops if l.edge == e][0]
                uv1_1 = l1[uv_layer].uv
                uv1_2 = l1.link_loop_next[uv_layer].uv
                uv2_1 = l2[uv_layer].uv
                uv2_2 = l2.link_loop_next[uv_layer].uv
                if (uv1_1 - uv2_2).length > 1e-5 or (uv1_2 - uv2_1).length > 1e-5:
                    e.seam = True
            bmesh.update_edit_mesh(obj.data)
            # Unwrap only selected faces
            bpy.ops.uv.unwrap(method='ANGLE_BASED', fill_holes=True, correct_aspect=True, use_subsurf_data=False, margin=0.005, no_flip=False, iterations=10, use_weights=False, weight_group="uv_importance", weight_factor=1)
            bpy.ops.object.mode_set(mode=current_mode)
        self.report({'INFO'}, "Marked seams and unwrapped selected faces/islands in selected meshes.")
        return {'FINISHED'}

def get_angle_stretch(face, uv_layer):
    import math
    loops = face.loops
    n = len(loops)
    angle_diff_sum = 0.0
    for i in range(n):
        v_prev = loops[i - 1].vert.co
        v = loops[i].vert.co
        v_next = loops[(i + 1) % n].vert.co
        uv_prev = loops[i - 1][uv_layer].uv
        uv = loops[i][uv_layer].uv
        uv_next = loops[(i + 1) % n][uv_layer].uv
        # 3D angle
        vec1_3d = (v_prev - v).normalized()
        vec2_3d = (v_next - v).normalized()
        angle_3d = math.acos(max(-1.0, min(1.0, vec1_3d.dot(vec2_3d))))
        # UV angle
        vec1_uv = (uv_prev - uv).normalized()
        vec2_uv = (uv_next - uv).normalized()
        angle_uv = math.acos(max(-1.0, min(1.0, vec1_uv.dot(vec2_uv))))
        angle_diff_sum += abs(angle_3d - angle_uv)
    return angle_diff_sum / n if n > 0 else 0.0

def get_uv_islands_with_stretch(obj):
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    uv_layer = bm.loops.layers.uv.verify()
    visited = set()
    islands = []
    for face in bm.faces:
        if face.index in visited:
            continue
        island_faces = set()
        stack = [face]
        while stack:
            f = stack.pop()
            if f.index in island_faces:
                continue
            island_faces.add(f.index)
            for e in f.edges:
                if not e.seam:
                    for linked_face in e.link_faces:
                        if linked_face.index not in island_faces:
                            stack.append(linked_face)
        # Use the maximum angle-based stretch value in the island
        angle_stretches = [get_angle_stretch(bm.faces[i], uv_layer) for i in island_faces]
        max_angle_stretch = max(angle_stretches) if angle_stretches else 0.0
        islands.append((island_faces, max_angle_stretch))
        visited.update(island_faces)
    bm.free()
    return islands

def select_relative_stretched_islands(obj, threshold):
    import bmesh
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select = False
    uv_layer = bm.loops.layers.uv.verify()
    all_islands = get_uv_islands_with_stretch(obj)
    if not all_islands:
        return
    stretches = [s for (_, s) in all_islands]
    min_s = min(stretches)
    max_s = max(stretches)
    range_s = max_s - min_s if max_s != min_s else 1.0
    for face_indices, avg_stretch in all_islands:
        norm = (avg_stretch - min_s) / range_s
        if norm >= threshold:
            for i in face_indices:
                bm.faces[i].select = True
    bmesh.update_edit_mesh(obj.data)

class SelectStretchingModalOperator(Operator):
    bl_idname = "object.select_stretching_modal"
    bl_label = "Select Stretching (Interactive)"
    bl_options = {"REGISTER", "UNDO"}

    threshold: bpy.props.FloatProperty(
        name="Relative Threshold",
        description="Normalized stretch threshold (0 = least stretched, 1 = most stretched)",
        default=0.8,
        min=0.0,
        max=1.0
    )

    _start_mouse_x = 0
    _start_threshold = 0.8
    _obj = None
    _area = None

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH' and context.mode == 'EDIT_MESH'

    def invoke(self, context, event):
        self._start_mouse_x = event.mouse_x
        self._start_threshold = self.threshold
        self._obj = context.object
        self._area = context.area
        context.window_manager.modal_handler_add(self)
        self.update_selection(context)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if self._area:
            for region in self._area.regions:
                if region.type == 'WINDOW':
                    region.tag_redraw()
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self.update_selection(context, restore=True)
            return {'CANCELLED'}
        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'}:
            return {'FINISHED'}
        elif event.type == 'MOUSEMOVE':
            delta = (event.mouse_x - self._start_mouse_x) / 600.0
            new_threshold = self._start_threshold + delta
            new_threshold = max(0.0, min(1.0, new_threshold))
            if abs(new_threshold - self.threshold) > 1e-4:
                self.threshold = new_threshold
                self.update_selection(context)
        return {'RUNNING_MODAL'}

    def update_selection(self, context, restore=False):
        if restore:
            bpy.ops.mesh.select_all(action='SELECT')
            return
        # Get all selected objects in edit mode
        selected_objects = [obj for obj in context.selected_objects if obj.type == 'MESH']
        
        if not selected_objects:
            self.report({'WARNING'}, "No mesh objects selected")
            return {'CANCELLED'}

        # Store all islands and their stretch values
        all_islands = []
        
        # Process each selected object
        for obj in selected_objects:
            islands = get_uv_islands_with_stretch(obj)
            # Add object reference to each island tuple
            all_islands.extend([(obj, faces, stretch) for faces, stretch in islands])

        if not all_islands:
            self.report({'WARNING'}, "No UV islands found")
            return {'CANCELLED'}

        # Find min and max stretch values across all objects
        min_stretch = min(stretch for _, _, stretch in all_islands)
        max_stretch = max(stretch for _, _, stretch in all_islands)
        stretch_range = max_stretch - min_stretch

        if stretch_range == 0:
            self.report({'WARNING'}, "No stretch variation found")
            return {'CANCELLED'}

        # Select islands based on normalized stretch values
        threshold_value = min_stretch + (stretch_range * self.threshold)
        
        # Deselect all faces first
        bpy.ops.mesh.select_all(action='DESELECT')
        
        # Select faces in islands that exceed the threshold
        for obj, faces, stretch in all_islands:
            if stretch >= threshold_value:
                # Make the object active temporarily
                context.view_layer.objects.active = obj
                # Enter edit mode if not already
                if obj.mode != 'EDIT':
                    bpy.ops.object.mode_set(mode='EDIT')
                # Select the faces
                bm = bmesh.from_edit_mesh(obj.data)
                for face_idx in faces:
                    if face_idx < len(bm.faces):
                        bm.faces[face_idx].select = True
                bmesh.update_edit_mesh(obj.data)

        self.report({'INFO'}, f"Selected islands with stretch >= {threshold_value:.3f}")
        return {'FINISHED'}

classes = (
    CheckMOFZipVersionOperator,
    ClearMOFZipPathOperator,
    DownloadMOFZipOperator,
    BAKEADDON_OT_missing_mof_popup,
    MOFProperties,
    MOFAddonPreferences,
    AutoUVOperator,
    AverageIslandsScaleOperator,
    PackUVOperator,
    MOFSeamsOperator,
    UnWrapOperator,
    SelectStretchingModalOperator,
    MOFMOFPanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.show_general_settings = BoolProperty(
        name="Show General Settings",
        default=False,
        description="Toggle visibility of general settings"
    )
    bpy.types.Scene.mof_properties = PointerProperty(type=MOFProperties)
    bpy.types.Scene.show_advanced_settings = BoolProperty(
        name="Show Advanced Settings",
        default=False,
        description="Toggle visibility of advanced settings"
    )
    bpy.types.Scene.show_auto_uv_settings = BoolProperty(
        name="Show Auto UV Settings",
        default=False,
        description="Toggle visibility of Auto UV settings"
    )

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.show_general_settings
    del bpy.types.Scene.mof_properties
    del bpy.types.Scene.show_advanced_settings
    del bpy.types.Scene.show_auto_uv_settings

if __name__ == "__main__":
    register()
