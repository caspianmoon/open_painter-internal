# --- START OF FILE properties.py ---

# gready/properties.py

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import PropertyGroup


EXPORT_MODE_ITEMS = (
    ("PARENT", "Grouped by Parent", ""),
    ("ALL", "Single Combined File", ""),
    ("INDIVIDUAL", "One File per Object", ""),
    ("COLLECTION", "Grouped by Collection", ""),
    ("SUBSTANCE_PAINTER", "Substance Painter", ""),
)

EXPORT_FORMAT_ITEMS = (
    ("FBX", "FBX", ""),
    ("OBJ", "OBJ", ""),
    ("GLTF", "GLTF", ""),
)

GLTF_IMAGE_FORMAT_ITEMS = (
    ("AUTO", "Auto", ""),
    ("JPEG", "JPEG", ""),
    ("WEBP", "WebP", ""),
    ("NONE", "None", ""),
)

EXPORT_SMOOTHING_ITEMS = (
    ("OFF", "Normals Only", ""),
    ("FACE", "Face", ""),
    ("EDGE", "Edge", ""),
)

EXPORT_TARGET_ENGINE_ITEMS = (
    ("UNITY", "Unity", ""),
    ("UNREAL", "Unreal", ""),
    ("UNITY_PRE_ROTATED", "Unity PreRotated", ""),
)

EXPORT_VC_COLOR_SPACE_ITEMS = (
    ("LINEAR", "Linear", ""),
    ("SRGB", "sRGB", ""),
)

EXPORT_AXIS_ITEMS = (
    ("X", "X", ""),
    ("Y", "Y", ""),
    ("Z", "Z", ""),
    ("-X", "-X", ""),
    ("-Y", "-Y", ""),
    ("-Z", "-Z", ""),
)


def _next_export_axis(axis):
    axis_values = [item[0] for item in EXPORT_AXIS_ITEMS]
    try:
        index = axis_values.index(axis)
    except ValueError:
        return axis_values[0]
    return axis_values[(index + 1) % len(axis_values)]


def update_custom_forward_axis(self, _context):
    if self.custom_export_forward_axis == self.custom_export_up_axis:
        self.custom_export_up_axis = _next_export_axis(self.custom_export_up_axis)


def update_custom_up_axis(self, _context):
    if self.custom_export_forward_axis == self.custom_export_up_axis:
        self.custom_export_forward_axis = _next_export_axis(self.custom_export_forward_axis)


def update_export_mode(self, _context):
    if self.export_mode == "SUBSTANCE_PAINTER" and self.export_format != "FBX":
        self.export_format = "FBX"
    if self.export_mode == "SUBSTANCE_PAINTER":
        self.apply_rot = False
        self.apply_scale = False


def update_export_format(self, _context):
    if self.export_mode == "SUBSTANCE_PAINTER" and self.export_format != "FBX":
        self.export_format = "FBX"


# --- Properties (Factor Edge Select - Easy Surface) ---

class FactorSelectProperties(PropertyGroup):
    """Properties for the Factor Edge Select feature"""
    threshold_factor: FloatProperty(
        name="Threshold Factor",
        description="Selection threshold factor (0=0deg, 1=180deg). Controls maximum angle difference.",
        default= 30.0 / 180.0,
        min=0.0,
        max=1.0,
        subtype='FACTOR'
    )

# --- Properties (Surface Select - Added Addon) ---
class SurfaceSelectProperties(PropertyGroup):
    angle_degrees: FloatProperty(
        name="Angle Threshold (Degrees)", # Explicitly mention degrees in name
        description="Maximum angle between face normals to consider connected (Enter value in degrees)",
        default=40.0,
        min=0.0,      # Hard minimum limit
        max=180.0,    # Hard maximum limit
        soft_min=0.0,
        soft_max=180.0,
        # REMOVED: subtype='ANGLE',
        # REMOVED: unit='ROTATION'
    )
    # --- End Property Definition ---


# --- Properties (Bevel Shader) ---

