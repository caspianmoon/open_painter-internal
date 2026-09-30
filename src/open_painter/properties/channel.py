import bpy

from ..constants import BLEND_ITEMS, CHANNEL_ITEMS
from ..core.layer_stack import replaying
from .effect import OP_TextureEffect
from ..core.layer_store import project_of as _project_of


def changed(self, context):
    _changed(self, context, True)
    _resolve_physical(self)


def numeric(self, context):
    """A uniform or resource-identity edit; graph structure is unchanged."""
    _changed(self, context, False)
    if hasattr(self, 'value') and hasattr(self, 'channel'):
        # The RNA bounds stay wide so Height can be typed past 1; every other
        # scalar channel is physical 0..1 and is clamped back here.
        low, high = _VALUE_RANGES.get(self.channel or '', (0.0, 1.0))
        clamped = min(max(self.value, low), high)
        if clamped != self.value:
            self.value = clamped


# Stored value bounds per channel. The RNA property stays wide (soft 0..1 keeps
# every slider compact) so Height alone can be typed past 1; `numeric` clamps
# everything else back to physical 0..1.
_VALUE_RANGES = {'HEIGHT': (-1.0, 10.0)}


def owning_set(owner):
    """The texture set holding a channel, mask or effect (by address)."""
    project = _project_of(owner)
    if project is None:
        return None
    pointer = owner.as_pointer()
    for ts in project.texture_sets:
        for layer in ts.layers:
            for prop in layer.channels:
                if prop.as_pointer() == pointer or any(e.as_pointer() == pointer for e in prop.effects):
                    return ts
            for mask in layer.masks:
                if mask.as_pointer() == pointer or mask.effect.as_pointer() == pointer:
                    return ts
    return None


def _resolve_physical(owner):
    """A structural edit (projection, source, kind) can change how metre sizes map to texture units."""
    if getattr(owner, 'units', 'TEXTURE') != 'PHYSICAL' or replaying():
        return
    ts = owning_set(owner)
    if ts is not None:
        from ..evaluation import physical
        physical.sync_owner(owner, ts)


# These derived controls apply an explicit node-side edit across a fill layer.
# Stored channel fields stay independent for recipes, image feeds and old files.
_PROJECTIONS = ('UV', 'TRIPLANAR', 'DECAL')


def _set_fill_mapping(prop, field, value):
    from ..core.layer_stack import replay
    ts = owning_set(prop)
    if ts is None:
        return
    layer = next((layer for layer in ts.layers
                  if any(item.as_pointer() == prop.as_pointer() for item in layer.channels)), None)
    if layer is None or layer.type != 'FILL':
        return
    with replay(ts):
        for other in layer.channels:
            setattr(other, field, value)


def _changed(self, context, structural):
    self.dirty = True
    if replaying():
        return   # a snapshot replay invalidates its set once (layer_stack.replay)
    # Resolve the owner by address, like layer.py and effect.py: a replayed
    # snapshot carries its source's layer_id until the copy is re-keyed, and a
    # lookup by that id would dirty the source set instead.
    from ..core.owners import locate
    found = locate(self)
    if found is None or found[1] is None:
        return
    ts, layer = found
    pointer = self.as_pointer()
    if not any(prop.as_pointer() == pointer for prop in layer.channels):
        return
    previous_dirty = {output.channel: output.dirty for output in ts.outputs}
    layer.dirty = True
    ts.dirty = True
    serial = ts.edit_serial
    ts.edit_serial += 1
    if not structural:
        from ..evaluation.structure_memo import numeric_edit
        numeric_edit(ts, serial)
    from ..evaluation.references import dependent_channels
    affected = dependent_channels(ts,layer.id,self.channel) | {self.channel}
    for output in ts.outputs:
        if output.channel in affected:
            output.dirty = True
    from ..evaluation.shader_preview import notify
    notify(self, context, previous_dirty=previous_dirty, structural=structural)


def image_changed(self, context):
    from ..graph.edits import detach_image_feed
    detach_image_feed(self)
    self.image_name = self.image.name if self.image else ""
    changed(self, context)


def detail_image_changed(self, context):
    self.detail_image_name = self.detail_image.name if self.detail_image else ""
    changed(self, context)


