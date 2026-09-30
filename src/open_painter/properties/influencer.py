"""Explicit scene inputs and bounded UV-space analysis settings."""
import bpy

from ..core.layer_stack import replaying
from ..core.layer_store import project_of as _project_of


def changed(self, context):
    project = _project_of(self)
    if project and not replaying():
        from ..core.layer_stack import invalidate
        for ts in project.texture_sets:
            if any(m.influence.as_pointer() == self.as_pointer() for layer in ts.layers for m in layer.masks):
                invalidate(ts)
                from ..evaluation.shader_preview import notify
                notify(self, context)


class OP_Influencer(bpy.types.PropertyGroup):
    physical_mapping_version: bpy.props.IntProperty(default=0, min=0, max=1, update=changed)
    physical_meters_per_unit: bpy.props.FloatProperty(default=1, min=1e-12, update=changed)
    physical_provenance: bpy.props.StringProperty(description='Source of the explicit metre conversion')
    distance_m: bpy.props.FloatProperty(name='Distance / Radius (m)', min=1e-9, precision=6,
        get=lambda self: self.distance * self.physical_meters_per_unit if self.physical_mapping_version else 0,
        set=lambda self, value: setattr(self, 'distance', value / self.physical_meters_per_unit))
    bounds_m: bpy.props.FloatVectorProperty(name='Height Bounds (m)', size=2, precision=6,
        get=lambda self: tuple(v * self.physical_meters_per_unit for v in self.bounds) if self.physical_mapping_version else (0, 0),
        set=lambda self, value: setattr(self, 'bounds', tuple(v / self.physical_meters_per_unit for v in value)))
    kind: bpy.props.EnumProperty(name='Scene Field', update=changed, items=[
        (key, label, '') for key, label in (
            ('UP','Upward Orientation'), ('DOWN','Downward Orientation'),
            ('HEIGHT','Relative Height'), ('DISTANCE','Object Distance'),
            ('CONTACT','Contact Proximity'), ('EXPOSURE','Directional Exposure'),
            ('HEAT','Radial Heat'), ('MOISTURE','Radial Moisture'), ('SOOT','Directional Soot'),
            ('ATTRIBUTE','Named Attribute'), ('VERTEX_GROUP','Vertex Group'),
            # 0.5.0 live kinds (appended: enum indices stay stable)
            ('CONVEXITY','Convexity (live)'), ('CONCAVITY','Concavity (live)'), ('EDGE','Edge Exposure (live)'),
            ('GROUND','Distance to Ground'), ('WORLD_HEIGHT','World Height'), ('PROXIMITY','Proximity'))])
    scope: bpy.props.EnumProperty(name='Influencers', default='EXPLICIT', update=changed,
        items=[('EXPLICIT','Chosen Object / Collection','Only the referenced object or collection'),
               ('SCENE','Every Other Mesh','Every visible mesh in the scene except this set\'s targets and exclusions')])
    exclude_collection: bpy.props.PointerProperty(name='Exclude', type=bpy.types.Collection, update=changed)
    max_objects: bpy.props.IntProperty(name='Max Objects', default=64, min=1, max=256, update=changed)
    falloff: bpy.props.EnumProperty(name='Falloff', default='LINEAR', update=changed,
        items=[('LINEAR','Linear',''), ('SMOOTH','Smooth','Smoothstep'), ('SQUARED','Squared','Fast near, soft far'),
               ('ROOT','Root','Soft near, fast far')])
    ground_object: bpy.props.PointerProperty(name='Ground', type=bpy.types.Object, update=changed,
        description='Object whose origin height is the ground plane (world Z = 0 when empty)')
    object: bpy.props.PointerProperty(name='Reference Object', type=bpy.types.Object, update=changed)
    collection: bpy.props.PointerProperty(name='Influencer Collection', type=bpy.types.Collection, update=changed)
    mode: bpy.props.EnumProperty(name='Analysis', default='MANUAL', update=changed,
        items=[('MANUAL','Update Manually','Retain last analysis until Update Analysis'),
               ('LIVE','Live Preview','Debounced analysis of explicit inputs'),
               ('FROZEN','Frozen','Keep the last valid analysis texture')])
    resolution: bpy.props.EnumProperty(name='Analysis Resolution', default='64', update=changed,
        items=[(str(n),f'{n} × {n}', 'UV-space sampling grid, independent of authored texture size') for n in (64,128,256,512,1024)])
    distance: bpy.props.FloatProperty(name='Distance / Radius', default=.2, min=.00001, subtype='DISTANCE', update=changed)
    bounds: bpy.props.FloatVectorProperty(name='Height Bounds', size=2, default=(0,1), update=changed)
    direction: bpy.props.FloatVectorProperty(name='Direction', size=3, default=(0,0,1), update=changed)
    attribute: bpy.props.StringProperty(name='Attribute / Group', update=changed)
    update_serial: bpy.props.IntProperty()
    image: bpy.props.PointerProperty(type=bpy.types.Image)
    provenance: bpy.props.StringProperty()
    error: bpy.props.StringProperty()