def update_bevel_preset_assignment(self, context):
    if context is None:
        return
    props = getattr(context.scene, "bevel_shading_props", None)
    if props and getattr(props, "suspend_preset_updates", False):
        return
    try:
        from . import operators_bevel_shader
        operators_bevel_shader.update_preset_assignments(context, self)
    except Exception as exc:
        print(f"G-Ready 2.1 Warning: Could not update Bevel Shader preset assignment: {exc}")


BEVEL_UNIT_ITEMS = (
    ('UNITS', 'units', 'Scene-independent generic units'),
    ('MILLIMETERS', 'mm', 'Millimeters'),
    ('CENTIMETERS', 'cm', 'Centimeters'),
    ('METERS', 'm', 'Meters'),
    ('INCHES', 'in', 'Inches'),
    ('FEET', 'ft', 'Feet'),
)


class BevelShaderPresetItem(PropertyGroup):
    radius: FloatProperty(
        name="Radius",
        description="Bevel shader radius for this preset",
        default=1.0,
        min=0.0,
        subtype='UNSIGNED',
        precision=4,
        update=update_bevel_preset_assignment,
    )
    units: EnumProperty(
        name="Units",
        description="Units used by this radius preset",
        items=BEVEL_UNIT_ITEMS,
        default='CENTIMETERS',
        update=update_bevel_preset_assignment,
    )
    color: FloatVectorProperty(
        name="Color",
        description="Color used to identify this radius preset",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.25, 0.55, 1.0),
    )
    last_scene_radius: FloatProperty(
        name="Stored Scene Radius",
        description="Previous scene-unit radius used to update matching assigned bevel data",
        default=-1.0,
        options={'HIDDEN'},
    )


class BevelShadingProperties(PropertyGroup):
    presets: CollectionProperty(type=BevelShaderPresetItem)
    presets_index: IntProperty(
        name="Preset Index",
        description="Active bevel radius preset",
        default=0,
        min=0,
    )
    boolean_radius_expanded: BoolProperty(
        name="Boolean Radius",
        description="Show Boolean Radius controls",
        default=False,
    )
    auto_preview_material: BoolProperty(
        name="Auto",
        description="Automatically toggle the preview material override with render preview",
        default=True,
    )
    max_bevel_value: FloatProperty(
        name="Max Bevel Value",
        description="Largest radius value currently known to the Bevel Shader system",
        default=0.0,
        options={'HIDDEN'},
    )
    suspend_preset_updates: BoolProperty(default=False, options={'HIDDEN'})
    preview_config_stored: BoolProperty(default=False, options={'HIDDEN'})
    preview_backup_shading: EnumProperty(
        name="Backup Shading",
        description="Viewport shading mode to restore after render preview",
        items=(
            ('WIREFRAME', "Wireframe", ""),
            ('SOLID', "Solid", ""),
            ('MATERIAL', "Material Preview", ""),
            ('RENDERED', "Rendered", ""),
        ),
        default='SOLID',
        options={'HIDDEN'},
    )
    preview_saved_engine: StringProperty(default="", options={'HIDDEN'})
    preview_saved_device: StringProperty(default="", options={'HIDDEN'})
    preview_saved_samples: IntProperty(default=0, options={'HIDDEN'})
    preview_saved_adaptive: BoolProperty(default=False, options={'HIDDEN'})
    preview_saved_adaptive_threshold: FloatProperty(default=0.1, options={'HIDDEN'})
    preview_saved_min_samples: IntProperty(default=0, options={'HIDDEN'})
    preview_saved_denoise: BoolProperty(default=False, options={'HIDDEN'})

    # Compatibility for older scripts that still address the previous properties.
    bevel_radius: FloatProperty(
        name="Radius",
        description="Compatibility radius for older Bevel Shader scripts",
        default=0.05,
        min=0.0,
        subtype='DISTANCE',
        unit='LENGTH',
        precision=4,
    )
    bevel_samples: IntProperty(
        name="Samples",
        description="Compatibility sample count for older Bevel Shader scripts",
        default=16,
        min=1,
        soft_max=128,
    )
# --- End Bevel Shader Properties ---

