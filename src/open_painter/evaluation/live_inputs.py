"""Exact, revisioned CPU leaves for the live GPU composition graph.

Only compositing and simple source modulation move to the GPU. Existing CPU
procedurals, mesh-aware masks, image sampling and filters retain their authored
resolution and numerical behavior. Their resulting textures are reusable leaves;
changing opacity or a fill value does not regenerate an unchanged noise field.
"""

from time import perf_counter
from types import SimpleNamespace

import numpy as np

from ..core.layer_stack import channel_for
from ..constants import OWNER
from . import metrics
from .channel_eval import TILE_ROWS, _procedural_tile, source
from .image_snapshots import ImageSnapshots
from .mask_eval import _effect_signature, _stack_signature, evaluate_masks
from .preview_policy import SlicePolicy, resolution, resources as preview_resources
from .raster_cache import for_texture_set
from .resource_keys import image_key, key_for
from .snapshots import capture


def _effects(prop):
    return tuple(effect for effect in prop.effects if effect.enabled and effect.strength > 0)


def _color(prop, kind):
    if kind == 'PAINT':
        return (0., 0., 0., 0.)
    if prop.channel in {'BASE_COLOR', 'NORMAL', 'EMISSION'}:
        return tuple(prop.color)
    return (prop.value, prop.value, prop.value, 1.)


