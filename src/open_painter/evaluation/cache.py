"""Debounced evaluation lifecycle; only transient scheduling lives in memory."""

from time import monotonic

import bpy
from bpy.app.handlers import persistent

from ..constants import OWNER
from .resource_keys import key_for
from . import dependencies
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



def job_state(scene, ts):
    key = key_for(scene, ts)
    state = _states.get(key)
    if state and state['serial'] == ts.edit_serial:
        return dict(state)
    return dict(state='QUEUED' if ts.dirty else 'READY', serial=ts.edit_serial, error='')


def set_state(scene, ts, state, error=''):
    _states[key_for(scene, ts)] = dict(state=state, serial=ts.edit_serial, error=error)



def failure_message(scene):
    return ' | '.join(f"{ts.name}: {state['error']}" for ts in scene.open_painter.texture_sets
                      if (state := job_state(scene, ts))['state'] == 'FAILED')


def retry(scene, ts):
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


def paint_input(scene, pressed):
    _paint_input[scene.as_pointer()] = (pressed, monotonic())


def tick():
    global _scene_cursor
    from .preview_policy import fair_order
    from .evaluator import LayerEvaluator
    from .raster_cache import prune

    live = {key_for(scene, ts) for scene in bpy.data.scenes
            if hasattr(scene, "open_painter") for ts in scene.open_painter.texture_sets}
    prune(live)
    from . import preview_policy
    preview_policy.prune(live)
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
    for scene in fair_order(list(bpy.data.scenes), _scene_cursor, lambda item: item.as_pointer()):
        if not hasattr(scene, "open_painter"):
            continue
        dependencies.refresh(scene)
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
                        scene.open_painter.status = f'Interactive {preview_size} · full {ts.resolution} pending'
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
                    scene.open_painter.status = "Updating textures…"
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
                material_manager.repair(ts)
                ts.dirty = False
                for layer in ts.layers:
                    layer.dirty = False
                    for channel in layer.channels:
                        channel.dirty = False
                set_state(scene, ts, "READY")
                scene.open_painter.status = failure_message(scene)
            except Exception as exc:
                scene.open_painter.status = str(exc)
                logger.error("Evaluation: %s", exc)
                # Keep unresolved outputs and their last complete pixels. The same
                # failed revision stays parked until an edit or explicit retry.
                ts.dirty = True
                set_state(scene, ts, "FAILED", str(exc))
            _pending.pop(key, None)
            discard(key)
            return 0.001
    return (
        0.016
        if any(
            scene.open_painter.paint_image
            for scene in bpy.data.scenes
            if hasattr(scene, "open_painter")
        )
        else 0.1
    )


@persistent
def on_load(_):
    from ..painting.paint_session import reset
    from .raster_cache import clear_shared

    reset()
    from .preview_policy import clear
    clear()
    clear_shared()
    dependencies.clear()
    _states.clear()
    _set_cursors.clear()
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
    # A save performs a complete authoritative flush; discard older preview jobs.
    cancel_all()
    from .preview_policy import clear
    clear()
    _regions.clear()
    from ..painting.paint_session import detect
    from .evaluator import LayerEvaluator

    for scene in bpy.data.scenes:
        if not hasattr(scene, "open_painter"):
            continue
        detect(scene, force=True)
        for ts in scene.open_painter.texture_sets:
            if ts.dirty:
                try:
                    LayerEvaluator(scene).evaluate_texture_set(ts.id)
                    set_state(scene, ts, "READY")
                except Exception as exc:
                    scene.open_painter.status = f"Saved project; preview needs attention: {exc}"
                    set_state(scene, ts, "FAILED", str(exc))
                    logger.error("Save preview: %s", exc)
    for image in bpy.data.images:
        if image.get(OWNER) and image.has_data and image.users:
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

    from .preview_policy import clear
    clear()
    clear_shared()
    dependencies.clear()
    _states.clear()
    _set_cursors.clear()

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
