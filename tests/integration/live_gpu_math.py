"""Window-context float GPU parity against the committed NumPy compositor.

Run with --factory-startup --python; this test intentionally refuses background
mode instead of claiming GPU verification from a CPU fallback.
"""
import json
import os
import sys
import traceback
from pathlib import Path

import bpy
import gpu
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.constants import BLEND_ITEMS, DEFAULTS
from open_painter.evaluation.blend_modes import blend
from open_painter.evaluation.live_gpu import Device, MODES
from open_painter.evaluation.metrics import Metrics

REPORT = Path(os.environ.get('OP_LIVE_MATH_REPORT', '/private/tmp/op-live-gpu-math.json'))


def reference(layers, channel, n):
    """CPU evaluator's documented premultiplied folder contract."""
    def stack(records, rgb, coverage):
        for record in records:
            mode = record.get('mode', 'NORMAL')
            amount = record.get('opacity', 1.)
            if mode == 'DISABLE' or amount == 0:
                continue
            if record.get('_mask') is not None:
                amount = amount * record['_mask'][..., None]
            children = record.get('children')
            if children is not None and record.get('passthrough'):
                grouped, alpha = stack(children, rgb.copy(), np.array(coverage))
                rgb, coverage = rgb + amount * (grouped-rgb), coverage + amount*(alpha-coverage)
                continue
            if children is not None:
                src, alpha = stack(children, np.zeros((n, n, 3), np.float32), np.float32(0))
                src = np.divide(src, alpha, out=np.zeros_like(src), where=alpha > 1e-8)
            else:
                source = record['source']
                rgba = np.broadcast_to(source.get('_texture', source.get('color', (0, 0, 0, 1))), (n, n, 4)).copy().astype(np.float32)
                if source.get('premultiplied'):
                    rgba[..., :3] = np.divide(rgba[..., :3], rgba[..., 3:4], out=np.zeros_like(rgba[..., :3]), where=rgba[..., 3:4] > 1e-8)
                field = source.get('_field')
                if field is not None:
                    kind = source.get('procedural', 0)
                    if kind == 1:
                        rgba[..., :3] *= (1+(field-.5)*source['detail'])[..., None]
                    elif kind == 2:
                        rgba[..., :3] += ((field-.5)*source['detail']*.35)[..., None]
                    elif kind == 3:
                        rgba[..., :3] = (.5+(field-.5)*source['height'])[..., None]
                    rgba[..., :3] = np.clip(rgba[..., :3], 0, 1)
                src, alpha = rgba[..., :3], rgba[..., 3:4]
            alpha = np.clip(alpha*amount, 0, 1)
            if np.ndim(coverage) == 0 and coverage == 1:
                rgb = blend(rgb, src, alpha, mode)
            else:
                backdrop = np.divide(rgb, coverage, out=np.zeros_like(rgb), where=coverage > 1e-8)
                mixed = blend(backdrop, src, 1, mode)
                rgb = (1-alpha)*rgb+alpha*((1-coverage)*src+coverage*mixed)
                coverage = alpha+coverage*(1-alpha)
        return rgb, coverage
    rgb, _ = stack(layers, np.array(DEFAULTS[channel][:3], np.float32), np.float32(1))
    if channel == 'NORMAL':
        normal = rgb*2-1
        lengths = np.linalg.norm(normal, axis=-1, keepdims=True)
        rgb = np.divide(normal, lengths, out=np.zeros_like(normal), where=lengths > 1e-8)*.5+.5
    out = np.ones((n, n, 4), np.float32)
    out[..., :3] = np.clip(rgb, 0, 1)
    return out


