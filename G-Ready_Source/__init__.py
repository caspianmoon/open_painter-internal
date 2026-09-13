# --- START OF FILE __init__.py --- [MODIFIED]

# gready/__init__.py

bl_info = {
    "name": "G-Ready 2.1",
    "blender": (4, 5, 0), # Updated for Blender 4.5
    "category": "Object",
    "author": "Farid Mammadov",
    "version": (2, 1, 0),
    "description": "This addon was created by 3D Artist Farid Mammadov (Pivot Point) to speed up game asset creation workflow. Includes tools for Boolean workflow, edge/surface selection, Bevel Node shading setup, texture baking (Closest Hit, Matrix), LOD generation, Vertex Color operations, UnBevel, and flexible Auto Renaming.", # <-- UPDATED Description
    "location": "View3D > Sidebar > G-Ready 2.1 Tab",
    "warning": "The addon is still in development. There can be bugs. Compositor AA is experimental. Matrix Bake is experimental.",
    "doc_url": "",
    "tracker_url": "",
}

import bpy
import math
import os
import bpy.utils.previews

from bpy.props import (
    PointerProperty,
    StringProperty,
    FloatProperty,
    EnumProperty,
    BoolProperty,
    IntProperty
)

# --- Import shared state FIRST ---
from . import shared_state

# Relative imports for other modules
from . import properties
from . import ui
from . import utils
from . import operators_general
from . import operators_cleanup
from . import operators_offset
from . import operators_baking
from . import operators_bevel_shader
from . import operators_lod
from . import operators_matrix_bake
from . import operators_vertex_color
from . import operators_unbevel
from . import operators_trimsheet
from . import operators_texture_to_trim
from . import operators_auto_uv
from . import operators_bake_explode
from . import operators_export
from . import operators_smart_apply
from .operators_baking import (
    BAKER_OT_AutoRename,
    OBJECT_OT_create_cages_from_list,
    BAKER_OT_Bake,
    BAKER_OT_visualize_ray_distance,
    BAKER_OT_remove_ray_distance_visualization,
    BAKER_OT_output_settings,
    update_max_ray_distance
)

# --- Update function for GPU Toggle ---
# [ ... keep existing update functions ... ]
def update_cycles_device(self, context):
    if not context.scene: return
    if context.scene.render.engine == 'CYCLES':
        new_device = 'GPU' if self.baker_use_gpu else 'CPU'
        if context.scene.cycles.device != new_device:
            context.scene.cycles.device = new_device
            print(f"G-Ready 2.1 Info: Cycles device set to {new_device} via UI toggle.")

# --- Update function for Normal Map Format ---
def update_normal_map_format(self, context):
    if not context.scene: return
    bake_settings = context.scene.render.bake
    if self.baker_normal_format == 'OPENGL':
        if bake_settings.normal_g != 'POS_Y':
            bake_settings.normal_g = 'POS_Y'
            print("G-Ready 2.1 Info: Normal Map Green Channel set to +Y (OpenGL)")
    elif self.baker_normal_format == 'DIRECTX':
        if bake_settings.normal_g != 'NEG_Y':
            bake_settings.normal_g = 'NEG_Y'
            print("G-Ready 2.1 Info: Normal Map Green Channel set to -Y (DirectX)")
# --- End Update functions ---

def update_show_ray_distance(self, context):
    """Update visualization when show_ray_distance is toggled"""
    if context is None:
        return
    try:
        if self.baker_show_ray_distance:
            bpy.ops.baker.visualize_ray_distance()
        else:
            bpy.ops.baker.remove_ray_distance_visualization()
    except Exception as e:
        print(f"G-Ready 2.1 Warning: Could not update ray distance visualization: {e}")

