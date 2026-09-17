"""Shared scalar images retain editable PBR values and exact CPU/live inputs."""
import sys
from pathlib import Path
from unittest.mock import patch

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import buffers, channel_eval, live_inputs
from open_painter.evaluation.blend_modes import sample_uv
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.live_inputs import Builder
from open_painter.evaluation.metrics import Metrics
from open_painter.evaluation.raster_cache import RasterCache
from open_painter.painting import projection
from open_painter.texture_engine import procedurals


class Cache:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def put(self, key, value, size):
        self.values[key] = value


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


open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Shared scalar material detail', 512)
ts.preview_backend = 'CPU'  # Exercise baked-output dirtiness and raster cache parity.
LayerEvaluator(scene).evaluate_texture_set(ts.id)
layer = stack.add(ts, 'FILL')
base, rough, height, normal = [stack.channel_for(layer, key)
                               for key in ('BASE_COLOR', 'ROUGHNESS', 'HEIGHT', 'NORMAL')]
texture = images.create('Scalar surface input', 32)
y, x = np.indices((32, 32), dtype=np.float32)
values = np.ones((32, 32, 4), np.float32)
values[..., :3] = (.1 + .8 * (x + y * .3) / 41.3)[..., None]
images.write(texture, values, pack=False)
for prop in (base, rough, height, normal):
    prop.enabled = True
    prop.procedural = 'FBM'  # Retained authoring metadata must not add a second pattern.
    prop.detail_image = texture
    prop.procedural_detail, prop.procedural_height = .4, -.08
base.color, rough.value = (.2, .35, .6, .75), .48
normal.color = (.5, .5, 1., 1.)
n = 128
u, v = np.meshgrid((np.arange(n, dtype=np.float32) + .5) / n,
                   (np.arange(n, dtype=np.float32) + .5) / n)
reference = sample_uv(values, u, v)[..., :3] @ np.array((.2126, .7152, .0722), np.float32)
resources = RasterCache()
with patch.object(procedurals, 'evaluate', side_effect=AssertionError('Detail added procedural noise')):
    for prop in (base, rough, height):
        actual = channel_eval.source(prop, 'FILL', n, 0, n, {}, ts, resources)
        if prop == base:
            expected = np.asarray(base.color[:3]) * (1 + (reference - .5) * .4)[..., None]
            np.testing.assert_allclose(actual[..., :3], expected, atol=1e-7)
            np.testing.assert_allclose(actual[..., 3], .75, atol=1e-7)
        else:
            expected = (.5 + (reference - .5) * -.08 if prop == height else
                        .48 + (reference - .5) * .4 * .35)
            np.testing.assert_allclose(actual[..., 0], expected, atol=1e-7)
    normal_full = channel_eval.source(normal, 'FILL', n, 0, n, {}, ts, resources)
    normal_tiles = np.concatenate([
        channel_eval.source(normal, 'FILL', n, low, min(low + 17, n), {}, ts, resources)
        for low in range(0, n, 17)])
    np.testing.assert_array_equal(normal_full, normal_tiles)

# Opt-in measurements count actual sampling separately from scalar strip reuse.
measured_resources = RasterCache()
with Metrics() as cold:
    channel_eval._detail_tile(base, n, 0, n, ts, measured_resources)
assert cold.counts['detail_sampled_strips'] == n // channel_eval.TILE_ROWS
assert cold.counts['detail_sampled_pixels'] == n * n
assert cold.counts['detail_sampled_bytes'] == n * n * 4
assert cold.phases['source_generation']['calls'] == n // channel_eval.TILE_ROWS
assert cold.counts['detail_decoded_bytes'] == 32 * 32 * 4
decoded = [value for key, value in measured_resources.items()
           if isinstance(key, tuple) and key[0] == 'decoded_detail_scalar']
assert len(decoded) == 1 and decoded[0].shape == (32, 32, 1)
assert decoded[0].dtype == np.float32 and not decoded[0].flags.writeable
assert all(value.dtype == np.float32 for key, value in measured_resources.items()
           if isinstance(key, tuple) and key[0] == 'detail_scalar'), \
    'A sampled scalar strip retained promoted float64 coordinates'
