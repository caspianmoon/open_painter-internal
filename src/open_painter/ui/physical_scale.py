"""Physical scale UI (0.9.5): metre-valued sizes with their resolved texture values."""
import bpy

from ..core.project_manager import active_set, require_set
from .operators import SafeOperator


def draw_size(layout, ts, owner, texture_field, physical_field, text):
    """Units toggle plus the metre field (Physical) or the texture field, with the resolved readout."""
    from ..evaluation import physical
    row = layout.row(align=True)
    row.prop(owner, 'units', expand=True)
    if owner.units == 'PHYSICAL':
        layout.prop(owner, physical_field, text=text)
        derived = physical.expected(owner, ts)
        if texture_field in derived:
            unit = 'repeats' if texture_field in {'procedural_scale', 'noise_scale', 'scale'} else 'texels'
            layout.label(text=f'= {derived[texture_field]:.2f} {unit} on this mesh', icon='DRIVER_DISTANCE')
        elif physical.density(ts) is None:
            layout.label(text='Assign a mesh to resolve physical sizes', icon='ERROR')
    else:
        layout.prop(owner, texture_field)


class OP_OT_physical_scale(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.physical_scale'
    bl_label = 'Physical Scale'
    bl_options = {'REGISTER', 'UNDO'}
    action: bpy.props.EnumProperty(items=[
        ('LOCK', 'Lock Sizes to Metres', 'Convert every procedural scale and filter radius of the set to world sizes so they stay the same on any asset'),
        ('UNLOCK', 'Back to Texture Units', 'Keep the current look but author sizes in repeats and texels again'),
        ('SYNC', 'Resolve Now', 'Re-derive the texture values from the metre sizes for the current mesh')], default='LOCK')

    @classmethod
    def description(cls, context, properties):
        return {'LOCK': 'Convert every procedural scale and filter radius of the set to world sizes',
                'UNLOCK': 'Author sizes in repeats and texels again, keeping the current look',
                'SYNC': 'Re-derive the texture values from the metre sizes for the current mesh'}[properties.action]

    def run(self, context):
        from ..evaluation import physical
        ts = require_set(context)
        if self.action == 'LOCK':
            count = physical.lock(ts)
            context.scene.open_painter.status = f'{count} size(s) now authored in metres · {physical.describe(ts)}'
        elif self.action == 'UNLOCK':
            count = physical.unlock(ts)
            context.scene.open_painter.status = f'{count} size(s) back to texture units'
        else:
            physical.forget(ts)
            count = physical.sync(ts)
            context.scene.open_painter.status = f'Resolved {count} value(s) · {physical.describe(ts)}'


def draw_set_scale(layout, context, ts):
    from ..evaluation import physical
    box = layout.box()
    box.prop(ts, "physical_space_status")
    if ts.physical_space_status == "KNOWN":
        box.prop(ts, "meters_per_world_unit")
        box.prop(ts, "physical_space_provenance")
        if not ts.physical_space_provenance.strip():
            box.label(text="Declare measurement or source provenance", icon="ERROR")
    else:
        box.label(text="Millimeter surface controls need a size declaration", icon="INFO")
    row = layout.row(align=True)
    row.label(text=physical.describe(ts), icon='DRIVER_DISTANCE')
    row.operator('open_painter.physical_scale', text='', icon='LOCKED' if not physical.uses(ts) else 'FILE_REFRESH').action = \
        'LOCK' if not physical.uses(ts) else 'SYNC'


CLASSES = (OP_OT_physical_scale,)
