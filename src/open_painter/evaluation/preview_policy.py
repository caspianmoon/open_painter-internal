"""Fair cooperative scheduling; full-resolution output stays authoritative."""

from .resource_keys import key_for

_proxies = {}
_resources = {}


class SlicePolicy:
    """Adapt rows of work between yields while retaining canonical cache strips."""
    def __init__(self,budget=.004):
        self.budget=budget; self.elapsed=0.; self.operations=0; self.first=True

    def ready(self,elapsed):
        from . import metrics
        metrics.sample('indivisible_compute_s',elapsed)
        self.elapsed+=elapsed; self.operations+=1
        if self.first or self.elapsed>=self.budget or self.operations>=64:
            metrics.sample('cooperative_compute_s',self.elapsed)
            self.first=False; self.elapsed=0.; self.operations=0
            return True
        return False


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
    serial = getattr(ts, 'edit_serial', 0)
    if getattr(cache, 'serial_stamp', None) != serial:
        # Memoized anchor/stack lookups are keyed by serial: entries of earlier
        # serials are unreachable yet pin whole layer snapshots until dropped.
        cache.signatures = {}
        cache.serial_stamp = serial
    return cache


def current(scene, ts, channel):
    entry = _proxies.get((*key_for(scene, ts), channel))
    return entry is not None and entry[0] == ts.edit_serial and entry[1] == resolution(ts)


def publish(scene, ts, channel, size, serial, pixels):
    from ..core import image_manager
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
    from ..core.texture_set_node import show_image
    show_image(ts, channel, image)   # the material's Texture Set node samples it instead of the set's own
    return image


def prune(live):
    """Release proxies and analysis caches of removed texture sets/scenes."""
    import bpy
    for key in set(_resources)-live:
        _resources.pop(key).clear()
    for key in list(_proxies):
        if key[:2] not in live:
            image=_proxies.pop(key)[2]
            try:
                bpy.data.images.remove(image)
            except ReferenceError:
                pass


def clear():
    """Restore authoritative bindings before save/render/undo/unregister."""
    import bpy
    from ..core import material_manager
    for scene in bpy.data.scenes:
        if hasattr(scene, 'open_painter'):
            for ts in scene.open_painter.texture_sets:
                # Cleanup only adapters that actually reference a CPU preview.
                # Native authoring graphs must survive disable/load without
                # rebuilding recipes (which may intentionally lack mesh maps).
                from ..core.texture_set_node import channel_nodes
                if ts.material and any(node.image and node.image.get('open_painter_disposable_preview')
                                       for node in channel_nodes(ts).values()):
                    material_manager.repair(ts, image_overrides={})
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
