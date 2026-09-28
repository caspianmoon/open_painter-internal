"""Actual GPU stroke kernels against the persisted CPU raster, including packed scalars."""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache
from open_painter.evaluation.engine import runtime
from open_painter.painting import gesture, gpu_strokes, stroke_raster

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
gpu.init()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
scene = bpy.context.scene
ts = sets.create(scene, 'GPU strokes', 512)
sets.assign(scene, ts, bpy.context.object)
bpy.context.view_layer.update()
entries = gesture.snapshot_entries(ts)
n = 64
# Coincident geometry can still have different surface eligibility. It must not
# collapse two overlapping UV interiors into the last triangle's identifier.
try:
    gpu_strokes.Surface(entries + entries, n)
    raise AssertionError('Coincident overlapping UV surfaces were accepted')
except gpu_strokes.UnsupportedSurface:
    pass
samples = np.asarray([[-.3, .1, 0, 0, 0, 1, .4, .45, 0, 0, 1, 0, 0],
                      [0, .1, 0, 0, 0, 1, .8, .45, 0, 0, 1, 0, 0],
                      [.3, .1, 0, 0, 0, 1, .7, .4, 0, 0, 1, 0, 0]], np.float32)
base = np.empty((n, n, 4), np.float32)
base[:] = (.2, .2, .2, .4)
settings = scene.open_painter.vector_brush
settings.base_color = (.8, .3, .1, 1)
settings.roughness = .7
settings.height = .50013
settings.opacity = .75
settings.flow = .6
settings.hardness = .35
alpha = bpy.data.images.new('Alpha', width=8, height=8, float_buffer=True)
alpha_data = np.ones((8, 8, 4), np.float32)
alpha_data[..., :3] = np.linspace(0, 1, 8)[None, :, None]
alpha.pixels.foreach_set(alpha_data.ravel())
cases = 0
for mode in ('MAX', 'FLOW'):
    for blend in ('PAINT', 'ERASE'):
        for reach in ('DEPTH', 'SURFACE'):
            for textured in (False, True):
                settings.accumulate, settings.blend, settings.reach = mode, blend, reach
                settings.alpha_image = alpha if textured else None
                data = gesture.stroke_settings(settings)
                anchors = np.column_stack((samples[:, :3], np.zeros(len(samples), np.float32)))
                canvas = gpu_strokes.Canvas(entries, n, {c: base for c in ('BASE_COLOR', 'ROUGHNESS', 'HEIGHT')},
                                            {c: False for c in ('BASE_COLOR', 'ROUGHNESS', 'HEIGHT')})
                try:
                    for i in range(len(samples)):
                        canvas.stamp(samples[i:i + 1], data, anchors[i:i + 1])
                        canvas.publish(data)
                    from open_painter.painting.surface_reach import pack_anchors
                    stroke = SimpleNamespace(samples=stroke_raster.pack_samples(samples),
                        hardness=settings.hardness, flow=settings.flow, opacity=settings.opacity,
                        accumulate=mode, projection='SURFACE', plane_normal=(0, 0, 1),
                        alpha_image=settings.alpha_image, alpha_rotation='RAKE', alpha_angle=0,
                        depth_limit=settings.depth_limit, reach=reach, anchors=pack_anchors(anchors))
                    coverage = stroke_raster._rasterise(stroke, n, entries)[..., None]
                    for channel, resource in canvas.outputs.items():
                        actual = np.array(runtime.device().readback(resource.texture, n)).reshape(n, n, 4)
                        if resource.mapping == 'rg':
                            actual = actual[..., [0, 0, 0, 1]]
                        from open_painter.painting.strokes import channel_value
                        expected = base.copy()
                        if blend == 'ERASE':
                            expected[..., 3:4] *= 1 - coverage
                        else:
                            out_alpha = coverage + base[..., 3:4] * (1 - coverage)
                            color = np.asarray(channel_value(settings, channel)[:3])
                            expected[..., :3] = (color * coverage + base[..., :3] * base[..., 3:4] * (1 - coverage)) / out_alpha
                            expected[..., 3:4] = out_alpha
                        tolerance = 2e-6 if channel == 'HEIGHT' else 1e-3
                        error = float(np.abs(actual - expected).max())
                        assert error <= tolerance, (mode, blend, reach, textured, channel, error)
                        cases += 1
                finally:
                    canvas.close()
