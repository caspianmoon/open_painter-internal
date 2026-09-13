"""Map-boundary queue. A running Cycles call remains blocking."""
import bpy


class OP_OT_bake_queue(bpy.types.Operator):
    bl_idname = 'open_painter.bake_queue'
    bl_label = 'Bake Selected Maps'
    bl_description = 'Queue maps; Esc cancels between maps. Each Cycles map blocks while baking'
    bl_options = {'REGISTER', 'UNDO'}
    _timer = None
    _job = None

    def finish(self, context):
        if self._job:
            self._job.close()
            self._job = None
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

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
        except Exception as exc:
            self.finish(context)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self._timer = context.window_manager.event_timer_add(.1, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self.finish(context)
            context.scene.open_painter.status = 'Bake queue stopped; completed maps retained'
            return {'FINISHED'}
        if event.type != 'TIMER' or event.timer != self._timer:
            return {'RUNNING_MODAL'}
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
