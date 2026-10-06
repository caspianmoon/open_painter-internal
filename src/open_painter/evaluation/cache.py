"""Authoring lifecycle: native shaders and isolated inputs, or explicit CPU mode.

The legacy debounce below belongs to CPU texture evaluation. Native parameter
edits are bound synchronously and never enter that full-stack image scheduler.
"""

import json
from time import monotonic

import bpy
from bpy.app.handlers import persistent

from ..constants import OWNER
from .resource_keys import key_for
from . import dependencies, field_jobs, layer_cache
from ..utils.logging import logger

_pending = {}
_paint_input = {}
_evaluators = {}
_jobs = {}
_live_checks = {}
_regions = {}
_states = {}
_progress = {}   # scene pointer -> the job progress text this scheduler wrote last
_faults = {}     # scene pointer -> the scheduler fault text ``tick`` wrote last
_fault_seen = set()   # fault messages whose traceback this run of failures logged
_fault_streak = 0     # consecutive timer ticks that raised
_tick_scene = None    # the scene ``_tick`` is servicing (None outside the scene loop)
_scene_cursor = None
_set_cursors = {}
_last_prune = (frozenset(), 0.0)
IDLE_INTERVAL = 0.1
ACTIVE_INTERVAL = 0.016
FAULT_INTERVAL = 1.0   # the rate a persistently failing tick retries at
FAULT_BACKOFF = 5      # consecutive failures before the timer slows to FAULT_INTERVAL
_wake_pending = False
_ticking = False


def wake():
    """Run the scheduler on the next event loop pass (a structural edit is pending).

    Only re-arms a registered timer: tests and explicit CPU workflows that
    unregistered it must not have it revived by a property callback.
    """
    global _wake_pending
    if bpy.app.background or not bpy.app.timers.is_registered(tick):
        return
    # A stream of edits must not keep removing an already-due callback from
    # Blender's timer queue. One wake covers every edit until the tick runs.
    if _wake_pending:
        return
    _wake_pending = True
    if _ticking:
        return
    bpy.app.timers.unregister(tick)
    bpy.app.timers.register(tick, first_interval=0.0, persistent=True)


def _prune(live, now):
    """Drop runtime state of deleted scenes/sets; idle ticks skip the scan."""
    global _last_prune
    from .raster_cache import prune
    from . import preview_policy, live_preview, shader_preview
    if _last_prune[0] == live and now - _last_prune[1] < 1.0:
        return
    _last_prune = (live, now)
    prune(live)
    preview_policy.prune(live)
    live_preview.prune(live)
    shader_preview.prune(live)
    dependencies.prune(live)
    live_scenes = {scene.as_pointer() for scene in bpy.data.scenes}
    layer_cache.prune(live_scenes)
    from . import profile
    profile.prune(live)
    for table in (_paint_input, _live_checks, _set_cursors, _progress, _faults):
        for key in set(table) - live_scenes:
            table.pop(key, None)
    for identifier in (set(_evaluators) | set(_pending) | set(_jobs) | set(_regions) | set(_states)) - live:
        discard(identifier)
        _pending.pop(identifier, None)
        _regions.pop(identifier, None)
        _states.pop(identifier, None)


def _set_status(scene, text):
    # RNA writes tag the UI; only write when the text actually changes.
    if scene.open_painter.status != text:
        scene.open_painter.status = text


def _set_progress(scene, text):
    _progress[scene.as_pointer()] = text
    _set_status(scene, text)


def _end_progress(scene):
    """A finished job's last progress line must not linger; the shader status
    takes the line back. Anything else written since (a failure) stays."""
    text = _progress.pop(scene.as_pointer(), None)
    if text is not None and scene.open_painter.status == text:
        from . import shader_preview
        _set_status(scene, '')
        shader_preview.restate(scene)



def job_state(scene, ts):
    from . import shader_preview
    if shader_preview.requested(ts):
        info = shader_preview.diagnostics(scene, ts, details=False)
        return dict(state='SHADER' if info['state'] == 'SHADER' else
                    'FAILED' if info['error'] else 'PREPARING_INPUT',
                    serial=ts.edit_serial, error=info['error'])
    key = key_for(scene, ts)
    state = _states.get(key)
    if state and state['serial'] == ts.edit_serial:
        return dict(state)
    return dict(state='QUEUED' if ts.dirty else 'READY', serial=ts.edit_serial, error='')


def set_state(scene, ts, state, error=''):
    key = key_for(scene, ts)
    entry = _states.get(key)
    if entry is None or entry['state'] != state or entry['serial'] != ts.edit_serial or entry['error'] != error:
        _states[key] = dict(state=state, serial=ts.edit_serial, error=error)



def failure_message(scene):
    return ' | '.join(f"{ts.name}: {state['error']}" for ts in scene.open_painter.texture_sets
                      if (state := job_state(scene, ts))['state'] == 'FAILED')


