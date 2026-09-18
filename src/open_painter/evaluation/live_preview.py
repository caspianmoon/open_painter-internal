"""Live texture composition, independent of authoritative CPU output images.

The persistent layer stack is the recipe. GPU leaves are disposable, keyed by
their actual inputs; changing composition parameters does not bake that recipe.
Explicit evaluation, save, render and export still flush the CPU reference.
All Blender/GPU access stays on the main thread in a window context.
"""
from collections import OrderedDict

import bpy

from . import metrics
from .resource_keys import key_for

_device = None
_window = None
_sessions = {}
_fallbacks = {}
_fallback_serials = {}


class TextureCache:
    """Bound disposable GPU leaves. Live commands may hold uncached inputs.

    This allowance is separate from CPU memoization and mandatory display and
    working textures. Eviction only drops a reference, never editable pixels.
    """
    def __init__(self, allowance=256 * 1024 * 1024):
        self.allowance = allowance
        self.entries = OrderedDict()
        self.used = 0

    def get(self, key):
        entry = self.entries.get(key)
        metrics.count('live_leaf_hits' if entry else 'live_leaf_misses')
        if entry is None:
            return None
        self.entries.move_to_end(key)
        return entry[0]

    def put(self, key, texture, size):
        if key in self.entries:
            self.used -= self.entries.pop(key)[1]
        if size > self.allowance:
            metrics.count('live_leaf_oversize_skips')
            return
        while self.entries and self.used + size > self.allowance:
            _, (_, old_size) = self.entries.popitem(last=False)
            self.used -= old_size
            metrics.count('live_leaf_evictions')
        self.entries[key] = texture, size
        self.used += size
        metrics.gauge('live_leaf_cache_bytes', self.used)

    def clear(self):
        self.entries.clear()
        self.used = 0
        metrics.gauge('live_leaf_cache_bytes', 0)


_leaves = TextureCache()


def requested(ts):
    return getattr(ts, 'preview_backend', 'LIVE') == 'LIVE'


def available(scene, ts):
    key = key_for(scene, ts)
    if key in _fallbacks and _fallback_serials.get(key) != ts.edit_serial:
        _fallbacks.pop(key, None)
        _fallback_serials.pop(key, None)
    if not requested(ts) or bpy.app.background or bpy.context.window is None:
        return False
    # A duplicated scene may share a material datablock but have different recipes.
    # It cannot display two different images in that one material simultaneously.
    if ts.material and any(other.material == ts.material for owner in bpy.data.scenes
                           if hasattr(owner, 'open_painter')
                           for other in owner.open_painter.texture_sets
                           if key_for(owner, other) != key_for(scene, ts)):
        _fallbacks[key] = 'Material is shared by multiple texture sets'
        _fallback_serials[key] = ts.edit_serial
        return False
    return key not in _fallbacks


def current(scene, ts):
    state = _sessions.get(key_for(scene, ts))
    return state is not None and state['serial'] == ts.edit_serial


def has_session(scene, ts):
    return key_for(scene, ts) in _sessions


def native_paint_input(scene):
    """Notice an active native stroke without downloading its entire canvas.

    For direct float paint inputs Blender already updates the shared GPU image.
    A stroke marks the recipe dirty conservatively; the final native scan/save
    still reads the current authored pixels. Complex mask/filter targets retain
    the existing detector because their CPU leaves need actual pixels.
    """
    project = scene.open_painter
    if project.paint_mask_id or project.paint_mask or not project.paint_image:
        return False
    ts = next((item for item in project.texture_sets if item.id == project.paint_set_id), None)
    if ts is None or not available(scene, ts):
        return False
    layer = next((item for item in ts.layers if item.id == project.paint_layer_id), None)
    if layer is None or layer.type != 'PAINT':
        return False
    from ..core.layer_stack import channel_for
    channel = project.paint_target_channel or project.paint_channel
    prop = channel_for(layer, channel)
    image = project.paint_image
    if (prop is None or prop.image != image or not image.is_float
            or image.colorspace_settings.name not in {'Non-Color', 'Linear Rec.709'}
            or tuple(image.size) != (int(ts.resolution), int(ts.resolution))
            or any(effect.enabled and effect.strength > 0 for effect in prop.effects)):
        return False
    from ..painting.channel_paint import mark_changed
    image['open_painter_pixel_revision'] = image.get('open_painter_pixel_revision', 0) + 1
    mark_changed(ts, layer, channel)
    metrics.count('live_native_paint_scan_skips')
    return True


