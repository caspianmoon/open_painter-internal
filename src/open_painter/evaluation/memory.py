"""Process-wide allowance for disposable CPU caches, distinct from Blender RAM.

Views retain their owning allocation. Multiple references to that allocation
count once. Eviction drops memoization only: callers retain their working value.
"""
from collections import OrderedDict
from . import metrics


def allocations(value):
    result = {}
    def visit(item):
        if hasattr(item, 'nbytes'):
            root = item
            while getattr(root, 'base', None) is not None:
                root = root.base
            if isinstance(root, memoryview):
                root = root.obj
            try:
                size = memoryview(root).nbytes
            except TypeError:
                size = getattr(root, 'nbytes', item.nbytes)
            result[id(root)] = int(size)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, (tuple, list)):
            for child in item:
                visit(child)
    visit(value)
    return result


class MemoryBudget:
    def __init__(self, allowance=256 * 1024 * 1024):
        self.allowance = allowance
        self.entries = OrderedDict()
        self.owners = {}
        self.used = 0

    def remove(self, token):
        entry = self.entries.pop(token, None)
        if entry is None:
            return
        roots, _, _ = entry
        for identity, size in roots.items():
            self.owners[identity][1] -= 1
            if not self.owners[identity][1]:
                del self.owners[identity]
                self.used -= size
        if metrics is not None:  # Blender may tear down modules before cache finalizers.
            metrics.gauge('cache_controlled_bytes', self.used)

    def touch(self, token):
        if token in self.entries:
            self.entries.move_to_end(token)

    def configure(self, allowance):
        self.allowance = max(0, int(allowance))
        while self.entries and self.used > self.allowance:
            token, (_, callback, _) = next(iter(self.entries.items()))
            self.remove(token)
            callback()

    def admit(self, token, value, evict):
        self.remove(token)
        roots = allocations(value)
        if sum(roots.values()) > self.allowance:
            metrics.count('cache_oversized_skips')
            return False
        def extra():
            return sum(size for identity, size in roots.items() if identity not in self.owners)
        while self.entries and self.used + extra() > self.allowance:
            victim, (_, callback, _) = next(iter(self.entries.items()))
            self.remove(victim)
            callback()
            metrics.count('cache_evictions')
        self.entries[token] = roots, evict, value
        for identity, size in roots.items():
            if identity not in self.owners:
                self.owners[identity] = [size, 0]
                self.used += size
            self.owners[identity][1] += 1
        metrics.gauge('cache_controlled_bytes', self.used)
        return True


shared = MemoryBudget()
