"""Revisioned texture inputs for the live GPU composition graph.

Validated scalar sources and mesh-aware masks can generate GPU texture inputs
directly. Other sources retain the CPU reference at authored resolution. Both
paths share content keys, so a composition edit reuses unchanged texture inputs.
"""

from time import perf_counter
from types import SimpleNamespace

import numpy as np

from ..core.layer_stack import channel_for
from ..constants import DEFAULTS, OWNER
from . import metrics, material_space
from .channel_eval import TILE_ROWS, _procedural_tile, _detail_tile, source
from .image_snapshots import ImageSnapshots
from .mask_eval import _effect_signature, _stack_signature, evaluate_masks, map_pixels
from .operation_contracts import surface
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


def _constant_identity(layer, prop, mode, backdrop):
    """Prove a constant Normal fill leaves a known opaque backdrop unchanged.

    Isolated folders have transparent coverage, so never supply their children
    with this backdrop. Retained sources make subsequent backdrop values unknown;
    this deliberately does not infer constants from textures, masks or filters.
    """
    if (backdrop is None or layer.type != 'FILL' or mode != 'NORMAL'
            or prop.image or prop.image_name or prop.procedural or getattr(prop, 'anchor', '')
            or getattr(prop, 'detail_image', None) or getattr(prop, 'detail_image_name', '')
            or _effects(prop)):
        return False
    color = _color(prop, layer.type)
    return all(np.isfinite(color)) and tuple(color[:3]) == backdrop


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
        detail = getattr(prop, 'detail_image', None)
        if detail:
            return (*self.prefix, 'detail-scalar-v1', material_space.identity(prop), image_key(detail), prop.projection,
                    prop.filtering, prop.tiling, tuple(prop.scale), prop.rotation,
                    tuple(prop.offset), prop.triplanar_hardness,
                    self.geometry_stamp if prop.projection == 'TRIPLANAR' or material_space.enabled(prop) else None)
        if self._gpu_field_supported(prop):
            return self._scalar_key(prop.procedural, prop.procedural_scale, prop.procedural_seed)
        return (*self.prefix, 'procedural', material_space.identity(prop), prop.procedural, prop.procedural_scale,
                prop.procedural_seed, prop.projection, tuple(prop.scale), prop.rotation,
                tuple(prop.offset), prop.triplanar_hardness,
                self.geometry_stamp if prop.projection == 'TRIPLANAR' or material_space.enabled(prop) else None)

    def _scalar_key(self, kind, scale, seed):
        from ..texture_engine.procedurals import ALIASES
        kind = kind.strip().upper().replace('-', '_').replace(' ', '_')
        return (*self.prefix, 'scalar-v1', ALIASES.get(kind, kind), scale, seed)

    def _gpu_field_supported(self, prop):
        from .live_procedurals import supports
        return (not material_space.enabled(prop) and not getattr(prop, 'detail_image', None)
                and not getattr(prop, 'detail_image_name', '')
                and not (self.n & (self.n - 1)) and prop.projection == 'UV'
                and tuple(prop.scale) == (1., 1.) and prop.rotation == 0
                and tuple(prop.offset) == (0., 0.) and prop.tiling == 'REPEAT'
                and supports(prop.procedural, self.n, scale=prop.procedural_scale,
                             seed=prop.procedural_seed))

    def _gpu_mask(self, layer, images):
        def field(kind, n, *, scale, seed):
            key = self._scalar_key(kind, scale, seed)
            texture = self.cache.get(key)
            if texture is None:
                texture = self.device.generate_scalar(kind, n, scale=scale, seed=seed)
                self.cache.put(key, texture, n*n*4)
            return texture

        def mesh_map(kind):
            image = next(item.image for item in self.mask_ts.baked_maps
                         if item.kind == kind and item.image)
            key = (*self.prefix, 'baked-map', image_key(image))
            texture = self.cache.get(key)
            if texture is None:
                raster = map_pixels(self.mask_ts, kind, images)
                texture = self.device.upload(raster)
                self.cache.put(key, texture, raster.nbytes)
                metrics.count('live_input_uploads')
                metrics.count('live_input_upload_bytes', raster.nbytes)
            return texture

        return self.device.generate_mask(self.mask_ts, layer, self.n,
                                         field=field, mesh_map=mesh_map)

    def _source_key(self, prop, kind):
        return (*self.prefix, 'source', material_space.identity(prop), getattr(prop, 'material_height_m', 0), self.ts.height_distance, kind, prop.channel, image_key(prop.image),
                prop.image_name if not prop.image else '', tuple(prop.color), prop.value,
                image_key(getattr(prop, 'detail_image', None)),
                getattr(prop, 'detail_image_name', '') if not getattr(prop, 'detail_image', None) else '',
                prop.procedural, prop.procedural_scale, prop.procedural_seed,
                prop.procedural_detail, prop.procedural_height, prop.projection,
                prop.filtering, prop.tiling, tuple(prop.scale), prop.rotation,
                tuple(prop.offset), prop.triplanar_hardness,
                tuple(_effect_signature(effect) for effect in _effects(prop)),
                # Surface-space effects pad each island from the set's seam map.
                self.geometry_stamp if prop.projection == 'TRIPLANAR' or material_space.enabled(prop)
                or any(surface(effect) for effect in _effects(prop)) else None,
                getattr(prop, 'anchor', ''), self.ts.edit_serial if getattr(prop, 'anchor', '') else None)

    def _texture(self, key, components, tile, resolve=True, gpu_source=None):
        if not resolve:
            return None
        texture = self.cache.get(key)
        if texture is not None:
            metrics.count('live_input_cache_hits')
            return texture
        metrics.count('live_input_cache_misses')
        if gpu_source is not None:
            from .live_procedurals import UnsupportedSource
            try:
                texture = gpu_source()
            except UnsupportedSource:
                metrics.count('live_cpu_source_fallbacks')
            else:
                self.cache.put(key, texture, self.n*self.n*components*4)
                return texture
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
        from .anchors import consumed
        references = any(mask.enabled and (mask.source == 'REFERENCE' or consumed(mask)) for mask in layer.masks)
        parameters = self.mask_ts if references else self.mask_ts.replaced(edit_serial=0)
        signature = _stack_signature(parameters, layer, self.signatures)
        key = (*self.prefix, 'mask', signature)
        texture = yield from self._texture(key, 1, lambda low, high:
            evaluate_masks(self.mask_ts, layer, self.n, low, high, images, self.resources), resolve,
            (lambda: self._gpu_mask(layer, images)) if hasattr(self.device, 'generate_mask') else None)
        return texture, key

    def _source(self, layer, prop, images, resolve):
        if prop.image_name and not prop.image:
            raise ValueError(f'Texture image is missing: {prop.image_name}. Reload or clear the channel texture.')
        detail = getattr(prop, 'detail_image', None)
        detail_name = getattr(prop, 'detail_image_name', '')
        if layer.type == 'FILL' and detail_name and not detail:
            raise ValueError(f'Surface detail image is missing: {detail_name}. '
                             'Reload or clear the surface detail texture.')
        effects = _effects(prop)
        color = _color(prop, layer.type)
        anchored = layer.type == 'FILL' and getattr(prop, 'anchor', '')
        if not prop.image and not effects and not anchored and (layer.type == 'PAINT' or not (prop.procedural or detail)):
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
        if (layer.type == 'FILL' and (prop.procedural or detail) and prop.channel != 'NORMAL'
                and not prop.image and not effects and not anchored and prop.tiling == 'REPEAT'):
            key = self._field_key(prop)
            field = _detail_tile if detail else _procedural_tile
            texture = yield from self._texture(key, 1, lambda low, high:
                field(prop, self.n, low, high, self.ts, self.resources), resolve,
                (lambda: self.device.generate_scalar(prop.procedural, self.n,
                    scale=prop.procedural_scale, seed=prop.procedural_seed))
                if hasattr(self.device, 'generate_scalar') and self._gpu_field_supported(prop) else None)
            operation = (3 if prop.channel == 'HEIGHT' else
                         1 if prop.channel in {'BASE_COLOR', 'EMISSION'} else 2)
            result = dict(field=texture, color=color, procedural=operation,
                          detail=prop.procedural_detail, height=material_space.height_amplitude(prop, self.ts))
            return result, ('modulate', key, operation, color, prop.procedural_detail, material_space.height_amplitude(prop, self.ts))
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

        def compose(parent, ancestry, known_backdrop=None):
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
                if layer.type == 'FOLDER':
                    passthrough = layer.group_mode == 'PASSTHROUGH'
                    children, child_signature = yield from compose(
                        layer.id, ancestry | {layer.id}, known_backdrop if passthrough else None)
                    if not children:
                        # Empty isolated groups have zero coverage; passthrough
                        # groups with no remaining changes retain their backdrop.
                        if resolve:
                            metrics.count('live_identity_elisions')
                        continue
                    mask, mask_signature = yield from self._mask(layer, images, resolve)
                    record = dict(mode=mode, opacity=opacity, mask=mask,
                                  children=children, passthrough=passthrough)
                    if not parent and not layers:
                        # The first root starts from a known default backdrop in
                        # passthrough mode, or transparent coverage when isolated.
                        # Cache its children before the root opacity/mask/blend;
                        # switching group mode changes that initial coverage.
                        record['checkpoint_key'] = (
                            *self.prefix, channel, layer.id, (passthrough, child_signature))
                    layers.append(record)
                    signatures.append(('folder', mode, opacity, mask_signature, passthrough, child_signature))
                else:
                    if _constant_identity(layer, prop, mode, known_backdrop):
                        if resolve:
                            metrics.count('live_identity_elisions')
                        continue
                    mask, mask_signature = yield from self._mask(layer, images, resolve)
                    value, source_signature = yield from self._source(layer, prop, images, resolve)
                    layers.append(dict(mode=mode, opacity=opacity, mask=mask, source=value))
                    signatures.append(('layer', mode, opacity, mask_signature, source_signature))
                known_backdrop = None
            return layers, tuple(signatures)

        try:
            layers, signature = yield from compose('', set(), DEFAULTS[channel][:3])
            return layers, (self.n, channel, signature)
        finally:
            if resolve:
                self.resources.release_required()
