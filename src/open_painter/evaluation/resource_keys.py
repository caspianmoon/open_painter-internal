"""Transient scene-scoped identities; persistent texture-set UUIDs stay unchanged."""
from contextlib import contextmanager

from ..utils.ids import new_id

_stable = 0

UID = 'open_painter_uid'   # a token minted once per image and saved with it
_minted = {}    # session_uid -> uid handed out before an undo step could persist it
_holders = {}   # uid -> the image last seen carrying it (a copy inherits the property)


def key_for(scene, texture_set):
    return scene.as_pointer(), texture_set.id


@contextmanager
def stable_keys():
    """Identities built inside name datablocks instead of tokenising them.

    Runtime keys name datablocks too, but an image's token outlives a rename.
    A persisted provenance — a layer cache's (0.9.0) — must still compare
    equal after the file is reopened, so it is built with names only.
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
    """Never an address: a datablock removed and replaced by one allocated at
    the same address would keep serving the raster built from the old one, and
    the keys persisted on boundary nodes would never match after a reload.
    Names are unique within a session; a namesake replacement is told apart by
    the content stamps that accompany every geometry key."""
    if not value:
        return None
    if _stable:
        return getattr(value, 'name', None)
    return getattr(value, 'name_full', None) or value.as_pointer()


def _claimed(uid, image):
    """True when an older live image carries this token: ``image`` is the copy."""
    held = _holders.get(uid)
    if held is None:
        return False
    try:
        other = held.session_uid
    except ReferenceError:
        return False   # the previous holder was removed or the file reloaded
    return other < image.session_uid


def image_uid(image):
    """The image's persistent token, minted on first sight.

    Minted here rather than in the pointer callbacks so every read path (baked
    maps, masks, influence images) gets one before its raster is requested. The
    session remembers what it handed out: an undo to a step older than the mint
    drops the property, and the same token comes back rather than a fresh one
    re-identifying every boundary. A copy carries the property with it; the
    later-created of two live images sharing a token is re-minted.
    """
    uid = image.get(UID)
    if uid and _claimed(uid, image):
        if _minted.get(image.session_uid) == uid:
            del _minted[image.session_uid]
        uid = None
    if not uid:
        uid = _minted.get(image.session_uid) or new_id()
    _minted[image.session_uid] = uid
    _holders[uid] = image
    if image.get(UID) != uid:
        try:
            image[UID] = uid
        except (AttributeError, RuntimeError, TypeError):
            pass   # a draw callback or linked data: the session's memory stands in
    return uid


def image_key(image):
    if not image:
        return None
    key = datablock_key(image) if _stable else image_uid(image)
    return (key, tuple(image.size), image.get('open_painter_pixel_revision', 0),
            image.colorspace_settings.name, image.alpha_mode)
