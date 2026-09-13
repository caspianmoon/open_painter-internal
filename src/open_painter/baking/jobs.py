"""Map-boundary queue. A running Cycles call remains blocking."""
import bpy


class OP_OT_bake_queue(bpy.types.Operator):
    bl_idname = 'open_painter.bake_queue'
    bl_label = 'Bake Selected Maps'
    bl_description = 'Queue maps; Esc cancels between maps. Each Cycles map blocks while baking'
    bl_options = {'REGISTER', 'UNDO'}
    _timer = None
    _job = None
    _last_timer_duration = 0.

    def finish(self, context):
        job, timer = self._job, self._timer
        self._job = self._timer = None
        self._last_timer_duration = 0.
        try:
            if job is not None:
                job.close()
        finally:
            if timer is not None:
                try:
                    context.window_manager.event_timer_remove(timer)
                except ReferenceError:
                    pass  # Closing the owning window already removes its timers.

    def execute(self, context):
        from ..core.project_manager import require_set
        from .bake_manager import bake_selected
        bake_selected(context, require_set(context))
        return {'FINISHED'}

    def invoke(self, context, event):
        from ..core.project_manager import require_set
        from .bake_manager import bake_steps
        try:
            self._job = bake_steps(context, require_set(context))
            kind = next(self._job)
            context.scene.open_painter.status = f'Bake queued: {kind} · Esc between maps'
            self._timer = context.window_manager.event_timer_add(.1, window=context.window)
            self._last_timer_duration = self._timer.time_duration
            context.window_manager.modal_handler_add(self)
        except Exception as exc:
            self.finish(context)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self.finish(context)
            context.scene.open_painter.status = 'Bake queue stopped; completed maps retained'
            return {'FINISHED'}
        if event.type != 'TIMER' or self._timer is None:
            return {'RUNNING_MODAL'}
        # Event exposes no timer handle. The owned Timer's duration changes only
        # when it becomes due; unrelated or queued TIMER events must not speed up
        # the queue. Consume the tick before entering a blocking map bake.
        duration = self._timer.time_duration
        if duration <= self._last_timer_duration:
            return {'RUNNING_MODAL'}
        self._last_timer_duration = duration
        try:
            kind = next(self._job)
            context.scene.open_painter.status = f'Bake queued: {kind} · Esc between maps'
        except StopIteration:
            self.finish(context)
            context.scene.open_painter.status = 'Mesh maps complete'
            return {'FINISHED'}
        except Exception as exc:
            self.finish(context)
            self.report({'ERROR'}, str(exc))
            context.scene.open_painter.status = f'Bake failed: {exc}'
            return {'FINISHED'}  # Completed maps still belong to this undo step.
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self.finish(context)
