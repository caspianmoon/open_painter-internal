"""Uniform Vulkan publication updates every mip and preserves save/fallback pixels."""
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import CHANNELS
from open_painter.core import layer_stack as stack, material_manager, texture_set_manager as sets
from open_painter.evaluation import anchors, cache, metrics, shader_preview
from open_painter.evaluation.engine import runtime
runtime.VULKAN_PIXEL_ROUTE = True   # this suite covers the Vulkan pixel route (the fallback to framebuffer publication)
from open_painter.utils.ids import new_id

runtime.ensure_gpu()
open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
ts = sets.create(scene, 'Uniform publication regression', 512)
base = stack.add(ts, 'FILL', 'Base')
coat = stack.add(ts, 'FILL', 'Coat')
for channel, value in (('ROUGHNESS', .3), ('METALLIC', .8)):
    prop = stack.channel_for(coat, channel)
    prop.enabled, prop.value = True, value
mask = coat.masks.add()
mask.id, mask.source, mask.procedural = new_id(), 'PROCEDURAL', 'CELLULAR'
material_manager.repair(ts)
dev = runtime.device()

def ready():
    for _ in range(100):
        shader_preview.tick(scene, ts)
        if shader_preview.current(scene, ts):
            return
    raise AssertionError(shader_preview.diagnostics(scene, ts))

def gpu_pixels(image):
    return np.frombuffer(gpu.texture.from_image(image).read(), np.float32).reshape(512, 512, 4).copy()

runtime.PIXEL_PUBLISH = True
ready()
engine = shader_preview.session_engine(scene, ts)
outputs = {output.channel: output.image for output in ts.outputs}
reference = {channel: gpu_pixels(outputs[channel]) for channel in CHANNELS}
runtime.PIXEL_PUBLISH = None
uniform = {'NORMAL', 'HEIGHT', 'AO', 'OPACITY'} if dev.backend == 'VULKAN' else set()
assert {channel for channel in CHANNELS if engine._uniform_output(ts, channel, outputs[channel]) is not None} == uniform

# Poison all texture levels, so a stale mip cannot accidentally match the reference.
for channel in uniform:
    gpu.texture.from_image(outputs[channel]).clear(format='FLOAT', value=(.25, .25, .25, 1.))
engine.published.clear()
with metrics.Metrics() as observed:
    engine.evaluate(budget=False)
assert observed.counts.get('engine_uniform_publications', 0) == len(uniform)
assert engine.dirty_cpu == (set() if dev.backend == 'VULKAN' else set(CHANNELS))
for channel in CHANNELS:
    np.testing.assert_array_equal(gpu_pixels(outputs[channel]), reference[channel], err_msg=channel)

# Explicit mip fetch avoids the Vulkan framebuffer API's level-zero read behavior.
shader = dev._create('void main(){op_out0=texelFetch(op_source,ivec2(0),op_lod);}',
                     ['op_source'], ints=('op_lod',))
scratch = dev.texture(1, 'RGBA32F')
framebuffer = gpu.types.GPUFrameBuffer(color_slots=(scratch,))
buffer = gpu.types.Buffer('FLOAT', 4)
try:
    for channel in uniform:
        texture = gpu.texture.from_image(outputs[channel])
        for lod in (0, 1, 4, 9):
            dev.draw(shader, [scratch], 1, [('op_source', texture)], ints={'op_lod': lod})
            with framebuffer.bind():
                framebuffer.read_color(0, 0, 1, 1, 4, 0, 'FLOAT', data=buffer)
            np.testing.assert_array_equal(np.frombuffer(buffer, np.float32), reference[channel][0, 0])
finally:
    dev.release(scratch, 1, 'RGBA32F')
print('UNIFORM_PUBLISH base pixels and all mip levels: PASS', flush=True)

# The source alpha changes, but the proved neutral normal does not.
with metrics.Metrics() as observed:
    mask.strength = .37
    ready()
if dev.backend == 'VULKAN':
    assert observed.counts.get('engine_uniform_publications', 0) == 0
    assert observed.counts['engine_channel_publications'] == 3, dict(observed.counts)
assert not np.array_equal(gpu_pixels(outputs['ROUGHNESS']), reference['ROUGHNESS'])
np.testing.assert_array_equal(gpu_pixels(outputs['NORMAL']), reference['NORMAL'])

# An authored normal change immediately leaves the proof and renders its pixels.
stack.channel_for(coat, 'NORMAL').color = (.7, .5, 1., 1.)
ready()
assert engine._uniform_output(ts, 'NORMAL', outputs['NORMAL']) is None
changed = gpu_pixels(outputs['NORMAL'])
assert not np.array_equal(changed, reference['NORMAL'])
with patch.object(runtime, 'PIXEL_PUBLISH', True):
    engine.published.clear()
    engine.evaluate(budget=False)
np.testing.assert_array_equal(gpu_pixels(outputs['NORMAL']), changed)

# Re-enter the proof and evict the texture. Canonical CPU pixels must already be
# current: an ordinary Blender texture reload must not resurrect the old normal.
stack.channel_for(coat, 'NORMAL').color = (.5, .5, 1., 1.)
ready()
if dev.backend == 'VULKAN':
    outputs['NORMAL'].gl_free()
    np.testing.assert_array_equal(gpu_pixels(outputs['NORMAL']), reference['NORMAL'])
