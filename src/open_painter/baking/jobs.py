"""Map-boundary queue. A running Cycles call remains blocking."""
import bpy


class OP_OT_bake_queue(bpy.types.Operator):
    bl_idname = 'open_painter.bake_queue'
    bl_label = 'Bake Selected Maps'
    bl_description = 'Queue maps; Esc or Stop ends the queue between maps. Each Cycles map blocks while baking'
    bl_options = {'REGISTER', 'UNDO'}
    _timer = None
    _job = None
    _last_timer_duration = 0.
    _set_id = ''
    _set_pointer = 0
    running_set_id = ''      # the set a live queue serves ('' when idle)
    stop_requested = False   # set by open_painter.bake_stop; honoured at the next map boundary

    def _set(self, context):
        """The captured set, or None when the sets changed underneath the queue."""
        ts = next((item for item in context.scene.open_painter.texture_sets if item.id == self._set_id), None)
        if ts is None or ts.as_pointer() != self._set_pointer:
            return None
        return ts

    def finish(self, context):
        job, timer = self._job, self._timer
        self._job = self._timer = None
        self._last_timer_duration = 0.
        OP_OT_bake_queue.running_set_id = ''
        OP_OT_bake_queue.stop_requested = False
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
        from . import history
        if OP_OT_bake_queue.running_set_id:
            self.report({'ERROR'}, 'A bake queue is already running')
            return {'CANCELLED'}
        try:
            # The target is captured now, by id and address: whatever the artist selects
            # while the queue runs, the maps land on this set or nowhere.
            ts = require_set(context)
            self._set_id, self._set_pointer = ts.id, ts.as_pointer()
            self._job = bake_steps(context, ts)
            kind = next(self._job)
            OP_OT_bake_queue.running_set_id = ts.id
            OP_OT_bake_queue.stop_requested = False
            context.scene.open_painter.status = f'Baking {kind} · Esc or Stop ends the queue after this map'
            self._timer = context.window_manager.event_timer_add(.1, window=context.window)
            self._last_timer_duration = self._timer.time_duration
            context.window_manager.modal_handler_add(self)
        except Exception as exc:
            self.finish(context)
            ts = self._set(context)
            if ts is not None:
                history.finish(ts, error=str(exc))
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        from . import history
        if event.type == 'ESC' or OP_OT_bake_queue.stop_requested:
            ts = self._set(context)
            self.finish(context)
            if ts is not None:
                history.finish(ts, stopped=True)
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
        ts = self._set(context)
        if ts is None:
            self.finish(context)
            context.scene.open_painter.status = 'Bake stopped: the texture sets changed; completed maps retained'
            return {'FINISHED'}
        try:
            kind = next(self._job)
            context.scene.open_painter.status = f'Baking {kind} · Esc or Stop ends the queue after this map'
        except StopIteration:
            self.finish(context)
            history.finish(ts)
            context.scene.open_painter.status = 'Mesh maps complete'
            return {'FINISHED'}
        except Exception as exc:
            self.finish(context)
            history.finish(ts, error=str(exc))
            self.report({'ERROR'}, str(exc))
            context.scene.open_painter.status = f'Bake failed: {exc}'
            return {'FINISHED'}  # Completed maps still belong to this undo step.
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self.finish(context)


class OP_OT_bake_stop(bpy.types.Operator):
    bl_idname = 'open_painter.bake_stop'
    bl_label = 'Stop After This Map'
    bl_description = ('End the bake queue at the next map boundary; a Cycles map that is baking cannot be '
                      'interrupted and completed maps are kept')
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(OP_OT_bake_queue.running_set_id)

    def execute(self, context):
        OP_OT_bake_queue.stop_requested = True
        context.scene.open_painter.status = 'Stopping after the current map…'
        return {'FINISHED'}


