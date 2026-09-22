"""Cooperative scene-field analysis: debounced, budgeted, published atomically.

A LIVE scene field never analyses inside an evaluation. When its inputs change
(an influencer moved, the mesh edited, a parameter changed) the evaluator keeps
showing the last published raster and *requests* a job here; ``tick`` (called
from the scheduler timer) starts it once the inputs have been quiet for
``DEBOUNCE`` seconds, advances it in strips under a time budget, and publishes
the result — image swap plus provenance — before invalidating the set. A job
whose inputs changed underneath it never publishes. Background scripts and
tests without the timer keep the synchronous path (``scene_fields.evaluate``).
"""
from time import monotonic

import bpy

DEBOUNCE = .15
REBAKE_DEBOUNCE = 2.0
_requests = {}   # (scene pointer, set id, mask id) -> {'signature', 'since'}
_jobs = {}       # same key -> {'signature', 'steps', 'progress', 'name'}
_rebakes = {}    # (scene pointer, set id) -> time of the last mesh edit
paused = None    # predicate(scene) -> bool: analysis waits while it holds (freeze while painting)


def active():
    """The engine runs whenever the scheduler timer does (never in plain background scripts)."""
    from . import cache
    return bpy.app.timers.is_registered(cache.tick)


def key_for(scene, ts, mask):
    return (scene.as_pointer(), ts.id, mask.id)


def request(scene, ts, mask, signature):
    """Ask for an analysis of ``mask``'s field with these inputs; supersedes a running one."""
    key = key_for(scene, ts, mask)
    entry = _requests.get(key)
    if entry is None or entry['signature'] != signature:
        _requests[key] = {'signature': signature, 'since': monotonic()}
        job = _jobs.get(key)
        if job is not None and job['signature'] != signature:
            _jobs.pop(key, None)   # a superseded analysis is never published
        from . import cache
        cache.wake()


def discard(scene, ts, mask):
    """Drop any request or running job for a mask whose published raster is current again."""
    key = key_for(scene, ts, mask)
    _requests.pop(key, None)
    _jobs.pop(key, None)


def pending(scene):
    pointer = scene.as_pointer()
    return any(key[0] == pointer for key in _requests) or any(key[0] == pointer for key in _jobs) \
        or any(key[0] == pointer for key in _rebakes)


def progress(scene):
    """(name, fraction) of the running job of a scene, or None."""
    pointer = scene.as_pointer()
    for key, job in _jobs.items():
        if key[0] == pointer:
            return job['name'], job['progress']
    return None


def _resolve(scene, key):
    ts = next((item for item in scene.open_painter.texture_sets if item.id == key[1]), None)
    if ts is None:
        return None, None
    mask = next((m for layer in ts.layers for m in layer.masks if m.id == key[2]), None)
    return ts, mask


def _analysed(mask):
    """The masks whose field this engine owns: the membership of ``dependencies.live_fields``
    (LIVE scene masks and live-edge generators), which is what requests jobs."""
    return mask.source == 'SCENE' or (
        mask.source == 'GENERATOR' and getattr(mask, 'edge_source', 'BAKED') == 'LIVE')


def _editing(ts):
    return any(ref.object is not None and ref.object.mode == 'EDIT' for ref in ts.assignments)


