"""Import only verified request-owned artifacts, at the transaction boundary."""

import tempfile
from pathlib import Path

from .schema import AuthoringError
from ..ai import generated_images


def placeholder(identifier, color_space, images):
    if identifier not in (images or {}):
        raise AuthoringError("Call list_generated_images and choose an image_id returned for this instruction")
    return {"$generated": identifier, "color_space": color_space}


def load(value, images):
    import bpy
    entry = (images or {}).get(value["$generated"])
    if entry is None:
        raise AuthoringError("Generated texture no longer belongs to this draft")
    data = generated_images.read(entry["state_directory"], entry)
    color_space = value["color_space"]
    identifier = value["$generated"]
    # Reuse only immutable originals; artists can paint over an imported image.
    for image in bpy.data.images:
        if (image.get("director_image_id") == identifier and not image.is_dirty and
                image.colorspace_settings.name == color_space and image.packed_file and
                bytes(image.packed_file.data) == data):
            return image
    with tempfile.TemporaryDirectory(prefix="painter-texture-") as directory:
        path = Path(directory) / "texture.png"
        path.write_bytes(data)
        image = bpy.data.images.load(str(path), check_existing=False)
        image.name = "Director " + identifier.split(":")[1][:12] + (" Color" if color_space == "sRGB" else " Data")
        image.colorspace_settings.name = color_space
        image.pack(data=data, data_len=len(data))
        # filepath_raw does not request a reload of the transient source file.
        image.filepath_raw = "//MaterialDirector/" + identifier.split(":")[1] + ".png"
    image.use_fake_user = True  # Retain the original for AI Undo after saving.
    image["director_image_id"] = identifier
    image["director_prompt"] = entry.get("prompt", "")
    return image