def retry(scene, ts):
    from . import live_preview, shader_preview
    if shader_preview.requested(ts):
        shader_preview.retry(scene, ts)
    else:
        live_preview.retry(scene, ts)
    _states.pop(key_for(scene, ts), None)
    _pending.pop(key_for(scene, ts), None)
    ts.dirty = True


def discard(key):
    entry = _jobs.pop(key, None)
    if entry:
        entry[1].close()
    _evaluators.pop(key, None)


def discard_evaluators():
    """Drop the work that reads set structs by address: ``texture_sets.add()``/
    ``remove()`` relocated them (0.9.7).

    Only the CPU evaluator jobs (generators over the resolved set) and a
    running field analysis (its strips read the influence through the RNA
    reference the job was started with) dereference the old addresses. The
    analysis goes back to the request queue, where its key resolves by id on
    the next start; queued cache refreshes, field requests and scheduled edge
    rebakes are keyed by id and resolve when they start, so they stay put:
    nothing re-issues them, and a full cancel here left a stale layer bound
    to its old image on every set add or remove.
    """
    for key in list(_jobs):
        discard(key)
    _evaluators.clear()
    for key, job in list(field_jobs._jobs.items()):
        field_jobs._jobs.pop(key, None)
        field_jobs._requests.setdefault(key, {'signature': job['signature'], 'since': monotonic()})


def cancel_all():
    """Every job and request of every scene: a file load or unregister."""
    discard_evaluators()
    field_jobs.cancel_all()
    layer_cache.cancel_all()


def paint_input(scene, pressed):
    _paint_input[scene.as_pointer()] = (pressed, monotonic())


def _modal_painting(scene):
    """Blender's brush operator is modal in a window showing this scene (a stroke is in progress)."""
    windows = [w for w in bpy.context.window_manager.windows if w.scene == scene]
    return any(op.bl_idname == "PAINT_OT_image_paint" for w in windows for op in w.modal_operators)


_NAVIGATION_OPERATORS = frozenset({
    'VIEW3D_OT_rotate', 'VIEW3D_OT_move', 'VIEW3D_OT_zoom', 'VIEW3D_OT_dolly',
    'VIEW3D_OT_view_roll', 'VIEW3D_OT_walk', 'VIEW3D_OT_fly',
    'IMAGE_OT_view_pan', 'IMAGE_OT_view_zoom',
})


def _modal_navigation(windows):
    """Native orbit/pan/zoom owns input in a window showing this scene."""
    return any(op.bl_idname in _NAVIGATION_OPERATORS for w in windows for op in w.modal_operators)


def painting_active(scene):
    """Freeze-while-painting gate (0.9.0): a brush is down, or was released less
    than the settle window ago, in a paint session of a project that freezes.

    Membership in ``_paint_input`` (not its pressed flag) defines the release
    window: the post-release branch of ``tick`` pops it when the stroke counts
    as finished.
    """
    project = getattr(scene, 'open_painter', None)
    if project is None or not project.paint_image or not project.freeze_while_painting:
        return False
    return scene.as_pointer() in _paint_input or _modal_painting(scene)


def stroke_finished(scene):
    """The single "stroke ended" point: apply deferred shader updates and resume paused work."""
    from . import shader_preview
    _paint_input.pop(scene.as_pointer(), None)
    shader_preview.resume(scene)
    if scene.open_painter.paint_correction:
        from ..baking import corrections
        corrections.stroke_finished(scene)
    wake()


def _rebake_pending(scene):
    """Run the live region rebake a correction stroke asked for (needs a window context)."""
    from ..baking import corrections
    ts = corrections.pending_rebake(scene)
    if ts is None or _modal_painting(scene):
        return False
    corrections.consume_pending(scene)
    window = next((w for w in bpy.context.window_manager.windows if w.scene == scene), None)
    if window is None:
        return False
    area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'), None)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
    try:
        # Not the operator: it rebakes the set selected in the dock, and the
        # session survives selecting another set to inspect it. The set that
        # owns the painted canvas is the one whose region changed.
        with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region, scene=scene):
            result = corrections.rebake_region(bpy.context, ts)
        faces = sum(max(v, 0) for v in result.values())
        full = [k for k, v in result.items() if v < 0]
        scene.open_painter.status = (f'Rebaked {faces} face(s)' + (f'; full bake of {", ".join(full)}' if full else '')
                                     if result else 'No Normal or AO map selected to rebake')
        logger.info('Live region rebake: %s', scene.open_painter.status)
    except Exception as exc:
        logger.error('Live region rebake: %s', exc)
        scene.open_painter.status = f'Region rebake failed: {exc}'
    return True


