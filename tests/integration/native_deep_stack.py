"""Deep-stack editing cost: smart materials stacked past a layer target, then warm edits.

Run in background Blender (``gpu.init``). Every timed edit is an RNA assignment,
one session tick and a one-pixel GPU fence, so the time covers Python callbacks,
native planning, every GPU pass and publication. Viewport drawing, event
delivery and scanout are excluded. Environment:

    OP_DEEP_REPORT   JSON report path (required)
    OP_DEEP_LAYERS   minimum layer count (default 200)
    OP_DEEP_RES      texture resolution (default 2048)
    OP_DEEP_SAMPLES  timed edits per case (default 24)
    OP_DEEP_PROFILE  1: add a cProfile summary of each case's Python time
"""
import cProfile
import io
import json
import math
import os
from pathlib import Path
import pstats
import sys
from time import perf_counter

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import CHANNELS, VERSION
from open_painter.core import image_manager as images, layer_stack as stack
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import shader_preview
from open_painter.evaluation.engine import runtime
from open_painter.evaluation.metrics import Metrics
from open_painter.smart_materials import composer
from open_painter.smart_materials.catalog import CATALOG

REPORT = Path(os.environ['OP_DEEP_REPORT'])
TARGET = int(os.environ.get('OP_DEEP_LAYERS', '200'))
RES = int(os.environ.get('OP_DEEP_RES', '2048'))
SAMPLES = int(os.environ.get('OP_DEEP_SAMPLES', '24'))
PROFILE = os.environ.get('OP_DEEP_PROFILE') == '1'
MAPS = ('CURVATURE', 'AO', 'WORLD_NORMAL', 'POSITION', 'NORMAL', 'MEAN_CURVATURE',
        'BEVEL_MASK', 'BEVEL_NORMAL')


def baked_maps(ts, size=512):
    """Smooth synthetic mesh maps: every recipe's mask requirement is satisfied."""
    y, x = np.indices((size, size), dtype=np.float32) / (size - 1)
    for index, kind in enumerate(MAPS):
        data = np.ones((size, size, 4), np.float32)
        wave = .5 + .5 * np.sin(6.2831 * (x * (index + 2) + y * (3 - index % 3)))
        data[..., 0] = wave
        data[..., 1] = .5 + .5 * np.cos(6.2831 * y * (index + 1))
        data[..., 2] = 1 - wave * .5
        image = images.create(f'Deep {kind}', size)
        images.write(image, data)
        entry = ts.baked_maps.add()
        entry.kind, entry.image = kind, image


