"""Explicit scene inputs and bounded UV-space analysis settings."""
import bpy


def changed(self, context):
    project = getattr(self.id_data, 'open_painter', None)
    if project:
        from ..core.layer_stack import invalidate
        for ts in project.texture_sets:
            if any(m.influence.as_pointer() == self.as_pointer() for layer in ts.layers for m in layer.masks):
                invalidate(ts)
                from ..evaluation.shader_preview import notify
                notify(self, context)


class OP_Influencer(bpy.types.PropertyGroup):
    kind: bpy.props.EnumProperty(name='Scene Field', update=changed, items=[
        (key, label, '') for key, label in (
            ('UP','Upward Orientation'), ('DOWN','Downward Orientation'),
            ('HEIGHT','Relative Height'), ('DISTANCE','Object Distance'),
            ('CONTACT','Contact Proximity'), ('EXPOSURE','Directional Exposure'),
            ('HEAT','Radial Heat'), ('MOISTURE','Radial Moisture'), ('SOOT','Directional Soot'),
            ('ATTRIBUTE','Named Attribute'), ('VERTEX_GROUP','Vertex Group'))])
    object: bpy.props.PointerProperty(name='Reference Object', type=bpy.types.Object, update=changed)
    collection: bpy.props.PointerProperty(name='Influencer Collection', type=bpy.types.Collection, update=changed)
    mode: bpy.props.EnumProperty(name='Analysis', default='MANUAL', update=changed,
        items=[('MANUAL','Update Manually','Retain last analysis until Update Analysis'),
               ('LIVE','Live Preview','Debounced analysis of explicit inputs'),
               ('FROZEN','Frozen','Keep the last valid analysis texture')])
    resolution: bpy.props.EnumProperty(name='Analysis Resolution', default='64', update=changed,
        items=[(str(n),f'{n} × {n}', 'UV-space sampling grid, independent of authored texture size') for n in (64,128,256)])
    distance: bpy.props.FloatProperty(name='Distance / Radius', default=.2, min=.00001, subtype='DISTANCE', update=changed)
    bounds: bpy.props.FloatVectorProperty(name='Height Bounds', size=2, default=(0,1), update=changed)
    direction: bpy.props.FloatVectorProperty(name='Direction', size=3, default=(0,0,1), update=changed)
    attribute: bpy.props.StringProperty(name='Attribute / Group', update=changed)
    update_serial: bpy.props.IntProperty()
    image: bpy.props.PointerProperty(type=bpy.types.Image)
    provenance: bpy.props.StringProperty()
    error: bpy.props.StringProperty()