assert not any(isinstance(key, tuple) and key[0] == 'decoded_image' for key in measured_resources), \
    'Scalar detail retained the unnecessary decoded RGBA image'
with Metrics() as warm:
    channel_eval._detail_tile(rough, n, 0, n, ts, measured_resources)
assert warm.counts['detail_reused_strips'] == n // channel_eval.TILE_ROWS
assert warm.counts['detail_sampled_strips'] == 0 and warm.phases['source_generation']['calls'] == 0

# Pre-reducing float32 luminance must agree with the previous RGBA-first path,
# including non-gray inputs, sRGB decoding, filtering and world projection.
rgb_texture = images.create('Detail reduction precision', 32)
rgb_values = np.random.default_rng(71).random((32, 32, 4), dtype=np.float32)
rgb_values[..., 3] = 1
images.write(rgb_texture, rgb_values, pack=False)
positions = np.stack((u * 1.4 - .2, v * .9, (u + v) * .23), axis=-1)
normals = np.stack((u - .5, v - .5, np.ones_like(u)), axis=-1)
normals /= np.linalg.norm(normals, axis=-1, keepdims=True)
base.detail_image = rgb_texture
base.scale, base.rotation, base.offset = (1.7, .8), .31, (.2, -.1)
for color_space in ('Non-Color', 'sRGB'):
    rgb_texture.colorspace_settings.name = color_space
    for projection_kind in ('UV', 'TRIPLANAR'):
        base.projection = projection_kind
        for filtering in ('NEAREST', 'BILINEAR'):
            base.filtering = filtering
            for tiling in ('REPEAT', 'NONE'):
                base.tiling = tiling
                geometry = {'geometry': (positions, normals)}
                old_rgba = channel_eval._image_source(rgb_texture, base, 'FILL', n, 0, n,
                                                      {}, ts, geometry)
                old_scalar = old_rgba[..., :3] @ np.array((.2126, .7152, .0722), np.float32)
                reduced = channel_eval._detail_tile(base, n, 0, n, ts, geometry)
                np.testing.assert_allclose(reduced, old_scalar, rtol=0, atol=3e-7,
                                           err_msg=str((color_space, projection_kind, filtering, tiling)))
base.detail_image = texture
base.projection = 'UV'

# UV transform, filtering and transparent borders use the ordinary image sampler.
base.scale, base.rotation, base.offset = (1.7, .8), .31, (.2, -.1)
base.filtering, base.tiling = 'NEAREST', 'NONE'
su, sv = channel_eval._coordinates(base, n, 0, n)
transformed = sample_uv(values, su, sv, 'NEAREST', 'NONE')[..., :3] @ np.array((.2126, .7152, .0722), np.float32)
np.testing.assert_allclose(channel_eval._detail_tile(base, n, 0, n, ts, resources), transformed, atol=1e-7)
actual = channel_eval.source(base, 'FILL', n, 0, n, {}, ts, resources)
np.testing.assert_array_equal(actual[..., 3], .75 * ((su >= 0) & (su <= 1) & (sv >= 0) & (sv <= 1)))
base.scale, base.rotation, base.offset = (1, 1), 0, (0, 0)
base.filtering, base.tiling = 'BILINEAR', 'REPEAT'

# Three channels share one scalar leaf. Uniform edits do not read, sample, upload,
# regenerate, resize or write source images, and planning performs no raster work.
cache, device = Cache(), Device()
builder = Builder(scene, ts, cache, device)
with patch.object(live_inputs, '_detail_tile', side_effect=AssertionError('Signature sampled detail')), \
     patch.object(images, 'pixels', side_effect=AssertionError('Signature decoded image')):
    signature = builder.signature('ROUGHNESS')
