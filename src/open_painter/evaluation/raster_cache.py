"""Bound transient texture memoization without changing texture resolution."""
from collections import OrderedDict
from . import metrics, memory
import weakref
from .resource_keys import key_for

_shared = {}
# Rasters every strip of a job reads; retained for the job when they exceed the allowance.
REQUIRED = ('geometry', 'tangents')


def for_texture_set(scene, texture_set, force=False):
    """Keep reusable CPU textures between edits, under one shared allowance."""
    from . import dependencies
    key = key_for(scene, texture_set)
    cache = _shared.setdefault(key, RasterCache())
    if force:
        cache.clear()
    geometry_stamp = dependencies.geometry_key(scene, texture_set)
    if getattr(cache, "geometry_stamp", None) != geometry_stamp:
        if hasattr(cache, "geometry_stamp"):
            from ..core.layer_stack import invalidate
            invalidate(texture_set)
        for key in REQUIRED:
            cache.pop(key, None)
        cache.geometry_stamp = geometry_stamp
    dependencies.remember(scene, texture_set, geometry_stamp)
    stamp = (texture_set.edit_serial, int(texture_set.resolution), geometry_stamp)
    if getattr(cache, 'stamp', None) != stamp:
        # Authoring edits invalidate memoized parameter lookups, not geometry.
        cache.signatures = {}
        cache.stamp = stamp
    return cache


def clear_shared():
    for cache in _shared.values():
        cache.clear()
    _shared.clear()


def prune(live):
    """Drop closed/deleted scene texture sets; called by the preview scheduler."""
    for key in set(_shared) - set(live):
        _shared.pop(key).clear()


def byte_size(value):
    return sum(memory.allocations(value).values())


def _gauge_required():
    if metrics is None or _shared is None:  # Blender may tear down modules before cache finalizers.
        return
    metrics.gauge('cache_required_geometry_bytes', sum(
        byte_size(cache.required) for cache in _shared.values()))


class RasterCache(OrderedDict):
    """Disposable memoization. Oversized values remain caller-owned, uncached."""
    def __init__(self, budget=None):
        super().__init__()
        self.manager = memory.shared if budget is None else memory.MemoryBudget(budget)
        self.signatures = {}
        self.required = {}

    @property
    def used(self):
        roots = {}
        for value in self.values():
            roots.update(memory.allocations(value))
        return sum(roots.values())

    @property
    def budget(self):
        return self.manager.allowance

    def __contains__(self, key):
        found = key in self.required or super().__contains__(key)
        metrics.count("cache_hits" if found else "cache_misses")
        return found

    def __getitem__(self, key):
        if key in self.required:
            return self.required[key]
        value = super().__getitem__(key)
        self.move_to_end(key)
        self.manager.touch((id(self), key))
        return value

    def __setitem__(self, key, value):
        self.pop(key, None)
        ref = weakref.ref(self)
        def evict():
            cache = ref()
            if cache is not None and OrderedDict.__contains__(cache, key):
                OrderedDict.__delitem__(cache, key)
        if self.manager.admit((id(self), key), value, evict):
            super().__setitem__(key, value)
            metrics.count("cache_insert_bytes", byte_size(value))
        elif key in REQUIRED:
            # Required for every strip: retain once for the current job, outside
            # disposable cache allowance, instead of rebuilding per strip.
            self.required[key] = value
            _gauge_required()

    def release_required(self):
        had_required = bool(self.required)
        self.required = {}
        if had_required:
            _gauge_required()

    def clear(self):
        self.release_required()
        for key in tuple(self):
            self.manager.remove((id(self), key))
        super().clear()
        self.signatures = {}

    def __delitem__(self, key):
        super().__delitem__(key)
        self.manager.remove((id(self), key))

    def pop(self, key, *default):
        if key in self.required:
            value = self.required.pop(key)
            _gauge_required()
            return value
        if not OrderedDict.__contains__(self, key):
            if default:
                return default[0]
            raise KeyError(key)
        value = super().__getitem__(key)
        del self[key]
        return value

    def __del__(self):
        self.clear()
