"""Bottom-to-top layer composition shared by channel outputs and composite anchors.

``Composer.compose`` is the reference composition loop (isolated folders
premultiplied against black, passthrough folders attenuated once against the
real backdrop). ``output`` and ``group`` reuse it to publish a layer's blended
result or a folder's child composite in the same numerical form.
"""
import numpy as np

from ..core.layer_stack import channel_for
from .blend_modes import blend
from .channel_eval import source


def _layers(ts, n):
    from .snapshots import Parameters, capture
    layers = tuple(ts.layers)
    if all(isinstance(layer, Parameters) for layer in layers):
        return layers
    scale = n / int(ts.resolution) if int(ts.resolution) else 1.0
    return tuple(layer if isinstance(layer, Parameters) else capture(layer, scale) for layer in layers)


class LayerProfiler:
    """Inclusive/exclusive wall time per layer id while a Composer runs (0.9.0 profiling)."""

    def __init__(self):
        from collections import defaultdict
        self.inclusive = defaultdict(float)
        self.exclusive = defaultdict(float)
        self._stack = []

    def enter(self, layer_id):
        from time import perf_counter
        self._stack.append([layer_id, perf_counter(), 0.0])

    def exit(self):
        from time import perf_counter
        layer_id, started, nested = self._stack.pop()
        elapsed = perf_counter() - started
        self.inclusive[layer_id] += elapsed
        self.exclusive[layer_id] += elapsed - nested
        if self._stack:
            self._stack[-1][2] += elapsed


