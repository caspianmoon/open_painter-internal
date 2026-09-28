"""GPU neighborhood filters match the CPU formulas, including clamped borders."""
import sys
from pathlib import Path

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack
from open_painter.evaluation import cache, channel_eval
from open_painter.evaluation.engine import runtime, filters

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
gpu.init()
scene = bpy.context.scene
ts = sets.create(scene, 'Spatial filter parity', 512)
layer = stack.add(ts, 'FILL', 'Filtered')
effect = layer.channels[0].effects.add()
effect.strength = 1
effect.amount = .8
effect.angle = .37
effect.samples = 8
effect.transform_scale = (1.2, .8)
effect.offset = (2.3, -1.2)
effect.noise = 'FBM'
effect.scale = 3.2
effect.seed = 19
n = 48
values = np.random.default_rng(91).random((n, n, 4), dtype=np.float32)
dev = runtime.device()
source = dev.upload(values)
count, errors = 0, {}
for kind in sorted(filters.KINDS):
    effect.kind = kind
    for radius in (0., 1.5, 8.):
        effect.radius = radius
        for mode in (('BLUR', 'MIN', 'MAX') if kind == 'SLOPE_BLUR' else ('BLUR',)):
            effect.mode = mode
            with dev.state():
                texture = filters.apply(source, effect, n)
                try:
                    actual = np.array(dev.readback(texture, n)).reshape(n, n, 4)
                finally:
                    dev.release(texture, n, 'RGBA32F')
            expected = channel_eval.apply_effect(values, effect)
            error = float(np.abs(actual - expected).max())
            errors[kind] = max(error, errors.get(kind, 0))
            assert error <= 2e-3, (kind, radius, mode, error)
            assert np.array_equal(actual[..., 3], values[..., 3]), (kind, 'alpha changed')
            count += 1
    print('FILTER_PASS', kind, errors[kind], flush=True)
dev.discard(source)
print('ENGINE_FILTERS_PASS', count, gpu.platform.backend_type_get(), flush=True)
# The cube's disconnected UV islands exercise cross-seam padding, crop borders,
# and surface directions. The CPU and GPU consume the same geometry lookup map.
bpy.ops.mesh.primitive_cube_add(size=2)
sets.assign(scene, ts, bpy.context.object)
bpy.context.view_layer.update()
effect.space = 'SURFACE'
effect.direction = (.3, .1, .8)
source = dev.upload(values)
effect.radius = 3.5
for kind in sorted(filters.KINDS & filters.SURFACE_KINDS):
    effect.kind = kind
    seams = channel_eval.seam_map_for(ts, n, [effect])
    assert seams is not None and seams.bounds
    with dev.state():
        texture = filters.apply(source, effect, n, ts)
        try:
            actual = np.array(dev.readback(texture, n)).reshape(n, n, 4)
        finally:
            dev.release(texture, n, 'RGBA32F')
    expected = channel_eval.apply_effect(values, effect, seams)
    error = float(np.abs(actual - expected).max())
    assert error <= 2e-3, ('SURFACE', kind, error)
    print('SURFACE_FILTER_PASS', kind, error, flush=True)
dev.discard(source)
open_painter.unregister()
