"""Independent texture effect pixels, opacity, order, bypass and alpha behavior."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.evaluation.channel_eval import apply_effect, apply_texture_effects
from open_painter.texture_engine.filters import apply


def effect(**kwargs):
    props = dict(enabled=True, strength=1, kind='LEVELS', black=0, white=1,
                 gamma=1, output_black=0, output_white=1)
    props.update(kwargs)
    return SimpleNamespace(**props)


class EffectTests(unittest.TestCase):
    def test_levels_known_values(self):
        source = np.array([[.2, .35, .8]], np.float32)
        np.testing.assert_allclose(apply(source, 'LEVELS', black=.2, white=.8,
                                        gamma=2, output_black=.1, output_white=.9),
                                   [[.1, .5, .9]], atol=1e-7)
        np.testing.assert_allclose(apply(np.array([[0, .25, 1]], np.float32), 'LEVELS',
                                        output_black=1, output_white=0), [[1, .75, 0]])
        # Texture effects return normalized channel values; HDR sign-preserving
        # levels belonged to the removed implementation and are not this contract.
        np.testing.assert_allclose(apply(np.array([[-.25, .25, 4]], np.float32),
                                        'LEVELS', gamma=2), [[0, .5, 1]])
        with self.assertRaisesRegex(ValueError, 'White'):
            apply(source, 'LEVELS', black=.5, white=.5)

    def test_effect_opacity_alpha_and_source_unchanged(self):
        source = np.array([[[.25, .5, .75, .3]]], np.float32)
        original = source.copy()
        result = apply_effect(source, effect(gamma=2, strength=.5))
        np.testing.assert_allclose(result[..., :3], (source[..., :3] + np.sqrt(source[..., :3])) / 2)
        np.testing.assert_array_equal(result[..., 3], source[..., 3])
        np.testing.assert_array_equal(source, original)
        np.testing.assert_allclose(apply_effect(np.array([[.25, .5, .75]], np.float32),
                                               effect(gamma=2)), np.sqrt([[.25, .5, .75]]))

    def test_order_bypass_and_pointwise_tile_equivalence(self):
        source = np.linspace(0, 1, 600, dtype=np.float32).reshape(10, 20, 3)
        transforms = [effect(gamma=2), effect(kind='INVERT')]
        whole = apply_texture_effects(source, transforms)
        np.testing.assert_allclose(whole, 1 - np.sqrt(source), atol=1e-7)
        np.testing.assert_array_equal(whole, np.concatenate([
            apply_texture_effects(tile, transforms) for tile in (source[:3], source[3:])]))
        reversed_result = apply_texture_effects(source, list(reversed(transforms)))
        np.testing.assert_allclose(reversed_result, np.sqrt(1 - source), atol=1e-7)
        self.assertGreater(float(np.abs(whole - reversed_result).mean()), .1)
        transforms[0].enabled = False
        np.testing.assert_allclose(apply_texture_effects(source, transforms), 1 - source)
        transforms[1].strength = 0
        np.testing.assert_array_equal(apply_texture_effects(source, transforms), source)

    def test_scalar_grayscale_and_rgba_invert(self):
        source = np.full((2, 4, 1), .3, np.float32)
        np.testing.assert_allclose(apply_effect(source, effect(kind='DESATURATE')), source)
        rgba = np.array([[[.2, .4, .8, .7]]], np.float32)
        np.testing.assert_allclose(apply_effect(rgba, effect(kind='INVERT')), [[[.8, .6, .2, .7]]])

    def test_filters_independent_expectations(self):
        source = np.array([[[.2, .4, .8]]], np.float32)
        gray = apply_effect(source, effect(kind='DESATURATE'))
        np.testing.assert_allclose(gray, [[[.38636] * 3]], atol=1e-7)
        result = apply_effect(source, effect(kind='BRIGHTNESS_CONTRAST', amount=.1, contrast=2))
        np.testing.assert_allclose(result, [[[0, .4, 1]]], atol=1e-7)
        rgb = apply_effect(np.array([[0, .5, 1]], np.float32),
                           effect(kind='GRADIENT_MAP', color=(.1, .2, .3), color2=(.9, .8, .7)))
        np.testing.assert_allclose(rgb, [[[.1, .2, .3], [.5, .5, .5], [.9, .8, .7]]], atol=1e-7)
