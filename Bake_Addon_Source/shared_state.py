# --- START OF FILE shared_state.py ---

# easy_surface/shared_state.py
"""
Module to hold shared state variables to avoid circular imports,
including the Boolean Cleanup draw handles and the custom icon collection.
"""
import bpy.utils.previews # <-- Added for icons

# Global list for Boolean Cleanup draw handles
cleanup_draw_handles = []

# Global lists for other modal viewport overlays
radial_draw_handles = []
angle_hud_draw_handles = []
offset_draw_handles = []
unbevel_draw_handles = []
trimsheet_draw_handles = []
trim_scale_draw_handles = []

# Transient Materials-to-TrimSheet layout preview data.  The cache contains
# draw-only primitive values and is never saved into Blender datablocks.
trim_layout_preview_cache = {}


def trim_layout_preview_context_key(context):
    window = getattr(context, "window", None)
    scene = getattr(context, "scene", None)
    view_layer = getattr(context, "view_layer", None)
    if scene is None or view_layer is None:
        return None
    return (
        window.as_pointer() if window else 0,
        scene.as_pointer(),
        view_layer.as_pointer(),
    )


# Global variable for custom icons
custom_icons: bpy.utils.previews.ImagePreviewCollection | None = None # <-- Added for icons

# --- END OF FILE shared_state.py ---
