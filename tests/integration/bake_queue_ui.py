"""Exercise the registered bake queue through Blender's real modal event loop.

Launch an isolated GUI with --factory-startup --enable-event-simulate --python.
OP_BAKE_QUEUE_REPORT selects the JSON report. Utility maps are genuinely baked
at the authored 512 resolution; wrappers observe events and inject one failure.
No fabricated Event objects or direct calls to modal() drive this regression.
"""
import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.baking import bake_manager, jobs
from open_painter.baking.maps.utility import UTILITY_KINDS
from open_painter.core import image_manager, texture_set_manager
from open_painter.evaluation import cache

REPORT = Path(os.environ.get('OP_BAKE_QUEUE_REPORT', '/private/tmp/op-bake-queue-ui.json'))
record = dict(status='RUNNING', blender=bpy.app.version_string,
              build=bpy.app.build_hash.decode(), cases=[], errors=[])
state = dict(case=None, texture_set=None, window=None, area=None, foreign_timer=None,
             stage=0, ready_at=0., started=time.monotonic(), done=False)
original = {name: getattr(jobs.OP_OT_bake_queue, name) for name in ('invoke', 'modal', 'finish')}
real_steps, real_utility = bake_manager.bake_steps, bake_manager.bake_utility


def current_maps():
    return {entry.kind: entry.image for entry in state['texture_set'].baked_maps if entry.image}


def resource_state():
    return {name: {item.as_pointer() for item in getattr(bpy.data, name)}
            for name in ('objects', 'meshes', 'materials', 'images', 'scenes', 'collections')}


def author_state():
    scene = bpy.context.scene
    return (scene.render.engine, bpy.context.mode, bpy.context.view_layer.objects.active,
            frozenset(bpy.context.selected_objects),
            tuple((obj.as_pointer(), obj.data.as_pointer(), tuple(tuple(row) for row in obj.matrix_world),
                   tuple(slot.material for slot in obj.material_slots))
                  for obj in scene.objects if obj.type == 'MESH'))


def observed_steps(context, ts):
    case = state['case']
    try:
        for kind in real_steps(context, ts):
            case['boundaries'].append(kind)
            yield kind
            event = case.get('active_event', {})
            case['resumes'].append(dict(event))
    finally:
        case['generator_closed'] = True


def observed_utility(ts, kind):
    case = state['case']
    case['calls'].append(dict(kind=kind, event=case.get('active_event', {}).get('type')))
    if case['name'] == 'failure' and kind == 'WORLD_NORMAL':
        raise RuntimeError('Injected second-map queue failure')
    return real_utility(ts, kind)


def observed_invoke(self, context, event):
    result = original['invoke'](self, context, event)
    case = state['case']
    if result == {'RUNNING_MODAL'}:
        case['initial_duration'] = float(self._timer.time_duration)
        case['last_advanced_duration'] = case['initial_duration']
    return result


def observed_finish(self, context):
    try:
        return original['finish'](self, context)
    finally:
        case = state['case']
        case['finish_calls'] += 1
        case['cleanup_detached'] = self._timer is None and self._job is None