class Composer:
    def __init__(self, ts, channel, n, cache, resources, profiler=None):
        from .buffers import scalar_compatible
        self.ts, self.channel, self.n, self.cache, self.resources = ts, channel, n, cache, resources
        self.profiler = profiler
        self.layers = _layers(ts, n)
        self.children, self.props, self.by_id = {}, {}, {}
        for layer in self.layers:
            self.children.setdefault(layer.parent_id, []).append(layer)
            self.props[layer.id] = channel_for(layer, channel)
            self.by_id[layer.id] = layer
        self.scalar = scalar_compatible(channel, self.layers, self.props)
        self.components = 1 if self.scalar else 3

    def compose(self, parent_id, y0, y1, initial, ancestry, initial_coverage=0, stop_id=None):
        """Blend the children of ``parent_id`` over ``initial``; ``stop_id`` ends after that layer."""
        ts, n, cache, resources = self.ts, self.n, self.cache, self.resources
        rgb = np.array(initial, dtype=np.float32)
        coverage = np.array(initial_coverage, dtype=np.float32)
        # Source-over cannot reduce opaque coverage. Keep that scalar
        # invariant rather than allocating and dividing an all-ones map.
        opaque = coverage.ndim == 0 and coverage == 1
        for layer in self.children.get(parent_id, []):
            rgb, coverage = self._blend_one(layer, y0, y1, rgb, coverage, opaque, ancestry)
            if layer.id == stop_id:
                break
        return rgb, coverage

    def _blend_one(self, layer, y0, y1, rgb, coverage, opaque, ancestry):
        if self.profiler is None:
            return self._blend_layer(layer, y0, y1, rgb, coverage, opaque, ancestry)
        self.profiler.enter(layer.id)
        try:
            return self._blend_layer(layer, y0, y1, rgb, coverage, opaque, ancestry)
        finally:
            self.profiler.exit()

    def _blend_layer(self, layer, y0, y1, rgb, coverage, opaque, ancestry):
        ts, n, cache, resources = self.ts, self.n, self.cache, self.resources
        if not layer.visible:
            return rgb, coverage
        if getattr(layer, 'bypass', False):
            from . import purpose
            if purpose.is_preview():
                # Bypass is preview-only: export and snapshots keep the layer.
                return rgb, coverage
        if layer.id in ancestry:
            raise ValueError("Layer hierarchy contains a cycle. Reparent the folder.")
        prop = self.props[layer.id]
        if prop and not prop.enabled:
            return rgb, coverage
        mode = prop.blend_mode if prop and prop.blend_mode != "INHERIT" else layer.blend_mode
        if mode == "DISABLE":
            return rgb, coverage
        amount = layer.opacity * (prop.opacity if prop else 1)
        if layer.masks:
            from .mask_eval import evaluate_masks
            amount = amount * evaluate_masks(ts, layer, n, y0, y1, cache, resources)[..., None]
        if not np.any(amount):
            return rgb, coverage
        if layer.type == "FOLDER" and layer.group_mode == "PASSTHROUGH":
            # Evaluate against the actual backdrop, then attenuate the whole
            # group's change once. Child opacity must not be applied twice.
            grouped, grouped_coverage = self.compose(layer.id, y0, y1, rgb, ancestry | {layer.id}, coverage)
            rgb = rgb + amount * (grouped - rgb)
            if not opaque:
                coverage = coverage + amount * (grouped_coverage - coverage)
            return rgb, coverage
        if layer.type == "FOLDER":
            src, alpha = self.compose(layer.id, y0, y1, (0,) * self.components, ancestry | {layer.id})
            # Child composition is premultiplied against black; recover straight color.
            src = np.divide(src, alpha, out=np.zeros_like(src), where=alpha > 1e-8)
        else:
            if not prop or not prop.enabled:
                return rgb, coverage
            if self.scalar:
                from .buffers import scalar_source
                src, alpha = scalar_source(prop, layer.type, n, y0, y1, ts, resources)
            else:
                rgba = source(prop, layer.type, n, y0, y1, cache, ts, resources)
                src, alpha = rgba[..., :3], rgba[..., 3:4]
        alpha = np.clip(alpha * amount, 0, 1)
        if opaque:
            rgb = blend(rgb, src, alpha, mode)
        else:
            # Premultiplied source-over composition. Blends only interact
            # where the backdrop exists; a first Multiply child stays visible.
            backdrop = np.divide(rgb, coverage, out=np.zeros_like(rgb), where=coverage > 1e-8)
            mixed = blend(backdrop, src, 1, mode)
            rgb = (1 - alpha) * rgb + alpha * ((1 - coverage) * src + coverage * mixed)
            coverage = alpha + coverage * (1 - alpha)
        return rgb, coverage

    # ---- published composites -------------------------------------------

    def _initial(self, parent_id, y0, y1):
        """(rgb, coverage) a group starts from: defaults at the root, zeros in an
        isolated folder, the folder's own backdrop when passthrough."""
        from ..constants import DEFAULTS
        if not parent_id:
            return DEFAULTS[self.channel][:self.components], 1
        parent = self.by_id.get(parent_id)
        if parent is None or parent.group_mode != 'PASSTHROUGH':
            return (0,) * self.components, 0
        return self.backdrop(parent_id, y0, y1)

    def backdrop(self, layer_id, y0, y1):
        """What a layer is blended over: its group composed up to, excluding, the layer."""
        layer = self.by_id[layer_id]
        initial, coverage = self._initial(layer.parent_id, y0, y1)
        siblings = self.children.get(layer.parent_id, [])
        position = next((i for i, item in enumerate(siblings) if item.id == layer_id), 0)
        if position == 0:
            return np.array(initial, np.float32), np.array(coverage, np.float32)
        return self.compose(layer.parent_id, y0, y1, initial, self._ancestry(layer.parent_id), coverage,
                            stop_id=siblings[position - 1].id)

    def _ancestry(self, parent_id):
        found = set()
        while parent_id and parent_id not in found:
            found.add(parent_id)
            parent = self.by_id.get(parent_id)
            parent_id = parent.parent_id if parent is not None else ''
        return found

    def _rows(self, rgb, coverage, y0, y1):
        """Straight RGBA rows: scalar composition broadcast to RGB, alpha from coverage."""
        shape = (y1 - y0, self.n)
        rgb = np.broadcast_to(np.asarray(rgb, np.float32), shape + (self.components,))
        if self.components == 1:
            rgb = np.repeat(rgb, 3, axis=-1)
        alpha = np.broadcast_to(np.asarray(coverage, np.float32).reshape(shape + (1,) if np.ndim(coverage) else ()),
                                shape + (1,))
        return np.ascontiguousarray(np.concatenate((rgb, alpha), axis=-1), np.float32)

    def output(self, layer_id, y0, y1):
        """The layer blended over its backdrop, in its group's context (straight RGBA)."""
        layer = self.by_id.get(layer_id)
        if layer is None:
            return np.zeros((y1 - y0, self.n, 4), np.float32)
        initial, coverage = self._initial(layer.parent_id, y0, y1)
        rgb, coverage = self.compose(layer.parent_id, y0, y1, initial, self._ancestry(layer.parent_id),
                                     coverage, stop_id=layer_id)
        return self._straight(rgb, coverage, y0, y1)

    def group(self, folder_id, y0, y1):
        """A folder's child composite before the folder blends (straight RGBA)."""
        folder = self.by_id.get(folder_id)
        if folder is None or folder.type != 'FOLDER':
            return np.zeros((y1 - y0, self.n, 4), np.float32)
        if folder.group_mode == 'PASSTHROUGH':
            initial, coverage = self.backdrop(folder_id, y0, y1)
        else:
            initial, coverage = (0,) * self.components, 0
        rgb, coverage = self.compose(folder_id, y0, y1, initial, self._ancestry(folder_id) | {folder_id}, coverage)
        return self._straight(rgb, coverage, y0, y1)

    def _straight(self, rgb, coverage, y0, y1):
        rgb, coverage = np.asarray(rgb, np.float32), np.asarray(coverage, np.float32)
        if coverage.ndim == 0 and coverage == 1:
            return self._rows(rgb, 1, y0, y1)
        shape = (y1 - y0, self.n, self.components)
        rgb, cov = np.broadcast_to(rgb, shape), np.broadcast_to(coverage, shape)
        straight = np.divide(rgb, cov, out=np.zeros(shape, np.float32), where=cov > 1e-8)
        return self._rows(straight, np.broadcast_to(coverage, shape[:2] + (1,)), y0, y1)
