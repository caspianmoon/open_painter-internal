import logging

import bpy

from .utils.logging import logger


def update_debug(self, context):
    logger.setLevel(logging.DEBUG if self.developer_mode else logging.INFO)


class OP_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    developer_mode: bpy.props.BoolProperty(name="Developer Mode", update=update_debug)
    library_directory: bpy.props.StringProperty(name="Smart Material Library", subtype="DIR_PATH")

    def draw(self, context):
        self.layout.prop(self, "developer_mode")
        self.layout.prop(self, "library_directory")
