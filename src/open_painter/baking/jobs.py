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


class OP_OT_edge_map_refresh(bpy.types.Operator):
    """Rebake the Curvature edge map cooperatively so the interface stays responsive."""
    bl_idname = 'open_painter.edge_map_refresh'
    bl_label = 'Rebake Edge Map (Background)'
    bl_options = {'REGISTER', 'INTERNAL'}
    set_id: bpy.props.StringProperty(options={'HIDDEN'}, description='Texture set to rebake; the active set when empty')
    running = False
    _timer = None
    _job = None
    _set_id = ''
    _expected = ''
    _last_timer_duration = 0.

    def _set(self, context):
        return next((ts for ts in context.scene.open_painter.texture_sets if ts.id == self._set_id), None)

    def finish(self, context):
        job, timer = self._job, self._timer
        self._job = self._timer = None
        OP_OT_edge_map_refresh.running = False
        try:
            if job is not None:
                job.close()
        finally:
            if timer is not None:
                try:
                    context.window_manager.event_timer_remove(timer)
                except ReferenceError:
                    pass

    def invoke(self, context, event):
        from ..core.project_manager import require_set
        from ..geometry.provenance import record
        from .maps.curvature import curvature_steps
        if OP_OT_edge_map_refresh.running:
            return {'CANCELLED'}
        try:
            ts = (next((item for item in context.scene.open_painter.texture_sets if item.id == self.set_id), None)
                  if self.set_id else require_set(context))
            if ts is None:
                raise ValueError('That texture set no longer exists.')
            self._set_id = ts.id
            self._expected = record(ts, 'CURVATURE')
            self._job = curvature_steps(ts)
            next(self._job)  # reads the evaluated meshes now, while the depsgraph is current
            context.scene.open_painter.status = 'Rebaking edge maps… 2% · Esc stops'
            self._timer = context.window_manager.event_timer_add(.02, window=context.window)
            self._last_timer_duration = self._timer.time_duration
            context.window_manager.modal_handler_add(self)
            OP_OT_edge_map_refresh.running = True
        except Exception as exc:
            self.finish(context)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        from ..core.layer_stack import invalidate
        from ..geometry.provenance import record
        from .bake_manager import register_maps
        if event.type == 'ESC':
            self.finish(context)
            context.scene.open_painter.status = 'Edge map rebake stopped; the previous map is kept'
            return {'FINISHED'}
        if event.type != 'TIMER' or self._timer is None:
            return {'PASS_THROUGH'}
        duration = self._timer.time_duration
        if duration <= self._last_timer_duration:
            return {'PASS_THROUGH'}
        self._last_timer_duration = duration
        try:
            fraction = next(self._job)
            context.scene.open_painter.status = f'Rebaking edge maps… {int(fraction * 100)}% · Esc stops'
        except StopIteration as done:
            produced = done.value
            self.finish(context)
            ts = self._set(context)
            if ts is None or record(ts, 'CURVATURE') != self._expected:
                for image in produced.values():
                    bpy.data.images.remove(image)
                context.scene.open_painter.status = 'Bake inputs changed; edge map rebake discarded'
                return {'FINISHED'}
            register_maps(ts, produced)
            invalidate(ts)
            context.scene.open_painter.status = 'Edge maps rebaked: thin, normal, wide and extreme reaches'
            for area in context.screen.areas:
                area.tag_redraw()
            return {'FINISHED'}
        except Exception as exc:
            self.finish(context)
            self.report({'ERROR'}, str(exc))
            context.scene.open_painter.status = f'Edge map rebake failed: {exc}'
            return {'FINISHED'}
        return {'PASS_THROUGH'}

    def cancel(self, context):
        self.finish(context)
