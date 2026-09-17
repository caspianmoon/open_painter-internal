import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import unittest
from types import SimpleNamespace
import numpy as np
from open_painter.texture_engine import filters
from open_painter.evaluation.operation_contracts import footprint, stack_footprint
from open_painter.evaluation.tiles import slices
from open_painter.evaluation.memory import MemoryBudget, allocations


class Footprints(unittest.TestCase):
    def test_cache_release_after_metrics_module_teardown(self):
        from open_painter.evaluation import memory
        from open_painter.evaluation.raster_cache import RasterCache
        cache = RasterCache(budget=128)
        cache['value'] = np.zeros(16, np.float32)
        manager = cache.manager
        saved = memory.metrics
        try:
            memory.metrics = None
            cache.clear()
        finally:
            memory.metrics = saved
        self.assertEqual(manager.used, 0)

    def test_strips_match_full_filters(self):
        data = np.random.default_rng(84).random((39, 27), dtype=np.float32)
        for kind in ('LEVELS', 'INVERT', 'BLUR', 'GAUSSIAN_BLUR', 'HIGH_PASS',
                     'SHARPEN', 'SMOOTH_MASK', 'DILATE', 'ERODE', 'EDGE_DETECT', 'HEIGHT_TO_NORMAL'):
            expected = filters.apply(data, kind, radius=1.7)
            for rows in (1, 7, 16, 39):
                actual = np.empty_like(expected)
                for target, source, crop in slices(len(data), rows, footprint(kind, 1.7)):
                    actual[target] = filters.apply(data[source], kind, radius=1.7)[crop]
                np.testing.assert_allclose(actual, expected, atol=1e-7, rtol=1e-6,
                                           err_msg=f'{kind}, {rows} rows')

    def test_wide_strips_match_full_filters(self):
        # FFT and running-sum blurs stay shift-invariant within the footprint halo.
        data = np.random.default_rng(85).random((300, 41, 4), dtype=np.float32)
        for kind, radius in (('GAUSSIAN_BLUR', 25.), ('BLUR', 60.), ('HIGH_PASS', 30.),
                             ('SHARPEN', 10.), ('SMOOTH_MASK', 12.)):
            expected = filters.apply(data, kind, radius=radius)
            for rows in (16, 128, 300):
                actual = np.empty_like(expected)
                for target, source, crop in slices(len(data), rows, footprint(kind, radius)):
                    actual[target] = filters.apply(data[source], kind, radius=radius)[crop]
                np.testing.assert_allclose(actual, expected, atol=1e-6, rtol=0,
                                           err_msg=f'{kind}, {rows} rows')

    def test_composed_halos_and_unknown(self):
        def effect(kind, radius=2, enabled=True):
            return SimpleNamespace(kind=kind, radius=radius, enabled=enabled, strength=1)
        self.assertEqual(stack_footprint([effect('BLUR'), effect('GAUSSIAN_BLUR')]), 8)
        self.assertEqual(stack_footprint([effect('WARP', enabled=False)]), 0)
        self.assertIsNone(stack_footprint([effect('WARP')]))
        self.assertIsNone(footprint('CUSTOM'))

    def test_shared_ownership_and_budget(self):
        data = np.ones(16, np.float32)
        self.assertEqual(sum(allocations((data, data[:1])).values()), 64)
        backing = bytearray(128)
        self.assertEqual(sum(allocations(np.frombuffer(backing, np.float32)[:1]).values()), 128)
        budget = MemoryBudget(128)
        evicted = []
        self.assertTrue(budget.admit(('scene1', 'a'), data, lambda: evicted.append('a')))
        self.assertTrue(budget.admit(('scene2', 'b'), data[:1], lambda: evicted.append('b')))
        self.assertEqual(budget.used, 64)
        self.assertTrue(budget.admit(('scene3', 'c'), data.copy(), lambda: evicted.append('c')))
        self.assertFalse(budget.admit('large', np.ones(33, np.float32), lambda: None))
        self.assertTrue(budget.admit('new', data.copy(), lambda: None))
        self.assertEqual(evicted, ['a', 'b'])
        self.assertEqual(budget.used, 128)
        for key in tuple(budget.entries):
            budget.remove(key)
        self.assertEqual(budget.used, 0)
