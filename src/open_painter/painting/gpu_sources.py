"""Persistent stroke records evaluated into transient GPU source textures.

Stores are keyed by document IDs, not RNA addresses. Every access resolves current
RNA so an undo never leaves a pointer into the retired document. Geometry/base
identity changes discard acceleration; CPU export remains the independent reference.
"""
from collections import OrderedDict

import numpy as np

from ..evaluation import metrics
from ..evaluation.engine import runtime
from ..evaluation.engine.formats import SCALAR_CHANNELS
from ..evaluation.engine.resources import TextureResource
from ..evaluation.resource_keys import image_key
from . import gpu_history, gpu_strokes, stroke_raster

_stores = OrderedDict()
_preview_surfaces = OrderedDict()


def clear():
    for store in _stores.values():
        store.close()
    _stores.clear()
    for surface in _preview_surfaces.values():
        surface.close()
    _preview_surfaces.clear()


def prune(live):
    import bpy
    scenes = {scene.session_uid: scene for scene in bpy.data.scenes}
    for key, store in list(_stores.items()):
        scene = scenes.get(key[0])
        if scene is None or (scene.as_pointer(), key[1]) not in live:
            store.close()
            del _stores[key]
    for key, surface in list(_preview_surfaces.items()):
        scene = scenes.get(key[0])
        if scene is None or (scene.as_pointer(), key[1]) not in live:
            surface.close()
            del _preview_surfaces[key]


def memory(set_id):
    return sum(store.history.bytes for key, store in _stores.items() if key[1] == set_id)


def retained_bytes(set_id):
    stores = [s for key, s in _stores.items() if key[1] == set_id]
    surfaces = {id(s.surface): s.surface for s in stores if s.surface is not None}
    surfaces.update((id(surface), surface) for key, surface in _preview_surfaces.items() if key[1] == set_id)
    return sum(s.output.bytes if s.output else 0 for s in stores) + sum(
        s.n * s.n * 32 + s.bounds_bytes for s in surfaces.values())


def surface_for(scene, ts, n):
    from ..evaluation.dependencies import geometry_key
    key = (scene.session_uid, ts.id, n, repr(geometry_key(scene, ts)))
    return next((s.surface for s in _stores.values() if s.surface is not None and s.surface_key == key), None)


def preview_surface(scene, ts, n, entries):
    """Reuse bounded-preview geometry across gestures, including large documents."""
    shared = surface_for(scene, ts, n)
    if shared is not None:
        return shared
    from ..evaluation.dependencies import geometry_key
    key = (scene.session_uid, ts.id, n, repr(geometry_key(scene, ts)))
    if key not in _preview_surfaces:
        surface = gpu_strokes.Surface(entries, n)
        for stale in list(_preview_surfaces):
            if stale[:3] == key[:3]:
                _preview_surfaces.pop(stale).close()
        _preview_surfaces[key] = surface
    _preview_surfaces.move_to_end(key)
    return _preview_surfaces[key]


def adopt(preview, stroke):
    """Adopt completed full-resolution gestures without replay or GPU readback."""
    canvas = preview.gpu
    if canvas is None or preview.size != preview.n or any(preview.premultiplied.values()):
        return
    for channel, resource in list(canvas.outputs.items()):
        key = (preview.scene.session_uid, preview.ts_id, preview.layer_id, channel, preview.n)
        store = _stores.get(key)
        if store is None or resource.format != store.source.format:
            continue
        _, _, _, records, base = store.source.records()
        desired = tuple((s.id, s.revision) for s in records)
        if base != store.base or desired != (*store.prefix, (stroke.id, stroke.revision)):
            continue
        rect = canvas.total_region
        if rect is not None:
            x, y, w, h = rect
            x0, y0 = max(0, x - 1), max(0, y - 1)
            rect = x0, y0, min(preview.n, x + w + 1) - x0, min(preview.n, y + h + 1) - y0
            from ..evaluation.engine.formats import texture_bytes
            if texture_bytes(max(rect[2:]), max(rect[2:]), resource.format) * 2 <= runtime.RETAIN_BYTES:
                first = gpu_history.optional_capture(store.output, rect)
                if first is not None:
                    last = gpu_history.optional_capture(resource, rect)
                    if last is None:
                        first.close()
                    else:
                        store.history.put(store.prefix, desired, first, last, runtime.RETAIN_BYTES)
        runtime.device().discard(store.output.texture)
        store.output = canvas.outputs.pop(channel)
        store.prefix = desired
        metrics.count('stroke_gpu_adoptions')