def tick(scene, budget=.008):
    """Advance this scene's field work; returns True when something ran."""
    from ..generators import scene_fields
    if paused is not None and paused(scene):
        return False
    pointer = scene.as_pointer()
    worked = False
    now = monotonic()
    running = next((key for key in _jobs if key[0] == pointer), None)
    if running is None:
        for key, entry in list(_requests.items()):
            if key[0] != pointer or now - entry['since'] < DEBOUNCE:
                continue
            ts, mask = _resolve(scene, key)
            if ts is None or mask is None or not _analysed(mask):
                _requests.pop(key, None)
                continue
            if _editing(ts):
                continue
            field = mask.influence
            if repr(scene_fields.signature(ts, field)) != entry['signature']:
                _requests.pop(key, None)   # inputs moved on; the evaluator will re-request
                continue
            try:
                steps = scene_fields.compute_steps(ts, field)
                next(steps)   # the first strip reads evaluated geometry while the depsgraph is current
            except StopIteration as done:
                _finish(scene, key, entry['signature'], done.value)
                _requests.pop(key, None)
                return True
            except Exception as error:
                field.error = str(error)
                _requests.pop(key, None)
                return True
            _jobs[key] = {'signature': entry['signature'], 'steps': steps, 'progress': 0.0, 'name': mask.name}
            _requests.pop(key, None)
            running = key
            worked = True
            break
    if running is not None:
        job = _jobs[running]
        deadline = monotonic() + budget
        ts, mask = _resolve(scene, running)
        if ts is None or mask is None:
            _jobs.pop(running, None)
            return True
        while True:
            try:
                job['progress'] = float(next(job['steps']))
            except StopIteration as done:
                _jobs.pop(running, None)
                _finish(scene, running, job['signature'], done.value)
                break
            except Exception as error:
                _jobs.pop(running, None)
                mask.influence.error = str(error)
                break
            if monotonic() >= deadline:
                break
        worked = True
    worked |= _tick_rebakes(scene, now)
    return worked


def _finish(scene, key, signature, value):
    from ..core.layer_stack import invalidate
    from ..generators import scene_fields
    from .shader_preview import notify
    ts, mask = _resolve(scene, key)
    if ts is None or mask is None or not _analysed(mask):
        return
    field = mask.influence
    if repr(scene_fields.signature(ts, field)) != signature:
        return   # changed underneath the job: the next evaluation re-requests
    scene_fields.publish(ts, mask, value, signature)
    invalidate(ts)
    notify(field)


def flush(scene):
    """Run every pending request and job of a scene to completion now."""
    from ..generators import scene_fields
    pointer = scene.as_pointer()
    for key, job in list(_jobs.items()):
        if key[0] != pointer:
            continue
        _jobs.pop(key, None)
        try:
            while True:
                next(job['steps'])
        except StopIteration as done:
            _finish(scene, key, job['signature'], done.value)
        except Exception as error:
            ts, mask = _resolve(scene, key)
            if mask is not None:
                mask.influence.error = str(error)
    for key, entry in list(_requests.items()):
        if key[0] != pointer:
            continue
        _requests.pop(key, None)
        ts, mask = _resolve(scene, key)
        if ts is None or mask is None or not _analysed(mask):
            continue
        try:
            value = scene_fields.compute(ts, mask.influence)
        except Exception as error:
            mask.influence.error = str(error)
            continue
        _finish(scene, key, entry['signature'], value)


def cancel_all():
    _requests.clear()
    _jobs.clear()
    _rebakes.clear()


def discard_scene(scene):
    """Cancel only disposable scene work before its RNA is removed."""
    pointer = scene.as_pointer()
    for table in (_requests, _jobs, _rebakes):
        for key in tuple(table):
            if key[0] == pointer:
                table.pop(key, None)


def cancel_rebakes():
    """Drop scheduled edge rebakes only (a render is about to own the scene)."""
    _rebakes.clear()


# ---- automatic edge map rebake -------------------------------------------------

def schedule_rebake(scene, ts):
    _rebakes[(scene.as_pointer(), ts.id)] = monotonic()
    from . import cache
    cache.wake()


def _tick_rebakes(scene, now):
    pointer = scene.as_pointer()
    for key, since in list(_rebakes.items()):
        if key[0] != pointer or now - since < REBAKE_DEBOUNCE:
            continue
        ts = next((item for item in scene.open_painter.texture_sets if item.id == key[1]), None)
        if ts is None:
            _rebakes.pop(key, None)
            continue
        if _editing(ts):
            continue
        from ..baking.jobs import OP_OT_edge_map_refresh
        if OP_OT_edge_map_refresh.running:
            continue
        _rebakes.pop(key, None)
        window = next((w for w in bpy.context.window_manager.windows if w.scene == scene), None)
        if window is None:
            continue
        try:
            with bpy.context.temp_override(window=window, scene=scene):
                bpy.ops.open_painter.edge_map_refresh('INVOKE_DEFAULT', set_id=ts.id)
        except Exception as error:
            from ..utils.logging import logger
            logger.error('Auto edge rebake: %s', error)
        return True
    return False
