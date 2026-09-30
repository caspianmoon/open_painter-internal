"""Persistent text settings and the last successfully generated appearance."""
import bpy


def changed(self, context):
    from ..core.layer_stack import replaying
    if replaying():
        return
    owner = self.id_data
    if isinstance(owner, bpy.types.NodeTree):
        from ..procedural_graph.edits import values_changed
        values_changed(owner)
        return
    from ..core.layer_store import project_of
    project = project_of(self)   # a mask's text settings live in its set's layer store
    if project is not None:
        from .mask import changed as mask_changed
        for ts in project.texture_sets:
            for layer in ts.layers:
                for mask in layer.masks:
                    if mask.text == self:
                        mask_changed(mask, context)


class OP_TextSettings(bpy.types.PropertyGroup):
    schema_version: bpy.props.IntProperty(default=1, options={'HIDDEN'})
    content: bpy.props.StringProperty(name='Text', default='Text', update=changed,
        description='Text content; line breaks are preserved (\\n also starts a new line)')
    font: bpy.props.StringProperty(name='Font', subtype='FILE_PATH', update=changed,
        description='Optional font file; an unavailable font keeps the saved mask')
    size: bpy.props.FloatProperty(name='Size', default=.15, min=.001, max=2, update=changed,
        description='Font size as a fraction of texture height')
    alignment: bpy.props.EnumProperty(name='Alignment', items=[('LEFT', 'Left', ''),
        ('CENTER', 'Center', ''), ('RIGHT', 'Right', '')], default='CENTER', update=changed)
    spacing: bpy.props.FloatProperty(name='Character Spacing', default=0, min=-1, max=5, update=changed)
    line_spacing: bpy.props.FloatProperty(name='Line Spacing', default=1.2, min=.1, max=5, update=changed)
    offset: bpy.props.FloatVectorProperty(name='Position', size=2, default=(.5, .5), update=changed)
    image: bpy.props.PointerProperty(type=bpy.types.Image)
    raster_signature: bpy.props.StringProperty(options={'HIDDEN'})