def trim(set_id, budget, protected=None):
    for key, surface in list(_preview_surfaces.items()):
        if memory(set_id) + retained_bytes(set_id) <= budget:
            break
        if key[1] == set_id and not surface.pins:
            surface.close()
            del _preview_surfaces[key]
            metrics.count('stroke_surface_evictions')
    stores = [store for key, store in _stores.items() if key[1] == set_id]
    for store in stores:
        if memory(set_id) + retained_bytes(set_id) <= budget:
            break
        if store is not protected:
            store.release_surface()
    for store in stores:
        excess = memory(set_id) + retained_bytes(set_id) - budget
        if excess <= 0:
            break
        store.history.trim(max(0, store.history.bytes - excess))
    for key, store in list(_stores.items()):
        if key[1] != set_id or store is protected:
            continue
        total = memory(set_id) + retained_bytes(set_id)
        if total <= budget:
            break
        store.close()
        del _stores[key]
        metrics.count('stroke_source_evictions')


class Source:
    def __init__(self, graph, layer, prop):
        self.scene_uid, self.set_id, self.layer_id = graph.scene.session_uid, graph.ts.id, layer.id
        self.channel, self.n = prop.channel, graph.n
        self.identity = ('strokes', self.set_id, self.layer_id, self.channel)
        self.mapping = 'rg' if self.channel in SCALAR_CHANNELS else 'rgba'
        # Stroke accumulation keeps the full precision of the persisted CPU record.
        self.format = 'RG32F' if self.mapping == 'rg' else 'RGBA32F'

    @property
    def dimensions(self):
        return self.n, self.n

    def context(self):
        import bpy
        scene = next(scene for scene in bpy.data.scenes if scene.session_uid == self.scene_uid)
        ts = next(t for t in scene.open_painter.texture_sets if t.id == self.set_id)
        layer = next(l for l in ts.layers if l.id == self.layer_id)
        prop = next(p for p in layer.channels if p.channel == self.channel)
        return scene, ts, layer, prop

    def records(self):
        from ..evaluation import dependencies
        from . import strokes
        scene, ts, layer, prop = self.context()
        records = [s for s in strokes.strokes_for(scene.open_painter, layer.stroke_key, ts)
                   if self.channel in strokes.stroke_channels(s)]
        base = repr((dependencies.geometry_key(scene, ts), image_key(prop.image), self.n))
        return scene, ts, prop, records, base

    @property
    def stamp(self):
        _, _, _, records, base = self.records()
        return self.identity, base, tuple((s.id, s.revision) for s in records)

    @property
    def texture(self):
        key = (self.scene_uid, self.set_id, self.layer_id, self.channel, self.n)
        store = _stores.get(key)
        if store is None:
            store = _stores[key] = Store(self)
        _stores.move_to_end(key)
        store.source = self
        texture = store.resolve().texture
        trim(self.set_id, runtime.RETAIN_BYTES, protected=store)
        return texture