# List of all classes to register/unregister
classes = (
    # Properties
    properties.FactorSelectProperties,
    properties.SurfaceSelectProperties,
    properties.BevelShaderPresetItem,
    properties.BevelShadingProperties,
    properties.EdgeDataProperties,
    properties.VColorOpsSettings,
    properties.GReadyActionFilterItem,
    properties.GReadyExportSettings,
    properties.GReadySmartApplyProps,
    operators_trimsheet.GREADY_PG_TrimSection,
    operators_trimsheet.GREADY_PG_TrimSettings,
    operators_texture_to_trim.GREADY_PG_TextureToTrimSettings,
    # UI
    ui.GReadyPanel,
    ui.GREADY_OT_radial_menu,
    ui.MESH_OT_gready_bevel_select,
    operators_trimsheet.GREADY_UL_trim_sections,
    operators_trimsheet.GREADY_PT_trim_sheet_uv,
    operators_texture_to_trim.GREADY_PT_texture_to_trim_sheet,
    ui.MODELING_PT_panel,
    ui.MATERIAL_PT_bevel_shading_panel,
    ui.OBJECT_PT_auto_rename_panel,
    ui.AUTO_UV_PT_panel,
    ui.BAKE_EXPLODE_PT_panel,
    ui.OBJECT_PT_bake_panel,
    ui.OBJECT_PT_export_gready,
    ui.GREADY_OT_export_toggle_selective_actions,
    ui.GREADY_OT_export_refresh_actions,
    ui.GREADY_OT_export_select_all_actions,
    ui.GREADY_OT_export_deselect_all_actions,
    ui.OBJECT_PT_smart_apply_export_gready,
    ui.VIEW3D_PT_vertex_color_ops_tools_gready,
    ui.LOD_PT_generator,
    ui.CONTACT_PT_gready,
    # Operators - General
    operators_general.OBJECT_OT_select_factor_loop,
    operators_general.MESH_OT_surface_select,
    operators_general.GREADY_OT_copy_email,
    # Operators - Cleanup
    operators_cleanup.OBJECT_OT_BooleanCleanup,
    # Operators - Offset
    operators_offset.OBJECT_OT_gready_offset,
    # Operators - Baking <<< --- MODIFIED --- <<<
    operators_baking.BAKER_OT_AutoRename,
    operators_baking.OBJECT_OT_create_cages_from_list,
    operators_baking.BAKER_OT_Bake, # Original Bake
    operators_baking.BAKER_OT_visualize_ray_distance, # New operator
    operators_baking.BAKER_OT_remove_ray_distance_visualization, # New operator
    operators_baking.BAKER_OT_output_settings, # Output settings file browser
    # Operators - Export
    operators_export.GREADY_OT_export_model,
    operators_export.GREADY_OT_open_export_dir,
    operators_export.GREADY_OT_origin_z_minimum,
    # Operators - Smart Apply
    operators_smart_apply.GREADY_OT_smart_apply_transforms,
    # Operators - Bevel Shader
    operators_bevel_shader.GREADY_OT_bevel_set_radius,
    operators_bevel_shader.GREADY_OT_bevel_set_zero_radius,
    operators_bevel_shader.GREADY_OT_bevel_preset_add,
    operators_bevel_shader.GREADY_OT_bevel_preset_remove,
    operators_bevel_shader.GREADY_OT_bevel_preview_toggle,
    operators_bevel_shader.GREADY_OT_bevel_material_preview_toggle,
    operators_bevel_shader.GREADY_OT_bevel_materials_repair,
    operators_bevel_shader.GREADY_OT_bevel_object_cleanup,
    operators_bevel_shader.GREADY_OT_bevel_global_cleanup,
    operators_bevel_shader.OBJECT_OT_apply_bevel_shading,
    operators_bevel_shader.OBJECT_OT_remove_bevel_shading,
    # Operators - LOD
    operators_lod.OBJECT_OT_generate_lods,
    # Operators - Matrix Bake
    operators_matrix_bake.MATRIXBAKE_OT_matrix_bake,
    # Operators - Vertex Color
    operators_vertex_color.OBJECT_OT_set_custom_vertex_color_ops,
    operators_vertex_color.OBJECT_OT_set_random_vertex_color_ops,
    operators_vertex_color.OBJECT_OT_select_by_vertex_color,
    # Operators - UnBevel
    operators_unbevel.OBJECT_OT_unbevel_modal_op,
    # Operators - Trim Sheet
    operators_trimsheet.GREADY_OT_trim_add,
    operators_trimsheet.GREADY_OT_trim_remove,
    operators_trimsheet.GREADY_OT_trim_import_preset,
    operators_trimsheet.GREADY_OT_trim_export_preset,
    operators_trimsheet.GREADY_OT_trim_draw,
    operators_trimsheet.GREADY_OT_trim_toggle_edit,
    operators_trimsheet.GREADY_OT_assign_to_trim,
    operators_trimsheet.GREADY_OT_trim_scale,
    operators_texture_to_trim.GREADY_OT_trim_layout_preview_toggle,
    operators_texture_to_trim.GREADY_OT_texture_to_trim_sheet,
    # Operators - Auto UV
    operators_auto_uv.MOFProperties,
    operators_auto_uv.MOFAddonPreferences,
    operators_auto_uv.ClearMOFZipPathOperator,
    operators_auto_uv.CheckMOFZipVersionOperator,
    operators_auto_uv.DownloadMOFZipOperator,
    operators_auto_uv.GREADY_OT_missing_mof_popup,
    operators_auto_uv.AutoUVOperator,
    operators_auto_uv.AverageIslandsScaleOperator,
    operators_auto_uv.PackUVOperator,
    operators_auto_uv.MOFSeamsOperator,
    operators_auto_uv.UnWrapOperator,
    operators_auto_uv.SelectStretchingModalOperator,
    # Operators - Bake Explode
    operators_bake_explode.BakeExplodeSettings,
    operators_bake_explode.BAKE_EXPLODE_OT_Store,
    operators_bake_explode.BAKE_EXPLODE_OT_Reset,
    operators_bake_explode.BAKE_EXPLODE_OT_Clear,
    operators_bake_explode.BAKE_EXPLODE_OT_HideLinkedDuplicates,
    operators_bake_explode.BAKE_EXPLODE_OT_UnhideLinkedDuplicates,
    operators_bake_explode.BAKE_EXPLODE_OT_ToggleVisualization,
)

addon_keymaps = []


