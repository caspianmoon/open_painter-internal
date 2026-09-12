"""Import grayscale brush masks without changing existing user image settings."""

from pathlib import Path

import bpy

from ..constants import OWNER
from .brush_bridge import ensure_brush


def import_alpha(context, filepath: str):
    path = Path(filepath).expanduser()
    if not path.is_file():
        raise ValueError("Choose an existing alpha image.")
    brush = ensure_brush(context)
    image = bpy.data.images.load(str(path), check_existing=False)
    texture = None
    try:
        if not image.size[0]:
            raise ValueError("The alpha image could not be read.")
        image[OWNER] = True
        image.colorspace_settings.name = "Non-Color"
        image.pack()
        image.use_fake_user = True
        texture = bpy.data.textures.new(path.stem + " Alpha", type="IMAGE")
        texture[OWNER] = True
        texture.image = image
        texture.use_fake_user = True
        brush.mask_texture = texture
        brush.mask_texture_slot.mask_map_mode = "VIEW_PLANE"
        return texture
    except Exception:
        if texture:
            bpy.data.textures.remove(texture)
        bpy.data.images.remove(image)
        raise