def status(scene, ts):
    reason = _fallbacks.get(key_for(scene, ts))
    if reason:
        return 'CPU preview: ' + reason
    if current(scene, ts):
        return 'Live GPU · final textures update on save/export'
    return 'Preparing live textures…' if requested(ts) else 'CPU texture preview'


def device():
    global _device, _window
    pointer = bpy.context.window.as_pointer() if bpy.context.window else None
    if pointer is None or bpy.app.background:
        raise RuntimeError('Live composition needs a Blender window GPU context')
    if _device is not None and pointer != _window:
        clear()
    if _device is None:
        from .live_gpu import Device
        _device = Device()
        _window = pointer
    return _device


def resource_estimate(scene, ts, n):
    """Conservative preflight for additional live resources, not total RSS.

    A shared Image still owns a CPU buffer beside GPU pixels and mipmaps. Count
    those explicitly before allocating 8 full-size displays. Authored images,
    CPU source workspaces and Blender/driver allocations remain separate.
    """
    from ..constants import CHANNELS
    from ..core.layer_stack import channel_for
    from . import live_display
    owner = key_for(scene, ts)
    display = 0
    seen = set()
    for key, entry in live_display._entries.items():
        if key[:2] != owner:
            for image in live_display._images(entry):
                if image.as_pointer() not in seen:
                    seen.add(image.as_pointer())
                    display += image.size[0] * image.size[1] * 16 * (1 + 4/3)
    display += n * n * 16 * len(CHANNELS) * (1 + 4/3)
    parents = {layer.id: layer.parent_id for layer in ts.layers}
    depth = 0
    for layer in ts.layers:
        ancestry = set()
        parent = layer.parent_id
        while parent and parent not in ancestry:
            ancestry.add(parent)
            parent = parents.get(parent, '')
        depth = max(depth, len(ancestry))
    inputs = 0
    for channel in CHANNELS:
        components = 0
        for layer in ts.layers:
            prop = channel_for(layer, channel)
            if not layer.visible or (prop and not prop.enabled):
                continue
            if any(mask.enabled for mask in layer.masks):
                components += 1
            if layer.type == 'FOLDER' or prop is None:
                continue
            effects = any(effect.enabled and effect.strength > 0 for effect in prop.effects)
            field = prop.procedural or getattr(prop, 'detail_image', None)
            if prop.image or effects or (layer.type == 'FILL' and field):
                simple = (layer.type == 'FILL' and field and not prop.image
                          and not effects and channel != 'NORMAL' and prop.tiling == 'REPEAT')
                components += 1 if simple else 4
        inputs = max(inputs, n * n * components * 4)
    scratch = n * n * 16 * (depth + 3 + 1/3)
    # Completed first-folder results have a separate, globally bounded cache.
    checkpoints = _device._checkpoint_limit if _device is not None else 256 * 1048576
    return int(display + scratch + _leaves.allowance + checkpoints + inputs)


def check_resources(scene, ts, n):
    addon = bpy.context.preferences.addons.get(__package__.rsplit('.', 1)[0])
    budget = (addon.preferences.live_preview_budget_mb if addon else 4096) * 1048576
    estimated = resource_estimate(scene, ts, n)
    metrics.gauge('live_resource_estimate_bytes', estimated)
    if estimated > budget:
        raise RuntimeError(f'Live preview needs approximately {estimated // 1048576} MiB; '
                           f'budget is {budget // 1048576} MiB. Select a smaller Preview '
                           'or increase Live Preview Resources in preferences')


