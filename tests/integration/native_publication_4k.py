"""Eight-channel 4K publication fits the device cap and preserves repeat edits."""
from pathlib import Path
import sys

import bpy
import gpu
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_preview import fixture, open_painter, shader_preview
from open_painter.constants import CHANNELS
from open_painter.core import layer_stack
from open_painter.evaluation.engine import runtime

gpu.init()
open_painter.register()
try:
    ts, _, ids, _ = fixture(bpy.context.scene, resolution=4096)
    control = next(layer for layer in ts.layers if layer.id == ids['coat'])
    for layer in ts.layers:
        for channel in CHANNELS:
            layer_stack.channel_for(layer, channel).enabled = True

    def ready(value):
        control.opacity = value
        for _ in range(300):
            shader_preview.tick(bpy.context.scene, ts)
            info = shader_preview.diagnostics(bpy.context.scene, ts)
            assert not info['error'], info
            if shader_preview.current(bpy.context.scene, ts):
                break
        else:
            raise AssertionError('4K publication never became current')
        engine = shader_preview.session_engine(bpy.context.scene, ts)
        assert engine.report['execution_backend'] == 'c17-host-' + gpu.platform.backend_type_get().lower(), engine.report
        memory = runtime.device().memory()
        assert memory['leased_bytes'] + memory['pooled_bytes'] <= runtime.DEVICE_BYTES, memory
        # Two immutable 4K revisions per channel do not fit the retained budget on any machine:
        # each channel publishes in place, with eviction recovery (engine_eviction.py).
        assert not runtime.revisions_fit(4096) and engine._native_revisions is None
        assert runtime.device().pixel_publish or engine._presentation.targets
        return engine, memory

    def samples():
        result = {}
        with runtime.device().state():
            for output in ts.outputs:
                target = gpu.texture.from_image(output.image)
                fb = gpu.types.GPUFrameBuffer(color_slots=[target])
                with fb.bind():
                    for x, y in ((100, 100), (2048, 2048), (3990, 3990)):
                        result[output.channel, x, y] = np.array(fb.read_color(x, y, 4, 4, 4, 0, 'FLOAT')).copy()
        return result

    ready(.37)
    expected = samples()
    for value in (.24, .62, .43, .81, .37):
        engine, memory = ready(value)
    for key, values in samples().items():
        np.testing.assert_array_equal(values, expected[key], err_msg=str(key))
    print('NATIVE_PUBLICATION_4K_PASS', engine.report['passes'], memory, flush=True)
finally:
    open_painter.unregister()
