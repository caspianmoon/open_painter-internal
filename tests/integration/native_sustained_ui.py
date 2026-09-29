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
bpy.context.preferences.view.show_splash = False
commands, draws, runs = [], [], []
accepted = {}
active = None
handler = observation = hook = None
latest = None
deadline = time.perf_counter() + RUNS * (DURATION + 15) + 90
original_evaluate = runtime.Engine.evaluate


def evaluated(self, *args, **kwargs):
    command = latest
    result = original_evaluate(self, *args, **kwargs)
    if result and command is not None and self.set_id == ts.id:
        revision = self._native_revisions.revision
        command.update(accepted=time.perf_counter(), revision=revision)
        accepted[revision] = command
    return result


def drawn():
    if active is None:
        return
    engine = shader_preview.session_engine(bpy.context.scene, ts)
    if engine is None or engine._native_revisions is None:
        return
    command = accepted.get(engine._native_revisions.revision)
    now = time.perf_counter()
    if command is not None:
        command.setdefault('drawn', now)
        draws.append(dict(run=len(runs), time=now, command=command['id'],
                          state_age_ms=1000 * (now - command['receipt']),
                          tracking_error=abs(control.opacity - command['value'])))


def percentile(values):
    return {f'p{p}': float(np.percentile(values, p)) for p in (50, 95, 99)} if values else {}


def finish(error=None):
    if handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
    if hook is not None:
        hook.stop()
    if observation is not None:
        observation.__exit__(None, None, None)
    report = dict(status='FAIL' if error else 'PASS', error=error, runs=runs,
                  commands=commands, draws=draws, blender=bpy.app.version_string,
                  build=bpy.app.build_hash.decode(), backend=gpu.platform.backend_type_get(),
                  renderer=gpu.platform.renderer_get(), vendor=gpu.platform.vendor_get(),
                  driver=gpu.platform.version_get(), viewport=[area.width, area.height],
                  render_engine=bpy.context.scene.render.engine, shading=area.spaces.active.shading.type,
                  execution_backend=shader_preview.session_engine(bpy.context.scene, ts).report.get('execution_backend'), resolution=2048,
                  measurement='Scripted RNA receipt to accepted revision and viewport POST_PIXEL draw; not OS input, GPU fence or scanout.',
                  instrumentation=observation.report() if observation else {},
                  note='No screenshots or readbacks inside timed runs. All commands and superseded states retained; missed timer slots are not delivered input events.')
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + '\n')
    print('NATIVE_SUSTAINED_' + report['status'], error or [r['summary'] for r in runs], flush=True)
    bpy.ops.wm.quit_blender()


def setup():
    global ts, control, area, handler, hook, warm_until
    open_painter.register()
    area = max((a for a in bpy.context.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(area=area, region=region):
        ts, _, ids, _ = fixture(bpy.context.scene, resolution=2048)
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
    hook = patch.object(runtime.Engine, 'evaluate', evaluated)
    hook.start()
    warm_until = time.perf_counter() + 8


def tick():
    global active, latest, observation, warm_until
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
            if now < warm_until or not shader_preview.current(bpy.context.scene, ts):
                return .1
            engine = shader_preview.session_engine(bpy.context.scene, ts)
            assert engine.report['execution_backend'] == 'c17-host-metal', engine.report
            if len(runs) == RUNS:
                observation.assert_no_raster()
                assert not observation.metrics.counts['engine_program_compiles'], observation.report()
                finish()
                return None
            # Captures happen between runs, outside latency measurements.
            bpy.ops.screen.screenshot(filepath=str(REPORT.with_name(f'{REPORT.stem}-{len(runs)}.png')))
            if observation is None:
                observation = RasterObservation().__enter__()
            active = dict(start=time.perf_counter(), first=len(commands), rebuilds=engine._native_graph.rebuilds,
                          memory_start=runtime.device().memory(), next_slot=0)
            return 1 / 60
        elapsed = now - active['start']
        if elapsed < DURATION:
            slot = int(elapsed * 60)
            if slot >= active['next_slot']:
                command = dict(id=len(commands), run=len(runs), receipt=now, slot=slot,
                               missed_slots=slot - active['next_slot'], value=.5 + .35 * math.sin(elapsed * 1.7))
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
        assert engine._native_graph.rebuilds == active['rebuilds'], 'Numeric edit rebuilt topology'
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
            receipt_to_draw_ms=percentile([1000*(c['drawn']-c['receipt']) for c in displayed]),
            displayed_state_age_ms=percentile([d['state_age_ms'] for d in observed]),
            tracking_error=percentile([d['tracking_error'] for d in observed])))
        runs.append(active)
        print('SUSTAINED_RUN', len(runs), active['summary'], flush=True)
        active = latest = None
        warm_until = now + 3
        return .1
    except Exception:
        finish(traceback.format_exc())
        return None


REPORT.parent.mkdir(parents=True, exist_ok=True)
bpy.app.timers.register(tick, first_interval=1)
