"""Bake Correction Brush UI (0.9.1): paint per-region bake fixes instead of editing cages."""
import bpy

from ..core.project_manager import active_set, require_set
from ..properties.bake import CORRECTION_KINDS
from .operators import SafeOperator


ACTIONS = [
    ('BRUSH', 'Bake Correction Brush', 'Paint the selected correction on the assigned mesh with the native brush'),
    ('STOP', 'Finish', 'End the correction session'),
    ('REBAKE_REGION', 'Rebake Painted Region', 'Rebake only the faces whose corrections changed since the last bake'),
    ('REBAKE_ALL', 'Rebake Normal + AO', 'Bake the Normal and AO maps again with every correction applied'),
    ('CLEAR', 'Clear This Correction', 'Reset the selected correction map'),
    ('CLEAR_ALL', 'Clear All Corrections', 'Remove every correction map of the texture set'),
    ('OVERLAY', 'Refresh Cage Overlay', 'Rebuild the corrected-cage wire overlay'),
]


class OP_OT_bake_correction(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.bake_correction'
    bl_label = 'Bake Correction'
    bl_options = {'REGISTER', 'UNDO'}
    action: bpy.props.EnumProperty(items=ACTIONS, default='BRUSH')
    kind: bpy.props.EnumProperty(items=CORRECTION_KINDS, default='OFFSET')

    @classmethod
    def description(cls, context, properties):
        if properties.action == 'BRUSH':
            return {k: d for k, _, d in CORRECTION_KINDS}.get(properties.kind, '')
        return {k: d for k, _, d in ACTIONS}.get(properties.action, '')

    def run(self, context):
        from ..baking import corrections
        ts = require_set(context)
        scene = context.scene
        settings = ts.bake.corrections
        if self.action == 'BRUSH':
            image = corrections.begin_brush(context, ts, self.kind)
            scene.open_painter.status = (f'Painting {corrections.LABELS[self.kind]} on {image.name} · '
                                         'strokes rebake their region when they end')
        elif self.action == 'STOP':
            corrections.end_brush(context)
            scene.open_painter.status = 'Bake correction session ended'
        elif self.action == 'REBAKE_REGION':
            result = corrections.rebake_region(context, ts)
            faces = sum(max(v, 0) for v in result.values())
            full = [k for k, v in result.items() if v < 0]
            scene.open_painter.status = (f'Rebaked {faces} face(s)' + (f'; full bake of {", ".join(full)}' if full else '')
                                         if result else 'No Normal or AO map selected to rebake')
        elif self.action == 'REBAKE_ALL':
            from ..baking.bake_manager import register_map
            from ..baking.cycles_baker import bake
            from ..core.layer_stack import invalidate
            kinds = [k for k in ('NORMAL', 'AO') if getattr(ts.bake, k.lower())]
            if not kinds:
                raise ValueError('Select Normal or AO in the bake settings first')
            context.window_manager.progress_begin(0, len(kinds))
            try:
                for index, kind in enumerate(kinds):
                    register_map(ts, kind, bake(context, ts, kind))
                    context.window_manager.progress_update(index + 1)
            finally:
                context.window_manager.progress_end()
            invalidate(ts)
            scene.open_painter.status = f'Rebaked {", ".join(kinds)} with corrections'
        elif self.action == 'CLEAR':
            if scene.open_painter.paint_correction == self.kind:
                corrections.end_brush(context)
            corrections.clear(ts, self.kind)
            scene.open_painter.status = f'Cleared {corrections.LABELS[self.kind]}'
        elif self.action == 'CLEAR_ALL':
            corrections.end_brush(context)
            corrections.clear(ts)
            scene.open_painter.status = 'Cleared every bake correction'
        elif self.action == 'OVERLAY':
            corrections.update_overlay(context, ts)
        if context.area is not None:
            context.area.tag_redraw()


def draw_corrections(layout, context, ts):
    """Collapsible Bake Corrections section for the bake panel."""
    from ..baking import corrections
    settings = ts.bake.corrections
    project = context.scene.open_painter
    header, panel = layout.panel('painter_bake_corrections', default_closed=True)
    active_keys = corrections.active_keys(ts)
    header.label(text='Bake Corrections' + (f' · {len(active_keys)} painted' if active_keys else ''), icon='BRUSH_DATA')
    if not panel:
        return
    painting = project.paint_correction
    panel.prop(settings, 'active', text='Correction')
    if painting:
        row = panel.row(align=True)
        row.label(text=f'Painting {corrections.LABELS[painting]}', icon='BRUSH_DATA')
        row.operator('open_painter.bake_correction', text='Finish', icon='CHECKMARK').action = 'STOP'
    else:
        op = panel.operator('open_painter.bake_correction', text='Bake Correction Brush', icon='BRUSH_DATA')
        op.action, op.kind = 'BRUSH', settings.active
    if settings.active == 'OFFSET':
        panel.prop(settings, 'value', text='Projection Distance')
        panel.prop(settings, 'offset_range')
    elif settings.active == 'DIRECTION':
        panel.prop(settings, 'value', text='Weight', slider=True)
        panel.prop(settings, 'direction_vector')
    elif settings.active == 'IGNORE':
        panel.prop(settings, 'value', text='Weight', slider=True)
        ignorable = [ref for ref in ts.bake.high_objects if ref.object and ref.ignorable]
        panel.label(text=f'{len(ignorable)} source(s) marked Ignore Brush Excludes' if ignorable
                    else 'Mark sources with Ignore Brush Excludes below', icon='INFO' if ignorable else 'ERROR')
        for ref in ts.bake.high_objects:
            if ref.object:
                panel.prop(ref, 'ignorable', text=ref.object.name)
    elif settings.active == 'AO_EXCLUDE':
        panel.prop(settings, 'value', text='Weight', slider=True)
        if ts.bake.ao_scope != 'SET':
            panel.label(text='AO Occluders is already Individual Parts: nothing to exclude', icon='INFO')
    else:
        panel.prop(settings, 'value', text='Weight', slider=True)
    panel.prop(settings, 'live_rebake')
    panel.prop(settings, 'show_cage')
    row = panel.row(align=True)
    row.operator('open_painter.bake_correction', text='Rebake Region', icon='FILE_REFRESH').action = 'REBAKE_REGION'
    row.operator('open_painter.bake_correction', text='Rebake All', icon='RENDER_STILL').action = 'REBAKE_ALL'
    row = panel.row(align=True)
    op = row.operator('open_painter.bake_correction', text='Clear', icon='X')
    op.action, op.kind = 'CLEAR', settings.active
    row.operator('open_painter.bake_correction', text='Clear All').action = 'CLEAR_ALL'
    if active_keys:
        panel.label(text='Painted: ' + ', '.join(corrections.LABELS[k] for k in active_keys), icon='CHECKMARK')


class OP_PT_native_bake_correction(bpy.types.Panel):
    """Brush controls while a correction session runs, in the Painter sidebar."""
    bl_label = 'Bake Correction Brush'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Painter'
    bl_order = 3

    @classmethod
    def poll(cls, context):
        from . import editor
        return editor.is_area(context.area) and bool(context.scene.open_painter.paint_correction)

    def draw(self, context):
        ts = active_set(context)
        if ts is None:
            return
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        draw_corrections(layout, context, ts)
        from ..painting.brush_bridge import draw
        draw(layout, context)


CLASSES = (OP_OT_bake_correction, OP_PT_native_bake_correction)
