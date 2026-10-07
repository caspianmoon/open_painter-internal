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
    OP_DEEP_PROFILE_STEADY 1: leave the first undo out of the undo profiles
    OP_DEEP_FRAMES   0: skip the window-manager frame emulation (see Frame; off on Windows)
    OP_DEEP_STRUCTURAL 0: skip structural edits (add, remove, blend mode, visibility,
                     smart material), each timed from the edit until the set is current
    OP_DEEP_PHASES   1: print wall-clock totals of the main phases of each edit
    OP_DEEP_SECTIONS 1: with OP_DEEP_PHASES, also the sections of patching and native planning
    OP_DEEP_REPEATS  samples per structural edit (default 3)
    OP_DEEP_PAINT_STEPS brush inputs on a paint layer mid-stack (default 24; OP_DEEP_ONLY=paint_middle)
    OP_DEEP_PAINT_UPLOAD 1: brush inputs as CPU pixel writes (a whole-canvas upload each)
"""
import cProfile
import io
import json
import math
import os
from pathlib import Path
import pstats
import sys
from time import monotonic, perf_counter

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import CHANNELS, VERSION
from open_painter.core import image_manager as images, layer_stack as stack
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import dependencies, gc_policy, shader_preview
from open_painter.evaluation.engine import runtime
from open_painter.evaluation.metrics import Metrics
from open_painter.smart_materials import composer
from open_painter.smart_materials.catalog import CATALOG

REPORT = Path(os.environ['OP_DEEP_REPORT'])
TARGET = int(os.environ.get('OP_DEEP_LAYERS', '200'))
RES = int(os.environ.get('OP_DEEP_RES', '2048'))
SAMPLES = int(os.environ.get('OP_DEEP_SAMPLES', '24'))
PROFILE = os.environ.get('OP_DEEP_PROFILE') == '1'
PROFILE_STEADY = os.environ.get('OP_DEEP_PROFILE_STEADY') == '1'   # undo profiles skip the first undo
STRUCTURAL = os.environ.get('OP_DEEP_STRUCTURAL', '1') == '1'
TRACE = os.environ.get('OP_DEEP_TRACE') == '1'
FIRST = os.environ.get('OP_DEEP_FIRST') == '1'   # time the first ticks of each drag
# Frame calls Blender internals by their exported names; blender.exe on Windows exports none.
FRAMES = os.environ.get('OP_DEEP_FRAMES', '0' if sys.platform == 'win32' else '1') == '1'
TRACEMALLOC = os.environ.get('OP_DEEP_TRACEMALLOC') == '1'
ONLY = set(filter(None, os.environ.get('OP_DEEP_ONLY', '').split(',')))   # case names to run; empty: all
# Brush inputs as CPU pixel writes, which make every next texture fetch upload the whole canvas
# (7-11 ms at 1K): what this case measured before 2026-10-03. By default a dab is drawn into the
# canvas texture, as Blender's brush updates only the region it paints.
PAINT_UPLOAD = os.environ.get('OP_DEEP_PAINT_UPLOAD') == '1'
PHASES = os.environ.get('OP_DEEP_PHASES') == '1'   # real-time totals of the main phases of each edit
phase_totals = {}


def time_phases():
    """Wrap the phases of an edit with wall-clock counters (no profiler overhead)."""
    from open_painter.evaluation import shader_graph
    from open_painter.native_bridge import graph as native_graph, channels as native_channels

    def timed(owner, name, label):
        original = getattr(owner, name)
        depth = [0]   # a recursive phase counts its outermost call only

        def wrapper(*args, **kwargs):
            depth[0] += 1
            started = perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                depth[0] -= 1
                if not depth[0]:
                    phase_totals[label] = phase_totals.get(label, 0.0) + (perf_counter() - started) * 1000
        setattr(owner, name, wrapper)
    timed(shader_preview, '_update', 'structure check (callback)')
    timed(shader_graph.Graph, 'build', 'build')
    timed(runtime.Engine, 'adopt', 'engine adopt')
    timed(native_graph.NativeGraph, 'prepare', 'native prepare')
    timed(native_channels.NativeChannels, 'execute', 'native execute')
    timed(runtime.Engine, 'evaluate', 'evaluate (all)')
    timed(shader_preview, 'tick', 'tick (all)')
    if os.environ.get('OP_DEEP_SECTIONS') == '1':
        # The sections of a structural edit (nested in the phases above).
        from open_painter.evaluation.engine import compiler
        for owner, name in ((compiler.EngineGraph, 'patch'), (shader_graph.Graph, 'plan'),
                            (shader_graph.Graph, '_follow_moved_layer'), (shader_graph.Graph, '_refresh_paths'),
                            (shader_graph.Graph, 'compile_unit'), (compiler.EngineGraph, '_reverse_changed'),
                            (compiler, '_chains'), (compiler.EngineGraph, '_reach_targets'),
                            (compiler.EngineGraph, '_index_importers'), (shader_graph.Graph, 'update'),
                            (shader_graph.Graph, 'validate_parameters'), (shader_graph.Graph, '_unbind_units'),
                            (native_graph.NativeGraph, '_index'), (native_graph.NativeGraph, '_compose'),
                            (native_graph.NativeGraph, '_scan_images'), (runtime.Engine, '_state'),
                            (runtime, '_positions'), (native_graph.NativeGraph, '_redescribe'),
                            (native_graph.NativeGraph, '_reindex'), (native_graph.NativeGraph, '_send_topology'),
                            (native_graph.NativeGraph, '_orders'), (native_graph.NativeGraph, '_regroup'), (compiler.EngineGraph, '_report'),
                            (compiler.EngineGraph, 'structural_signature'), (shader_graph.Graph, 'rebuild_scope'),
                            (compiler.EngineGraph, '_references_reached'), (shader_graph.Graph, '_live_effects'),
                            (shader_graph.Graph, '_used_resources'), (compiler.EngineGraph, '_recompile_importers'),
                            (shader_graph.Graph, 'cache_states'), (shader_graph.Graph, '_build_warnings')):
            timed(owner, name, f"{owner.__name__.rpartition('.')[2]}.{name}")
    import gc
    collecting = {}

    def collection(phase, info):
        if phase == 'start':
            collecting['started'] = perf_counter()
            return
        label = f"gc gen{info['generation']}"
        phase_totals[label] = phase_totals.get(label, 0.0) + (perf_counter() - collecting['started']) * 1000
    gc.callbacks.append(collection)
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


def tick(scene, ts):
    """One tick of the set as the scheduler runs it (``cache._tick``): pending depsgraph updates
    are reported first."""
    dependencies.report_updates()
    shader_preview.tick(scene, ts)


def settle(scene, ts, started):
    """Tick until the set is current; milliseconds from ``started`` and the ticks it took."""
    for ticks in range(1, 2001):
        with Frame():
            tick(scene, ts)
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


def run_structural(scene, ts, repeats=int(os.environ.get('OP_DEEP_REPEATS', '3'))):
    """Structural edits on the deep stack, each timed from the edit until the set is current."""
    rows = {}

    def record(name, action, selected=None):
        if ONLY and name not in ONLY:
            return
        durations, ticks, callbacks, stage_rows = [], [], [], []
        profiler = cProfile.Profile() if PROFILE else None
        from open_painter.evaluation import dependencies
        from open_painter.evaluation.cache import key_for
        for index in range(repeats):
            if selected is not None:
                # The UI edits the selected layer (adds above it, removes it), whose runs and
                # spine idle arming prepared.
                identifier = selected() if callable(selected) else selected
                ts.active_layer = next(i for i, l in enumerate(ts.layers) if l.id == identifier)
                engine = shader_preview.session_engine(scene, ts)
                with Frame():
                    while engine.prearm(identifier, dependencies.image_epochs()):
                        pass
                    # Arming runs while idle, long before the next edit: its GPU work is done
                    # by then, not waited for by the edit's first readback (as in FIRST mode).
                    fence()
            # The pause between two edits: the idle timer freezes what the last one kept.
            gc_policy.settle(now=monotonic() + gc_policy.SETTLE_DELAY)
            entry = shader_preview._sessions.get(key_for(scene, ts), {})
            seen = len(entry.get('history', ()))
            phase_totals.clear()
            counted = Metrics() if PHASES else None
            if counted is not None:
                counted.__enter__()
            started = perf_counter()
            if profiler:
                profiler.enable()
            with Frame():
                action(index)
            callbacks.append((perf_counter() - started) * 1000)
            if TRACE:
                print('DEEP_STACK_TRACE', name, index, 'edited, footprint', footprint_mib(), 'MiB', flush=True)
            elapsed, count = settle(scene, ts, started)
            if profiler:
                profiler.disable()
            if counted is not None:
                counted.__exit__(None, None, None)
                print('DEEP_STACK_COUNTS', name, index, {k: v for k, v in sorted(counted.counts.items())
                                                         if 'suffix' in k or 'pass' in k or 'patch' in k
                                                         or 'build' in k}, flush=True)
            durations.append(elapsed)
            ticks.append(count)
            # Stage rows this edit produced (the history holds the last few evaluations).
            entry = shader_preview._sessions.get(key_for(scene, ts), {})
            produced = list(entry.get('history', ()))[seen:] if len(entry.get('history', ())) > seen else []
            if PHASES:
                import gc
                print('DEEP_STACK_PHASES', name, index, f'total {elapsed:.1f} ms', 'frozen', gc.get_freeze_count(),
                      {label: round(ms, 1) for label, ms in sorted(phase_totals.items())}, flush=True)
            stage_rows.append({stage: round(sum(row[stage] for row in produced), 2) for stage in
                               ('build', 'update', 'inputs', 'translate', 'compile', 'render', 'publish',
                                'rebuilt', 'reused')})
        rows[name] = dict(median_ms=float(np.median(durations)), samples_ms=durations, ticks=ticks,
                          footprint_mib=footprint_mib(), callback_ms=callbacks, stages=stage_rows)
        if profiler:
            text = io.StringIO()
            pstats.Stats(profiler, stream=text).sort_stats('cumulative').print_stats(50)
            rows[name]['profile'] = text.getvalue()
            profiler.dump_stats(str(REPORT) + f'.{name}.prof')   # raw data, for callers and callees
        print('DEEP_STACK_STRUCTURAL', name, f"median {rows[name]['median_ms']:.0f} ms", 'ticks', ticks,
              'footprint', rows[name]['footprint_mib'], 'MiB', 'callback ms', [round(c) for c in callbacks],
              'stages', stage_rows[-1] if stage_rows else None, flush=True)

    leaves = [layer.id for layer in ts.layers if layer.type != 'FOLDER']
    middle = leaves[len(leaves) // 2]

    def layer(identifier):
        return next(item for item in ts.layers if item.id == identifier)

    def add_fill(index):
        ts.active_layer = len(ts.layers) - 1
        added = stack.add(ts, 'FILL', f'Structural fill {index}')
        stack.channel_for(added, 'BASE_COLOR').color = (.3, .2 + index * .1, .6, 1)
    def top():
        return ts.layers[len(ts.layers) - 1].id
    record('add_fill_top', add_fill, top)

    def remove_fill(index):
        stack.remove(ts, stack.index_of(ts, next(item.id for item in ts.layers
                                                  if item.name.startswith('Structural fill'))))
    record('remove_layer', remove_fill,
           lambda: next(item.id for item in ts.layers if item.name.startswith('Structural fill')))

    def add_fill_middle(index):
        # As the artist adds one: above the selected layer, mid-stack.
        ts.active_layer = next(i for i, item in enumerate(ts.layers) if item.id == middle)
        added = stack.add(ts, 'FILL', f'Middle fill {index}')
        stack.channel_for(added, 'BASE_COLOR').color = (.2, .3 + index * .1, .5, 1)
    record('add_fill_middle', add_fill_middle, middle)

    def remove_middle(index):
        stack.remove(ts, stack.index_of(ts, next(item.id for item in ts.layers
                                                  if item.name.startswith('Middle fill'))))
    record('remove_middle', remove_middle,
           lambda: next(item.id for item in ts.layers if item.name.startswith('Middle fill')))

    def move_middle(index):
        stack.move(ts, stack.index_of(ts, middle), 1 if index % 2 == 0 else -1)
    record('move_middle', move_middle, middle)

    def blend(index):
        layer(middle).blend_mode = ('MULTIPLY', 'NORMAL', 'SCREEN')[index % 3]
    record('blend_mode_middle', blend, middle)

    def visibility(index):
        target = layer(middle)
        target.visible = not target.visible
    record('visibility_middle', visibility, middle)

    names = sorted(CATALOG)

    def apply_material(index):
        if TRACEMALLOC and index == 0:
            import tracemalloc
            tracemalloc.start(8)
        ts.active_layer = len(ts.layers) - 1
        composer.apply(ts, names[(index * 7) % len(names)], seed=100 + index)
    record('apply_smart_material', apply_material, top)
    return rows


def run_undo(scene, ts, repeats=int(os.environ.get('OP_DEEP_REPEATS', '3'))):
    """Memfile undo of a numeric edit (a leaf's opacity) and of a structural one (its blend
    mode), each timed from ``ed.undo`` until the set is current again."""
    from statistics import median
    leaves = [layer.id for layer in ts.layers if layer.type != 'FOLDER']
    middle, set_id = leaves[len(leaves) // 2], ts.id
    current = {}

    def resolve():
        """Undo reloads the Scene: references taken before it are stale."""
        current['scene'] = bpy.context.scene
        current['ts'] = next(item for item in current['scene'].open_painter.texture_sets if item.id == set_id)
    resolve()

    def layer():
        return next(item for item in current['ts'].layers if item.id == middle)

    # The pause before the first edit, as before a structural edit (run_structural): the idle
    # ticks arm the edited layer and keep the finals the paint strokes published fused, the
    # state the first undo returns to.
    from open_painter.evaluation import dependencies
    current['ts'].active_layer = next(i for i, item in enumerate(current['ts'].layers) if item.id == middle)
    engine = shader_preview.session_engine(current['scene'], current['ts'])
    with Frame():
        while engine.prearm(middle, dependencies.image_epochs()):
            pass
        fence()
    edits = {'undo_numeric': lambda i: setattr(layer(), 'opacity', .3 + .1 * (i % 5)),
             'undo_structural': lambda i: setattr(layer(), 'blend_mode', 'MULTIPLY' if i % 2 else 'SCREEN')}
    rows = {}
    for name, edit in edits.items():
        if ONLY and name not in ONLY:
            continue
        durations = []
        profiler = cProfile.Profile() if PROFILE else None
        for index in range(repeats):
            bpy.ops.ed.undo_push(message=f'{name} before {index}')
            edit(index)
            settle(current['scene'], current['ts'], perf_counter())
            bpy.ops.ed.undo_push(message=f'{name} {index}')
            gc_policy.settle(now=monotonic() + gc_policy.SETTLE_DELAY)   # the pause before the undo
            phase_totals.clear()
            counted = Metrics() if PHASES else None
            if counted:
                counted.__enter__()
            started = perf_counter()
            # The session's first memfile step was written whole, so undoing to it re-reads every
            # ID (Blender rebuilds every image's GPU texture): OP_DEEP_PROFILE_STEADY=1 profiles
            # the later undos only.
            profiled = profiler and (index or not PROFILE_STEADY)
            if profiled:
                profiler.enable()
            with Frame():
                bpy.ops.ed.undo()
            resolve()
            elapsed, count = settle(current['scene'], current['ts'], started)
            if profiled:
                profiler.disable()
            durations.append(elapsed)
            if PHASES:
                counted.__exit__(None, None, None)
                print('DEEP_STACK_PHASES', name, index, f'total {elapsed:.1f} ms',
                      {k: round(v, 1) for k, v in sorted(phase_totals.items())}, flush=True)
                engine = shader_preview.session_engine(current['scene'], current['ts'])
                native = getattr(engine, '_native_graph', None)
                print('DEEP_STACK_COUNTS', name, index, {k: v for k, v in sorted(counted.counts.items())
                                                          if k.startswith(('engine_', 'native_'))},
                      'native patch', getattr(native, 'last_patch_count', None), 'dirty',
                      getattr(native, 'last_dirty_count', None), 'rendered', len(getattr(engine, 'rendered', ())),
                      flush=True)
        rows[name] = dict(median_ms=median(durations), durations=durations, footprint_mib=footprint_mib())
        if profiler:
            text = io.StringIO()
            pstats.Stats(profiler, stream=text).sort_stats('cumulative').print_stats(60)
            rows[name]['profile'] = text.getvalue()
            profiler.dump_stats(str(REPORT) + f'.{name}.prof')
        print('DEEP_STACK_UNDO', name, f"median {rows[name]['median_ms']:.0f} ms", [round(d) for d in durations],
              'footprint', rows[name]['footprint_mib'], 'MiB', flush=True)
    return rows


def run_paint(scene, ts, steps=int(os.environ.get('OP_DEEP_PAINT_STEPS', '24'))):
    """Emulated brush inputs on a paint layer added in the middle of the stack (as
    paint_freeze.py drives them), each timed from the pixels landing until the set is
    current. Writing the pixels is Blender's brush in the product and is not timed."""
    from statistics import median
    from unittest.mock import patch
    from open_painter.evaluation import cache, dependencies
    from open_painter.painting import paint_session
    from open_painter.painting.channel_paint import target as paint_target
    project = scene.open_painter
    leaves = [layer.id for layer in ts.layers if layer.type != 'FOLDER']
    middle = leaves[len(leaves) // 2]
    ts.active_layer = next(i for i, item in enumerate(ts.layers) if item.id == middle)
    layer_id = stack.add(ts, 'PAINT', 'Deep paint').id
    image = paint_target(ts, next(item for item in ts.layers if item.id == layer_id), 'BASE_COLOR')
    settle(scene, ts, perf_counter())
    project.paint_image = image
    project.paint_set_id, project.paint_layer_id = ts.id, layer_id
    project.paint_channel = project.paint_target_channel = 'BASE_COLOR'
    project.paint_mask, project.paint_mask_id = False, ''
    project.paint_canvases.clear()
    paint_session.reset()
    paint_session.detect(scene, force=True)
    pointer = scene.as_pointer()
    engine = shader_preview.session_engine(scene, ts)
    with Frame():   # the idle timer arms the selected layer
        while engine.prearm(layer_id, dependencies.image_epochs()):
            pass
    data, n = images.pixels(image), image.size[0]
    durations, ticks = [], []
    profiler = cProfile.Profile() if PROFILE else None
    canvas = None if PAINT_UPLOAD else gpu.types.GPUFrameBuffer(color_slots=gpu.texture.from_image(image))
    try:
        with patch.object(cache, '_modal_painting', lambda s: True):
            for step in range(steps):
                y, x = (step * 37) % (n - 64), (step * 53) % (n - 64)
                value = (step % 5) / 5 + .1
                if canvas is None:
                    # Every CPU write makes the next texture fetch upload the whole canvas.
                    data[y:y + 64, x:x + 64, :3] = value
                    image.pixels.foreach_set(data.ravel())
                    image.update()
                else:
                    # Blender's brush updates the canvas texture where it paints: a dab on the GPU.
                    with canvas.bind():
                        gpu.state.scissor_test_set(True)
                        gpu.state.scissor_set(x, y, 64, 64)
                        canvas.clear(color=(value, value, value, 1.0))
                        gpu.state.scissor_test_set(False)
                phase_totals.clear()
                started = perf_counter()
                if profiler:
                    profiler.enable()
                cache._live_checks.pop(pointer, None)
                with Frame():
                    cache.tick()
                elapsed, count = settle(scene, ts, started)
                if profiler:
                    profiler.disable()
                durations.append(elapsed)
                ticks.append(count)
                if PHASES:
                    engine = shader_preview.session_engine(scene, ts)
                    print('DEEP_STACK_PHASES paint_middle', step, f'total {elapsed:.1f} ms',
                          {k: round(v, 1) for k, v in sorted(phase_totals.items())},
                          'passes', engine.report.get('passes') if engine else None, flush=True)
        cache._paint_input[pointer] = (True, monotonic() - .2)   # the stroke is released
        cache._live_checks.pop(pointer, None)
        cache.tick()
        settle(scene, ts, perf_counter())
    finally:
        project.paint_image = None
        project.paint_set_id = project.paint_layer_id = project.paint_target_channel = ''
        project.paint_canvases.clear()
        cache._paint_input.pop(pointer, None)
    if profiler:
        profiler.dump_stats(str(REPORT) + '.paint_middle.prof')
    warm = sorted(durations[2:] or durations)
    row = dict(median_ms=median(warm), p95_ms=warm[min(len(warm) - 1, int(len(warm) * .95))], ticks=ticks,
               durations=durations, footprint_mib=footprint_mib())
    print('DEEP_STACK_PAINT paint_middle', f"median {row['median_ms']:.1f} ms p95 {row['p95_ms']:.1f} ms",
          'ticks', ticks, 'footprint', row['footprint_mib'], 'MiB', flush=True)
    return row


def main():
    gpu.init()
    open_painter.register()
    if PHASES:
        time_phases()
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
            cold_profiler.dump_stats(str(REPORT) + '.cold.prof')
        cold['footprint_mib'] = footprint_mib()
        cold['images'] = image_breakdown(ts)
        print('DEEP_STACK_IMAGES', cold['images'], flush=True)
        print('DEEP_STACK_COLD', f'{cold_s:.2f}s', cold['passes'], cold['units'], cold['backend'],
              'footprint', cold['footprint_mib'], 'MiB', flush=True)
        if PHASES:
            # What a full garbage collection has to traverse.
            import gc
            from collections import Counter
            tracked = gc.get_objects()
            kinds = Counter(type(item).__qualname__ for item in tracked)
            print('DEEP_STACK_GC_OBJECTS', len(tracked), 'frozen', gc.get_freeze_count(), kinds.most_common(25),
                  flush=True)
            del tracked

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
                    tick(scene, ts)
                    # Pixel publication (Vulkan) spends a time budget per tick, so an edit can take
                    # several ticks to publish every channel; on Metal one tick must do it.
                    for _ in range(100):
                        if shader_preview.current(scene, ts) or not runtime.device().pixel_publish:
                            break
                        tick(scene, ts)
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
            tick(scene, ts)
            print('DEEP_STACK_CASE', name, f"median {row['median_ms']:.1f} ms p95 {row['p95_ms']:.1f} ms",
                  'passes', row['passes'], 'units', row['units_rendered'], 'footprint', row['footprint_mib'], 'MiB',
                  'engine', row['engine_memory_mib'], flush=True)
        structural = run_structural(scene, ts) if STRUCTURAL else {}
        paint = run_paint(scene, ts) if not ONLY or 'paint_middle' in ONLY else {}
        undo = run_undo(scene, ts) if not ONLY or ONLY & {'undo_numeric', 'undo_structural'} else {}
        data = dict(status='PASS', version=VERSION, blender=bpy.app.version_string,
                    backend=gpu.platform.backend_type_get(), resolution=RES, counts=counts,
                    applied=applied, authoring_seconds=authoring_s, cold=cold, cases=results,
                    structural=structural, paint=paint, undo=undo,
                    library=os.environ.get('OP_LIBOPENPAINTER_PATH'),
                    native_engine=os.environ.get('OP_NATIVE_ENGINE'))
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(data, indent=2) + '\n')
        print('DEEP_STACK_PASS', REPORT, flush=True)
    finally:
        open_painter.unregister()


main()
