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
    payload_version: bpy.props.IntProperty(default=0, options={'HIDDEN'})
    dab_payload: bpy.props.StringProperty(options={'HIDDEN'})
    brush_graph_data: bpy.props.StringProperty(options={'HIDDEN'})
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
    depth_limit: bpy.props.FloatProperty(name='Depth Limit', default=1.0, min=.05, max=1.0, update=touched,
        description='How far behind or in front of the stamp plane a surface may lie and still receive '
                    'paint, in radii (strokes recorded before 0.13 keep one radius)')
    blend: bpy.props.EnumProperty(name='Mode', default='PAINT', update=touched,
        items=[('PAINT', 'Paint', ''), ('ERASE', 'Erase', 'Remove earlier strokes and paint under it'),
               ('SMUDGE', 'Smudge', 'Transport accumulated paint and coverage together')])
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
    # Where the samples were placed (0.13 hardening, H2): one row per sample, the surface point
    # the pointer hit and the evaluated polygon it hit (``painting.surface_reach``), packed like
    # the samples. Empty for strokes recorded before the field existed.
    anchors: bpy.props.StringProperty(options={'HIDDEN'})
    reach: bpy.props.EnumProperty(name='Reach', default='DEPTH', update=touched,
        items=[('SURFACE', 'Surface', 'Each stamp paints the surface the pointer hit and what connects to it '
                                      'without turning away: a nearby hidden or separate surface stays clean'),
               ('DEPTH', 'Depth Band', 'Each stamp paints every surface within the depth limit of its plane, '
                                       'including surfaces behind the one the pointer hit (strokes recorded '
                                       'before 0.13.0 keep this)')],
        description='Which surfaces a stamp may reach')
    # Permission (0.13 hardening, H2): the texture sets this stroke may render on, comma separated,
    # decided when it was recorded. ``scope_version`` 0 marks a stroke recorded before the field
    # existed: every layer or mask sharing its key renders it, as it always did.
    scope: bpy.props.StringProperty(options={'HIDDEN'}, update=touched,
                                    description='Texture set ids permitted to render this stroke')
    scope_version: bpy.props.IntProperty(default=0, options={'HIDDEN'}, update=touched)


class OP_VectorBrushSettings(bpy.types.PropertyGroup):
    particles: bpy.props.BoolProperty(name='Particle Painting', default=False)
    particle_count: bpy.props.IntProperty(name='Particles per Dab', default=8, min=1, max=64)
    particle_rate: bpy.props.FloatProperty(name='Emission Rate', default=60, min=1, max=1000)
    particle_speed: bpy.props.FloatProperty(name='Speed', default=1., min=.001, max=100)
    particle_spread: bpy.props.FloatProperty(name='Spread', default=.3, min=0, max=2)
    particle_gravity: bpy.props.FloatVectorProperty(name='Gravity', size=3, default=(0, 0, -9.81))
    particle_drag: bpy.props.FloatProperty(name='Drag', default=.1, min=0, max=50)
    particle_lifetime: bpy.props.FloatProperty(name='Lifetime', default=1., min=.01, max=10)
    particle_bounce: bpy.props.FloatProperty(name='Bounce', default=0., min=0, max=1)
    node_group: bpy.props.PointerProperty(name='Brush Graph', type=bpy.types.NodeTree,
        poll=lambda self, tree: bool(tree.get('op_brush_graph')))
    symmetry: bpy.props.BoolVectorProperty(name='Symmetry', size=3, default=(False, False, False),
        description='Reflect brush samples across the active object local X, Y and Z planes')
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
        items=[('SURFACE', 'Surface', 'Each stamp lies in the plane of the surface it hit and follows it'),
               ('PLANAR', 'View Planar', 'Every stamp projects along the view direction of the gesture')])
    depth_limit: bpy.props.FloatProperty(name='Depth Limit', default=.5, min=.05, max=1.0, subtype='FACTOR',
        description='How far behind or in front of the stamp plane a surface may lie and still receive '
                    'paint, in radii: a nearby parallel surface further away than this stays clean')
    pressure_size: bpy.props.BoolProperty(name='Pressure Size', default=True,
                                          description='Tablet pressure scales the radius')
    pressure_flow: bpy.props.BoolProperty(name='Pressure Flow', default=False,
                                          description='Tablet pressure scales the flow of each stamp')
    blend: bpy.props.EnumProperty(name='Mode', default='PAINT', items=[('PAINT', 'Paint', ''), ('ERASE', 'Erase', ''),
        ('SMUDGE', 'Smudge', 'Move the enabled material channels together')])
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
    universal: bpy.props.BoolProperty(name='Paint Across Sets', default=False,
                                      description='The gesture may cross onto the surfaces of other texture sets; each '
                                                  'set it touches renders it through a linked paint layer of its own')
    reach: bpy.props.EnumProperty(name='Reach', default='SURFACE',
        items=[('SURFACE', 'Surface', 'Each stamp paints the surface under the pointer and what connects to it '
                                      'without turning away from the stamp: a nearby hidden or separate surface '
                                      'inside the brush stays clean'),
               ('DEPTH', 'Depth Band', 'Each stamp paints every surface within the depth limit of its plane, '
                                       'including hidden and separate surfaces behind or beside the one under '
                                       'the pointer')],
        description='Which surfaces a stamp may reach; Surface is the safe default for ordinary painting')
