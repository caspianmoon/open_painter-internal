"""Copied scenes retain authoring IDs but must own distinct pending jobs."""
import sys
from pathlib import Path
from time import monotonic
import bpy
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation import cache
from open_painter.evaluation.evaluator import LayerEvaluator

open_painter.register()
a = bpy.context.scene
ta = sets.create(a, 'Original', 512)
ta.preview_backend = 'CPU'  # The queued raster scheduler is the subject of this test.
la = stack.add(ta, 'FILL')
LayerEvaluator(a).evaluate_texture_set(ta.id)
b = a.copy()
tb = b.open_painter.texture_sets[0]
assert ta.id == tb.id
# Copies can deliberately share output images; isolate outputs to test scheduler ownership.
tb.outputs.clear()
tb.material = None
LayerEvaluator(b).evaluate_texture_set(tb.id)
stack.channel_for(la, 'ROUGHNESS').value = .21
stack.channel_for(tb.layers[0], 'ROUGHNESS').value = .79
key = getattr(cache, 'key_for', lambda scene, ts: ts.id)
cache._pending[key(a, ta)] = (ta.edit_serial, monotonic())
cache._pending[key(b, tb)] = (tb.edit_serial, monotonic())
assert len(cache._pending) == 2, 'Copied scenes collide in scheduler pending identity'
for scene, ts in ((a, ta), (b, tb)):
    cache._pending[key(scene, ts)] = (ts.edit_serial, monotonic()-1)
for _ in range(4000):
    cache.tick()
    if not ta.dirty and not tb.dirty:
        break
assert not ta.dirty and not tb.dirty
for ts, expected in ((ta, .21), (tb, .79)):
    np.testing.assert_allclose(images.pixels(images.output(ts, 'ROUGHNESS').image)[..., :3], expected, atol=1e-6)
# Deletion prunes runtime entries even when no evaluator was allocated yet.
cache._pending[key(b, tb)] = (tb.edit_serial, monotonic())
bpy.data.scenes.remove(b)
cache.tick()
assert not cache._pending
cache.on_load(None)
assert not cache._jobs and not cache._pending
open_painter.unregister()
print('MULTI_SCENE_SCHEDULING_PASS', flush=True)