shader_preview.sync_pixels()
assert not engine.dirty_cpu and not engine.deferred
for channel in uniform:
    values = np.empty(512 * 512 * 4, np.float32)
    outputs[channel].pixels.foreach_get(values)
    np.testing.assert_array_equal(values.reshape(512, 512, 4), reference[channel])
print('UNIFORM_PUBLISH mask edits, proof exit/reentry and save pixels: PASS', flush=True)

# A later failure cannot leave an earlier uniform channel's CPU pixels stale.
if dev.backend == 'VULKAN':
    engine.published.pop('AO', None)
    engine.published.pop('BASE_COLOR', None)
    # Fail the shared readback boundary so this covers fused publication too.
    with patch.object(dev, 'readback', side_effect=RuntimeError('later channel failed')):
        try:
            engine.evaluate(['AO', 'BASE_COLOR'], budget=False)
        except RuntimeError as exc:
            assert str(exc) == 'later channel failed'
        else:
            raise AssertionError('Expected publication failure')
    assert 'AO' not in engine.dirty_cpu
    values = np.empty(512 * 512 * 4, np.float32)
    outputs['AO'].pixels.foreach_get(values)
    np.testing.assert_array_equal(values.reshape(512, 512, 4), reference['AO'])
    engine.sync_pixels()
    assert not engine.dirty_cpu

# A final channel's uniform proof must not prune its unit when another channel
# reads that unit through an OUTPUT anchor. CHANNEL units are not inlined, so
# this verifies an actual cross-channel GPU dependency, not just graph metadata.
anchor_ts = sets.create(scene, 'Uniform output anchor consumer', 512)
producer = stack.add(anchor_ts, 'FILL', 'Neutral normal producer')
producer_id = producer.id
producer.publish_output = True
consumer = stack.add(anchor_ts, 'FILL', 'Base color from normal output')
consumer_id = consumer.id
for prop in consumer.channels:
    prop.enabled = prop.channel == 'BASE_COLOR'
prop = stack.channel_for(consumer, 'BASE_COLOR')
prop.anchor, prop.anchor_channel = anchors.output_id(producer_id), 'NORMAL'
assert anchors.resolve(anchor_ts, prop.anchor).kind == 'OUTPUT'
material_manager.repair(anchor_ts)

def anchor_ready():
    for _ in range(100):
        shader_preview.tick(scene, anchor_ts)
        if shader_preview.current(scene, anchor_ts):
            return
    raise AssertionError(shader_preview.diagnostics(scene, anchor_ts))

anchor_ready()
anchor_engine = shader_preview.session_engine(scene, anchor_ts)
anchor_outputs = {output.channel: output.image for output in anchor_ts.outputs}
normal_stamp = anchor_engine.published['NORMAL']
normal_before = gpu_pixels(anchor_outputs['NORMAL'])
color_before = gpu_pixels(anchor_outputs['BASE_COLOR'])
assert np.all(color_before[..., 2] > color_before[..., 0]), 'Consumer did not sample the normal anchor'
producer_key = ('CHANNEL', producer_id, 'NORMAL')
consumer_key = ('CHANNEL', consumer_id, 'BASE_COLOR')
rendered = set()
evaluate = anchor_engine.evaluate

def record_evaluation(*args, **kwargs):
    result = evaluate(*args, **kwargs)
    rendered.update(anchor_engine.rendered)
    return result

# Refresh RNA references after the collection addition. Producer opacity changes
# its dependency stamp even though its opaque neutral output is unchanged.
producer = next(layer for layer in anchor_ts.layers if layer.id == producer_id)
consumer = next(layer for layer in anchor_ts.layers if layer.id == consumer_id)
with patch.object(anchor_engine, 'evaluate', side_effect=record_evaluation), metrics.Metrics() as observed:
    stack.channel_for(producer, 'NORMAL').opacity = .375
    consumer.opacity = .25
    anchor_ready()
assert {producer_key, consumer_key} <= rendered, rendered
if dev.backend == 'VULKAN':
    assert anchor_engine._uniform_output(anchor_ts, 'NORMAL', anchor_outputs['NORMAL']) is not None
    assert anchor_engine.published['NORMAL'] == normal_stamp
    assert observed.counts.get('engine_uniform_publications', 0) == 0
    assert observed.counts.get('engine_channel_publications', 0) == 1, dict(observed.counts)
np.testing.assert_array_equal(gpu_pixels(anchor_outputs['NORMAL']), normal_before)
anchored = gpu_pixels(anchor_outputs['BASE_COLOR'])
assert not np.array_equal(anchored, color_before), 'Consumer opacity edit did not change its output'
assert np.all(anchored[..., 2] > anchored[..., 0]), 'The anchored contribution was lost'
with patch.object(runtime, 'PIXEL_PUBLISH', True):
    anchor_engine.published.clear()
    anchor_engine.evaluate(budget=False)
np.testing.assert_array_equal(gpu_pixels(anchor_outputs['BASE_COLOR']), anchored)
print('UNIFORM_PUBLISH neutral NORMAL OUTPUT anchor consumer: PASS', flush=True)
print('UNIFORM_PUBLISH_PASS', dev.backend, flush=True)
open_painter.unregister()
