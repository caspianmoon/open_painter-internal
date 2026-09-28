"""GPU point sampling preserves the existing bounded brush preview exactly."""
import argparse
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from open_painter.evaluation import metrics
from open_painter.evaluation.engine import runtime
from open_painter.painting.gesture import _shrink

parser = argparse.ArgumentParser()
parser.add_argument('--report', type=Path)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
dev = runtime.Device()
formats = {'R16F': 1, 'R32F': 1, 'RG16F': 2, 'RG32F': 2, 'RGBA16F': 4, 'RGBA32F': 4}


def source(n, fmt):
    components = formats[fmt]
    data = np.arange(n * n * components, dtype=np.float32).reshape(n, n, components)
    np.remainder(data, 65521, out=data)
    data /= 32761.0
    data -= .25
    return dev.upload(data, fmt)


def original(texture, n, size, packed):
    values = np.array(dev.readback(texture, n)).reshape(n, n, 4)
    if packed:
        values = values[..., [0, 0, 0, 1]]
    return _shrink(values, n // size)


def sampled(texture, n, size, packed):
    values = np.array(dev.readback_sampled(texture, size)).reshape(size, size, 4)
    if packed:
        values = values[..., [0, 0, 0, 1]]
    return values


def state():
    return (gpu.state.viewport_get(), gpu.state.scissor_get(), gpu.state.blend_get(),
            gpu.state.depth_test_get(), gpu.state.depth_mask_get())


rows = []
for fmt in formats:
    for n, size in ((17, 17), (51, 17), (512, 128)):
        texture = source(n, fmt)
        try:
            for packed in ((False, True) if formats[fmt] == 2 else (False,)):
                expected = original(texture, n, size, packed)
                before = state()
                with metrics.Metrics() as observed:
                    actual = sampled(texture, n, size, packed)
                np.testing.assert_array_equal(actual.view(np.uint32), expected.view(np.uint32))
                assert state() == before
                assert observed.counts['engine_texture_readback_bytes'] == size * size * formats[fmt] * 4
        finally:
            dev.discard(texture)

texture = source(64, 'RG32F')
try:
    leases = dict(dev._leases)
    before = state()
    for broken in ('draw', 'readback'):
        with patch.object(dev, broken, side_effect=RuntimeError('injected preview failure')):
            try:
                dev.readback_sampled(texture, 16)
            except RuntimeError as error:
                assert 'injected preview failure' in str(error)
            else:
                raise AssertionError('Expected injected failure')
        assert dev._leases == leases and state() == before
    for invalid in (0, 15, 128):
        try:
            dev.readback_sampled(texture, invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(invalid)
    np.testing.assert_array_equal(sampled(texture, 64, 16, True), original(texture, 64, 16, True))
finally:
    dev.discard(texture)

# Exercise the actual Preview fallback with mixed GPU-resource and CPU-image
# inputs. This catches accidentally applying the preview stride twice, and
# verifies restore returns each node to its original image/resource binding.
import bpy
from open_painter.evaluation import shader_preview
from open_painter.evaluation.engine import headless
from open_painter.evaluation.engine.resources import TextureResource
from open_painter.evaluation.shader_resources import RESOURCE
from open_painter.painting import gesture

texture = source(512, 'RG32F')
resource = TextureResource('preview-test', texture, 'RG32F', 'rg')
tree = headless.Tree('Preview inputs')
gpu_node, cpu_node = (tree.nodes.new('ShaderNodeTexImage') for _ in range(2))
for node, channel in ((gpu_node, 'ROUGHNESS'), (cpu_node, 'BASE_COLOR')):
    node['op_stroke_layer'], node['op_stroke_channel'] = 'layer', channel
gpu_node.gpu_resource = resource
cpu_image = bpy.data.images.new('CPU preview input', width=512, height=512, float_buffer=True)
cpu_image.colorspace_settings.name = 'Non-Color'
cpu_image[RESOURCE] = True
cpu_values = np.arange(512 * 512 * 4, dtype=np.float32).reshape(512, 512, 4) % 257 / 256
cpu_image.pixels.foreach_set(cpu_values.ravel())
cpu_node.image = cpu_image
ts = SimpleNamespace(id='preview', resolution='512')
scene = SimpleNamespace(open_painter=SimpleNamespace(texture_sets=[ts]))
layer = SimpleNamespace(id='layer', channels=())
graph = SimpleNamespace(nodes=lambda: iter((gpu_node, cpu_node)))
preview = None
created = []
try:
    expected_gpu = original(texture, 512, 128, True)
    with patch.object(runtime, 'device', return_value=dev), \
            patch.object(shader_preview, 'requested', return_value=True), \
            patch.object(shader_preview, 'session_graph', return_value=graph), \
            patch.object(shader_preview, 'inputs_changed'), \
            patch.object(gesture, 'PREVIEW_LIMIT', 128), patch.object(gesture, '_watch'):
        preview = gesture.Preview(scene, ts, layer, ('ROUGHNESS', 'BASE_COLOR'), [], gpu=False)
        created = list(preview.images.values())
        assert preview.active and preview.size == 128 and preview.factor == 4
        np.testing.assert_array_equal(preview.bases['ROUGHNESS'], expected_gpu)
        np.testing.assert_array_equal(preview.bases['BASE_COLOR'], _shrink(cpu_values, 4))
        for channel in ('ROUGHNESS', 'BASE_COLOR'):
            pixels = np.empty(128 * 128 * 4, np.float32)
            preview.images[channel].pixels.foreach_get(pixels)
            np.testing.assert_array_equal(pixels.reshape(128, 128, 4), preview.bases[channel])
        assert gpu_node.gpu_resource is None and gpu_node.image == preview.images['ROUGHNESS']
        preview.restore()
        assert gpu_node.gpu_resource is resource and gpu_node.image is None
        assert cpu_node.image == cpu_image and not preview.active
finally:
    if preview is not None and preview.active:
        with patch.object(gesture, '_watch'), patch.object(shader_preview, 'inputs_changed'):
            preview.restore()
    cpu_node.release_image()
    gesture._orphans[:] = [(image, when) for image, when in gesture._orphans if image not in created]
    for image in [*created, cpu_image]:
        bpy.data.images.remove(image)
    dev.discard(texture)
print('PREVIEW_READBACK mixed-input fallback and restoration: PASS', flush=True)

if args.report:
    for n in (2048, 4096):
        for fmt in ('RG32F', 'RGBA16F'):
            texture = source(n, fmt)
            size, packed = 1024, fmt == 'RG32F'
            samples = {'original': [], 'sampled': []}
            counters = {}
            try:
                expected = original(texture, n, size, packed)
                np.testing.assert_array_equal(sampled(texture, n, size, packed), expected)
                for index in range(10):
                    order = ('original', 'sampled') if index % 2 == 0 else ('sampled', 'original')
                    for mode in order:
                        with metrics.Metrics() as observed:
                            started = perf_counter()
                            value = globals()[mode](texture, n, size, packed)
                            elapsed = (perf_counter() - started) * 1000
                        if index >= 2:
                            samples[mode].append(elapsed)
                            counters[mode] = dict(observed.counts)
                        del value
                np.testing.assert_array_equal(sampled(texture, n, size, packed), expected)
                row = dict(size=n, preview_size=size, format=fmt, samples=samples, counters=counters,
                           median_ms={mode: statistics.median(values) for mode, values in samples.items()})
                rows.append(row)
                print('PREVIEW_READBACK_TIMING', n, fmt, row['median_ms'], flush=True)
            finally:
                dev.discard(texture)
                dev.trim(0)
    args.report.write_text(json.dumps(dict(status='PASS', backend=dev.backend, results=rows,
        scope='GPU source acquisition for bounded fallback previews; includes readback, CPU copy and channel remapping, not full brush latency'), indent=2), encoding='utf-8')

assert not dev._leases, dev._leases
dev.trim(0)
print('PREVIEW_READBACK_PASS: exact strided pixels, all six formats, dimensions, state, leases and failure retry', flush=True)
