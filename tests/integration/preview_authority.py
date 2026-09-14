"""Fair queued sets and latest authoritative save after partially evaluated work."""
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
scene = bpy.context.scene
for index in range(3):
    ts = sets.create(scene, f'Fair set {index}', 512)
    ts.preview_backend = 'CPU'  # Queue fairness and save authority concern raster jobs.
    layer = stack.add(ts, 'FILL')
    stack.channel_for(layer, 'BASE_COLOR').procedural = 'FBM'
    stack.channel_for(layer, 'BASE_COLOR').procedural_scale = 10 + index
    cache._pending[cache.key_for(scene, ts)] = (ts.edit_serial, monotonic() - 1)
scene.open_painter.active_texture_set = 1
cache.tick()
assert cache.job_state(scene, scene.open_painter.texture_sets[1])['state'] == 'RUNNING'
for _ in range(2):
    cache.tick()
assert all(cache.job_state(scene, ts)['state'] == 'RUNNING' for ts in scene.open_painter.texture_sets)
ts = scene.open_painter.texture_sets[0]
stack.channel_for(ts.layers[0], 'ROUGHNESS').value = .713
path = Path(__file__).resolve().parents[2] / '.blender-test/preview-authority.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
assert not cache._jobs
for ts in scene.open_painter.texture_sets:
    assert not ts.dirty
    assert all(tuple(out.image.size) == (512, 512) for out in ts.outputs)
bpy.ops.wm.open_mainfile(filepath=str(path))
ts = bpy.context.scene.open_painter.texture_sets[0]
np.testing.assert_allclose(images.pixels(images.output(ts, 'ROUGHNESS').image)[..., 0], .713, atol=1e-7)
open_painter.unregister()
print('PREVIEW_AUTHORITY_PASS')
