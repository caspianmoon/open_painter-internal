"""Real GPU pixels and state restoration on the backend Blender actually runs."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from open_painter.evaluation.engine import runtime


def state():
    return (gpu.state.viewport_get(), gpu.state.scissor_get(), gpu.state.blend_get(),
            gpu.state.depth_test_get(), gpu.state.depth_mask_get())


def main():
    dev = runtime.Device()
    checks = []
    for name in ('publish', 'publish_pixels', 'mip', 'finish', 'pad', 'downsample', 'copy'):
        dev.fixed(name)
    checks.append('all fixed programs compile')
    # Deliberately unused declarations reproduce OpenGL linker elimination.
    constant = dev._create('void main() { op_out0 = vec4(0.125, 0.25, 0.5, 1.0); }',
                           ['op_unused'], parameter_slots=1, ints=('op_unused_int',))
    ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', 4, [0.0] * 4))
    caller = dev.texture(32)
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(caller,))
    with framebuffer.bind():
        gpu.state.viewport_set(3, 4, 17, 18)
        gpu.state.scissor_set(2, 1, 7, 9)
        gpu.state.scissor_test_set(True)
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('LESS')
        gpu.state.depth_mask_set(True)
        original = state()
        try:
            for n in (1, 8, 17, 32):
                y, x = np.mgrid[:n, :n].astype(np.float32)
                expected = np.stack((x / n, y / n, (x + y) / (2 * n), np.ones_like(x)), axis=-1)
                source, target = dev.upload(expected), dev.texture(n)
                with dev.state():
                    dev.draw(dev.fixed('copy'), [target], n, [('op_source', source)])
                assert state() == original, (n, state(), original)
                actual = np.array(dev.readback(target, n), np.float32).reshape(n, n, 4)
                np.testing.assert_array_equal(actual, expected)
                assert state() == original
                with dev.state():
                    dev.draw(constant, [target], n, [('op_unused', source)], ubo,
                             ints={'op_unused_int': 123})
                actual = np.array(dev.readback(target, n), np.float32).reshape(n, n, 4)
                np.testing.assert_array_equal(actual, np.broadcast_to((.125, .25, .5, 1.), actual.shape))
                dev.fill(target, n, (.25, .5, .75, 1.))
                actual = np.array(dev.readback(target, n), np.float32).reshape(n, n, 4)
                np.testing.assert_array_equal(actual, np.broadcast_to((.25, .5, .75, 1.), actual.shape))
                assert state() == original
            checks.extend(['full coverage and orientation at 1/8/17/32 pixels',
                           'unused uniforms and block', 'clipping and draw state restored',
                           'unclipped clear', 'readback state restored'])
            # Vulkan framebuffer reads retain the texture's native component count.
            # Distinct pixels catch packed R/RG data mistaken for an RGBA array.
            for fmt, components in (('R16F', 1), ('R32F', 1), ('RG16F', 2), ('RG32F', 2)):
                values = np.arange(8 * 8 * components, dtype=np.float32).reshape(8, 8, components) / 128
                texture = dev.upload(values, fmt)
                try:
                    actual = np.asarray(dev.readback(texture, 8)).reshape(8, 8, 4)
                    expected = np.zeros((8, 8, 4), np.float32)
                    expected[..., 3] = 1
                    expected[..., :components] = values
                    np.testing.assert_array_equal(actual, expected)
                    assert state() == original
                finally:
                    dev.discard(texture)
            checks.append('compact R/RG readback preserves pixel order and missing components')
            with patch.object(dev, '_batch', side_effect=RuntimeError('injected draw failure')):
                try:
                    with dev.state():
                        dev.draw(dev.fixed('copy'), [caller], 32, [('op_source', source)])
                except RuntimeError as error:
                    assert str(error) == 'injected draw failure'
                else:
                    raise AssertionError('failure injection did not execute')
            assert state() == original
            checks.append('state restored after draw failure')
            dev.calibrate(samples=2)
            assert state() == original
            checks.append('profiling calibration state restored')
        finally:
            gpu.state.scissor_test_set(False)
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')
            gpu.state.depth_mask_set(False)
    report = dict(status='PASS', backend=gpu.platform.backend_type_get(),
                  renderer=gpu.platform.renderer_get(), blender=bpy.app.version_string, checks=checks)
    path = Path(os.environ.get('OP_ENGINE_BACKEND_REPORT', ROOT / '.blender-test/engine-backend-contract.json'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    print('ENGINE_BACKEND_CONTRACT_PASS', json.dumps(report), flush=True)


main()
