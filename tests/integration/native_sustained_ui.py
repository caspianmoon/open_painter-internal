"""Five warmed 60-second RNA edit runs in a real rendered Metal viewport.

Timer receipt -> revision acceptance -> POST_PIXEL draw, not OS event delivery,
GPU completion or scanout. Every received, superseded and undrawn command is
retained. Independent native widget/capture proof lives in shader_preview_ui.py.
"""
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback
from unittest.mock import patch

import bpy
import gpu
from mathutils import Quaternion
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_preview import fixture, render_setup, open_painter, shader_preview, RasterObservation
from open_painter.evaluation.engine import runtime
from open_painter.constants import CHANNELS
from open_painter.core import layer_stack

REPORT = Path(os.environ['OP_SUSTAINED_REPORT'])
DURATION = float(os.environ.get('OP_SUSTAINED_SECONDS', '60'))
RUNS = int(os.environ.get('OP_SUSTAINED_RUNS', '5'))
EXPECTED = os.environ.get('OP_SUSTAINED_BACKEND', 'c17-host-' + gpu.platform.backend_type_get().lower())
# Frame breakdown: viewport draw spans (PRE_VIEW to POST_PIXEL), scheduler ticks and
# engine evaluations, so cadence can be attributed rather than only counted.
TRACE = os.environ.get('OP_SUSTAINED_TRACE') == '1'
RESOLUTION = int(os.environ.get('OP_SUSTAINED_RES', '2048'))
trace = dict(views=[], ticks=[], evaluations=[])
view_handler = None
assert DURATION > 0 and RUNS > 0
bpy.context.preferences.view.show_splash = False
commands, draws, runs = [], [], []
last_accepted = None
active = None
handler = observation = hook = None
latest = None
deadline = time.perf_counter() + RUNS * (DURATION + 15) + 90
original_evaluate = runtime.Engine.evaluate
warm_evaluations = 0
MIN_WARM_EVALUATIONS = 16


def evaluated(self, *args, **kwargs):
    global last_accepted, warm_evaluations, warm_until
    command = latest
    started = time.perf_counter()
    result = original_evaluate(self, *args, **kwargs)
    if TRACE and active is not None:
        trace['evaluations'].append((started, time.perf_counter(), len(result or ()), self.report.get('passes')))
    if result and active is None and self.set_id == ts.id:
        if warm_evaluations == 0:
            # Start warmup after initial resource/shader setup has completed;
            # a busy desktop can consume the old setup-relative wall interval.
            warm_until = time.perf_counter() + max(8, float(os.environ.get('OP_SUSTAINED_WARM_EDITS', '0')))
        warm_evaluations += 1
    if result and command is not None and self.set_id == ts.id:
        # Common observation boundary: successful Engine.evaluate return. A
        # legacy executor has no native revision object. Do not time only C.
        command.update(accepted=time.perf_counter(), evaluation_ms=1000 * (time.perf_counter() - started),
                       gpu_passes=self.report['passes'], published_channels=len(result),
                       native_revision=self._native_revisions.revision if self._native_revisions else None)
        last_accepted = command
    return result


def view_started():
    if TRACE and active is not None:
        trace['views'].append([time.perf_counter(), None])


def drawn():
    if TRACE and active is not None and trace['views'] and trace['views'][-1][1] is None:
        trace['views'][-1][1] = time.perf_counter()
    if active is None:
        return
    engine = shader_preview.session_engine(bpy.context.scene, ts)
    if engine is None:
        return
    command = last_accepted
    now = time.perf_counter()
    if command is not None:
        command.setdefault('drawn', now)
        draws.append(dict(run=len(runs), time=now, command=command['id'],
                          state_age_ms=1000 * (now - command['receipt']),
                          tracking_error=abs(control.opacity - command['value']),
                          ideal_tracking_error=abs(.5 + .35 * math.sin(min(DURATION, now-active['start']) * 1.7) - command['value'])))


def percentile(values):
    return {f'p{p}': float(np.percentile(values, p)) for p in (50, 95, 99)} if values else {}