def run():
    assert not bpy.app.background, 'A real Blender GPU window is required'
    assert set(MODES) == {item[0] for item in BLEND_ITEMS}
    rng = np.random.default_rng(42819)
    n = 32
    device = Device()
    target = gpu.types.GPUTexture((n, n), format='RGBA32F')
    checks, errors = [], []
    image = None
    try:
        def leaf(mode='NORMAL', opacity=.71, *, constant=False, premultiplied=False, procedural=0):
            rgba = rng.random((n, n, 4), dtype=np.float32)
            rgba.reshape(-1, 4)[:8] = np.array([
                (0, 0, 0, 0), (1, 1, 1, 1), (.5, .5, .5, 1),
                (.25, 0, 1, .4), (1, 0, .5, .9), (0, 1, .25, 1e-9),
                (.5, .5, 0, .9), (.5, .5, 1, .7)], np.float32)
            mask = rng.random((n, n), dtype=np.float32)
            mask[:2] = 0
            mask[2:4] = 1
            source = dict(premultiplied=premultiplied)
            if constant:
                source['color'] = (.23, .62, .81, .67)
            else:
                if premultiplied:
                    rgba[..., :3] *= rgba[..., 3:4]
                source.update(texture=device.upload(rgba), _texture=rgba)
            if procedural:
                field = rng.random((n, n), dtype=np.float32)
                source.update(field=device.upload(field), _field=field,
                              procedural=procedural, detail=2.7, height=3.2)
            return dict(mode=mode, opacity=opacity, mask=device.upload(mask), _mask=mask, source=source)

        def verify(name, layers, channel='BASE_COLOR'):
            expected = reference(layers, channel, n)
            device.compose(layers, channel, n, target)
            result = np.array(target.read(), dtype=np.float32).reshape(n, n, 4)
            error = float(np.max(np.abs(result-expected)))
            np.testing.assert_allclose(result, expected, atol=8e-6, rtol=8e-6, err_msg=name)
            errors.append(error)
            checks.append(name)

        for mode in MODES:
            records = [leaf(opacity=1), leaf(mode)]
            verify(f'{mode}:opaque', records)
            verify(f'{mode}:normal-output', records, 'NORMAL')
            group = dict(mode=mode, opacity=.61, children=[leaf('MULTIPLY', .42), leaf(mode, .84)])
            verify(f'{mode}:isolated', [leaf(), group])
            passthrough = dict(opacity=.36, children=[leaf(mode), group], passthrough=True)
            verify(f'{mode}:passthrough-nested', [leaf(), passthrough])
        for kind in (1, 2, 3):
            for constant in (False, True):
                verify(f'procedural-{kind}-constant-{constant}', [leaf(constant=constant, procedural=kind)])
        verify('native-float-premultiplied', [leaf(premultiplied=True)])
        verify('empty-isolated-folders', [dict(children=[], opacity=.5), dict(children=[dict(children=[])])])
        verify('normal-zero-vector-fallback', [dict(source=dict(color=(.5, .5, .5, 1)))], 'NORMAL')

        # Unmasked passthrough folders at full opacity are an organizational
        # wrapper. Nested wrappers must not add full-size copies/composites.
        for mode in MODES:
            records = [leaf(mode), leaf('MULTIPLY')]
            wrapped = [dict(children=[dict(children=records, passthrough=True)], passthrough=True)]
            with Metrics() as flattened:
                verify(f'{mode}:unit-passthrough-elision', wrapped)
            with Metrics() as direct:
                verify(f'{mode}:direct-passthrough-children', records)
            assert flattened.counts['live_gpu_passes'] == direct.counts['live_gpu_passes']
        # Fractional and masked groups still require the backdrop interpolation.
        for wrapper in (dict(opacity=.999), dict(mask=device.upload(np.full((n,n), .6, np.float32)),
                                                _mask=np.full((n,n), .6, np.float32))):
            verify('retained-passthrough-interpolation', [dict(children=[leaf('OVERLAY')],
                                                               passthrough=True, **wrapper)])

        # Invalid modes fail while output still contains the last complete edit.
        before = np.array(target.read(), dtype=np.float32)
        try:
            device.compose([leaf(), leaf('NOT_A_BLEND')], 'BASE_COLOR', n, target)
        except ValueError:
            pass
        else:
            raise AssertionError('Unknown mode was not rejected')
        np.testing.assert_array_equal(np.array(target.read()), before)
        checks.append('failed-compose-retains-output')

        # The compositor must not alter surrounding draw state or leak leases.
        framebuffer = gpu.types.GPUFrameBuffer(color_slots=target)
        with framebuffer.bind():
            original = (gpu.state.viewport_get(), gpu.state.scissor_get(), gpu.state.blend_get(),
                        gpu.state.depth_test_get(), gpu.state.depth_mask_get())
            try:
                gpu.state.viewport_set(3, 4, 17, 18)
                gpu.state.scissor_set(2, 1, 7, 9)
                gpu.state.blend_set('ALPHA')
                gpu.state.depth_test_set('LESS')
                gpu.state.depth_mask_set(True)
                state = (gpu.state.viewport_get(), gpu.state.scissor_get(), gpu.state.blend_get(),
                         gpu.state.depth_test_get(), gpu.state.depth_mask_get())
                verify('gpu-state-restoration', [leaf()])
                assert state == (gpu.state.viewport_get(), gpu.state.scissor_get(), gpu.state.blend_get(),
                                 gpu.state.depth_test_get(), gpu.state.depth_mask_get())
            finally:
                gpu.state.viewport_set(*original[0]); gpu.state.scissor_set(*original[1])
                gpu.state.blend_set(original[2]); gpu.state.depth_test_set(original[3])
                gpu.state.depth_mask_set(original[4])
        assert not device._leased

        # Flattened stack length must not allocate a texture per compositor node.
        records = [leaf() for _ in range(80)]
        device.compose(records, 'BASE_COLOR', n, target)
        with Metrics() as warm:
            device.compose(records, 'BASE_COLOR', n, target)
        assert warm.counts['live_gpu_allocations'] == 0
        assert warm.counts['live_gpu_uploads'] == 0
        assert len(device._pool) < 12, len(device._pool)
        checks.append('warm-composition-reuses-scratch-without-uploads')

        # Readback here is test-only. Production mip generation never reads CPU
        # pixels and only changes shared GPU storage of this test Image.
        image = bpy.data.images.new('Live GPU mip test', width=n, height=n, alpha=True, float_buffer=True)
        image.use_half_precision = False
        image.colorspace_settings.name = 'Non-Color'
        shared = gpu.texture.from_image(image)
        cpu_before = np.array(image.pixels[:], np.float32)
        records = [leaf()]
        device.compose(records, 'BASE_COLOR', n, shared)
        device.generate_mips(shared, n)
        expected = np.array(shared.read(), np.float32).reshape(n, n, 4)
        for level in range(1, 6):
            size = n >> level
            expected = expected.reshape(size, 2, size, 2, 4).mean(axis=(1, 3))
            read_mip = gpu.types.GPUTexture((size, size), format='RGBA32F')
            with device._state():
                device._pass(read_mip, size, opcode=4, current=shared, procedural=level)
            actual = np.array(read_mip.read(), np.float32).reshape(size, size, 4)
            np.testing.assert_allclose(actual, expected, atol=2e-6, err_msg=f'mip {level}')
        np.testing.assert_array_equal(np.array(image.pixels[:], np.float32), cpu_before)
        with Metrics() as mips:
            device.generate_mips(shared, n)
        assert mips.counts['live_gpu_allocations'] == 0
        checks.append('gpu-only-mips-no-cpu-pixel-change-or-warm-allocation')

        report = dict(passed=True, blender=bpy.app.version_string, build=bpy.app.build_hash.decode(),
                      backend=gpu.platform.backend_type_get(), resolution=n, format='RGBA32F',
                      max_absolute_error=max(errors), checks=checks, warm_counters=dict(warm.counts),
                      warm_mip_counters=dict(mips.counts))
        REPORT.write_text(json.dumps(report, indent=2))
        print('LIVE_GPU_MATH_PASS', json.dumps(report), flush=True)
    finally:
        device.close()
        if image is not None:
            bpy.data.images.remove(image)


if bpy.app.background:
    raise RuntimeError('Live GPU math tests require an actual window GPU context')


def deferred():
    try:
        run()
    except Exception:
        REPORT.write_text(json.dumps(dict(passed=False, traceback=traceback.format_exc()), indent=2))
        traceback.print_exc()
    finally:
        bpy.ops.wm.quit_blender()


bpy.app.timers.register(deferred, first_interval=2)
