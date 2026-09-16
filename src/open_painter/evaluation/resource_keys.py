"""Transient scene-scoped identities; persistent texture-set UUIDs stay unchanged."""
from contextlib import contextmanager

_stable = 0


def key_for(scene, texture_set):
    return scene.as_pointer(), texture_set.id


@contextmanager
def stable_keys():
    """Identities built inside name datablocks instead of addressing them.

    Runtime resource keys use addresses (fast, unique within a session). A
    persisted provenance — a layer cache's (0.9.0) — must still compare equal
    after the file is reopened, so it is built with names.
    """
    global _stable
    _stable += 1
    try:
        yield
    finally:
        _stable -= 1


def stable():
    return _stable > 0


def datablock_key(value):
    if not value:
        return None
    return getattr(value, 'name', None) if _stable else value.as_pointer()


def image_key(image):
    if not image:
        return None
    return (datablock_key(image), tuple(image.size), image.get('open_painter_pixel_revision', 0),
            image.colorspace_settings.name)
