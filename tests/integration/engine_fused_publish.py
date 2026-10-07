"""Same-driver pixel equality and pass removal for fused Vulkan publication."""
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
from open_painter.evaluation.engine import fusion, runtime
runtime.VULKAN_PIXEL_ROUTE = True   # this suite covers the Vulkan pixel route (the fallback to framebuffer publication)
from open_painter.utils.ids import new_id

runtime.ensure_gpu()
open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.mesh.primitive_plane_add(size=2)
obj = bpy.context.object
for loop in obj.data.uv_layers.active.data:
    u, v = loop.uv
    loop.uv = (u * .5 + .25, v * .5 + .25)
ts = sets.create(scene, 'Fused publication regression', 512)
sets.assign(scene, ts, obj)
base = stack.add(ts, 'FILL', 'Base')
stack.channel_for(base, 'BASE_COLOR').color = (.31, .42, .23, 1.)
coat = stack.add(ts, 'FILL', 'Coat')
coat_id = coat.id
for kind, value in (('BASE_COLOR', (.73, .27, .12, .83)), ('NORMAL', (.7, .5, 1., 1.)),
                    ('ROUGHNESS', .30003), ('METALLIC', .80007), ('HEIGHT', .50003125)):
    prop = stack.channel_for(coat, kind)
    prop.enabled = True
    if kind in {'BASE_COLOR', 'NORMAL'}:
        prop.color = value
    else:
        prop.value = value
mask = coat.masks.add()
mask.id, mask.source, mask.procedural = new_id(), 'PROCEDURAL', 'CELLULAR'
mask.scale, mask.offset, mask.rotation = (1.7, .73), (.13, -.07), .29
material_manager.repair(ts)

def ready():
    for _ in range(100):
        shader_preview.tick(scene, ts)
        if shader_preview.current(scene, ts):
            return
    raise AssertionError(shader_preview.diagnostics(scene, ts))

def pixels(image):
    return np.frombuffer(gpu.texture.from_image(image).read(), np.float32).reshape(512, 512, 4).copy()

with patch.object(runtime, 'FUSE_PIXEL_PUBLISH', False):
    ready()
engine = shader_preview.session_engine(scene, ts)
outputs = {output.channel: output.image for output in ts.outputs}
dev = runtime.device()
assert engine._donor(512) is not None, 'Fixture must exercise real padding donors'

def publish(fused, selected=None, chains=False):
    engine.published.clear()
    for state in engine.states.values():
        state.stamp = None
    with (patch.object(runtime, 'FUSE_PIXEL_PUBLISH', fused), patch.object(fusion, 'ENABLED', chains),
          metrics.Metrics() as observed):
        engine.evaluate(selected, budget=False)
    return {kind: pixels(outputs[kind]) for kind in selected or CHANNELS}, dev.passes, dict(observed.counts)

# Pass removal is measured with chains rendered unit by unit: chain fusion's one-time compile
# and verification (on the second evaluation of the same edited set) would add passes.
for precise in (False, True):
    engine.precise = precise
    for pad in (False, True):
        engine.pad = pad
        start = dev.passes
        reference, after, _ = publish(False)
        legacy_passes = after - start
        actual, end, counts = publish(True)
        fused_passes = end - after
        for kind in CHANNELS:
            np.testing.assert_array_equal(actual[kind], reference[kind],
                                          err_msg=f'{kind}, precise={precise}, pad={pad}')
        if dev.backend == 'VULKAN':
            assert counts.get('engine_fused_publications', 0) == 4, counts
            assert legacy_passes - fused_passes == 4, (legacy_passes, fused_passes)
        else:
            assert counts.get('engine_fused_publications', 0) == 0, counts
        # HEIGHT must retain the sub-half-float detail in every precision mode.
        assert np.any((actual['HEIGHT'][..., 0] > .5) & (actual['HEIGHT'][..., 0] < .5001))
        print('FUSED_PUBLISH exact pixels/pass counts:', precise, pad, legacy_passes, fused_passes, flush=True)

