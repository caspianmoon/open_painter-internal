"""Cached texture edits reuse sources; parameters, bakes, painting and force invalidate."""
import sys
from pathlib import Path
from unittest.mock import patch

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation import mask_eval, raster_cache, cache as lifecycle
from open_painter.texture_engine import procedurals

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Persistent cache', 512)
layer = stack.add(ts, 'FILL')
base, rough = stack.channel_for(layer, 'BASE_COLOR'), stack.channel_for(layer, 'ROUGHNESS')
base.color = (.12, .2, .32, 1)
for channel in (base, rough):
    channel.procedural = 'FBM'
    channel.procedural_scale = 5.37
    channel.procedural_detail = .8
entry = ts.baked_maps.add()
entry.kind = 'AO'
entry.image = images.create('Cache AO', 32)
data = np.ones((32, 32, 4), np.float32)
data[..., :3] = np.linspace(.1, .95, 32)[None, :, None]
images.write(entry.image, data)
mask = layer.masks.add()
mask.source, mask.generator = 'GENERATOR', 'CAVITY_DIRT'
mask.amount, mask.contrast = .2, 1


def pixels(channel):
    return images.pixels(images.output(ts, channel).image).copy()


LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
initial = pixels('ROUGHNESS')
shared = raster_cache.for_texture_set(scene, ts)
assert 0 < shared.used <= shared.budget
rough.value = .61
with patch.object(procedurals, 'evaluate', wraps=procedurals.evaluate) as noise, \
     patch.object(mask_eval, '_evaluate_masks', wraps=mask_eval._evaluate_masks) as masks:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    assert noise.call_count == masks.call_count == 0, 'Constant edit regenerated unchanged sources'
assert np.max(abs(pixels('ROUGHNESS') - initial)) > .02
assert raster_cache.for_texture_set(scene, ts) is shared

rough.procedural_scale = 8.9
before = pixels('ROUGHNESS')
with patch.object(procedurals, 'evaluate', wraps=procedurals.evaluate) as noise:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    assert noise.call_count > 0, 'Procedural scale change reused stale noise'
assert np.mean(abs(pixels('ROUGHNESS') - before)) > .005

before = pixels('BASE_COLOR')
mask.amount = .65
with patch.object(mask_eval, '_evaluate_masks', wraps=mask_eval._evaluate_masks) as masks:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    assert masks.call_count > 0, 'Generator setting change reused stale mask'
assert np.mean(abs(pixels('BASE_COLOR') - before)) > .005

before = pixels('BASE_COLOR')
base.offset = (.21, -.16)
with patch.object(procedurals, 'evaluate', wraps=procedurals.evaluate) as noise:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    assert noise.call_count > 0, 'UV transform change reused stale procedural'
assert np.mean(abs(pixels('BASE_COLOR') - before)) > .005

# A baker replaces its output image; map identity invalidates dependent generators.
replacement = images.create('Rebaked cache AO', 32, (.95, .95, .95, 1))
entry.image = replacement
assert ts.dirty and all(output.dirty for output in ts.outputs), 'Map replacement did not invalidate dependent outputs'
with patch.object(mask_eval, '_evaluate_masks', wraps=mask_eval._evaluate_masks) as masks:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    assert masks.call_count > 0, 'Rebaked map identity did not invalidate generator coverage'
before = pixels('BASE_COLOR')
# External in-place pixel edits have no property callback; explicit force rebuilds.
images.write(replacement, np.tile(np.array([0, 0, 0, 1], np.float32), (32, 32, 1)))
with patch.object(procedurals, 'evaluate', wraps=procedurals.evaluate) as noise:
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    assert noise.call_count > 0, 'Force evaluation failed to clear reusable sources'
assert np.mean(abs(pixels('BASE_COLOR') - before)) > .02
forced = {output.channel: pixels(output.channel) for output in ts.outputs}
stack.invalidate(ts)
LayerEvaluator(scene).evaluate_texture_set(ts.id)
for channel, expected in forced.items():
    np.testing.assert_array_equal(pixels(channel), expected)

# Paint buffers remain edit-serial dependent even when the image identity is stable.
layer.masks.clear()
mask = layer.masks.add()
mask.source = 'PAINT'
mask.image = images.create('Painted cache mask', 32, (1, 1, 1, 1))
stack.invalidate(ts)
LayerEvaluator(scene).evaluate_texture_set(ts.id)
images.write(mask.image, np.tile(np.array([0, 0, 0, 1], np.float32), (32, 32, 1)))
stack.invalidate(ts)
LayerEvaluator(scene).evaluate_texture_set(ts.id)
np.testing.assert_allclose(pixels('BASE_COLOR')[..., :3], .5, atol=1e-7)

assert raster_cache._shared
raster_cache.prune(set())
assert not raster_cache._shared
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
assert raster_cache._shared
lifecycle.on_load(None)
assert not raster_cache._shared
open_painter.unregister()
assert not raster_cache._shared
print('PERSISTENT_TEXTURE_CACHE_PASS: constant edits reuse, parameter/map/UV/paint invalidation, forced pixels, lifecycle cleanup', flush=True)