leaves, built = finish(builder.channel('ROUGHNESS'))
assert signature == built
shared_leaf = leaves[0]['source']['field']
finish(Builder(scene, ts, cache, device).channel('BASE_COLOR'))
finish(Builder(scene, ts, cache, device).channel('HEIGHT'))
assert device.uploads == 1, 'A shared scalar was uploaded separately for PBR channels'
rough.value, rough.procedural_detail, height.procedural_height = .66, .7, -.12
with patch.object(images, 'pixels', side_effect=AssertionError('Uniform edit decoded a source')), \
     patch.object(images, 'write', side_effect=AssertionError('Uniform edit wrote an image')), \
     patch.object(live_inputs, '_detail_tile', side_effect=AssertionError('Uniform edit sampled a source')):
    leaves, changed = finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
    assert changed != signature and leaves[0]['source']['field'] is shared_leaf
    finish(Builder(scene, ts, cache, device).channel('HEIGHT'))
assert device.uploads == 1 and tuple(texture.size) == (32, 32) and ts.resolution == '512'
rough.filtering = 'NEAREST'
finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
assert device.uploads == 2, 'Filtering change retained stale sampled detail'

# Managed pixel changes invalidate only consuming outputs and replace the leaf.
for out in ts.outputs:
    out.dirty = False
values[..., :3] = .83
images.write(texture, values, pack=False)
assert {out.channel for out in ts.outputs if out.dirty} == {'BASE_COLOR', 'ROUGHNESS', 'HEIGHT', 'NORMAL'}
leaves, _ = finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
np.testing.assert_allclose(leaves[0]['source']['field'], .83, atol=1e-7)
assert device.uploads == 3

# Scalar working buffers must remain exactly equal to the full RGB reference.
# The evaluator's per-tile source pre-warm must also honour the detail image:
# the retained procedural is never generated, only to be discarded.
with patch.object(procedurals, 'evaluate', side_effect=AssertionError('Evaluator pre-warmed unused noise')), \
     patch.object(channel_eval, '_detail_tile', wraps=channel_eval._detail_tile) as detail_tile:
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
assert detail_tile.call_count, 'Evaluator skipped pre-warming the detail image'
expected = {out.channel: images.pixels(out.image) for out in ts.outputs}
with patch.object(buffers, 'scalar_compatible', return_value=False):
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
for out in ts.outputs:
    np.testing.assert_array_equal(images.pixels(out.image), expected[out.channel])

# A shared detail pixel edit must dirty a published channel's consumers, even
# when those consumers do not directly reference the source image themselves.
reference_ts = sets.create(scene, 'Published generated detail', 512)
reference_ts.preview_backend = 'CPU'
published = stack.add(reference_ts, 'FILL', 'Published roughness')
consumer = stack.add(reference_ts, 'FILL', 'Color from published detail')
for prop in published.channels:
    prop.enabled = prop.channel == 'ROUGHNESS'
for prop in consumer.channels:
    prop.enabled = prop.channel == 'BASE_COLOR'
published_prop = stack.channel_for(published, 'ROUGHNESS')
published_prop.detail_image, published_prop.procedural_detail = texture, 2
stack.channel_for(consumer, 'BASE_COLOR').color = (.8, .2, .05, 1)
mask = consumer.masks.add()
mask.id, mask.source = 'reference-generated-detail', 'REFERENCE'
mask.reference_layer, mask.reference_channel = published.id, 'ROUGHNESS'
LayerEvaluator(scene).evaluate_texture_set(reference_ts.id)
old_color = images.pixels(images.output(reference_ts, 'BASE_COLOR').image)
values[..., :3] = .12
images.write(texture, values, pack=False)
assert {out.channel for out in reference_ts.outputs if out.dirty} == {'ROUGHNESS', 'BASE_COLOR'}
LayerEvaluator(scene).evaluate_texture_set(reference_ts.id)
latest = images.pixels(images.output(reference_ts, 'BASE_COLOR').image)
assert np.max(np.abs(latest - old_color)) > .05
LayerEvaluator(scene).evaluate_texture_set(reference_ts.id, force=True)
np.testing.assert_array_equal(images.pixels(images.output(reference_ts, 'BASE_COLOR').image), latest)

# Triplanar detail retains evaluated geometry after material-only edits.
ref = ts.assignments.add()
ref.object, ref.slot_index = scene.objects['Cube'], 0
rough.projection = 'TRIPLANAR'
with patch.object(projection, 'geometry', wraps=projection.geometry) as geometry:
    finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
