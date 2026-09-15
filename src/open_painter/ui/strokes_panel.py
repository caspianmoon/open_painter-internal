"""Strokes of the active layer: list, properties, edits and rasterisation."""
import bpy

from ..core.layer_stack import active, channel_for
from ..core.project_manager import require_set
from ..painting import strokes as stroke_records
from .operators import SafeOperator
from .texture_operations import _redraw


class OP_UL_strokes(bpy.types.UIList):
    def filter_items(self, context, data, propname):
        ts = require_set(context)
        layer = active(ts)
        key = getattr(layer, 'stroke_key', '') if layer else ''
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item if item.layer_key == key else 0 for item in items]
        return flags, []

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'name', text='', emboss=False, icon='CURVE_BEZCURVE')
        row.label(text=f'{item.sample_count}')
        row.label(text='', icon='BRUSH_DATA' if item.blend == 'PAINT' else 'X')


class OP_OT_stroke(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.stroke'
    bl_label = 'Stroke'
    bl_options = {'REGISTER', 'UNDO'}
    action: bpy.props.EnumProperty(items=[(key, key.title().replace('_', ' '), '') for key in
                                          ('DELETE', 'DUPLICATE', 'MOVE_UP', 'MOVE_DOWN', 'RESEED', 'RASTERIZE', 'PURGE_ORPHANS')])

    def run(self, context):
        project = context.scene.open_painter
        ts = require_set(context)
        if self.action == 'PURGE_ORPHANS':
            count = stroke_records.purge_orphans(project)
            self.report({'INFO'}, f'Removed {count} orphan strokes')
            _redraw(context, ts)
            return
        index = project.active_stroke
        if not 0 <= index < len(project.strokes):
            raise ValueError('Select a stroke first.')
        stroke = project.strokes[index]
        if self.action == 'DELETE':
            stroke_records.remove(project, stroke.id)
            project.active_stroke = min(index, len(project.strokes) - 1)
        elif self.action == 'DUPLICATE':
            from ..core.layer_stack import snapshot, restore
            values = snapshot(stroke)
            copy = project.strokes.add()
            restore(values, copy)
            from ..utils.ids import new_id
            copy.id, copy.name = new_id(), stroke.name + ' Copy'
            copy.order = stroke.order + 1
            stroke_records.touch(project, copy)
            project.active_stroke = len(project.strokes) - 1
        elif self.action in {'MOVE_UP', 'MOVE_DOWN'}:
            siblings = stroke_records.strokes_for(project, stroke.layer_key)
            position = next(i for i, s in enumerate(siblings) if s.id == stroke.id)
            target = position + (1 if self.action == 'MOVE_UP' else -1)
            if 0 <= target < len(siblings):
                other = siblings[target]
                stroke.order, other.order = other.order, stroke.order
                stroke_records.touch(project, stroke)
        elif self.action == 'RESEED':
            stroke_records.touch(project, stroke)
        elif self.action == 'RASTERIZE':
            _rasterize(context, project, ts, stroke)
        _redraw(context, ts)


class OP_OT_mask_strokes(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.mask_strokes'
    bl_label = 'Mask Strokes'
    bl_description = 'Choose which vector strokes a Vector Strokes mask renders'
    bl_options = {'REGISTER', 'UNDO'}
    action: bpy.props.EnumProperty(items=[('OWN', "This Layer's Strokes", 'The strokes painted on this layer'),
                                          ('NEW', 'New Key', 'A fresh key: paint into it with the Vector Brush on this mask'),
                                          ('CLEAR', 'Clear', '')])

    def run(self, context):
        ts = require_set(context)
        layer = active(ts)
        if layer is None or not layer.masks:
            raise ValueError('Select a Vector Strokes mask first.')
        mask = layer.masks[max(0, min(layer.active_mask, len(layer.masks) - 1))]
        if self.action == 'OWN':
            mask.stroke_key = stroke_records.ensure_key(layer) if layer.type == 'PAINT' else layer.stroke_key
            if not mask.stroke_key:
                raise ValueError('Only paint layers own strokes; pick New Key for a fill layer.')
        elif self.action == 'NEW':
            from ..utils.ids import new_id
            mask.stroke_key = new_id()
        else:
            mask.stroke_key = ''
        _redraw(context, ts, mask=True)


def _rasterize(context, project, ts, stroke):
    """Flatten one stroke into the linked paint layer's images of this set, then drop it."""
    import numpy as np
    from ..core import image_manager as images
    from ..painting import stroke_raster
    layer = next((item for item in ts.layers if item.stroke_key == stroke.layer_key), None)
    if layer is None:
        raise ValueError('This set has no layer rendering that stroke.')
    n = int(ts.resolution)
    for channel in stroke_records.stroke_channels(stroke):
        prop = channel_for(layer, channel)
        if prop is None:
            continue
        original = images.pixels(prop.image) if prop.image else np.zeros((n, n, 4), np.float32)
        if original.shape != (n, n, 4):
            raise ValueError('Authored paint image size differs from the texture set; resample it first')
        rows = stroke_raster.channel_rows(ts, layer, channel, n, 0, n, base=original)
        replacement = images.create(f'{ts.name}_{layer.name}_{channel}', n, color_data=channel == 'BASE_COLOR')
        replacement.use_fake_user = False
        images.write(replacement, rows, pack=True)
        prop.image, prop.enabled = replacement, True
    stroke_records.remove(project, stroke.id)


def draw_strokes(layout, context, ts, layer):
    project = context.scene.open_painter
    if layer is None or layer.type != 'PAINT':
        return
    box = layout.box()
    row = box.row(align=True)
    row.label(text='Vector Strokes', icon='CURVE_BEZCURVE')
    count = len(stroke_records.strokes_for(project, layer.stroke_key))
    row.label(text=f'{count}')
    if not layer.stroke_key:
        box.label(text='Paint with the Vector Brush to add strokes.')
        return
    box.template_list('OP_UL_strokes', '', project, 'strokes', project, 'active_stroke', rows=3)
    row = box.row(align=True)
    for action, icon in (('MOVE_UP', 'TRIA_UP'), ('MOVE_DOWN', 'TRIA_DOWN'), ('DUPLICATE', 'DUPLICATE'),
                         ('RASTERIZE', 'IMAGE_DATA'), ('DELETE', 'TRASH')):
        row.operator('open_painter.stroke', text='', icon=icon).action = action
    index = project.active_stroke
    if 0 <= index < len(project.strokes) and project.strokes[index].layer_key == layer.stroke_key:
        stroke = project.strokes[index]
        for name in ('radius', 'hardness', 'opacity', 'flow', 'accumulate', 'projection', 'blend'):
            box.prop(stroke, name)
        box.template_ID(stroke, 'alpha_image', open='image.open')
        for name in ('base_color', 'roughness', 'metallic', 'height'):
            box.prop(stroke, name)
        linked = stroke_records.linked_layers(project, stroke.layer_key)
        if len(linked) > 1:
            box.label(text='Also on: ' + ', '.join(other.name for other, _ in linked if other.id != ts.id), icon='LINKED')


class OP_OT_resample_paint_images(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.resample_paint_images'
    bl_label = 'Resample Paint Images'
    bl_description = ('Rescale this set\'s painted images to the Output Resolution (destructive; strokes, '
                      'fills, procedurals and masks already re-render at any resolution)')
    bl_options = {'REGISTER', 'UNDO'}

    def run(self, context):
        from ..core import image_manager as images
        ts = require_set(context)
        n = int(ts.resolution)
        count = 0
        for layer in ts.layers:
            items = [prop for prop in layer.channels if layer.type == 'PAINT'] + [m for m in layer.masks if m.source == 'PAINT']
            for item in items:
                image = item.image
                if image is None or tuple(image.size) == (n, n):
                    continue
                copy = image.copy()
                copy.scale(n, n)
                copy.pack()
                item.image = copy
                count += 1
        from ..core.layer_stack import invalidate
        invalidate(ts)
        self.report({'INFO'}, f'Resampled {count} paint images to {n}')


def pending_resample(ts):
    n = int(ts.resolution)
    return sum(1 for layer in ts.layers
               for item in ([prop for prop in layer.channels if layer.type == 'PAINT'] + [m for m in layer.masks if m.source == 'PAINT'])
               if item.image is not None and tuple(item.image.size) != (n, n))


CLASSES = (OP_UL_strokes, OP_OT_stroke, OP_OT_mask_strokes, OP_OT_resample_paint_images)
