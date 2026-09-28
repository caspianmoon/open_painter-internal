"""Simulated driver contracts, not evidence of Vulkan/OpenGL hardware support.

The strict API rejects optimized-out bindings and Vulkan image attachments.
Pixel correctness is covered separately by the real GPU integration tests.
"""
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.evaluation.engine import runtime


class StrictShader:
    def __init__(self, active=(), blocks=(), samplers=None):
        self.active, self.blocks = set(active), set(blocks)
        self.samplers = self.active if samplers is None else set(samplers)
        self.queries = 0
        self.writes = []

    def uniform_from_name(self, name):
        self.queries += 1
        if name not in self.active:
            raise ValueError(f'uniform {name} not found')
        return 0

    def uniform_block_from_name(self, name):
        if name not in self.blocks:
            raise ValueError(f'uniform block {name} not found')
        return 0

    def bind(self):
        pass

    def uniform_float(self, name, value):
        if name not in self.active:
            raise ValueError(f'write to inactive uniform {name}')
        self.writes.append(name)

    uniform_int = uniform_float

    def uniform_sampler(self, name, value):
        if name not in self.samplers:
            raise ValueError(f'write to inactive sampler {name}')
        self.writes.append(name)

    def uniform_block(self, name, value):
        if name not in self.blocks:
            raise ValueError(f'write to inactive block {name}')
        self.writes.append(name)


class StrictGPU:
    def __init__(self, backend, shader=None):
        self.backend = backend
        def create(info):
            if shader is not None:
                return shader
            uniforms = [call.args[1] for call in info.push_constant.call_args_list]
            samplers = [call.args[2] for call in info.sampler.call_args_list]
            blocks = [call.args[2] for call in info.uniform_buf.call_args_list]
            return StrictShader(active=uniforms + (samplers if backend == 'OPENGL' else []),
                                samplers=samplers, blocks=blocks)
        self.shader = SimpleNamespace(create_from_info=create)
        self.platform = SimpleNamespace(backend_type_get=lambda: backend)
        self.capabilities = SimpleNamespace(max_textures_frag_get=lambda: 16)
        self.values = dict(viewport=(3, 4, 17, 18), scissor=(2, 1, 7, 9), blend='ALPHA',
                           depth_test='LESS', depth_mask=True)
        self.state = SimpleNamespace()
        for name in self.values:
            setattr(self.state, name + '_get', lambda name=name: self.values[name])
            setattr(self.state, name + '_set', self.setter(name))
        self.attachments = []
        self.texture = SimpleNamespace(from_image=self.from_image)
        self.types = SimpleNamespace(
            GPUTexture=lambda size, **kwargs: SimpleNamespace(width=size[0], height=size[1], image=False),
            GPUShaderCreateInfo=Mock,
            GPUFrameBuffer=self.framebuffer,
            Buffer=lambda kind, n: np.zeros(n, np.float32))

    def setter(self, name):
        def set_value(*values):
            self.values[name] = values if len(values) > 1 else values[0]
        return set_value

    def from_image(self, image):
        return SimpleNamespace(width=image.size[0], height=image.size[1], image=True)

    def framebuffer(self, *, color_slots):
        for slot in color_slots:
            texture = slot['texture'] if isinstance(slot, dict) else slot
            if self.backend == 'VULKAN' and texture.image:
                raise AssertionError('Vulkan image textures cannot be render attachments')
            self.attachments.append(texture)

        @contextmanager
        def bind():
            try:
                yield
            finally:
                # Framebuffer rebinds do not preserve viewport/scissor rectangles.
                self.values['viewport'] = self.values['scissor'] = (0, 0, 1, 1)

        def read_color(*args, data):
            data[:] = .375

        return SimpleNamespace(bind=bind, read_color=read_color, clear=Mock())


