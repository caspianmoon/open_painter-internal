"""Resolved authoring data and CPU-only text; actual GPU material smudging."""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
import gpu
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack
from open_painter.evaluation import cache
from open_painter.evaluation.engine import runtime
from open_painter.evaluation.text_source import ensure
from open_painter.painting import gesture, gpu_strokes, stroke_raster, payload, symmetry

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
scene = bpy.context.scene
ts = sets.create(scene, 'Authoring', 512)
sets.assign(scene, ts, bpy.context.object)
layer = layer_stack.add(ts, 'PAINT', 'Paint')
mask = layer.masks.add()
mask.source = 'TEXT'
mask.text.content = 'Paint\\nTogether'
image = ensure(mask.text, 128)
assert sum(image.pixels[0::4]) > 50
saved = np.array(image.pixels[:])
assert ensure(mask.text, 128) == image
mask.text.font = '/missing/font.ttf'
assert np.array_equal(ensure(mask.text, 128).pixels[:], saved)
print('TEXT_CPU_PASS', flush=True)
row = np.array([0, .2, 0, 0, 0, 1, 1, .2, 0, 0, 1, 0, 0])
matrices = list(symmetry.transforms(np.diag((-2., 3., .5, 1)), (True, True, True)))
mirrored = list(symmetry.reflect(row, matrices))
assert len(mirrored) == 1 and np.allclose(mirrored[0][:3], (0, -.2, 0))
print('SYMMETRY_DEDUP_PASS', flush=True)
gpu.init()
bpy.context.view_layer.update()
entries = gesture.snapshot_entries(ts)
n = 64
samples = np.array([[-.25, 0, 0, 0, 0, 1, .7, .3, 0, 0, 1, 0, 0],
                    [0, 0, 0, 0, 0, 1, .9, .3, 0, 0, 1, 0, 0],
                    [.25, 0, 0, 0, 0, 1, .8, .3, 0, 0, 1, 0, 0]], np.float32)
dabs = [{'donor': samples[max(i - 1, 0), :3].tolist()} for i in range(len(samples))]
settings = scene.open_painter.vector_brush
settings.blend, settings.reach = 'SMUDGE', 'DEPTH'
settings.flow, settings.opacity, settings.hardness = .6, .8, .4
data = gesture.stroke_settings(settings)
data['dabs'] = dabs
base = np.random.default_rng(3).random((n, n, 4), dtype=np.float32)
for channel in ('BASE_COLOR', 'ROUGHNESS', 'HEIGHT', 'NORMAL'):
    initial = base.copy()
    if channel in {'ROUGHNESS', 'HEIGHT'}:
        initial[..., 1:3] = initial[..., :1]
    canvas = gpu_strokes.Canvas(entries, n, {channel: initial}, {channel: False}, precise=True)
    canvas.stamp(samples, data)
    canvas.publish(data)
    actual = np.asarray(runtime.device().readback(canvas.outputs[channel].texture, n)).reshape(n, n, 4)
    if canvas.outputs[channel].mapping == 'rg':
        actual = actual[..., [0, 0, 0, 1]]
    expected = initial.copy()
    frozen = gesture.brush_snapshot(settings)
    frozen.alpha_rotation, frozen.alpha_angle, frozen.plane_normal = 'RAKE', 0., (0, 0, 1)
    for sample, dab in zip(samples, dabs):
        stroke = payload.dab_stroke(frozen, sample, dab)
        coverage = stroke_raster._rasterise(stroke, n, entries)
        uv = payload.donor_uv(entries, dab['donor'], sample[3:6])
        expected = payload.smudge(expected, coverage, uv, channel == 'NORMAL')
    error = float(np.max(np.abs(actual - expected)))
    assert error < 3e-6, (channel, error)
    canvas.close()
    print('SMUDGE_PASS', channel, error, flush=True)
runtime.forget_device()
open_painter.unregister()
print('AUTHORING_SOURCES_PASS', flush=True)
