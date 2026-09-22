import logging

import bpy

from .utils.logging import logger


def update_debug(self, context):
    logger.setLevel(logging.DEBUG if self.developer_mode else logging.INFO)


def update_cache(self, context):
    from .evaluation.memory import shared
    shared.configure(self.texture_cache_mb * 1024 * 1024)


class OP_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    developer_mode: bpy.props.BoolProperty(name="Developer Mode", update=update_debug)
    library_directory: bpy.props.StringProperty(name="Smart Material Library", subtype="DIR_PATH")
    director_codex_binary: bpy.props.StringProperty(name="Custom Runtime (Optional)", subtype="FILE_PATH",
        description="Advanced override for a supported Codex executable; leave empty for automatic setup")
    director_credentials: bpy.props.EnumProperty(name="Account Storage", default="keyring", items=[
        ("keyring", "OS Keyring", "Keep Codex credentials in the operating system credential store"),
        ("ephemeral", "This Connection Only", "Sign in again after disconnecting; keep credentials in memory")])
    director_share_context: bpy.props.BoolProperty(name="Share Material Context with Codex",
        description="Allow selected material/mesh summaries, enabled model/map previews and a chosen reference image to be sent through your Codex account")
    dense_layer_rows: bpy.props.BoolProperty(
        name="Blend and Opacity in Layer Rows", default=False,
        description="Show each layer row's blend menu and opacity field (dense rows); off keeps the "
                    "row for the name and edits the selected layer's blend above the list")
    texture_cache_mb: bpy.props.IntProperty(
        name="Texture Cache (MiB)", default=256, min=0, max=8192,
        description="Shared disposable CPU cache across all texture sets; excludes editable images and working memory",
        update=update_cache)
    live_preview_budget_mb: bpy.props.IntProperty(
        name="Live Preview Resources (MiB)", default=4096, min=256, max=32768,
        description="Preflight allowance for extra live display buffers, GPU inputs and composition scratch; excludes authored images and CPU evaluation")

    def draw(self, context):
        self.layout.prop(self, "developer_mode")
        self.layout.prop(self, "library_directory")
        self.layout.prop(self, "dense_layer_rows")
        self.layout.prop(self, "texture_cache_mb")
        self.layout.prop(self, "live_preview_budget_mb")
        box = self.layout.box()
        box.label(text="Material Director")
        from .ui.material_director import connection_settings
        connection_settings(box, self)
        from .evaluation.memory import shared
        self.layout.label(text=f"Cached CPU data: {shared.used / 1048576:.1f} MiB")