def _mesh_alt_g_has_default_conflict():
    try:
        default_config = bpy.context.window_manager.keyconfigs.default
        mesh_keymap = default_config.keymaps.get("Mesh") if default_config else None
        if not mesh_keymap:
            return False

        for item in mesh_keymap.keymap_items:
            if (item.active and item.type == 'G' and item.value == 'PRESS' and
                    item.alt and not item.ctrl and not item.shift and not item.oskey):
                return True
    except Exception as e:
        print(f"G-Ready 2.1 Warning: Could not inspect default Mesh keymap for Alt+G: {e}")
    return False


def register_keymaps():
    wm = bpy.context.window_manager
    addon_config = wm.keyconfigs.addon
    if not addon_config:
        print("G-Ready 2.1 Warning: Addon keyconfig unavailable; radial menu hotkey not registered.")
        return

    use_shift = _mesh_alt_g_has_default_conflict()
    keymap = addon_config.keymaps.new(name="Mesh", space_type='EMPTY')
    keymap_item = keymap.keymap_items.new(
        ui.GREADY_OT_radial_menu.bl_idname,
        type='G',
        value='PRESS',
        alt=True,
        shift=use_shift
    )
    addon_keymaps.append((keymap, keymap_item))

    hotkey = "Alt+Shift+G" if use_shift else "Alt+G"
    print(f"G-Ready 2.1 Info: Registered radial menu hotkey in Edit Mesh mode: {hotkey}")


def unregister_keymaps():
    for keymap, keymap_item in addon_keymaps:
        try:
            keymap.keymap_items.remove(keymap_item)
        except Exception as e:
            print(f"G-Ready 2.1 Warning: Could not remove radial menu keymap item: {e}")
    addon_keymaps.clear()

