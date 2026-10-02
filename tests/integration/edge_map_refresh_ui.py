"""Real-window regression: a crease-only Curvature map is rebaked as a cooperative
modal job when an edge-driven effect is added, with progress in the status line
and no long stall of the event loop.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/edge_map_refresh_ui.py
OP_EDGE_MAP_UI_REPORT selects the JSON report.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.baking import jobs
from open_painter.baking.bake_manager import register_map, refresh_stale_curvature, stale_curvature
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview

REPORT = Path(os.environ.get('OP_EDGE_MAP_UI_REPORT', '/private/tmp/op-edge-map-ui.json'))
state = {'started': time.monotonic(), 'phase': 'setup', 'modal_calls': [], 'statuses': [], 'errors': [],
         'longest_modal_ms': 0.0, 'timer_events': 0}

original_modal = jobs.OP_OT_edge_map_refresh.modal


def observed_modal(self, context, event):
    started = time.perf_counter()
    result = original_modal(self, context, event)
    elapsed = (time.perf_counter() - started) * 1000
    if event.type == 'TIMER':
        state['timer_events'] += 1
    state['longest_modal_ms'] = max(state['longest_modal_ms'], elapsed)
    status = context.scene.open_painter.status
    if not state['statuses'] or state['statuses'][-1] != status:
        state['statuses'].append(status)
    return result


jobs.OP_OT_edge_map_refresh.modal = observed_modal

# Each modal step runs one blocking Cycles bake of one reach (curvature_steps); how long
# that takes is the bake device's speed. Time the bakes so the stall check below can tell
# a step's own overhead from the bake it runs.
from open_painter.baking.maps import curvature as curvature_maps
original_reach = curvature_maps.bake_curvature_reach
state['reach_bake_ms'] = []


def timed_reach(*args, **kwargs):
    started = time.perf_counter()
    try:
        return original_reach(*args, **kwargs)
    finally:
        state['reach_bake_ms'].append(round((time.perf_counter() - started) * 1000, 1))


curvature_maps.bake_curvature_reach = timed_reach


def finish(status):
    report = dict(status=status, blender=bpy.app.version_string, elapsed_s=round(time.monotonic() - state['started'], 2),
                  **{k: v for k, v in state.items() if k != 'started'})
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + '\n')
    print('EDGE_MAP_REFRESH_UI_' + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()
    return None


def setup():
    assert not bpy.app.background, 'This regression requires a real Blender GUI'
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    scene = bpy.context.scene
    cube = scene.objects['Cube']
    # Hard edges register as curvature in the Cycles Bevel bake (wide rounded rims read
    # faint at every reach: edge_map_set.py). Simple subdivision keeps them hard while
    # making the bake take long enough to need many cooperative steps.
    dense = cube.modifiers.new('dense', 'SUBSURF')
    dense.subdivision_type = 'SIMPLE'
    dense.levels = dense.render_levels = 3
    ts = sets.create(scene, 'Edge map refresh regression', 1024)
    sets.assign(scene, ts, cube)
    n = 64
    for kind in ('AO', 'POSITION'):
        entry = ts.baked_maps.add()
        entry.kind = kind
        entry.image = images.create('Refresh ' + kind, n)
        images.write(entry.image, np.full((n, n, 4), .5, np.float32), pack=False)
    old = images.create(f'{ts.name}_BakedCurvature', n)
    old['open_painter_curvature_method'] = 'GEOMETRIC_CREASES_V1'
    images.write(old, np.zeros((n, n, 4), np.float32), pack=False)
    register_map(ts, 'CURVATURE', old)
    assert stale_curvature(ts) is not None
    state['texture_set_id'] = ts.id
    state['old_image'] = old.name
    state['old_image_pointer'] = old.as_pointer()
    state['phase'] = 'add'


def texture_set():
    scene = bpy.context.scene
    return next(ts for ts in scene.open_painter.texture_sets if ts.id == state['texture_set_id'])


def add_effect():
    ts = texture_set()
    area = max((a for a in bpy.context.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=bpy.context.window, area=area, region=region):
        started = time.perf_counter()
        result = refresh_stale_curvature(ts, {'CURVATURE'}, bpy.context)
        state['queue_ms'] = round((time.perf_counter() - started) * 1000, 1)
    assert result == 'QUEUED', result
    assert jobs.OP_OT_edge_map_refresh.running
    layer = stack.add(ts, 'FILL', 'Edge Wear')
    mask = layer.masks.add()
    mask.source, mask.generator = 'GENERATOR', 'EDGE_WEAR'
    state['phase'] = 'wait'


def poll():
    try:
        if state['phase'] == 'setup':
            setup()
            return .2
        if state['phase'] == 'add':
            add_effect()
            return .05
        if state['phase'] == 'wait':
            if jobs.OP_OT_edge_map_refresh.running:
                if time.monotonic() - state['started'] > 300:
                    raise AssertionError('Edge map rebake did not finish within 5 minutes')
                return .05
            ts = texture_set()
            prop = next(m for m in ts.baked_maps if m.kind == 'CURVATURE')
            assert prop.image is not None and prop.image.as_pointer() != state['old_image_pointer']
            from open_painter.baking.maps.curvature import METHOD
            assert prop.image.get('open_painter_curvature_method') == METHOD
            assert stale_curvature(ts) is None
            from open_painter.baking.maps.curvature import EDGE_MAP_KINDS, edge_map_set_complete
            assert edge_map_set_complete(ts), [m.kind for m in ts.baked_maps]
            state['reach_means'] = {kind: float(images.pixels(next(m.image for m in ts.baked_maps if m.kind == kind))[..., 0].mean())
                                    for kind in EDGE_MAP_KINDS}
            assert prop.provenance, 'rebaked map has no provenance'
            pixels = images.pixels(prop.image)
            assert pixels.shape == (1024, 1024, 4) and pixels[..., :3].max() > .5, 'edge map is empty'
            assert state['timer_events'] >= 3, state['timer_events']
            assert any('%' in text for text in state['statuses']), state['statuses']
            assert 'Edge maps rebaked' in state['statuses'][-1], state['statuses'][-1]
            # No step stalls the event loop beyond the one reach bake it runs: 1.5 s on top of
            # the longest bake (a factory-settings Blender bakes on the CPU, seconds per reach
            # on a laptop; a GPU bake keeps whole steps under a second).
            assert len(state['reach_bake_ms']) == 4, state['reach_bake_ms']
            assert state['longest_modal_ms'] < max(state['reach_bake_ms']) + 1500, state
            assert state['queue_ms'] < 3000, state['queue_ms']
            return finish('PASS')
    except Exception:
        state['errors'].append(traceback.format_exc())
        return finish('FAIL')


bpy.app.timers.register(poll, first_interval=.5)
