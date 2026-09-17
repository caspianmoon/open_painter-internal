"""Exact live leaves and dependency reuse, without requiring a window GPU context.

The device below substitutes owned NumPy arrays only for GPU upload. Numerical
GPU blending and native-image sampling are covered by separate GUI tests.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.channel_eval import source
from open_painter.evaluation.image_snapshots import ImageSnapshots
from open_painter.evaluation.live_inputs import Builder
from open_painter.evaluation import live_inputs
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.painting import projection
from open_painter.generators import scene_fields


class Cache:
    def __init__(self, capacity=None):
        self.values = {}
        self.capacity = capacity

    def get(self, key):
        return self.values.get(key)

    def put(self, key, texture, size):
        self.values[key] = texture
        while self.capacity is not None and len(self.values) > self.capacity:
            self.values.pop(next(iter(self.values)))


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


def pure_signature(builder, channel):
    with patch.object(builder.cache, 'get', side_effect=AssertionError('Signature queried GPU cache')), \
         patch.object(builder.cache, 'put', side_effect=AssertionError('Signature wrote GPU cache')), \
         patch.object(builder.device, 'upload', side_effect=AssertionError('Signature uploaded a texture')), \
         patch.object(images, 'pixels', side_effect=AssertionError('Signature read image pixels')), \
         patch.object(live_inputs, 'source', side_effect=AssertionError('Signature generated source pixels')), \
         patch.object(live_inputs, '_procedural_tile', side_effect=AssertionError('Signature generated noise')), \
         patch.object(live_inputs, 'evaluate_masks', side_effect=AssertionError('Signature generated a mask')):
        return builder.signature(channel)


open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Live leaf test', 512)
layer = stack.add(ts, 'FILL')
prop = stack.channel_for(layer, 'ROUGHNESS')
prop.procedural, prop.value, prop.procedural_detail = 'FBM', .44, .28
cache, device = Cache(), Device()

builder = Builder(scene, ts, cache, device)
planned_signature = pure_signature(builder, 'ROUGHNESS')
layers, signature = finish(builder.channel('ROUGHNESS'))
assert planned_signature == signature
leaf = layers[0]['source']
expected = source(stack.channel_for(builder.ts.layers[0], 'ROUGHNESS'), 'FILL', 512, 0, 512,
                  ImageSnapshots(builder.resources, ts.edit_serial), builder.ts, builder.resources)
actual = np.clip(leaf['color'][0] + (leaf['field'] - .5) * leaf['detail'] * .35, 0, 1)
np.testing.assert_allclose(actual, expected[..., 0], atol=1e-7)
uploads = device.uploads
prop.value = .77
_, changed = finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
assert device.uploads == uploads and changed != signature, 'A scalar edit regenerated its noise input'
layer.opacity = .7
_, opacity_changed = finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
assert device.uploads == uploads and opacity_changed != changed

mask = layer.masks.add()
mask.source, mask.procedural, mask.black = 'PROCEDURAL', 'FBM', .1
builder = Builder(scene, ts, cache, device)
layers, signature = finish(builder.channel('ROUGHNESS'))
expected_mask = evaluate_masks(builder.ts, builder.ts.layers[0], 512, 0, 512,
                              ImageSnapshots(builder.resources, ts.edit_serial), builder.resources)
np.testing.assert_allclose(layers[0]['mask'], expected_mask, atol=1e-7)
uploads = device.uploads
prop.value = .33
finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
assert device.uploads == uploads, 'A scalar edit regenerated an unchanged mask'
mask.black = .3
finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
assert device.uploads == uploads + 1, 'A mask setting did not invalidate exactly its mask leaf'

normal = stack.channel_for(layer, 'NORMAL')
normal.enabled, normal.procedural, normal.procedural_height = True, 'FBM', .07
builder = Builder(scene, ts, cache, device)
layers, _ = finish(builder.channel('NORMAL'))
expected_normal = source(stack.channel_for(builder.ts.layers[0], 'NORMAL'), 'FILL', 512, 0, 512,
                         ImageSnapshots(builder.resources, ts.edit_serial), builder.ts, builder.resources)
np.testing.assert_allclose(layers[0]['source']['texture'], expected_normal, atol=2e-7)

# An unchanged image-backed fill and painted mask survive composition edits.
prop.procedural = ''
texture = images.create('Live image source', 32)
pixels = np.ones((32, 32, 4), np.float32)
pixels[..., :3] = np.linspace(.2, .8, 32)[None, :, None]
images.write(texture, pixels, pack=False)
prop.image = texture
mask.source, mask.image = 'PAINT', texture
effect = prop.effects.add()
effect.kind, effect.gamma = 'GAMMA', 1.8
layers, signature = finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
uploads = device.uploads
layer.opacity = .45
prop.blend_mode = 'MULTIPLY'
with patch.object(images, 'pixels', wraps=images.pixels) as reads:
    same_inputs, changed = finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
assert device.uploads == uploads and reads.call_count == 0
assert changed != signature
np.testing.assert_array_equal(layers[0]['source']['texture'], same_inputs[0]['source']['texture'])
np.testing.assert_array_equal(layers[0]['mask'], same_inputs[0]['mask'])
before_source = layers[0]['source']['texture'].copy()
before_mask = layers[0]['mask'].copy()
pixels[..., :3] = .95
images.write(texture, pixels, pack=False)
updated, _ = finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
assert device.uploads == uploads + 2, 'A managed image write must invalidate both consuming leaves'
assert np.max(abs(updated[0]['source']['texture'] - before_source)) > .05
assert np.max(abs(updated[0]['mask'] - before_mask)) > .05

# Channel-filter edits reuse decoded image pixels while generating new results.
effect.gamma = 2.3
with patch.object(images, 'pixels', wraps=images.pixels) as reads:
    finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
assert reads.call_count == 0, 'A filter parameter edit reread unchanged image pixels'

# Material-only edits retain evaluated triplanar geometry and its noise leaf.
prop.effects.clear()
prop.image = None
prop.procedural, prop.projection = 'FBM', 'TRIPLANAR'
layer.masks.clear()
ref = ts.assignments.add()
ref.object, ref.slot_index = scene.objects['Cube'], 0
stack.invalidate(ts)
with patch.object(projection, 'geometry', wraps=projection.geometry) as geometry:
    finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
assert geometry.call_count == 1
uploads = device.uploads
prop.value = .29
with patch.object(projection, 'geometry', wraps=projection.geometry) as geometry:
    finish(Builder(scene, ts, cache, device).channel('ROUGHNESS'))
assert geometry.call_count == 0 and device.uploads == uploads

# A Surface-space channel effect pads each island across seams, so its leaf
# follows the effect's space and direction and the mesh it pads across; a
# UV-space effect keeps its leaf through a re-unwrap (0.9.7).
from open_painter.evaluation import dependencies
prop.projection = 'UV'
seam_effect = prop.effects.add()
seam_effect.kind, seam_effect.radius, seam_effect.space = 'GAUSSIAN_BLUR', 4, 'SURFACE'
surface_signature = pure_signature(Builder(scene, ts, cache, device), 'ROUGHNESS')
seam_effect.space = 'UV'
uv_signature = pure_signature(Builder(scene, ts, cache, device), 'ROUGHNESS')
assert uv_signature != surface_signature, 'a Space toggle reused the old filtered leaf'
seam_effect.space = 'SURFACE'
assert pure_signature(Builder(scene, ts, cache, device), 'ROUGHNESS') == surface_signature
seam_effect.kind, seam_effect.direction = 'DIRECTIONAL_BLUR', (0., 0., -1.)
down_signature = pure_signature(Builder(scene, ts, cache, device), 'ROUGHNESS')
seam_effect.direction = (1., 0., 0.)
assert pure_signature(Builder(scene, ts, cache, device), 'ROUGHNESS') != down_signature, \
    'a Direction edit reused the old directional leaf'
seam_effect.kind = 'GAUSSIAN_BLUR'
seam_effect.space = 'UV'
uv_signature = pure_signature(Builder(scene, ts, cache, device), 'ROUGHNESS')
seam_effect.space = 'SURFACE'
padded_signature = pure_signature(Builder(scene, ts, cache, device), 'ROUGHNESS')
mesh = scene.objects['Cube'].data
for loop in mesh.uv_layers.active.data:
    loop.uv = (loop.uv.x * .9 + .05, loop.uv.y * .9 + .05)
mesh.update()
dependencies._mesh_epochs[mesh.as_pointer()] = dependencies.mesh_epoch(mesh) + 1
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)
assert pure_signature(Builder(scene, ts, cache, device), 'ROUGHNESS') != padded_signature, \
    'a re-unwrap reused the padding of the old UV layout'
seam_effect.space = 'UV'
assert pure_signature(Builder(scene, ts, cache, device), 'ROUGHNESS') == uv_signature, \
    'a re-unwrap regenerated a UV-space filtered leaf'
prop.effects.clear()
prop.projection = 'TRIPLANAR'

# Duplicate scene UUIDs remain independent cache owners.
other_scene = scene.copy()
other = other_scene.open_painter.texture_sets[0]
assert other.id == ts.id
first = Builder(scene, ts, cache, device)
second = Builder(other_scene, other, cache, device)
assert first.prefix != second.prefix and first.prefix[0][1] == second.prefix[0][1]
finish(second.channel('ROUGHNESS'))
assert {key[0] for key in cache.values}.issuperset({first.prefix[0], second.prefix[0]})

# Eviction drops cache ownership, not references held by an executable plan.
mask = layer.masks.add()
mask.source, mask.procedural = 'PROCEDURAL', 'FBM'
evicting = Cache(capacity=1)
builder = Builder(scene, ts, evicting, device)
planned_signature = pure_signature(builder, 'ROUGHNESS')
layers, resolved_signature = finish(builder.channel('ROUGHNESS'))
assert planned_signature == resolved_signature
kept_mask = layers[0]['mask'].copy()
kept_source = layers[0]['source']['field'].copy()
evicting.values.clear()
assert pure_signature(builder, 'ROUGHNESS') == resolved_signature
np.testing.assert_array_equal(layers[0]['mask'], kept_mask)
np.testing.assert_array_equal(layers[0]['source']['field'], kept_source)

# Explicit proxy size changes only the captured preview and its pixel radii.
preview_ts = sets.create(scene, 'Explicit live proxy', 1024)
preview_layer = stack.add(preview_ts, 'FILL')
preview_prop = stack.channel_for(preview_layer, 'ROUGHNESS')
preview_prop.procedural = 'FBM'
preview_effect = preview_prop.effects.add()
preview_effect.kind, preview_effect.radius = 'GAUSSIAN_BLUR', 4
preview_ts.preview_quality = '512'
builder = Builder(scene, preview_ts, cache, device)
assert builder.n == 512 and builder.ts.resolution == '512'
assert builder.ts.layers[0].channels[1].effects[0].radius == 2
layers, signature = finish(builder.channel('ROUGHNESS'))
assert layers[0]['source']['texture'].shape == (512, 512, 4) and signature[0] == 512
assert preview_ts.resolution == '1024' and preview_effect.radius == 4
assert all(tuple(output.image.size) == (1024, 1024) for output in preview_ts.outputs)

# Bounded scene analysis retains authoring provenance while its result is sampled
# at the explicit preview size; it must not rebake once per preview row strip.
scene_mask = preview_layer.masks.add()
scene_mask.id, scene_mask.source = 'live-preview-scene-field', 'SCENE'
scene_mask.influence.kind, scene_mask.influence.mode = 'UP', 'LIVE'
assignment = preview_ts.assignments.add()
assignment.object, assignment.slot_index = scene.objects['Cube'], 0
with patch.object(scene_fields, 'compute', wraps=scene_fields.compute) as analysis:
    finish(Builder(scene, preview_ts, cache, device).channel('ROUGHNESS'))
assert analysis.call_count == 1, 'Scene analysis repeated for each preview strip'
assert scene_mask.influence.provenance == repr(scene_fields.signature(preview_ts, scene_mask.influence))
assert tuple(scene_mask.influence.image.size) == (64, 64)
preview_prop.opacity = .6
with patch.object(scene_fields, 'compute', wraps=scene_fields.compute) as analysis:
    finish(Builder(scene, preview_ts, cache, device).channel('ROUGHNESS'))
assert analysis.call_count == 0, 'A composition edit repeated independent scene analysis'

# Published inputs retain transitive authoring revisions and stable source IDs.
reference_ts = sets.create(scene, 'Live published inputs', 512)
published = stack.add(reference_ts, 'FILL', 'Published source')
published_prop = stack.channel_for(published, 'ROUGHNESS')
published_prop.value = .2
consumer = stack.add(reference_ts, 'FILL', 'Reference consumer')
reference = consumer.masks.add()
reference.id, reference.source = 'live-reference', 'REFERENCE'
reference.reference_layer, reference.reference_channel = published.id, 'ROUGHNESS'
layers, before_signature = finish(Builder(scene, reference_ts, cache, device).channel('ROUGHNESS'))
np.testing.assert_allclose(layers[1]['mask'], .2, atol=1e-7)
published_prop.value = .7
layers, after_signature = finish(Builder(scene, reference_ts, cache, device).channel('ROUGHNESS'))
np.testing.assert_allclose(layers[1]['mask'], .7, atol=1e-7)
assert before_signature != after_signature

# Failed leaves never enter the GPU cache, and retry can rebuild that same key.
class FailingDevice(Device):
    def upload(self, array):
        raise RuntimeError('Injected live leaf upload failure')


failure_cache = Cache()
builder = Builder(scene, ts, failure_cache, FailingDevice())
try:
    finish(builder.channel('ROUGHNESS'))
except RuntimeError as error:
    assert str(error) == 'Injected live leaf upload failure'
else:
    raise AssertionError('An upload failure was swallowed')
assert not failure_cache.values and not builder.resources.required
finish(Builder(scene, ts, failure_cache, device).channel('ROUGHNESS'))
assert failure_cache.values

# A captured edit does not silently incorporate later parameter changes. The
# scheduler, not this leaf builder, discards that superseded revision's output.
builder = Builder(scene, reference_ts, cache, device)
captured_serial = builder.ts.edit_serial
published_prop.value = .4
assert reference_ts.edit_serial != captured_serial
layers, _ = finish(builder.channel('ROUGHNESS'))
assert abs(layers[0]['source']['color'][0] - .7) < 1e-7
layers, _ = finish(Builder(scene, reference_ts, cache, device).channel('ROUGHNESS'))
assert abs(layers[0]['source']['color'][0] - .4) < 1e-7

# Native float paint and its exact CPU precision fallback describe the same
# authored source. Pure planning must never create a native GPU texture.
import gpu
paint_layer = stack.add(reference_ts, 'PAINT', 'Native signature source')
paint_prop = stack.channel_for(paint_layer, 'BASE_COLOR')
paint_prop.enabled = True
paint_prop.image = images.create('Native paint signature', 512, (.1, .2, .3, .5), True)
builder = Builder(scene, reference_ts, cache, device)
with patch.object(gpu.texture, 'from_image', side_effect=AssertionError('Signature acquired a native GPU texture')):
    native_signature = pure_signature(builder, 'BASE_COLOR')
with patch.object(gpu.texture, 'from_image', return_value=SimpleNamespace(format='RGBA32F')):
    _, resolved_native_signature = finish(builder.channel('BASE_COLOR'))
assert native_signature == resolved_native_signature
with patch.object(gpu.texture, 'from_image', return_value=SimpleNamespace(format='RGBA16F')):
    _, fallback_signature = finish(Builder(scene, reference_ts, cache, device).channel('BASE_COLOR'))
assert native_signature == fallback_signature

# Reject oversized live resources before allocating displays; preserve authored
# resolution, and accept a separately selected preview instead of downsampling it.
from open_painter.evaluation import live_preview
reference_ts.resolution = '8192'
try:
    live_preview.check_resources(scene, reference_ts, 8192)
except RuntimeError as exc:
    assert 'budget' in str(exc)
else:
    raise AssertionError('8K live displays bypassed the resource preflight')
assert reference_ts.resolution == '8192'
reference_ts.preview_quality = '512'
live_preview.check_resources(scene, reference_ts, 512)
assert reference_ts.resolution == '8192'
reference_ts.resolution = '512'

open_painter.unregister()
print('LIVE_INPUT_DEPENDENCIES_PASS: exact leaves, uniform/image/filter/geometry reuse, managed writes, '
      'scene identity, eviction safety, explicit preview resolution, references, failed leaves, '
      'immutable authoring revisions, scene analysis provenance, pure planning after eviction, '
      'native/fallback source identity', flush=True)
