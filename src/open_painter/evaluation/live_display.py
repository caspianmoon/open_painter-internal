"""Disposable full-precision GPU display images; authored pixels stay separate.

GPU composition writes to Blender's shared image texture, including its mip
chain. No Image.update(), pixel transfer, or packing belongs in this module.
Save/export/render callers must restore these bindings and flush CPU outputs.
"""

import bpy

from ..constants import DEFAULTS, OWNER
from .resource_keys import key_for

_MARKER = 'open_painter_live_gpu_display'
_entries = {}


def exclusive_material(scene, ts):
    """A shared material cannot show different scene-scoped graph revisions."""
    if not ts.material or not ts.material.get(OWNER):
        return False
    owner = key_for(scene, ts)
    return not any(
        other.material == ts.material and key_for(candidate, other) != owner
        for candidate in bpy.data.scenes if hasattr(candidate, 'open_painter')
        for other in candidate.open_painter.texture_sets
    )


def _output(ts, channel):
    return next((output.image for output in ts.outputs if output.channel == channel), None)


def _alive(image):
    try:
        return image is not None and image.as_pointer() != 0
    except ReferenceError:
        return False


def _images(entry):
    return tuple(image for image in (entry.get('published'), entry.get('pending'))
                 if _alive(image))


def _slots():
    # Retain hidden Image Editor spaces as well: switching editor types should
    # never reveal a removed disposable image after save or extension disable.
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == 'IMAGE_EDITOR':
                    yield area, space


def _canvases():
    return {scene.tool_settings.image_paint.canvas.as_pointer()
            for scene in bpy.data.scenes if scene.tool_settings.image_paint.canvas}


def _bind_nodes(ts, channel, image, only=None):
    """Show ``image`` for ``channel`` in the material's Texture Set node (its group's sampler)."""
    if not ts.material:
        return False
    from ..core.texture_set_node import channel_nodes
    node = channel_nodes(ts).get(channel)
    if node is None or node.image == image or (only is not None and node.image not in only):
        return False
    node.image = image
    return True


def _remove(image):
    if _alive(image) and image.get(_MARKER):
        bpy.data.images.remove(image)


def target(scene, ts, channel, n):
    """Return a GPU write target; a resized target is unpublished until ready."""
    import gpu
    if bpy.app.background or bpy.context.window is None:
        raise RuntimeError('Live GPU display requires a Blender window context')
    if not exclusive_material(scene, ts):
        raise ValueError('Live GPU display requires a material owned by one texture set and scene')
    from .preview_policy import resolution
    if channel not in DEFAULTS or n != resolution(ts):
        raise ValueError('Live GPU display must preserve the selected preview resolution')
    key = (*key_for(scene, ts), channel)
    entry = _entries.setdefault(key, {})
    for role in ('pending', 'published'):
        image = entry.get(role)
        if _alive(image) and tuple(image.size) == (n, n):
            return image, gpu.texture.from_image(image)
    old_pending = entry.pop('pending', None)
    _remove(old_pending)
    image = bpy.data.images.new(f'OP Live · {ts.name} · {channel}', width=n, height=n,
                                alpha=True, float_buffer=True)
    image[OWNER] = True
    image[_MARKER] = True
    image['open_painter_disposable_preview'] = True
    image['texture_set_id'] = ts.id
    image['channel'] = channel
    image.use_fake_user = False
    image.use_half_precision = False
    image.colorspace_settings.name = 'Non-Color'
    image.generated_color = DEFAULTS[channel]
    try:
        texture = gpu.texture.from_image(image)
        if texture.format != 'RGBA32F':
            raise RuntimeError(f'Live GPU display requires RGBA32F; received {texture.format}')
    except Exception:
        _remove(image)
        raise
    entry['pending'] = image
    return image, texture


def publish(scene, ts, channel, image):
    """Publish a completed GPU raster without touching its stale CPU pixels."""
    key = (*key_for(scene, ts), channel)
    entry = _entries.get(key)
    if (not entry or not _alive(image) or image not in _images(entry)
            or not exclusive_material(scene, ts)):
        raise ValueError('The live display target is no longer owned by this texture set')
    old = entry.get('published')
    eligible = {_output(ts, channel), old}
    eligible.discard(None)
    canvases = _canvases()
    _bind_nodes(ts, channel, image)
    for area, space in _slots():
        if (space.mode == 'VIEW' and space.image in eligible
                and space.image.as_pointer() not in canvases):
            if space.image != image:
                space.image = image
            area.tag_redraw()
    entry['published'] = image
    if entry.get('pending') == image:
        entry.pop('pending')
    if old != image:
        _remove(old)
    # EEVEE retains temporal history when only a GPU texture changes. Material
    # tagging invalidates that history; image.update() would overwrite our GPU
    # result using its deliberately untouched CPU buffer.
    ts.material.update_tag()
    for window in bpy.context.window_manager.windows:
        if window.scene == scene:
            for area in window.screen.areas:
                if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                    area.tag_redraw()
    return image


def rebind(scene, ts):
    """Reattach completed images after fixed display material repair."""
    if not exclusive_material(scene, ts):
        return
    owner = key_for(scene, ts)
    for key, entry in _entries.items():
        image = entry.get('published')
        if key[:2] == owner and _alive(image):
            _bind_nodes(ts, key[2], image)


def displayed_image(scene, ts, channel):
    entry = _entries.get((*key_for(scene, ts), channel), {})
    image = entry.get('published')
    return image if _alive(image) else _output(ts, channel)


def _restore_entry(key, entry, ts=None):
    images = _images(entry)
    authoritative = _output(ts, key[2]) if ts is not None else None
    if ts is not None:
        _bind_nodes(ts, key[2], authoritative, only=images)
    for area, space in _slots():
        if space.image in images:
            space.image = authoritative
            area.tag_redraw()
    for image in images:
        _remove(image)


def restore(scene, ts):
    """Restore authoritative nodes/editor images and release this set's proxies."""
    owner = key_for(scene, ts)
    for key in list(_entries):
        if key[:2] == owner:
            _restore_entry(key, _entries.pop(key), ts)


def _owners():
    return {key_for(scene, ts): ts for scene in bpy.data.scenes
            if hasattr(scene, 'open_painter') for ts in scene.open_painter.texture_sets}


def prune(live):
    """Release deleted scene/set resources without changing unrelated images."""
    owners = _owners()
    for key in list(_entries):
        if key[:2] not in live:
            _restore_entry(key, _entries.pop(key), owners.get(key[:2]))


def clear():
    """Restore all authoritative bindings before save, load, or unregister."""
    owners = _owners()
    for key in list(_entries):
        _restore_entry(key, _entries.pop(key), owners.get(key[:2]))
    # A file loaded after an interrupted session may contain an orphaned proxy.
    # Recover by its channel/set metadata only when a unique owner is available.
    for image in list(bpy.data.images):
        if image.get(_MARKER):
            matches = [ts for ts in owners.values() if ts.id == image.get('texture_set_id')]
            ts = matches[0] if len(matches) == 1 else None
            _restore_entry((0, image.get('texture_set_id'), image.get('channel')), {'published': image}, ts)