class BackendContract(unittest.TestCase):
    @contextmanager
    def device(self, backend, shader=None):
        gpu = StrictGPU(backend, shader)
        with patch.dict(sys.modules, {'gpu': gpu}), patch.object(runtime, 'ensure_gpu'), \
                patch.object(runtime, 'PIXEL_PUBLISH', None):
            dev = runtime.Device()
            dev.batch = SimpleNamespace(draw=Mock())
            with patch.object(runtime, 'device', return_value=dev):
                yield gpu, dev

    def test_opengl_optimized_uniforms_are_reflected_once(self):
        shader = StrictShader(active={'op_source', 'op_mode'})
        with self.device('OPENGL', shader) as (gpu, dev):
            shader = dev._create('void main() {}', ['op_source', 'op_unused'],
                                 parameter_slots=1, ints=('op_mode', 'op_unused_int'))
            queries = shader.queries
            before = gpu.values.copy()
            for _ in range(3):
                with dev.state():
                    dev.draw(shader, [dev.texture(8)], 8,
                             [('op_source', dev.texture(8)), ('op_unused', dev.texture(8))],
                             ubo=object(), ints={'op_mode': 1, 'op_unused_int': 2})
            self.assertEqual(shader.queries, queries)
            self.assertEqual(shader.writes, ['op_mode', 'op_source'] * 3)
            self.assertEqual(gpu.values, before)

    def test_active_uniforms_and_block_are_bound(self):
        shader = StrictShader(active={'op_inv_size'}, blocks={'op_params'})
        with self.device('OPENGL', shader) as (_, dev):
            dev._create('void main() {}', [], parameter_slots=1)
            with dev.state():
                dev.draw(shader, [dev.texture(8)], 8, ubo=object())
            self.assertEqual(shader.writes, ['op_inv_size', 'op_params'])

    def test_descriptor_samplers_do_not_need_uniform_locations(self):
        for backend in ('METAL', 'VULKAN'):
            shader = StrictShader(samplers={'op_source'})
            with self.subTest(backend=backend), self.device(backend, shader) as (_, dev):
                with dev.state():
                    dev.draw(dev.fixed('copy'), [dev.texture(8)], 8, [('op_source', dev.texture(8))])
                self.assertEqual(shader.writes, ['op_source'])

    def test_state_restored_when_draw_fails(self):
        for backend in ('OPENGL', 'VULKAN'):
            with self.subTest(backend=backend), self.device(backend) as (gpu, dev):
                before = gpu.values.copy()

                def fail(shader):
                    self.assertEqual(gpu.values['scissor'], (0, 0, 8, 8))
                    self.assertEqual(gpu.values['blend'], 'NONE')
                    raise RuntimeError('device draw failed')

                dev.batch.draw.side_effect = fail
                with self.assertRaisesRegex(RuntimeError, 'device draw failed'), dev.state():
                    dev.draw(dev.fixed('copy'), [dev.texture(8)], 8)
                self.assertEqual(gpu.values, before)

    def test_vulkan_publication_avoids_image_attachments_and_reuses_readback(self):
        with self.device('VULKAN') as (gpu, dev):
            image = SimpleNamespace(size=(8, 8), pixels=SimpleNamespace(foreach_set=Mock()))
            engine = runtime.Engine(None, SimpleNamespace(id='test'))
            engine.pad = False
            before = gpu.values.copy()
            with dev.state():
                for _ in range(2):
                    engine._publish(image, 'BASE_COLOR', dev.texture(8), 8)
            self.assertTrue(all(not t.image for t in gpu.attachments))
            calls = image.pixels.foreach_set.call_args_list
            self.assertEqual(len(calls), 2)
            self.assertIs(calls[0].args[0], calls[1].args[0])
            np.testing.assert_array_equal(calls[0].args[0], np.full(8 * 8 * 4, .375))
            self.assertEqual(gpu.values, before)

    def test_scratch_returned_after_failed_publication(self):
        for backend in ('OPENGL', 'VULKAN'):
            with self.subTest(backend=backend), self.device(backend) as (_, dev):
                image = SimpleNamespace(size=(8, 8), pixels=SimpleNamespace(foreach_set=Mock()))
                engine = runtime.Engine(None, SimpleNamespace(id='test'))
                engine.pad = False
                dev.batch.draw.side_effect = RuntimeError('publication failed')
                with self.assertRaisesRegex(RuntimeError, 'publication failed'), dev.state():
                    engine._publish(image, 'BASE_COLOR', dev.texture(8), 8)
                self.assertEqual(len(dev.pool[(8, runtime.HALF)]), 1)

    def test_partial_failure_preserves_save_sync(self):
        for backend in ('OPENGL', 'VULKAN'):
            with self.subTest(backend=backend), self.device(backend):
                channels = ('BASE_COLOR', 'ROUGHNESS')
                outputs = [SimpleNamespace(channel=c, image=SimpleNamespace(as_pointer=lambda: 1))
                           for c in channels]
                ts = SimpleNamespace(id='test', outputs=outputs)
                scene = SimpleNamespace(open_painter=SimpleNamespace(texture_sets=[ts]))
                engine = runtime.Engine(scene, ts)
                engine.graph = SimpleNamespace(n=8)
                engine.finals = {c: ('const', ()) for c in channels}
                engine._ref_stamp = Mock(return_value=1)
                engine._checkpoints = Mock(return_value=[])
                engine._texture_of = Mock()
                engine._publish = Mock(side_effect=[None, RuntimeError('second channel failed')])
                if backend == 'VULKAN':
                    engine.dirty_cpu.update(channels)
                with self.assertRaisesRegex(RuntimeError, 'second channel failed'):
                    engine.evaluate(channels, budget=False)
                self.assertEqual(engine.dirty_cpu, {'BASE_COLOR'} if backend == 'OPENGL' else {'ROUGHNESS'})
                self.assertEqual(engine.published, {'BASE_COLOR': 1})

    def test_readback_restores_state_and_replaces_buffer_on_resize(self):
        with self.device('OPENGL') as (gpu, dev):
            before = gpu.values.copy()
            first = dev.readback(dev.texture(8), 8)
            self.assertIs(first, dev.readback(dev.texture(8), 8))
            self.assertIsNot(first, dev.readback(dev.texture(16), 16))
            self.assertEqual(set(dev._readbacks), {16})
            self.assertEqual(gpu.values, before)


if __name__ == '__main__':
    unittest.main()
