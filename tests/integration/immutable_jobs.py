"""Frozen authoring and bounded decoded-image reuse preserve CPU results."""
import sys
from pathlib import Path
import bpy
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation.snapshots import capture
from open_painter.evaluation.image_snapshots import ImageSnapshots
from open_painter.evaluation.raster_cache import RasterCache
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.metrics import Metrics
open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Immutable parameters', 512)
layer = stack.add(ts, 'FILL')
rough = stack.channel_for(layer, 'ROUGHNESS')
rough.value = .3
snapshot = capture(ts)
rough.value = .7
assert stack.channel_for(snapshot.layers[0], 'ROUGHNESS').value == np.float32(.3)
assert snapshot.as_pointer() == ts.as_pointer()
try:
    snapshot.resolution = '1024'
    raise AssertionError('Snapshot accepted a mutation')
except TypeError:
    pass
layer.masks.add().source = 'PAINT'
mask = layer.masks[0]
mask.image = images.create('Shared coverage',512,(.4,.4,.4,1))
resources = RasterCache(budget=32*1024*1024)
with Metrics() as counters:
    first = ImageSnapshots(resources, ts.edit_serial).read(mask.image)
    second = ImageSnapshots(resources, ts.edit_serial).read(mask.image)
assert first is second and not first.flags.writeable
assert counters.counts['image_read_bytes'] == first.nbytes
assert counters.counts['decoded_image_reuses'] == 1
# Different authoring serials cannot reuse unmanaged source snapshots.
third = ImageSnapshots(resources, ts.edit_serial+1).read(mask.image)
assert third is not first
# Eviction is transparent, including an allowance smaller than one source.
resources.manager.configure(0)
fourth = ImageSnapshots(resources, ts.edit_serial).read(mask.image)
assert resources.used == 0
np.testing.assert_array_equal(first, fourth)
# Managed writes produce a new image identity even inside one author revision.
images.write(mask.image,np.tile(np.array([.8,.8,.8,1],np.float32),(512,512,1)),pack=False)
fifth = ImageSnapshots(resources, ts.edit_serial).read(mask.image)
np.testing.assert_allclose(fifth[...,0],.8,atol=1e-7)
LayerEvaluator(scene).evaluate_texture_set(ts.id)
np.testing.assert_allclose(images.pixels(images.output(ts,'ROUGHNESS').image)[...,:3],.66,atol=1e-6)
open_painter.unregister()
print('IMMUTABLE_JOBS_PASS',flush=True)
