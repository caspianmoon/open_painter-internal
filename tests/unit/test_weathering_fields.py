"""Weathering continuity, growth and material-stack invariants."""
import sys
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.texture_engine.procedurals import evaluate
from open_painter.aging import document, model
from open_painter.aging.environments import ENVIRONMENTS, MAINTENANCE, USAGE

KINDS = ('WEATHERING_FRONT', 'CORROSION_GRAIN', 'RUNOFF_TRAILS')


class WeatheringTests(unittest.TestCase):
    def test_strip_evaluation_matches_whole_field_with_negative_coordinates(self):
        u, v = np.meshgrid(np.linspace(-1.3, 2.1, 77, dtype=np.float32),
                           np.linspace(-.7, 1.8, 69, dtype=np.float32))
        for kind in KINDS:
            with self.subTest(kind=kind):
                whole = evaluate(kind, u, v, seed=317, scale=6.7)
                strips = np.concatenate([evaluate(kind, u[i:i+11], v[i:i+11], seed=317, scale=6.7)
                                          for i in range(0, len(v), 11)])
                np.testing.assert_array_equal(whole, strips)
                self.assertTrue(np.isfinite(whole).all())
                self.assertGreater(float(whole.std()), .025)
                self.assertGreaterEqual(float(whole.min()), 0)
                self.assertLessEqual(float(whole.max()), 1)
                self.assertGreater(float(np.abs(whole - evaluate(kind, u, v, seed=318, scale=6.7)).mean()), .005)
                # Crossing a UV tile must continue the field, not repeat it.
                self.assertGreater(float(np.abs(whole - evaluate(kind, u+1, v, seed=317, scale=6.7)).mean()), .005)

    def test_damage_grows_without_reshuffling_existing_regions(self):
        u, v = np.meshgrid(np.linspace(0, 1, 192, dtype=np.float32), np.linspace(0, 1, 192, dtype=np.float32))
        field = evaluate('WEATHERING_FRONT', u, v, seed=19, scale=6)
        previous = np.zeros(field.shape, bool)
        for age in (.1, .25, .45, .65, .9):
            damaged = field > 1 - .92 * age
            self.assertFalse(np.any(previous & ~damaged))
            previous = damaged
        self.assertGreater(previous.mean(), .7)

    def test_runoff_has_finite_vertical_support(self):
        u, v = np.meshgrid(np.linspace(-1, 2, 240, dtype=np.float32), np.linspace(-1, 2, 240, dtype=np.float32))
        field = evaluate('RUNOFF_TRAILS', u, v, seed=42, scale=4)
        occupied = field > .1
        self.assertGreater(occupied.mean(), .003)
        self.assertLess(occupied.mean(), .20)
        self.assertLess(occupied.mean(axis=0).max(), .75)
        # Trails vary far more across their width than along gravity.
        self.assertGreater(np.abs(np.diff(field, axis=1)).mean(), np.abs(np.diff(field, axis=0)).mean() * 2)

    def test_protected_coats_share_coordinates_and_nested_damage(self):
        construction = model.Construction('CARBON_STEEL', ('EPOXY_PRIMER', 'POLYURETHANE_PAINT'))
        for years in (0, 3, 8, 20):
            state = model.simulate(construction, [model.Segment(years, ENVIRONMENTS['COASTAL'], USAGE['MODERATE'], MAINTENANCE['NONE'])])
            doc = document.build(state, construction, seed=317)
            by_role = dict(zip(doc['roles'], doc['layers']))
            primer, paint, corrosion = [by_role[r] for r in ('primer', 'paint', 'corrosion')]
            self.assertLessEqual(primer['mask']['amount'], paint['mask']['amount'])
            self.assertEqual(primer['mask']['amount'], corrosion['mask']['amount'])
            self.assertEqual(primer['mask_effects'][0]['black'], corrosion['mask_effects'][0]['black'])
            for key in ('procedural', 'scale', 'seed', 'projection', 'triplanar_hardness'):
                self.assertEqual(primer['mask_effects'][0][key], paint['mask_effects'][0][key])
            self.assertEqual(corrosion['mask_effects'][0]['operation'], 'MAX')
            self.assertNotIn('METALLIC', by_role['handling_polish']['channels'])
            self.assertGreater(paint['height_range'][0], primer['height_value'])
            self.assertEqual(doc['version'], 2)


if __name__ == '__main__':
    unittest.main()
