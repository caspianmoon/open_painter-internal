"""Revision-scoped image decoding shared by channels under the cache allowance.

Decoded arrays are immutable. Unmanaged/native edits are bounded by the authoring
serial (paint detection/explicit refresh advances it); managed writes also carry
their own image revision. Eviction never removes editable Blender images.

Decoded pixels are straight colour, like the shader's Image Texture node, and
finite: inf/NaN texels from other bakers' EXR/HDR files read as 1/0.
"""
import numpy as np
from ..core import image_manager
from ..utils.logging import logger
from .resource_keys import image_key
from . import metrics

_reported = set()


def _sanitise(value, image):
    if np.isfinite(value).all():
        return
    # Unused EXR background is often inf or NaN; the texture filters refuse
    # non-finite input (failing the whole set) and NaN survives clipping into
    # caches and exports. The shader samples the file as-is.
    np.nan_to_num(value, nan=0.0, posinf=1.0, neginf=0.0, copy=False)
    metrics.count('nonfinite_image_reads')
    key = image_key(image)
    if key not in _reported:
        _reported.add(key)
        logger.warning('%s: non-finite texels read as 0/1', image.name)


def associated(image):
    """True when ``Image.pixels`` holds premultiplied colour.

    Blender associates the float buffer of a file in a colour space (a 16-bit
    PNG/TIFF or EXR with Straight or Premultiplied alpha). Data colour spaces,
    channel-packed or ignored alpha, byte images and generated float images
    (the managed canvases) are read as stored; native float brush canvases are
    premultiplied by the brush and are the PAINT readers' concern.
    """
    return (image.is_float and image.alpha_mode in {'STRAIGHT', 'PREMUL'}
            and image.source in {'FILE', 'SEQUENCE', 'TILED'}
            and not image.colorspace_settings.is_data)


def stored_encoding(image):
    """The transfer the shader's Image Texture node undoes that a raw read keeps.

    'SRGB' for a byte image tagged sRGB (image.open's default for a PNG or JPG);
    '' otherwise. Float buffers are scene-linear once loaded, so only byte
    buffers keep the file's curve. A raw read (a Normal Combine detail map is
    data) returns the stored values; the shader samples the same image through
    the node's decode and re-encodes with this transfer, so neither evaluator
    has to retag a datablock another layer may read as colour.
    """
    return 'SRGB' if not image.is_float and image.colorspace_settings.name == 'sRGB' else ''


def _decode(value, image, linear):
    _sanitise(value, image)
    if associated(image):
        alpha = value[..., 3:4]
        np.divide(value[..., :3], alpha, out=value[..., :3], where=alpha > 1e-8)
        value[..., :3][value[..., 3] <= 1e-8] = 0
    if linear and stored_encoding(image) == 'SRGB':
        rgb = value[..., :3]
        value[..., :3] = np.where(rgb <= .04045, rgb / 12.92,
                                 ((rgb + .055) / 1.055) ** 2.4)
    return value


class ImageSnapshots(dict):
    def __init__(self, resources, serial, initial=None):
        super().__init__(initial or {})
        self.resources = resources
        self.serial = serial

    def read(self, image, linear=False):
        key = ('decoded_image', self.serial, image_key(image), linear)
        if key in self.resources:
            metrics.count('decoded_image_reuses')
            return self.resources[key]
        # A dirty-region detector may already own a fresh native canvas read.
        value = self.get(image.as_pointer())
        value = image_manager.pixels(image) if value is None else value.copy()
        value = _decode(value, image, linear)
        value.flags.writeable = False
        self.resources[key] = value
        metrics.count('decoded_image_bytes', value.nbytes)
        return value


def read(cache, image, linear=False):
    if isinstance(cache, ImageSnapshots):
        return cache.read(image, linear)
    return _decode(image_manager.pixels(image), image, linear)