# --- Properties for Edge Data ---
class EdgeDataProperties(PropertyGroup):
    """Properties for setting edge data like Bevel Weight and Crease"""
    bevel_weight_value: FloatProperty(
        name="Bevel Weight",
        description="Value to apply to selected edges' Bevel Weight",
        default=1.0, # Already 1.0
        min=0.0,
        max=1.0,
        subtype='FACTOR' # Use FACTOR subtype for 0-1 range
    )
    crease_value: FloatProperty(
        name="Crease",
        description="Value to apply to selected edges' Crease",
        default=1.0, # <-- CHANGED from 0.3 to 1.0
        min=0.0,
        max=1.0,
        subtype='FACTOR' # Use FACTOR subtype for 0-1 range
    )
# --- END Edge Data Properties ---

# --- Properties for Vertex Color Ops --- <<< --- NEW SECTION --- <<<
class VColorOpsSettings(PropertyGroup):
    """Properties for the Vertex Color Ops Tools addon (Integrated into G-Ready 2.1)"""
    custom_color: FloatVectorProperty(
        name="Custom Color",
        description="Color to apply when using 'Apply'",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.8, 0.2, 0.2)
    )
# --- END Vertex Color Ops Properties ---


# --- Action filter for selective FBX export ---
class GReadyActionFilterItem(PropertyGroup):
    """Represents a single bpy.data.action for selective export."""
    name: StringProperty(name="Action Name")
    use_export: BoolProperty(name="Export", default=True)
    frame_range: StringProperty(name="Frame Range", default="")


def update_selective_actions(self, context):
    """Sync action list when Filter Actions is toggled."""
    try:
        if not hasattr(context.scene, 'gready_export_settings'):
            return
        export_settings = context.scene.gready_export_settings
        existing = {item.name: item for item in export_settings.filtered_actions}
        export_settings.filtered_actions.clear()
        for action in bpy.data.actions:
            item = export_settings.filtered_actions.add()
            item.name = action.name
            if action.name in existing:
                item.use_export = existing[action.name].use_export
            else:
                item.use_export = True
            if action.frame_range:
                item.frame_range = f"{int(action.frame_range[0])}-{int(action.frame_range[1])}"
        print(f"G-Ready: Synced {len(export_settings.filtered_actions)} actions")
    except Exception as e:
        print(f"G-Ready Error in update_selective_actions: {e}")
        import traceback
        traceback.print_exc()


