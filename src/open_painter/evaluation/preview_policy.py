"""Fair cooperative scheduling; full-resolution output stays authoritative."""

from .resource_keys import key_for

_proxies = {}
_resources = {}


def resolution(ts):
    mode = getattr(ts, 'preview_quality', 'FULL')
    return min(int(ts.resolution), int(mode)) if mode != 'FULL' else int(ts.resolution)


def resources(scene, ts, size):
    from .raster_cache import RasterCache
    from .dependencies import geometry_key
    key = key_for(scene, ts)
    cache = _resources.setdefault(key, RasterCache())
    stamp = (geometry_key(scene, ts), size)
    if getattr(cache, 'geometry_stamp', None) != stamp:
        cache.clear()
        cache.geometry_stamp = stamp
    return cache


def current(scene, ts, channel):
    entry = _proxies.get((*key_for(scene, ts), channel))
    return entry is not None and entry[0] == ts.edit_serial and entry[1] == resolution(ts)


def publish(scene, ts, channel, size, serial, pixels):
    from ..core import image_manager
    from ..constants import OWNER
    if serial != ts.edit_serial:
        return None
    key = (*key_for(scene, ts), channel)
    previous = _proxies.get(key)
    image = previous[2] if previous and previous[1] == size else None
    if image is None:
        import bpy
        if previous:
            bpy.data.images.remove(previous[2])
        image = image_manager.create(f'OP Preview · {ts.name} · {channel}', size,
                                     color_data=channel in {'BASE_COLOR', 'EMISSION'})
        image.use_fake_user = False
        image['open_painter_disposable_preview'] = True
    image_manager.write(image, pixels, pack=False)
    _proxies[key] = (serial, size, image)
    if ts.material and ts.material.use_nodes:
        for node in ts.material.node_tree.nodes:
            if node.get(OWNER) and node.get('role') == channel and node.bl_idname == 'ShaderNodeTexImage':
                node.image = image
    return image


def clear():
    """Restore authoritative bindings before save/render/undo/unregister."""
    import bpy
    from ..core import material_manager
    for scene in bpy.data.scenes:
        if hasattr(scene, 'open_painter'):
            for ts in scene.open_painter.texture_sets:
                if ts.material:
                    material_manager.repair(ts)
    _proxies.clear()
    for cache in _resources.values():
        cache.clear()
    _resources.clear()
    for image in list(bpy.data.images):
        if image.get('open_painter_disposable_preview'):
            bpy.data.images.remove(image)


def fair_order(items, previous, identity, preferred=None):
    if not items:
        return []
    keys = [identity(item) for item in items]
    if previous in keys:
        start = (keys.index(previous) + 1) % len(items)
    elif preferred in keys:
        start = keys.index(preferred)
    else:
        start = 0
    return items[start:] + items[:start]
