"""The engine's Vulkan publication path, run on any backend (``runtime.PIXEL_PUBLISH``).

On Vulkan Blender creates image textures as storage, not render targets, so results reach
the set's images through their pixel buffers, and each such write makes Blender rebuild
the image's texture. Headless (``gpu.init``), with the path forced:

- the published pixels match the CPU reference on the texels the UVs cover;
- nothing is left for a later CPU sync (a Cycles viewport or a save reads nothing back);
- an evaluation publishes within its budget, the channels waiting longest first, and the set
  is not current until every changed channel is out;
- a bake publishes the held channels before Cycles samples the pixels, and a flush (render,
  export) publishes every channel at once;
- the engine report names the path and what the budget held back.
"""
import sys
from pathlib import Path
from time import monotonic
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import CHANNELS
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.engine import runtime, stages
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.export import padding
from open_painter.utils.ids import new_id

TOLERANCE = 2e-3

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
runtime.PRECISE = True
runtime.PIXEL_PUBLISH = True
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
ts = sets.create(scene, 'Pixel publication', 512)
sets.assign(scene, ts, bpy.context.object)
base = stack.add(ts, 'FILL', 'Base')
stack.channel_for(base, 'BASE_COLOR').color = (.3, .35, .4, 1.)
coat_id = stack.add(ts, 'FILL', 'Coat').id
coat = next(layer for layer in ts.layers if layer.id == coat_id)
for channel, value in (('BASE_COLOR', (.7, .2, .1, 1.)), ('ROUGHNESS', .3), ('METALLIC', .8)):
    prop = stack.channel_for(coat, channel)
    prop.enabled = True
    if channel == 'BASE_COLOR':
        prop.color = value
    else:
        prop.value = value
mask = coat.masks.add()
mask.id, mask.source, mask.procedural = new_id(), 'PROCEDURAL', 'CELLULAR'
material_manager.repair(ts)


def session():
    return shader_preview._sessions[shader_preview.key_for(scene, ts)]


def ready(seconds=60):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def compare(label):
    """The images' pixels (what the pixel path wrote) against the CPU reference."""
    engine = {channel: images.pixels(next(o.image for o in ts.outputs if o.channel == channel))[..., :3].copy()
              for channel in CHANNELS}
    evaluator = LayerEvaluator(scene)
    covered = padding.ownership(ts, int(ts.resolution)).covered
    errors = {}
    for channel in CHANNELS:
        output = evaluator.evaluate_channel(ts.id, channel)
        reference = np.clip(images.pixels(output.image if hasattr(output, 'image') else output)[..., :3], 0, 1)
        errors[channel] = float(np.abs(engine[channel] - reference)[covered].max())
    assert max(errors.values()) <= TOLERANCE, (label, errors)
    # The reference evaluator wrote its own result into the images: have the engine publish again.
    shader_preview.session_engine(scene, ts).published.clear()
    session()['dirty'] = True
    print('PIXEL_PUBLISH', label, 'max', round(max(errors.values()), 6), flush=True)
    return errors


# ---- 1. the pixel path matches the reference and leaves nothing for a CPU sync ----------------------------
ready()
assert runtime.device().pixel_publish
compare('first build')
ready()
engine = shader_preview.session_engine(scene, ts)
assert not engine.dirty_cpu, engine.dirty_cpu
with patch('gpu.texture.from_image', side_effect=AssertionError('a pixel-published channel was read back')):
    engine.sync_pixels()   # a Cycles viewport, a save, a bake: nothing to read back
print('PIXEL_PUBLISH nothing left for a CPU sync: PASS', flush=True)

# ---- 2. the budget: one channel per evaluation, the channels waiting longest first --------------------------
runtime.PIXEL_BUDGET_MS = 0.0
next(layer for layer in ts.layers if layer.id == coat_id).visible = False
order, ticks = [], 0
while True:
    shader_preview.tick(scene, ts)
    ticks += 1
    row = session()['history'][-1]
    if row['published']:
        assert len(row['published']) == 1, row['published']
        order += row['published']
        if engine.deferred:
            assert not shader_preview.current(scene, ts), 'current while channels are held back'
            assert set(row['deferred']) == engine.deferred
    if shader_preview.current(scene, ts):
        break
    assert ticks < 40, (order, engine.deferred)