class Builder:
    """Capture one authoring revision and yield bounded source-generation work.

    ``cache`` owns disposable GPU textures through get(key)/put(key,texture,bytes).
    Returned layer dictionaries retain the textures needed by that one channel,
    even if subsequent leaf uploads evict their entries from the shared cache.
    The caller checks authoring revision/window lifetime between yielded steps.
    """

    def __init__(self, scene, author_ts, cache, device):
        self.scene = scene
        self.n = resolution(author_ts)
        self.resources = (for_texture_set(scene, author_ts) if self.n == int(author_ts.resolution)
                          else preview_resources(scene, author_ts, self.n))
        self.ts = capture(author_ts, self.n / int(author_ts.resolution)).replaced(resolution=str(self.n))
        # Scene-analysis provenance belongs to the authored texture set. Keeping
        # the proxy size there would disagree with scene_fields.update(author)
        # and repeat that independent analysis for every preview row strip.
        self.mask_ts = self.ts.replaced(resolution=str(author_ts.resolution))
        self.prefix = (key_for(scene, author_ts), self.n)
        self.cache = cache
        self.device = device
        self.geometry_stamp = getattr(self.resources, 'geometry_stamp', None)
        self.children = {}
        for layer in self.ts.layers:
            self.children.setdefault(layer.parent_id, []).append(layer)
        # Keep neutral-serial signatures separate from the CPU evaluator's memo.
        self.signatures = SimpleNamespace(signatures={}, geometry_stamp=self.geometry_stamp)

    def _field_key(self, prop):
        return (*self.prefix, 'procedural', prop.procedural, prop.procedural_scale,
                prop.procedural_seed, prop.projection, tuple(prop.scale), prop.rotation,
                tuple(prop.offset), prop.triplanar_hardness,
                self.geometry_stamp if prop.projection == 'TRIPLANAR' else None)

    def _source_key(self, prop, kind):
        return (*self.prefix, 'source', kind, prop.channel, image_key(prop.image),
                prop.image_name if not prop.image else '', tuple(prop.color), prop.value,
                prop.procedural, prop.procedural_scale, prop.procedural_seed,
                prop.procedural_detail, prop.procedural_height, prop.projection,
                prop.filtering, prop.tiling, tuple(prop.scale), prop.rotation,
                tuple(prop.offset), prop.triplanar_hardness,
                tuple(_effect_signature(effect) for effect in _effects(prop)),
                self.geometry_stamp if prop.projection == 'TRIPLANAR' else None)

    def _texture(self, key, components, tile, resolve=True):
        if not resolve:
            return None
        texture = self.cache.get(key)
        if texture is not None:
            metrics.count('live_input_cache_hits')
            return texture
        metrics.count('live_input_cache_misses')
        shape = (self.n, self.n) if components == 1 else (self.n, self.n, components)
        raster = np.empty(shape, np.float32)
        metrics.count('live_input_allocated_bytes', raster.nbytes)
        slices = SlicePolicy()
        for low in range(0, self.n, TILE_ROWS):
            high = min(self.n, low + TILE_ROWS)
            started = perf_counter()
            raster[low:high] = tile(low, high)
            if slices.ready(perf_counter() - started):
                yield
        # GPU upload is an indivisible boundary. Publish only a complete leaf.
        texture = self.device.upload(raster)
        metrics.count('live_input_uploads')
        metrics.count('live_input_upload_bytes', raster.nbytes)
        self.cache.put(key, texture, raster.nbytes)
        return texture

    def _mask(self, layer, images, resolve):
        if not any(mask.enabled for mask in layer.masks):
            return None, None
        # Native edits increment the source Image revision before this builder is
        # captured. References depend on other authored channels and conservatively
        # retain the actual serial until their transitive keys are represented.
        references = any(mask.enabled and mask.source == 'REFERENCE' for mask in layer.masks)
        parameters = self.mask_ts if references else self.mask_ts.replaced(edit_serial=0)
        signature = _stack_signature(parameters, layer, self.signatures)
        key = (*self.prefix, 'mask', signature)
        texture = yield from self._texture(key, 1, lambda low, high:
            evaluate_masks(self.mask_ts, layer, self.n, low, high, images, self.resources), resolve)
        return texture, key

    def _source(self, layer, prop, images, resolve):
        if prop.image_name and not prop.image:
            raise ValueError(f'Texture image is missing: {prop.image_name}. Reload or clear the channel texture.')
        effects = _effects(prop)
        color = _color(prop, layer.type)
        if not prop.image and not effects and (layer.type == 'PAINT' or not prop.procedural):
            return {'color': color}, ('constant', color)
        if (layer.type == 'PAINT' and not effects and prop.image and prop.image.is_float
                and tuple(prop.image.size) == (self.n, self.n)
                and prop.image.colorspace_settings.name in {'Non-Color', 'Linear Rec.709'}):
            key = self._source_key(prop, layer.type)
            if not resolve:
                return {}, key
            import gpu
            # Blender's native brush already updates this GPU image. Keep its
            # premultiplied pixels and avoid a CPU read/reupload on each stroke.
            if prop.image.get(OWNER) and getattr(prop.image, 'use_half_precision', False):
                prop.image.use_half_precision = False
            texture = gpu.texture.from_image(prop.image)
            if getattr(texture, 'format', None) == 'RGBA32F':
                metrics.count('live_native_paint_inputs')
                return {'texture': texture, 'premultiplied': True}, key
            # Keep external image settings intact and preserve float32 precision
            # if Blender retained an already-created half-precision native texture.
            metrics.count('live_native_paint_precision_fallbacks')
        if (layer.type == 'FILL' and prop.procedural and prop.channel != 'NORMAL'
                and not prop.image and not effects and prop.tiling == 'REPEAT'):
            key = self._field_key(prop)
            texture = yield from self._texture(key, 1, lambda low, high:
                _procedural_tile(prop, self.n, low, high, self.ts, self.resources), resolve)
            operation = (3 if prop.channel == 'HEIGHT' else
                         1 if prop.channel in {'BASE_COLOR', 'EMISSION'} else 2)
            result = dict(field=texture, color=color, procedural=operation,
                          detail=prop.procedural_detail, height=prop.procedural_height)
            return result, ('modulate', key, operation, color, prop.procedural_detail, prop.procedural_height)
        key = self._source_key(prop, layer.type)
        try:
            texture = yield from self._texture(key, 4, lambda low, high:
                source(prop, layer.type, self.n, low, high, images, self.ts, self.resources), resolve)
        finally:
            # Full-frame filter fallbacks are temporary CPU workspaces, not a
            # second cache of all layer/channel RGBA images beside the GPU leaves.
            for entry in tuple(images):
                if isinstance(entry, tuple) and entry[0] == 'filtered_channel':
                    images.pop(entry)
        return {'texture': texture}, key

    def signature(self, channel):
        """Describe a channel without resolving, reading or uploading its leaves.

        This permits an already-published channel to remain current even after
        its disposable input textures were evicted from the shared GPU cache.
        """
        job = self._channel(channel, resolve=False)
        try:
            next(job)
        except StopIteration as result:
            return result.value[1]
        finally:
            job.close()
        raise RuntimeError('Pure live-input signature unexpectedly generated work')

    def channel(self, channel):
        """Return ``(bottom_to_top_layers, dependency_and_uniform_signature)``."""
        return (yield from self._channel(channel, resolve=True))

    def _channel(self, channel, resolve):
        # Managed writes and native stroke detection both advance Image revisions;
        # parameter-only edits can reuse immutable decoded input pixels.
        images = ImageSnapshots(self.resources, 0)

        def compose(parent, ancestry):
            layers, signatures = [], []
            for layer in self.children.get(parent, ()):
                prop = channel_for(layer, channel)
                if not layer.visible or (prop and not prop.enabled):
                    continue
                mode = prop.blend_mode if prop and prop.blend_mode != 'INHERIT' else layer.blend_mode
                opacity = layer.opacity * (prop.opacity if prop else 1.)
                if mode == 'DISABLE' or opacity <= 0:
                    continue
                if layer.id in ancestry:
                    raise ValueError('Layer hierarchy contains a cycle. Reparent the folder.')
                if layer.type != 'FOLDER' and prop is None:
                    continue
                mask, mask_signature = yield from self._mask(layer, images, resolve)
                if layer.type == 'FOLDER':
                    children, child_signature = yield from compose(layer.id, ancestry | {layer.id})
                    passthrough = layer.group_mode == 'PASSTHROUGH'
                    layers.append(dict(mode=mode, opacity=opacity, mask=mask,
                                       children=children, passthrough=passthrough))
                    signatures.append(('folder', mode, opacity, mask_signature, passthrough, child_signature))
                else:
                    value, source_signature = yield from self._source(layer, prop, images, resolve)
                    layers.append(dict(mode=mode, opacity=opacity, mask=mask, source=value))
                    signatures.append(('layer', mode, opacity, mask_signature, source_signature))
            return layers, tuple(signatures)

        try:
            layers, signature = yield from compose('', set())
            return layers, (self.n, channel, signature)
        finally:
            if resolve:
                self.resources.release_required()
