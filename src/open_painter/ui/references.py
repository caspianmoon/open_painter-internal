"""Native searchable source selection for stable layer references."""
import bpy
from ..core.project_manager import active_set, require_set
from ..core.layer_stack import active, invalidate

_items = []


def sources(self, context):
    global _items
    ts = active_set(context) if context else None
    _items = [(layer.id,layer.name,'Published mask or channel') for layer in ts.layers if layer != active(ts)] if ts else []
    return _items


class OP_OT_reference_source(bpy.types.Operator):
    bl_idname = 'open_painter.reference_source'
    bl_label = 'Choose Published Source'
    bl_options = {'REGISTER','UNDO'}
    bl_property = 'source'
    source: bpy.props.EnumProperty(items=sources)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        ts = require_set(context)
        layer = active(ts)
        mask = layer.masks[layer.active_mask]
        old = mask.reference_layer
        mask.reference_layer = self.source
        from ..evaluation.references import issues
        errors = issues(ts)
        if errors:
            mask.reference_layer = old
            self.report({'ERROR'},' | '.join(errors))
            return {'CANCELLED'}
        invalidate(ts)
        return {'FINISHED'}