assert geometry.call_count == 1
uploads = device.uploads
rough.value = .3
with patch.object(projection, 'geometry', side_effect=AssertionError('Scalar edit rebuilt geometry')), \
     patch.object(live_inputs, '_detail_tile', side_effect=AssertionError('Scalar edit rebuilt triplanar detail')):
    finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
assert device.uploads == uploads

# Shared image IDs cannot alias scene-scoped texture-set cache owners.
other_scene = scene.copy()
other = other_scene.open_painter.texture_sets[0]
assert other.id == ts.id
first = Builder(scene, ts, cache, device)
second = Builder(other_scene, other, cache, device)
assert first._field_key(rough) != second._field_key(stack.channel_for(other.layers[0], 'ROUGHNESS'))

# A missing source is actionable; legacy procedural metadata cannot hide it.
rough.detail_image = None
rough.detail_image_name = 'Lost detail'
for evaluate in (lambda: channel_eval.source(rough, 'FILL', n, 0, n, {}, ts, {}),
                 lambda: Builder(scene, ts, cache, device).signature('ROUGHNESS'),
                 lambda: buffers.scalar_source(rough, 'FILL', n, 0, n, ts, {})):
    try:
        evaluate()
    except ValueError as error:
        assert 'Surface detail image is missing: Lost detail' in str(error)
    else:
        raise AssertionError('Missing detail silently reverted to procedural noise')
from open_painter.evaluation import cache as scheduler
output = images.output(ts, 'ROUGHNESS')
last_complete = images.pixels(output.image)
try:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
except ValueError as error:
    assert 'Surface detail image is missing: Lost detail' in str(error)
else:
    raise AssertionError('A missing detail source was marked complete')
assert ts.dirty and output.dirty and scheduler.job_state(scene, ts)['state'] == 'FAILED'
np.testing.assert_array_equal(images.pixels(output.image), last_complete)
rough.detail_image_name = ''
rough.procedural = ''
legacy = channel_eval.source(rough, 'FILL', n, 0, n, {}, ts, {})
np.testing.assert_allclose(legacy[:3], .3, atol=1e-7)

# Images an artist picks arrive sRGB-tagged (image.open's default for PNG/JPG):
# a Normal Combine detail map is data and is read as stored without retagging
# the datablock (the same image may be a colour fill elsewhere), a painted mask
# is decoded like the shader's Image Texture node samples it.
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.utils.ids import new_id
opened = bpy.data.images.new('Opened normal map', width=8, height=8)
opened.colorspace_settings.name = 'sRGB'
opened.pixels.foreach_set(np.tile(np.array([.5, .5, 1., 1.], np.float32), 8 * 8))
normal = stack.channel_for(layer, 'NORMAL')
combine = normal.effects.add()
combine.kind = 'NORMAL_COMBINE'
combine.image = opened
assert opened.colorspace_settings.name == 'sRGB', opened.colorspace_settings.name
flat = np.full((8, 8, 3), (.5, .5, 1.), np.float32)
combined = channel_eval.apply_effect(np.concatenate((flat, np.ones((8, 8, 1), np.float32)), axis=-1), combine)
np.testing.assert_allclose(combined[..., :3], flat, atol=1.5 / 255)   # stored .5, not the decoded .214
assert opened.colorspace_settings.name == 'sRGB', opened.colorspace_settings.name
grey = bpy.data.images.new('Opened mask', width=8, height=8)
grey.colorspace_settings.name = 'sRGB'
grey.pixels.foreach_set(np.full(8 * 8 * 4, .5, np.float32))
painted = layer.masks.add()
painted.id, painted.source, painted.image = new_id(), 'PAINT', grey
coverage = evaluate_masks(ts, layer, 8, 0, 8, {}, {})
assert abs(float(coverage.mean()) - .214) < .01, float(coverage.mean())   # .5 decoded from sRGB
layer.masks.clear()
normal.effects.remove(len(normal.effects) - 1)

open_painter.unregister()
print('GENERATED_DETAIL_INPUTS_PASS', flush=True)