def tick():
    """The scheduler timer callback: ``_tick`` kept alive whatever it raises.

    Blender unregisters a timer whose function raises, and ``wake`` refuses to
    revive one that is not registered, so a single failing dependency refresh
    (a mesh mask signature, a decal image, a depsgraph that is not available)
    used to stop every live update until Blender restarted. When the timer
    owns the call the failure is reported instead (the traceback once per
    distinct message, the project status line every time) and the next tick
    retries; a run of failures backs off to a slow rate so a persistent fault
    neither spins the log nor costs the UI its idle rate. A script that drives
    the scheduler by hand (tests, the unregistered CPU workflow) still sees the
    raise: there is no timer to lose, and the caller wants the crash.
    """
    global _fault_streak, _wake_pending, _ticking
    _wake_pending = False
    _ticking = True
    try:
        interval = _tick()
    except Exception as exc:
        _wake_pending = False
        if not bpy.app.timers.is_registered(tick):
            raise
        _fault_streak += 1
        _report_fault(exc)
        return FAULT_INTERVAL if _fault_streak >= FAULT_BACKOFF else IDLE_INTERVAL
    finally:
        _ticking = False
    if _fault_streak:
        _fault_streak = 0
        _fault_seen.clear()   # a fault that returns after a recovery is a new episode
        _clear_faults()
    return min(interval, ACTIVE_INTERVAL) if _wake_pending else interval


def _report_fault(exc):
    """Log the fault and put it on the status line of the scene it broke."""
    message = f'{type(exc).__name__}: {exc}'
    if message not in _fault_seen:
        _fault_seen.add(message)
        logger.error('Scheduler tick failed (%d in a row): %s', _fault_streak, message, exc_info=True)
    else:
        logger.debug('Scheduler tick failed (%d in a row): %s', _fault_streak, message)
    text = f'Live update failed: {str(exc) or type(exc).__name__}'
    # The fault interrupted one scene's turn; anything before the scene loop
    # (the prune) is nobody's in particular and every project learns of it.
    scenes = [_tick_scene] if _tick_scene is not None else list(bpy.data.scenes)
    for scene in scenes:
        try:
            if hasattr(scene, 'open_painter'):
                _faults[scene.as_pointer()] = text
                _set_status(scene, text)
        except Exception as inner:   # a scene removed mid-tick; the log already has the fault
            logger.debug('Scheduler fault status: %s', inner)


def _clear_faults():
    """A recovered tick takes its fault line back the way ``_end_progress`` does:
    only while the line still shows it, so a message written since stays."""
    from . import shader_preview
    for scene in bpy.data.scenes:
        text = _faults.pop(scene.as_pointer(), None) if hasattr(scene, 'open_painter') else None
        if text is not None and scene.open_painter.status == text:
            _set_status(scene, '')
            shader_preview.restate(scene)
    _faults.clear()


def _forget_faults():
    global _fault_streak, _tick_scene, _wake_pending, _ticking
    _faults.clear()
    _fault_seen.clear()
    _fault_streak = 0
    _tick_scene = None
    _wake_pending = _ticking = False


