"""Managed image creation, linear pixel access and packing."""

import bpy
import numpy as np

from ..constants import OWNER


def create(name: str, resolution: int, color=(0, 0, 0, 0), color_data=False):
    image = bpy.data.images.new(
        name, width=resolution, height=resolution, alpha=True, float_buffer=True
    )
    image[OWNER] = True
    image.colorspace_settings.name = "Linear Rec.709" if color_data else "Non-Color"
    image.generated_color = color
    image.use_fake_user = True
    return image


def pixels(image, out=None) -> np.ndarray:
    if not image or not image.size[0] or not len(image.pixels):
        raise ValueError("Texture image is missing or has no pixel data. Reload it first.")
    shape = (image.size[1], image.size[0], 4)
    result = out.reshape(-1) if out is not None and out.shape == shape else np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
    image.pixels.foreach_get(result)
    return result.reshape(image.size[1], image.size[0], 4)


def write(image, values: np.ndarray, pack=True):
    image.pixels.foreach_set(np.ascontiguousarray(values, dtype=np.float32).ravel())
    image.update()
    if pack:
        image.pack()


def output(ts, channel):
    prop = next((o for o in ts.outputs if o.channel == channel), None)
    if prop is None:
        prop = ts.outputs.add()
        prop.channel = channel
    n = int(ts.resolution)
    if not prop.image or not prop.image.get(OWNER):
        from ..constants import CHANNEL_LABELS, DEFAULTS

        prop.image = create(
            f"{ts.name}_{CHANNEL_LABELS[channel]}", n, DEFAULTS[channel], channel == "BASE_COLOR"
        )
        prop.image["texture_set_id"] = ts.id
        prop.image["channel"] = channel
        prop.dirty = True
    if tuple(prop.image.size) != (n, n):
        prop.image.scale(n, n)
        prop.dirty = True
    return prop
