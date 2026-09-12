"""Independent tone remapping expectations, components, order and source immutability."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from open_painter.evaluation.effects import levels, apply_effects


def effect(**kwargs):
    props = dict(
        enabled=True,
        opacity=1,
        channel="BASE_COLOR",
        type="LEVELS",
        component="RGB",
        input_black=0,
        input_white=1,
        gamma=1,
        output_black=0,
        output_white=1,
        clamp=True,
    )
    props.update(kwargs)
    return SimpleNamespace(**props)


class EffectTests(unittest.TestCase):
    def test_levels_known_values(self):
        np.testing.assert_allclose(
            levels(np.array([0.2, 0.35, 0.8]), 0.2, 0.8, 2, 0.1, 0.9), [0.1, 0.5, 0.9]
        )
        np.testing.assert_allclose(
            levels(np.array([0.0, 0.25, 1.0]), output_black=1, output_white=0), [1, 0.75, 0]
        )
        np.testing.assert_allclose(
            levels(np.array([-0.25, 0.25, 4.0]), gamma=2, clamp=False), [-0.5, 0.5, 2]
        )
        np.testing.assert_allclose(levels(np.array([-0.25, 0.25, 4.0]), gamma=2), [0, 0.5, 1])
        with self.assertRaisesRegex(ValueError, "White"):
            levels(np.array([0.5]), black=0.5, white=0.5)

    def test_component_opacity_and_source_unchanged(self):
        source = np.array([0.25, 0.5, 0.75])
        original = source.copy()
        result = apply_effects([effect(component="G", gamma=2, opacity=0.5)], source, "BASE_COLOR")
        np.testing.assert_allclose(result, [0.25, (0.5 + np.sqrt(0.5)) / 2, 0.75])
        np.testing.assert_array_equal(source, original)
        np.testing.assert_array_equal(apply_effects([effect(gamma=2)], source, "ROUGHNESS"), source)
        np.testing.assert_allclose(
            apply_effects([effect(component="G", gamma=2)], source, "", mask=True), np.sqrt(source)
        )

    def test_order_bypass_and_tile_equivalence(self):
        source = np.linspace(0, 1, 600, dtype=np.float32).reshape(10, 20, 3)
        transforms = [effect(gamma=2), effect(type="FILTER", filter="INVERT")]
        whole = apply_effects(transforms, source, "BASE_COLOR")
        np.testing.assert_allclose(whole, 1 - np.sqrt(source), atol=1e-7)
        np.testing.assert_array_equal(
            whole,
            np.concatenate(
                [apply_effects(transforms, t, "BASE_COLOR") for t in (source[:3], source[3:])]
            ),
        )
        transforms[0].enabled = False
        np.testing.assert_allclose(apply_effects(transforms, source, "BASE_COLOR"), 1 - source)
        transforms[1].opacity = 0
        np.testing.assert_array_equal(apply_effects(transforms, source, "BASE_COLOR"), source)

    def test_scalar_grayscale(self):
        source = np.full((2, 4, 1), 0.3)
        result = apply_effects(
            [effect(type="FILTER", filter="GRAYSCALE", channel="ROUGHNESS")], source, "ROUGHNESS"
        )
        np.testing.assert_array_equal(result, source)

    def test_filters(self):
        source = np.array([0.2, 0.4, 0.8])
        gray = apply_effects([effect(type="FILTER", filter="GRAYSCALE")], source, "BASE_COLOR")
        np.testing.assert_allclose(gray, [0.38636] * 3)
        result = apply_effects(
            [effect(type="FILTER", filter="BRIGHTNESS_CONTRAST", brightness=0.1, contrast=2)],
            source,
            "BASE_COLOR",
        )
        np.testing.assert_allclose(result, [0, 0.4, 1], atol=1e-7)