def _tick():
    global _scene_cursor, _tick_scene
    from .preview_policy import fair_order
    from .evaluator import LayerEvaluator

    from . import live_preview, shader_preview
    from ..core import layer_store
    dependencies.report_updates()
    layer_store.maintain()   # a copied or appended scene, a directly added set: each gets its own store
    _tick_scene = None
    live = frozenset(key_for(scene, ts) for scene in bpy.data.scenes
                     if hasattr(scene, "open_painter") for ts in scene.open_painter.texture_sets)
    _prune(live, monotonic())
    for scene in fair_order(list(bpy.data.scenes), _scene_cursor, lambda item: item.as_pointer()):
        if not hasattr(scene, "open_painter"):
            continue
        _tick_scene = scene
        dependencies.refresh(scene)
        if field_jobs.progress(scene) is None and layer_cache.progress(scene) is None:
            _end_progress(scene)   # a finished job's line goes before the shader loop writes
        from ..painting.paint_session import detect

        activity = _paint_input.get(scene.as_pointer())
        windows = [w for w in bpy.context.window_manager.windows if w.scene == scene]
        painting = _modal_painting(scene)
        scene_key = scene.as_pointer()
        busy = any(key_for(scene, ts) in _jobs for ts in scene.open_painter.texture_sets)
        if painting:
            # Composite snapshots during a stroke, not only after mouse release.
            # Never invalidate a partially completed snapshot with another scan.
            _paint_input[scene_key] = (True, monotonic())
            if not busy and monotonic() - _live_checks.get(scene_key, 0) >= 0.033:
                if not shader_preview.native_paint_input(scene):
                    detect(scene, force=True)
                _live_checks[scene_key] = monotonic()
        elif activity and not busy:
            pressed, when = activity
            if monotonic() - when > 0.12:
                detect(scene, force=True)
                stroke_finished(scene)
        elif not activity and not busy and scene.open_painter.paint_image:
            from ..ui.editor import is_area

            if scene.open_painter.paint_correction:
                from ..baking import corrections
                corrections.poll(scene)
                _rebake_pending(scene)
            elif (not _modal_navigation(windows)
                  and not any(is_area(area) for w in windows for area in w.screen.areas)):
                # Catch short, unobserved native strokes, but not mid-orbit: a
                # dirty canvas stays dirty after a stroke and a 4K read/compare
                # can stall navigation. Keep the scan due until navigation ends.
                detect(scene)
        texture_sets = list(scene.open_painter.texture_sets)
        active = scene.open_painter.active_texture_set
        preferred = texture_sets[active].id if 0 <= active < len(texture_sets) else None
        for ts in fair_order(texture_sets, _set_cursors.get(scene_key), lambda item: item.id, preferred):
            key = key_for(scene, ts)
            if shader_preview.requested(ts):
                # Authoring uses Blender's material shader. Dirty output images
                # remain pending for an explicit bake, regardless of inactivity.
                _pending.pop(key, None)
                discard(key)
                _regions.pop(key, None)
                ready = shader_preview.tick(scene, ts)
                info = shader_preview.diagnostics(scene, ts, details=False)
                set_state(scene, ts, 'SHADER' if ready else
                          'FAILED' if info['error'] else 'PREPARING_INPUT', info['error'])
                if (info['pending_resources'] and not info['error'] and not info.get('frozen')
                        and not info.get('held') and not painting_active(scene)):
                    # Only a queue that was actually stepped earns the fast rate: a
                    # failed boundary keeps its siblings queued, and a stroke on
                    # another set (or the correction brush) gates this one without
                    # freezing it. Both wait at the idle/painting rate, never spin.
                    _scene_cursor = scene_key
                    _set_cursors[scene_key] = ts.id
                    return .001
                continue
            if not ts.dirty:
                set_state(scene, ts, "READY")
                _pending.pop(key, None)
                discard(key)
                _regions.pop(key, None)
                continue
            state = job_state(scene, ts)
            if state["state"] == "FAILED":
                continue
            now = monotonic()
            serial, since = _pending.setdefault(key, (ts.edit_serial, now))
            if serial != ts.edit_serial:
                _pending[key] = (ts.edit_serial, now)
                discard(key)
                set_state(scene, ts, "STALE")
                if not painting:
                    continue
            if key not in _jobs and not painting and (now - since < 0.08 or shader_preview.held(scene, ts)):
                set_state(scene, ts, "QUEUED")
                continue
            try:
                from ..constants import CHANNELS
                from ..core import image_manager, material_manager
                from ..core.validation import hierarchy_issues

                issues = hierarchy_issues(ts)
                if issues:
                    raise ValueError(" | ".join(issues))
                evaluator = _evaluators.get(key)
                if evaluator is None:
                    evaluator = _evaluators[key] = LayerEvaluator(scene)
                outputs = [image_manager.output(ts, channel) for channel in CHANNELS]
                active_channel = scene.open_painter.display_channel
                outputs.sort(key=lambda output: output.channel != active_channel)
                from . import preview_policy
                preview_size = preview_policy.resolution(ts)
                if preview_size < int(ts.resolution):
                    candidate = next((output for output in outputs if output.dirty and
                                      not preview_policy.current(scene, ts, output.channel)), None)
                    if candidate:
                        name = 'preview:' + candidate.channel
                        entry = _jobs.get(key)
                        if entry is None or entry[0] != name:
                            discard(key)
                            entry = (name, evaluator.channel_steps(ts.id, candidate.channel,
                                                                  preview_resolution=preview_size))
                            _jobs[key] = entry
                        set_state(scene, ts, 'RUNNING')
                        _set_status(scene, f'Interactive {preview_size} · full {ts.resolution} pending')
                        deadline = monotonic() + .006
                        while True:
                            try:
                                next(entry[1])
                            except StopIteration:
                                _jobs.pop(key, None)
                                break
                            if monotonic() >= deadline:
                                break
                        _scene_cursor = scene_key
                        _set_cursors[scene_key] = ts.id
                        return .001
                    if painting or now - since < .25:
                        return .016
                dirty = next((output for output in outputs if output.dirty), None)
                if dirty:
                    _scene_cursor = scene_key
                    _set_cursors[scene_key] = ts.id
                    _set_status(scene, "Updating textures…")
                    set_state(scene, ts, "RUNNING")
                    entry = _jobs.get(key)
                    if entry is None or entry[0] != dirty.channel:
                        region = _regions.get(key)
                        if region and (region[0] != ts.edit_serial or region[1] not in ('*', dirty.channel)):
                            region = None
                        entry = (
                            dirty.channel,
                            evaluator.channel_steps(ts.id, dirty.channel, region),
                        )
                        _jobs[key] = entry
                    job = entry[1]
                    deadline = monotonic() + 0.006
                    while True:
                        try:
                            next(job)
                        except StopIteration:
                            _jobs.pop(key, None)
                            if not any(output.dirty for output in outputs):
                                _regions.pop(key, None)
                            break
                        if monotonic() >= deadline:
                            return 0.001
                if any(output.dirty for output in outputs):
                    return 0.001
                live_preview.suspend(scene, ts)
                material_manager.repair(ts)
                ts.dirty = False
                for layer in ts.layers:
                    layer.dirty = False
                    for channel in layer.channels:
                        channel.dirty = False
                set_state(scene, ts, "READY")
                _set_status(scene, failure_message(scene))
            except Exception as exc:
                _set_status(scene, str(exc))
                logger.error("Evaluation: %s", exc)
                # Keep unresolved outputs and their last complete pixels. The same
                # failed revision stays parked until an edit or explicit retry.
                ts.dirty = True
                set_state(scene, ts, "FAILED", str(exc))
            _pending.pop(key, None)
            discard(key)
            return 0.001
        # Cooperative jobs run after the shader loop: a structural rebuild or a
        # boundary must not wait for a multi-second cache refresh, and a rebuild
        # that re-requests the cache supersedes the job before more strips are
        # wasted. Their progress line is written last so it wins the tick.
        if field_jobs.tick(scene, budget=.006):
            running = field_jobs.progress(scene)
            if running is not None:
                _set_progress(scene, f'Analysing {running[0]}… {int(running[1] * 100)}%')
            _scene_cursor = scene_key
            return .001
        if layer_cache.tick(scene, budget=.006):
            running = layer_cache.progress(scene)
            if running is not None:
                _set_progress(scene, f'Caching {running[0]}… {int(running[1] * 100)}%')
            _scene_cursor = scene_key
            return .001
        _end_progress(scene)
    _tick_scene = None
    # Idle projects poll slowly: native edits bind synchronously in their RNA
    # callbacks and structural edits wake the timer, so 60 Hz is only needed
    # while painting or while a shader session still has work to finish.
    return (
        ACTIVE_INTERVAL
        if any(
            scene.open_painter.paint_image or _paint_input.get(scene.as_pointer())
            or field_jobs.pending(scene) or layer_cache.pending(scene)
            or any(shader_preview.pending_work(scene, ts) for ts in scene.open_painter.texture_sets)
            for scene in bpy.data.scenes
            if hasattr(scene, "open_painter")
        )
        else IDLE_INTERVAL
    )


