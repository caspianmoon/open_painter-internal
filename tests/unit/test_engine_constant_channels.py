"""One-texel eligibility must be revoked by anything that introduces detail."""

import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.constants import CHANNELS, DEFAULTS
from open_painter.evaluation.engine.constant_channels import spatially_constant_channel
from open_painter.evaluation.engine.uniforms import uniform_channel


def source(kind, **overrides):
    fields = dict(channel=kind, enabled=True, blend_mode='INHERIT', opacity=1.,
                  value=DEFAULTS[kind][0], color=DEFAULTS[kind], projection='UV',
                  tiling='REPEAT', material_mapping_version=0, anchor='', image=None,
                  image_name='', detail_image=None, detail_image_name='', procedural='', effects=[])
    return NS(**(fields | overrides))


def fill(**overrides):
    fields = dict(type='FILL', parent_id='', bypass=False, cache_mode='LIVE', cache=[],
                  stroke_key='', blend_mode='NORMAL', opacity=1., visible=True,
                  channels=[source(kind) for kind in CHANNELS], masks=[])
    return NS(**(fields | overrides))


def prop(layer, kind):
    return next(item for item in layer.channels if item.channel == kind)


class SpatiallyConstantChannelTests(unittest.TestCase):
    def test_empty_and_default_stacks_are_eligible(self):
        for kind in CHANNELS:
            self.assertTrue(spatially_constant_channel(NS(layers=[]), kind))
            self.assertTrue(spatially_constant_channel(NS(layers=[fill(), fill()]), kind))

    def test_arbitrary_colors_and_coverage_need_gpu_evaluation(self):
        base, coat = fill(opacity=.73), fill(opacity=.27)
        ts = NS(layers=[base, coat])
        for kind in ('BASE_COLOR', 'NORMAL', 'EMISSION'):
            prop(base, kind).color = (.31, .48, .92, .41)
            prop(coat, kind).color = (.61, .23, .77, .83)
            prop(coat, kind).opacity = .38
            self.assertTrue(spatially_constant_channel(ts, kind))
            self.assertIsNone(uniform_channel(ts, kind))

    def test_scalar_range_preserves_values_before_final_clamp(self):
        layer = fill()
        ts = NS(layers=[layer])
        for kind in ('ROUGHNESS', 'METALLIC', 'HEIGHT', 'AO', 'OPACITY'):
            for value in (-1., -.375, .31, 2., 10.):
                with self.subTest(channel=kind, value=value):
                    prop(layer, kind).value = value
                    self.assertTrue(spatially_constant_channel(ts, kind))

    def test_numeric_edits_are_checked_without_cached_identity(self):
        layer = fill()
        ts = NS(layers=[layer])
        item = prop(layer, 'HEIGHT')
        self.assertTrue(spatially_constant_channel(ts, 'HEIGHT'))
        for value in (float('nan'), float('inf'), -1.001, 10.001):
            item.value = value
            self.assertFalse(spatially_constant_channel(ts, 'HEIGHT'))
        item.value = .37
        self.assertTrue(spatially_constant_channel(ts, 'HEIGHT'))
        item.procedural = 'CELLULAR'
        self.assertFalse(spatially_constant_channel(ts, 'HEIGHT'))
        item.procedural = ''
        self.assertTrue(spatially_constant_channel(ts, 'HEIGHT'))

    def test_spatial_sources_and_effects_revoke_proof(self):
        edits = dict(projection='DECAL', tiling='NONE', material_mapping_version=1,
                     anchor='output-id', image=object(), image_name='missing.png',
                     detail_image=object(), detail_image_name='missing-detail.png',
                     procedural='CELLULAR', effects=[NS(enabled=False)])
        for field, value in edits.items():
            with self.subTest(field=field):
                layer = fill()
                setattr(prop(layer, 'NORMAL'), field, value)
                self.assertFalse(spatially_constant_channel(NS(layers=[layer]), 'NORMAL'))

    def test_topology_masks_and_caches_revoke_proof(self):
        edits = dict(type='FOLDER', parent_id='folder-id', bypass=True,
                     cache_mode='FROZEN', cache=[NS(image=None)], stroke_key='stroke-id',
                     masks=[NS(enabled=False)])
        for field, value in edits.items():
            with self.subTest(field=field):
                self.assertFalse(spatially_constant_channel(NS(layers=[fill(**{field: value})]), 'NORMAL'))

    def test_hidden_layer_content_and_coverage_remain_checked(self):
        layer = fill(visible=False)
        ts = NS(layers=[layer])
        self.assertTrue(spatially_constant_channel(ts, 'NORMAL'))
        prop(layer, 'NORMAL').procedural = 'CELLULAR'
        self.assertFalse(spatially_constant_channel(ts, 'NORMAL'))
        prop(layer, 'NORMAL').procedural = ''
        for owner, field, value in ((layer, 'opacity', float('nan')),
                                    (layer, 'visible', float('inf')),
                                    (prop(layer, 'NORMAL'), 'opacity', 1.001)):
            previous = getattr(owner, field)
            setattr(owner, field, value)
            self.assertFalse(spatially_constant_channel(ts, 'NORMAL'))
            setattr(owner, field, previous)

    def test_disabled_channel_and_disable_blend_are_unwired(self):
        layer = fill()
        ts = NS(layers=[layer])
        item = prop(layer, 'NORMAL')
        item.procedural = 'CELLULAR'
        item.enabled = False
        self.assertTrue(spatially_constant_channel(ts, 'NORMAL'))
        item.enabled, item.blend_mode = True, 'DISABLE'
        self.assertTrue(spatially_constant_channel(ts, 'NORMAL'))
        item.blend_mode, layer.blend_mode = 'INHERIT', 'DISABLE'
        self.assertTrue(spatially_constant_channel(ts, 'NORMAL'))
        layer.blend_mode = 'NORMAL'
        self.assertFalse(spatially_constant_channel(ts, 'NORMAL'))

    def test_other_channel_detail_does_not_change_selected_channel(self):
        layer = fill()
        prop(layer, 'BASE_COLOR').procedural = 'CELLULAR'
        ts = NS(layers=[layer])
        self.assertTrue(spatially_constant_channel(ts, 'NORMAL'))
        self.assertFalse(spatially_constant_channel(ts, 'BASE_COLOR'))

    def test_unknown_blends_and_invalid_color_fail_closed(self):
        layer = fill()
        item = prop(layer, 'NORMAL')
        ts = NS(layers=[layer])
        for mode in ('UNKNOWN', 'MULTIPLY', 'NORMAL_COMBINE'):
            item.blend_mode = mode
            self.assertFalse(spatially_constant_channel(ts, 'NORMAL'))
        item.blend_mode = 'NORMAL'
        for color in ((.5, .5, 1.), (.5, .5, 1., float('nan')), (-.1, .5, 1., 1.), (.5, 1.1, 1., 1.)):
            item.color = color
            self.assertFalse(spatially_constant_channel(ts, 'NORMAL'))

    def test_missing_duplicate_or_stale_state_fails_closed(self):
        self.assertFalse(spatially_constant_channel(NS(), 'NORMAL'))
        self.assertFalse(spatially_constant_channel(NS(layers=[]), 'UNKNOWN'))
        for field in ('channels', 'cache_mode', 'masks'):
            layer = fill()
            delattr(layer, field)
            self.assertFalse(spatially_constant_channel(NS(layers=[layer]), 'NORMAL'))
        layer = fill()
        layer.channels.append(deepcopy(prop(layer, 'NORMAL')))
        self.assertFalse(spatially_constant_channel(NS(layers=[layer]), 'NORMAL'))
        layer.channels = [item for item in layer.channels if item.channel != 'NORMAL']
        self.assertFalse(spatially_constant_channel(NS(layers=[layer]), 'NORMAL'))


if __name__ == '__main__':
    unittest.main()
