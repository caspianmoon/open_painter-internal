"""Live no-op planning retains the CPU folder/alpha contract and dirty outputs.

No window/GPU is needed: upload retains exact arrays and the resulting plan is
composed with the existing CPU blend functions, then compared to LayerEvaluator.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.constants import CHANNELS, DEFAULTS
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import live_inputs
from open_painter.evaluation.blend_modes import blend
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.live_inputs import Builder
from open_painter.smart_materials import composer


class Cache:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def put(self, key, texture, size):
        self.values[key] = texture


class Device:
    def __init__(self):
        self.uploads = 0

    def upload(self, array):
        self.uploads += 1
        return array.copy()


def finish(job):
    while True:
        try:
            next(job)
        except StopIteration as done:
            return done.value


def plan_pixels(records, channel, n):
    """Execute the live plan using the committed CPU blend/coverage operations."""
    def compose(records, rgb, coverage):
        opaque = np.ndim(coverage) == 0 and coverage == 1
        for record in records:
            amount = record['opacity']
            if record['mask'] is not None:
                amount = amount * record['mask'][..., None]
            children = record.get('children')
            if children is not None and record['passthrough']:
                grouped, alpha = compose(children, np.array(rgb), np.array(coverage))
                rgb = rgb + amount * (grouped - rgb)
                if not opaque:
                    coverage = coverage + amount * (alpha - coverage)
                continue
            if children is not None:
                src, alpha = compose(children, np.zeros((n, n, 3), np.float32), np.float32(0))
                src = np.divide(src, alpha, out=np.zeros_like(src), where=alpha > 1e-8)
            else:
                value = record['source']
                rgba = np.broadcast_to(value.get('texture', value.get('color', (0, 0, 0, 1))),
                                       (n, n, 4)).copy().astype(np.float32)
                field = value.get('field')
                if field is not None:
                    if value['procedural'] == 1:
                        rgba[..., :3] *= (1 + (field - .5) * value['detail'])[..., None]
                    elif value['procedural'] == 2:
                        rgba[..., :3] += ((field - .5) * value['detail'] * .35)[..., None]
                    elif value['procedural'] == 3:
                        rgba[..., :3] = (.5 + (field - .5) * value['height'])[..., None]
                    rgba[..., :3] = np.clip(rgba[..., :3], 0, 1)
                src, alpha = rgba[..., :3], rgba[..., 3:4]
            alpha = np.clip(alpha * amount, 0, 1)
            if opaque:
                rgb = blend(rgb, src, alpha, record['mode'])
            else:
                backdrop = np.divide(rgb, coverage, out=np.zeros_like(rgb), where=coverage > 1e-8)
                mixed = blend(backdrop, src, 1, record['mode'])
                rgb = (1 - alpha) * rgb + alpha * ((1 - coverage) * src + coverage * mixed)
                coverage = alpha + coverage * (1 - alpha)
        return rgb, coverage

    rgb, _ = compose(records, np.array(DEFAULTS[channel][:3], np.float32), np.float32(1))
    if channel == 'NORMAL':
        normal = rgb * 2 - 1
        lengths = np.linalg.norm(normal, axis=-1, keepdims=True)
        rgb = np.divide(normal, lengths, out=np.zeros_like(normal), where=lengths > 1e-8) * .5 + .5
    result = np.ones((n, n, 4), np.float32)
    result[..., :3] = np.clip(rgb, 0, 1)
    return result


def assert_cpu_parity(ts, channel='BASE_COLOR'):
    builder = Builder(scene, ts, cache, device)
    signature = builder.signature(channel)
    records, resolved_signature = finish(builder.channel(channel))
    assert signature == resolved_signature
    actual = plan_pixels(records, channel, builder.n)
    LayerEvaluator(scene).evaluate_channel(ts.id, channel)
    expected = images.pixels(images.output(ts, channel).image)
    np.testing.assert_array_equal(actual, expected)
    return records, signature


def untouched_plan(ts, channel):
    builder = Builder(scene, ts, cache, device)
    with patch.object(builder.cache, 'get', side_effect=AssertionError('No-op queried cache')), \
         patch.object(builder.device, 'upload', side_effect=AssertionError('No-op uploaded pixels')), \
         patch.object(live_inputs, '_procedural_tile', side_effect=AssertionError('No-op generated source')), \
         patch.object(live_inputs, 'source', side_effect=AssertionError('No-op sampled source')), \
         patch.object(live_inputs, 'evaluate_masks', side_effect=AssertionError('No-op generated mask')):
        signature = builder.signature(channel)
        records, resolved_signature = finish(builder.channel(channel))
    assert records == [] and resolved_signature == signature
    return signature


open_painter.register()
scene = bpy.context.scene
cache, device = Cache(), Device()

# The actual Worn recipe leaves four channels inactive inside its folder. Its
# folder mask/opacity must not make those outputs recompose, regardless of mode.
worn = sets.create(scene, 'Worn identity planning', 512)
for kind, color in (('AO', (.4, .4, .4, 1)), ('CURVATURE', (.8, .8, .8, 1))):
    entry = worn.baked_maps.add()
    entry.kind, entry.image = kind, images.create('Worn identity ' + kind, 16, color)
composer.apply(worn, 'Worn Painted Metal')
folder = next(layer for layer in worn.layers if layer.type == 'FOLDER')
folder_mask = folder.masks.add()
folder_mask.source, folder_mask.procedural = 'PROCEDURAL', 'FBM'
inactive = ('NORMAL', 'AO', 'EMISSION', 'OPACITY')
before = {channel: Builder(scene, worn, cache, device).signature(channel) for channel in CHANNELS}
for mode in ('PASSTHROUGH', 'ISOLATED'):
    folder.group_mode, folder.opacity = mode, .37
    folder_mask.black = .24
    after = {channel: Builder(scene, worn, cache, device).signature(channel) for channel in CHANNELS}
    for channel in inactive:
        assert untouched_plan(worn, channel) == before[channel] == after[channel]
        assert_cpu_parity(worn, channel)
    for channel in set(CHANNELS) - set(inactive):
        assert before[channel] != after[channel], 'A contributing Worn channel lost its folder dependency'

# Planning is disposable. It never marks authoritative output images clean or
# updates their revision; save/export still needs the latest complete CPU edit.
folder.opacity = .61
out = images.output(worn, 'AO')
revision = out.revision
assert out.dirty and worn.dirty
untouched_plan(worn, 'AO')
assert out.dirty and worn.dirty and out.revision == revision

ts = sets.create(scene, 'Constant identity planning', 512)
outer = stack.add(ts, 'FOLDER', 'Outer')
outer.group_mode, outer.opacity = 'PASSTHROUGH', .53
outer_mask = outer.masks.add()
outer_mask.source, outer_mask.procedural = 'PROCEDURAL', 'FBM'
inner = stack.add(ts, 'FOLDER', 'Inner', outer.id)
inner.group_mode, inner.opacity = 'PASSTHROUGH', .71
layer = stack.add(ts, 'FILL', 'Default color', inner.id)
layer.opacity = .61
prop = stack.channel_for(layer, 'BASE_COLOR')
prop.color = (.5, .5, .5, .27)
mask = layer.masks.add()
mask.source, mask.procedural = 'PROCEDURAL', 'FBM'
signature = untouched_plan(ts, 'BASE_COLOR')
assert_cpu_parity(ts)
outer.opacity, inner.opacity, layer.opacity, mask.black = .91, .43, .33, .41
assert untouched_plan(ts, 'BASE_COLOR') == signature
assert_cpu_parity(ts)

# A passthrough wrapper's own blend mode does not alter the child/backdrop rule.
outer.blend_mode = 'ADD'
assert untouched_plan(ts, 'BASE_COLOR') == signature
assert_cpu_parity(ts)
outer.blend_mode = 'NORMAL'

# Isolated children contribute coverage even when their RGB equals the root
# default. Keep them, especially when nested inside an attenuated passthrough.
inner.group_mode = 'ISOLATED'
records, _ = assert_cpu_parity(ts)
assert records[0]['children'][0]['children'], 'An isolated fill lost its coverage'
inner.group_mode = 'PASSTHROUGH'

# A different color, non-Normal blend, filter, texture or procedural remains a
# real dependency; future edits must change signatures and produce new pixels.
prop.color = (.2, .37, .71, .27)
records, changed = assert_cpu_parity(ts)
assert records and changed != signature
prop.color = (.5, .5, .5, .27)
prop.blend_mode = 'MULTIPLY'
assert assert_cpu_parity(ts)[0]
prop.blend_mode = 'INHERIT'
effect = prop.effects.add()
effect.kind, effect.gamma = 'GAMMA', 1.8
assert assert_cpu_parity(ts)[0]
prop.effects.clear()
prop.procedural, prop.procedural_detail = 'FBM', .4
assert assert_cpu_parity(ts)[0]
prop.procedural = ''
prop.image = images.create('Identity image source', 16, (.5, .5, .5, .27))
assert assert_cpu_parity(ts)[0]
prop.image = None
prop.image_name = 'Missing identity source'
try:
    Builder(scene, ts, cache, device).signature('BASE_COLOR')
except ValueError as exc:
    assert 'Texture image is missing' in str(exc)
else:
    raise AssertionError('An unresolved image was folded into a constant')
prop.image_name = ''

# After a changing backdrop, the root default color is not an identity anymore.
ts.layers.clear()
base = stack.add(ts, 'FILL', 'Different backdrop')
stack.channel_for(base, 'BASE_COLOR').color = (.19, .31, .73, 1)
top = stack.add(ts, 'FILL', 'Return to default')
stack.channel_for(top, 'BASE_COLOR').color = (.5, .5, .5, .47)
records, _ = assert_cpu_parity(ts)
assert len(records) == 2, 'A default fill was removed above an unknown backdrop'

# Transparent isolated coverage is not an opaque black backdrop. This child is
# essential: a following Multiply child blends against the established coverage.
ts.layers.clear()
group = stack.add(ts, 'FOLDER', 'Coverage-sensitive isolated')
group.group_mode, group.opacity = 'ISOLATED', .67
dark = stack.add(ts, 'FILL', 'Black coverage', group.id)
stack.channel_for(dark, 'EMISSION').enabled = True
stack.channel_for(dark, 'EMISSION').color = (0, 0, 0, .37)
light = stack.add(ts, 'FILL', 'Light multiply', group.id)
light_prop = stack.channel_for(light, 'EMISSION')
light_prop.enabled, light_prop.color, light_prop.blend_mode = True, (.6, .7, .8, .6), 'MULTIPLY'
records, _ = assert_cpu_parity(ts, 'EMISSION')
assert len(records[0]['children']) == 2

open_painter.unregister()
print('LIVE_IDENTITY_PLANNING_PASS: Worn inactive channels, pure no-op signatures, latest-output dirty state, '
      'constant fills, nested passthrough, isolated coverage, retained changing inputs, missing images, CPU parity', flush=True)
