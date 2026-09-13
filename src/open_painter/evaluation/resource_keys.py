"""Transient scene-scoped identities; persistent texture-set UUIDs stay unchanged."""


def key_for(scene, texture_set):
    return scene.as_pointer(), texture_set.id


def image_key(image):
    if not image:
        return None
    return (image.as_pointer(), tuple(image.size), image.get('open_painter_pixel_revision', 0),
            image.colorspace_settings.name)
