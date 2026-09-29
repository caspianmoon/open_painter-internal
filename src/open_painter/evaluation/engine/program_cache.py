"""Bounded shared shader reuse; live unit owners keep evicted shaders valid."""
from collections import OrderedDict


class ProgramCache:
    def __init__(self, budget=32 * 1024 * 1024, minimum=128 * 1024):
        self.budget, self.minimum = budget, minimum
        self.entries = OrderedDict()
        self.policy = None
        self.bytes = 0

    def _policy(self):
        from ...native_bridge.lifecycle import active_library
        lib = active_library()
        if self.policy is None and lib is not None:
            from ...native_bridge.cache import Cache
            self.policy = Cache(lib)
            for key, (_, cost) in self.entries.items():
                self.policy.put(key, cost)
        return self.policy

    def get(self, key):
        value = self.entries.get(key)
        if value is not None:
            self.entries.move_to_end(key)
            policy = self._policy()
            if policy is not None:
                policy.touch(key)
            return value[0]
        return None

    def put(self, key, shader, source_bytes):
        cost = max(self.minimum, source_bytes)
        policy = self._policy()
        if policy is not None:
            policy.put(key, cost)
        previous = self.entries.pop(key, None)
        self.bytes += cost - (previous[1] if previous else 0)
        self.entries[key] = shader, cost
        if policy is not None:
            victims = policy.trim(self.budget)
        else:
            total, victims = self.bytes, []
            for candidate, (_, charge) in self.entries.items():
                if total <= self.budget: break
                victims.append(candidate)
                total -= charge
        retired = []
        for candidate in victims:
            value, charge = self.entries.pop(candidate)
            self.bytes -= charge
            retired.append(value)
        return retired

    def __len__(self):
        return len(self.entries)

    def close(self):
        if self.policy is not None:
            self.policy.close()
            self.policy = None
        self.entries.clear()
        self.bytes = 0
