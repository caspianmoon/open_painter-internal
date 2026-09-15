"""Scene field presets: a few live-geometry recipes composed on the mask stack."""
import bpy

from ..core.project_manager import require_set
from ..core.layer_stack import active
from ..utils.ids import new_id
from .operators import SafeOperator
from .texture_operations import _redraw

# (label, description, [(kind, operation, settings)]) — stacked masks combine live.
PRESETS = {
    'CREVICE_DIRT': ('Dirt in crevices near other parts',
                     'Up-facing × close to any other mesh × concave',
                     [('UP', 'NORMAL', {}),
                      ('PROXIMITY', 'MULTIPLY', {'scope': 'SCENE', 'distance': .05, 'falloff': 'SMOOTH'}),
                      ('CONCAVITY', 'MULTIPLY', {'distance': .02})]),
    'RADIAL_RUST': ('Radial rust from bolts',
                    'Proximity to a collection of small parts with a smooth falloff',
                    [('PROXIMITY', 'NORMAL', {'distance': .03, 'falloff': 'SMOOTH'})]),
    'GROUND_MUD': ('Ground mud',
                   'Low on the object and not facing up',
                   [('GROUND', 'NORMAL', {'distance': .3, 'falloff': 'SQUARED'}),
                    ('DOWN', 'MAX', {}),
                    ('UP', 'MIN', {'falloff': 'LINEAR'})]),
    'LIVE_EDGES': ('Live edge exposure',
                   'Convexity and creases measured on the evaluated mesh',
                   [('EDGE', 'NORMAL', {'distance': .02, 'resolution': '512'})]),
}


class OP_OT_scene_field_preset(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.scene_field_preset'
    bl_label = 'Add Scene Field Preset'
    bl_description = 'Add live scene-field masks to the selected layer'
    bl_options = {'REGISTER', 'UNDO'}
    preset: bpy.props.EnumProperty(items=[(key, label, description) for key, (label, description, _) in PRESETS.items()])

    def run(self, context):
        ts = require_set(context)
        layer = active(ts)
        if layer is None:
            raise ValueError('Select a layer first.')
        _, _, terms = PRESETS[self.preset]
        for kind, operation, settings in terms:
            mask = layer.masks.add()
            mask.id, mask.source, mask.name = new_id(), 'SCENE', kind.replace('_', ' ').title()
            mask.operation = operation
            field = mask.influence
            field.kind, field.mode = kind, 'LIVE'
            if 'resolution' not in settings:
                field.resolution = '256'
            for key, value in settings.items():
                setattr(field, key, value)
        layer.active_mask = len(layer.masks) - 1
        _redraw(context, ts, mask=True)


class OP_MT_scene_field_presets(bpy.types.Menu):
    bl_idname = 'OP_MT_scene_field_presets'
    bl_label = 'Scene Field Presets'

    def draw(self, context):
        for key, (label, _, _) in PRESETS.items():
            self.layout.operator('open_painter.scene_field_preset', text=label).preset = key


CLASSES = (OP_OT_scene_field_preset, OP_MT_scene_field_presets)
