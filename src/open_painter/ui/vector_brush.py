"""Paint Material: a modal brush whose gesture becomes one stroke record.

Samples land on the eligible assigned surfaces the pointer crosses, resampled at the
brush spacing between pointer events and re-projected onto the surface; the enabled
channels show the stroke on the model while the pointer is down (``painting.gesture``).
The stroke is data, rasterised per set and per resolution once it is committed.
"""
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from ..core.layer_stack import active
from ..core.project_manager import require_set
from ..painting import gesture
from ..utils.logging import logger

IDLE_STATUS = 'Paint Material: drag to paint · Esc exits'
GESTURE_STATUS = 'Paint Material: painting · Esc discards this stroke'


class ViewProjector:
    """Rays and pixel sizes of one 3D viewport region."""

    def __init__(self, region, view):
        self.region, self.view = region, view

    def ray(self, xy):
        from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
        region = self.region
        local = (xy[0] - region.x, xy[1] - region.y)
        if not (0 <= local[0] < region.width and 0 <= local[1] < region.height):
            return None
        return region_2d_to_origin_3d(region, self.view, local), region_2d_to_vector_3d(region, self.view, local)

    def world_per_pixel(self, xy, point):
        from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
        region = self.region
        local = (xy[0] - region.x, xy[1] - region.y)
        origin = region_2d_to_origin_3d(region, self.view, local)
        if self.view.is_perspective:
            direction = region_2d_to_vector_3d(region, self.view, local)
            side = region_2d_to_vector_3d(region, self.view, (local[0] + 1.0, local[1]))
            return max((side - direction).length * (point - origin).length, 1e-7)
        side_origin = region_2d_to_origin_3d(region, self.view, (local[0] + 1.0, local[1]))
        return max((side_origin - origin).length, 1e-7)

    def plane_normal(self):
        # The disc axis must face the viewer (the raster culls texels whose normal points away
        # from it): the view's +Z, not the direction it looks along.
        return tuple(self.view.view_rotation @ Vector((0, 0, 1)))


def material_destination(context, mask_requested=False):
    """(ts, layer, mask, reason) where the next material stroke goes, from the shared
    selection resolver (``ui.selection.resolve``): the active paint layer, or the active
    Vector Strokes mask when the selection is a mask (or the tool was opened for masks).
    ``reason`` says why nothing is paintable; the tool never falls back to another layer."""
    from . import selection
    try:
        ts = require_set(context)
    except ValueError as error:
        return None, None, None, str(error)
    target = selection.resolve(context, ts)
    layer = target.layer
    if layer is None:
        return ts, None, None, 'Select a paint layer: material strokes live on paint layers.'
    from .strokes_panel import active_strokes_mask
    strokes_mask = active_strokes_mask(layer)
    if mask_requested or target.kind == 'MASK':
        if strokes_mask is None:
            if target.kind == 'MASK' and target.mask is not None:
                return ts, layer, None, f'{target.mask.name or "The selected mask"} is not a Vector Strokes mask with a key of its own.'
            return ts, layer, None, 'Select a Vector Strokes mask (with a key of its own) to paint into.'
        _, _, mask, reason = gesture.target_state(context.scene, ts.id, layer.id, strokes_mask.id)
        return ts, layer, mask, (reason[0].upper() + reason[1:] + '.') if reason else ''
    _, _, _, reason = gesture.target_state(context.scene, ts.id, layer.id)
    if reason:
        return ts, layer, None, reason[0].upper() + reason[1:] + '.'
    return ts, layer, None, ''