def _reset_runtime(*, forget_corrections):
    """Drop every runtime table that holds RNA or per-file state.

    Bake-correction records (the rasters the last bake used, the canvas
    snapshot) are numpy copies keyed by addresses that are stable within one
    file: an undo keeps them so the next stroke still rebakes only its region;
    a file load must not, since another file's datablocks may reuse them.
    """
    from ..painting.paint_session import reset
    from .raster_cache import clear_shared

    reset()
    cancel_all()
    from .live_preview import clear as clear_live
    clear_live()
    from .shader_preview import clear as clear_shaders
    clear_shaders(undo=not forget_corrections)   # an undo keeps the leaves its rebuild adopts
    from ..core import owners, layer_store
    owners.clear()
    layer_store.clear()
    from .preview_policy import clear
    clear()
    clear_shared()
    dependencies.clear(undo=not forget_corrections)
    _states.clear()
    _set_cursors.clear()
    from ..baking.diagnostics import forget
    forget()
    from ..baking.doctor import forget as forget_doctor
    forget_doctor()
    if forget_corrections:
        from ..baking import corrections
        corrections.forget()
    from ..geometry.seams import clear as clear_seams
    clear_seams()
    _pending.clear()
    _paint_input.clear()
    cancel_all()
    _live_checks.clear()
    _regions.clear()
    _forget_faults()
    from . import physical
    physical.forget()
    from . import gc_policy
    gc_policy.release(defer=True)   # every session just went: their frozen cycles are swept at the next pause


def _migrate_angles(project):
    """Mesh selector angles saved as degrees (before project format 2) become radians.

    The properties changed units in place: a file saved before that reads 45
    where 0.785 is meant, and the value is not clamped on the way in, so the
    normal-direction mask covers everything. Module base snapshots (JSON texts)
    carry the same fields and must agree with the migrated instances, or the
    next Update reports every angle as a local edit. The stamp keeps this to
    one pass per file: ``texture_set_manager.create`` writes the current
    format on a project's first set, so only a file saved before the stamp
    existed enters the scan, and only from ``on_load``. Undo and redo never
    reach a state older than the loaded (already migrated) file, so they skip
    it; a scan there would also fight the memfile step it just restored.
    """
    from ..properties.mesh_selector import FORMAT_VERSION, degrees_to_radians, migrate_angles
    if project.format_version >= FORMAT_VERSION:
        return
    before = project.format_version
    for ts in project.texture_sets:
        if before < 3 and ts.export.padding_mode == 'AUTOMATIC':
            # Export gutters arrived with format 3: an older file's exports gain them, and
            # the Export view shows that this was the upgrade's choice, not the artist's.
            ts.export.padding_migrated = True
        if before >= 2:
            continue
        for layer in ts.layers:
            for mask in layer.masks:
                migrate_angles(mask.mesh)
    from ..smart_materials.modules import BASE_TEXT_PREFIX
    for text in bpy.data.texts:
        if before >= 2 or not (text.get(OWNER) and text.name.startswith(BASE_TEXT_PREFIX)):
            continue
        try:
            base = json.loads(text.as_string())
        except ValueError:
            continue   # read_base treats it as missing; nothing to convert
        converted = False
        for layer in base.get('layers') or ():
            for mask in layer.get('masks', ()) if isinstance(layer, dict) else ():
                mesh = mask.get('mesh') if isinstance(mask, dict) else None
                if isinstance(mesh, dict) and degrees_to_radians(mesh) is not mesh:
                    mask['mesh'] = degrees_to_radians(mesh)
                    converted = True
        if converted:
            from ..smart_materials.modules import write_json
            write_json(text, base)
    project.format_version = FORMAT_VERSION


