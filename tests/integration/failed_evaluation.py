"""Failures retain dirty work and last-good pixels without an automatic retry loop."""
import sys
from pathlib import Path
from time import monotonic
from unittest.mock import patch
import bpy
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation import cache
from open_painter.evaluation.evaluator import LayerEvaluator

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Failure recovery', 512)
ts.preview_backend = 'CPU'  # Test CPU job failure/retry and save-flush contracts explicitly.
layer = stack.add(ts, 'FILL')
LayerEvaluator(scene).evaluate_texture_set(ts.id)
output = images.output(ts, 'ROUGHNESS')
before, revision = images.pixels(output.image), output.revision
rough = stack.channel_for(layer, 'ROUGHNESS')
rough.value = .84
key = getattr(cache, 'key_for', lambda scene, ts: ts.id)(scene, ts)
cache._pending[key] = (ts.edit_serial, monotonic()-1)
def broken(*args, **kwargs):
    yield
    raise RuntimeError('Injected evaluation failure')
with patch.object(LayerEvaluator, 'channel_steps', side_effect=broken) as calls:
    for _ in range(10):
        cache.tick()
    assert ts.dirty and output.dirty, 'Failed evaluation was marked clean'
    assert calls.call_count == 1, 'Failed job retried without an edit or explicit retry'
assert cache.job_state(scene, ts)['state'] == 'FAILED'
assert 'Injected evaluation failure' in scene.open_painter.status
np.testing.assert_array_equal(images.pixels(output.image), before)
assert output.revision == revision
# Failed outputs also block export instead of passing off old pixels as current.
from tempfile import TemporaryDirectory
from open_painter.export.exporter import export
with TemporaryDirectory(prefix='op-failure-export-') as directory:
    ts.export.directory=directory
    with patch.object(LayerEvaluator, 'channel_steps', side_effect=broken):
        try:
            export(bpy.context, ts, ts.export)
            raise AssertionError('Export accepted a failed latest revision')
        except RuntimeError as exc:
            assert 'Injected evaluation failure' in str(exc)
    assert not list(Path(directory).iterdir())
# Retry is explicit and does not need a destructive edit or a resolution change.
cache.retry(scene, ts)
cache._pending[key] = (ts.edit_serial, monotonic()-1)
for _ in range(1000):
    cache.tick()
    if not ts.dirty: break
assert not ts.dirty and cache.job_state(scene,ts)['state']=='READY'
np.testing.assert_allclose(images.pixels(output.image)[..., :3], .84, atol=1e-6)
# A paused old job must not publish after another authoring edit, even if resumed directly.
rough.value = .41
job = LayerEvaluator(scene).channel_steps(ts.id, 'ROUGHNESS')
next(job)
revision = output.revision
rough.value = .64
for _ in job: pass
assert output.revision == revision and output.dirty
# Stop immediately on resume: an old job's decoded image cache must never seed
# a new revision's mask cache with old pixels after a source write.
mask=layer.masks.add(); mask.source='PAINT'
mask.image=images.create('Pending mask',512,(1,1,1,1))
stack.invalidate(ts)
job=LayerEvaluator(scene).channel_steps(ts.id,'ROUGHNESS')
next(job)  # decodes and caches the white mask, then yields before composition
images.write(mask.image,np.tile(np.array([0,0,0,1],np.float32),(512,512,1)),pack=False)
assert next(job, 'cancelled') == 'cancelled', 'Stale job continued computing with old decoded pixels'
LayerEvaluator(scene).evaluate_texture_set(ts.id)
np.testing.assert_allclose(images.pixels(output.image)[..., :3],.5,atol=1e-6)
layer.masks.clear()
stack.invalidate(ts)
# Saving drains the latest complete edit and cancels older preview buffers.
cache._jobs[key] = ('ROUGHNESS', LayerEvaluator(scene).channel_steps(ts.id, 'ROUGHNESS'))
next(cache._jobs[key][1])
with TemporaryDirectory(prefix='op-failure-save-') as directory:
    identifier=ts.id
    bpy.ops.wm.save_as_mainfile(filepath=directory+'/recovery.blend')
    assert not cache._jobs
    bpy.ops.wm.open_mainfile(filepath=directory+'/recovery.blend')
    ts=next(t for t in bpy.context.scene.open_painter.texture_sets if t.id==identifier)
    np.testing.assert_allclose(images.pixels(images.output(ts,'ROUGHNESS').image)[..., :3], .64, atol=1e-6)
    LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts.id)
    assert not ts.dirty
open_painter.unregister()
print('FAILED_EVALUATION_PASS', flush=True)
