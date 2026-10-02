"""Eviction of images published in place: Blender recreates an image's GPU texture from its CPU
pixels (Texture Time Out, ``gl_free``), and those lag behind a framebuffer publication.

Channels published straight into their images (OpenGL's scheduler, and sets whose native
revisions would not fit the retained budget: runtime.revisions_fit) come back exactly, every mip,
from the texture they were published into, without a new evaluation. Native revisions restore
their own channels (native_host_metal.py).
"""
from pathlib import Path
import sys

import bpy
import gpu
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_preview import fixture, open_painter, shader_preview
from open_painter.evaluation import metrics
from open_painter.evaluation.engine import runtime
from open_painter.native_bridge import lifecycle
from open_painter.native_bridge.presentation import HostGPUProbe

N = 512
gpu.init()
open_painter.register()
try:
    # Pixel publication (a background Vulkan session's default) keeps the CPU pixels current, so
    # Blender's own upload restores an evicted image; publish into the images, as in a window.
    runtime.PIXEL_PUBLISH = False
    # Every backend publishes in place when native revisions do not fit (2K on smaller machines).
    runtime.revisions_fit = lambda n: False
    scene = bpy.context.scene
    ts, _, ids, _ = fixture(scene, resolution=N)

    def history():
        return len(shader_preview._sessions[shader_preview.key_for(scene, ts)].get('history', ()))

    def ready():
        """Current, and quiet: a leaf that turns from its placeholder into pixels at the end
        of a tick has the layers sampling it evaluated once more on the next."""
        quiet = 0
        for _ in range(300):
            rows = history() if shader_preview.key_for(scene, ts) in shader_preview._sessions else -1
            shader_preview.tick(scene, ts)
            info = shader_preview.diagnostics(scene, ts)
            assert not info['error'], info
            quiet = quiet + 1 if shader_preview.current(scene, ts) and history() == rows else 0
            if quiet == 3:
                return shader_preview.session_engine(scene, ts)
        raise AssertionError(info)

    reader = HostGPUProbe(lifecycle._core, N)

    def published():
        return {(output.channel, level): reader.read_mip(gpu.texture.from_image(output.image), level, N >> level)
                for output in ts.outputs for level in range(10)}

    engine = ready()
    assert engine._native_revisions is None, 'revisions were forced off'
    in_place = set(engine._presentation.targets) if engine._presentation is not None else set()
    assert in_place, 'no channel was published in place'
    before = published()
    # The reader sees each level, not level 0 again: a detailed channel's mip is not a corner
    # of its level 0.
    detailed = [channel for channel in sorted(in_place) if np.ptp(before[channel, 0][..., :3]) > 0]
    assert 'BASE_COLOR' in detailed, detailed
    assert not np.array_equal(before['BASE_COLOR', 1], before['BASE_COLOR', 0][:N // 2, :N // 2])
    rows = history()

    with metrics.Metrics() as counted:
        for output in ts.outputs:
            output.image.gl_free()
        engine = ready()
    restored = counted.counts['engine_image_restorations']
    assert restored == len(in_place), (restored, sorted(in_place))
    assert history() == rows, 'restoring an evicted image evaluated the material again'
    for key, values in published().items():
        np.testing.assert_array_equal(values, before[key], err_msg='eviction ' + str(key))

    # A later edit publishes over the restored textures; a second eviction restores that edit.
    control = next(layer for layer in ts.layers if layer.id == ids['coat'])
    control.opacity = .41
    engine = ready()
    edited = published()
    assert any(not np.array_equal(edited[key], before[key]) for key in before), 'the edit changed nothing'
    for output in ts.outputs:
        output.image.gl_free()
    engine = ready()
    for key, values in published().items():
        np.testing.assert_array_equal(values, edited[key], err_msg='second eviction ' + str(key))
    print('ENGINE_EVICTION_PASS', len(in_place), 'channels restored in place, every mip', flush=True)
finally:
    open_painter.unregister()