def _relearn(*, forget_corrections):
    _reset_runtime(forget_corrections=forget_corrections)
    from . import physical
    from ..core.image_manager import adopt_alpha_mode
    for image in bpy.data.images:
        if image.get(OWNER):
            adopt_alpha_mode(image)   # canvases saved before 0.9.7 carried straight alpha
    for scene in bpy.data.scenes:
        if hasattr(scene, "open_painter"):
            for ts in scene.open_painter.texture_sets:
                from ..core.layer_stack import ensure_channels
                for layer in ts.layers:
                    ensure_channels(layer)
                ts.dirty = True
                for output in ts.outputs:
                    output.dirty = True
                try:
                    physical.sync(ts)   # the asset may differ from where the material was authored
                except Exception:
                    pass


@persistent
def on_load(_):
    from .text_source import clear as clear_text
    clear_text()
    from ..painting.gpu_sources import clear as clear_stroke_gpu
    clear_stroke_gpu()
    from ..baking.bake_manager import forget as forget_released
    from .shader_resources import forget_graveyard, retire_unloaded_leaves, freeze_new_leaves
    forget_graveyard()         # the previous file's released datablocks went with it
    retire_unloaded_leaves()   # before any draw regenerates a bufferless leaf blank
    forget_released()   # the remembered superseded-map names belong to the previous file
    freeze_new_leaves(False)   # a fresh file: normal building is allowed
    from ..core import layer_store
    layer_store.clear()
    layer_store.ensure_all()   # stacks an older file kept on its sets move to their stores first
    for scene in bpy.data.scenes:
        if hasattr(scene, "open_painter"):
            _migrate_angles(scene.open_painter)   # file-format upgrades: the loaded file only
    _relearn(forget_corrections=True)


_undo_main = None   # the address of bpy.data before an undo or redo (on_undo_pre)
_undo_snapshots = {}   # (scene session id, set id) -> the set's stored properties before it (undo_follow)


@persistent
def on_undo_pre(_):
    global _undo_main
    _undo_main = bpy.data.as_pointer()
    _undo_snapshots.clear()
    if getattr(bpy.context, 'mode', '') == 'PAINT_TEXTURE':
        return   # a stroke's undo restores pixels: no set is rebuilt to follow (and no snapshot is due)
    from . import shader_preview, undo_follow
    try:
        for scene in bpy.data.scenes:
            if not hasattr(scene, "open_painter"):
                continue
            for ts in scene.open_painter.texture_sets:
                if shader_preview.followable(scene, ts):
                    snapshot = undo_follow.snapshot(scene, ts)
                    if snapshot is not None:
                        _undo_snapshots[(scene.session_uid, ts.id)] = snapshot
    except Exception:
        _undo_snapshots.clear()   # the undo then rebuilds every set, as it would without them
        logger.debug('Undo snapshots', exc_info=True)


def _restored_datablocks():
    """Whether the undo or redo that just ran restored datablocks. A memfile step reads the file
    into a new Main (``bpy.data``, while the old one still exists); an image paint, sculpt or
    edit-mode step restores pixels or a mesh in place and keeps it, with every struct and
    Python wrapper the sessions hold."""
    global _undo_main
    before, _undo_main = _undo_main, None
    return before != bpy.data.as_pointer()   # None: no pre handler ran


