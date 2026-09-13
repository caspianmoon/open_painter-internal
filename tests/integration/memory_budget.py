"""Three sets share one allowance; eviction never changes authoritative pixels."""
import sys
from pathlib import Path
import bpy
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation import memory, raster_cache
from open_painter.evaluation.evaluator import LayerEvaluator

open_painter.register()
scene = bpy.context.scene
identities = []
for index in range(3):
    ts = sets.create(scene, f'Budget {index}', 512)
    layer = stack.add(ts, 'FILL')
    prop = stack.channel_for(layer, 'ROUGHNESS')
    prop.procedural = 'FBM'
    prop.procedural_seed = index + 1
    identities.append(ts.id)
expected = {}
for ts in scene.open_painter.texture_sets:
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    expected[ts.id] = images.pixels(images.output(ts, 'ROUGHNESS').image)
raster_cache.clear_shared()
memory.shared.allowance = 16384  # smaller than one source strip: skip admission
for ts in scene.open_painter.texture_sets:
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    assert memory.shared.used <= memory.shared.allowance
    np.testing.assert_array_equal(images.pixels(images.output(ts, 'ROUGHNESS').image), expected[ts.id])
raster_cache.clear_shared()
assert memory.shared.used == 0
from unittest.mock import patch
from open_painter.painting import projection
ts = scene.open_painter.texture_sets[-1]
ref = ts.assignments.add()
ref.object = bpy.context.object
stack.channel_for(ts.layers[0], 'ROUGHNESS').projection = 'TRIPLANAR'
with patch.object(projection, 'geometry', wraps=projection.geometry) as geometry:
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    assert geometry.call_count == 1, 'Oversized geometry rebuilt for each strip'
assert all(cache.required_geometry is None for cache in raster_cache._shared.values())
assert memory.shared.used <= memory.shared.allowance
# Cancellation also releases the mandatory working geometry reservation.
job = LayerEvaluator(scene).channel_steps(ts.id, 'ROUGHNESS')
next(job)
assert any(cache.required_geometry is not None for cache in raster_cache._shared.values())
job.close()
assert all(cache.required_geometry is None for cache in raster_cache._shared.values())
memory.shared.allowance = 256 * 1024 * 1024
open_painter.unregister()
print('MEMORY_BUDGET_PASS')
