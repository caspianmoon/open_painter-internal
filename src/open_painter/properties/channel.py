import bpy

from ..constants import BLEND_ITEMS, CHANNEL_ITEMS
from .effect import OP_TextureEffect


def changed(self, context):
    _changed(self, context, True)


def numeric(self, context):
    """A uniform or resource-identity edit; graph structure is unchanged."""
    _changed(self, context, False)


def _changed(self, context, structural):
    self.dirty = True
    # Resolve by stable ID, without coupling properties to UI selection.
    for ts in self.id_data.open_painter.texture_sets:
        for layer in ts.layers:
            if layer.id == self.layer_id:
                previous_dirty = {output.channel: output.dirty for output in ts.outputs}
                layer.dirty = True
                ts.dirty = True
                ts.edit_serial += 1
                from ..evaluation.references import dependent_channels
                affected = dependent_channels(ts,layer.id,self.channel) | {self.channel}
                for output in ts.outputs:
                    if output.channel in affected:
                        output.dirty = True
                from ..evaluation.shader_preview import notify
                notify(self, context, previous_dirty=previous_dirty, structural=structural)
                return


def image_changed(self, context):
    self.image_name = self.image.name if self.image else ""
    changed(self, context)


def detail_image_changed(self, context):
    self.detail_image_name = self.detail_image.name if self.detail_image else ""
    changed(self, context)


class OP_Channel(bpy.types.PropertyGroup):
    channel: bpy.props.EnumProperty(items=CHANNEL_ITEMS)
    layer_id: bpy.props.StringProperty()
    enabled: bpy.props.BoolProperty(default=True, update=changed)
    opacity: bpy.props.FloatProperty(
        name="Channel Opacity",
        default=1,
        min=0,
        max=1,
        update=numeric,
        description="Multiplied by the layer's overall opacity",
    )
    opacity_percent: bpy.props.IntProperty(
        name="Opacity",
        description="Opacity of this channel, in percent",
        min=0,
        max=100,
        get=lambda self: int(round(self.opacity * 100)),
        set=lambda self, value: setattr(self, "opacity", value / 100),
    )
    blend_mode: bpy.props.EnumProperty(
        name="Channel Blend",
        update=changed,
        items=[("INHERIT", "Use Layer Blend", "Preserve the layer's blend mode")] + BLEND_ITEMS,
    )
    color: bpy.props.FloatVectorProperty(
        name="Color",
        size=4,
        subtype="COLOR",
        min=0,
        max=1,
        default=(0.5, 0.5, 0.5, 1),
        update=numeric,
    )
    value: bpy.props.FloatProperty(name="Value", default=0.5, min=0, max=1, update=numeric)
    # Fill content instantiated from an anchor point; replaces image/color/value.
    anchor: bpy.props.StringProperty(name="Anchor", update=changed,
        description="Anchor point whose published result becomes this channel's content")
    procedural: bpy.props.StringProperty(default='',update=changed)
    procedural_scale: bpy.props.FloatProperty(name='Scale',default=8,min=.01,max=512,update=numeric)
    procedural_seed: bpy.props.IntProperty(name='Seed',default=1,min=0,update=numeric)
    procedural_detail: bpy.props.FloatProperty(name='Variation',default=.15,min=0,max=2,update=numeric)
    procedural_height: bpy.props.FloatProperty(name='Height Amplitude',default=.02,min=-1,max=1,update=numeric)
    effects: bpy.props.CollectionProperty(type=OP_TextureEffect)
    active_effect: bpy.props.IntProperty()
    image_name: bpy.props.StringProperty()
    image: bpy.props.PointerProperty(name="Texture", type=bpy.types.Image, update=image_changed)
    detail_image_name: bpy.props.StringProperty()
    detail_image: bpy.props.PointerProperty(
        name="Surface Detail", type=bpy.types.Image, update=detail_image_changed,
        description="Scalar texture varying the editable color, roughness or height; replaces procedural detail",
    )
    projection: bpy.props.EnumProperty(
        name="Projection",
        items=[
            ("UV", "UV", ""),
            ("TRIPLANAR", "Triplanar", "World-space projection rasterized through mesh UVs"),
        ],
        update=changed,
    )
    filtering: bpy.props.EnumProperty(
        name="Filtering",
        update=changed,
        items=[
            ("BILINEAR", "Bilinear", "Interpolate adjacent pixels"),
            ("NEAREST", "Nearest", "Keep hard pixel edges"),
        ],
    )
    tiling: bpy.props.EnumProperty(
        name="UV Wrap",
        update=changed,
        items=[
            ("REPEAT", "Repeat", "Repeat the texture outside its tile"),
            ("NONE", "None", "Transparent outside the original tile"),
        ],
    )
    triplanar_hardness: bpy.props.FloatProperty(
        name="Triplanar Hardness",
        default=4,
        min=1,
        max=64,
        update=numeric,
    )
    scale: bpy.props.FloatVectorProperty(
        name="Scale", size=2, default=(1, 1), min=0.001, update=numeric
    )
    rotation: bpy.props.FloatProperty(name="Rotation", subtype="ANGLE", update=numeric)
    offset: bpy.props.FloatVectorProperty(name="Offset", size=2, update=numeric)
    dirty: bpy.props.BoolProperty(default=True)


class OP_Output(bpy.types.PropertyGroup):
    channel: bpy.props.EnumProperty(items=CHANNEL_ITEMS)
    image: bpy.props.PointerProperty(type=bpy.types.Image)
    dirty: bpy.props.BoolProperty(default=True)
    revision: bpy.props.IntProperty(default=0)
