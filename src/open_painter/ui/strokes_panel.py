"""Strokes of the active layer: list, properties, edits and rasterisation."""
import bpy

from ..core.layer_stack import active, channel_for
from ..core.project_manager import require_set
from ..painting import strokes as stroke_records
from .operators import SafeOperator
from .texture_operations import _redraw


def active_strokes_mask(layer):
    """The layer's active mask when it is a Vector Strokes mask with a key of its own to edit."""
    if layer is None or not layer.masks:
        return None
    mask = layer.masks[max(0, min(layer.active_mask, len(layer.masks) - 1))]
    return mask if mask.source == 'STROKES' and mask.stroke_key else None


class OP_UL_strokes(bpy.types.UIList):
    def filter_items(self, context, data, propname):
        ts = require_set(context)
        layer = active(ts)
        key = getattr(layer, 'stroke_key', '') if layer else ''
        items = getattr(data, propname)
        # The strokes this set renders: one its scope excludes is listed where it was painted.
        flags = [self.bitflag_filter_item if item.layer_key == key and stroke_records.permits(item, ts) else 0
                 for item in items]
        return flags, []

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'name', text='', emboss=False, icon='CURVE_BEZCURVE')
        row.label(text=f'{item.sample_count}')
        row.label(text='', icon='BRUSH_DATA' if item.blend == 'PAINT' else 'X')


class OP_UL_mask_strokes(OP_UL_strokes):
    """The strokes of the active Vector Strokes mask's key (the mask section's list)."""
    def filter_items(self, context, data, propname):
        ts = require_set(context)
        mask = active_strokes_mask(active(ts))
        key = mask.stroke_key if mask is not None else ''
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item if item.layer_key == key and stroke_records.permits(item, ts) else 0
                 for item in items]
        return flags, []


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
            from ..utils.ids import new_id
            values = snapshot(stroke)
            # Later siblings move up one so the copy gets its own slot right after
            # the original (equal orders would make Move Up/Down a no-op swap).
            for sibling in project.strokes:
                if sibling.layer_key == values['layer_key'] and sibling.order > values['order']:
                    sibling.order += 1
            # add() reallocates the collection: `stroke` is stale from here on.
            copy = project.strokes.add()
            restore(values, copy)
            copy.id, copy.name = new_id(), values['name'] + ' Copy'
            copy.order = values['order'] + 1
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


def _flatten(ts, stroke, channel, n, original):
    """``original`` (straight RGBA) with this one stroke composited, as channel_rows would
    composite it; the other strokes of the key stay live records."""
    import numpy as np
    from ..painting import stroke_raster
    rows = np.array(original, np.float32, copy=True)
    alpha = stroke_raster.coverage(ts, stroke, n)[..., None]
    if stroke.blend == 'ERASE':
        rows[..., 3:4] *= 1.0 - alpha
        return rows
    color = np.asarray(stroke_records.channel_value(stroke, channel), np.float32)
    out_alpha = alpha + rows[..., 3:4] * (1.0 - alpha)
    premultiplied = color * alpha + rows[..., :3] * rows[..., 3:4] * (1.0 - alpha)
    rows[..., :3] = np.divide(premultiplied, out_alpha, out=np.zeros_like(premultiplied), where=out_alpha > 1e-8)
    rows[..., 3:4] = out_alpha
    return rows


