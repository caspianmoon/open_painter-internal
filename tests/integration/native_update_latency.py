"""Synchronous GPU-complete update timing; intentionally excludes a viewport."""
import json
import math
import os
from pathlib import Path
import sys
from time import perf_counter

import bpy
import gpu
import numpy as np

# Preload the selected release before importing the shared fixture. The fixture
# adds the checkout to sys.path, but submodules follow this package's __path__.
source = Path(os.environ['OP_UPDATE_SOURCE'])
sys.path.insert(0, str(source.parent))
import open_painter
assert Path(open_painter.__file__).resolve().parent == source.resolve()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_preview import fixture, shader_preview
from open_painter.constants import CHANNELS, VERSION
from open_painter.core import layer_stack
from open_painter.evaluation.engine import runtime
from open_painter.evaluation.metrics import Metrics

report = Path(os.environ['OP_UPDATE_REPORT'])
samples = int(os.environ.get('OP_UPDATE_SAMPLES', '100'))
gpu.init()
open_painter.register()
try:
    ts, _, ids, _ = fixture(bpy.context.scene, resolution=2048)
    control = next(layer for layer in ts.layers if layer.id == ids['coat'])
    for layer in ts.layers:
        for channel in CHANNELS:
            layer_stack.channel_for(layer, channel).enabled = True
    for _ in range(300):
        shader_preview.tick(bpy.context.scene, ts)
        info = shader_preview.diagnostics(bpy.context.scene, ts)
        assert not info['error'], info
        if shader_preview.current(bpy.context.scene, ts):
            break
    else:
        raise AssertionError('Fixture did not become current')
    dev = runtime.device()
    engine = shader_preview.session_engine(bpy.context.scene, ts)
    assert engine.report['execution_backend'] == 'c17-host-' + gpu.platform.backend_type_get().lower(), engine.report

    def update(value):
        started = perf_counter()
        control.opacity = value
        shader_preview.tick(bpy.context.scene, ts)
        assert shader_preview.current(bpy.context.scene, ts)
        # The same existing one-pixel GPU read fence in both releases. It is
        # instrumentation, never added to the production interactive path.
        dev.finish()
        return (perf_counter() - started) * 1000

    for i in range(20):
        update(.5 + .3 * math.sin(i * .7))
    start_memory = dev.memory()
    with Metrics() as metrics:
        durations = [update(.5 + .3 * math.sin(i * .7 + 1.1)) for i in range(samples)]
    end_memory = dev.memory()
    update(.37)
    pixels = report.with_suffix('.pixels.npz')
    np.savez_compressed(pixels, **{o.channel: np.array(gpu.texture.from_image(o.image).read(), np.float32)
                                  for o in ts.outputs})
    data = dict(status='PASS', version=VERSION, source=str(source),
        blender=bpy.app.version_string, build=bpy.app.build_hash.decode(), backend=gpu.platform.backend_type_get(),
        samples_ms=durations, median_ms=float(np.median(durations)), p95_ms=float(np.percentile(durations, 95)),
        passes=engine.report['passes'], memory_start=start_memory, memory_end=end_memory,
        counters=dict(metrics.counts), pixels=str(pixels),
        scope='RNA edit, session update and GPU completion in background Blender; includes one-pixel fence overhead; excludes viewport rendering, event delivery and scanout.')
    assert not data['counters'].get('engine_program_compiles', 0)
    assert not data['counters'].get('engine_image_pixel_writes', 0)
    report.write_text(json.dumps(data, indent=2) + '\n')
    print('NATIVE_UPDATE_LATENCY_PASS', VERSION, data['median_ms'], data['p95_ms'], flush=True)
finally:
    open_painter.unregister()
