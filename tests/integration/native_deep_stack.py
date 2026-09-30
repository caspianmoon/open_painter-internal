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
    OP_DEEP_FRAMES   0: skip the window-manager frame emulation (see Frame)
    OP_DEEP_STRUCTURAL 0: skip structural edits (add, remove, blend mode, visibility,
                     smart material), each timed from the edit until the set is current
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
STRUCTURAL = os.environ.get('OP_DEEP_STRUCTURAL', '1') == '1'
TRACE = os.environ.get('OP_DEEP_TRACE') == '1'
FIRST = os.environ.get('OP_DEEP_FIRST') == '1'   # time the first ticks of each drag
FRAMES = os.environ.get('OP_DEEP_FRAMES', '1') == '1'
TRACEMALLOC = os.environ.get('OP_DEEP_TRACEMALLOC') == '1'
ONLY = set(filter(None, os.environ.get('OP_DEEP_ONLY', '').split(',')))   # case names to run; empty: all
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


class Frame:
    """One iteration of Blender's window-manager loop, which background Blender never runs.

    The UI wraps event handling (timers included) in GPU_render_begin/end, which
    drains Metal's autorelease pool, and every draw update calls GPU_render_step
    and frees unused image textures, releasing Metal's deferred frees. Without
    it, a long background benchmark retains every GPU object it released. Test
    instrumentation only; the add-on never calls these internal functions.
    """
    _calls = None
    snapshot = None

    def __init__(self):
        if Frame._calls is None and FRAMES:
            import ctypes
            host = ctypes.CDLL(None)
            step = getattr(host, '_ZN7blender15GPU_render_stepEb')
            step.argtypes = [ctypes.c_bool]
            Frame._calls = (getattr(host, '_ZN7blender16GPU_render_beginEv'), step,
                            getattr(host, '_ZN7blender34BKE_image_free_unused_gpu_texturesEv'),
                            getattr(host, '_ZN7blender14GPU_render_endEv'))

    def __enter__(self):
        if FRAMES:
            Frame._calls[0]()
        return self

    def __exit__(self, *exc):
        if FRAMES:
            begin, step, free_unused, end = Frame._calls
            step(False)
            free_unused()
            end()


