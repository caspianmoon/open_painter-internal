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
            GPUTexture=lambda size, format='RGBA8', **kwargs: SimpleNamespace(
                width=size[0], height=size[1], format=format, image=False),
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
            components = {'R16F': 1, 'R32F': 1, 'RG16F': 2, 'RG32F': 2}.get(texture.format, 4)
            if self.backend == 'VULKAN':
                # Vulkan copies native components even when the caller requests RGBA.
                data[:args[2] * args[3] * components] = .375
            else:
                values = data.reshape(-1, args[4])
                values[:] = 0.0
                if args[4] == 4:
                    values[:, 3] = 1.0
                values[:, :components] = .375

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

    def test_compact_readback_preserves_pixels_and_default_components(self):
        for backend in ('OPENGL', 'VULKAN'):
            with self.subTest(backend=backend), self.device(backend) as (gpu, dev):
                before = gpu.values.copy()
                for fmt, expected in (('R32F', (.375, 0, 0, 1)), ('RG16F', (.375, .375, 0, 1))):
                    values = dev.readback(dev.texture(8, fmt), 8)
                    np.testing.assert_array_equal(np.asarray(values).reshape(-1, 4), np.tile(expected, (64, 1)))
                self.assertEqual(gpu.values, before)

    def test_publication_keeps_gpu_rgba_expansion_and_height_precision(self):
        with self.device('VULKAN') as (gpu, dev):
            image = SimpleNamespace(size=(8, 8), pixels=SimpleNamespace(foreach_set=Mock()))
            engine = runtime.Engine(None, SimpleNamespace(id='test'))
            engine.pad = False
            before = gpu.values.copy()
            for channel, precise, fmt in (('ROUGHNESS', False, 'RGBA16F'), ('HEIGHT', False, 'RGBA32F'),
                                          ('METALLIC', True, 'RGBA32F')):
                engine.precise = precise
                with dev.state():
                    engine._publish(image, channel, dev.texture(8), 8)
                self.assertIn((8, fmt), dev.pool)
                values = image.pixels.foreach_set.call_args.args[0]
                np.testing.assert_array_equal(np.asarray(values), np.full(8 * 8 * 4, .375))
            calls = image.pixels.foreach_set.call_args_list
            self.assertTrue(all(call.args[0] is calls[0].args[0] for call in calls))
            self.assertEqual(gpu.values, before)

    def test_retention_counts_real_formats_and_pins_consumers(self):
        with self.device('OPENGL') as (_, dev):
            engine = runtime.Engine(None, SimpleNamespace(id='test'))
            def state(fmt):
                return SimpleNamespace(formats=(fmt,), textures=[dev.texture(8, fmt)])
            first, second, pinned = ('SRC', 'a'), ('SRC', 'b'), ('SRC', 'pinned')
            for key in (first, second, pinned):
                engine.states[key] = state('RGBA16F')
            with patch.object(runtime, 'RETAIN_BYTES', 8 * 8 * 8 * 2):
                engine._retain(first, engine.states[first], 8)
                engine._retain(pinned, engine.states[pinned], 8)
                self.assertEqual(sum(engine.retained.values()), 1024)
                engine._in_use[pinned] += 1
                engine._retain(second, engine.states[second], 8)
                self.assertIsNone(engine.states[first].textures)
                self.assertIsNotNone(engine.states[pinned].textures)
                self.assertEqual(sum(engine.retained.values()), 1024)

    def test_scalar_layout_preserves_height_precision_and_halves_storage(self):
        from open_painter.evaluation.engine.formats import layout, texture_bytes
        self.assertEqual(layout(('CHANNEL', 'x', 'ROUGHNESS'), 'Out'), 'RG16F')
        self.assertEqual(layout(('SRC', 'x', 'HEIGHT', 0), 'Color'), 'RG32F')
        self.assertEqual(layout(('MASK', 'x', 'm'), 'Value'), 'R32F')
        self.assertEqual(texture_bytes(4096, 4096, 'RG16F'), 64 * 1024 * 1024)
        self.assertEqual(texture_bytes(4096, 4096, 'RGBA16F'), 128 * 1024 * 1024)

    def test_pool_moves_bytes_between_leased_and_free(self):
        with self.device('VULKAN') as (_, dev):
            texture = dev.texture(8, 'RG16F')
            self.assertEqual(dev.memory(), {'leased_bytes': 256, 'pooled_bytes': 0})
            dev.release(texture, 8, 'RG16F')
            self.assertEqual(dev.memory(), {'leased_bytes': 0, 'pooled_bytes': 256})
            self.assertIs(dev.texture(8, 'RG16F'), texture)
            dev.release(texture, 8, 'RG16F')
            dev.trim()
            self.assertEqual(dev.memory(), {'leased_bytes': 0, 'pooled_bytes': 0})

    def test_owned_resource_binding_avoids_blender_image_and_tracks_revision(self):
        from open_painter.evaluation.engine.resources import TextureResource
        for backend in ('OPENGL', 'VULKAN'):
            with self.subTest(backend=backend), self.device(backend) as (_, dev):
                texture = dev.texture(8, 'RG32F')
                resource = TextureResource('height', texture, 'RG32F', 'rg')
                sampler = SimpleNamespace(kind='image', node=SimpleNamespace(image=None, gpu_resource=resource))
                engine = runtime.Engine(None, SimpleNamespace(id='test'))
                self.assertIs(engine._bind(sampler, 8, {}, {}, None, {}), texture)
                stamp = resource.stamp
                resource.touch()
                self.assertNotEqual(resource.stamp, stamp)

    def test_pool_budget_preserves_recently_reused_format(self):
        with self.device('VULKAN') as (_, dev), patch.object(runtime, 'POOL_BYTES', 256):
            recent = dev.texture(4, 'RGBA16F')
            old = dev.texture(4, 'RGBA32F')
            dev.release(recent, 4, 'RGBA16F')
            dev.release(old, 4, 'RGBA32F')
            # The immediate cap may evict the first free texture already.
            recent = dev.texture(4, 'RGBA16F')
            dev.release(recent, 4, 'RGBA16F')
            dev.trim(3)
            self.assertEqual(dev.memory()['pooled_bytes'], 128)
            self.assertIs(dev.texture(4, 'RGBA16F'), recent)

    def test_reduced_sampler_limits_reject_pass_and_select_stroke_fallback(self):
        from open_painter.painting.gpu_strokes import Canvas, UnsupportedSurface
        for backend in ('OPENGL', 'VULKAN'):
            with self.subTest(backend=backend), self.device(backend) as (_, dev):
                dev.max_samplers = 4
                with self.assertRaises(UnsupportedSurface):
                    Canvas([], 8, {}, {})
                with self.assertRaisesRegex(ValueError, 'device limit'):
                    dev._create('void main() {}', ['a', 'b', 'c', 'd', 'e'])

    def test_region_draw_restores_state_and_rejects_feedback(self):
        for backend in ('OPENGL', 'VULKAN'):
            with self.subTest(backend=backend), self.device(backend) as (gpu, dev):
                source, target = dev.texture(16), dev.texture(16)
                shader = dev.fixed('copy')
                before = gpu.values.copy()
                def check_region(shader):
                    self.assertEqual(gpu.values['viewport'], (2, 3, 4, 5))
                    self.assertEqual(gpu.values['scissor'], (2, 3, 4, 5))
                dev.batch.draw.side_effect = check_region
                with dev.state():
                    dev.draw(shader, [target], 16, [('op_source', source)], region=(2, 3, 4, 5))
                self.assertEqual(gpu.values, before)
                with self.assertRaisesRegex(ValueError, 'render target'):
                    dev.draw(shader, [source], 16, [('op_source', source)])


if __name__ == '__main__':
    unittest.main()
