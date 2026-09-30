"""Persistent texture effects shared by channel and adjustment-layer stacks."""
import bpy

from ..core.layer_stack import replaying
from ..core.layer_store import project_of as _project_of


def changed(self, context):
    # A Normal Combine detail map is data whatever its colorspace tag: both
    # evaluators read it as stored per sample (channel_eval.apply_effect and
    # shader_graph.Graph.effect_image), so the artist's image is never retagged
    # here; a Base Color fill of the same image keeps decoding it as colour.
    _changed(self, context, True)
    from .channel import _resolve_physical
    _resolve_physical(self)


def numeric(self, context):
    """A uniform or resource-identity edit; graph structure is unchanged."""
    _changed(self, context, False)


def _changed(self, context, structural):
    project = _project_of(self)
    if not project or replaying():
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
                serial = ts.edit_serial
                ts.edit_serial += 1
                if not structural:
                    from ..evaluation.structure_memo import numeric_edit
                    numeric_edit(ts, serial)
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
                notify(self, context, previous_dirty=previous_dirty, structural=structural)
                return


# Stable string identifiers are serialized; the menu selects these through an operator.
def physical_changed(self, context):
    from .channel import physical_changed as resolve
    resolve(self, context)


class OP_TextureEffect(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(default='Levels')
    # Only ANCHOR items carry an id: consumers reference the capture position by it.
    id: bpy.props.StringProperty()
    kind: bpy.props.StringProperty(default='LEVELS', update=changed)
    enabled: bpy.props.BoolProperty(default=True, update=changed)
    strength: bpy.props.FloatProperty(name='Opacity',default=1,min=0,max=1,update=numeric)
    radius: bpy.props.FloatProperty(default=2,min=0,max=64,update=numeric)
    # Physical scale (0.9.5): sizes in metres, resolved to texture units per asset.
    units: bpy.props.EnumProperty(name='Units', default='TEXTURE', update=physical_changed, items=[
        ('TEXTURE', 'Texture', 'Sizes in UV repeats and texels'),
        ('PHYSICAL', 'Physical', 'Sizes in world distance; the texture values follow the assigned mesh')])
    physical_radius: bpy.props.FloatProperty(name='Radius', default=.002, min=0, soft_max=1, subtype='DISTANCE',
        unit='LENGTH', update=physical_changed, description='World radius of the filter (blur, dilation, displacement)')
    feature_size: bpy.props.FloatProperty(name='Noise Feature Size', default=.05, min=1e-5, soft_max=10, subtype='DISTANCE',
        unit='LENGTH', update=physical_changed, description='World size of one cell of the driving noise')
    amount: bpy.props.FloatProperty(default=.5,min=-10,max=10,update=numeric)
    contrast: bpy.props.FloatProperty(default=1,min=0,max=10,update=numeric)
    gamma: bpy.props.FloatProperty(default=1,min=.01,max=10,update=numeric)
    black: bpy.props.FloatProperty(default=0,min=0,max=1,update=numeric)
    white: bpy.props.FloatProperty(default=1,min=0,max=1,update=numeric)
    output_black: bpy.props.FloatProperty(name='Output Black',default=0,min=0,max=1,update=numeric)
    output_white: bpy.props.FloatProperty(name='Output White',default=1,min=0,max=1,update=numeric)
    hue: bpy.props.FloatProperty(name='Hue Shift',default=0,min=-1,max=1,update=numeric,
        description='Hue rotation in turns; one third turns red to green')
    saturation: bpy.props.FloatProperty(name='Saturation',default=1,min=0,max=4,update=numeric)
    lightness: bpy.props.FloatProperty(name='Lightness',default=0,min=-1,max=1,update=numeric)
    offset: bpy.props.FloatVectorProperty(name='Offset',size=2,default=(0,0),update=numeric,
        description='Texture displacement in pixels')
    transform_scale: bpy.props.FloatVectorProperty(name='Transform Scale',size=2,default=(1,1),min=.001,update=numeric)
    softness: bpy.props.FloatProperty(name='Softness',default=.05,min=.0001,max=1,update=numeric)
    steps: bpy.props.IntProperty(name='Steps',default=4,min=2,max=256,update=numeric)
    samples: bpy.props.IntProperty(name='Samples',default=8,min=2,max=64,update=numeric)
    mode: bpy.props.EnumProperty(name='Mode',items=[('BLUR','Blur','Average neighboring samples'),
        ('MIN','Minimum','Erode using the driving slopes'),('MAX','Maximum','Expand using the driving slopes')],update=changed)
    noise: bpy.props.StringProperty(name='Driving Noise',default='FBM',update=numeric)
    matrix: bpy.props.FloatVectorProperty(name='Channel Matrix',size=9,default=(1,0,0,0,1,0,0,0,1),update=numeric)
    bias: bpy.props.FloatVectorProperty(name='Channel Offset',size=3,default=(0,0,0),update=numeric)
    image: bpy.props.PointerProperty(name='Detail Texture',type=bpy.types.Image,update=changed)
    angle: bpy.props.FloatProperty(default=0,subtype='ANGLE',update=numeric)
    # Seamless surface filters (0.9.4): neighbourhood effects follow the mesh across UV seams.
    space: bpy.props.EnumProperty(name='Space', default='SURFACE', update=changed, items=[
        ('UV', 'UV', 'Image space: fast, but seams are visible where islands end'),
        ('SURFACE', 'Surface', 'Follow the mesh: texels across a UV seam are the neighbours, and '
                               'directional effects take a world-space direction')])
    direction: bpy.props.FloatVectorProperty(name='Direction', default=(0., 0., -1.), subtype='DIRECTION', update=numeric,
        description='World-space direction of a directional blur or warp in Surface space')
    scale: bpy.props.FloatProperty(default=8,min=.01,max=512,update=numeric)
    seed: bpy.props.IntProperty(default=1,min=0,update=numeric)
    color: bpy.props.FloatVectorProperty(size=3,subtype='COLOR',default=(0,0,0),min=0,max=1,update=numeric)
    color2: bpy.props.FloatVectorProperty(size=3,subtype='COLOR',default=(1,1,1),min=0,max=1,update=numeric)
