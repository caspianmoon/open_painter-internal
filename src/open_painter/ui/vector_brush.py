"""Vector Brush: a modal stroke recorder. Samples land on whatever assigned surface
the pointer crosses; the stroke is data, rasterised per set and per resolution."""
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from ..core.layer_stack import active
from ..core.project_manager import require_set
from ..painting import strokes as stroke_records
from ..painting.stroke_raster import SAMPLE_WIDTH
from .operators import SafeOperator


class OP_OT_vector_brush(bpy.types.Operator):
    """Paint vector strokes on the assigned surfaces; Esc or right-click finishes"""
    bl_idname = 'open_painter.vector_brush'
    bl_label = 'Vector Brush'
    bl_options = {'REGISTER', 'UNDO'}
    mask: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'},
                                 description='Record into the active Vector Strokes mask\'s key instead of the layer\'s')
    _handler = None

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        from ..core.layer_stack import channel_for
        try:
            ts = require_set(context)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        layer = active(ts)
        from .strokes_panel import active_strokes_mask
        mask = active_strokes_mask(layer) if self.mask else None
        if mask is None and (layer is None or layer.type != 'PAINT'):
            self.report({'ERROR'}, 'Select a paint layer: vector strokes live on paint layers.')
            return {'CANCELLED'}
        self._set_id, self._layer_id = ts.id, layer.id
        # A Vector Strokes mask with its own key collects the strokes itself (any layer type).
        self._key = mask.stroke_key if mask is not None else ''
        self._region = next(region for region in context.area.regions if region.type == 'WINDOW')
        self._view = context.space_data.region_3d
        self._points, self._samples, self._last = [], [], None
        self._painting = False
        self._batch = None
        self._sets = {(ref.object.as_pointer(), ref.slot_index): item.id
                      for item in context.scene.open_painter.texture_sets for ref in item.assignments if ref.object}
        OP_OT_vector_brush._handler = bpy.types.SpaceView3D.draw_handler_add(self._draw, (context,), 'WINDOW', 'POST_VIEW')
        context.window.cursor_modal_set('PAINT_BRUSH')
        context.window_manager.modal_handler_add(self)
        context.scene.open_painter.status = 'Vector Brush: drag to paint · Esc finishes'
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        if OP_OT_vector_brush._handler is not None:
            bpy.types.SpaceView3D.draw_handler_remove(OP_OT_vector_brush._handler, 'WINDOW')
            OP_OT_vector_brush._handler = None
        context.window.cursor_modal_restore()
        context.scene.open_painter.status = ''
        context.area.tag_redraw()

    def cancel(self, context):
        self._finish(context)

    def _hit(self, context, event):
        from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
        region = self._region
        xy = (event.mouse_x - region.x, event.mouse_y - region.y)
        if not (0 <= xy[0] < region.width and 0 <= xy[1] < region.height):
            return None
        origin = region_2d_to_origin_3d(region, self._view, xy)
        direction = region_2d_to_vector_3d(region, self._view, xy)
        hit, point, normal, _, obj, _ = context.scene.ray_cast(context.evaluated_depsgraph_get(), origin, direction)
        if not hit:
            return None
        original = obj.original
        if not any(key[0] == original.as_pointer() for key in self._sets):
            return None   # not an assigned surface: the stroke lifts here
        return Vector(point), Vector(normal).normalized(), direction

    def _radius(self, context, settings, event, point, direction):
        if settings.radius_mode == 'WORLD':
            return settings.radius
        from bpy_extras.view3d_utils import region_2d_to_origin_3d
        # Screen radius resolved at the hit depth: the world size of `screen_radius` pixels there.
        region = self._region
        xy = (event.mouse_x - region.x, event.mouse_y - region.y)
        a = region_2d_to_origin_3d(region, self._view, xy)
        depth = (point - a).length
        from bpy_extras.view3d_utils import region_2d_to_vector_3d
        side = region_2d_to_vector_3d(region, self._view, (xy[0] + settings.screen_radius, xy[1]))
        return max((side - direction).length * depth, 1e-5)

    def modal(self, context, event):
        settings = context.scene.open_painter.vector_brush
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            if self._painting:
                self._commit(context, settings)
            self._finish(context)
            return {'FINISHED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            hit = self._hit(context, event)
            if hit is None:
                return {'PASS_THROUGH'}
            self._painting = True
            self._points, self._samples, self._last = [], [], None
            self._add_sample(context, settings, event, hit)
            return {'RUNNING_MODAL'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} and self._painting:
            hit = self._hit(context, event)
            if hit is not None:
                self._add_sample(context, settings, event, hit)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self._painting:
            self._painting = False
            self._commit(context, settings)
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}

    def _add_sample(self, context, settings, event, hit):
        point, normal, direction = hit
        pressure = event.pressure if event.is_tablet else 1.0
        radius = self._radius(context, settings, event, point, direction) * (1.0 if not event.is_tablet else max(pressure, .05))
        if self._last is not None and (point - self._last).length < settings.spacing * radius:
            return
        tangent = (point - self._last) if self._last is not None else Vector((0, 0, 0))
        if tangent.length > 1e-9:
            tangent.normalize()
        if settings.jitter:
            rng = np.random.default_rng(settings.seed + len(self._samples))
            offset = Vector(rng.normal(0, settings.jitter * radius * .5, 3))
            offset -= normal * offset.dot(normal)
            point = point + offset
        tilt = tuple(event.tilt) if hasattr(event, 'tilt') else (0.0, 0.0)
        self._samples.append([*point, *normal, pressure, radius, tilt[0], tilt[1], *tangent])
        self._points.append(point.copy())
        self._last = point
        self._batch = None

    def _commit(self, context, settings):
        if not self._samples:
            return
        project = context.scene.open_painter
        ts = next((item for item in project.texture_sets if item.id == self._set_id), None)
        layer = next((item for item in ts.layers if item.id == self._layer_id), None) if ts else None
        if ts is None or layer is None:
            return
        samples = np.asarray(self._samples, np.float32).reshape(-1, SAMPLE_WIDTH)
        # The disc axis must face the viewer (the raster culls texels whose normal points away
        # from it): the view's +Z, not the direction it looks along.
        plane = tuple(self._view.view_rotation @ Vector((0, 0, 1))) if settings.projection == 'PLANAR' else (0, 0, 1)
        stroke_records.record(project, ts, layer, samples, settings, plane_normal=plane, universal=settings.universal,
                              key=self._key)
        self._samples, self._points, self._batch = [], [], None
        bpy.ops.ed.undo_push(message='Vector Stroke')
        context.area.tag_redraw()

    def _draw(self, context):
        if not self._points:
            return
        if self._batch is None:
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            coords = [tuple(p) for p in self._points]
            self._batch = (shader, batch_for_shader(shader, 'LINE_STRIP', {'pos': coords}))
        shader, batch = self._batch
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.line_width_set(2.0)
        shader.bind()
        shader.uniform_float('color', (1.0, .6, .2, 1.0))
        batch.draw(shader)
        gpu.state.depth_test_set('NONE')
        gpu.state.line_width_set(1.0)


def draw_vector_brush(layout, context):
    settings = context.scene.open_painter.vector_brush
    box = layout.box()
    box.label(text='Vector Brush', icon='CURVE_BEZCURVE')
    box.operator('open_painter.vector_brush', text='Paint Vector Strokes', icon='BRUSH_DATA')
    row = box.row(align=True)
    row.prop(settings, 'radius_mode', expand=True)
    box.prop(settings, 'screen_radius' if settings.radius_mode == 'SCREEN' else 'radius')
    for name in ('hardness', 'opacity', 'flow', 'spacing', 'jitter'):
        box.prop(settings, name, slider=True)
    box.prop(settings, 'accumulate')
    box.prop(settings, 'projection')
    box.prop(settings, 'blend')
    box.template_ID(settings, 'alpha_image', open='image.open')
    box.prop(settings, 'universal')
    box.label(text='Material', icon='MATERIAL')
    box.prop(settings, 'channels')
    for name in ('base_color', 'roughness', 'metallic', 'height'):
        box.prop(settings, name)


CLASSES = (OP_OT_vector_brush,)