# --- List of Baking/Rename Properties to manage ---
# [ ... keep existing baking_rename_props_list ... ]
baking_rename_props_list = [
    # Auto Rename Props
    ("baker_rename_mode", EnumProperty(
        name="Rename Mode",
        description="Method used to determine the base name for low/high pairs",
        items=[
            ('NUMERICAL', "Numerical", "Assign sequential numbers (0, 1, 2...) as base names"),
            ('HIGH_TO_LOW', "High -> Low", "Use the high-poly's original name as the base name"),
            ('LOW_TO_HIGH', "Low -> High", "Use the low-poly's original name as the base name"),
        ],
        default='NUMERICAL'
    )),
    ("baker_rename_use_bbox", BoolProperty(name="Use Bounding Box", default=True)),
    ("baker_rename_use_pivot", BoolProperty(name="Use Pivot Point", default=True)),
    ("baker_rename_bbox_threshold", FloatProperty(name="BBox Threshold (cm)", description="Maximum difference in bounding box dimensions (in cm) to consider objects a pair", default=1.0, min=0.0, soft_max=10.0, step=10, precision=2)),
    ("baker_rename_pivot_threshold", FloatProperty(name="Pivot Threshold (cm)", description="Maximum distance between pivot points (in cm) to consider objects a pair", default=0.1, min=0.0, soft_max=5.0, step=1, precision=3)),
    # Baking Props
    ("baker_cage_offset", FloatProperty(name="Cage Offset (cm)", description="Amount to offset cage vertices along their normals (in cm) using Shrink/Fatten", default=1.0, min=-100.0, soft_max=10.0, step=10, precision=2)),
    ("bake_output_dir", StringProperty(name="Output Directory", subtype='DIR_PATH', default="//")),
    ("bake_image_format", EnumProperty(name="Image Format", items=[('PNG', "PNG", ""), ('JPEG', "JPEG", ""), ('TIFF', "TIFF", ""), ('TARGA', "TARGA", ""), ('BMP', "BMP", ""), ('OPEN_EXR', "OpenEXR", "")], default='PNG')),
    ("baker_png_color_mode", EnumProperty(name="Color Mode", items=[('BW', "BW", "Black & White"), ('RGB', "RGB", "RGB Color"), ('RGBA', "RGBA", "RGB Color with Alpha")], default='RGBA')),
    ("baker_png_bit_depth", EnumProperty(name="Bit Depth", items=[('8', "8", "8 bits per channel"), ('16', "16", "16 bits per channel")], default='8')),
    ("baker_png_compression", IntProperty(name="Compression", description="PNG compression level (0=none, 100=max)", default=15, min=0, max=100)),
    ("baker_jpeg_quality", IntProperty(name="Quality", description="JPEG quality (0=low, 100=high)", default=90, min=0, max=100)),
    ("baker_jpeg_color_mode", EnumProperty(name="Color Mode", items=[('BW', "BW", "Black & White"), ('RGB', "RGB", "RGB Color")], default='RGB')), # No RGBA for JPEG
    ("baker_tiff_color_mode", EnumProperty(name="Color Mode", items=[('BW', "BW", "Black & White"), ('RGB', "RGB", "RGB Color"), ('RGBA', "RGBA", "RGB Color with Alpha")], default='RGBA')),
    ("baker_tiff_bit_depth", EnumProperty(name="Bit Depth", items=[('8', "8", "8 bits per channel"), ('16', "16", "16 bits per channel")], default='8')),
    ("baker_tiff_codec", EnumProperty(name="Compression", items=[('NONE', "None", ""), ('LZW', "LZW", ""), ('PACKBITS', "Pack Bits", ""), ('DEFLATE', "Deflate (ZIP)", "")], default='DEFLATE')),
    ("baker_targa_color_mode", EnumProperty(name="Color Mode", items=[('BW', "BW", "Black & White"), ('RGB', "RGB", "RGB Color"), ('RGBA', "RGBA", "RGB Color with Alpha")], default='RGBA')),
    ("baker_targa_rle", BoolProperty(name="RLE Compression", description="Use Targa RLE compression", default=False)),
    ("baker_exr_color_mode", EnumProperty(name="Color Mode", items=[('RGB', "RGB", "RGB Color"), ('RGBA', "RGBA", "RGB Color with Alpha")], default='RGBA')),
    ("baker_exr_bit_depth", EnumProperty(name="Bit Depth", items=[('16', "Float (Half)", "16 bits per channel"), ('32', "Float (Full)", "32 bits per channel")], default='16')),
    ("baker_exr_codec", EnumProperty(name="Codec", items=[('NONE', "None", ""), ('PXR24', "PXR24", "Lossy 24-bit float"), ('ZIP', "ZIP", "Lossless, scanline zip"), ('PIZ', "PIZ", "Lossless, wavelet"), ('RLE', "RLE", "Lossless run-length encoding"), ('ZIPS', "ZIPS", "Lossless, scanline zip"), ('B44', "B44", "Lossy 4x4, half float"), ('B44A', "B44A", "Lossy 4x4, half float + alpha"), ('DWAA', "DWAA", "Lossy DCT"), ('DWAB', "DWAB", "Lossy DCT")], default='ZIP')),
    ("baker_exr_use_preview", BoolProperty(name="Save Preview", description="Save a preview image in the EXR file", default=True)),
    ("baker_export_preset", EnumProperty(
        name="Export Preset",
        description="Choose the baked texture set and channel convention",
        items=[
            ('CUSTOM', "Custom", "Bake the selected map using the current output settings"),
            (
                'PUREPLAY_ORD',
                "PurePlay ORD",
                "Bake BaseColor, Metallic, DirectX Normal, and packed Occlusion/Roughness/Displacement PNG maps",
            ),
        ],
        default='CUSTOM',
    )),
    ("baker_pureplay_bake_ao", BoolProperty(
        name="AO Map",
        description="Bake geometric AO into ORD red; when disabled ORD red is full white",
        default=True,
    )),
    ("bake_map_type", EnumProperty(name="Bake Type", items=[('COMBINED', "Combined", ""), ('AO', "Ambient Occlusion", ""), ('SHADOW', "Shadow", ""), ('POSITION', "Position", ""), ('NORMAL', "Normal", ""), ('UV', "UV", ""), ('ROUGHNESS', "Roughness", ""), ('EMIT', "Emit", ""), ('ENVIRONMENT', "Environment", ""), ('DIFFUSE', "Diffuse", ""), ('GLOSSY', "Glossy", ""), ('TRANSMISSION', "Transmission", "")], default='NORMAL')),
    ("baker_normal_format", EnumProperty(name="Normal Format", description="Set the Green channel orientation for Normal Map baking", items=[('OPENGL', "OpenGL (+Y)", "Set Green channel to +Y"), ('DIRECTX', "DirectX (-Y)", "Set Green channel to -Y")], default='OPENGL', update=update_normal_map_format)),
    ("baker_use_no_skew", BoolProperty(name="No Skew (Edge Split)", description="Add and apply an Edge Split modifier to cages before offsetting to reduce skewing on sharp corners (Normal Bake only)", default=False)),
    ("baker_no_skew_use_angle", BoolProperty(name="Edge Angle", description="Split edges based on the angle between faces", default=False)),
    ("baker_no_skew_angle", FloatProperty(name="Split Angle", description="Angle above which to split edges for the 'No Skew' feature", default=math.radians(30.0), min=0.0, max=math.pi, subtype='ANGLE', unit='ROTATION')),
    ("baker_no_skew_use_sharps", BoolProperty(name="Sharp Edges", description="Also split edges marked as sharp for the 'No Skew' feature", default=False)),
    ("baker_bake_mode", EnumProperty(name="Bake Mode", items=[('HIGH_TO_LOW', "High to Low", "")], default='HIGH_TO_LOW')),
    ("baker_use_gpu", BoolProperty(name="Use GPU", description="Set Cycles device to GPU (if available) when checked, CPU when unchecked. Applied immediately if Cycles is active.", default=True, update=update_cycles_device)),
    ("baker_resolution_preset", EnumProperty(name="Resolution Preset", items=[('256', "256x256", ""), ('512', "512x512", ""), ('1024', "1024x1024", ""), ('2048', "2048x2048", ""), ('4096', "4096x4096", ""), ('8192', "8192x8192", ""), ('CUSTOM', "Custom", "")], default='2048')),
    ("baker_custom_width", IntProperty(name="Width", default=2048, min=1, soft_max=8192)),
    ("baker_custom_height", IntProperty(name="Height", default=2048, min=1, soft_max=8192)),
    ("baker_samples", IntProperty(name="Samples", description="Number of samples for Cycles baking", default=32, min=1, soft_max=4096)),
    ("baker_multiple_materials", BoolProperty(name="Multiple Materials", description="Save one baked image per material instead of one combined image", default=False)),
    ("baker_matrix_bake_selected", BoolProperty(name="Bake Selected", description="Matrix Bake only the selected mesh objects and their matching _high/_cage partners", default=False)),
    ("baker_use_uv_override", BoolProperty(name="Use Custom UV Channel", description="Override default UV channel for baking", default=False)),
    ("baker_source_uv_map", StringProperty(name="Source UV Map", description="Source UV map name for high poly objects (leave empty for active)", default="")),
    ("baker_target_uv_map", StringProperty(name="Target UV Map", description="Target UV map name for low poly objects (leave empty for active)", default="")),
    ("baker_use_compositor_aa", BoolProperty(
        name="Anti-Alias Output (Compositor)",
        description="EXPERIMENTAL: Render the final bake through the Compositor's Anti-Aliasing node (Threshold 0.1). Slower, clears existing Compositor setup!",
        default=False
    )),
    # Eye Button Prop
    ("baker_show_ray_distance", BoolProperty(
        name="Show Ray Distance",
        description="Toggle visibility of Max Ray Distance",
        default=False,
        update=update_show_ray_distance
    )),
    # Max Ray Distance Prop
    ("max_ray_distance", FloatProperty(
        name="Max Ray Distance",
        description="Maximum ray distance for baking",
        default=0.0,
        min=0.0,
        update=update_max_ray_distance
    )),
]


