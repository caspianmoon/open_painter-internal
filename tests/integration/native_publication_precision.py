"""Explicit full precision keeps full-float mip inputs even for half-float images."""
import os
from pathlib import Path
import sys

import bpy
import gpu
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_preview import fixture, open_painter, shader_preview
from open_painter.constants import CHANNELS
from open_painter.core import layer_stack
from open_painter.native_bridge import lifecycle
from open_painter.native_bridge.presentation import HostGPUProbe

gpu.init()
open_painter.register()
try:
    ts, _, ids, _ = fixture(bpy.context.scene, resolution=512)
    control = next(layer for layer in ts.layers if layer.id == ids['coat'])
    for layer in ts.layers:
        for channel in CHANNELS:
            layer_stack.channel_for(layer, channel).enabled = True
    control.opacity = .3793
    for _ in range(300):
        shader_preview.tick(bpy.context.scene, ts)
        info = shader_preview.diagnostics(bpy.context.scene, ts)
        assert not info['error'], info
        if shader_preview.current(bpy.context.scene, ts):
            break
    else:
        raise AssertionError('Fixture never became current')
    engine = shader_preview.session_engine(bpy.context.scene, ts)
    engine.precise = True
    reader = HostGPUProbe(lifecycle._core, 512)
    captures = {}
    for mode in ('0', '1'):
        os.environ['OP_NATIVE_ENGINE'] = mode
        engine.published.clear()
        engine.evaluate(CHANNELS, budget=False)
        if mode == '1' and engine._native_revisions is not None:
            # Framebuffer publication stages each channel in a revision texture; pixel
            # publication (Vulkan) has none and writes the full-float pixels directly.
            stream = engine._native_revisions
            for _, token, _ in stream.current.values():
                assert stream.resources[token][2] == 'RGBA32F', stream.resources[token][2]
        captures[mode] = {(output.channel, level):
            reader.read_mip(gpu.texture.from_image(output.image), level, 512 >> level)
            for output in ts.outputs for level in range(10)}
    for key, values in captures['0'].items():
        np.testing.assert_array_equal(captures['1'][key], values, err_msg=str(key))
    print('NATIVE_PUBLICATION_PRECISION_PASS all eight channels and every mip', flush=True)
finally:
    os.environ.pop('OP_NATIVE_ENGINE', None)
    open_painter.unregister()
