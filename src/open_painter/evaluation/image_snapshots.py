"""Revision-scoped image decoding shared by channels under the cache allowance.

Decoded arrays are immutable. Unmanaged/native edits are bounded by the authoring
serial (paint detection/explicit refresh advances it); managed writes also carry
their own image revision. Eviction never removes editable Blender images.
"""
import numpy as np
from ..core import image_manager
from .resource_keys import image_key
from . import metrics


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
        if linear and image.colorspace_settings.name == 'sRGB':
            rgb = value[..., :3]
            value[..., :3] = np.where(rgb <= .04045, rgb / 12.92,
                                     ((rgb + .055) / 1.055) ** 2.4)
        value.flags.writeable = False
        self.resources[key] = value
        metrics.count('decoded_image_bytes', value.nbytes)
        return value


def read(cache, image, linear=False):
    if isinstance(cache, ImageSnapshots):
        return cache.read(image, linear)
    value = image_manager.pixels(image)
    if linear and image.colorspace_settings.name == 'sRGB':
        rgb = value[..., :3]
        value[..., :3] = np.where(rgb <= .04045, rgb / 12.92,
                                 ((rgb + .055) / 1.055) ** 2.4)
    return value