class OP_OT_vector_brush(bpy.types.Operator):
    """Paint the enabled material channels on the assigned surfaces; Esc discards a stroke in progress and exits when idle"""
    bl_idname = 'open_painter.vector_brush'
    bl_label = 'Paint Material'
    bl_options = {'REGISTER', 'UNDO'}
    mask: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'},
                                 description='Record into the active Vector Strokes mask\'s key instead of the layer\'s')
    _handler = None
    _active = None      # the running operator, for handlers that must end its gesture

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        # The tool opens on a paintable destination; every pointer-down resolves it again.
        ts, layer, mask, reason = material_destination(context, self.mask)
        if reason:
            self.report({'ERROR'}, reason)
            return {'CANCELLED'}
        project = context.scene.open_painter
        region = next(region for region in context.area.regions if region.type == 'WINDOW')
        self._projector = ViewProjector(region, context.space_data.region_3d)
        self._view = context.space_data.region_3d.as_pointer()   # the view the rays come from
        self._gesture = None    # built at pointer-down from the selection of that moment
        self._batch = None
        OP_OT_vector_brush._handler = bpy.types.SpaceView3D.draw_handler_add(self._draw, (context,), 'WINDOW', 'POST_VIEW')
        OP_OT_vector_brush._active = self
        context.window.cursor_modal_set('PAINT_BRUSH')
        context.window_manager.modal_handler_add(self)
        project.status = IDLE_STATUS
        return {'RUNNING_MODAL'}

    @property
    def painting(self):
        return self._gesture is not None and self._gesture.active

    def _finish(self, context):
        if self.painting:
            self._end(context, commit=False, reason='the tool closed')
        if OP_OT_vector_brush._handler is not None:
            bpy.types.SpaceView3D.draw_handler_remove(OP_OT_vector_brush._handler, 'WINDOW')
            OP_OT_vector_brush._handler = None
        if OP_OT_vector_brush._active is self:
            OP_OT_vector_brush._active = None
        try:
            context.window.cursor_modal_restore()
            context.scene.open_painter.status = ''
            context.area.tag_redraw()
        except (AttributeError, ReferenceError):
            pass

    def cancel(self, context):
        self._finish(context)

    @staticmethod
    def _attributes(event):
        pressure = float(event.pressure) if event.is_tablet else 1.0
        tilt = tuple(event.tilt) if hasattr(event, 'tilt') else (0.0, 0.0)
        return (pressure, float(tilt[0]), float(tilt[1]), 1.0 if event.is_tablet else 0.0)

    def _end(self, context, commit, reason='', push=True):
        new_source = self._gesture is not None and self._gesture.new_source
        ts_id = self._gesture.ts_id if self._gesture is not None else None
        stroke = self._gesture.end(commit, reason) if self._gesture is not None else None
        self._batch = None
        if stroke is not None and push:
            from ..evaluation import shader_preview
            if new_source:
                # The first stroke on a layer adds a source unit to the live shader graph.
                # Build that structural change now, before the undo push, so the pushed step
                # captures a consistent graph. Left to the next timer tick it lands after the
                # push; a following undo then cannot revert the node trees the rebuild created
                # and leaves the material's Procedural Graph pointing at freed data — a hard
                # crash on the next depsgraph build (0.13 hardening, H4).
                try:
                    ts = next((t for t in context.scene.open_painter.texture_sets if t.id == ts_id), None)
                    if ts is not None and shader_preview.requested(ts):
                        shader_preview.ensure(context.scene, ts)
                except Exception:
                    logger.exception('Paint Material: could not flush the shader graph before the undo step')
            bpy.ops.ed.undo_push(message='Paint Material')
        elif self._gesture is not None and self._gesture.reason and self._gesture.reason != 'nothing painted':
            self.report({'INFO'}, f'Stroke discarded: {self._gesture.reason}')
        try:
            context.scene.open_painter.status = IDLE_STATUS
            context.area.tag_redraw()
        except (AttributeError, ReferenceError):
            pass
        return stroke

    def _begin(self, context, event, xy):
        """Pointer-down: resolve the selection now, capture the target, the scope and the brush
        for this gesture alone, and start it at the surface under the pointer."""
        # An authored gesture is starting and will push its own undo step: leave the post-undo
        # window where new leaf datablocks are withheld, so this stroke's boundaries build normally
        # and are captured by the push (see cache.on_undo; docs/quality-stability-0.13.md, S2).
        from ..evaluation.shader_resources import freeze_new_leaves
        freeze_new_leaves(False)
        ts, layer, mask, reason = material_destination(context, self.mask)
        if reason:
            self.report({'WARNING'}, reason)
            return {'RUNNING_MODAL'}
        project = context.scene.open_painter
        settings = project.vector_brush
        scope = gesture.Scope.of(project, ts, across_sets=bool(settings.universal))
        # A Vector Strokes mask with its own key collects the strokes itself (any layer type).
        pending = gesture.Gesture(context.scene, ts, layer, settings, scope, self._projector,
                                  key=mask.stroke_key if mask is not None else '',
                                  mask_id=mask.id if mask is not None else '')
        hit = pending.hit(xy)
        if hit is None:
            return {'PASS_THROUGH'}
        if not pending.start(xy, self._attributes(event), hit, self._projector.plane_normal()):
            self.report({'WARNING'}, f'Nothing painted: {pending.reason or "the target is not paintable"}.')
            return {'RUNNING_MODAL'}
        self._gesture = pending
        project.status = GESTURE_STATUS
        self._batch = None
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def _check(self, context):
        """A change that invalidates the captured target ends the gesture without a record:
        the selection moved to another destination, the destination went away or hid, or
        the assigned geometry changed. The next pointer-down starts over from the new state."""
        reason = self._gesture.invalidated()
        if not reason:
            ts, layer, mask, _ = material_destination(context, self.mask)
            now = (ts.id if ts is not None else '', layer.id if layer is not None else '', mask.id if mask is not None else '')
            if now != (self._gesture.ts_id, self._gesture.layer_id, self._gesture.mask_id):
                reason = 'the target changed'
        if reason:
            self._end(context, commit=False, reason=reason)
            return True
        return False

    def _displaced(self, context):
        """The area the tool opened in is no longer the 3D view its rays come from (re-typed,
        closed, or its view replaced): the tool ends rather than paint from a stale view."""
        area, space = getattr(context, 'area', None), getattr(context, 'space_data', None)
        view = getattr(space, 'region_3d', None) if space is not None else None
        try:
            return area is None or area.type != 'VIEW_3D' or view is None or view.as_pointer() != self._view
        except ReferenceError:
            return True

    def modal(self, context, event):
        if self._displaced(context):
            self._finish(context)
            return {'FINISHED'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            if self.painting:
                self._end(context, commit=False, reason='cancelled')
                return {'RUNNING_MODAL'}
            self._finish(context)
            return {'FINISHED'}
        xy = (event.mouse_x, event.mouse_y)
        if not self.painting:
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                return self._begin(context, event, xy)
            return {'PASS_THROUGH'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            if self._check(context):
                return {'RUNNING_MODAL'}
            self._gesture.advance(xy, self._attributes(event))
            self._batch = None
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if self._check(context):
                return {'RUNNING_MODAL'}
            self._gesture.advance(xy, self._attributes(event))
            self._end(context, commit=True)
            return {'RUNNING_MODAL'}
        if event.value == 'PRESS' and (event.ctrl or event.oskey or event.alt):
            # An accelerator during a gesture (save, undo…): the partial stroke commits
            # once and the gesture ends, then the shortcut runs; the release commits nothing.
            self._end(context, commit=True)
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    # ---- guide line -----------------------------------------------------------------------------

    def _draw(self, context):
        if self._gesture is None:
            return
        segments = [segment for segment in self._gesture.segments if len(segment) > 1]
        if not segments:
            return
        if self._batch is None:
            shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
            coords, indices, offset = [], [], 0
            for segment in segments:
                coords.extend(tuple(p) for p in segment)
                indices.extend((offset + i, offset + i + 1) for i in range(len(segment) - 1))
                offset += len(segment)
            self._batch = (shader, batch_for_shader(shader, 'LINES', {'pos': coords}, indices=indices))
        shader, batch = self._batch
        depth = gpu.state.depth_test_get()
        try:
            gpu.state.depth_test_set('LESS_EQUAL')
            shader.bind()
            shader.uniform_float('viewportSize', gpu.state.viewport_get()[2:])
            shader.uniform_float('lineWidth', 2.0)
            shader.uniform_float('color', (1.0, .6, .2, 1.0))
            batch.draw(shader)
        finally:
            gpu.state.depth_test_set(depth)


def interrupt(commit, reason):
    """End the running gesture from outside its event loop (a save, a file load, undo)."""
    running = OP_OT_vector_brush._active
    if running is None:
        return False
    try:
        if running.painting:
            # No undo push from inside an application handler: the save or load that called
            # this is the step the artist sees.
            running._end(bpy.context, commit=commit, reason=reason, push=False)
    except Exception:
        logger.exception('Paint Material: ending the gesture on %s failed', reason)
    return True


def _on_save(_):
    # The documented rule: a save commits the partial gesture once and ends it; the file
    # never stores a preview image in place of a layer's input.
    interrupt(commit=True, reason='saving')
    gesture.discard_all(unbind=True)


def _on_load_or_undo(*_):
    interrupt(commit=False, reason='the file changed')
    # The data is about to be replaced: nothing is unbound by hand under the undo system.
    gesture.discard_all(unbind=False)
    if OP_OT_vector_brush._handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(OP_OT_vector_brush._handler, 'WINDOW')
        except ValueError:
            pass
        OP_OT_vector_brush._handler = None
    OP_OT_vector_brush._active = None


def _after_undo(*_):
    # A restored step may reference preview images its nodes have since moved on from.
    gesture.sweep()


_HANDLERS = (('save_pre', _on_save), ('load_pre', _on_load_or_undo), ('undo_pre', _on_load_or_undo),
             ('redo_pre', _on_load_or_undo), ('undo_post', _after_undo), ('redo_post', _after_undo))


def register():
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn not in handlers:
            handlers.append(fn)


def unregister():
    _on_load_or_undo()
    gesture.release_all()
    for name, fn in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn in handlers:
            handlers.remove(fn)


def destination(context):
    """'Body > Mud Paint | Color, Roughness, Metallic, Height': where the next stroke goes —
    the current selection while the tool is idle, the captured target while a gesture runs
    (what the renderer is writing), or why nothing is paintable."""
    from ..constants import CHANNEL_ITEMS
    running = OP_OT_vector_brush._active
    if running is not None and running.painting:
        return running._gesture.describe(context.scene.open_painter)
    project = context.scene.open_painter
    ts, layer, mask, reason = material_destination(context, running.mask if running is not None else False)
    if reason:
        return reason
    settings = project.vector_brush
    labels = {key: label for key, label, *_ in CHANNEL_ITEMS}
    channels = [settings.channels] if isinstance(settings.channels, str) else sorted(settings.channels)
    names = ', '.join(labels.get(c, c) for c in channels if c in labels) or 'no channels'
    target = f'{ts.name} > {layer.name}' if layer is not None else ts.name
    if mask is not None:
        target += f' > {mask.name or "Vector Strokes mask"}'
    return f'{target} | {names}'


PRIMARY_CHANNELS = (('BASE_COLOR', 'Color', 'base_color'), ('ROUGHNESS', 'Roughness', 'roughness'),
                    ('METALLIC', 'Metallic', 'metallic'), ('HEIGHT', 'Height', 'height'))


def draw_material_brush(layout, context):
    """The everyday material brush: destination, size, opacity, flow, hardness, spacing,
    alpha, channel toggles and the values of the enabled channels."""
    project = context.scene.open_painter
    settings = project.vector_brush
    running = OP_OT_vector_brush._active is not None
    box = layout.box()
    row = box.row(align=True)
    row.label(text=destination(context), icon='MATERIAL')
    row = box.row(align=True)
    row.operator('open_painter.vector_brush', text='Painting Material · Esc exits' if running else 'Paint Material',
                 icon='BRUSH_DATA', depress=running)
    if running:
        row.enabled = False
    column = box.column(align=True)
    sub = column.row(align=True)
    sub.prop(settings, 'radius_mode', expand=True)
    column.prop(settings, 'screen_radius' if settings.radius_mode == 'SCREEN' else 'radius', text='Size')
    for name in ('opacity', 'flow', 'hardness', 'spacing'):
        column.prop(settings, name, slider=True)
    box.template_ID(settings, 'alpha_image', open='image.open', text='Alpha')
    row = box.row(align=True)
    row.prop(settings, 'blend', expand=True)
    grid = box.grid_flow(columns=2, align=True, even_columns=True)
    for key, label, _ in PRIMARY_CHANNELS:
        grid.prop_enum(settings, 'channels', key, text=label)
    enabled = {settings.channels} if isinstance(settings.channels, str) else set(settings.channels)
    values = box.column(align=True)
    for key, label, field in PRIMARY_CHANNELS:
        if key in enabled:
            values.prop(settings, field, text=label, slider=field != 'base_color')
    extra = sorted(enabled - {key for key, *_ in PRIMARY_CHANNELS})
    if extra:
        box.label(text='Also: ' + ', '.join(extra), icon='INFO')
    scope = box.column(align=True)
    row = scope.row(align=True)
    for index, axis in enumerate('XYZ'):
        row.prop(settings, 'symmetry', index=index, text=axis, toggle=True)
    scope.prop(settings, 'reach')
    scope.template_ID(settings, 'node_group')
    scope.operator('open_painter.brush_graph', text='Edit Brush Graph', icon='NODETREE')
    scope.prop(settings, 'particles')
    if settings.particles:
        for name in ('particle_count', 'particle_rate', 'particle_speed', 'particle_spread', 'particle_gravity',
                     'particle_drag', 'particle_lifetime', 'particle_bounce'):
            scope.prop(settings, name)
    scope.prop(settings, 'universal')
    if settings.universal:
        from ..core.project_manager import require_set
        try:
            ts = require_set(context)
        except ValueError:
            ts = None
        if ts is not None:
            names = gesture.Scope.of(project, ts, across_sets=True).names(project)
            scope.label(text='Sets the stroke may reach: ' + ', '.join(names), icon='OUTLINER_COLLECTION')
    row = box.row(align=True)
    row.prop(settings, 'pressure_size', toggle=True)
    row.prop(settings, 'pressure_flow', toggle=True)


def draw_vector_brush(layout, context):
    """Advanced stroke controls, below the ordinary brush controls."""
    settings = context.scene.open_painter.vector_brush
    box = layout.box()
    box.label(text='Stroke Details', icon='CURVE_BEZCURVE')
    box.prop(settings, 'accumulate')
    box.prop(settings, 'projection')
    box.prop(settings, 'depth_limit', slider=True)
    row = box.row(align=True)
    row.prop(settings, 'jitter', slider=True)
    row.prop(settings, 'seed')
    more = sorted(key for key, *_ in PRIMARY_CHANNELS)
    box.label(text='Other channels', icon='MATERIAL')
    grid = box.grid_flow(columns=2, align=True, even_columns=True)
    for key in ('AO', 'EMISSION', 'OPACITY', 'NORMAL'):
        grid.prop_enum(settings, 'channels', key)
    enabled = {settings.channels} if isinstance(settings.channels, str) else set(settings.channels)
    for key, field in (('AO', 'ao'), ('EMISSION', 'emission'), ('OPACITY', 'opacity_value'), ('NORMAL', 'normal')):
        if key in enabled:
            box.prop(settings, field)


CLASSES = (OP_OT_vector_brush,)