def finish(error=None):
    if handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
    if view_handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(view_handler, 'WINDOW')
    if hook is not None:
        hook.stop()
    if observation is not None:
        observation.__exit__(None, None, None)
    parity = None
    if not error and os.environ.get('OP_SUSTAINED_PIXELS') == '1':
        # Full output comparison after timing, with an identical authored value.
        # Readback and compression never occur inside the measured interval.
        try:
            control.opacity = .37
            for _ in range(300):
                shader_preview.tick(bpy.context.scene, ts)
                if shader_preview.current(bpy.context.scene, ts):
                    break
            else:
                raise AssertionError('Parity endpoint never became current')
            values = {out.channel: np.array(gpu.texture.from_image(out.image).read(), dtype=np.float32)
                      for out in ts.outputs}
            assert set(values) == set(CHANNELS)
            assert np.ptp(values['BASE_COLOR']) > .05
            target = REPORT.with_suffix('.pixels.npz')
            np.savez_compressed(target, **values)
            parity = dict(opacity=.37, channels=list(values), pixels=str(target))
        except Exception:
            error = traceback.format_exc()
    report = dict(status='FAIL' if error else 'PASS', error=error, runs=runs,
                  parity=parity, expected_backend=EXPECTED,
                  warm_edit_seconds=float(os.environ.get('OP_SUSTAINED_WARM_EDITS', '0')),
                  minimum_warm_evaluations=MIN_WARM_EVALUATIONS,
                  commands=commands, draws=draws, blender=bpy.app.version_string,
                  build=bpy.app.build_hash.decode(), backend=gpu.platform.backend_type_get(),
                  renderer=gpu.platform.renderer_get(), vendor=gpu.platform.vendor_get(),
                  driver=gpu.platform.version_get(), viewport=[area.width, area.height],
                  render_engine=bpy.context.scene.render.engine, shading=area.spaces.active.shading.type,
                  execution_backend=shader_preview.session_engine(bpy.context.scene, ts).report.get('execution_backend'), resolution=RESOLUTION,
                  measurement='Scripted RNA receipt to accepted revision and viewport POST_PIXEL draw; not OS input, GPU fence or scanout.',
                  instrumentation=observation.report() if observation else {},
                  note='No screenshots or readbacks inside timed runs. All commands and superseded states retained; missed timer slots are not delivered input events.',
                  trace=summarize_trace() if TRACE else None)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + '\n')
    print('NATIVE_SUSTAINED_' + report['status'], error or [r['summary'] for r in runs], flush=True)
    bpy.ops.wm.quit_blender()


def summarize_trace():
    def spans(rows):
        return [1000 * (row[1] - row[0]) for row in rows if row[1] is not None]
    views = [row for row in trace['views'] if row[1] is not None]
    seconds = sum(run['summary']['duration_s'] for run in runs) or 1.0
    gaps = [1000 * (b[0] - a[1]) for a, b in zip(views, views[1:])]
    return dict(view_draws_per_s=len(views) / seconds, view_draw_ms=percentile(spans(views)),
                gap_between_draws_ms=percentile(gaps),
                ticks_per_s=len(trace['ticks']) / seconds, tick_ms=percentile(spans(trace['ticks'])),
                evaluations_per_s=len(trace['evaluations']) / seconds,
                evaluation_ms=percentile(spans(trace['evaluations'])),
                passes_per_evaluation=percentile([row[3] for row in trace['evaluations'] if row[3] is not None]),
                busy_share=dict(view=sum(spans(views)) / 1000 / seconds,
                                ticks=sum(spans(trace['ticks'])) / 1000 / seconds))