class GReadyExportSettings(PropertyGroup):
    export_dir: StringProperty(name="", description="Export Directory")
    export_mode: EnumProperty(name="", items=EXPORT_MODE_ITEMS, default="PARENT", update=update_export_mode)
    export_format: EnumProperty(name="", items=EXPORT_FORMAT_ITEMS, default="FBX", update=update_export_format)
    obj_separate_by_materials: BoolProperty(name="", description="Separate Objects By Materials", default=True)
    obj_export_smooth_groups: BoolProperty(name="", description="Export Smooth Groups", default=True)
    gltf_export_image_format: EnumProperty(name="", items=GLTF_IMAGE_FORMAT_ITEMS, default="AUTO")
    gltf_export_attributes: BoolProperty(name="", description="Export Attributes", default=False)
    apply_rot: BoolProperty(description="Apply Rotation for Exported Models", default=True)
    apply_rot_rotated: BoolProperty(description="Apply Rotation for Objects with not 0,0,0 rotation", default=True)
    apply_scale: BoolProperty(description="Apply Scale for Exported Models", default=True)
    apply_loc: BoolProperty(description="Apply Location for Exported Models", default=True)
    set_custom_fbx_name: BoolProperty(description="Set Custom Name for FBX", default=False)
    custom_fbx_name: StringProperty(name="", description="Custom Name for FBX", default="")
    custom_export_path: BoolProperty(description="Custom Export Path", default=False)
    delete_mats_before_export: BoolProperty(description="Delete Materials before Export", default=False)
    convert_viewport_colors: BoolProperty(name="Convert Viewport Colors", description="Create Principled BSDF materials from Viewport Display colors for export", default=True)
    embed_textures: BoolProperty(name="Embed Textures", description="Embed textures inside FBX file (increases file size but more portable)", default=False)
    triangulate_before_export: BoolProperty(description="Triangulate Meshes before Export", default=False)
    substance_triangulate_quads: BoolProperty(description="Triangulate Substance Painter quads with Canonical Symmetric Triangulate V6", default=True)
    export_path: StringProperty(name="", description="Path for Export FBX", subtype="DIR_PATH", default="")
    export_custom_options: BoolProperty(description="Custom FBX Export Options", default=False)
    export_loose_edges: BoolProperty(name="", description="Loose Edges", default=False)
    export_tangent_space: BoolProperty(name="", description="Tangent Space", default=False)
    export_smoothing: EnumProperty(name="", items=EXPORT_SMOOTHING_ITEMS, default="OFF")
    export_custom_props: BoolProperty(name="", description="Custom Props", default=True)
    export_animation: BoolProperty(name="Animation", description="Export animation data", default=True)
    export_anim_key_all_bones: BoolProperty(name="Key All Bones", description="Force a keyframe on every bone for exported animations", default=True)
    export_anim_nla_strips: BoolProperty(name="NLA Strips", description="Export NLA strips as animation takes", default=True)
    export_anim_all_actions: BoolProperty(name="All Actions", description="Export all compatible actions", default=True)
    export_anim_force_start_end_keying: BoolProperty(name="Force Start/End Keying", description="Force keyframes at the start and end of each animation", default=True)
    export_anim_sampling_rate: FloatProperty(name="Sampling Rate", description="Animation sampling rate", default=1.0, min=0.01, soft_min=0.01, soft_max=100.0)
    export_anim_simplify: FloatProperty(name="Simplify", description="Animation keyframe simplification factor", default=1.0, min=0.0, soft_min=0.0, soft_max=100.0)
    export_target_engine: EnumProperty(name="", items=EXPORT_TARGET_ENGINE_ITEMS, default="UNITY")
    export_combine_meshes: BoolProperty(description="Combine Meshes before Export", default=False)
    export_only_deform_bones: BoolProperty(name="", description="Only Deform Bones", default=False)
    export_add_leaf_bones: BoolProperty(name="", description="Add Leaf Bones", default=False)
    export_vc_color_space: EnumProperty(name="", items=EXPORT_VC_COLOR_SPACE_ITEMS, default="LINEAR")
    use_custom_export_scale: BoolProperty(name="", description="Set custom scale for export", default=False)
    custom_export_scale_value: FloatProperty(name="", description="Scale", default=1.0, min=0.00001, max=9999, step=1)
    use_custom_export_axes: BoolProperty(name="", description="Set custom Forward/Up axes", default=False)
    custom_export_forward_axis: EnumProperty(
        name="",
        default="-Z",
        items=EXPORT_AXIS_ITEMS,
        update=update_custom_forward_axis,
    )
    custom_export_up_axis: EnumProperty(
        name="",
        default="Y",
        items=EXPORT_AXIS_ITEMS,
        update=update_custom_up_axis,
    )
    show_export_axis_tooltip: BoolProperty(
        name="",
        description="Show/Hide Export Axes Conversion Tooltip for UE/Unity",
        default=True,
        options={'HIDDEN'},
    )
    debug: BoolProperty(description="Enable Debug Mode", default=False, options={'HIDDEN'})

    # --- NEW: Selective Action Export ---
    use_selective_actions: BoolProperty(
        name="Filter Actions",
        description="Only export selected actions (instead of All Actions) via NLA strips",
        default=False,
        update=update_selective_actions,
    )
    filtered_actions: CollectionProperty(type=GReadyActionFilterItem)
    filtered_actions_index: IntProperty(name="Active Index", default=-1, options={'HIDDEN'})
    # --- END NEW ---


class GReadySmartApplyProps(PropertyGroup):
    apply_location: BoolProperty(name="Location", default=False)
    apply_rotation: BoolProperty(name="Rotation", default=False)
    apply_scale: BoolProperty(name="Scale", default=True)
    compensate_modifiers: BoolProperty(
        name="Keep Array/Mirror",
        description="Preserve array & mirror visually after apply",
        default=True,
    )

# --- END OF FILE properties.py ---