@persistent
def on_undo(_):
    from .shader_resources import retire_unloaded_leaves, freeze_new_leaves
    retire_unloaded_leaves()   # a leaf re-read from the memfile lost its pixels: never ready
    # No new leaf datablock may be bound into the persisted material tree until the next authored
    # gesture: one created in this between-undo-steps window is freed by a later undo while the
    # tree still points at it (the open 0.13 crash; docs/quality-stability-0.13.md, S2).
    freeze_new_leaves(True)
    from . import shader_preview
    restored = _restored_datablocks()
    before = dict(_undo_snapshots)
    _undo_snapshots.clear()
    if not restored and all(shader_preview.requested(ts) for scene in bpy.data.scenes
                            if hasattr(scene, "open_painter")
                            for ts in scene.open_painter.texture_sets):
        # Nothing was restored but pixels or a mesh, which report their depsgraph updates like an
        # edit: the sessions keep their graphs and render what reads them (an undone brush stroke
        # cost a whole rebuild). A CPU-mode set starts over, as after any undo.
        return
    # Memfile undo restores properties without their update callbacks: every
    # session, job and dirty flag starts over. Correction records stay (see
    # ``_reset_runtime``): an undone stroke is a region change, not a new bake.
    _relearn(forget_corrections=False)
    if restored and before:
        # A set whose stored properties changed only in numeric values, or in some layers' other
        # values with their collections in place, keeps its compiled structure: its graph follows
        # the restored structs and patches those layers (``undo_follow``). Compared after
        # ``_relearn``, whose writes (derived physical values) count too.
        from . import undo_follow
        followed = {}
        try:
            for scene in bpy.data.scenes:
                if not hasattr(scene, "open_painter"):
                    continue
                for ts in scene.open_painter.texture_sets:
                    snapshot = before.get((scene.session_uid, ts.id))
                    if snapshot is not None:
                        changed = undo_follow.changed_layers(snapshot, undo_follow.snapshot(scene, ts))
                        if changed is not None:
                            followed[key_for(scene, ts)] = changed
        except Exception:
            followed = {}
            logger.debug('Undo comparison', exc_info=True)
        shader_preview.follow_undos(followed)


def _pack_images(source_images):
    """Pack every managed image and authored input, each on its own: an artist
    texture whose file vanished raises from ``pack()``, and the managed
    canvases after it (generated, saved blank unless packed) must still land."""
    from ..core.image_manager import pack_image
    from . import shader_preview
    # The channel images of engine-evaluated sets are derived: re-rendered after the file
    # opens. A CPU-mode set's outputs are its authoritative result and are packed.
    derived = {out.image.as_pointer() for scene in bpy.data.scenes if hasattr(scene, 'open_painter')
               for ts in scene.open_painter.texture_sets if shader_preview.requested(ts)
               for out in ts.outputs if out.image is not None}
    for image in bpy.data.images:
        if not ((image.get(OWNER) or image.as_pointer() in source_images) and image.has_data and image.users):
            continue
        if image.as_pointer() in derived:
            continue
        try:
            pack_image(image)
        except Exception as exc:
            logger.error('Pack %s: %s', image.name, exc)
            for scene in bpy.data.scenes:
                if hasattr(scene, 'open_painter'):
                    scene.open_painter.status = f'Could not pack {image.name}: {exc}'


@persistent
def on_save(_):
    _save(pixels=False, leaves=True)


def _save(pixels, leaves=False):
    # Save native recipes and their authored inputs. Only explicit CPU preview
    # mode flushes full channel outputs; native mode completes source boundaries.
    # Requested automatic layer caches are completed before packing. Field
    # analyses and scheduled edge rebakes are not cancelled: nothing
    # re-requests them in the running session (0.9.7).
    for key in list(_jobs):
        discard(key)
    _evaluators.clear()
    from .live_preview import clear as clear_live
    clear_live()
    from .preview_policy import clear
    clear()
    _regions.clear()
    from ..painting.paint_session import detect
    from .evaluator import LayerEvaluator

    source_images = set()
    try:
        for scene in bpy.data.scenes:
            if not hasattr(scene, "open_painter"):
                continue
            detect(scene, force=True)
            stroke_finished(scene)
            # A brush alpha a stroke keeps (or the brush holds) is an authored input too: a
            # generated one saved without its pixels reloads blank and every stroke using it
            # would render differently after reopening (0.13 hardening, H4). A generated float
            # alpha is packed as full-precision, channel-packed data so it reloads as stored
            # (a straight-alpha EXR comes back premultiplied and at half precision).
            alphas = [stroke.alpha_image for stroke in scene.open_painter.strokes if stroke.alpha_image is not None]
            if scene.open_painter.vector_brush.alpha_image is not None:
                alphas.append(scene.open_painter.vector_brush.alpha_image)
            for image in alphas:
                source_images.add(image.as_pointer())
                if image.source == 'GENERATED' and image.is_float:
                    if image.alpha_mode != 'CHANNEL_PACKED':
                        image.alpha_mode = 'CHANNEL_PACKED'
                    if image.use_half_precision:
                        image.use_half_precision = False
            for ts in scene.open_painter.texture_sets:
                sources = [item.image for item in ts.baked_maps]
                for layer in ts.layers:
                    for prop in layer.channels:
                        sources.extend((prop.image, prop.detail_image))
                        sources.extend(effect.image for effect in prop.effects)
                    for mask in layer.masks:
                        sources.extend((mask.image, mask.effect.image, mask.influence.image))
                source_images.update(image.as_pointer() for image in sources if image)
                from . import shader_preview
                if shader_preview.requested(ts):
                    try:
                        shader_preview.flush(scene, ts, pixels=pixels)
                        info = shader_preview.diagnostics(scene, ts)
                        set_state(scene, ts, 'SHADER' if shader_preview.current(scene, ts)
                                  else 'FAILED', info['error'])
                    except Exception as exc:
                        scene.open_painter.status = f'Shader input needs attention: {exc}'
                        set_state(scene, ts, 'FAILED', str(exc))
                        logger.error('Save shader input: %s', exc)
                elif ts.dirty:
                    try:
                        LayerEvaluator(scene).evaluate_texture_set(ts.id)
                        set_state(scene, ts, "READY")
                    except Exception as exc:
                        scene.open_painter.status = f"Saved project; preview needs attention: {exc}"
                        set_state(scene, ts, "FAILED", str(exc))
                        logger.error("Save preview: %s", exc)
    finally:
        # A map a rebake superseded is neither written nor packed; removing it
        # now keeps a session of rebakes from holding every generation in memory.
        try:
            from ..baking.bake_manager import sweep_released
            sweep_released()
        except Exception as exc:
            logger.error('Sweep superseded maps: %s', exc)
        if leaves:
            # The CPU inputs the engine samples are written with the file (and packed below),
            # so a reopened file adopts them instead of regenerating every one.
            from .shader_resources import persist_bound_leaves
            persist_bound_leaves()
        # Whatever failed above, the strokes are packed: an unpacked managed
        # canvas reloads blank.
        _pack_images(source_images)