def land_edge_maps(scene_name, set_id, expected, produced):
    """Register a finished background edge-map bake on the set it was started
    for and return the status text.

    The set is resolved by id inside the scene recorded at invoke: the window
    may show another scene by the time the job lands. The landed maps get their
    own undo step; the modal operator itself is not an UNDO operator, so
    without one the next Ctrl+Z after an unrelated edit would return to the
    memfile written before the maps existed.
    """
    from ..core.layer_stack import invalidate
    from ..geometry.provenance import record
    from .bake_manager import register_maps
    scene = bpy.data.scenes.get(scene_name)
    ts = (next((item for item in scene.open_painter.texture_sets if item.id == set_id), None)
          if scene is not None else None)
    if ts is None or record(ts, 'CURVATURE') != expected:
        from ..core.image_manager import live_bake_pointers
        live = live_bake_pointers(ts) if ts is not None else set()
        for image in produced.values():
            try:
                if image.as_pointer() in live:
                    continue  # defensive: never remove a registered image
                bpy.data.images.remove(image)
            except ReferenceError:
                pass
        status = ('Scene removed; edge map rebake discarded' if scene is None
                  else 'Bake inputs changed; edge map rebake discarded')
        if scene is not None:
            scene.open_painter.status = status
        return status
    register_maps(ts, produced)
    invalidate(ts)
    status = scene.open_painter.status = 'Edge maps rebaked: thin, normal, wide and extreme reaches'
    bpy.ops.ed.undo_push(message='Rebake Edge Maps')
    return status


class OP_OT_edge_map_refresh(bpy.types.Operator):
    """Rebake the Curvature edge map cooperatively so the interface stays responsive."""
    bl_idname = 'open_painter.edge_map_refresh'
    bl_label = 'Rebake Edge Map (Background)'
    bl_options = {'REGISTER', 'INTERNAL'}
    if hasattr(bpy, 'props'):   # the pure unit tests load this module with a stub bpy
        # SKIP_SAVE: a finished modal stores its properties as the type's last-used
        # values, which a panel button would otherwise inherit from the auto rebake.
        set_id: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'},
                                         description='Texture set to rebake; the active set when empty')
    else:
        set_id = ''
    running = False
    running_set_id = ''   # the set the live job serves: a global flag alone cannot tell sets apart
    _timer = None
    _job = None
    _set_id = ''
    _scene_name = ''
    _expected = ''
    _last_timer_duration = 0.

    def _status(self, context, text):
        scene = bpy.data.scenes.get(self._scene_name)
        (context.scene if scene is None else scene).open_painter.status = text

    def finish(self, context):
        job, timer = self._job, self._timer
        self._job = self._timer = None
        OP_OT_edge_map_refresh.running = False
        OP_OT_edge_map_refresh.running_set_id = ''
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
            self._scene_name = context.scene.name
            self._expected = record(ts, 'CURVATURE')
            self._job = curvature_steps(context, ts)
            next(self._job)  # captures bake inputs before the first modal step
            self._status(context, 'Rebaking edge maps… 2% · Esc stops')
            self._timer = context.window_manager.event_timer_add(.02, window=context.window)
            self._last_timer_duration = self._timer.time_duration
            context.window_manager.modal_handler_add(self)
            OP_OT_edge_map_refresh.running = True
            OP_OT_edge_map_refresh.running_set_id = ts.id
        except Exception as exc:
            self.finish(context)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self.finish(context)
            self._status(context, 'Edge map rebake stopped; the previous map is kept')
            return {'FINISHED'}
        if event.type != 'TIMER' or self._timer is None:
            return {'PASS_THROUGH'}
        duration = self._timer.time_duration
        if duration <= self._last_timer_duration:
            return {'PASS_THROUGH'}
        self._last_timer_duration = duration
        try:
            fraction = next(self._job)
            self._status(context, f'Rebaking edge maps… {int(fraction * 100)}% · Esc stops')
        except StopIteration as done:
            produced = done.value
            self.finish(context)
            land_edge_maps(self._scene_name, self._set_id, self._expected, produced)
            for area in context.screen.areas:
                area.tag_redraw()
            return {'FINISHED'}
        except Exception as exc:
            self.finish(context)
            self.report({'ERROR'}, str(exc))
            self._status(context, f'Edge map rebake failed: {exc}')
            return {'FINISHED'}
        return {'PASS_THROUGH'}

    def cancel(self, context):
        self.finish(context)
