"""Optional GPU region history. Eviction only removes acceleration, never document undo."""
from collections import OrderedDict
from dataclasses import dataclass

from ..evaluation.engine import runtime
from ..evaluation.engine.formats import texture_bytes
from .gpu_strokes import program, uniforms

COPY_REGION = r'''
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy) + ivec2(op_params.p[0].xy);
  op_out0 = texelFetch(op_source, p, 0);
}
'''


@dataclass
class Patch:
    region: tuple
    size: int
    format: str
    texture: object

    @property
    def bytes(self):
        return texture_bytes(self.size, self.size, self.format)

    def close(self):
        if self.texture is not None:
            runtime.device().release(self.texture, self.size, self.format)
            self.texture = None


def capture(resource, region):
    x, y, width, height = region
    size = max(width, height)
    dev = runtime.device()
    texture = dev.texture(size, resource.format)
    shader = program('region', COPY_REGION, ['op_source'], 1, ())
    try:
        with dev.state():
            dev.draw(shader, [texture], size, [('op_source', resource.texture)], uniforms([(x, y, 0, 0)]),
                     region=(0, 0, width, height))
    except Exception:
        dev.release(texture, size, resource.format)
        raise
    return Patch(region, size, resource.format, texture)


def optional_capture(resource, region):
    """Snapshot allocation is optional acceleration, never a failed paint commit."""
    try:
        return capture(resource, region)
    except Exception:
        from ..utils.logging import logger
        logger.debug('GPU undo snapshot unavailable; document replay remains authoritative', exc_info=True)
        return None


def restore(patch, resource):
    dev = runtime.device()
    x, y, _, _ = patch.region
    shader = program('region', COPY_REGION, ['op_source'], 1, ())
    with dev.state():
        dev.draw(shader, [resource.texture], resource.dimensions[0], [('op_source', patch.texture)],
                 uniforms([(-x, -y, 0, 0)]), region=patch.region)
    resource.touch()


class History:
    def __init__(self):
        self.entries = OrderedDict()

    @property
    def bytes(self):
        return sum(a.bytes + b.bytes for a, b in self.entries.values())

    def put(self, before, after, first, last, budget):
        key = before, after
        previous = self.entries.pop(key, None)
        if previous:
            for patch in previous:
                patch.close()
        self.entries[key] = first, last
        self.trim(budget)

    def trim(self, budget):
        from ..native_bridge.cache import select_victims
        for key in select_victims(self.entries, budget, lambda value: sum(patch.bytes for patch in value)):
            patches = self.entries.pop(key)
            from ..evaluation import metrics
            metrics.count('stroke_undo_evictions')
            for patch in patches:
                patch.close()

    def move(self, current, desired, resource):
        # Only exact adjacent document states may restore a cached patch.
        key, index = ((current, desired), 1) if (current, desired) in self.entries else ((desired, current), 0)
        patches = self.entries.get(key)
        if patches is None:
            return False
        restore(patches[index], resource)
        self.entries.move_to_end(key)
        return True

    def close(self):
        self.trim(0)