def register():
    print("\n--- G-Ready 2.1 Register ---")
    shared_state.custom_icons = bpy.utils.previews.new()
    print(f"DEBUG: Initialized shared_state.custom_icons: {shared_state.custom_icons}")

    # --- Icon Loading Logic [ ... keep existing ... ] ---
    try:
        addon_dir = os.path.dirname(os.path.realpath(__file__))
        icons_dir = os.path.join(addon_dir, "icons")
        print(f"DEBUG: Addon directory: {addon_dir}")
        print(f"DEBUG: Expected icons directory: {icons_dir}")
    except Exception as e:
        print(f"ERROR: Could not determine addon/icons directory: {e}")
        icons_dir = None

    if icons_dir and os.path.exists(icons_dir):
        print(f"G-Ready 2.1 Info: Loading custom icons from: {icons_dir}")
        try:
            files = os.listdir(icons_dir)
            print(f"DEBUG: Files found in icons folder: {files}")
            loaded_icon_keys = []
            for f in files:
                filepath = os.path.join(icons_dir, f)
                if not os.path.isfile(filepath):
                    continue

                if f.lower().endswith(".png"):
                    icon_name = os.path.splitext(f)[0]
                    print(f"DEBUG: Attempting to load icon '{icon_name}' from '{filepath}'")
                    try:
                        shared_state.custom_icons.load(icon_name, filepath, 'IMAGE')
                        print(f"  - SUCCESS: Loaded icon: {icon_name}")
                        loaded_icon_keys.append(icon_name)
                    except Exception as e:
                        print(f"  - WARNING: Could not load icon '{f}': {e}")
                else:
                    print(f"  - Skipping non-PNG file: {f}")

            if not loaded_icon_keys:
                 print(f"  - No PNG files were successfully loaded.")
            else:
                 print(f"DEBUG: Final loaded icon keys: {loaded_icon_keys}")

        except Exception as e:
            print(f"ERROR: Failed during icon directory scan/load: {e}")

    elif icons_dir:
        print(f"G-Ready 2.1 WARNING: Icons folder not found at: {icons_dir}")
        print("  - Custom icons will not be available.")
    else:
         print(f"G-Ready 2.1 ERROR: Could not determine icons directory path.")


    print("DEBUG: Registering classes...")
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError as e:
            if "already registered" not in str(e):
                print(f"WARNING: Could not register {cls.__name__}. Error: {e}")
        except AttributeError as e:
             print(f"ERROR: Could not register class {cls.__name__}. Possible missing import or definition? Error: {e}")
        except Exception as e:
             print(f"ERROR: Unexpected error registering {cls.__name__}: {e}")

    register_keymaps()

    print("DEBUG: Registering properties...")
    try:
        bpy.types.Scene.factor_select_props = PointerProperty(type=properties.FactorSelectProperties)
        bpy.types.Scene.surface_select_props = PointerProperty(type=properties.SurfaceSelectProperties)
        bpy.types.Scene.bevel_shading_props = PointerProperty(type=properties.BevelShadingProperties)
        operators_bevel_shader.register_object_properties()
        bpy.types.Scene.edge_data_props = PointerProperty(type=properties.EdgeDataProperties)
        bpy.types.Scene.gready_vcolor_ops_settings = PointerProperty(type=properties.VColorOpsSettings)
        bpy.types.Scene.gready_export_settings = PointerProperty(type=properties.GReadyExportSettings)
        bpy.types.Scene.gready_smart_apply_props = PointerProperty(type=properties.GReadySmartApplyProps)
        bpy.types.Scene.gready_trim_settings = PointerProperty(type=operators_trimsheet.GREADY_PG_TrimSettings)
        bpy.types.Scene.gready_texture_to_trim_settings = PointerProperty(type=operators_texture_to_trim.GREADY_PG_TextureToTrimSettings)
        bpy.types.Material.gready_trim_sections = bpy.props.CollectionProperty(type=operators_trimsheet.GREADY_PG_TrimSection)
        bpy.types.Material.gready_trim_index = bpy.props.IntProperty(name="Active Trim", default=-1)
        operators_texture_to_trim.register_material_trim_priority_property()
        # Legacy migration sources. Live Trim Sheet data is material-owned.
        bpy.types.Scene.gready_trim_sections = bpy.props.CollectionProperty(type=operators_trimsheet.GREADY_PG_TrimSection)
        bpy.types.Scene.gready_trim_index = bpy.props.IntProperty(name="Active Trim", default=-1)
        bpy.types.Image.gready_trim_sections = bpy.props.CollectionProperty(type=operators_trimsheet.GREADY_PG_TrimSection)
        bpy.types.Image.gready_trim_index = bpy.props.IntProperty(name="Active Trim", default=-1)
        bpy.types.Object.gready_trim_scale_stashes = StringProperty(name="G-Ready 2.1 Trim Scale Stashes", default="", options={'HIDDEN'})
        bpy.types.Object.gready_trim_scale_next_patch_id = IntProperty(name="G-Ready 2.1 Trim Scale Next Patch ID", default=1, min=1, options={'HIDDEN'})
        bpy.types.Object.gready_trim_scale_next_edge_id = IntProperty(name="G-Ready 2.1 Trim Scale Next Edge ID", default=1, min=1, options={'HIDDEN'})

        # Register baking, rename mode, and matrix bake properties
        for prop_name, prop_instance in baking_rename_props_list: # Use the combined list
            setattr(bpy.types.Scene, prop_name, prop_instance)

        # Register LOD property
        setattr(bpy.types.Scene, "lod_generator_levels", bpy.props.IntProperty(
            name="Max LOD Level",
            description="Highest LOD level to generate (e.g., 3 generates LOD0, LOD1, LOD2, LOD3)",
            default=3,
            min=1,
            max=5
        ))

        # Hidden flag: has the default bevel preset been auto-added in this file?
        setattr(bpy.types.Scene, "bevel_shader_auto_added", bpy.props.BoolProperty(
            name="Auto Added",
            description="Internal: tracks if default bevel preset was auto-added",
            default=False,
            options={'HIDDEN'}
        ))
        # Register LOD mode
        setattr(bpy.types.Scene, "lod_mode", bpy.props.EnumProperty(
            name="LOD Mode",
            description="Choose between ratio-based or target triangle count decimation",
            items=[
                ('RATIO', "Ratio", "Use decimation ratio sliders"),
                ('TARGET_TRIS', "Target Tris", "Use target triangle count inputs")
            ],
            default='RATIO'
        ))
        # Register LOD ratio properties
        for i in range(1, 6):
            setattr(bpy.types.Scene, f"lod_ratio_{i}", bpy.props.FloatProperty(
                name=f"LOD{i} Ratio",
                description=f"Decimation ratio for LOD{i} (0.0 = no decimation, 1.0 = full decimation)",
                default=0.5 ** i,  # 0.5, 0.25, 0.125, 0.0625, 0.03125
                min=0.0,
                max=1.0,
                precision=5
            ))
        # Register LOD tris properties
        for i in range(1, 6):
            setattr(bpy.types.Scene, f"lod_tris_{i}", bpy.props.IntProperty(
                name=f"LOD{i} Tris",
                description=f"Target triangle count for LOD{i}",
                default=4096 // (2 ** i),  # 2048, 1024, 512, 256, 128
                min=1,
                max=100000
            ))

        # Register UnBevel property
        setattr(bpy.types.Scene, "unbevel_edge_slide", bpy.props.BoolProperty(
            name="Edge Slide",
            description="For UnBevel: Move end vertices along adjacent edge directions (preserves shape)",
            default=False
        ))

        # Register Auto UV properties
        setattr(bpy.types.Scene, "show_general_settings", bpy.props.BoolProperty(
            name="Show General Settings",
            default=False,
            description="Toggle visibility of general settings"
        ))
        setattr(bpy.types.Scene, "show_advanced_settings", bpy.props.BoolProperty(
            name="Show Advanced Settings",
            default=False,
            description="Toggle visibility of advanced settings"
        ))
        setattr(bpy.types.Scene, "show_auto_uv_settings", bpy.props.BoolProperty(
            name="Show Auto UV Settings",
            default=False,
            description="Toggle visibility of Auto UV settings"
        ))
        setattr(bpy.types.Scene, "mof_properties", bpy.props.PointerProperty(type=operators_auto_uv.MOFProperties))

        # Register Bake Explode property
        setattr(bpy.types.Scene, "bake_explode_settings", bpy.props.PointerProperty(type=operators_bake_explode.BakeExplodeSettings))

        print("  - Properties registered successfully.")
        operators_trimsheet.ensure_trimsheet_draw_handler()
        operators_texture_to_trim.register_trim_layout_preview_runtime()
        for scene in bpy.data.scenes:
            try:
                scene.render.bake.margin = 4
            except Exception as e:
                print(f"Warning: Could not set default bake margin for scene '{scene.name}': {e}")
    except Exception as e:
        print(f"ERROR: Failed to register properties: {e}")

    v = bl_info['version']
    print(f"G-Ready 2.1 Addon Registered (v{v[0]}.{v[1]}.{v[2]})")
    print("--- End G-Ready 2.1 Register ---\n")

