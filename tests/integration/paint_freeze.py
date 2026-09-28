"""Freeze while painting (0.9.0): a stroke defers boundary work; release catches up once.

Emulates native brush strokes in background mode (the real brush operator
cannot run here): pixels are written to the canvas, the modal check is
patched and the scheduler tick runs as it would at 60 Hz.
"""
import json
from pathlib import Path
import sys
from time import monotonic
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
# Exercise the retained CPU-boundary fallback; GPU filters have dedicated parity coverage.
from unittest.mock import patch
patch('open_painter.evaluation.engine.filters.supported', return_value=False).start()
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, field_jobs, layer_cache, metrics, shader_preview
from open_painter.painting import paint_session
from open_painter.painting.channel_paint import target as paint_target
from open_painter.utils.ids import new_id


def ready(scene, ts):
    deadline = monotonic() + 60
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def layer_by_id(ts, identifier):
    return next(layer for layer in ts.layers if layer.id == identifier)


def counters(observed):
    return observed.report()['counters']


def paint_block(image, step):
    n = image.size[0]
    data = images.pixels(image)
    y, x = (step * 7) % (n - 40), (step * 11) % (n - 40)
    data[y:y + 40, x:x + 40, :3] = (step % 5) / 5 + .1
    image.pixels.foreach_set(data.ravel())
    image.update()


