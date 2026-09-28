"""Evaluate a proven spatially constant channel with the ordinary GPU shaders."""

from collections import Counter
from copy import copy
from time import perf_counter

from .. import metrics
from . import stages


class ConstantRenderer:
    """One-texel state independent of the parent engine's full-size textures.

    The caller proves eligibility and handles output image publication. This
    helper deliberately never calls ``Engine.evaluate``: it must not publish to
    a Blender image, invoke itself recursively, or use the final-pass shortcut.
    """

    def __init__(self):
        self.child = None
        self._graph = None
        self._buffer = None
        self.rendered = []
        self.stages = stages.evaluation()

    def close(self):
        """Release only the child's one-texel leases, including after failure."""
        if self.child is not None:
            self.child.close(1)
        self.child = None
        self._graph = None
        self._buffer = None
        self.rendered = []
        self.stages = stages.evaluation()

    def evaluate(self, parent, channel, epochs=None):
        """Return final RGBA using the parent's programs and intermediate formats.

        ``rendered`` and ``stages`` describe this call for the parent to merge.
        Its graph and UnitState objects are never resized or mutated. A dedicated
        16-byte buffer avoids evicting Device's reusable full-resolution buffer.
        """
        import gpu
        import numpy as np
        from .runtime import Engine, HALF, RGBA, device

        if parent.graph is None:
            raise ValueError('Constant evaluation requires an adopted graph')
        if self.child is None:
            self.child = Engine(parent.scene, parent.ts())
        child = self.child
        child.precise = parent.precise
        child.pad = False
        if self._graph is not parent.graph:
            graph = copy(parent.graph)
            graph.n = 1
            child.adopt(graph)
            self._graph = parent.graph
        child.stages = self.stages = stages.evaluation()
        child.rendered = self.rendered = []
        child._in_use = Counter()
        child._surface_key = None
        child.profiling = False
        epochs = epochs or {}
        dev = device()
        fmt = RGBA if parent.precise or channel == 'HEIGHT' else HALF
        target = None
        with child._evaluation_states(), dev.state():
            try:
                # Plain fills contain no coordinate-dependent operations, but
                # their layer boundaries still round through the real formats.
                source = child._texture_of(child.finals[channel], 1, {}, epochs)
                started = perf_counter()
                target = dev.texture(1, fmt)
                dev.draw(dev.fixed('publish_pixels'), [target], 1,
                         [('op_source', source), ('op_donor', source)],
                         ints={'op_mode': int(channel == 'NORMAL'), 'op_pad': 0,
                               'op_scalar': int(source.format in {'RG16F', 'RG32F'})})
                if self._buffer is None:
                    self._buffer = gpu.types.Buffer('FLOAT', 4)
                framebuffer = gpu.types.GPUFrameBuffer(color_slots=(target,))
                with framebuffer.bind():
                    framebuffer.read_color(0, 0, 1, 1, 4, 0, 'FLOAT', data=self._buffer)
                metrics.count('engine_texture_readbacks')
                metrics.count('engine_texture_readback_bytes', 16)
                metrics.count('engine_constant_evaluations')
                self.stages['publish_ms'] += (perf_counter() - started) * 1000
                return tuple(float(value) for value in np.frombuffer(self._buffer, np.float32))
            finally:
                if target is not None:
                    dev.release(target, 1, fmt)