def build(scene):
    for obj in tuple(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
    obj = bpy.context.object
    ts = sets.create(scene, 'Deep stack', RES)
    sets.assign(scene, ts, obj)
    baked_maps(ts)
    names = sorted(CATALOG)
    applied, index = [], 0
    while len(ts.layers) < TARGET:
        name = names[index % len(names)]
        composer.apply(ts, name, seed=index + 1)
        applied.append(name)
        index += 1
    for layer in ts.layers:
        if layer.type != 'FOLDER':
            for channel in CHANNELS:
                prop = stack.channel_for(layer, channel)
                if prop is not None and channel in {'BASE_COLOR', 'ROUGHNESS', 'METALLIC', 'HEIGHT'}:
                    prop.enabled = True
    stack.invalidate(ts)
    return ts, applied


def fence():
    runtime.device().finish()


def main():
    gpu.init()
    open_painter.register()
    scene = bpy.context.scene
    try:
        started = perf_counter()
        ts, applied = build(scene)
        authoring_s = perf_counter() - started
        counts = dict(layers=len(ts.layers),
                      folders=sum(layer.type == 'FOLDER' for layer in ts.layers),
                      masks=sum(len(layer.masks) for layer in ts.layers),
                      effects=sum(len(prop.effects) for layer in ts.layers for prop in layer.channels))
        print('DEEP_STACK_BUILT', counts, f'{authoring_s:.1f}s', flush=True)
        started = perf_counter()
        cold_profiler = cProfile.Profile() if PROFILE else None
        with Metrics() as cold_metrics:
            if cold_profiler:
                cold_profiler.enable()
            shader_preview.flush(scene, ts, pixels=False)
            fence()
            if cold_profiler:
                cold_profiler.disable()
        cold_s = perf_counter() - started
        engine = shader_preview.session_engine(scene, ts)
        cold = dict(seconds=cold_s, passes=engine.report.get('passes'), units=len(engine.states),
                    programs=len(runtime.device().shaders), compiles=runtime.device().compiles,
                    compile_seconds=runtime.device().compile_seconds,
                    backend=engine.report.get('execution_backend'), counters=dict(cold_metrics.counts),
                    memory=engine.memory())
        if cold_profiler:
            text = io.StringIO()
            pstats.Stats(cold_profiler, stream=text).sort_stats('cumulative').print_stats(60)
            cold['profile'] = text.getvalue()
        print('DEEP_STACK_COLD', f'{cold_s:.2f}s', cold['passes'], cold['units'], cold['backend'], flush=True)

        top_level = [layer for layer in ts.layers if not layer.parent_id]
        leaves = [layer for layer in ts.layers if layer.type != 'FOLDER']
        # ts.layers is bottom to top within the stack order used by the UI.
        cases = {
            'top_folder_opacity': (lambda: next(l for l in ts.layers if l.id == top_level[-1].id), 'opacity'),
            'bottom_folder_opacity': (lambda: next(l for l in ts.layers if l.id == top_level[0].id), 'opacity'),
            'middle_leaf_opacity': (lambda: next(l for l in ts.layers if l.id == leaves[len(leaves) // 2].id), 'opacity'),
            'bottom_leaf_roughness': (lambda: stack.channel_for(next(l for l in ts.layers if l.id == leaves[1].id), 'ROUGHNESS'), 'value'),
        }
        masked = next((l for l in leaves[len(leaves) // 3:] if len(l.masks)), None)
        if masked is not None:
            cases['middle_mask_amount'] = (lambda: next(l for l in ts.layers if l.id == masked.id).masks[0], 'amount')
        results = {}
        for name, (target, attribute) in cases.items():
            owner = target()
            base = float(getattr(owner, attribute))

            def edit(i):
                # Distinct consecutive values inside (0.3, 0.9): every edit changes the unit.
                value = .6 + .3 * math.sin(i * .9 + .3)
                begin = perf_counter()
                setattr(target(), attribute, value)
                shader_preview.tick(scene, ts)
                assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
                fence()
                return (perf_counter() - begin) * 1000
            for i in range(4):
                edit(i)
            profiler = cProfile.Profile() if PROFILE else None
            with Metrics() as metrics:
                if profiler:
                    profiler.enable()
                durations = [edit(i + 4) for i in range(SAMPLES)]
                if profiler:
                    profiler.disable()
            stage = engine.stages
            row = dict(median_ms=float(np.median(durations)), p95_ms=float(np.percentile(durations, 95)),
                       min_ms=float(np.min(durations)), samples_ms=durations,
                       passes=engine.report.get('passes'), units_rendered=engine.report.get('units_rendered'),
                       evaluate_ms=engine.report.get('evaluate_ms'),
                       stages={k: stage[k] for k in ('inputs_ms', 'translate_ms', 'compile_ms', 'publish_ms')},
                       render_ms=dict(stage['render_ms']), pass_kinds=dict(stage['passes']),
                       counters=dict(metrics.counts))
            if profiler:
                text = io.StringIO()
                pstats.Stats(profiler, stream=text).sort_stats('cumulative').print_stats(45)
                row['profile'] = text.getvalue()
                text = io.StringIO()
                pstats.Stats(profiler, stream=text).sort_stats('tottime').print_stats(30)
                row['profile_self'] = text.getvalue()
            results[name] = row
            setattr(target(), attribute, base)
            shader_preview.tick(scene, ts)
            print('DEEP_STACK_CASE', name, f"median {row['median_ms']:.1f} ms p95 {row['p95_ms']:.1f} ms",
                  'passes', row['passes'], 'units', row['units_rendered'], flush=True)
        data = dict(status='PASS', version=VERSION, blender=bpy.app.version_string,
                    backend=gpu.platform.backend_type_get(), resolution=RES, counts=counts,
                    applied=applied, authoring_seconds=authoring_s, cold=cold, cases=results,
                    library=os.environ.get('OP_LIBOPENPAINTER_PATH'),
                    native_engine=os.environ.get('OP_NATIVE_ENGINE'))
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(data, indent=2) + '\n')
        print('DEEP_STACK_PASS', REPORT, flush=True)
    finally:
        open_painter.unregister()


main()