def setup():
    global ts, control, area, handler, hook, warm_until, view_handler
    open_painter.register()
    area = max((a for a in bpy.context.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(area=area, region=region):
        ts, _, ids, _ = fixture(bpy.context.scene, resolution=RESOLUTION)
        control = next(layer for layer in ts.layers if layer.id == ids['coat'])
        for layer in ts.layers:
            for channel in CHANNELS:
                layer_stack.channel_for(layer, channel).enabled = True
        render_setup(bpy.context.scene)
        bpy.context.scene.render.engine = 'BLENDER_EEVEE'
        space = area.spaces.active
        space.shading.type = 'RENDERED'
        space.overlay.show_overlays = False
        space.region_3d.view_perspective = 'ORTHO'
        space.region_3d.view_rotation = Quaternion((1, 0, 0, 0))
        space.region_3d.view_location = (0, 0, 0)
        space.region_3d.view_distance = 3
    handler = bpy.types.SpaceView3D.draw_handler_add(drawn, (), 'WINDOW', 'POST_PIXEL')
    if TRACE:
        view_handler = bpy.types.SpaceView3D.draw_handler_add(view_started, (), 'WINDOW', 'PRE_VIEW')
        from open_painter.evaluation import cache
        original_tick = cache._tick

        def traced_tick():
            started = time.perf_counter()
            try:
                return original_tick()
            finally:
                if active is not None:
                    trace['ticks'].append((started, time.perf_counter()))
        cache._tick = traced_tick
    hook = patch.object(runtime.Engine, 'evaluate', evaluated)
    hook.start()
    warm_until = time.perf_counter() + max(8, float(os.environ.get('OP_SUSTAINED_WARM_EDITS', '0')))


def tick():
    global active, latest, observation, warm_until, last_accepted, warm_evaluations
    try:
        now = time.perf_counter()
        assert now < deadline, 'Sustained qualification timed out'
        if 'ts' not in globals():
            setup()
            return .1
        diagnostic = shader_preview.diagnostics(bpy.context.scene, ts)
        assert not diagnostic.get('error'), diagnostic
        area.tag_redraw()
        if active is None:
            needs_edits = bool(os.environ.get('OP_SUSTAINED_WARM_EDITS')) and warm_evaluations < MIN_WARM_EVALUATIONS
            if needs_edits:
                warm_until = max(warm_until, now + 1)
                control.opacity = .5 + .35 * math.sin(now * 1.7)
            elif now < warm_until and os.environ.get('OP_SUSTAINED_WARM_EDITS'):
                # Exercise actual publication/reuse before measuring, then leave
                # one second at the same starting value for both executors.
                control.opacity = (.68 if warm_until - now < 1 else
                                   .5 + .35 * math.sin((warm_until-now) * 1.7))
            if now < warm_until or not shader_preview.current(bpy.context.scene, ts):
                return .1
            engine = shader_preview.session_engine(bpy.context.scene, ts)
            assert engine.report['execution_backend'] == EXPECTED, engine.report
            if len(runs) == RUNS:
                observation.assert_no_raster()
                assert not observation.metrics.counts['engine_program_compiles'], observation.report()
                assert not observation.metrics.counts['engine_graph_builds'], observation.report()
                finish()
                return None
            # Captures happen between runs, outside latency measurements.
            bpy.ops.screen.screenshot(filepath=str(REPORT.with_name(f'{REPORT.stem}-{len(runs)}.png')))
            if observation is None:
                observation = RasterObservation().__enter__()
            last_accepted = None
            active = dict(start=time.perf_counter(), first=len(commands), graph_identity=id(engine.graph),
                          memory_start=runtime.device().memory(), next_slot=0, warm_evaluations=warm_evaluations)
            return 1 / 60
        elapsed = now - active['start']
        if elapsed < DURATION:
            slot = int(elapsed * 60)
            if slot >= active['next_slot']:
                command = dict(id=len(commands), run=len(runs), receipt=now, slot=slot,
                               missed_slots=slot - active['next_slot'], value=.5 + .35 * math.sin(slot / 60 * 1.7))
                active['next_slot'] = slot + 1
                commands.append(command)
                latest = command
                control.opacity = command['value']
                command['callback_ms'] = 1000 * (time.perf_counter() - now)
            return 1 / 60
        # Drain the final edit and require a draw of its accepted revision.
        if latest.get('drawn') is None:
            assert elapsed < DURATION + 10, 'Final command never reached a viewport draw'
            return .02
        engine = shader_preview.session_engine(bpy.context.scene, ts)
        assert id(engine.graph) == active['graph_identity'], 'Numeric edit replaced topology'
        entries = commands[active['first']:]
        displayed = [c for c in entries if 'drawn' in c]
        observed = [d for d in draws if d['run'] == len(runs)]
        assert displayed and observed
        for command in entries:
            command['outcome'] = 'drawn' if 'drawn' in command else ('accepted_undrawn' if 'accepted' in command else 'superseded')
        active.update(end=now, memory_end=runtime.device().memory(), summary=dict(
            duration_s=elapsed, commands=len(entries), displayed_revisions=len(displayed),
            feedback_hz=len(displayed) / elapsed, superseded=sum(c['outcome']=='superseded' for c in entries),
            accepted_undrawn=sum(c['outcome']=='accepted_undrawn' for c in entries),
            missed_timer_slots=sum(c['missed_slots'] for c in entries),
            callback_ms=percentile([c['callback_ms'] for c in entries]),
            evaluation_ms=percentile([c['evaluation_ms'] for c in entries if 'evaluation_ms' in c]),
            gpu_passes_per_evaluation=percentile([c['gpu_passes'] for c in entries if 'gpu_passes' in c]),
            receipt_to_draw_ms=percentile([1000*(c['drawn']-c['receipt']) for c in displayed]),
            displayed_state_age_ms=percentile([d['state_age_ms'] for d in observed]),
            tracking_error=percentile([d['tracking_error'] for d in observed]),
            ideal_tracking_error=percentile([d['ideal_tracking_error'] for d in observed])))
        runs.append(active)
        print('SUSTAINED_RUN', len(runs), active['summary'], flush=True)
        if TRACE:
            print('SUSTAINED_TRACE', json.dumps(summarize_trace()), flush=True)
        active = latest = None
        warm_evaluations = 0
        warm_until = now + 3
        return .1
    except Exception:
        finish(traceback.format_exc())
        return None


REPORT.parent.mkdir(parents=True, exist_ok=True)
bpy.app.timers.register(tick, first_interval=1)