# Chain fusion composes with the pixel route: a chain that ends at a channel's final renders up
# to the unit below it, and the final still publishes fused with its pixel read.
for attempt in range(3):   # the second evaluation compiles and verifies the chain; the third reuses it
    start = dev.passes
    actual, end, counts = publish(True, chains=True)
    for kind in CHANNELS:
        np.testing.assert_array_equal(actual[kind], reference[kind], err_msg=f'{kind}, chain fusion {attempt}')
    if dev.backend == 'VULKAN':
        assert counts.get('engine_fused_publications', 0) == 4, (attempt, counts)
    assert not counts.get('engine_chain_mismatches'), counts
print('FUSED_PUBLISH with chain fusion:', end - start, 'passes', dict(
    (name, value) for name, value in counts.items() if name.startswith('engine_chain')), flush=True)

# Removing donor remapping would preserve unpadded pixels but fail the padded
# equality above. Verify that this fixture actually distinguishes those paths.
engine.pad = False
unpadded, _, _ = publish(False, ['BASE_COLOR'])
engine.pad = True
padded, _, _ = publish(False, ['BASE_COLOR'])
assert not np.array_equal(unpadded['BASE_COLOR'], padded['BASE_COLOR'])

# Donors held as half floats (sides up to DONOR_HALF_SIDE) are the same texel coordinates: the
# padded images equal those published from full-float donors, bit for bit.
pictures = {}
for side in (0, runtime.DONOR_HALF_SIDE):
    with patch.object(runtime, 'DONOR_HALF_SIDE', side):
        engine.padding = (None, engine.padding[1] if engine.padding else None)   # built again
        pictures[side] = publish(False)[0]
        assert engine._donor(512).format == ('RG16F' if side else 'RG32F'), engine._donor(512).format
for kind in CHANNELS:
    np.testing.assert_array_equal(pictures[runtime.DONOR_HALF_SIDE][kind], pictures[0][kind], err_msg='donor ' + kind)
print('FUSED_PUBLISH half-float donors: PASS', flush=True)

# An OUTPUT anchor must retain the producer's ordinary CHANNEL texture. The
# consumer still qualifies, and must agree exactly with the unfused path.
coat = next(layer for layer in ts.layers if layer.id == coat_id)
coat.publish_output = True
reader = stack.add(ts, 'FILL', 'Output anchor reader')
for prop in reader.channels:
    prop.enabled = prop.channel == 'ROUGHNESS'
prop = stack.channel_for(reader, 'ROUGHNESS')
prop.anchor, prop.anchor_channel = anchors.output_id(coat_id), 'BASE_COLOR'
ready()
engine = shader_preview.session_engine(scene, ts)
assert engine.graph.consumers.get(('CHANNEL', coat_id, 'BASE_COLOR'))
reference, _, _ = publish(False, ['BASE_COLOR', 'ROUGHNESS'])
decisions = {}
fused_final = engine._fused_final

def record_decision(kind, *args):
    result = fused_final(kind, *args)
    decisions[kind] = result is not None
    return result

with patch.object(engine, '_fused_final', side_effect=record_decision):
    actual, _, counts = publish(True, ['BASE_COLOR', 'ROUGHNESS'])
for kind in actual:
    np.testing.assert_array_equal(actual[kind], reference[kind], err_msg='OUTPUT anchor ' + kind)
if dev.backend == 'VULKAN':
    assert decisions == {'BASE_COLOR': False, 'ROUGHNESS': True}, decisions
print('FUSED_PUBLISH OUTPUT-anchor fallback: PASS', flush=True)

# A failed read cannot advance publication or leak its finished scratch texture.
if dev.backend == 'VULKAN':
    engine.published.pop('ROUGHNESS', None)
    with patch.object(dev, 'readback', side_effect=RuntimeError('injected publication failure')) as read:
        try:
            engine.evaluate(['ROUGHNESS'], budget=False)
        except RuntimeError as error:
            assert str(error) == 'injected publication failure'
        else:
            raise AssertionError('Expected readback failure')
    scratch = read.call_args.args[0]
    assert id(scratch) not in dev._leases
    assert 'ROUGHNESS' not in engine.published
    engine.evaluate(['ROUGHNESS'], budget=False)
    np.testing.assert_array_equal(pixels(outputs['ROUGHNESS']), reference['ROUGHNESS'])
print('FUSED_PUBLISH_PASS', dev.backend, flush=True)
open_painter.unregister()
