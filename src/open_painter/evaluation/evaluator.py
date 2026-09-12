"""Bottom-to-top, isolated-folder composition into six controlled images."""

from time import perf_counter

import numpy as np

from ..constants import CHANNELS, DEFAULTS
from ..core import image_manager, material_manager
from ..core.layer_stack import channel_for
from ..utils.logging import logger
from .blend_modes import blend
from .channel_eval import source


class LayerEvaluator:
    """Evaluate only dirty channel outputs; persistent layer data is authoritative."""

    def __init__(self, scene):
        self.scene = scene
        self.geometry_cache = {}

    def evaluate_texture_set(self, texture_set_id: str, force=False):
        ts = next((s for s in self.scene.open_painter.texture_sets if s.id == texture_set_id), None)
        if ts is None:
            raise ValueError("No active texture set.")
        from ..core.validation import hierarchy_issues

        issues = hierarchy_issues(ts)
        if issues:
            raise ValueError(" | ".join(issues))
        self.geometry_cache.clear()
        started = perf_counter()
        for channel in CHANNELS:
            out = image_manager.output(ts, channel)
            if force or out.dirty:
                self.evaluate_channel(ts.id, channel)
        self.geometry_cache.clear()
        ts.dirty = False
        for layer in ts.layers:
            layer.dirty = False
            for prop in layer.channels:
                prop.dirty = False
        material_manager.repair(ts)
        logger.debug("Evaluated %s in %.3fs", ts.name, perf_counter() - started)

    def evaluate_channel(self, texture_set_id: str, channel: str):
        job = self.channel_steps(texture_set_id, channel)
        while True:
            try:
                next(job)
            except StopIteration as done:
                return done.value

    def channel_steps(self, texture_set_id: str, channel: str, region=None):
        """Cooperative preview job; callers may yield to Blender between row tiles."""
        ts = next(s for s in self.scene.open_painter.texture_sets if s.id == texture_set_id)
        n = int(ts.resolution)
        if region is None:
            result = np.empty((n, n, 4), dtype=np.float32)
            low, high, cache = 0, n, {}
        else:
            # Unchanged rows retain their last authoritative composite. A paint
            # raster uses the same UV grid, including inside masked/grouped layers.
            result = image_manager.pixels(image_manager.output(ts, channel).image)
            low, high = region[2], region[3]
            cache = {region[4]: region[5]}
        children = {}
        for layer in ts.layers:
            children.setdefault(layer.parent_id, []).append(layer)

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
                prop = channel_for(layer, channel)
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
                    src, alpha = compose(layer.id, y0, y1, (0, 0, 0), ancestry | {layer.id})
                    # Child composition is premultiplied against black; recover straight color.
                    src = np.divide(src, alpha, out=np.zeros_like(src), where=alpha > 1e-8)
                else:
                    if not prop or not prop.enabled:
                        continue
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

        for y0 in range(low, high, 32):
            y1 = min(high, y0 + 32)
            rgb, _ = compose("", y0, y1, DEFAULTS[channel][:3], set(), 1)
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
        out = image_manager.output(ts, channel)
        image_manager.write(out.image, result, pack=False)
        out.dirty = False
        out.revision += 1
        return out.image
