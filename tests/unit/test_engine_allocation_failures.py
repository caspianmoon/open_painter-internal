"""Partial GPU allocations remain owned until failure cleanup can release them."""
from collections import OrderedDict
from contextlib import contextmanager, nullcontext
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import weakref
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.evaluation.engine import filters, runtime
from open_painter.evaluation.engine.resources import TextureResource
from open_painter.evaluation import channel_eval
from open_painter.painting import gpu_strokes


@contextmanager
def failed_allocation(which=2):
    dev = runtime.Device.__new__(runtime.Device)
    dev.pool, dev._leases = OrderedDict(), {}
    dev._pooled_bytes, dev._pooled_ids = 0, set()
    dev.max_samplers = 16
    dev.engines = weakref.WeakSet()
    dev.draw = Mock()
    dev.fill = Mock()
    made = []
    calls = 0

    def allocate(size, format, **kwargs):
        nonlocal calls
        calls += 1
        if calls == which:
            raise RuntimeError('injected texture allocation failure')
        item = SimpleNamespace(width=size[0], height=size[1], format=format)
        made.append(item)
        return item

    gpu = SimpleNamespace(types=SimpleNamespace(GPUTexture=allocate,
        Buffer=lambda kind, size, values=None: values if values is not None else np.zeros(size, np.float32)))
    with patch.dict(sys.modules, {'gpu': gpu}), patch.object(runtime, 'device', return_value=dev):
        yield dev, made


class AllocationFailureTests(unittest.TestCase):
    def test_unit_target_failure_releases_partial_batch_and_retries(self):
        with failed_allocation() as (dev, made):
            engine = runtime.Engine(None, SimpleNamespace(id='allocation-test'))
            engine.graph = SimpleNamespace(consumers={})
            state = SimpleNamespace(textures=None, stamp=None, formats=('RGBA32F', 'RG32F'),
                                    shader=object(), program=SimpleNamespace(samplers=()), uniforms=lambda: None)
            key = ('CHANNEL', 'layer', 'NORMAL')
            with patch.object(engine, '_state', return_value=state), patch.object(engine, '_stamp', return_value=1):
                with self.assertRaisesRegex(RuntimeError, 'injected texture allocation failure'):
                    engine._render(key, 16, {}, {})
                self.assertIsNone(state.textures)
                self.assertEqual(dev._leases, {})
                textures = engine._render(key, 16, {}, {})
                self.assertEqual(len(textures), 2)
                self.assertIs(textures[0], made[0])
                for item, fmt in zip(textures, state.formats):
                    dev.release(item, 16, fmt)
            self.assertEqual(dev._leases, {})

    def test_uv_filter_second_scratch_failure_releases_first(self):
        with failed_allocation() as (dev, made), patch.object(filters, 'uniforms', return_value=None):
            effect = SimpleNamespace(kind='BLUR')
            with self.assertRaisesRegex(RuntimeError, 'injected texture allocation failure'):
                filters._apply_uv(object(), effect, 16)
            self.assertEqual(len(made), 1)
            self.assertEqual(dev._leases, {})
            self.assertIn(id(made[0]), dev._pooled_ids)

    def test_surface_filter_second_scratch_failure_releases_map_and_first(self):
        seams = SimpleNamespace(bounds={0: (0, 1, 0, 1)}, halo=2,
            depth=np.array([0]), _nearest=np.array([True]), rows=np.array([0]), cols=np.array([0]),
            source_x=np.array([0]), source_y=np.array([0]))
        with failed_allocation(3) as (dev, made), patch.object(filters, 'program', return_value=None), \
                patch.object(channel_eval, 'seam_map_for', return_value=seams):
            effect = SimpleNamespace(kind='BLUR', space='SURFACE', radius=1.)
            with self.assertRaisesRegex(RuntimeError, 'injected texture allocation failure'):
                filters.apply(object(), effect, 16, ts=object())
            self.assertEqual(len(made), 2)
            self.assertEqual(dev._leases, {})
            self.assertNotIn(id(made[0]), dev._pooled_ids)
            self.assertIn(id(made[1]), dev._pooled_ids)

    def test_canvas_second_scratch_failure_unpins_surface_and_releases_first(self):
        surface = SimpleNamespace(pins=0, pending_close=False, close=Mock())
        previous = set(gpu_strokes._canvases)
        with failed_allocation() as (dev, made):
            with self.assertRaisesRegex(RuntimeError, 'injected texture allocation failure'):
                gpu_strokes.Canvas([], 16, {}, {}, surface=surface)
            self.assertEqual(surface.pins, 0)
            surface.close.assert_not_called()
            self.assertEqual(dev._leases, {})
            self.assertIn(id(made[0]), dev._pooled_ids)
            self.assertEqual(set(gpu_strokes._canvases), previous)

    def test_canvas_initial_copy_failure_discards_pending_target_and_keeps_borrowed_base(self):
        for failed_copy in (1, 2):
            with self.subTest(failed_copy=failed_copy), failed_allocation(99) as (dev, made):
                dev.state = nullcontext
                dev.fixed = Mock()
                copies = 0
                def copy(*args, **kwargs):
                    nonlocal copies
                    copies += 1
                    if copies == failed_copy:
                        raise RuntimeError('injected initialization copy failure')
                dev.draw.side_effect = copy
                base = dev.texture(16, 'RGBA32F')
                resource = TextureResource('borrowed', base, 'RGBA32F')
                before = dict(dev._leases)
                surface = SimpleNamespace(pins=0, pending_close=False, close=Mock())
                with self.assertRaisesRegex(RuntimeError, 'injected initialization copy failure'):
                    gpu_strokes.Canvas([], 16, {'BASE_COLOR': resource, 'EMISSION': resource},
                                       {'BASE_COLOR': False, 'EMISSION': False}, surface=surface)
                self.assertEqual(surface.pins, 0)
                self.assertEqual(dev._leases, before)
                self.assertNotIn(id(made[-1]), dev._pooled_ids)
                self.assertNotIn(id(base), dev._pooled_ids)

    def test_surface_island_upload_and_crop_failure_release_every_partial_resource(self):
        for failed in (6, 7):
            # Map, two scratch targets and result precede the island's lookup,
            # mask and crop; failing mask/crop must release prior island uploads.
            with self.subTest(failed=failed), failed_allocation(failed) as (dev, made), \
                    patch.object(filters, 'program', return_value=None), \
                    patch.object(filters, 'uniforms', return_value=None):
                dev.fixed = Mock()
                seams = SimpleNamespace(bounds={0: (0, 1, 0, 1)}, halo=2,
                    depth=np.array([0]), _nearest=np.array([True]), rows=np.array([0]), cols=np.array([0]),
                    source_x=np.array([0]), source_y=np.array([0]),
                    island_of=np.zeros((16, 16), np.int32), entries=lambda island: np.array([0]))
                effect = SimpleNamespace(kind='BLUR', space='SURFACE', radius=1.)
                with patch.object(channel_eval, 'seam_map_for', return_value=seams), \
                        self.assertRaisesRegex(RuntimeError, 'injected texture allocation failure'):
                    filters.apply(object(), effect, 16, ts=object())
                self.assertEqual(len(made), failed - 1)
                self.assertEqual(dev._leases, {})
                for item in made[4:]:
                    self.assertNotIn(id(item), dev._pooled_ids)


if __name__ == '__main__':
    unittest.main()
