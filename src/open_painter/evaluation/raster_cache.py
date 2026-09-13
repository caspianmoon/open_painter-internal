"""Bound transient texture memoization without changing texture resolution."""
from collections import OrderedDict
from . import metrics
from .resource_keys import key_for

_shared = {}


def for_texture_set(scene, texture_set, force=False):
    """Keep reusable CPU textures between edits, with independent per-set budgets."""
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
        cache.pop("geometry", None)
        cache.geometry_stamp = geometry_stamp
    dependencies.remember(scene, texture_set, geometry_stamp)
    stamp = (texture_set.edit_serial, int(texture_set.resolution), geometry_stamp)
    if getattr(cache, 'stamp', None) != stamp:
        # Authoring edits invalidate memoized parameter lookups, not geometry.
        cache.signatures = {}
        cache.stamp = stamp
    return cache


def clear_shared():
    _shared.clear()


def prune(live):
    """Drop closed/deleted scene texture sets; called by the preview scheduler."""
    for key in set(_shared) - set(live):
        _shared.pop(key, None)


def byte_size(value):
    if hasattr(value, 'nbytes'):
        return value.nbytes
    if isinstance(value, (tuple,list)):
        return sum(byte_size(item) for item in value)
    return 0


class RasterCache(OrderedDict):
    """LRU budget; a single required raster may exceed the budget.

    Geometry is kept separately because rerasterizing mesh positions every tile
    is more expensive than texture operations. The budget covers reusable
    procedural/mask results, not output images or the current filter workspace.
    """
    def __init__(self, budget=256 * 1024 * 1024):
        super().__init__()
        self.budget = budget
        self.used = 0

    def __contains__(self, key):
        found = super().__contains__(key)
        metrics.count("cache_hits" if found else "cache_misses")
        return found

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        if key in self:
            self.used -= byte_size(super().__getitem__(key)) if key != 'geometry' else 0
            super().__delitem__(key)
        cost = byte_size(value) if key != 'geometry' else 0
        while self.used + cost > self.budget:
            victim = next((k for k in self if k != 'geometry'),None)
            if victim is None:
                break
            self.used -= byte_size(super().__getitem__(victim))
            super().__delitem__(victim)
            metrics.count("cache_evictions")
        metrics.count("cache_insert_bytes", cost)
        super().__setitem__(key,value)
        self.used += cost
        metrics.gauge("cache_scalar_bytes", sum(c.used for c in _shared.values()))
        metrics.gauge("cache_geometry_bytes", sum(byte_size(OrderedDict.get(c, "geometry")) for c in _shared.values()))

    def clear(self):
        super().clear()
        self.used=0
        self.signatures = {}

    def __delitem__(self, key):
        if key != 'geometry':
            self.used -= byte_size(super().__getitem__(key))
        super().__delitem__(key)

    def pop(self, key, *default):
        if key not in self:
            if default:
                return default[0]
            raise KeyError(key)
        value = super().__getitem__(key)
        del self[key]
        return value
