"""Local painted-mask edits through finite filters match full CPU composition."""
import sys
from pathlib import Path
import bpy
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation import metrics

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Dirty strips', 512)
folder = stack.add(ts, 'FOLDER')
layer = stack.add(ts, 'FILL')
layer.parent_id = folder.id
stack.channel_for(layer, 'BASE_COLOR').color = (.1, .3, .8, 1)
mask = folder.masks.add()
mask.source = 'PAINT'
mask.image = images.create('Local coverage', 512)
filtered = folder.masks.add()
filtered.source = 'FILTER'
filtered.effect.radius = 1.5
data = np.ones((512, 512, 4), np.float32)
evaluator = LayerEvaluator(scene)
for kind in ('LEVELS', 'GAUSSIAN_BLUR', 'DILATE', 'WARP'):
    filtered.effect.kind = kind
    data[..., :3] = .2
    images.write(mask.image, data)
    evaluator.evaluate_texture_set(ts.id, force=True)
    data[3:9, 20:40, :3] = .9
    images.write(mask.image, data)
    stack.invalidate(ts)
    with metrics.Metrics() as observation:
        job = evaluator.channel_steps(ts.id, 'BASE_COLOR',
                                      (0, 512, 3, 9, mask.image.as_pointer(), data.copy()))
        for _ in job:
            pass
    regional = images.pixels(images.output(ts, 'BASE_COLOR').image)
    evaluator.evaluate_texture_set(ts.id, force=True)
    expected = images.pixels(images.output(ts, 'BASE_COLOR').image)
    np.testing.assert_allclose(regional, expected, atol=1e-7, rtol=1e-6, err_msg=kind)
    if kind != 'WARP':
        assert observation.phases['masking']['calls'] < 10, observation.phases
# Native canvas detection retains the same mask region for every dependent channel.
from open_painter.painting import paint_session
from open_painter.evaluation import cache
filtered.effect.kind = 'GAUSSIAN_BLUR'
evaluator.evaluate_texture_set(ts.id, force=True)
project = scene.open_painter
project.paint_image = mask.image
project.paint_set_id = ts.id
project.paint_layer_id = folder.id
project.paint_mask = True
paint_session._rasters[scene.as_pointer()] = (mask.image.as_pointer(), images.pixels(mask.image))
data[400:404, 30:50, :3] = .8
mask.image.pixels.foreach_set(data.ravel())
mask.image.update()
assert paint_session.detect(scene, force=True)
region = cache._regions[cache.key_for(scene, ts)]
assert region[1] == '*' and region[2:4] == (400, 404), region[:4]
for out in ts.outputs:
    for _ in evaluator.channel_steps(ts.id, out.channel, region):
        pass
actual = {out.channel: images.pixels(out.image) for out in ts.outputs}
evaluator.evaluate_texture_set(ts.id, force=True)
for out in ts.outputs:
    np.testing.assert_array_equal(actual[out.channel], images.pixels(out.image))
project.paint_image = None
open_painter.unregister()
print('DIRTY_TILES_PASS')
