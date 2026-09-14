"""Persistent texture effects shared by channel and adjustment-layer stacks."""
import bpy


def changed(self, context):
    project = getattr(self.id_data, 'open_painter', None)
    if not project:
        return
    pointer = self.as_pointer()
    for ts in project.texture_sets:
        for layer in ts.layers:
            channel = next((channel for channel in layer.channels
                            if any(effect.as_pointer() == pointer for effect in channel.effects)), None)
            on_mask = channel is None and any(mask.effect.as_pointer() == pointer for mask in layer.masks)
            on_layer = any(effect.as_pointer() == pointer for effect in getattr(layer, 'effects', ()))
            if channel is not None or on_mask or on_layer:
                previous_dirty = {output.channel: output.dirty for output in ts.outputs}
                layer.dirty = True
                ts.dirty = True
                ts.edit_serial += 1
                if channel is not None:
                    channel.dirty = True
                from ..evaluation.references import dependent_channels
                affected = (dependent_channels(ts, layer.id, channel.channel) | {channel.channel}
                            if channel is not None else
                            dependent_channels(ts, layer.id) |
                            {prop.channel for prop in layer.channels if prop.enabled})
                # Preserve conservative raster scheduling for legacy CPU masks
                # and layer effects; native bindings supply their exact closure.
                if channel is None and ts.preview_backend == 'CPU':
                    affected.update(output.channel for output in ts.outputs)
                for output in ts.outputs:
                    if output.channel in affected:
                        output.dirty = True
                from ..evaluation.shader_preview import notify
                notify(self, context, previous_dirty=previous_dirty)
                return


# Stable string identifiers are serialized; the menu selects these through an operator.
class OP_TextureEffect(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(default='Levels')
    # Only ANCHOR items carry an id: consumers reference the capture position by it.
    id: bpy.props.StringProperty()
    kind: bpy.props.StringProperty(default='LEVELS', update=changed)
    enabled: bpy.props.BoolProperty(default=True, update=changed)
    strength: bpy.props.FloatProperty(name='Opacity',default=1,min=0,max=1,update=changed)
    radius: bpy.props.FloatProperty(default=2,min=0,max=64,update=changed)
    amount: bpy.props.FloatProperty(default=.5,min=-10,max=10,update=changed)
    contrast: bpy.props.FloatProperty(default=1,min=0,max=10,update=changed)
    gamma: bpy.props.FloatProperty(default=1,min=.01,max=10,update=changed)
    black: bpy.props.FloatProperty(default=0,min=0,max=1,update=changed)
    white: bpy.props.FloatProperty(default=1,min=0,max=1,update=changed)
    output_black: bpy.props.FloatProperty(name='Output Black',default=0,min=0,max=1,update=changed)
    output_white: bpy.props.FloatProperty(name='Output White',default=1,min=0,max=1,update=changed)
    hue: bpy.props.FloatProperty(name='Hue Shift',default=0,min=-1,max=1,update=changed,
        description='Hue rotation in turns; one third turns red to green')
    saturation: bpy.props.FloatProperty(name='Saturation',default=1,min=0,max=4,update=changed)
    lightness: bpy.props.FloatProperty(name='Lightness',default=0,min=-1,max=1,update=changed)
    offset: bpy.props.FloatVectorProperty(name='Offset',size=2,default=(0,0),update=changed,
        description='Texture displacement in pixels')
    transform_scale: bpy.props.FloatVectorProperty(name='Transform Scale',size=2,default=(1,1),min=.001,update=changed)
    softness: bpy.props.FloatProperty(name='Softness',default=.05,min=.0001,max=1,update=changed)
    steps: bpy.props.IntProperty(name='Steps',default=4,min=2,max=256,update=changed)
    samples: bpy.props.IntProperty(name='Samples',default=8,min=2,max=64,update=changed)
    mode: bpy.props.EnumProperty(name='Mode',items=[('BLUR','Blur','Average neighboring samples'),
        ('MIN','Minimum','Erode using the driving slopes'),('MAX','Maximum','Expand using the driving slopes')],update=changed)
    noise: bpy.props.StringProperty(name='Driving Noise',default='FBM',update=changed)
    matrix: bpy.props.FloatVectorProperty(name='Channel Matrix',size=9,default=(1,0,0,0,1,0,0,0,1),update=changed)
    bias: bpy.props.FloatVectorProperty(name='Channel Offset',size=3,default=(0,0,0),update=changed)
    image: bpy.props.PointerProperty(name='Detail Texture',type=bpy.types.Image,update=changed)
    angle: bpy.props.FloatProperty(default=0,subtype='ANGLE',update=changed)
    scale: bpy.props.FloatProperty(default=8,min=.01,max=512,update=changed)
    seed: bpy.props.IntProperty(default=1,min=0,update=changed)
    color: bpy.props.FloatVectorProperty(size=3,subtype='COLOR',default=(0,0,0),min=0,max=1,update=changed)
    color2: bpy.props.FloatVectorProperty(size=3,subtype='COLOR',default=(1,1,1),min=0,max=1,update=changed)
