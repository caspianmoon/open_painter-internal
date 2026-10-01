"""Native surface snapshots/anchors compared with independent NumPy semantics."""
import copy
import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.native_bridge import load_library, lifecycle
from open_painter.native_bridge.reach import Reach, choose
from open_painter.painting import surface_reach
from test_surface_reach import grid, target_of, sample, anchor


@unittest.skipUnless(os.environ.get('OP_TEST_NATIVE_LIBRARY'), 'native build not supplied')
class NativeReachTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])

    def test_owned_snapshot_anchor_validation_and_nearest(self):
        rng = np.random.default_rng(7384)
        target = target_of([grid(48, 48), grid(8, 8, z=.01, polygon_offset=2304)])
        for mirrored in (False, True):
            target['mirrored'] = mirrored
            with patch.object(lifecycle, 'cpu_library', return_value=None):
                reference = surface_reach.prepare(copy.deepcopy(target))
            snapshot = Reach(self.lib, target)
            actual = snapshot.snapshot()
            for key in ('corners', 'normals', 'lo', 'hi', 'order', 'sorted_polygons'):
                np.testing.assert_array_equal(actual[key], reference[key], err_msg=key)
            anchors = rng.uniform(-.05, 1.05, (280, 4)).astype(np.float32)
            anchors[:, 2] = rng.choice([0, .01, .2], len(anchors))
            anchors[:, 3] = (np.clip(anchors[:, 1] * 48, 0, 47).astype(int) * 48
                             + np.clip(anchors[:, 0] * 48, 0, 47).astype(int))
            anchors[::4, 3] = -1
            axes = np.tile((0, 0, -1 if mirrored else 1), (len(anchors), 1)).astype(np.float32)
            radii = rng.uniform(.01, .2, len(anchors)).astype(np.float32)
            wanted = rng.random(len(anchors)) > .3
            with patch.object(lifecycle, 'cpu_library', return_value=None):
                valid = surface_reach.validate(reference, anchors, radii)
                nearest = surface_reach.nearest(reference, anchors, axes, radii, wanted)
                seeds = surface_reach.seeds(reference, anchors, axes, radii, radii, wanted)
            for a, b in zip(snapshot.pairs(anchors, radii), valid):
                np.testing.assert_array_equal(a, b)
            for a, b in zip(snapshot.nearest(anchors, axes, radii, wanted), nearest):
                np.testing.assert_allclose(a, b, rtol=0, atol=2e-14)
            tri, samples, resolved = snapshot.pairs(anchors, radii, axes, radii, wanted)
            for a, b in zip((tri, samples, np.flatnonzero(~resolved)), seeds):
                np.testing.assert_array_equal(a, b)
            # Neither mutable source arrays nor exported copies alter the native authority.
            actual['corners'][:] = 999
            np.testing.assert_array_equal(snapshot.snapshot()['corners'], reference['corners'])
            snapshot.close()

    def test_full_multitarget_masks_chunk_boundaries_and_stale_anchors(self):
        entries = [target_of([grid(16, 16, z=z)]) for z in (0., .002)]
        samples = np.asarray([sample((.3, .4, .002), radius=.12)] * 259)
        anchors = np.asarray([anchor((.3, .4, .002), 6 * 16 + 4)] * len(samples))
        for hit in (anchors, None, anchors + np.array([0, 0, 0, 100000])):
            with patch.object(lifecycle, 'cpu_library', return_value=None):
                reference = surface_reach.for_targets(copy.deepcopy(entries), samples, hit)
                expected = [s.mask(np.arange(512), 254, 259) for s in reference]
            with patch.object(lifecycle, 'cpu_library', return_value=self.lib), \
                    patch('open_painter.native_bridge.paint.cpu_library', return_value=self.lib):
                native = surface_reach.for_targets(copy.deepcopy(entries), samples, hit)
                actual = [s.mask(np.arange(512), 254, 259) for s in native]
            for a, b in zip(actual, expected):
                np.testing.assert_array_equal(a, b)
            self.assertFalse(actual[0].any())
            self.assertTrue(actual[1].any())

    def test_target_selection_ties_and_empty_inputs(self):
        valid = np.array([[True, False, False], [False, False, False]])
        distances = np.array([[.1, .2, np.inf], [0, .2, np.inf]])
        np.testing.assert_array_equal(choose(self.lib, valid, distances), [[False, True, False], [False, False, False]])
        self.assertEqual(choose(self.lib, np.empty((0, 3)), np.empty((0, 3))).shape, (0, 3))
        snapshot = Reach(self.lib, dict(positions=np.empty((0, 3)), all_tri_vertices=np.empty((0, 3)), all_tri_polygons=[]))
        self.assertEqual(len(snapshot.pairs(np.empty((0, 4)), [])[0]), 0)
        snapshot.close()
