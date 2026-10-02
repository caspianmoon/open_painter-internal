"""Scratch returned mid-evaluation stays bounded without evicting active work."""
from collections import OrderedDict
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.evaluation.engine import runtime
from open_painter.evaluation.engine.formats import texture_bytes


def device():
    dev = runtime.Device.__new__(runtime.Device)
    dev.pool, dev._leases = OrderedDict(), {}
    dev._pooled_bytes, dev._pooled_ids = 0, set()
    dev._mip_scratch, dev._mip_scratch_bytes, dev._mip_scratch_owner = {}, 0, None   # trim(0) drops it
    return dev


def texture(n, fmt='RGBA32F'):
    return SimpleNamespace(width=n, height=n, format=fmt)


class TexturePoolTests(unittest.TestCase):
    def assert_accounting(self, dev):
        self.assertTrue(all(dev.pool.values()), 'Empty historical sizes must not accumulate')
        sizes = [texture_bytes(n, n, fmt) for (n, fmt), free in dev.pool.items() for _ in free]
        identities = [id(item) for free in dev.pool.values() for item in free]
        self.assertEqual(dev._pooled_bytes, sum(sizes))
        self.assertEqual(dev._pooled_ids, set(identities))
        self.assertEqual(len(identities), len(dev._pooled_ids))
        self.assertFalse(dev._pooled_ids & dev._leases.keys())
        self.assertLessEqual(dev._pooled_bytes, runtime.POOL_BYTES)
        self.assertTrue(all(len(free) <= runtime.POOL_SPARES for free in dev.pool.values()))

    def test_island_and_undo_size_churn_is_bounded_before_final_trim(self):
        dev = device()
        pinned = texture(4096)
        dev._leases[id(pinned)] = texture_bytes(4096, 4096, 'RGBA32F')
        with patch.object(runtime, 'POOL_BYTES', 32 * 1024 * 1024):
            for n in range(512, 768):
                # A filter can return three scratch textures per island before
                # evaluation's final trim. Each island has a different extent.
                for _ in range(4):
                    item = texture(n)
                    dev._leases[id(item)] = texture_bytes(n, n, item.format)
                    dev.release(item, n, item.format)
                    self.assert_accounting(dev)
            self.assertEqual(dev._leases, {id(pinned): 256 * 1024 * 1024})
            dev.trim(0)
            self.assertEqual(dev.pool, {})
            self.assertEqual(dev._pooled_ids, set())
            self.assertEqual(dev._pooled_bytes, 0)

    def test_reuse_prunes_bucket_and_duplicate_return_cannot_alias_leases(self):
        dev = device()
        item = texture(32)
        dev._leases[id(item)] = texture_bytes(32, 32, item.format)
        dev.release(item, 32)
        dev.release(item, 32)
        self.assert_accounting(dev)
        fake = SimpleNamespace(types=SimpleNamespace(GPUTexture=lambda size, format: texture(size[0], format)))
        with patch.dict(sys.modules, {'gpu': fake}):
            first = dev.texture(32)
            second = dev.texture(32)
        self.assertIs(first, item)
        self.assertIsNot(first, second)
        self.assertEqual(dev.pool, {})
        self.assertEqual(dev._pooled_bytes, 0)
        self.assertEqual(dev._leases[id(first)], texture_bytes(32, 32, first.format))

    def test_oversized_return_does_not_evict_reusable_small_scratch(self):
        dev = device()
        small, big = texture(8), texture(64)
        with patch.object(runtime, 'POOL_BYTES', 4096):
            dev.release(small, 8)
            dev._leases[id(big)] = texture_bytes(64, 64, big.format)
            dev.release(big, 64)
            self.assertEqual(dev.pool, {(8, 'RGBA32F'): [small]})
            self.assertNotIn(id(big), dev._leases)
            self.assert_accounting(dev)

    def test_wrong_size_checkout_and_allocation_failure_leave_exact_accounting(self):
        dev = device()
        wrong = texture(16)
        dev.release(wrong, 8)
        def fail(*args, **kwargs):
            raise RuntimeError('allocation failed')
        fake = SimpleNamespace(types=SimpleNamespace(GPUTexture=fail))
        with patch.dict(sys.modules, {'gpu': fake}), self.assertRaisesRegex(RuntimeError, 'allocation failed'):
            dev.texture(8)
        self.assertEqual(dev.memory(), {'leased_bytes': 0, 'pooled_bytes': 0})
        self.assertEqual(dev.pool, {})
        self.assert_accounting(dev)

    def test_trim_and_lowered_budget_keep_exact_count(self):
        dev = device()
        for n in (8, 16, 24):
            for _ in range(3):
                dev.release(texture(n), n)
        dev.trim(1)
        self.assert_accounting(dev)
        self.assertTrue(all(len(free) == 1 for free in dev.pool.values()))
        with patch.object(runtime, 'POOL_BYTES', 1024):
            dev.trim(3)
            self.assert_accounting(dev)
            self.assertEqual(dev.pool, {})


if __name__ == '__main__':
    unittest.main()