def image_breakdown(ts):
    """CPU bytes of loaded images by role (each also has a GPU copy once sampled)."""
    outputs = {out.image.as_pointer() for out in ts.outputs if out.image}
    baked = {entry.image.as_pointer() for entry in ts.baked_maps if entry.image}
    rows = {}
    for image in bpy.data.images:
        if not image.has_data:
            continue
        pointer = image.as_pointer()
        role = ('output' if pointer in outputs else 'baked' if pointer in baked
                else 'leaf' if image.get('open_painter_shader_resource') else
                'bundled' if image.get('open_painter_generated_source') else 'other')
        size = image.size[0] * image.size[1] * image.channels * (4 if image.is_float else 1)
        count, total = rows.get(role, (0, 0))
        rows[role] = (count + 1, total + size)
    return {role: dict(count=count, mib=total // 2 ** 20) for role, (count, total) in rows.items()}


def footprint_mib():
    """This process's physical footprint (Activity Monitor's Memory; GPU memory included on Apple silicon)."""
    import ctypes
    try:
        libproc = ctypes.CDLL('/usr/lib/libproc.dylib')
    except OSError:
        return None
    buffer = (ctypes.c_uint8 * 256)()
    if libproc.proc_pid_rusage(os.getpid(), 2, buffer) != 0:
        return None
    return int.from_bytes(bytes(buffer[72:80]), 'little') // (1024 * 1024)


def settle(scene, ts, started):
    """Tick until the set is current; milliseconds from ``started`` and the ticks it took."""
    for ticks in range(1, 2001):
        with Frame():
            shader_preview.tick(scene, ts)
        if TRACE and TRACEMALLOC and ticks % 2 == 0:
            import tracemalloc
            snapshot = tracemalloc.take_snapshot()
            if Frame.snapshot is not None:
                for stat in snapshot.compare_to(Frame.snapshot, 'lineno')[:6]:
                    print('DEEP_STACK_TRACE malloc', str(stat)[:220], flush=True)
            Frame.snapshot = snapshot
        if TRACE:
            engine = shader_preview.session_engine(scene, ts)
            loaded = [image for image in bpy.data.images if image.has_data]
            print('DEEP_STACK_TRACE tick', ticks, 'footprint', footprint_mib(), 'MiB images', len(bpy.data.images),
                  'loaded', len(loaded), sum(i.size[0] * i.size[1] * i.channels * (4 if i.is_float else 1)
                                             for i in loaded) // 2 ** 20, 'MiB engine',
                  {k: v // 2 ** 20 for k, v in engine.memory().items() if isinstance(v, int)} if engine else None,
                  flush=True)
        info = shader_preview.diagnostics(scene, ts, details=False)
        assert not info.get('error'), info
        if shader_preview.current(scene, ts) and not info.get('pending_resources'):
            fence()
            return (perf_counter() - started) * 1000, ticks
    raise AssertionError('The set did not settle')


def run_structural(scene, ts, repeats=3):
    """Structural edits on the deep stack, each timed from the edit until the set is current."""
    rows = {}

    def record(name, action):
        if ONLY and name not in ONLY:
            return
        durations, ticks = [], []
        profiler = cProfile.Profile() if PROFILE else None
        for index in range(repeats):
            started = perf_counter()
            if profiler:
                profiler.enable()
            with Frame():
                action(index)
            if TRACE:
                print('DEEP_STACK_TRACE', name, index, 'edited, footprint', footprint_mib(), 'MiB', flush=True)
            elapsed, count = settle(scene, ts, started)
            if profiler:
                profiler.disable()
            durations.append(elapsed)
            ticks.append(count)
        rows[name] = dict(median_ms=float(np.median(durations)), samples_ms=durations, ticks=ticks,
                          footprint_mib=footprint_mib())
        if profiler:
            text = io.StringIO()
            pstats.Stats(profiler, stream=text).sort_stats('cumulative').print_stats(50)
            rows[name]['profile'] = text.getvalue()
        print('DEEP_STACK_STRUCTURAL', name, f"median {rows[name]['median_ms']:.0f} ms", 'ticks', ticks,
              'footprint', rows[name]['footprint_mib'], 'MiB', flush=True)

    leaves = [layer.id for layer in ts.layers if layer.type != 'FOLDER']
    middle = leaves[len(leaves) // 2]

    def layer(identifier):
        return next(item for item in ts.layers if item.id == identifier)

    def add_fill(index):
        ts.active_layer = len(ts.layers) - 1
        added = stack.add(ts, 'FILL', f'Structural fill {index}')
        stack.channel_for(added, 'BASE_COLOR').color = (.3, .2 + index * .1, .6, 1)
        stack.invalidate(ts)
    record('add_fill_top', add_fill)

    def remove_fill(index):
        stack.remove(ts, stack.index_of(ts, next(item.id for item in ts.layers
                                                  if item.name.startswith('Structural fill'))))
        stack.invalidate(ts)
    record('remove_layer', remove_fill)

    def blend(index):
        layer(middle).blend_mode = ('MULTIPLY', 'NORMAL', 'SCREEN')[index % 3]
    record('blend_mode_middle', blend)

    def visibility(index):
        target = layer(middle)
        target.visible = not target.visible
    record('visibility_middle', visibility)

    names = sorted(CATALOG)

    def apply_material(index):
        if TRACEMALLOC and index == 0:
            import tracemalloc
            tracemalloc.start(8)
        ts.active_layer = len(ts.layers) - 1
        composer.apply(ts, names[(index * 7) % len(names)], seed=100 + index)
    record('apply_smart_material', apply_material)
    return rows


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
            with Frame():
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
        cold['footprint_mib'] = footprint_mib()
        cold['images'] = image_breakdown(ts)
        print('DEEP_STACK_IMAGES', cold['images'], flush=True)
        print('DEEP_STACK_COLD', f'{cold_s:.2f}s', cold['passes'], cold['units'], cold['backend'],
              'footprint', cold['footprint_mib'], 'MiB', flush=True)

        top_level = [layer for layer in ts.layers if not layer.parent_id]
        leaves = [layer for layer in ts.layers if layer.type != 'FOLDER']
        # ts.layers is bottom to top within the stack order used by the UI.
        ids = dict(top_folder=top_level[-1].id, bottom_folder=top_level[0].id,
                   middle_leaf=leaves[len(leaves) // 2].id, second_leaf=leaves[1].id)

        def layer_by(identifier):
            return next(l for l in ts.layers if l.id == identifier)

        cases = {
            'top_folder_opacity': (lambda: layer_by(ids['top_folder']), 'opacity', ids['top_folder']),
            'bottom_folder_opacity': (lambda: layer_by(ids['bottom_folder']), 'opacity', ids['bottom_folder']),
            'middle_leaf_opacity': (lambda: layer_by(ids['middle_leaf']), 'opacity', ids['middle_leaf']),
            'bottom_leaf_roughness': (lambda: stack.channel_for(layer_by(ids['second_leaf']), 'ROUGHNESS'), 'value',
                                      ids['second_leaf']),
        }
        masked = next((l.id for l in leaves[len(leaves) // 3:] if len(l.masks)), None)
        if masked is not None:
            # The mask's black level: the same control the real-window slider test drags.
            cases['middle_mask_black'] = (lambda: layer_by(masked).masks[0], 'black', masked)
        results = {}
        for name, (target, attribute, layer_id) in cases.items():
            if ONLY and name not in ONLY:
                continue
            owner = target()
            base = float(getattr(owner, attribute))

            def edit(i):
                # Distinct consecutive values inside (0.3, 0.9): every edit changes the unit.
                value = .6 + .3 * math.sin(i * .9 + .3)
                with Frame():
                    begin = perf_counter()
                    setattr(target(), attribute, value)
                    shader_preview.tick(scene, ts)
                    assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
                    fence()
                    return (perf_counter() - begin) * 1000
            first = {}
            if FIRST:
                # A new drag on the selected layer: idle arming (when enabled) runs to completion first.
                ts.active_layer = next(i for i, l in enumerate(ts.layers) if l.id == layer_id)
                from open_painter.evaluation import dependencies
                from open_painter.evaluation.engine import suffix
                prearmed = 0
                with Frame():
                    while prearmed < 64 and engine.prearm(layer_id, dependencies.image_epochs()):
                        prearmed += 1
                    fence()
                first = dict(prearmed_runs=prearmed, prearm=suffix.prearm_enabled())
                for label, index in (('first', 100), ('second', 101), ('third', 102)):
                    with Metrics() as tick_metrics:
                        first[label + '_ms'] = edit(index)
                    first[label + '_suffix'] = {k[len('engine_suffix_'):]: v for k, v in tick_metrics.counts.items()
                                                if k.startswith('engine_suffix_')}
                print('DEEP_STACK_FIRST', name, json.dumps(first), flush=True)
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
            row = dict(first=first, median_ms=float(np.median(durations)), p95_ms=float(np.percentile(durations, 95)),
                       min_ms=float(np.min(durations)), samples_ms=durations,
                       passes=engine.report.get('passes'), units_rendered=engine.report.get('units_rendered'),
                       evaluate_ms=engine.report.get('evaluate_ms'),
                       stages={k: stage[k] for k in ('inputs_ms', 'translate_ms', 'compile_ms', 'publish_ms')},
                       render_ms=dict(stage['render_ms']), pass_kinds=dict(stage['passes']),
                       counters=dict(metrics.counts), footprint_mib=footprint_mib(),
                       engine_memory_mib={k: v // 2 ** 20 for k, v in engine.memory().items() if isinstance(v, int)})
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
                  'passes', row['passes'], 'units', row['units_rendered'], 'footprint', row['footprint_mib'], 'MiB',
                  'engine', row['engine_memory_mib'], flush=True)
        structural = run_structural(scene, ts) if STRUCTURAL else {}
        data = dict(status='PASS', version=VERSION, blender=bpy.app.version_string,
                    backend=gpu.platform.backend_type_get(), resolution=RES, counts=counts,
                    applied=applied, authoring_seconds=authoring_s, cold=cold, cases=results,
                    structural=structural,
                    library=os.environ.get('OP_LIBOPENPAINTER_PATH'),
                    native_engine=os.environ.get('OP_NATIVE_ENGINE'))
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(data, indent=2) + '\n')
        print('DEEP_STACK_PASS', REPORT, flush=True)
    finally:
        open_painter.unregister()


main()
