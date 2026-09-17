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
assert all(not cache.required for cache in raster_cache._shared.values())
assert memory.shared.used <= memory.shared.allowance
# Cancellation also releases the mandatory working geometry reservation.
job = LayerEvaluator(scene).channel_steps(ts.id, 'ROUGHNESS')
next(job)
assert any(cache.required for cache in raster_cache._shared.values())
job.close()
assert all(not cache.required for cache in raster_cache._shared.values())
# The decal tangent frame is required by every strip of a Normal channel like the geometry:
# oversized, it is retained for the job and dropped with the geometry, never rebuilt per strip.
from open_painter.painting import decals
normal = images.create('Decal normal', 16)
images.write(normal, np.full((16, 16, 4), (.75, .5, .933, 1.), np.float32), pack=False)
decal = stack.add(ts, 'FILL', 'Decal')
empty = decals.create_object(bpy.context, decals.PREFIX + 'Budget', normal, size=.5)
empty[decals.MARKER] = decal.id
decal.decal_object, decal.decal_object_name = empty, empty.name
for prop in decal.channels:
    prop.projection, prop.tiling, prop.enabled = 'DECAL', 'NONE', False
prop = stack.channel_for(decal, 'NORMAL')
prop.image, prop.enabled = normal, True
builds = []
def counting(ts_, n_, geometry_cache=None):
    if geometry_cache is None or 'tangents' not in geometry_cache:
        builds.append(n_)
    return counting.real(ts_, n_, geometry_cache)
counting.real = projection.tangents
with patch.object(projection, 'tangents', counting):
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
assert builds == [512], ('Oversized tangents rebuilt for each strip', len(builds))
assert all(not cache.required for cache in raster_cache._shared.values())
job = LayerEvaluator(scene).channel_steps(ts.id, 'NORMAL')
next(job)
cache = raster_cache.for_texture_set(scene, ts)
assert 'tangents' in cache.required and 'geometry' in cache.required
bpy.context.object.location.x += 1.     # a moved target drops the retained frame with the geometry
bpy.context.view_layer.update()
assert 'tangents' not in raster_cache.for_texture_set(scene, ts) and not cache.required
job.close()
# Whole-image mask rasters the allowance refuses (or evicts between strips) are
# held by the job, like the geometry: a mesh mask and a vector stroke rasterise
# once per channel, not once per 64-row band of the mask stack.
from types import SimpleNamespace
from open_painter.evaluation import mesh_masks
from open_painter.painting import stroke_raster, strokes as records
from open_painter.utils.ids import new_id
worn = stack.add(ts, 'FILL', 'Worn')
stack.channel_for(worn, 'ROUGHNESS').value = .9
mask = worn.masks.add()
mask.id, mask.name, mask.source = new_id(), 'Cube', 'MESH'
mask.mesh.kind, mask.mesh.object = 'OBJECT', bpy.context.object
marks = stack.add(ts, 'PAINT', 'Marks')
x = bpy.context.object.matrix_world.translation.x + 1.
z = np.linspace(-.8, .8, 17, dtype=np.float32)
samples = np.stack([np.full(17, x, np.float32), np.zeros(17, np.float32), z, np.ones(17, np.float32),
                    np.zeros(17, np.float32), np.zeros(17, np.float32), np.ones(17, np.float32),
                    np.full(17, .25, np.float32)] + [np.zeros(17, np.float32)] * 4 + [np.ones(17, np.float32)], axis=1)
records.record(scene.open_painter, ts, marks, samples, SimpleNamespace(
    radius=.25, hardness=.9, opacity=1., flow=1., accumulate='MAX', alpha_image=None, projection='SURFACE',
    blend='PAINT', base_color=(1, 0, 0, 1), roughness=.9, metallic=0., height=.5, ao=1., emission=(0, 0, 0, 1),
    opacity_value=1., normal=(.5, .5, 1, 1), channels={'ROUGHNESS'}))
bpy.context.view_layer.update()
with patch.object(mesh_masks, 'raster', wraps=mesh_masks.raster) as mesh_raster, \
        patch.object(stroke_raster, '_rasterise', wraps=stroke_raster._rasterise) as stroke_rasterise:
    LayerEvaluator(scene).evaluate_channel(ts.id, 'ROUGHNESS')
assert mesh_raster.call_count == 1, ('Mesh mask rebuilt for each strip', mesh_raster.call_count)
assert stroke_rasterise.call_count == 1, ('Stroke rebuilt for each strip', stroke_rasterise.call_count)
assert memory.shared.used <= memory.shared.allowance
memory.shared.allowance = 256 * 1024 * 1024
open_painter.unregister()
print('MEMORY_BUDGET_PASS')
