"""Vector strokes: brush gestures kept as data and rasterised on demand.

A stroke belongs to the project, not to a texture set: its ``layer_key`` links
the paint layers (one per set) that render it, so one gesture across several
sets stays one editable stroke. Samples are packed float32 rows
(``painting.stroke_raster.SAMPLE_WIDTH``) in a string: one RNA write, one undo
blob, invisible to the evaluators' parameter walks.
"""
import bpy

from ..constants import CHANNEL_ITEMS


def touched(self, context):
    from ..painting.strokes import touch
    touch(self.id_data.open_painter, self)


class OP_Stroke(bpy.types.PropertyGroup):
    id: bpy.props.StringProperty()
    name: bpy.props.StringProperty(default='Stroke')
    layer_key: bpy.props.StringProperty(description='Links the paint layers (one per set) rendering this stroke')
    revision: bpy.props.IntProperty(default=0)
    order: bpy.props.IntProperty(default=0)
    selected: bpy.props.BoolProperty(default=False)
    # Brush
    radius: bpy.props.FloatProperty(name='Radius', default=.05, min=.0001, max=10, subtype='DISTANCE', update=touched,
                                    description='World radius at pressure 1; per-sample radii are stored with the samples')
    hardness: bpy.props.FloatProperty(name='Hardness', default=.8, min=0, max=1, update=touched)
    opacity: bpy.props.FloatProperty(name='Opacity', default=1, min=0, max=1, update=touched)
    flow: bpy.props.FloatProperty(name='Flow', default=1, min=0, max=1, update=touched)
    accumulate: bpy.props.EnumProperty(name='Accumulate', default='MAX', update=touched,
        items=[('MAX', 'Max', 'A stroke never darkens where it overlaps itself'),
               ('FLOW', 'Flow', 'Overlapping samples build up')])
    alpha_image: bpy.props.PointerProperty(name='Alpha', type=bpy.types.Image, update=touched)
    alpha_rotation: bpy.props.EnumProperty(name='Alpha Rotation', default='RAKE', update=touched,
        items=[('RAKE', 'Rake', 'Follow the stroke direction'), ('FIXED', 'Fixed', 'World-aligned'),])
    alpha_angle: bpy.props.FloatProperty(name='Alpha Angle', default=0, subtype='ANGLE', update=touched)
    projection: bpy.props.EnumProperty(name='Projection', default='SURFACE', update=touched,
        items=[('SURFACE', 'Surface', 'Each sample stamps in the plane of the surface it hit'),
               ('PLANAR', 'Planar', 'Every sample stamps along one direction (the view when recorded)')])
    plane_normal: bpy.props.FloatVectorProperty(size=3, default=(0, 0, 1), update=touched)
    blend: bpy.props.EnumProperty(name='Mode', default='PAINT', update=touched,
        items=[('PAINT', 'Paint', ''), ('ERASE', 'Erase', 'Remove earlier strokes and paint under it')])
    # Material
    channels: bpy.props.StringProperty(default='BASE_COLOR,ROUGHNESS,METALLIC,HEIGHT', update=touched,
                                       description='Channels this stroke writes (comma separated)')
    base_color: bpy.props.FloatVectorProperty(name='Base Color', size=4, subtype='COLOR', default=(.5, .5, .5, 1),
                                              min=0, max=1, update=touched)
    roughness: bpy.props.FloatProperty(name='Roughness', default=.5, min=0, max=1, update=touched)
    metallic: bpy.props.FloatProperty(name='Metallic', default=0, min=0, max=1, update=touched)
    height: bpy.props.FloatProperty(name='Height', default=.5, min=0, max=1, update=touched)
    ao: bpy.props.FloatProperty(name='AO', default=1, min=0, max=1, update=touched)
    emission: bpy.props.FloatVectorProperty(name='Emission', size=4, subtype='COLOR', default=(0, 0, 0, 1),
                                            min=0, max=1, update=touched)
    opacity_value: bpy.props.FloatProperty(name='Opacity Value', default=1, min=0, max=1, update=touched)
    normal: bpy.props.FloatVectorProperty(name='Normal', size=4, subtype='COLOR', default=(.5, .5, 1, 1),
                                          min=0, max=1, update=touched)
    # Samples
    samples: bpy.props.StringProperty(options={'HIDDEN'})
    sample_count: bpy.props.IntProperty(default=0)
    bounds_min: bpy.props.FloatVectorProperty(size=3)
    bounds_max: bpy.props.FloatVectorProperty(size=3)


class OP_VectorBrushSettings(bpy.types.PropertyGroup):
    radius: bpy.props.FloatProperty(name='Radius', default=.05, min=.0001, max=10, subtype='DISTANCE')
    radius_mode: bpy.props.EnumProperty(name='Radius', default='WORLD',
        items=[('WORLD', 'World', 'Radius in scene units'), ('SCREEN', 'Screen', 'Radius in pixels, resolved at the hit')])
    screen_radius: bpy.props.FloatProperty(name='Screen Radius', default=30, min=1, max=500)
    hardness: bpy.props.FloatProperty(name='Hardness', default=.8, min=0, max=1)
    opacity: bpy.props.FloatProperty(name='Opacity', default=1, min=0, max=1)
    flow: bpy.props.FloatProperty(name='Flow', default=1, min=0, max=1)
    spacing: bpy.props.FloatProperty(name='Spacing', default=.25, min=.02, max=2,
                                     description='Distance between samples as a fraction of the radius')
    jitter: bpy.props.FloatProperty(name='Jitter', default=0, min=0, max=1)
    seed: bpy.props.IntProperty(name='Seed', default=1, min=0)
    accumulate: bpy.props.EnumProperty(name='Accumulate', default='MAX',
        items=[('MAX', 'Max', ''), ('FLOW', 'Flow', '')])
    alpha_image: bpy.props.PointerProperty(name='Alpha', type=bpy.types.Image)
    projection: bpy.props.EnumProperty(name='Projection', default='SURFACE',
        items=[('SURFACE', 'Surface', ''), ('PLANAR', 'View Planar', '')])
    blend: bpy.props.EnumProperty(name='Mode', default='PAINT', items=[('PAINT', 'Paint', ''), ('ERASE', 'Erase', '')])
    channels: bpy.props.EnumProperty(name='Channels', options={'ENUM_FLAG'}, items=[(key, label, '') for key, label, _ in CHANNEL_ITEMS],
                                     default={'BASE_COLOR', 'ROUGHNESS', 'METALLIC', 'HEIGHT'})
    base_color: bpy.props.FloatVectorProperty(name='Base Color', size=4, subtype='COLOR', default=(.5, .5, .5, 1), min=0, max=1)
    roughness: bpy.props.FloatProperty(name='Roughness', default=.5, min=0, max=1)
    metallic: bpy.props.FloatProperty(name='Metallic', default=0, min=0, max=1)
    height: bpy.props.FloatProperty(name='Height', default=.5, min=0, max=1)
    ao: bpy.props.FloatProperty(name='AO', default=1, min=0, max=1)
    emission: bpy.props.FloatVectorProperty(name='Emission', size=4, subtype='COLOR', default=(0, 0, 0, 1), min=0, max=1)
    opacity_value: bpy.props.FloatProperty(name='Opacity Value', default=1, min=0, max=1)
    normal: bpy.props.FloatVectorProperty(name='Normal', size=4, subtype='COLOR', default=(.5, .5, 1, 1), min=0, max=1)
    universal: bpy.props.BoolProperty(name='Paint Across Sets', default=True,
                                      description='A stroke that reaches other texture sets creates linked paint layers there')