def run():
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    scene = bpy.context.scene
    project = scene.open_painter
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    obj = bpy.context.object
    report = {}

    # Set A: a paint layer whose painted roughness feeds a spatial blur (a CPU boundary).
    a = sets.create(scene, 'Freeze blur', 512)
    sets.assign(scene, a, obj)
    a_paint = stack.add(a, 'PAINT', 'Painted roughness')
    a_paint_id = a_paint.id
    a_image = paint_target(a, layer_by_id(a, a_paint_id), 'ROUGHNESS')
    blur = stack.channel_for(layer_by_id(a, a_paint_id), 'ROUGHNESS').effects.add()
    blur.kind, blur.radius = 'GAUSSIAN_BLUR', 2.
    # Set B: a fill whose painted mask is followed by a blur filter mask, plus a
    # second painted+blurred layer for the multi-set session.
    obj.data.materials.append(None)
    obj.active_material_index = 1
    b = sets.create(scene, 'Freeze mask', 512)
    sets.assign(scene, b, obj)
    b_fill = stack.add(b, 'FILL', 'Masked fill')
    b_fill_id = b_fill.id
    stack.channel_for(b_fill, 'BASE_COLOR').color = (.2, .7, .3, 1.)
    painted = b_fill.masks.add()
    painted.id, painted.source = new_id(), 'PAINT'
    painted.image = images.create('Freeze mask paint', 512)
    painted_id = painted.id
    filtered = layer_by_id(b, b_fill_id).masks.add()
    filtered.id, filtered.source = new_id(), 'FILTER'
    filtered.effect.kind, filtered.effect.radius = 'GAUSSIAN_BLUR', 2.
    b_paint = stack.add(b, 'PAINT', 'B painted')
    b_paint_id = b_paint.id
    b_image = paint_target(b, layer_by_id(b, b_paint_id), 'ROUGHNESS')
    b_blur = stack.channel_for(layer_by_id(b, b_paint_id), 'ROUGHNESS').effects.add()
    b_blur.kind, b_blur.radius = 'GAUSSIAN_BLUR', 2.
    for ts in (a, b):
        material_manager.repair(ts)
        ready(scene, ts)
        shader_preview.flush(scene, ts)
    assert project.freeze_while_painting, 'the gate is on by default'
    pointer = scene.as_pointer()

    def session(ts, layer_id, image, channel='ROUGHNESS', mask_id=''):
        project.paint_image = image
        project.paint_set_id, project.paint_layer_id = ts.id, layer_id
        project.paint_channel = channel
        project.paint_target_channel = channel
        project.paint_mask = bool(mask_id)
        project.paint_mask_id = mask_id
        project.paint_canvases.clear()
        paint_session.reset()
        paint_session.detect(scene, force=True)

    def end_session():
        project.paint_image = None
        project.paint_set_id = project.paint_layer_id = project.paint_mask_id = project.paint_target_channel = ''
        project.paint_mask = False
        project.paint_canvases.clear()
        cache._paint_input.pop(pointer, None)

    def stroke(image_list, inputs):
        """Emulated brush inputs while the brush operator is modal."""
        intervals = []
        with patch.object(cache, '_modal_painting', lambda s: True):
            for step in range(inputs):
                for image in image_list:
                    paint_block(image, step)
                cache._live_checks.pop(pointer, None)
                intervals.append(cache.tick())
        return intervals

    def release():
        cache._paint_input[pointer] = (True, monotonic() - .2)
        cache._live_checks.pop(pointer, None)
        cache.tick()

    def settle(ts_list):
        for ts in ts_list:
            ready(scene, ts)

    # ---- gate on: nothing regenerates during the stroke -------------------
    session(a, a_paint_id, a_image)
    boundary_before = {n.image.name: images.pixels(n.image).sum() for u in shader_preview.ensure(scene, a).units.values()
                       for n in u.tree.nodes if n.get('op_boundary') and n.image}
    builds = shader_preview.diagnostics(scene, a)['graph_builds']
    with metrics.Metrics() as observed:
        intervals = stroke([a_image], 8)
        during = shader_preview.diagnostics(scene, a)
        assert during['frozen'] and during['deferred_owners'] == 1, during
        assert shader_preview.status(scene, a).startswith('Frozen'), shader_preview.status(scene, a)
        assert cache.painting_active(scene)
        assert field_jobs.paused is cache.painting_active and layer_cache.paused is cache.painting_active
        field_jobs._requests[(pointer, a.id, 'fake')] = {'signature': 'x', 'since': monotonic() - 5}
        assert field_jobs.tick(scene) is False, 'scene-field analysis waits for the stroke'
        field_jobs._requests.clear()
    stroke_counters = counters(observed)
    assert stroke_counters.get('shader_resource_requests', 0) == 0, stroke_counters
    assert stroke_counters.get('shader_topology_invalidations', 0) == 0, stroke_counters
    assert stroke_counters.get('shader_paint_deferrals', 0) == 8, stroke_counters
    assert shader_preview.diagnostics(scene, a)['graph_builds'] == builds
    assert all(abs(i - cache.ACTIVE_INTERVAL) < 1e-9 for i in intervals), intervals
    with metrics.Metrics() as observed:
        release()
        assert pointer not in cache._paint_input
        assert shader_preview.diagnostics(scene, a)['deferred_owners'] == 0
        settle([a])
    release_counters = counters(observed)
    assert release_counters.get('shader_paint_resumes', 0) == 1, release_counters
    assert release_counters.get('shader_resource_requests', 0) == 1, release_counters
    boundary_after = {n.image.name: images.pixels(n.image).sum() for u in shader_preview.ensure(scene, a).units.values()
                      for n in u.tree.nodes if n.get('op_boundary') and n.image}
    assert boundary_after != boundary_before, 'the blur boundary must be regenerated after the stroke'
    report['stroke_requests_gate_on'] = release_counters.get('shader_resource_requests', 0)
    print('PAINT_FREEZE_CHANNEL_OK', flush=True)

    # A painted mask behind a filter mask: the mask-prefix boundary waits too.
    end_session()
    b_fill_layer = layer_by_id(b, b_fill_id)
    b_fill_layer.active_mask = 0
    session(b, b_fill_id, layer_by_id(b, b_fill_id).masks[0].image, mask_id=painted_id)
    with metrics.Metrics() as observed:
        stroke([layer_by_id(b, b_fill_id).masks[0].image], 5)
    assert counters(observed).get('shader_resource_requests', 0) == 0, counters(observed)
    with metrics.Metrics() as observed:
        release()
        settle([b])
    assert counters(observed).get('shader_resource_requests', 0) == 1, counters(observed)
    print('PAINT_FREEZE_MASK_OK', flush=True)

    # ---- gate off: legacy behaviour, one regeneration per input -------------
    end_session()
    session(a, a_paint_id, a_image)
    project.freeze_while_painting = False
    with metrics.Metrics() as observed:
        stroke([a_image], 4)
    off = counters(observed)
    assert off.get('shader_resource_requests', 0) >= 4, off
    assert off.get('shader_paint_deferrals', 0) == 0, off
    release()
    settle([a])
    report['stroke_requests_gate_off'] = off.get('shader_resource_requests', 0)
    project.freeze_while_painting = True
    # Turning the gate off mid-stroke applies the deferred work immediately.
    with metrics.Metrics() as observed:
        with patch.object(cache, '_modal_painting', lambda s: True):
            paint_block(a_image, 1)
            cache._live_checks.pop(pointer, None)
            cache.tick()
            assert shader_preview.diagnostics(scene, a)['deferred_owners'] == 1
            project.freeze_while_painting = False
            assert shader_preview.diagnostics(scene, a)['deferred_owners'] == 0
    assert counters(observed).get('shader_paint_resumes', 0) == 1
    project.freeze_while_painting = True
    release()
    settle([a])
    print('PAINT_FREEZE_TOGGLE_OK', flush=True)

    # ---- multi-set session: every member catches up once ---------------------
    end_session()
    session(a, a_paint_id, a_image)
    for ts, layer_id, image in ((a, a_paint_id, a_image), (b, b_paint_id, b_image)):
        entry = project.paint_canvases.add()
        entry.set_id, entry.layer_id, entry.image = ts.id, layer_id, image
    with metrics.Metrics() as observed:
        with patch.object(cache, '_modal_painting', lambda s: True):
            for step in range(4):
                paint_block(a_image, step)
                paint_block(b_image, step)
                b_image.update()
                cache._live_checks.pop(pointer, None)
                cache.tick()
    assert counters(observed).get('shader_resource_requests', 0) == 0, counters(observed)
    assert shader_preview.diagnostics(scene, b)['deferred_owners'] >= 1, 'the second canvas defers too'
    with metrics.Metrics() as observed:
        release()
        settle([a, b])
    assert counters(observed).get('shader_resource_requests', 0) == 2, counters(observed)
    assert counters(observed).get('shader_paint_resumes', 0) == 2, counters(observed)
    print('PAINT_FREEZE_MULTI_OK', flush=True)

    # ---- a gated set without deferred owners waits, never spins (0.9.7) ------
    # Set A has a boundary queued when a stroke starts on set B: the gate holds
    # A without freezing it, so the tick must keep the painting rate instead of
    # returning the 1 kHz stepping interval for a queue nothing steps.
    end_session()
    settle([a, b])
    blur.radius = 3.
    assert shader_preview.diagnostics(scene, a)['pending_resources'], shader_preview.diagnostics(scene, a)
    steps_before = shader_preview.diagnostics(scene, a)['resource_steps']
    session(b, b_paint_id, b_image)
    intervals = stroke([b_image], 6)
    assert all(abs(i - cache.ACTIVE_INTERVAL) < 1e-9 for i in intervals), intervals
    info = shader_preview.diagnostics(scene, a)
    assert info['pending_resources'] and not info['frozen'] and info['resource_steps'] == steps_before, info
    release()
    settle([a, b])
    # A failed boundary keeps its siblings queued; the scheduler idles instead of spinning.
    end_session()
    blur.radius = 4.   # a radius the raster cache has not seen: a real queue
    assert shader_preview.diagnostics(scene, a)['pending_resources']
    shader_preview._sessions[(pointer, a.id)]['error'] = 'boundary failed'
    assert abs(cache.tick() - cache.IDLE_INTERVAL) < 1e-9
    shader_preview.retry(scene, a)
    settle([a])
    print('PAINT_FREEZE_GATE_RATE_OK', flush=True)

    # ---- save, undo, backend switch and finish during a stroke ----------------
    end_session()
    session(a, a_paint_id, a_image)
    with metrics.Metrics() as observed:
        with patch.object(cache, '_modal_painting', lambda s: True):
            paint_block(a_image, 3)
            cache._live_checks.pop(pointer, None)
            cache.tick()
            assert shader_preview.diagnostics(scene, a)['deferred_owners'] == 1
            cache.on_save(None)
            assert shader_preview.diagnostics(scene, a)['deferred_owners'] == 0
            assert shader_preview.current(scene, a), shader_preview.diagnostics(scene, a)
    assert counters(observed).get('shader_resource_requests', 0) == 1, counters(observed)
    cache._paint_input.pop(pointer, None)

    with patch.object(cache, '_modal_painting', lambda s: True):
        paint_block(a_image, 4)
        cache._live_checks.pop(pointer, None)
        cache.tick()
        assert shader_preview._deferred, 'a deferred owner exists before undo'
        cache.on_load(None)
        assert not shader_preview._deferred and not cache._paint_input
    material_manager.repair(a)
    ready(scene, a)
    material_manager.repair(b)
    ready(scene, b)

    session(a, a_paint_id, a_image)
    with patch.object(cache, '_modal_painting', lambda s: True):
        paint_block(a_image, 5)
        cache._live_checks.pop(pointer, None)
        cache.tick()
        assert shader_preview.diagnostics(scene, a)['deferred_owners'] == 1
        a.preview_backend = 'CPU'
        assert not shader_preview._deferred.get((pointer, a.id)), 'switching to CPU drops the deferred update'
        a.preview_backend = 'LIVE'
    cache._paint_input.pop(pointer, None)
    ready(scene, a)

    session(a, a_paint_id, a_image)
    with metrics.Metrics() as observed:
        with patch.object(cache, '_modal_painting', lambda s: True):
            paint_block(a_image, 6)
            cache._live_checks.pop(pointer, None)
            cache.tick()
            assert shader_preview.diagnostics(scene, a)['deferred_owners'] == 1
        paint_session.finish(bpy.context)
        assert project.paint_image is None and not shader_preview._deferred
        settle([a])
    assert counters(observed).get('shader_paint_resumes', 0) == 1, counters(observed)
    print('PAINT_FREEZE_LIFECYCLE_OK', flush=True)

    # ---- CPU reference backend keeps its own stroke path ---------------------
    b.preview_backend = 'CPU'
    session(b, b_paint_id, b_image)
    with metrics.Metrics() as observed:
        with patch.object(cache, '_modal_painting', lambda s: True):
            paint_block(b_image, 7)
            cache._live_checks.pop(pointer, None)
            cache.tick()
    assert not shader_preview.native_paint_input(scene), 'CPU sets do not take the native shader path'
    assert pointer in paint_session._rasters, 'the CPU path scans the canvas during the stroke'
    assert counters(observed).get('shader_paint_deferrals', 0) == 0
    end_session()
    b.preview_backend = 'LIVE'
    ready(scene, b)
    print('PAINT_FREEZE_PASS', flush=True)
    (ROOT / '.blender-test' / 'paint-freeze.json').write_text(json.dumps(report, indent=2))
    open_painter.unregister()


run()
