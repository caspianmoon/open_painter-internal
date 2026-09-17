"""Reference parity for the shader compiler, without requiring Blender."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from open_painter.constants import BLEND_ITEMS
from open_painter.evaluation import blend_modes
from open_painter.evaluation.shader_math import Signal, blend, blend_value, srgb_encode
from open_painter.evaluation.shader_effects import POINTWISE, UnsupportedEffect, apply_effect
from open_painter.texture_engine import filters


class Numeric:
    """Interpret Blender's primitive operations, independently of node builders."""
    def math(self, op, *args):
        a = float(args[0])
        b = float(args[1]) if len(args) > 1 else 0.0
        if op == "ADD": return a + b
        if op == "SUBTRACT": return a - b
        if op == "MULTIPLY": return a * b
        if op == "DIVIDE": return a / b if b else 0.0
        if op == "MAXIMUM": return max(a, b)
        if op == "MINIMUM": return min(a, b)
        if op == "ABSOLUTE": return abs(a)
        if op == "GREATER_THAN": return float(a > b)
        if op == "LESS_THAN": return float(a < b)
        if op == "COMPARE": return float(abs(a - b) <= float(args[2]))
        if op == "SQRT": return np.sqrt(max(a, 0))
        if op == "POWER": return a ** b
        if op == "FLOOR": return np.floor(a)
        raise AssertionError(op)

    def vec(self, op, *args):
        a = np.asarray(args[0], dtype=np.float64)
        b = np.asarray(args[1], dtype=np.float64) if len(args) > 1 else 0.0
        if op == "ADD": return a + b
        if op == "SUBTRACT": return a - b
        if op in {"MULTIPLY", "SCALE"}: return a * b
        if op == "MINIMUM": return np.minimum(a, b)
        if op == "MAXIMUM": return np.maximum(a, b)
        if op == "LENGTH": return np.linalg.norm(a)
        if op == "DISTANCE": return np.linalg.norm(a - b)
        if op == "DOT_PRODUCT": return np.dot(a, b)
        raise AssertionError(op)

    def combine(self, *values): return np.asarray(values, dtype=np.float64)
    def separate(self, value): return tuple(value)
    def mix(self, a, b, factor): return np.asarray(a) + (np.asarray(b) - a) * factor
    def clamp(self, value, lo=0.0, hi=1.0): return np.clip(value, lo, hi)
    def param(self, owner, field, index=None):
        value = getattr(owner, field)
        return value if index is None else value[index]
    def effect_image(self, effect): return effect.image


def effect(kind, **changes):
    values = dict(kind=kind, enabled=True, strength=.71, amount=.37, contrast=1.6,
                  gamma=1.7, black=.13, white=.86, output_black=.06,
                  output_white=.92, hue=-.21, saturation=1.3, lightness=.12,
                  softness=.11, steps=5, color=(.22, .09, .48), color2=(.71, .86, .12),
                  matrix=(.8, .1, .1, .1, .7, .2, .1, .2, .7), bias=(.01, -.02, .07),
                  image=(.61, .42, .83))
    values.update(changes)
    return SimpleNamespace(**values)


def expected_effect(data, prop):
    params = dict(vars(prop))
    params.update(strength=prop.amount, position=prop.amount, brightness=prop.amount,
                  value=prop.lightness, tolerance=prop.softness)
    if prop.kind == "NORMAL_COMBINE":
        image = np.array(prop.image, np.float32)[None, None, :]
        params["detail"] = filters.apply(image, "NORMAL_STRENGTH", strength=prop.amount)
    original = np.asarray(data, np.float32)[None, None, :]
    filtered = filters.apply(original, prop.kind, **{k:v for k,v in params.items() if k != "kind"})
    return (original + (filtered - original) * prop.strength)[0, 0]


class ShaderArithmetic(unittest.TestCase):
    def test_all_blends_match_reference(self):
        rng = np.random.default_rng(1984)
        inputs = [(rng.uniform(-.2, 1.2, 3), rng.random(3)) for _ in range(30)]
        inputs += [(np.full(3, a), np.full(3, b)) for a in (0., .25, .5, 1.) for b in (0., .5, 1.)]
        for mode, *_ in BLEND_ITEMS:
            for base, source in inputs:
                expected = blend_modes.blend(base, source, 1.0, mode)
                actual = blend_value(Numeric(), base, source, mode)
                np.testing.assert_allclose(actual, expected, atol=2e-6, rtol=2e-5, err_msg=mode)

    def test_folder_source_over_and_opaque_stack(self):
        for coverage in (0., .3, 1.):
            for mode, *_ in BLEND_ITEMS:
                source, base = np.array((.8, .2, .7)), np.array((.4, .7, .3))
                old_alpha, source_alpha, amount = coverage, .67, .42
                alpha = source_alpha * amount
                backdrop = base * old_alpha
                actual = blend(Numeric(), Signal(backdrop, old_alpha), Signal(source, source_alpha), amount, mode)
                if mode == "DISABLE":
                    expected, expected_alpha = backdrop, old_alpha
                else:
                    mixed = blend_modes.blend(base if old_alpha else np.zeros(3), source, 1., mode)
                    expected = (1-alpha) * backdrop + alpha * ((1-old_alpha) * source + old_alpha * mixed)
                    expected_alpha = alpha + old_alpha * (1-alpha)
                np.testing.assert_allclose(actual.color, expected, atol=2e-6, rtol=2e-5, err_msg=mode)
                self.assertAlmostEqual(actual.alpha, expected_alpha)

    def test_every_pointwise_filter_matches_reference(self):
        rng = np.random.default_rng(2026)
        inputs = [rng.random(3) for _ in range(16)]
        inputs += [np.full(3, x) for x in (0., .125, .375, .625, .875, 1.)]
        for kind in POINTWISE:
            prop = effect(kind)
            for value in inputs:
                actual = apply_effect(Numeric(), Signal(value, .37), prop)
                expected = expected_effect(value, prop)
                np.testing.assert_allclose(actual.color, expected, atol=3e-6, rtol=2e-5, err_msg=kind)
                self.assertEqual(actual.alpha, .37)

    def test_disabled_and_zero_strength_preserve_source(self):
        source = (.17, .42, .96)
        for kind in POINTWISE:
            for changes in ({"enabled": False}, {"strength": 0.}):
                actual = apply_effect(Numeric(), Signal(source, .2), effect(kind, **changes))
                np.testing.assert_allclose(actual.color, source, atol=1e-12)
                self.assertEqual(actual.alpha, .2)

    def test_srgb_encode_recovers_stored_bytes(self):
        # A Normal Combine detail map tagged sRGB is sampled through the Image
        # Texture node's decode; the encode must hand back the stored value the
        # CPU's raw read sees (image_snapshots._decode's transfer, inverted).
        stored = np.concatenate((np.arange(256) / 255., (0., .0031308 * 12.92, .04045, 1e-6)))
        decoded = np.where(stored <= .04045, stored / 12.92, ((stored + .055) / 1.055) ** 2.4)
        for value, linear in zip(stored, decoded):
            actual = srgb_encode(Numeric(), np.full(3, linear))
            np.testing.assert_allclose(actual, np.full(3, value), atol=1e-6, rtol=1e-6)

    def test_spatial_operations_are_explicit_boundaries(self):
        for kind in set(filters.CATALOG) - POINTWISE:
            with self.assertRaises(UnsupportedEffect, msg=kind):
                apply_effect(Numeric(), Signal((.5, .5, .5)), effect(kind))


if __name__ == "__main__":
    unittest.main(argv=[__file__])
