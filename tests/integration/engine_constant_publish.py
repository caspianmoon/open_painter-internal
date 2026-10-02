"""One-texel constant evaluation preserves published pixels and image lifetime."""
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
from open_painter.evaluation.engine.constant_channels import spatially_constant_channel
from open_painter.evaluation.engine.formats import SCALAR_CHANNELS
from open_painter.utils.ids import new_id

runtime.ensure_gpu()
open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
ts = sets.create(scene, 'Constant publication regression', 512)
base_id = stack.add(ts, 'FILL', 'Base').id
coat_id = stack.add(ts, 'FILL', 'Coat').id


def layer(identifier):
    return next(item for item in ts.layers if item.id == identifier)


for identifier, opacity in ((base_id, .73), (coat_id, .27)):
    item = layer(identifier)
    item.opacity = opacity
    for index, prop in enumerate(item.channels):
        prop.enabled, prop.opacity = True, .81 if identifier == base_id else .43
        if prop.channel in SCALAR_CHANNELS:
            prop.value = (.50003125 if prop.channel == 'HEIGHT' else
                          .17 + index * .073 + (.19 if identifier == coat_id else 0.))
        else:
            prop.color = ((.31, .48, .92, .41) if identifier == base_id else (.61, .23, .77, .83))
material_manager.repair(ts)
dev = runtime.device()
constant = set(SCALAR_CHANNELS) | {'NORMAL'}


def ready():
    for _ in range(100):
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def pixels(image):
    n = image.size[0]
    return np.frombuffer(gpu.texture.from_image(image).read(), np.float32).reshape(n, n, 4).copy()


def cpu_pixels(image):
    values = np.empty(len(image.pixels), np.float32)
    image.pixels.foreach_get(values)
    return values.reshape(image.size[1], image.size[0], 4)


def current():
    return shader_preview.session_engine(scene, ts), {out.channel: out.image for out in ts.outputs}


def publish(shortcut, selected=None):
    engine, outputs = current()
    engine.published.clear()
    for state in engine.states.values():
        state.stamp = None
    # Vulkan compares its old pixel route with the shortcut. Other backends
    # keep their own native route on both sides: host upload and framebuffer
    # stores can round differently, and neither optimization applies there.
    with patch.multiple(runtime, CONSTANT_OUTPUTS=shortcut,
                        PIXEL_PUBLISH=None if shortcut or dev.backend != 'VULKAN' else True,
                        FUSE_PIXEL_PUBLISH=False), metrics.Metrics() as observed:
        engine.evaluate(selected, budget=False)
    return ({kind: pixels(outputs[kind]) for kind in selected or CHANNELS},
            dict(observed.counts))


with patch.multiple(runtime, CONSTANT_OUTPUTS=False, PIXEL_PUBLISH=True, FUSE_PIXEL_PUBLISH=False):
    ready()

# Explicit LOD sampling avoids Vulkan framebuffer.read_color's level-zero read.
shader = dev._create('void main(){op_out0=texelFetch(op_source,ivec2(0),op_lod);}',
                     ['op_source'], ints=('op_lod',))
scratch = dev.texture(1, 'RGBA32F')
framebuffer = gpu.types.GPUFrameBuffer(color_slots=(scratch,))
read = gpu.types.Buffer('FLOAT', 4)


def check_mips(image, expected):
    texture = gpu.texture.from_image(image)
    last = int(np.log2(image.size[0]))
    for lod in sorted({0, min(1, last), min(4, last), last}):
        with dev.state():
            dev.draw(shader, [scratch], 1, [('op_source', texture)], ints={'op_lod': lod})
            with framebuffer.bind():
                framebuffer.read_color(0, 0, 1, 1, 4, 0, 'FLOAT', data=read)
        np.testing.assert_array_equal(np.frombuffer(read, np.float32), expected,
                                      err_msg=f'{image.name}: mip {lod}')