def steps(scene, ts):
    """Cooperatively prepare changed leaves, then submit texture-only GPU passes.

    Output dirty flags intentionally remain set: a live display is not a saved
    texture. No inactivity timer triggers a full CPU bake behind the user's back.
    """
    from ..constants import CHANNELS
    from ..core import material_manager
    from ..core.validation import hierarchy_issues
    from . import live_display
    from .live_inputs import Builder

    issues = hierarchy_issues(ts)
    if issues:
        raise ValueError(' | '.join(issues))
    from .preview_policy import resolution
    check_resources(scene, ts, resolution(ts))
    renderer = device()
    key = key_for(scene, ts)
    serial = ts.edit_serial
    builder = Builder(scene, ts, _leaves, renderer)
    state = _sessions.setdefault(key, dict(serial=-1, signatures={}))
    # Keep image-node bindings stable across edits. Repairing to authoritative
    # images and then rebinding proxies would invalidate EEVEE twice per edit.
    material_manager.repair(ts, image_overrides={
        channel: live_display.displayed_image(scene, ts, channel) for channel in CHANNELS})
    channels = sorted(CHANNELS, key=lambda channel: channel != scene.open_painter.display_channel)
    try:
        for channel in channels:
            signature = builder.signature(channel)
            if serial != ts.edit_serial:
                return False
            if state['signatures'].get(channel) == signature:
                metrics.count('live_channel_reuses')
            else:
                layers, resolved_signature = yield from builder.channel(channel)
                if serial != ts.edit_serial:
                    return False
                if resolved_signature != signature:
                    raise RuntimeError('Live texture dependencies changed during preparation')
                image, texture = live_display.target(scene, ts, channel, builder.n)
                renderer.compose(layers, channel, builder.n, texture)
                renderer.generate_mips(texture, builder.n)
                live_display.publish(scene, ts, channel, image)
                state['signatures'][channel] = signature
                metrics.count('live_channel_publications')
            yield
            if serial != ts.edit_serial:
                return False
        state['serial'] = serial
        metrics.count('live_revisions')
        return True
    finally:
        builder.resources.release_required()


def suspend(scene, ts):
    """Restore authoritative image bindings before a synchronous CPU consumer."""
    from . import live_display
    live_display.restore(scene, ts)
    _sessions.pop(key_for(scene, ts), None)


def failed(scene, ts, error):
    # Keep the last completed display while the CPU fallback works. It is
    # restored only after a successful authoritative evaluation, never on error.
    _fallbacks[key_for(scene, ts)] = str(error)
    _fallback_serials[key_for(scene, ts)] = ts.edit_serial
    metrics.count('live_cpu_fallbacks')


def retry(scene, ts):
    _fallbacks.pop(key_for(scene, ts), None)
    _fallback_serials.pop(key_for(scene, ts), None)
    suspend(scene, ts)
    invalidate_inputs(scene, ts)


def invalidate_inputs(scene, ts):
    key = key_for(scene, ts)
    if _device is not None:
        _device.discard_checkpoints(key)
    for token in list(_leaves.entries):
        if token[0] == key:
            _leaves.used -= _leaves.entries.pop(token)[1]


def prune(live):
    from . import live_display
    live_display.prune(live)
    if _window is not None and _window not in {window.as_pointer() for window in bpy.context.window_manager.windows}:
        clear()
    if _device is not None:
        _device.prune_checkpoints(live)
    for table in (_sessions, _fallbacks, _fallback_serials):
        for key in set(table) - live:
            table.pop(key, None)
    # Leaf keys start with the scene/texture-set identity. Keep no deleted owners.
    for key in list(_leaves.entries):
        if key[0] not in live:
            _leaves.used -= _leaves.entries.pop(key)[1]


def clear():
    global _device, _window
    from . import live_display
    live_display.clear()
    _sessions.clear()
    _fallbacks.clear()
    _fallback_serials.clear()
    _leaves.clear()
    if _device is not None:
        _device.close()
    _device = None
    _window = None