def observed_modal(self, context, event):
    case = state['case']
    try:
        assert isinstance(event, bpy.types.Event), 'Queue received a fabricated event'
        info = dict(type=event.type, native=True, has_timer=hasattr(event, 'timer'))
        if event.type == 'TIMER':
            info['own_duration'] = float(self._timer.time_duration)
            case['timer_events'] += 1
            case['events_without_timer'] += not info['has_timer']
            if info['own_duration'] == case['initial_duration']:
                case['early_foreign_events'] += 1
            if case['resumes'] and info['own_duration'] == case['last_advanced_duration']:
                case['repeated_duration_events'] += 1
        if len(case['event_samples']) < 16:
            case['event_samples'].append(info)
        case['active_event'] = info
        before = len(case['resumes'])
        result = original['modal'](self, context, event)
        if len(case['resumes']) != before:
            # Reporting an operator error can mutate the native Event RNA.
            # Assert against the event values captured before invoking modal.
            assert info['type'] == 'TIMER', 'A non-timer event advanced the bake queue'
            assert info['own_duration'] > case['last_advanced_duration'], (
                'An unrelated/queued TIMER advanced work before the queue timer advanced')
            assert len(case['resumes']) == before + 1, 'One event ran multiple map boundaries'
            case['last_advanced_duration'] = info['own_duration']
        if case['name'] == 'cancel' and not case['esc_sent'] and current_maps():
            assert set(current_maps()) == {'POSITION'}, 'Cancellation missed the first map boundary'
            case['esc_sent'] = True
            context.window.event_simulate(type='ESC', value='PRESS')
            context.window.event_simulate(type='ESC', value='RELEASE')
        if result in ({'FINISHED'}, {'CANCELLED'}):
            case['modal_done'] = True
            case['terminal_result'] = sorted(result)
        return result
    except Exception:
        # Capture the original AttributeError here so the failing revision exits
        # with a useful JSON report instead of leaving a broken modal handler.
        record['errors'].append(traceback.format_exc())
        try:
            original['finish'](self, context)
        except Exception:
            record['errors'].append(traceback.format_exc())
        case['modal_done'] = True
        return {'FINISHED'}
    finally:
        case.pop('active_event', None)


# Register these observers with the real class. All production behavior remains
# in the original methods; Blender supplies invoke/modal events and owns dispatch.
jobs.OP_OT_bake_queue.invoke = observed_invoke
jobs.OP_OT_bake_queue.modal = observed_modal
jobs.OP_OT_bake_queue.finish = observed_finish
bake_manager.bake_steps = observed_steps
bake_manager.bake_utility = observed_utility


def setup():
    assert not bpy.app.background, 'This regression requires a real Blender GUI'
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    bpy.context.preferences.view.show_splash = False
    state['window'] = bpy.context.window
    state['area'] = max((area for area in bpy.context.screen.areas if area.type == 'VIEW_3D'),
                        key=lambda area: area.width * area.height)
    ts = texture_set_manager.create(bpy.context.scene, 'Native bake queue regression', 512)
    texture_set_manager.assign(bpy.context.scene, ts, bpy.context.scene.objects['Cube'])
    ts.bake.selected_to_active = False
    ts.bake.normal = ts.bake.ao = ts.bake.curvature = False
    for kind in UTILITY_KINDS:
        setattr(ts.bake, kind.lower(), kind in {'POSITION', 'WORLD_NORMAL'})
    state['texture_set'] = ts
    state['ready_at'] = time.monotonic() + .4


