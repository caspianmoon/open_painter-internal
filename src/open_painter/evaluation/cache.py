"""Authoring lifecycle: native shaders and isolated inputs, or explicit CPU mode.

The legacy debounce below belongs to CPU texture evaluation. Native parameter
edits are bound synchronously and never enter that full-stack image scheduler.
"""

from time import monotonic

import bpy
from bpy.app.handlers import persistent

from ..constants import OWNER
from .resource_keys import key_for
from . import dependencies, field_jobs
from ..utils.logging import logger

_pending = {}
_paint_input = {}
_evaluators = {}
_jobs = {}
_live_checks = {}
_regions = {}
_states = {}
_scene_cursor = None
_set_cursors = {}
_last_prune = (frozenset(), 0.0)
IDLE_INTERVAL = 0.1
ACTIVE_INTERVAL = 0.016


def wake():
    """Run the scheduler on the next event loop pass (a structural edit is pending).

    Only re-arms a registered timer: tests and explicit CPU workflows that
    unregistered it must not have it revived by a property callback.
    """
    if bpy.app.background or not bpy.app.timers.is_registered(tick):
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
    for table in (_paint_input, _live_checks, _set_cursors):
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



def job_state(scene, ts):
    from . import shader_preview
    if shader_preview.requested(ts):
        info = shader_preview.diagnostics(scene, ts)
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


def cancel_all():
    for key in list(_jobs):
        discard(key)
    _evaluators.clear()
    field_jobs.cancel_all()


def paint_input(scene, pressed):
    _paint_input[scene.as_pointer()] = (pressed, monotonic())


def tick():
    global _scene_cursor
    from .preview_policy import fair_order
    from .evaluator import LayerEvaluator

    from . import live_preview, shader_preview
    live = frozenset(key_for(scene, ts) for scene in bpy.data.scenes
                     if hasattr(scene, "open_painter") for ts in scene.open_painter.texture_sets)
    _prune(live, monotonic())
    for scene in fair_order(list(bpy.data.scenes), _scene_cursor, lambda item: item.as_pointer()):
        if not hasattr(scene, "open_painter"):
            continue
        dependencies.refresh(scene)
        if field_jobs.tick(scene):
            running = field_jobs.progress(scene)
            if running is not None:
                _set_status(scene, f'Analysing {running[0]}… {int(running[1] * 100)}%')
            _scene_cursor = scene.as_pointer()
            return .001
        from ..painting.paint_session import detect

        activity = _paint_input.get(scene.as_pointer())
        windows = [w for w in bpy.context.window_manager.windows if w.scene == scene]
        painting = any(
            op.bl_idname == "PAINT_OT_image_paint" for w in windows for op in w.modal_operators
        )
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
                _paint_input.pop(scene.as_pointer(), None)
        elif not activity and not busy and scene.open_painter.paint_image:
            from ..ui.editor import is_area

            if not any(is_area(area) for w in windows for area in w.screen.areas):
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
                info = shader_preview.diagnostics(scene, ts)
                set_state(scene, ts, 'SHADER' if ready else
                          'FAILED' if info['error'] else 'PREPARING_INPUT', info['error'])
                if info['pending_resources']:
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
            if key not in _jobs and not painting and now - since < 0.08:
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
                active_channel = scene.open_painter.blend_channel
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
    # Idle projects poll slowly: native edits bind synchronously in their RNA
    # callbacks and structural edits wake the timer, so 60 Hz is only needed
    # while painting or while a shader session still has work to finish.
    return (
        ACTIVE_INTERVAL
        if any(
            scene.open_painter.paint_image or _paint_input.get(scene.as_pointer())
            or field_jobs.pending(scene)
            or any(shader_preview.pending_work(scene, ts) for ts in scene.open_painter.texture_sets)
            for scene in bpy.data.scenes
            if hasattr(scene, "open_painter")
        )
        else IDLE_INTERVAL
    )


@persistent
def on_load(_):
    from ..painting.paint_session import reset
    from .raster_cache import clear_shared

    reset()
    cancel_all()
    from .live_preview import clear as clear_live
    clear_live()
    from .shader_preview import clear as clear_shaders
    clear_shaders()
    from .preview_policy import clear
    clear()
    clear_shared()
    dependencies.clear()
    _states.clear()
    _set_cursors.clear()
    from ..baking.diagnostics import forget
    forget()
    _pending.clear()
    _paint_input.clear()
    cancel_all()
    _live_checks.clear()
    _regions.clear()
    for scene in bpy.data.scenes:
        if hasattr(scene, "open_painter"):
            for ts in scene.open_painter.texture_sets:
                from ..core.layer_stack import ensure_channels
                for layer in ts.layers:
                    ensure_channels(layer)
                ts.dirty = True
                for output in ts.outputs:
                    output.dirty = True


@persistent
def on_save(_):
    # Save native recipes and their authored inputs. Only explicit CPU preview
    # mode flushes full channel outputs; native mode completes source boundaries.
    cancel_all()
    from .live_preview import clear as clear_live
    clear_live()
    from .preview_policy import clear
    clear()
    _regions.clear()
    from ..painting.paint_session import detect
    from .evaluator import LayerEvaluator

    source_images = set()
    for scene in bpy.data.scenes:
        if not hasattr(scene, "open_painter"):
            continue
        detect(scene, force=True)
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
                    shader_preview.flush(scene, ts)
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
    for image in bpy.data.images:
        if (image.get(OWNER) or image.as_pointer() in source_images) and image.has_data and image.users:
            from ..core.image_manager import pack_image
            pack_image(image)


def register():
    addon = bpy.context.preferences.addons.get(__package__.rsplit('.', 1)[0])
    if addon:
        from .memory import shared
        shared.configure(addon.preferences.texture_cache_mb * 1024 * 1024)
    bpy.app.handlers.depsgraph_update_post.append(dependencies.on_depsgraph)
    bpy.app.handlers.frame_change_post.append(dependencies.on_frame)
    bpy.app.handlers.load_post.append(on_load)
    bpy.app.handlers.undo_post.append(on_load)
    bpy.app.handlers.redo_post.append(on_load)
    bpy.app.handlers.save_pre.append(on_save)
    bpy.app.handlers.render_pre.append(on_save)
    if not bpy.app.background:
        bpy.app.timers.register(tick, first_interval=0.5, persistent=True)


def unregister():
    from ..painting.paint_session import restore_faces
    from .raster_cache import clear_shared

    cancel_all()
    from .live_preview import clear as clear_live
    clear_live()
    from .shader_preview import clear as clear_shaders
    clear_shaders()
    from .preview_policy import clear
    clear()
    clear_shared()
    dependencies.clear()
    _states.clear()
    _set_cursors.clear()
    from ..baking.diagnostics import forget
    forget()

    for scene in bpy.data.scenes:
        if hasattr(scene, "open_painter"):
            restore_faces(scene.open_painter)
    for handlers, fn in (
        (bpy.app.handlers.depsgraph_update_post, dependencies.on_depsgraph),
        (bpy.app.handlers.frame_change_post, dependencies.on_frame),
        (bpy.app.handlers.load_post, on_load),
        (bpy.app.handlers.undo_post, on_load),
        (bpy.app.handlers.redo_post, on_load),
        (bpy.app.handlers.save_pre, on_save),
        (bpy.app.handlers.render_pre, on_save),
    ):
        if fn in handlers:
            handlers.remove(fn)
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    from ..painting.paint_session import reset

    reset()
    _pending.clear()
    _paint_input.clear()
    cancel_all()
    _live_checks.clear()
    _regions.clear()