@persistent
def on_bake(*_):
    """A bake samples the sets' images on the CPU: copy the engine's results into their pixels."""
    from .shader_preview import sync_pixels
    sync_pixels()


@persistent
def on_render(_):
    _save(pixels=True)   # render engines read the set's images on the CPU
    # A scheduled edge rebake would run a Cycles bake from the timer while the
    # render job owns the scene; cache and field jobs may keep their requests.
    field_jobs.cancel_rebakes()


def register():
    addon = bpy.context.preferences.addons.get(__package__.rsplit('.', 1)[0])
    if addon:
        from .memory import shared
        shared.configure(addon.preferences.texture_cache_mb * 1024 * 1024)
    bpy.app.handlers.depsgraph_update_post.append(dependencies.on_depsgraph)
    bpy.app.handlers.frame_change_post.append(dependencies.on_frame)
    bpy.app.handlers.load_post.append(on_load)
    bpy.app.handlers.undo_pre.append(on_undo_pre)
    bpy.app.handlers.redo_pre.append(on_undo_pre)
    bpy.app.handlers.undo_post.append(on_undo)
    bpy.app.handlers.redo_post.append(on_undo)
    bpy.app.handlers.save_pre.append(on_save)
    bpy.app.handlers.render_pre.append(on_render)
    bpy.app.handlers.object_bake_pre.append(on_bake)
    field_jobs.paused = painting_active
    layer_cache.paused = painting_active
    if not bpy.app.background:
        bpy.app.timers.register(tick, first_interval=0.5, persistent=True)


def unregister():
    from .text_source import clear as clear_text
    clear_text()
    from ..painting.paint_session import restore_faces
    from .raster_cache import clear_shared

    cancel_all()
    from .live_preview import clear as clear_live
    clear_live()
    from .shader_preview import clear as clear_shaders
    clear_shaders()
    from ..core import owners
    owners.clear()
    from .engine.runtime import forget_device
    forget_device()   # the engine's compiled programs and pooled textures go with the add-on
    from .preview_policy import clear
    clear()
    clear_shared()
    dependencies.clear()
    _states.clear()
    _set_cursors.clear()
    from ..baking.diagnostics import forget
    forget()
    from ..baking.doctor import forget as forget_doctor
    forget_doctor()
    from ..baking.corrections import forget as forget_corrections
    forget_corrections()
    from ..geometry.seams import clear as clear_seams
    clear_seams()
    from . import gc_policy
    gc_policy.release()

    for scene in bpy.data.scenes:
        if hasattr(scene, "open_painter"):
            restore_faces(scene.open_painter)
    for handlers, fn in (
        (bpy.app.handlers.depsgraph_update_post, dependencies.on_depsgraph),
        (bpy.app.handlers.frame_change_post, dependencies.on_frame),
        (bpy.app.handlers.load_post, on_load),
        (bpy.app.handlers.undo_pre, on_undo_pre),
        (bpy.app.handlers.redo_pre, on_undo_pre),
        (bpy.app.handlers.undo_post, on_undo),
        (bpy.app.handlers.redo_post, on_undo),
        (bpy.app.handlers.save_pre, on_save),
        (bpy.app.handlers.render_pre, on_render),
        (bpy.app.handlers.object_bake_pre, on_bake),
    ):
        if fn in handlers:
            handlers.remove(fn)
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    field_jobs.paused = None
    layer_cache.paused = None
    from ..painting.paint_session import reset

    reset()
    _pending.clear()
    _paint_input.clear()
    cancel_all()
    _live_checks.clear()
    _regions.clear()
    _forget_faults()