def physical_changed(self, context):
    """A metre size or the units switch: derive the texture values (their own callbacks notify)."""
    if replaying():
        return
    from ..evaluation import physical
    ts = owning_set(self)
    if ts is not None:
        physical.sync_owner(self, ts)
        from ..evaluation.structure_memo import structural_edit
        structural_edit(ts)   # the units switch decides whether the set reads the geometry


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
        subtype="PERCENTAGE",
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
    value: bpy.props.FloatProperty(name="Value", default=0.5, min=-1.0, soft_min=0.0,
                                         soft_max=1.0, max=10.0, update=numeric)
    # Fill content instantiated from an anchor point; replaces image/color/value.
    anchor: bpy.props.StringProperty(name="Anchor", update=changed,
        description="Anchor point whose published result becomes this channel's content")
    anchor_channel: bpy.props.StringProperty(name="Anchor Channel", update=changed,
        description="Channel read from a published layer output or folder composite; empty reads this channel")
    procedural: bpy.props.StringProperty(default='',update=changed)
    procedural_scale: bpy.props.FloatProperty(name='Scale',default=8,min=.01,max=512,update=numeric)
    # Physical scale (0.9.5): sizes in metres, resolved to texture units per asset.
    units: bpy.props.EnumProperty(name='Units', default='TEXTURE', update=physical_changed, items=[
        ('TEXTURE', 'Texture', 'Sizes in UV repeats and texels'),
        ('PHYSICAL', 'Physical', 'Sizes in world distance; the texture values follow the assigned mesh')])
    feature_size: bpy.props.FloatProperty(name='Feature Size', default=.05, min=1e-5, soft_max=10, subtype='DISTANCE',
        unit='LENGTH', update=physical_changed, description='World size of one procedural cell (noise period)')
    # Version 0 preserves every legacy saved material. Version 1 is an
    # explicitly declared metric frame; texture scale is not rescaled behind it.
    material_mapping_version: bpy.props.IntProperty(default=0, min=0, max=1, update=changed)
    material_period_m: bpy.props.FloatProperty(name="Physical Repeat (m)", default=.001, min=.000001, soft_max=.1, precision=6, update=numeric)
    material_meters_per_unit: bpy.props.FloatProperty(name="Meters per World Unit", default=1, min=1e-9, update=numeric)
    material_frame: bpy.props.EnumProperty(name="Material Frame", default="WORLD", update=changed,
        items=[('WORLD', 'Declared World Frame', 'Explicit orthonormal directions'),
               ('OBJECT', 'Object Frame', 'Object rotation with physical world distances'),
               ('COMPONENT', 'Connected Member Frame', 'Geometric principal long axis per connected member')])
    material_origin: bpy.props.FloatVectorProperty(name="Frame Origin", size=3, update=numeric)
    material_axis_u: bpy.props.FloatVectorProperty(name="Across", size=3, default=(1, 0, 0), update=numeric)
    material_axis_v: bpy.props.FloatVectorProperty(name="Along", size=3, default=(0, 1, 0), update=numeric)
    material_directional: bpy.props.BoolProperty(name="Follow Along Axis", default=False, update=numeric,
        description="Project image detail around the declared Along axis, preserving member grain on every side")
    material_height_m: bpy.props.FloatProperty(name="Relief Amplitude (m)", default=.00002, min=0, soft_max=.001, precision=6, update=numeric)
    material_image_extent_m: bpy.props.FloatVectorProperty(name="Image Tile Extent (m)", size=2, default=(.01, .01), min=.000001, precision=6, update=numeric)
    material_source_id: bpy.props.StringProperty(description="Persistent coherent structural source identity")
    source_metadata: bpy.props.StringProperty(description="Source purpose, interpretation and import/generation provenance")
    procedural_seed: bpy.props.IntProperty(name='Seed',default=1,min=0,update=numeric)
    procedural_detail: bpy.props.FloatProperty(name='Variation',default=.15,min=0,max=2,update=numeric)
    procedural_height: bpy.props.FloatProperty(name='Height Amplitude',default=.02,min=-1,max=1,update=numeric)
    effects: bpy.props.CollectionProperty(type=OP_TextureEffect)
    active_effect: bpy.props.IntProperty()
    image_name: bpy.props.StringProperty()
    mesh_map: bpy.props.StringProperty(description="Destination mesh-map binding", update=changed)
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
            ("DECAL", "Decal", "Projected from the layer's decal object: move, rotate, scale or duplicate it in the viewport"),
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
    fill_projection: bpy.props.EnumProperty(name="Projection",
        items=[(key, key.title(), '') for key in _PROJECTIONS],
        get=lambda self: _PROJECTIONS.index(self.projection),
        set=lambda self, value: _set_fill_mapping(self, 'projection', _PROJECTIONS[value]))
    fill_scale: bpy.props.FloatVectorProperty(name="Scale", size=2, min=.001,
        get=lambda self: tuple(self.scale),
        set=lambda self, value: _set_fill_mapping(self, 'scale', value))
    fill_rotation: bpy.props.FloatProperty(name="Rotation", subtype='ANGLE',
        get=lambda self: self.rotation,
        set=lambda self, value: _set_fill_mapping(self, 'rotation', value))
    fill_offset: bpy.props.FloatVectorProperty(name="Offset", size=2,
        get=lambda self: tuple(self.offset),
        set=lambda self, value: _set_fill_mapping(self, 'offset', value))
    fill_triplanar_hardness: bpy.props.FloatProperty(name="Triplanar Hardness", min=1, max=64,
        get=lambda self: self.triplanar_hardness,
        set=lambda self, value: _set_fill_mapping(self, 'triplanar_hardness', value))
    dirty: bpy.props.BoolProperty(default=True)


class OP_Output(bpy.types.PropertyGroup):
    channel: bpy.props.EnumProperty(items=CHANNEL_ITEMS)
    image: bpy.props.PointerProperty(type=bpy.types.Image)
    dirty: bpy.props.BoolProperty(default=True)
    revision: bpy.props.IntProperty(default=0)
