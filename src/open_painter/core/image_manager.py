"""Managed image creation, linear pixel access and packing."""

from ..evaluation.metrics import measured, count

import bpy
import numpy as np

from ..constants import OWNER


def create(name: str, resolution: int, color=(0, 0, 0, 0), color_data=False):
    image = bpy.data.images.new(
        name, width=resolution, height=resolution, alpha=True, float_buffer=True
    )
    image[OWNER] = True
    image.colorspace_settings.name = "Linear Rec.709" if color_data else "Non-Color"
    adopt_alpha_mode(image)
    image.generated_color = color
    image.use_fake_user = True
    return image


def adopt_alpha_mode(image):
    """Managed float images hold data: paint canvases are premultiplied, the rest
    is read as stored. Channel-packed alpha keeps Blender from unpremultiplying
    them on GPU upload once packed (the shader divides once itself) and from
    premultiplying them again when a saved file is reopened."""
    if image.is_float and image.alpha_mode != 'CHANNEL_PACKED':
        image.alpha_mode = 'CHANNEL_PACKED'


def live_bake_pointers(ts):
    """as_pointer() set of the set's current baked-map images; empty when unreadable."""
    try:
        entries = list(ts.baked_maps)
    except (ReferenceError, AttributeError):
        return set()
    pointers = set()
    for entry in entries:
        try:
            if entry.image:
                pointers.add(entry.image.as_pointer())
        except ReferenceError:
            continue
    return pointers


@measured('image_read')
def pixels(image, out=None) -> np.ndarray:
    if not image or not image.size[0] or not len(image.pixels):
        raise ValueError("Texture image is missing or has no pixel data. Reload it first.")
    shape = (image.size[1], image.size[0], 4)
    result = out.reshape(-1) if out is not None and out.shape == shape else np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
    count("image_read_bytes", result.nbytes)
    if out is None or out.shape != shape:
        count("image_read_allocated_bytes", result.nbytes)
    image.pixels.foreach_get(result)
    return result.reshape(image.size[1], image.size[0], 4)


@measured('image_write')
def write(image, values: np.ndarray, pack=True):
    count("image_write_bytes", values.size * 4)
    image.pixels.foreach_set(np.ascontiguousarray(values, dtype=np.float32).ravel())
    image.update()
    image["open_painter_pixel_revision"] = image.get("open_painter_pixel_revision", 0) + 1
    from ..evaluation.dependencies import image_written
    image_written(image)
    if pack:
        pack_image(image)


def output(ts, channel):
    prop = next((o for o in ts.outputs if o.channel == channel), None)
    if prop is None:
        prop = ts.outputs.add()
        prop.channel = channel
    n = int(ts.resolution)
    if not prop.image or not prop.image.get(OWNER):
        from ..constants import CHANNEL_LABELS, DEFAULTS

        prop.image = create(
            f"{ts.name}_{CHANNEL_LABELS[channel]}", n, DEFAULTS[channel], channel in {"BASE_COLOR", "EMISSION"}
        )
        prop.image["texture_set_id"] = ts.id
        prop.image["channel"] = channel
        prop.dirty = True
    if tuple(prop.image.size) != (n, n):
        prop.image.scale(n, n)
        prop.dirty = True
    return prop


def clone(image):
    """A copy holding the current pixels.

    ``Image.copy()`` duplicates the datablock and its packed bytes but not the
    pixel buffer: a painted canvas that was not packed since its last stroke
    copies as its last save (or blank, when never saved). Paint canvases are
    packed by the save handler, not per stroke, so flush first.
    """
    if image.is_dirty or not (image.packed_file or image.filepath):
        pack_image(image)
    return image.copy()


@measured("packing")
def pack_image(image):
    # On the supported Blender build, packing clears is_dirty and native pixel
    # edits set it again. Require that flag as well as the managed revision.
    identity = repr((image.get('open_painter_pixel_revision', 0), tuple(image.size),
                     image.colorspace_settings.name, image.filepath))
    if image.packed_file and not image.is_dirty and image.get('open_painter_packed_identity') == identity:
        count('pack_unchanged_skips')
        return
    count("pack_calls")
    image.pack()
    image['open_painter_packed_identity'] = identity
