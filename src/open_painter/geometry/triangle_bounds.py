"""Conservative box queries over an immutable triangle snapshot, using NumPy.

Spatially ordered blocks reject distant triangles before the exact original
per-triangle comparisons. Query results retain original triangle order. Small,
non-finite and broad queries use the direct scan; there is no global cache.
"""
import numpy as np

BLOCK_SIZE = 256
MIN_TRIANGLES = 4096


class TriangleBounds:
    def __init__(self, minimum, maximum):
        self.minimum, self.maximum = np.asarray(minimum), np.asarray(maximum)
        self.order = self.block_minimum = self.block_maximum = None
        self.native = None
        from ..native_bridge.lifecycle import cpu_library
        lib = cpu_library()
        if lib is not None and hasattr(lib, 'op_bounds_create'):
            from ..native_bridge.geometry import Bounds
            self.native = Bounds(lib, self.minimum, self.maximum)
            return
        count = len(self.minimum)
        if count < MIN_TRIANGLES:
            return
        try:
            if not (np.isfinite(self.minimum).all() and np.isfinite(self.maximum).all()):
                return
            # Halve before adding/subtracting so finite extreme coordinates do
            # not overflow while choosing an axis or ordering triangle centres.
            extent = self.maximum.max(axis=0).astype(np.float64) * .5 - self.minimum.min(axis=0).astype(np.float64) * .5
            axis = int(np.argmax(extent))
            centres = self.minimum[:, axis].astype(np.float64) * .5 + self.maximum[:, axis].astype(np.float64) * .5
            order = np.argsort(centres, kind='stable')
            if count <= np.iinfo(np.uint32).max:
                order = order.astype(np.uint32)
            starts = np.arange(0, count, BLOCK_SIZE)
            low = np.minimum.reduceat(self.minimum[order], starts, axis=0)
            high = np.maximum.reduceat(self.maximum[order], starts, axis=0)
            self.order, self.block_minimum, self.block_maximum = order, low, high
        except MemoryError:
            # Acceleration is optional; the existing bounds remain sufficient.
            self.order = self.block_minimum = self.block_maximum = None

    @property
    def extra_bytes(self):
        """Owned acceleration arrays; the caller already owns the input bounds."""
        if self.native is not None:
            return self.native.bytes
        return sum(array.nbytes for array in (self.order, self.block_minimum, self.block_maximum)
                   if array is not None)

    def _scan(self, low, high):
        return np.flatnonzero(np.all(self.minimum <= high, axis=1) & np.all(self.maximum >= low, axis=1))

    def query(self, low, high):
        """Original triangle indices intersecting the inclusive query box."""
        if self.native is not None:
            return self.native.query(low, high)
        if self.order is None:
            return self._scan(low, high)
        blocks = np.flatnonzero(np.all(self.block_minimum <= high, axis=1) &
                                np.all(self.block_maximum >= low, axis=1))
        if not len(blocks):
            return np.zeros(0, np.intp)
        if len(blocks) * BLOCK_SIZE >= len(self.minimum) // 2:
            # A large brush or long/overlapping triangles can touch most blocks.
            # Avoid materializing and sorting a full gathered candidate list.
            return self._scan(low, high)
        positions = (blocks[:, None] * BLOCK_SIZE + np.arange(BLOCK_SIZE)).ravel()
        candidates = self.order[positions[positions < len(self.minimum)]]
        keep = np.all(self.minimum[candidates] <= high, axis=1) & np.all(self.maximum[candidates] >= low, axis=1)
        result = np.asarray(candidates[keep], dtype=np.intp)
        result.sort()
        return result
