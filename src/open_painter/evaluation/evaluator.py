"""Bottom-to-top, isolated-folder composition into controlled texture images."""

from time import perf_counter

import numpy as np

from ..constants import CHANNELS, DEFAULTS
from ..core import image_manager, material_manager
from ..core.layer_stack import channel_for
from ..utils.logging import logger
from . import metrics
from .channel_eval import source, TILE_ROWS, _field_tile
from .raster_cache import RasterCache, for_texture_set


class LayerEvaluator:
    """Evaluate only dirty channel outputs; persistent layer data is authoritative."""

    def __init__(self, scene):
        self.scene = scene
        self.geometry_cache = RasterCache()

    def evaluate_texture_set(self, texture_set_id: str, force=False):
        from . import cache as scheduler
        ts = next((s for s in self.scene.open_painter.texture_sets if s.id == texture_set_id), None)
        try:
            return self._evaluate_texture_set(texture_set_id, force)
        except Exception as exc:
            if ts is not None:
                ts.dirty = True
                scheduler.set_state(self.scene, ts, "FAILED", str(exc))
                self.scene.open_painter.status = scheduler.failure_message(self.scene)
            raise

    def _evaluate_texture_set(self, texture_set_id, force=False):
        ts = next((s for s in self.scene.open_painter.texture_sets if s.id == texture_set_id), None)
        if ts is None:
            raise ValueError("No active texture set.")
        from ..core.validation import hierarchy_issues
        from ..core.udim import validate as validate_udim
        validate_udim(ts)

        issues = hierarchy_issues(ts)
        if issues:
            raise ValueError(" | ".join(issues))
        from . import dependencies, cache as scheduler
        dependencies.refresh(self.scene)
        scheduler.discard(scheduler.key_for(self.scene, ts))
        from . import live_preview
        if force:
            live_preview.invalidate_inputs(self.scene, ts)
        self.geometry_cache = for_texture_set(self.scene, ts, force=force)
        started = perf_counter()
        for channel in CHANNELS:
            out = image_manager.output(ts, channel)
            if force or out.dirty:
                self.evaluate_channel(ts.id, channel)
        ts.dirty = False
        for layer in ts.layers:
            layer.dirty = False
            for prop in layer.channels:
                prop.dirty = False
        live_preview.suspend(self.scene, ts)
        material_manager.repair(ts)
        scheduler.set_state(self.scene, ts, "READY")
        self.scene.open_painter.status = scheduler.failure_message(self.scene)
        logger.debug("Evaluated %s in %.3fs", ts.name, perf_counter() - started)

    def evaluate_channel(self, texture_set_id: str, channel: str):
        job = self.channel_steps(texture_set_id, channel)
        while True:
            try:
                next(job)
            except StopIteration as done:
                return done.value

    def channel_steps(self, texture_set_id: str, channel: str, region=None, preview_resolution=None):
        ts = next(s for s in self.scene.open_painter.texture_sets if s.id == texture_set_id)
        if preview_resolution is None:
            resources = for_texture_set(self.scene, ts)
        else:
            from .preview_policy import resources as preview_resources
            resources = preview_resources(self.scene, ts, preview_resolution)
        try:
            return (yield from self._channel_steps(texture_set_id, channel, region, preview_resolution, resources))
        finally:
            resources.release_required()

    def _channel_steps(self, texture_set_id: str, channel: str, region=None, preview_resolution=None, resources=None):
        """Cooperative preview job; callers may yield to Blender between row tiles."""
        ts = next(s for s in self.scene.open_painter.texture_sets if s.id == texture_set_id)
        from ..core.udim import validate as validate_udim
        validate_udim(ts)
        self.geometry_cache = resources if resources is not None else for_texture_set(self.scene, ts)
        author = ts
        serial = author.edit_serial
        from .snapshots import capture
        n = preview_resolution or int(ts.resolution)
        ts = capture(author, n / int(author.resolution)).replaced(resolution=str(n))
        if region is not None:
            from .operation_contracts import stack_footprint, mask_footprint
            from .tiles import expand
            halo = 0
            for layer in ts.layers:
                if not layer.visible:
                    continue
                prop = channel_for(layer, channel)
                if prop and not prop.enabled:
                    continue
                footprints = [mask_footprint(layer.masks), stack_footprint(prop.effects) if prop else 0,
                              None if prop and getattr(prop, "anchor", "") else 0]
                if None in footprints:
                    region = None
                    break
                # Sum is conservative across nested folders and source effects.
                halo += sum(footprints)
            if region is not None:
                low, high = expand(region[2], region[3], halo, n)
                region = (*region[:2], low, high, *region[4:])
        if region is None:
            result = np.empty((n, n, 4), dtype=np.float32)
            metrics.count("output_buffer_allocated_bytes", result.nbytes)
            metrics.gauge("output_buffer_bytes", result.nbytes)
            low, high, cache = 0, n, {}
        else:
            # Unchanged rows retain their last authoritative composite. A paint
            # raster uses the same UV grid, including inside masked/grouped layers.
            from .resource_keys import image_key
            output_image = image_manager.output(author, channel).image
            output_key = ('cpu_output', image_key(output_image))
            if output_key in self.geometry_cache:
                result = self.geometry_cache[output_key].copy()
                metrics.count('output_readback_skips')
            else:
                result = image_manager.pixels(output_image)
            low, high = region[2], region[3]
            cache = {region[4]: region[5]}
        from .image_snapshots import ImageSnapshots
        from .composite import Composer
        cache = ImageSnapshots(self.geometry_cache, serial, cache)
        composer = Composer(ts, channel, n, cache, self.geometry_cache)
        children, channel_properties, components = composer.children, composer.props, composer.components

        def visible_work(parent_id, ancestry):
            for layer in children.get(parent_id, ()):
                prop = channel_properties[layer.id]
                if not layer.visible or (prop and not prop.enabled):
                    continue
                mode = prop.blend_mode if prop and prop.blend_mode != "INHERIT" else layer.blend_mode
                if mode == "DISABLE" or layer.opacity == 0 or (prop and prop.opacity == 0):
                    continue
                if layer.id in ancestry:
                    raise ValueError("Layer hierarchy contains a cycle. Reparent the folder.")
                yield layer, prop
                if layer.type == "FOLDER":
                    yield from visible_work(layer.id, ancestry | {layer.id})

        work = tuple(visible_work("", set()))
        from .preview_policy import SlicePolicy
        slices = SlicePolicy()
        compose = composer.compose

        for y0 in range(low, high, TILE_ROWS):
            y1 = min(high, y0 + TILE_ROWS)
            # A cold smart material can contain several expensive sources. Yield
            # between those independent computations, before cheap composition,
            # instead of blocking the UI for the sum of all sources in a tile.
            skipped = set()
            for layer, prop in work:
                if layer.parent_id in skipped:
                    skipped.add(layer.id)
                    continue
                if layer.masks:
                    from .mask_eval import evaluate_masks
                    step_started = perf_counter()
                    mask = evaluate_masks(ts, layer, n, y0, y1, cache, self.geometry_cache)
                    if slices.ready(perf_counter()-step_started):
                        yield
                    if author.edit_serial != serial:
                        return None
                    if not np.any(mask):
                        skipped.add(layer.id)
                        continue
                # A detail image overrides a retained procedural; warm the source
                # the composite actually samples, never the noise it ignores.
                if layer.type == "FILL" and prop and (prop.procedural or getattr(prop, "detail_image", None)
                                                      or getattr(prop, "detail_image_name", "")):
                    step_started = perf_counter()
                    _field_tile(prop, n, y0, y1, ts, self.geometry_cache)
                    if slices.ready(perf_counter()-step_started):
                        yield
                    if author.edit_serial != serial:
                        return None
            step_started = perf_counter()
            rgb, _ = compose("", y0, y1, DEFAULTS[channel][:components], set(), 1)
            if channel == "NORMAL":
                vectors = rgb * 2 - 1
                lengths = np.linalg.norm(vectors, axis=-1, keepdims=True)
                vectors = np.divide(
                    vectors, lengths, out=np.zeros_like(vectors), where=lengths > 1e-8
                )
                rgb = vectors * 0.5 + 0.5
            result[y0:y1, :, :3] = np.clip(rgb, 0, 1)
            result[y0:y1, :, 3] = 1
            if slices.ready(perf_counter()-step_started):
                yield
            if author.edit_serial != serial:
                return None
        if author.edit_serial != serial:
            # Main-thread edits between yields cannot publish an older raster.
            return None
        if preview_resolution is not None:
            from .preview_policy import publish
            return publish(self.scene, author, channel, n, serial, result)
        out = image_manager.output(author, channel)
        image_manager.write(out.image, result, pack=False)
        from .resource_keys import image_key
        result.flags.writeable = False
        # Retain the active channel only, and never evict reusable sources to
        # save a future output readback. Eight 2K RGBA outputs alone are 512 MiB.
        manager = self.geometry_cache.manager
        if channel == self.scene.open_painter.display_channel and result.nbytes <= manager.allowance // 4:
            for key in tuple(self.geometry_cache):
                if isinstance(key,tuple) and key[0] == 'cpu_output':
                    self.geometry_cache.pop(key)
            if manager.used + result.nbytes <= manager.allowance:
                self.geometry_cache[('cpu_output', image_key(out.image))] = result
            else:
                metrics.count('output_cache_capacity_skips')
        out.dirty = False
        out.revision += 1
        return out.image