class Store:
    def __init__(self, source):
        self.source = source
        self.base = None
        self.prefix = ()
        self.output = self.surface = None
        self.surface_key = None
        self.unsupported = False
        self.history = gpu_history.History()

    def close(self):
        self.history.close()
        if self.output is not None:
            runtime.device().discard(self.output.texture)
            self.output = None
        self.release_surface()
        self.prefix, self.base = (), None

    def release_surface(self):
        if self.surface is not None:
            surface, self.surface = self.surface, None
            if not any(other.surface is surface for other in _stores.values()):
                surface.close()

    def ensure_surface(self, scene, ts):
        if runtime.device().max_samplers < 5:
            self.unsupported = True
            return
        from ..evaluation.dependencies import geometry_key
        from .gesture import snapshot_entries
        key = (scene.session_uid, ts.id, self.source.n, repr(geometry_key(scene, ts)))
        self.surface_key = key
        self.surface = next((s.surface for s in _stores.values() if s is not self and
                             s.surface is not None and s.surface_key == key), None)
        if self.surface is None:
            try:
                self.surface = gpu_strokes.Surface(snapshot_entries(ts), self.source.n)
            except gpu_strokes.UnsupportedSurface:
                self.unsupported = True

    def initialize(self, scene, ts, prop, base):
        from .gesture import snapshot_entries
        from ..evaluation.channel_eval import _image_source
        from ..evaluation.snapshots import capture
        self.close()
        source = self.source
        self.unsupported = False
        self.ensure_surface(scene, ts)
        pixels = (np.zeros((source.n, source.n, 4), np.float32) if prop.image is None else
                  _image_source(prop.image, capture(prop), 'PAINT', source.n, 0, source.n, {}, ts, {}))
        if source.mapping == 'rg':
            pixels = np.ascontiguousarray(pixels[..., [0, 3]])
        texture = runtime.device().upload(pixels, source.format)
        self.output = TextureResource(source.identity, texture, source.format, source.mapping)
        self.base = base

    def resolve(self):
        try:
            return self._resolve()
        except Exception:
            # An incomplete GPU pass must never become the next stroke's base.
            # Reconstruct the entire authoritative prefix before replacing it.
            from ..utils.logging import logger
            logger.debug('GPU stroke evaluation failed; replaying the document on CPU', exc_info=True)
            scene, ts, prop, records, base = self.source.records()
            self.history.close()
            self.release_surface()
            self.unsupported = True
            return self._cpu(ts, prop, records, base)

    def _cpu(self, ts, prop, records, base):
        from ..evaluation.channel_eval import _image_source
        from ..evaluation.snapshots import capture
        _, _, layer, _ = self.source.context()
        n, channel = self.source.n, self.source.channel
        pixels = (None if prop.image is None else
                  _image_source(prop.image, capture(prop), 'PAINT', n, 0, n, {}, ts, {}))
        values = stroke_raster.channel_rows(ts, layer, channel, n, 0, n, base=pixels)
        if self.source.mapping == 'rg':
            values = np.ascontiguousarray(values[..., [0, 3]])
        texture = runtime.device().upload(values, self.source.format)
        revision = 0 if self.output is None else self.output.revision + 1
        if self.output is not None:
            runtime.device().discard(self.output.texture)
        self.output = TextureResource(self.source.identity, texture, self.source.format, self.source.mapping, revision)
        self.prefix = tuple((s.id, s.revision) for s in records)
        self.base = base
        metrics.count('stroke_cpu_fallbacks')
        return self.output

    def _resolve(self):
        from .gesture import stroke_settings
        from .surface_reach import unpack_anchors
        scene, ts, prop, records, base = self.source.records()
        desired = tuple((s.id, s.revision) for s in records)
        if self.output is not None and self.base == base:
            if self.prefix == desired:
                return self.output
            if self.history.move(self.prefix, desired, self.output):
                self.prefix = desired
                metrics.count('stroke_gpu_history_hits')
                return self.output
        if self.output is None or self.base != base or desired[:len(self.prefix)] != self.prefix:
            self.initialize(scene, ts, prop, base)
        channel, n = self.source.channel, self.source.n
        if self.surface is None and not self.unsupported:
            self.ensure_surface(scene, ts)
        from .payload import decode
        resolved_fallback = any(any('values' in d or 'planar' in d for d in decode(s, s.sample_count)) for s in records)
        if self.surface is None or resolved_fallback:
            # UV overlaps retain the established per-triangle reduction exactly.
            metrics.count('stroke_gpu_overlap_fallbacks')
            return self._cpu(ts, prop, records, base)
        for record in records[len(self.prefix):]:
            samples = stroke_raster.unpack_samples(record.samples)
            from .payload import decode
            settings = stroke_settings(record, tuple(record.plane_normal) if record.projection == 'PLANAR' else None)
            settings.update(alpha_rotation=record.alpha_rotation, alpha_angle=record.alpha_angle)
            settings['dabs'] = decode(record, len(samples))
            anchors = unpack_anchors(record.anchors)
            canvas = gpu_strokes.Canvas(self.surface.entries, n, {channel: self.output}, {channel: False},
                                        surface=self.surface, precise=True, set_id=ts.id)
            first = last = None
            try:
                canvas.stamp(samples, settings, anchors if len(anchors) == len(samples) else None)
                rect = canvas.region
                if rect is not None:
                    x, y, w, h = rect
                    x0, y0 = max(0, x - 1), max(0, y - 1)
                    rect = x0, y0, min(n, x + w + 1) - x0, min(n, y + h + 1) - y0
                    from ..evaluation.engine.formats import texture_bytes
                    if texture_bytes(max(rect[2:]), max(rect[2:]), self.output.format) * 2 <= runtime.RETAIN_BYTES:
                        first = gpu_history.optional_capture(self.output, rect)
                canvas.publish(settings)
                new = canvas.outputs.pop(channel)
                runtime.device().discard(self.output.texture)
                self.output = new
                after = (*self.prefix, (record.id, record.revision))
                if first is not None:
                    last = gpu_history.optional_capture(self.output, rect)
                    if last is not None:
                        self.history.put(self.prefix, after, first, last, runtime.RETAIN_BYTES)
                        first = last = None
                self.prefix = after
                metrics.count('stroke_gpu_replays')
            finally:
                for patch in (first, last):
                    if patch is not None:
                        patch.close()
                canvas.close()
        return self.output
