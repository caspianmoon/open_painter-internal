"""Coverage and topology regressions for opt-in material damage sources.

Legacy procedural numerical behavior is separately checked against the frozen
reference. These tests check the defects these sources are meant to avoid:
round-ended abrasion, closed cell networks and soft cloud-like paint loss.
"""

from collections import deque
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from open_painter.texture_engine import procedurals


def coordinates(size):
    v, u = np.indices((size, size), dtype=np.float32)
    return (u + .5) / size, (v + .5) / size


def component_sizes(binary):
    """Small pure-NumPy/Python diagnostic; no SciPy runtime dependency."""
    remaining = binary.copy()
    height, width = binary.shape
    result = []
    for y, x in np.argwhere(binary):
        if not remaining[y, x]:
            continue
        queue = deque(((int(y), int(x)),))
        remaining[y, x] = False
        count = 0
        while queue:
            qy, qx = queue.popleft()
            count += 1
            for ny, nx in ((qy - 1, qx), (qy + 1, qx), (qy, qx - 1), (qy, qx + 1)):
                if 0 <= ny < height and 0 <= nx < width and remaining[ny, nx]:
                    remaining[ny, nx] = False
                    queue.append((ny, nx))
        result.append(count)
    return sorted(result)


class SurfaceSourceTests(unittest.TestCase):
    SOURCES = ("TAPERED_SCRATCHES", "FINE_FISSURES", "FRACTURED_PAINT")

    def test_sources_are_finite_deterministic_and_strip_independent(self):
        u, v = coordinates(73)
        u, v = u * 1.7 - .35, v * 1.9 - .48
        for source in self.SOURCES:
            for seed in (17, -2 ** 35 + 19):
                with self.subTest(source=source, seed=seed):
                    kwargs = dict(seed=seed, scale=9.7, angle=-38)
                    full = procedurals.evaluate(source, u, v, **kwargs)
                    strips = np.concatenate([procedurals.evaluate(source, u[a:b], v[a:b], **kwargs)
                                             for a, b in ((0, 1), (1, 16), (16, 48), (48, 73))])
                    np.testing.assert_array_equal(full, strips)
                    self.assertEqual(full.dtype, np.float32)
                    self.assertTrue(np.all(np.isfinite(full)))
                    self.assertGreaterEqual(float(full.min()), 0)
                    self.assertLessEqual(float(full.max()), 1)
                    self.assertGreater(float(full.std()), .005)
                    empty = procedurals.evaluate(source, u, v, density=0, **kwargs)
                    self.assertFalse(np.any(empty))

    def test_abrasion_tapers_in_width_and_pressure_to_its_ends(self):
        across = np.linspace(-.06, .06, 601, dtype=np.float32)
        center = procedurals._tapered_trace(np.zeros_like(across), across, .5, .04, 0)
        near_tip = procedurals._tapered_trace(np.full_like(across, .46), across, .5, .04, 0)
        outside = procedurals._tapered_trace(np.full_like(across, .501), across, .5, .04, 0)
        self.assertFalse(np.any(outside), "A scratch must not retain a round capsule cap")
        self.assertGreater(np.count_nonzero(center > .1), np.count_nonzero(near_tip > .1) * 3)
        self.assertLess(float(near_tip.max()), float(center.max()) * .5)
        u, v = coordinates(512)
        abrasion = procedurals.evaluate("TAPERED_SCRATCHES", u, v, seed=17, scale=12)
        self.assertLess(float((abrasion > .1).mean()), .01, "Abrasion must remain sparse")

    def test_fissures_leave_a_connected_surface_instead_of_closed_cell_outlines(self):
        u, v = coordinates(320)
        fissures = procedurals.evaluate("FINE_FISSURES", u, v, seed=17, scale=8, width=.032)
        unbroken_surface = fissures < .15
        components = component_sizes(unbroken_surface)
        self.assertGreater(components[-1] / unbroken_surface.sum(), .98,
                           "Closed cellular crack networks divide the intact surface into islands")
        self.assertLess(float((fissures > .15).mean()), .04)
        self.assertGreater(float((fissures > .15).mean()), .002)

    def test_paint_fragments_have_solid_interiors_sharp_edges_and_varied_sizes(self):
        u, v = coordinates(384)
        fragments = procedurals.evaluate("FRACTURED_PAINT", u, v, seed=17, scale=11)
        solid = fragments > .5
        self.assertGreater(float(solid.mean()), .15)
        self.assertLess(float(solid.mean()), .36)
        self.assertLess(float(((fragments > .05) & (fragments < .95)).mean()), .05,
                        "Paint-loss boundaries must not become broad smooth color blobs")
        sizes = [size for size in component_sizes(solid) if size > 4]
        self.assertGreater(len(sizes), 20)
        self.assertGreater(sizes[-1] / sizes[0], 15, "Fragments need more than one uniform size")
        self.assertLess(sizes[-1] / fragments.size, .07, "One chip must not swallow the UV tile")


if __name__ == "__main__":
    unittest.main()
