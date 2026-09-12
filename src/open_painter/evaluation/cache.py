"""Debounced evaluation lifecycle; only transient scheduling lives in memory."""

from time import monotonic

import bpy
from bpy.app.handlers import persistent

from ..constants import OWNER
from ..utils.logging import logger

_pending = {}
_paint_input = {}
_evaluators = {}
_jobs = {}
_live_checks = {}
_regions = {}


def paint_input(scene, pressed):
    _paint_input[scene.as_pointer()] = (pressed, monotonic())


def tick():
    from .evaluator import LayerEvaluator

    live = {
        ts.id
        for scene in bpy.data.scenes
        if hasattr(scene, "open_painter")
        for ts in scene.open_painter.texture_sets
    }
    for identifier in set(_evaluators) - live:
        _evaluators.pop(identifier, None)
        _pending.pop(identifier, None)
        _jobs.pop(identifier, None)
        _regions.pop(identifier, None)
    for scene in bpy.data.scenes:
        if not hasattr(scene, "open_painter"):
            continue
        from ..painting.paint_session import detect

        activity = _paint_input.get(scene.as_pointer())
        windows = [w for w in bpy.context.window_manager.windows if w.scene == scene]
        painting = any(
            op.bl_idname == "PAINT_OT_image_paint" for w in windows for op in w.modal_operators
        )
        scene_key = scene.as_pointer()
        busy = any(ts.id in _jobs for ts in scene.open_painter.texture_sets)
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
        for ts in scene.open_painter.texture_sets:
            if not ts.dirty:
                _pending.pop(ts.id, None)
                _evaluators.pop(ts.id, None)
                _jobs.pop(ts.id, None)
                _regions.pop(ts.id, None)
                continue
            now = monotonic()
            serial, since = _pending.setdefault(ts.id, (ts.edit_serial, now))
            if serial != ts.edit_serial:
                _pending[ts.id] = (ts.edit_serial, now)
                _evaluators.pop(ts.id, None)
                _jobs.pop(ts.id, None)
                if not painting:
                    continue
            if ts.id not in _jobs and not painting and now - since < 0.08:
                continue
            try:
                from ..constants import CHANNELS
                from ..core import image_manager, material_manager
                from ..core.validation import hierarchy_issues

                issues = hierarchy_issues(ts)
                if issues:
                    raise ValueError(" | ".join(issues))
                evaluator = _evaluators.get(ts.id)
                if evaluator is None:
                    evaluator = _evaluators[ts.id] = LayerEvaluator(scene)
                outputs = [image_manager.output(ts, channel) for channel in CHANNELS]
                dirty = next((output for output in outputs if output.dirty), None)
                if dirty:
                    entry = _jobs.get(ts.id)
                    if entry is None or entry[0] != dirty.channel:
                        region = _regions.get(ts.id)
                        if region and (region[0], region[1]) != (ts.edit_serial, dirty.channel):
                            region = None
                        entry = (
                            dirty.channel,
                            evaluator.channel_steps(ts.id, dirty.channel, region),
                        )
                        _jobs[ts.id] = entry
                    job = entry[1]
                    deadline = monotonic() + 0.006
                    while True:
                        try:
                            next(job)
                        except StopIteration:
                            _jobs.pop(ts.id, None)
                            _regions.pop(ts.id, None)
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
                scene.open_painter.status = ""
            except Exception as exc:
                scene.open_painter.status = str(exc)
                logger.error("Evaluation: %s", exc)
                ts.dirty = False
            _pending.pop(ts.id, None)
            _evaluators.pop(ts.id, None)
            _jobs.pop(ts.id, None)
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

    reset()
    _pending.clear()
    _paint_input.clear()
    _evaluators.clear()
    _jobs.clear()
    _live_checks.clear()
    _regions.clear()
    for scene in bpy.data.scenes:
        if hasattr(scene, "open_painter"):
            for ts in scene.open_painter.texture_sets:
                ts.dirty = True
                for output in ts.outputs:
                    output.dirty = True


@persistent
def on_save(_):
    # A save performs a complete authoritative flush; discard older preview jobs.
    _jobs.clear()
    _evaluators.clear()
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
                except Exception as exc:
                    scene.open_painter.status = f"Saved project; preview needs attention: {exc}"
                    logger.error("Save preview: %s", exc)
    for image in bpy.data.images:
        if image.get(OWNER) and image.has_data:
            image.pack()


def register():
    bpy.app.handlers.load_post.append(on_load)
    bpy.app.handlers.undo_post.append(on_load)
    bpy.app.handlers.redo_post.append(on_load)
    bpy.app.handlers.save_pre.append(on_save)
    if not bpy.app.background:
        bpy.app.timers.register(tick, first_interval=0.5, persistent=True)


def unregister():
    from ..painting.paint_session import restore_faces

    for scene in bpy.data.scenes:
        if hasattr(scene, "open_painter"):
            restore_faces(scene.open_painter)
    for handlers, fn in (
        (bpy.app.handlers.load_post, on_load),
        (bpy.app.handlers.undo_post, on_load),
        (bpy.app.handlers.redo_post, on_load),
        (bpy.app.handlers.save_pre, on_save),
    ):
        if fn in handlers:
            handlers.remove(fn)
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    from ..painting.paint_session import reset

    reset()
    _pending.clear()
    _paint_input.clear()
    _evaluators.clear()
    _jobs.clear()
    _live_checks.clear()
    _regions.clear()
