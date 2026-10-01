"""The collector policy freezes what an edit kept once edits pause, and sweeps frozen cycles later."""

import gc
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from open_painter.evaluation import gc_policy


class Cycle:
    def __init__(self):
        self.me = self


class GcPolicyTest(unittest.TestCase):
    def setUp(self):
        gc_policy.release()

    def tearDown(self):
        gc_policy.release()

    def test_settles_only_after_a_build_and_a_pause(self):
        self.assertIsNone(gc_policy.settle(now=gc_policy._state['active'] + 10))   # nothing was built
        gc_policy.allocated()
        began = gc_policy._state['active']
        self.assertIsNone(gc_policy.settle(now=began + gc_policy.SETTLE_DELAY / 2))   # still editing
        self.assertEqual(gc_policy.settle(now=began + gc_policy.SETTLE_DELAY), 'settled')
        self.assertGreater(gc.get_freeze_count(), 0)
        self.assertIsNone(gc_policy.settle(now=began + gc_policy.SETTLE_DELAY * 2))   # once per build

    def test_young_garbage_is_collected_before_freezing(self):
        garbage = Cycle()
        marker = id(garbage)
        del garbage
        gc_policy.allocated()
        gc_policy.settle(now=gc_policy._state['active'] + gc_policy.SETTLE_DELAY)
        self.assertNotIn(marker, {id(item) for item in gc.get_objects(generation=2)})
        frozen_cycles = [item for item in gc.get_objects() if isinstance(item, Cycle)]
        self.assertEqual(frozen_cycles, [])

    def test_frozen_objects_are_still_freed_by_reference_counting(self):
        kept = [object() for _ in range(10)]
        gc_policy.allocated()
        gc_policy.settle(now=gc_policy._state['active'] + gc_policy.SETTLE_DELAY)
        frozen = gc.get_freeze_count()
        del kept
        self.assertLess(gc.get_freeze_count(), frozen)

    def test_a_frozen_cycle_waits_for_the_sweep(self):
        gc_policy.allocated()
        gc_policy.settle(now=gc_policy._state['active'] + gc_policy.SETTLE_DELAY)
        cycle = Cycle()
        gc_policy.allocated()
        gc_policy.settle(now=gc_policy._state['active'] + gc_policy.SETTLE_DELAY)
        del cycle
        gc.collect()
        self.assertEqual(sum(isinstance(item, Cycle) for item in gc.get_objects()), 0)   # frozen: not listed
        swept = gc.get_freeze_count()
        gc_policy.sweep()
        self.assertLess(gc.get_freeze_count(), swept)

    def test_idle_sweep_needs_growth_and_a_long_pause(self):
        gc_policy.sweep()
        baseline = gc_policy._state['swept']
        grown = [[index] for index in range(max(gc_policy.SWEEP_MINIMUM, int(baseline * .3)) + 10)]
        gc_policy.allocated()
        began = gc_policy._state['active']
        self.assertEqual(gc_policy.settle(now=began + gc_policy.SETTLE_DELAY), 'settled')
        self.assertIsNone(gc_policy.settle(now=began + gc_policy.SWEEP_IDLE / 2))
        self.assertEqual(gc_policy.settle(now=began + gc_policy.SWEEP_IDLE), 'swept')
        self.assertIsNone(gc_policy.settle(now=began + gc_policy.SWEEP_IDLE * 2))   # no growth since
        del grown

    def test_a_reset_sweeps_at_the_next_pause(self):
        gc_policy.allocated()
        gc_policy.settle(now=gc_policy._state['active'] + gc_policy.SETTLE_DELAY)
        cycle = Cycle()
        gc_policy.allocated()
        gc_policy.settle(now=gc_policy._state['active'] + gc_policy.SETTLE_DELAY)
        del cycle
        frozen = gc.get_freeze_count()
        gc_policy.release(defer=True)   # sessions dropped: nothing is walked now
        self.assertEqual(gc.get_freeze_count(), frozen)
        began = gc_policy._state['active']
        self.assertIsNone(gc_policy.settle(now=began + gc_policy.SETTLE_DELAY / 2))
        self.assertEqual(gc_policy.settle(now=began + gc_policy.SETTLE_DELAY), 'swept')
        self.assertLess(gc.get_freeze_count(), frozen)
        self.assertIsNone(gc_policy.settle(now=began + gc_policy.SETTLE_DELAY * 2))

    def test_release_unfreezes(self):
        gc_policy.allocated()
        gc_policy.settle(now=gc_policy._state['active'] + gc_policy.SETTLE_DELAY)
        gc_policy.release()
        self.assertEqual(gc.get_freeze_count(), 0)


if __name__ == "__main__":
    unittest.main()