changed = {'BASE_COLOR', 'ROUGHNESS', 'METALLIC'}
assert changed <= set(order) and len(order) == len(set(order)), order
assert order == sorted(order, key=CHANNELS.index), ('held channels publish in waiting order', order)
held_row = next(row for row in session()['history'] if row['deferred'])
text = stages.report(scene, ts)
assert 'held for the next tick' in text or held_row['deferred'], text
compare('after a budgeted edit')
print('PIXEL_PUBLISH budget: PASS', order, flush=True)

# ---- 3. a bake publishes the held channels first ----------------------------------------------------------------------
stack.channel_for(next(layer for layer in ts.layers if layer.id == coat_id), 'METALLIC').value = .2
stack.channel_for(next(layer for layer in ts.layers if layer.id == coat_id), 'ROUGHNESS').value = .9
next(layer for layer in ts.layers if layer.id == coat_id).visible = True
shader_preview.tick(scene, ts)
assert engine.deferred, 'the zero budget holds channels back'
shader_preview.sync_pixels()   # what a bake runs before Cycles samples the images
assert not engine.deferred and not engine.dirty_cpu, (engine.deferred, engine.dirty_cpu)
compare('a bake right after an edit')
ready()
print('PIXEL_PUBLISH bake: PASS', flush=True)
next(layer for layer in ts.layers if layer.id == coat_id).visible = False
ready()

# ---- 4. a flush publishes every channel at once -----------------------------------------------------------------
next(layer for layer in ts.layers if layer.id == coat_id).visible = True
before = len(session()['history'])
shader_preview.flush(scene, ts)
assert not engine.deferred and shader_preview.current(scene, ts)
flushed = {channel for row in list(session()['history'])[before:] for channel in row['published']}
assert changed <= flushed, flushed   # within the flush, whatever its budgeted ticks published first
compare('after a flush')
print('PIXEL_PUBLISH flush: PASS', flush=True)

# ---- 5. the report names the path ----------------------------------------------------------------------------------
runtime.PIXEL_BUDGET_MS = 20.0
stack.channel_for(next(layer for layer in ts.layers if layer.id == coat_id), 'ROUGHNESS').value = .55
ready()
text = stages.report(scene, ts)
assert 'pixels' in text and 'goes through the CPU' in text, text
print('PIXEL_PUBLISH report: PASS', flush=True)

# Scalar channels keep RGB equal and alpha opaque. Allow driver-specific half-float
# rounding for preview outputs; Height and precise mode must retain float32 values,
# including details smaller than a half-float step.
from types import SimpleNamespace
from open_painter.evaluation import metrics
from open_painter.evaluation.engine.formats import SCALAR_CHANNELS
n = 8
values = np.linspace(-.1, 1.1, n * n, dtype=np.float32).reshape(n, n)
values[2, 3] = .50003125
source_values = np.empty((n, n, 4), np.float32)
source_values[..., :3] = values[..., None]
source_values[..., 3] = .25
dev = runtime.device()
source = dev.upload(source_values)
target = images.create('Scalar publication regression', n)
isolated = runtime.Engine(None, SimpleNamespace(id='scalar-regression'))
isolated.pad = False
try:
    for precise in (False, True):
        isolated.precise = precise
        for channel in sorted(SCALAR_CHANNELS):
            with metrics.Metrics() as observed, dev.state():
                isolated._publish(target, channel, source, n)
            actual = images.pixels(target)
            full_precision = precise or channel == 'HEIGHT'
            expected = np.repeat(np.clip(values, 0, 1)[..., None], 3, axis=2)
            if full_precision:
                np.testing.assert_array_equal(actual[..., :3], expected)
                assert actual[2, 3, 0] != np.float32(.5), 'sub-half-float detail was lost'
            else:
                np.testing.assert_allclose(actual[..., :3], expected, atol=5e-4, rtol=0,
                                           err_msg=f'{channel}, precise={precise}')
            np.testing.assert_array_equal(actual[..., :3], np.repeat(actual[..., :1], 3, axis=2))
            np.testing.assert_array_equal(actual[..., 3], np.ones((n, n), np.float32))
            assert observed.counts['engine_texture_readback_bytes'] == n * n * 4 * 4
finally:
    dev.discard(source)
    bpy.data.images.remove(target)
print('PIXEL_PUBLISH scalar precision, alpha and transfer size: PASS', flush=True)

runtime.PIXEL_PUBLISH = None
print('PIXEL_PUBLISH_PASS', flush=True)
open_painter.unregister()
