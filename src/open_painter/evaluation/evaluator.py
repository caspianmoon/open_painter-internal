"""Bottom-to-top, isolated-folder composition into controlled texture images."""

from time import perf_counter

import numpy as np

from ..constants import CHANNELS, DEFAULTS
from ..core import image_manager, material_manager
from ..core.layer_stack import channel_for
from ..utils.logging import logger
from .blend_modes import blend
from . import metrics
from .channel_eval import source, TILE_ROWS, _procedural_tile
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

        issues = hierarchy_issues(ts)
        if issues:
            raise ValueError(" | ".join(issues))
        from . import dependencies, cache as scheduler
        dependencies.refresh(self.scene)
        scheduler.discard(scheduler.key_for(self.scene, ts))
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

    def channel_steps(self, texture_set_id: str, channel: str, region=None):
        ts = next(s for s in self.scene.open_painter.texture_sets if s.id == texture_set_id)
        resources = for_texture_set(self.scene, ts)
        try:
            return (yield from self._channel_steps(texture_set_id, channel, region))
        finally:
            resources.release_required()

    def _channel_steps(self, texture_set_id: str, channel: str, region=None):
        """Cooperative preview job; callers may yield to Blender between row tiles."""
        ts = next(s for s in self.scene.open_painter.texture_sets if s.id == texture_set_id)
        self.geometry_cache = for_texture_set(self.scene, ts)
        serial = ts.edit_serial
        n = int(ts.resolution)
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
                footprints = [mask_footprint(layer.masks), stack_footprint(prop.effects) if prop else 0]
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
            result = image_manager.pixels(image_manager.output(ts, channel).image)
            low, high = region[2], region[3]
            cache = {region[4]: region[5]}
        children = {}
        channel_properties = {}
        for layer in ts.layers:
            children.setdefault(layer.parent_id, []).append(layer)
            channel_properties[layer.id] = channel_for(layer, channel)

        from .buffers import scalar_compatible
        scalar = scalar_compatible(channel, ts.layers, channel_properties)
        components = 1 if scalar else 3

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

        def compose(parent_id, y0, y1, initial, ancestry, initial_coverage=0):
            rgb = np.array(initial, dtype=np.float32)
            coverage = np.array(initial_coverage, dtype=np.float32)
            # Source-over cannot reduce opaque coverage. Keep that scalar
            # invariant rather than allocating and dividing an all-ones map.
            opaque = coverage.ndim == 0 and coverage == 1
            for layer in children.get(parent_id, []):
                if not layer.visible:
                    continue
                if layer.id in ancestry:
                    raise ValueError("Layer hierarchy contains a cycle. Reparent the folder.")
                prop = channel_properties[layer.id]
                if prop and not prop.enabled:
                    continue
                mode = (
                    prop.blend_mode if prop and prop.blend_mode != "INHERIT" else layer.blend_mode
                )
                if mode == "DISABLE":
                    continue
                amount = layer.opacity * (prop.opacity if prop else 1)
                if layer.masks:
                    from .mask_eval import evaluate_masks

                    amount = amount * evaluate_masks(ts, layer, n, y0, y1, cache, self.geometry_cache)[..., None]
                if not np.any(amount):
                    continue
                if layer.type == "FOLDER" and layer.group_mode == "PASSTHROUGH":
                    # Evaluate against the actual backdrop, then attenuate the whole
                    # group's change once. Child opacity must not be applied twice.
                    grouped, grouped_coverage = compose(
                        layer.id, y0, y1, rgb, ancestry | {layer.id}, coverage
                    )
                    rgb = rgb + amount * (grouped - rgb)
                    if not opaque:
                        coverage = coverage + amount * (grouped_coverage - coverage)
                    continue
                if layer.type == "FOLDER":
                    src, alpha = compose(layer.id, y0, y1, (0,) * components, ancestry | {layer.id})
                    # Child composition is premultiplied against black; recover straight color.
                    src = np.divide(src, alpha, out=np.zeros_like(src), where=alpha > 1e-8)
                else:
                    if not prop or not prop.enabled:
                        continue
                    if scalar:
                        from .buffers import scalar_source
                        src, alpha = scalar_source(prop, layer.type, n, y0, y1, ts, self.geometry_cache)
                    else:
                        rgba = source(prop, layer.type, n, y0, y1, cache, ts, self.geometry_cache)
                        src, alpha = rgba[..., :3], rgba[..., 3:4]
                alpha = np.clip(alpha * amount, 0, 1)
                if opaque:
                    rgb = blend(rgb, src, alpha, mode)
                else:
                    # Premultiplied source-over composition. Blends only interact
                    # where the backdrop exists; a first Multiply child stays visible.
                    backdrop = np.divide(
                        rgb, coverage, out=np.zeros_like(rgb), where=coverage > 1e-8
                    )
                    mixed = blend(backdrop, src, 1, mode)
                    rgb = (1 - alpha) * rgb + alpha * ((1 - coverage) * src + coverage * mixed)
                    coverage = alpha + coverage * (1 - alpha)
            return rgb, coverage

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
                    mask = evaluate_masks(ts, layer, n, y0, y1, cache, self.geometry_cache)
                    yield
                    if ts.edit_serial != serial:
                        return None
                    if not np.any(mask):
                        skipped.add(layer.id)
                        continue
                if layer.type == "FILL" and prop and prop.procedural:
                    _procedural_tile(prop, n, y0, y1, ts, self.geometry_cache)
                    yield
                    if ts.edit_serial != serial:
                        return None
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
            yield
            if ts.edit_serial != serial:
                return None
        if ts.edit_serial != serial:
            # Main-thread edits between yields cannot publish an older raster.
            return None
        out = image_manager.output(ts, channel)
        image_manager.write(out.image, result, pack=False)
        out.dirty = False
        out.revision += 1
        return out.image
