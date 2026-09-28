"""Native R/RG readbacks preserve pixels, borrowed storage and caller GPU state."""
from pathlib import Path
import sys
from unittest.mock import patch

import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from open_painter.evaluation.engine import runtime

runtime.ensure_gpu()
dev = runtime.device()


def state():
    return (gpu.state.viewport_get(), gpu.state.scissor_get(), gpu.state.blend_get(),
            gpu.state.depth_test_get(), gpu.state.depth_mask_get())


def values_for(n, components):
    # Exact half-float values distinguish adjacent pixels and the two components.
    values = np.arange(n * n * components, dtype=np.float32).reshape(n, n, components)
    return ((values * 37) % 263 - 31) / 128


def assert_pixels(buffer, values):
    n, _, components = values.shape
    expected = np.zeros((n, n, 4), np.float32)
    expected[..., :components] = values
    expected[..., 3] = 1.
    actual = np.frombuffer(buffer, np.float32).reshape(n, n, 4)
    np.testing.assert_array_equal(actual.view(np.uint32), expected.view(np.uint32))


def verify_reads():
    sentinel = dev.texture(19, 'RGBA32F')
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(sentinel,))
    try:
        with dev.state(), framebuffer.bind():
            gpu.state.viewport_set(2, 3, 11, 13)
            gpu.state.scissor_set(1, 2, 7, 9)
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('LESS_EQUAL')
            gpu.state.depth_mask_set(True)
            before = state()
            for n in (17, 129, 513, 17):
                rgba = None
                previous_transfer = None
                for fmt, components in (('R16F', 1), ('R32F', 1), ('RG16F', 2), ('RG32F', 2), ('R32F', 1)):
                    values = values_for(n, components)
                    texture = dev.upload(values, fmt)
                    try:
                        leases = set(dev._leases)
                        buffer = dev.readback(texture, n)
                        assert_pixels(buffer, values)
                        assert state() == before
                        assert set(dev._leases) == leases
                        assert len(dev._readbacks) == 1 and n in dev._readbacks
                        if rgba is not None:
                            assert buffer is rgba, 'Component changes must reuse the RGBA buffer'
                        rgba = buffer
                        transfer = dev._compact_readback
                        assert transfer[:2] == (n, components)
                        if previous_transfer is not None:
                            if previous_transfer[1] == components:
                                assert transfer[2] is previous_transfer[2]
                            else:
                                assert transfer[2] is not previous_transfer[2]
                        previous_transfer = transfer
                        assert dev.readback(texture, n) is buffer
                        assert dev._compact_readback[2] is transfer[2]
                        assert_pixels(buffer, values)
                    finally:
                        dev.discard(texture)
            assert state() == before
    finally:
        dev.release(sentinel, 19, 'RGBA32F')
    print('COMPACT_READBACK R/RG bits, defaults, resolution/component reuse and state: PASS', flush=True)


def verify_read_failure():
    values = values_for(33, 2)
    texture = dev.upload(values, 'RG32F')
    sentinel = dev.texture(19, 'RGBA32F')
    constructor = gpu.types.GPUFrameBuffer

    class BrokenRead:
        def __init__(self, *args, **kwargs):
            self.framebuffer = constructor(*args, **kwargs)

        def bind(self):
            return self.framebuffer.bind()

        def read_color(self, *args, **kwargs):
            raise RuntimeError('injected compact read failure')

    framebuffer = constructor(color_slots=(sentinel,))
    try:
        with dev.state(), framebuffer.bind():
            gpu.state.viewport_set(2, 3, 11, 13)
            gpu.state.scissor_set(1, 2, 7, 9)
            before, leases = state(), set(dev._leases)
            with patch.object(gpu.types, 'GPUFrameBuffer', BrokenRead):
                try:
                    dev.readback(texture, 33)
                except RuntimeError as error:
                    assert str(error) == 'injected compact read failure'
                else:
                    raise AssertionError('Expected read failure')
            assert state() == before and set(dev._leases) == leases
            assert_pixels(dev.readback(texture, 33), values)
            assert state() == before and set(dev._leases) == leases
    finally:
        dev.discard(texture)
        dev.release(sentinel, 19, 'RGBA32F')
    print('COMPACT_READBACK read failure restores state and permits retry: PASS', flush=True)


if __name__ == '__main__':
    verify_reads()
    verify_read_failure()
    print('COMPACT_READBACK_PASS', dev.backend, flush=True)