def unregister():
    print("\n--- G-Ready 2.1 Unregister ---")
    
    # Cleanup Bake Explode visualization
    try:
        from . import operators_bake_explode
        operators_bake_explode.cleanup_all_visualization()
    except Exception as e:
        print(f"Warning: Could not cleanup Bake Explode visualization: {e}")

    unregister_keymaps()

    try:
        operators_trimsheet.remove_trimsheet_draw_handlers()
    except Exception as e:
        print(f"Warning: Could not remove Trim Sheet draw handlers: {e}")
    try:
        operators_texture_to_trim.unregister_trim_layout_preview_runtime()
    except Exception as e:
        print(f"Warning: Could not clear TrimSheet preview runtime: {e}")

    if shared_state.cleanup_draw_handles:
         print("DEBUG: Removing Boolean Cleanup draw handlers...")
         handles_to_remove = shared_state.cleanup_draw_handles[:]
         shared_state.cleanup_draw_handles.clear()
         for handle in handles_to_remove:
             try:
                 bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
             except ValueError:
                 pass

    for handle_list_name in ("radial_draw_handles", "angle_hud_draw_handles", "offset_draw_handles", "unbevel_draw_handles", "trim_scale_draw_handles"):
        handle_list = getattr(shared_state, handle_list_name, [])
        if handle_list:
            print(f"DEBUG: Removing {handle_list_name}...")
            handles_to_remove = handle_list[:]
            handle_list.clear()
            for handle in handles_to_remove:
                try:
                    bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
                except ValueError:
                    pass

    print("DEBUG: Unregistering baking, rename mode, and matrix bake properties...")
    for prop_name, _ in reversed(baking_rename_props_list): # Use the combined list
        if hasattr(bpy.types.Scene, prop_name):
            try:
                delattr(bpy.types.Scene, prop_name)
            except Exception as e:
                print(f"Warning: Could not delete scene prop {prop_name}: {e}")

    print("DEBUG: Unregistering other properties...")
    # [ ... keep existing property unregistration ... ]
    try:
        operators_bevel_shader.unregister_object_properties()
    except Exception as e:
        print(f"Warning: Could not unregister Bevel Shader object properties: {e}")
    if hasattr(bpy.types.Scene, 'factor_select_props'):
        try: del bpy.types.Scene.factor_select_props
        except Exception as e: print(f"Warning: Could not delete factor_select_props: {e}")
    if hasattr(bpy.types.Scene, 'surface_select_props'):
        try: del bpy.types.Scene.surface_select_props
        except Exception as e: print(f"Warning: Could not delete surface_select_props: {e}")
    if hasattr(bpy.types.Scene, 'bevel_shader_auto_added'):
        try: del bpy.types.Scene.bevel_shader_auto_added
        except Exception as e: print(f"Warning: Could not delete bevel_shader_auto_added: {e}")
    if hasattr(bpy.types.Scene, 'bevel_shading_props'):
        try: del bpy.types.Scene.bevel_shading_props
        except Exception as e: print(f"Warning: Could not delete bevel_shading_props: {e}")
    if hasattr(bpy.types.Scene, 'edge_data_props'):
        try: del bpy.types.Scene.edge_data_props
        except Exception as e: print(f"Warning: Could not delete edge_data_props: {e}")
    if hasattr(bpy.types.Scene, 'gready_vcolor_ops_settings'):
        try: del bpy.types.Scene.gready_vcolor_ops_settings
        except Exception as e: print(f"Warning: Could not delete gready_vcolor_ops_settings: {e}")
    if hasattr(bpy.types.Scene, 'gready_export_settings'):
        try: del bpy.types.Scene.gready_export_settings
        except Exception as e: print(f"Warning: Could not delete gready_export_settings: {e}")
    if hasattr(bpy.types.Scene, 'gready_smart_apply_props'):
        try: del bpy.types.Scene.gready_smart_apply_props
        except Exception as e: print(f"Warning: Could not delete gready_smart_apply_props: {e}")
    if hasattr(bpy.types.Scene, 'gready_trim_settings'):
        try: del bpy.types.Scene.gready_trim_settings
        except Exception as e: print(f"Warning: Could not delete gready_trim_settings: {e}")
    if hasattr(bpy.types.Scene, 'gready_texture_to_trim_settings'):
        try: del bpy.types.Scene.gready_texture_to_trim_settings
        except Exception as e: print(f"Warning: Could not delete gready_texture_to_trim_settings: {e}")
    if hasattr(bpy.types.Material, 'gready_trim_sections'):
        try: del bpy.types.Material.gready_trim_sections
        except Exception as e: print(f"Warning: Could not delete material gready_trim_sections: {e}")
    if hasattr(bpy.types.Material, 'gready_trim_index'):
        try: del bpy.types.Material.gready_trim_index
        except Exception as e: print(f"Warning: Could not delete material gready_trim_index: {e}")
    try:
        operators_texture_to_trim.unregister_material_trim_priority_property()
    except Exception as e:
        print(f"Warning: Could not delete material trim priority: {e}")
    if hasattr(bpy.types.Scene, 'gready_trim_sections'):
        try: del bpy.types.Scene.gready_trim_sections
        except Exception as e: print(f"Warning: Could not delete scene gready_trim_sections: {e}")
    if hasattr(bpy.types.Scene, 'gready_trim_index'):
        try: del bpy.types.Scene.gready_trim_index
        except Exception as e: print(f"Warning: Could not delete scene gready_trim_index: {e}")
    if hasattr(bpy.types.Image, 'gready_trim_sections'):
        try: del bpy.types.Image.gready_trim_sections
        except Exception as e: print(f"Warning: Could not delete image gready_trim_sections: {e}")
    if hasattr(bpy.types.Image, 'gready_trim_index'):
        try: del bpy.types.Image.gready_trim_index
        except Exception as e: print(f"Warning: Could not delete image gready_trim_index: {e}")
    if hasattr(bpy.types.Object, 'gready_trim_scale_stashes'):
        try: del bpy.types.Object.gready_trim_scale_stashes
        except Exception as e: print(f"Warning: Could not delete gready_trim_scale_stashes: {e}")
    if hasattr(bpy.types.Object, 'gready_trim_scale_next_patch_id'):
        try: del bpy.types.Object.gready_trim_scale_next_patch_id
        except Exception as e: print(f"Warning: Could not delete gready_trim_scale_next_patch_id: {e}")
    if hasattr(bpy.types.Object, 'gready_trim_scale_next_edge_id'):
        try: del bpy.types.Object.gready_trim_scale_next_edge_id
        except Exception as e: print(f"Warning: Could not delete gready_trim_scale_next_edge_id: {e}")
    if hasattr(bpy.types.Scene, "lod_generator_levels"):
        try: del bpy.types.Scene.lod_generator_levels
        except Exception as e: print(f"Warning: Could not delete lod_generator_levels: {e}")
    if hasattr(bpy.types.Scene, "lod_mode"):
        try: del bpy.types.Scene.lod_mode
        except Exception as e: print(f"Warning: Could not delete lod_mode: {e}")
    for i in range(1, 6):
        lod_ratio_prop = f"lod_ratio_{i}"
        if hasattr(bpy.types.Scene, lod_ratio_prop):
            try: delattr(bpy.types.Scene, lod_ratio_prop)
            except Exception as e: print(f"Warning: Could not delete {lod_ratio_prop}: {e}")
        lod_tris_prop = f"lod_tris_{i}"
        if hasattr(bpy.types.Scene, lod_tris_prop):
            try: delattr(bpy.types.Scene, lod_tris_prop)
            except Exception as e: print(f"Warning: Could not delete {lod_tris_prop}: {e}")
    if hasattr(bpy.types.Scene, "unbevel_edge_slide"):
        try: del bpy.types.Scene.unbevel_edge_slide
        except Exception as e: print(f"Warning: Could not delete unbevel_edge_slide: {e}")
    if hasattr(bpy.types.Scene, "show_general_settings"):
        try: del bpy.types.Scene.show_general_settings
        except Exception as e: print(f"Warning: Could not delete show_general_settings: {e}")
    if hasattr(bpy.types.Scene, "show_advanced_settings"):
        try: del bpy.types.Scene.show_advanced_settings
        except Exception as e: print(f"Warning: Could not delete show_advanced_settings: {e}")
    if hasattr(bpy.types.Scene, "show_auto_uv_settings"):
        try: del bpy.types.Scene.show_auto_uv_settings
        except Exception as e: print(f"Warning: Could not delete show_auto_uv_settings: {e}")
    if hasattr(bpy.types.Scene, "mof_properties"):
        try: del bpy.types.Scene.mof_properties
        except Exception as e: print(f"Warning: Could not delete mof_properties: {e}")
    if hasattr(bpy.types.Scene, "bake_explode_settings"):
        try: del bpy.types.Scene.bake_explode_settings
        except Exception as e: print(f"Warning: Could not delete bake_explode_settings: {e}")
    print("DEBUG: Unregistering classes...")
    for cls in reversed(classes):
        module = getattr(cls, '__module__', None)
        cls_name = getattr(cls, '__name__', None)
        # Check if the class is actually registered before trying to unregister
        # This avoids errors if registration failed initially
        if module and cls_name and hasattr(bpy.types, cls_name) and getattr(bpy.types, cls_name) == cls:
             try:
                 bpy.utils.unregister_class(cls)
             except RuntimeError: pass # Might already be unregistered if Blender is quitting
             except ValueError: pass # Might happen if it wasn't registered correctly
             except Exception as e: print(f"Warning: Could not unregister {cls_name}: {e}")
        # else: print(f"DEBUG: Skipping unregister for {cls_name} (not found or mismatch in bpy.types)")


    print("DEBUG: Unloading custom icons...")
    if shared_state.custom_icons:
        try:
            bpy.utils.previews.remove(shared_state.custom_icons)
            print("  - Custom icons preview collection removed.")
        except Exception as e:
            print(f"Warning: Could not remove custom icons preview collection: {e}")
    else:
        print("  - No custom icons collection to remove.")

    shared_state.custom_icons = None

    print("G-Ready 2.1 Addon Unregistered")
    print("--- End G-Ready 2.1 Unregister ---\n")


# --- END OF FILE __init__.py ---