def check_clear_rounding():
    """Vulkan transfer-clear must match Image upload while CPU pixels stay full-float.

    The driver can round a float32 clear to float16 differently from Blender's
    host upload. Exercise both ties-to-even directions, the normal/subnormal
    boundary, and nearby float32 values, as well as ordinary arbitrary colors.
    """
    minimum = np.float32(2**-14)
    midpoint_low = np.float32(.5 + 2**-12)
    midpoint_high = np.float32(.5 + 3 * 2**-12)
    values = [0., 1., 2**-25, 2**-24, 2**-14 - 2**-24,
              np.nextafter(minimum, np.float32(0.)), minimum,
              np.nextafter(minimum, np.float32(1.)), .3578,
              midpoint_low, midpoint_high]
    for midpoint in (np.float32(2**-25), midpoint_low, midpoint_high):
        values.extend((np.nextafter(midpoint, np.float32(0.)),
                       np.nextafter(midpoint, np.float32(1.))))
    colors = [(value, value, value, 1.) for value in values]
    rng = np.random.default_rng(19)
    colors.extend(tuple(rng.random(3)) + (1.,) for _ in range(24))
    publisher = runtime.Engine(scene, ts)
    try:
        for half in (True, False):
            image = bpy.data.images.new('Constant clear rounding', width=4, height=4,
                                        alpha=True, float_buffer=True)
            try:
                image.colorspace_settings.name = 'Non-Color'
                image.alpha_mode = 'CHANNEL_PACKED'
                image.use_half_precision = half
                for raw_color in colors:
                    color = tuple(float(value) for value in np.asarray(raw_color, np.float32))
                    expected_cpu = np.empty((4, 4, 4), np.float32)
                    expected_cpu[:] = color
                    image.pixels.foreach_set(expected_cpu.ravel())
                    texture = gpu.texture.from_image(image)
                    assert texture.format == ('RGBA16F' if half else 'RGBA32F'), texture.format
                    expected_gpu = pixels(image)
                    texture.clear(format='FLOAT', value=(.125, .25, .375, 1.))
                    publisher._publish_uniform(image, 'ROUGHNESS', color, 4)
                    np.testing.assert_array_equal(cpu_pixels(image), expected_cpu,
                                                  err_msg=f'CPU precision: half={half}, color={color}')
                    np.testing.assert_array_equal(pixels(image), expected_gpu,
                                                  err_msg=f'GPU rounding: half={half}, color={color}')
                    check_mips(image, expected_gpu[0, 0])
                print('CONSTANT_PUBLISH clear/upload rounding:', half, len(colors), flush=True)
            finally:
                bpy.data.images.remove(image)
    finally:
        publisher.close(4)