def start_case(name):
    ts, window, area = state['texture_set'], state['window'], state['area']
    old = tuple(current_maps().values())
    ts.baked_maps.clear()
    for image in old:
        bpy.data.images.remove(image)
    case = dict(name=name, boundaries=[], calls=[], resumes=[], event_samples=[],
                timer_events=0, events_without_timer=0, early_foreign_events=0,
                repeated_duration_events=0, generator_closed=False, cleanup_detached=False,
                finish_calls=0, esc_sent=False, modal_done=False)
    state['case'] = case
    state['before_resources'] = resource_state()
    state['before_author'] = author_state()
    record['cases'].append(case)
    if name == 'success':
        # WM TIMER events do not identify their source on Blender 5.2. The
        # production queue must gate on its own timer, even when these fire first.
        state['foreign_timer'] = bpy.context.window_manager.event_timer_add(.01, window=window)
    region = next(region for region in area.regions if region.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        window.cursor_warp(region.x + region.width // 2, region.y + region.height // 2)
        result = bpy.ops.open_painter.bake_queue('INVOKE_DEFAULT')
    assert result == {'RUNNING_MODAL'}, result


def verify_case():
    case = state['case']
    ts = state['texture_set']
    expected = {'POSITION', 'WORLD_NORMAL'} if case['name'] == 'success' else {'POSITION'}
    assert set(current_maps()) == expected, (case['name'], sorted(current_maps()))
    assert case['timer_events'] and case['events_without_timer'] == case['timer_events'], (
        'The regression did not exercise native TIMER events without Event.timer')
    assert case['generator_closed'] and case['cleanup_detached'] and case['finish_calls'] == 1
    assert not any(op.bl_idname in {'open_painter.bake_queue', 'OPEN_PAINTER_OT_bake_queue'}
                   for op in state['window'].modal_operators), 'Completed modal handler remains installed'
    for entry in ts.baked_maps:
        assert entry.provenance, 'Completed map provenance was not recorded'
        pixels = image_manager.pixels(entry.image)
        assert pixels.shape == (512, 512, 4) and np.isfinite(pixels).all()
        assert np.any(pixels[..., 3] > 0) and float(pixels[..., :3].std()) > .05
    assert author_state() == state['before_author'], 'Queue changed artist scene/selection/material state'
    after = resource_state()
    for kind, before in state['before_resources'].items():
        if kind == 'images':
            assert after[kind] - before == {image.as_pointer() for image in current_maps().values()}
            assert not before - after[kind]
        else:
            assert after[kind] == before, 'Bake queue leaked temporary ' + kind
    case['status_message'] = bpy.context.scene.open_painter.status
    if case['name'] == 'success':
        assert [item['kind'] for item in case['calls']] == ['POSITION', 'WORLD_NORMAL']
        assert case['early_foreign_events'] > 0 and case['repeated_duration_events'] > 0, (
            'The fast unrelated timer did not exercise the queue timer gate')
        assert case['status_message'] == 'Mesh maps complete'
    elif case['name'] == 'cancel':
        assert case['esc_sent'] and [item['kind'] for item in case['calls']] == ['POSITION']
        assert 'stopped' in case['status_message']
    else:
        assert [item['kind'] for item in case['calls']] == ['POSITION', 'WORLD_NORMAL']
        assert 'Bake failed' in case['status_message'] and 'Injected second-map' in case['status_message']
    case['result'] = 'PASS'
    if state['foreign_timer'] is not None:
        bpy.context.window_manager.event_timer_remove(state['foreign_timer'])
        state['foreign_timer'] = None


def complete():
    state['done'] = True
    record['status'] = 'FAIL' if record['errors'] else 'PASS'
    record['elapsed_s'] = time.monotonic() - state['started']
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(record, indent=2) + '\n')
    print('BAKE_QUEUE_UI_' + record['status'], REPORT, flush=True)
    if state['foreign_timer'] is not None:
        bpy.context.window_manager.event_timer_remove(state['foreign_timer'])
        state['foreign_timer'] = None
    bake_manager.bake_steps, bake_manager.bake_utility = real_steps, real_utility
    for name, method in original.items():
        setattr(jobs.OP_OT_bake_queue, name, method)
    bpy.ops.wm.quit_blender()


def drive():
    try:
        if time.monotonic() - state['started'] > 60:
            raise TimeoutError('Native bake queue did not finish within 60 seconds')
        if record['errors']:
            complete()
            return None
        if state['texture_set'] is None:
            setup()
        elif state['case'] is None and time.monotonic() >= state['ready_at']:
            start_case(('success', 'cancel', 'failure')[state['stage']])
        elif state['case'] is not None and state['case']['modal_done']:
            verify_case()
            state['stage'] += 1
            state['case'] = None
            if state['stage'] == 3:
                complete()
                return None
            state['ready_at'] = time.monotonic() + .2
        return .02
    except Exception:
        record['errors'].append(traceback.format_exc())
        traceback.print_exc()
        complete()
        return None


bpy.app.timers.register(drive, first_interval=2.)