print('GPU_STROKES_PASS', cases, gpu.platform.backend_type_get(), flush=True)

# The engine must consume persisted records through GPU inputs and restore a
# prior prefix through its region history without rerasterizing old strokes.
from time import monotonic
from open_painter.core import layer_stack as stack, image_manager
from open_painter.evaluation import shader_preview, metrics
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.painting import strokes, gpu_sources
layer = stack.add(ts, 'PAINT', 'Recorded GPU strokes')
settings.blend, settings.accumulate, settings.reach = 'PAINT', 'MAX', 'DEPTH'
settings.alpha_image = None
settings.channels = {'BASE_COLOR', 'ROUGHNESS', 'HEIGHT'}


def ready():
    end = monotonic() + 90
    while monotonic() < end:
        shader_preview.tick(scene, ts)
        state = shader_preview.diagnostics(scene, ts)
        assert not state.get('error'), state
        if shader_preview.current(scene, ts):
            return
    raise AssertionError('GPU stroke source timed out')


def compare(label):
    engine = shader_preview.session_engine(scene, ts)
    for channel in settings.channels:
        actual = engine.render_channel(channel)[..., :3].copy()
        output = LayerEvaluator(scene).evaluate_channel(ts.id, channel)
        image = output.image if hasattr(output, 'image') else output
        expected = np.clip(image_manager.pixels(image)[..., :3], 0, 1)
        assert np.max(np.abs(actual - expected)) <= 2e-3, (label, channel, float(np.max(np.abs(actual - expected))))


strokes.record(scene.open_painter, ts, layer, samples[:1], settings, universal=False)
ready()
compare('first')
with metrics.Metrics() as measured:
    second = strokes.record(scene.open_painter, ts, layer, samples[1:], settings, universal=False)
    identifier = second.id
    ready()
    assert measured.counts.get('stroke_gpu_replays') == 3, measured.report()
compare('append')
with metrics.Metrics() as measured:
    strokes.remove(scene.open_painter, identifier)
    ready()
    assert measured.counts.get('stroke_gpu_history_hits') == 3, measured.report()
    assert not measured.counts.get('stroke_gpu_replays'), measured.report()
compare('undo prefix')
assert gpu_sources.memory(ts.id) > 0
gpu_sources.trim(ts.id, 0)
assert gpu_sources.memory(ts.id) == 0
# Eviction cannot retire geometry borrowed by an active gesture.
surface = gpu_strokes.Surface(entries, n)
canvas = gpu_strokes.Canvas(entries, n, {'BASE_COLOR': base}, {'BASE_COLOR': False}, surface=surface)
surface.close()
assert surface.pending_close and surface.position is not None
canvas.stamp(samples[:1], gesture.stroke_settings(settings))
canvas.publish(gesture.stroke_settings(settings))
canvas.close()
assert surface.position is None and surface.normal is None

# Partial shader failures preserve records and publish a deterministic CPU replay.
from unittest.mock import patch
with patch.object(gpu_strokes.Canvas, 'publish', side_effect=RuntimeError('injected pass failure')):
    with metrics.Metrics() as measured:
        strokes.record(scene.open_painter, ts, layer, samples[1:2], settings, universal=False)
        ready()
        assert measured.counts.get('stroke_cpu_fallbacks', 0) == 3, measured.report()
compare('partial GPU failure')

# Losing optional undo storage must still allow the stroke to finish.
gpu_sources.clear()
from open_painter.painting import gpu_history
with patch.object(gpu_history, 'capture', side_effect=MemoryError('injected snapshot allocation failure')):
    strokes.record(scene.open_painter, ts, layer, samples[2:], settings, universal=False)
    ready()
compare('undo allocation failure')
ts.resolution = '1024'
ready()
compare('resolution change')
uv = bpy.context.object.data.uv_layers.active.data
uv[0].uv.x += .015
bpy.context.object.data.update()
bpy.context.view_layer.update()
ready()
compare('geometry change')
scene.name = 'Renamed GPU authoring scene'
strokes.record(scene.open_painter, ts, layer, samples[:1], settings, universal=False)
ready()
compare('scene rename')
print('GPU_STROKE_SOURCES_PASS', flush=True)
open_painter.unregister()
