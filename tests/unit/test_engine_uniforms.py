"""Uniform output proofs must fail closed after edits that introduce detail."""

import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.constants import CHANNELS, DEFAULTS
from open_painter.evaluation.engine.uniforms import uniform_channel
from open_painter.evaluation.shader_math import Signal, blend


def channel(kind, **overrides):
    values = dict(channel=kind, enabled=True, blend_mode='INHERIT', opacity=1.,
                  value=DEFAULTS[kind][0], color=DEFAULTS[kind], projection='UV',
                  tiling='REPEAT', material_mapping_version=0, anchor='', image=None,
                  image_name='', detail_image=None, detail_image_name='', procedural='', effects=[])
    return NS(**(values | overrides))


def cellular(**overrides):
    values = dict(parent_id='', source='PROCEDURAL', enabled=True, procedural='CELLULAR',
                  projection='UV', tiling='REPEAT', material_mapping_version=0, image=None,
                  node_group=None, reference_anchor='', reference_layer='', reference_mask='',
                  detail_height_anchor='', detail_normal_anchor='', stroke_key='',
                  operation='MULTIPLY', black=0., white=1., strength=1., invert=False,
                  noise_scale=12., scale=(1., 1.), offset=(0., 0.), rotation=0., seed=1)
    return NS(**(values | overrides))


def fill(**overrides):
    values = dict(type='FILL', parent_id='', bypass=False, cache_mode='LIVE', cache=[],
                  stroke_key='', blend_mode='NORMAL', opacity=1., visible=True,
                  channels=[channel(kind) for kind in CHANNELS], masks=[])
    return NS(**(values | overrides))


class NumericOpaqueMath:
    """Execute the real shader composition entry point on float32 arrays."""
    def math(self, operation, a, b):
        if operation != 'MULTIPLY':
            raise AssertionError(operation)
        return np.asarray(a, np.float32) * np.asarray(b, np.float32)

    def clamp(self, value):
        return np.clip(value, 0., 1.)

    def mix(self, a, b, amount):
        a, b = np.asarray(a, np.float32), np.asarray(b, np.float32)
        return a + (b - a) * amount