def _rasterize(context, project, ts, stroke):
    """Flatten one stroke into the paint images of every layer rendering it (each set at its
    own resolution), then drop the record. The stroke is one project-wide record: leaving a
    linked set unflattened would erase it there."""
    import numpy as np
    from ..core import image_manager as images
    key = stroke.layer_key
    linked = stroke_records.permitted_sets(project, stroke)   # only where the stroke's scope lets it render
    if not linked:
        raise ValueError('No paint layer renders that stroke; only layer strokes can be rasterised.')
    if stroke_records.linked_masks(project, key):
        raise ValueError('A Vector Strokes mask renders this stroke; a mask cannot receive pixels. Unlink it first.')
    staged = []
    # Every target is validated and composited before any image is swapped, so a
    # size mismatch cannot leave one set flattened while the stroke stays live.
    for other, layer in linked:
        n = int(other.resolution)
        for channel in stroke_records.stroke_channels(stroke):
            prop = channel_for(layer, channel)
            if prop is None:
                continue
            original = images.pixels(prop.image) if prop.image else np.zeros((n, n, 4), np.float32)
            if original.shape != (n, n, 4):
                raise ValueError(f'{other.name}: authored paint image size differs from the texture set; resample it first')
            if prop.image and prop.image.is_float:
                # Float paint canvases store premultiplied pixels (what every reader divides
                # by alpha); the stroke composite works in straight colour.
                np.divide(original[..., :3], original[..., 3:4], out=original[..., :3], where=original[..., 3:4] > 1e-8)
                original[..., :3][original[..., 3] <= 1e-8] = 0
            rows = _flatten(other, stroke, channel, n, original)
            rows[..., :3] *= rows[..., 3:4]   # managed images are float: back to premultiplied
            staged.append((other, layer, prop, channel, n, rows))
    for other, layer, prop, channel, n, rows in staged:
        replacement = images.create(f'{other.name}_{layer.name}_{channel}', n, color_data=channel == 'BASE_COLOR')
        replacement.use_fake_user = False
        images.write(replacement, rows, pack=True)
        prop.image, prop.enabled = replacement, True
    stroke_records.remove(project, stroke.id)


def draw_strokes(layout, context, ts, layer, mask=None):
    """The strokes box of a paint layer, or of a Vector Strokes ``mask`` (its key's strokes)."""
    project = context.scene.open_painter
    if layer is None or (mask is None and layer.type != 'PAINT'):
        return
    key = mask.stroke_key if mask is not None else layer.stroke_key
    box = layout.box()
    row = box.row(align=True)
    row.label(text='Vector Strokes', icon='CURVE_BEZCURVE')
    count = len(stroke_records.strokes_for(project, key, ts))
    row.label(text=f'{count}')
    if not key:
        box.label(text='Paint with the Vector Brush to add strokes.')
        return
    box.template_list('OP_UL_mask_strokes' if mask is not None else 'OP_UL_strokes', '', project, 'strokes',
                      project, 'active_stroke', rows=3)
    row = box.row(align=True)
    for action, icon in (('MOVE_UP', 'TRIA_UP'), ('MOVE_DOWN', 'TRIA_DOWN'), ('DUPLICATE', 'DUPLICATE'),
                         ('RASTERIZE', 'IMAGE_DATA'), ('DELETE', 'TRASH')):
        row.operator('open_painter.stroke', text='', icon=icon).action = action
    index = project.active_stroke
    if 0 <= index < len(project.strokes) and project.strokes[index].layer_key == key:
        stroke = project.strokes[index]
        for name in ('radius', 'hardness', 'opacity', 'flow', 'accumulate', 'projection', 'blend'):
            box.prop(stroke, name)
        box.template_ID(stroke, 'alpha_image', open='image.open')
        for name in ('base_color', 'roughness', 'metallic', 'height'):
            box.prop(stroke, name)
        box.prop(stroke, 'reach')
        linked = stroke_records.permitted_sets(project, stroke)
        if len(linked) > 1:
            box.label(text='Also on: ' + ', '.join(other.name for other, _ in linked if other.id != ts.id), icon='LINKED')
        excluded = [other.name for other, _ in stroke_records.linked_layers(project, stroke.layer_key)
                    if not stroke_records.permits(stroke, other)]
        if excluded:
            box.label(text='Not permitted on: ' + ', '.join(excluded), icon='LOCKED')


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
                copy = images.clone(image)   # unsaved strokes travel with the copy
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


CLASSES = (OP_UL_strokes, OP_UL_mask_strokes, OP_OT_stroke, OP_OT_mask_strokes, OP_OT_resample_paint_images)
