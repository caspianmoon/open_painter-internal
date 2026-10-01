"""Blender benchmark: --python this_file -- --size 2048 --channels 4 --output report.json.

Run each size/channel combination in a fresh Blender process. Timings include an
explicit GPU completion, not just command submission. Painting uses the actual
material gesture preview; undo measures a Blender undo plus engine restoration.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from time import perf_counter

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('OP_BENCHMARK_SOURCE', str(ROOT / 'src')))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import cache, metrics, shader_preview
from open_painter.evaluation.engine import runtime
from open_painter.painting import gesture
from open_painter.texture_engine import filters

parser = argparse.ArgumentParser()
parser.add_argument('--size', type=int, default=1024)
parser.add_argument('--channels', type=int, choices=(1, 4), default=1)
parser.add_argument('--repeat', type=int, default=5)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
gpu.init()
bpy.context.preferences.edit.use_global_undo = True
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
ts = sets.create(scene, 'Authoring benchmark', args.size)
sets.assign(scene, ts, bpy.context.object)
ts_id = ts.id
layer = stack.add(ts, 'PAINT', 'Paint')
layer_id = layer.id
channels = ('BASE_COLOR', 'ROUGHNESS', 'METALLIC', 'HEIGHT')[:args.channels]
for prop in layer.channels:
    prop.enabled = prop.channel in channels
    if prop.enabled:
        from open_painter.core.image_manager import create
        prop.image = create('Benchmark input ' + prop.channel, args.size, (.3, .3, .3, 1))
bpy.context.view_layer.update()


def current():
    scene = bpy.context.scene
    return scene, next(t for t in scene.open_painter.texture_sets if t.id == ts_id)


def synchronize(texture):
    # Read the actual result, not an unrelated 1x1 scratch texture: backends may
    # satisfy an unrelated read without waiting for pending paint/filter passes.
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(texture,))
    with runtime.device().state(), framebuffer.bind():
        framebuffer.read_color(0, 0, 1, 1, 4, 0, 'FLOAT')


def synchronize_outputs():
    _, ts = current()
    for output in ts.outputs:
        if output.channel in channels and output.image is not None:
            synchronize(gpu.texture.from_image(output.image))


def ready():
    scene, ts = current()
    deadline = perf_counter() + 180
    while perf_counter() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        if info.get('error'):
            raise RuntimeError(info['error'])
        if shader_preview.current(scene, ts):
            synchronize_outputs()
            return
    raise RuntimeError('Benchmark evaluation timed out')


def measure(fn, wait=synchronize_outputs):
    times = []
    before = dict(observed.counts)
    for i in range(args.repeat + 1):
        started = perf_counter()
        fn(i)
        wait()
        times.append((perf_counter() - started) * 1000)
    return {'cold_ms': times[0], 'warm_ms': times[1:],
            'counters': {k: v - before.get(k, 0) for k, v in observed.counts.items() if v != before.get(k, 0)},
            'p50_ms': float(np.percentile(times[1:], 50)), 'p95_ms': float(np.percentile(times[1:], 95))}


with metrics.Metrics() as observed:
    started = perf_counter()
    ready()
    initialization = (perf_counter() - started) * 1000
    scene, ts = current()
    layer = next(l for l in ts.layers if l.id == layer_id)
    settings = scene.open_painter.vector_brush
    settings.channels = set(channels)
    settings.reach = 'DEPTH'
    data = gesture.stroke_settings(settings)
    opened = perf_counter()
    preview = gesture.Preview(scene, ts, layer, channels, gesture.snapshot_entries(ts))
    runtime.device().finish()
    preview_initialization = (perf_counter() - opened) * 1000
    assert preview.active, 'Benchmark must exercise a live gesture'

    def paint(i):
        x = -.5 + i * .08
        sample = np.asarray([[x, 0, 0, 0, 0, 1, 1, .12, 0, 0, 1, 0, 0]], np.float32)
        preview.stamp(sample, data)
        preview.publish(data, force=True)
        ready()

    painting = measure(paint)
    preview_size = preview.size
    engine = shader_preview.session_engine(scene, ts)
    preview_memory = engine.memory() if hasattr(engine, 'memory') else None
    preview.restore()
    ready()

    def publish(i):
        scene, ts = current()
        engine = shader_preview.session_engine(scene, ts)
        engine.published.clear()
        engine.evaluate(channels=channels, budget=False)

    publication = measure(publish)
    # Scalar reference filter; separate from engine filtering so both algorithm
    # and integration costs can be compared without conflating them.
    source = np.random.default_rng(19).random((args.size, args.size), dtype=np.float32)
    def blur(i):
        for channel in channels:
            filters.gaussian_blur(source, 4)
    filtering = measure(blur, wait=lambda: None)
    gpu_filtering = None
    if not os.environ.get('OP_BENCHMARK_SOURCE'):
        from open_painter.evaluation.engine import filters as gpu_filters
        effect = layer.channels[0].effects.add()
        effect.kind, effect.radius = 'GAUSSIAN_BLUR', 4
        rgba = np.empty((args.size, args.size, 4), np.float32)
        rgba[..., :3], rgba[..., 3] = source[..., None], 1.
        texture = runtime.device().upload(rgba)
        del rgba
        def gpu_blur(i):
            dev = runtime.device()
            with dev.state():
                for channel in channels:
                    output = gpu_filters.apply(texture, effect, args.size)
                    synchronize(output)
                    dev.release(output, args.size, 'RGBA32F')
        gpu_filtering = measure(gpu_blur, wait=lambda: None)
        runtime.device().discard(texture)
        layer.channels[0].effects.remove(len(layer.channels[0].effects) - 1)

    def undo(i):
        scene, ts = current()
        bpy.ops.ed.undo_push(message='Authoring baseline')
        next(l for l in ts.layers if l.id == layer_id).opacity = .25 + (i % 2) * .5
        ready()
        bpy.ops.ed.undo_push(message='Authoring edit')
        assert bpy.ops.ed.undo() == {'FINISHED'}
        ready()
    undo_time = measure(undo)
    report = {'blender': bpy.app.version_string, 'backend': gpu.platform.backend_type_get(),
              'implementation': 'reference' if os.environ.get('OP_BENCHMARK_SOURCE') else 'upgrade',
              'synchronization': 'one-pixel read of actual output after each operation',
              'renderer': gpu.platform.renderer_get(), 'resolution': args.size, 'channels': channels,
              'preview_resolution': preview_size, 'initialization_ms': initialization,
              'preview_initialization_ms': preview_initialization, 'preview_memory': preview_memory,
              'painting': painting, 'publication': publication, 'cpu_gaussian': filtering, 'gpu_gaussian': gpu_filtering,
              'undo_transaction': undo_time, 'metrics': observed.report()}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
print('AUTHORING_BENCHMARK', json.dumps({k: v for k, v in report.items() if k != 'metrics'}), flush=True)
open_painter.unregister()