class UniformChannelTests(unittest.TestCase):
    def test_empty_and_neutral_stacks_match_both_precisions(self):
        for precise in (False, True):
            for kind in CHANNELS:
                with self.subTest(precise=precise, channel=kind):
                    self.assertEqual(uniform_channel(NS(layers=[]), kind, precise), DEFAULTS[kind])
                    self.assertEqual(uniform_channel(NS(layers=[fill(), fill()]), kind, precise), DEFAULTS[kind])

    def test_neutral_normal_survives_spatial_coverage_and_quantization(self):
        ts = NS(layers=[fill(), fill(masks=[cellular()])])
        expected = uniform_channel(ts, 'NORMAL')
        self.assertEqual(expected, (.5, .5, 1., 1.))
        # Run the production composition math with varying per-pixel masks and
        # retained intermediate precision. A nonconstant mask cannot alter the
        # neutral result, even with partial source alpha/channel/layer opacity.
        coverage = np.linspace(0., 1., 257, dtype=np.float32)[:, None]
        for dtype in (np.float16, np.float32):
            result = Signal(np.array(expected[:3], np.float32), 1.)
            for alpha, opacity in ((1., .73), (.375, .27), (.125, 1.)):
                result = blend(NumericOpaqueMath(), result,
                               Signal(expected[:3], alpha), coverage * opacity, 'NORMAL', 'NORMAL')
                result = Signal(result.color.astype(dtype).astype(np.float32), result.alpha)
            np.testing.assert_array_equal(result.color, np.tile(expected[:3], (len(coverage), 1)))

    def test_ordinary_fixture_proves_only_unmodified_channels(self):
        base, coat = fill(), fill(masks=[cellular()])
        base.channels[0].color = (.3, .35, .4, 1.)
        coat.channels[0].color = (.7, .2, .1, 1.)
        coat.channels[1].value, coat.channels[2].value = .3, .8
        ts = NS(layers=[base, coat])
        for kind in ('BASE_COLOR', 'ROUGHNESS', 'METALLIC'):
            self.assertIsNone(uniform_channel(ts, kind))
        for kind in ('NORMAL', 'HEIGHT', 'AO', 'EMISSION', 'OPACITY'):
            self.assertEqual(uniform_channel(ts, kind), DEFAULTS[kind])

    def test_numeric_changes_revoke_and_restore_proof_without_cache(self):
        layer = fill(masks=[cellular()])
        prop, mask = layer.channels[3], layer.masks[0]
        ts = NS(layers=[layer])
        self.assertEqual(uniform_channel(ts, 'NORMAL'), DEFAULTS['NORMAL'])
        mask.strength, mask.seed = .31, 1907364117
        layer.opacity, prop.opacity = .73, .2
        self.assertEqual(uniform_channel(ts, 'NORMAL'), DEFAULTS['NORMAL'])
        prop.color = (.6, .5, 1., 1.)
        self.assertIsNone(uniform_channel(ts, 'NORMAL'))
        prop.color = DEFAULTS['NORMAL']
        self.assertEqual(uniform_channel(ts, 'NORMAL'), DEFAULTS['NORMAL'])

    def test_disabled_channel_and_disable_blend_follow_compiler_wiring(self):
        layer = fill()
        prop = layer.channels[3]
        prop.color, prop.procedural = (.7, .5, 1., 1.), 'CELLULAR'
        ts = NS(layers=[layer])
        self.assertIsNone(uniform_channel(ts, 'NORMAL'))
        prop.enabled = False
        self.assertEqual(uniform_channel(ts, 'NORMAL'), DEFAULTS['NORMAL'])
        prop.enabled, prop.blend_mode = True, 'DISABLE'
        self.assertEqual(uniform_channel(ts, 'NORMAL'), DEFAULTS['NORMAL'])
        prop.blend_mode = 'INHERIT'
        self.assertIsNone(uniform_channel(ts, 'NORMAL'))

    def test_unsupported_source_or_layer_edits_revoke_proof(self):
        source_edits = dict(projection='DECAL', tiling='NONE', material_mapping_version=1,
                            anchor='anchor-id', image=object(), image_name='missing.png',
                            detail_image=object(), detail_image_name='missing-detail.png',
                            procedural='CELLULAR', effects=[NS(enabled=False)],
                            blend_mode='NORMAL_COMBINE', opacity=float('nan'))
        layer_edits = dict(type='FOLDER', parent_id='folder-id', bypass=True,
                           cache_mode='FROZEN', cache=[NS(image=None)], stroke_key='stroke-id',
                           blend_mode='MULTIPLY', opacity=float('inf'))
        for location, edits in (('source', source_edits), ('layer', layer_edits)):
            for field, value in edits.items():
                with self.subTest(location=location, field=field):
                    layer = fill()
                    setattr(layer.channels[3] if location == 'source' else layer, field, value)
                    self.assertIsNone(uniform_channel(NS(layers=[layer]), 'NORMAL'))

    def test_hidden_layer_is_not_assumed_to_have_valid_content(self):
        layer = fill(visible=False)
        layer.channels[3].image_name = 'missing.png'
        self.assertIsNone(uniform_channel(NS(layers=[layer]), 'NORMAL'))

    def test_mask_bounds_and_sources_fail_closed(self):
        edits = dict(source='FILTER', procedural='UNKNOWN', projection='TRIPLANAR',
                     tiling='NONE', material_mapping_version=1, parent_id='group-id',
                     image=object(), node_group=object(), reference_anchor='anchor-id',
                     operation='UNKNOWN', black=float('nan'), white=float('inf'),
                     strength=2., noise_scale=0., scale=(1e20, 1.), offset=(0., float('nan')),
                     rotation=float('inf'), seed=-1)
        for field, value in edits.items():
            with self.subTest(field=field):
                mask = cellular(**{field: value})
                self.assertIsNone(uniform_channel(NS(layers=[fill(masks=[mask])]), 'NORMAL'))
        mask = cellular(enabled=False, procedural='UNKNOWN')
        ts = NS(layers=[fill(masks=[mask])])
        self.assertEqual(uniform_channel(ts, 'NORMAL'), DEFAULTS['NORMAL'])
        mask.enabled = True
        self.assertIsNone(uniform_channel(ts, 'NORMAL'))

    def test_all_admitted_mask_operations_and_degenerate_levels(self):
        for operation in ('NORMAL', 'MULTIPLY', 'ADD', 'MAX', 'MIN'):
            with self.subTest(operation=operation):
                masks = [cellular(), cellular(operation=operation, black=1., white=0., invert=True)]
                self.assertEqual(uniform_channel(NS(layers=[fill(masks=masks)]), 'NORMAL'), DEFAULTS['NORMAL'])

    def test_equal_arbitrary_colors_are_not_a_quantization_proof(self):
        layers = [fill(), fill(masks=[cellular()])]
        for layer in layers:
            layer.channels[0].color = (.3, .3, .3, 1.)
        self.assertNotEqual(np.float32(np.float16(.3)), np.float32(.3))
        self.assertIsNone(uniform_channel(NS(layers=layers), 'BASE_COLOR'))

    def test_missing_or_unknown_state_falls_back(self):
        layer = fill()
        for field in ('channels', 'cache_mode', 'masks'):
            broken = deepcopy(layer)
            delattr(broken, field)
            self.assertIsNone(uniform_channel(NS(layers=[broken]), 'NORMAL'))
        self.assertIsNone(uniform_channel(NS(), 'NORMAL'))
        self.assertIsNone(uniform_channel(NS(layers=[]), 'UNKNOWN'))
        layer.channels.append(channel('NORMAL'))
        self.assertIsNone(uniform_channel(NS(layers=[layer]), 'NORMAL'))


if __name__ == '__main__':
    unittest.main()