try:
    for n in (512, 1024):
        engine, outputs = current()
        previous = engine._constant_renderer
        previous_child = previous.child if previous is not None else None
        previous_n = engine.graph.n
        ts.resolution = str(n)
        ready()
        engine, outputs = current()
        if previous_child is not None and previous_n != n:
            # Resolution adoption must not return one-texel leases to an n×n pool.
            assert previous.child is None and not previous_child.states
        assert engine.graph.n == n
        for precise in (False, True):
            engine.precise = precise
            reference, _ = publish(False)
            cpu_reference = {kind: cpu_pixels(outputs[kind]) for kind in constant}
            full_buffer = dev._readbacks.get(n)
            for kind in constant:
                gpu.texture.from_image(outputs[kind]).clear(format='FLOAT', value=(.125, .25, .375, 1.))
            actual, counts = publish(True)
            for kind in CHANNELS:
                np.testing.assert_array_equal(actual[kind], reference[kind],
                                              err_msg=f'{kind}, n={n}, precise={precise}')
            if dev.backend == 'VULKAN':
                assert counts.get('engine_constant_evaluations', 0) == len(constant), counts
                assert engine._constant_renderer.child.graph.n == 1
                assert dev._readbacks.get(n) is full_buffer, 'Tiny reads evicted the full-size transfer buffer'
                assert not engine.dirty_cpu
                for kind in constant:
                    np.testing.assert_array_equal(cpu_pixels(outputs[kind]), cpu_reference[kind], err_msg=kind)
                    check_mips(outputs[kind], reference[kind][0, 0])
                    outputs[kind].gl_free()
                    np.testing.assert_array_equal(pixels(outputs[kind]), reference[kind], err_msg='reload ' + kind)
                    check_mips(outputs[kind], reference[kind][0, 0])
            else:
                assert counts.get('engine_constant_evaluations', 0) == 0, counts
            print('CONSTANT_PUBLISH exact CPU/GPU pixels and mips:', n, precise, flush=True)

    # Numeric edits retain the proof but must invalidate the result stamp.
    before = engine.published['HEIGHT']
    stack.channel_for(layer(coat_id), 'HEIGHT').value = .81357
    ready()
    changed = pixels(outputs['HEIGHT'])
    assert engine.published['HEIGHT'] != before
    reference, _ = publish(False, ['HEIGHT'])
    np.testing.assert_array_equal(changed, reference['HEIGHT'])
    publish(True)

    # Forced pixel publication is a compatibility opt-out, independent of the flag.
    with patch.object(runtime, 'PIXEL_PUBLISH', True), metrics.Metrics() as observed:
        engine.published.clear()
        engine.evaluate(budget=False)
        for kind in constant:
            assert not engine._constant_output(ts, kind, outputs[kind])
    assert observed.counts.get('engine_constant_evaluations', 0) == 0

    # A mask is a structural exit; removing it creates fresh one-texel state.
    previous = engine._constant_renderer
    previous_child = previous.child if previous is not None else None
    mask = layer(coat_id).masks.add()
    mask.id, mask.source, mask.procedural = new_id(), 'PROCEDURAL', 'CELLULAR'
    ready()
    engine, outputs = current()
    assert not spatially_constant_channel(ts, 'NORMAL')
    assert not engine._constant_output(ts, 'NORMAL', outputs['NORMAL'])
    if previous_child is not None:
        assert previous.child is None and not previous_child.states
    masked = pixels(outputs['NORMAL'])
    reference, _ = publish(False, ['NORMAL'])
    np.testing.assert_array_equal(masked, reference['NORMAL'])
    layer(coat_id).masks.remove(0)
    ts.resolution = '512'
    ready()
    engine, outputs = current()
    assert spatially_constant_channel(ts, 'NORMAL')
    assert engine.graph.n == 512
    print('CONSTANT_PUBLISH edits, fallback and resolution adoption: PASS', flush=True)

    # A full-size cross-channel reader must not inherit the child's one-texel cache.
    layer(coat_id).publish_output = True
    reader = stack.add(ts, 'FILL', 'Normal OUTPUT reader')
    for prop in reader.channels:
        prop.enabled = prop.channel == 'BASE_COLOR'
    prop = stack.channel_for(reader, 'BASE_COLOR')
    prop.anchor, prop.anchor_channel = anchors.output_id(coat_id), 'NORMAL'
    ready()
    engine, outputs = current()
    producer = ('CHANNEL', coat_id, 'NORMAL')
    assert engine.graph.consumers.get(producer)
    reference, _ = publish(False)
    actual, _ = publish(True)
    for kind in CHANNELS:
        np.testing.assert_array_equal(actual[kind], reference[kind], err_msg='anchor ' + kind)
    if dev.backend == 'VULKAN':
        child = engine._constant_renderer.child
        assert child.states[producer] is not engine.states[producer]
        assert engine.states[producer].textures[0].width == 512
        assert child.states[producer].textures[0].width == 1
        renderer = engine._constant_renderer
        textures = [texture for state in child.states.values() for texture in state.textures or ()]
        engine.close()
        assert renderer.child is None and not child.states
        assert all(id(texture) not in dev._leases for texture in textures)
    print('CONSTANT_PUBLISH independent anchor caches and close: PASS', flush=True)
    if dev.backend == 'VULKAN':
        check_clear_rounding()
finally:
    dev.release(scratch, 1, 'RGBA32F')

print('CONSTANT_PUBLISH_PASS', dev.backend, flush=True)
open_painter.unregister()
