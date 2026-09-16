import bpy

from ..constants import BLEND_ITEMS, CHANNELS
from .channel import OP_Channel
from .mask import OP_Mask


def changed(self, context):
    _changed(self, context, True)


def numeric(self, context):
    """A uniform edit: the shader binds it without re-deriving graph structure."""
    _changed(self, context, False)


def _changed(self, context, structural):
    project = getattr(self.id_data, "open_painter", None)
    if project:
        pointer = self.as_pointer()
        for ts in project.texture_sets:
            owned = next((layer for layer in ts.layers if layer.as_pointer() == pointer or
                          any(mask.as_pointer() == pointer for mask in layer.masks)), None)
            belongs = ts.as_pointer() == pointer or owned is not None
            if belongs:
                previous_dirty = {output.channel: output.dirty for output in ts.outputs}
                ts.dirty = True
                ts.edit_serial += 1
                from ..evaluation.references import dependent_channels
                affected = ({prop.channel for prop in owned.channels if prop.enabled}
                            | dependent_channels(ts, owned.id) if owned else
                            {output.channel for output in ts.outputs})
                if ts.preview_backend == 'CPU':
                    affected.update(output.channel for output in ts.outputs)
                for output in ts.outputs:
                    if output.channel in affected:
                        output.dirty = True
                from ..evaluation.shader_preview import notify
                notify(self, context, previous_dirty=previous_dirty, structural=structural)
                return


def _texture_set_of(self):
    project = getattr(self.id_data, "open_painter", None)
    if not project:
        return None
    pointer = self.as_pointer()
    return next((ts for ts in project.texture_sets
                 if any(layer.as_pointer() == pointer for layer in ts.layers)), None)


def preview_only(self, context):
    """A preview-side edit (bypass, cache mode): the shader restructures, export output is unchanged.

    The authoring serial moves so sessions notice, but no output is marked dirty.
    """
    ts = _texture_set_of(self)
    if ts is None:
        return
    ts.edit_serial += 1
    from ..evaluation.shader_preview import notify
    notify(self, context, structural=True)


def cache_changed(self, context):
    if self.cache_mode != 'LIVE' and not len(self.cache):
        # Every channel row at once: the collection is never reallocated later,
        # so compiled bindings may keep the rows' addresses.
        for channel in CHANNELS:
            row = self.cache.add()
            row.channel = channel
    preview_only(self, context)


class OP_LayerCache(bpy.types.PropertyGroup):
    """One channel of a cached layer: the published image and what it was built from."""
    channel: bpy.props.StringProperty()
    image: bpy.props.PointerProperty(type=bpy.types.Image)
    provenance: bpy.props.StringProperty()
    refresh_serial: bpy.props.IntProperty(default=0)


def decal_object_changed(self, context):
    """The name mirrors the pointer so a re-link after load (or a deleted Empty) never falls
    back to an object chosen earlier."""
    self.decal_object_name = self.decal_object.name if self.decal_object is not None else ''
    changed(self, context)


class OP_Layer(bpy.types.PropertyGroup):
    director_recipe: bpy.props.StringProperty(description="Material Director recipe provenance")
    effect_semantic: bpy.props.EnumProperty(name='Age Response',items=[(k,k.replace('_',' ').title(),'Explicit artistic effect role')
        for k in ('NONE','DIRT','DUST','FADE','SCRATCHES','OXIDATION','COATING_LOSS')])
    id: bpy.props.StringProperty()
    name: bpy.props.StringProperty(default="Layer")
    type: bpy.props.EnumProperty(
        items=[
            ("PAINT", "Paint", "Raster channel images"),
            ("FILL", "Fill", "Constant or texture fill"),
            ("FOLDER", "Folder", "Group layers"),
        ]
    )
    visible: bpy.props.BoolProperty(default=True, update=numeric)
    opacity: bpy.props.FloatProperty(name="Opacity", default=1, min=0, max=1, update=numeric)
    blend_mode: bpy.props.EnumProperty(
        name="Blend",
        items=BLEND_ITEMS,
        update=changed,
    )
    group_mode: bpy.props.EnumProperty(
        name="Folder Composition",
        update=changed,
        items=[
            ("ISOLATED", "Isolated", "Blend children within the folder"),
            ("PASSTHROUGH", "Passthrough", "Child blend modes see layers below the folder"),
        ],
    )
    parent_id: bpy.props.StringProperty(update=changed)
    order: bpy.props.IntProperty()
    channels: bpy.props.CollectionProperty(type=OP_Channel)
    dirty: bpy.props.BoolProperty(default=True)
    masks: bpy.props.CollectionProperty(type=OP_Mask)
    active_mask: bpy.props.IntProperty()
    expanded: bpy.props.BoolProperty(name="Expand Group", default=True)
    # Published outputs: derived anchor ids "<id>:out" (this layer blended over
    # its backdrop) and "<id>:group" (a folder's child composite before it blends).
    publish_output: bpy.props.BoolProperty(name="Publish Output", default=False, update=changed,
        description="Publish this layer's blended result as an anchor other layers can read")
    publish_group: bpy.props.BoolProperty(name="Publish Folder Composite", default=False, update=changed,
        description="Publish the folder's child composite, before the folder itself blends, as an anchor")
    # Layer performance (0.9.0): preview-only state. Export, bakes and 2D
    # snapshots evaluate the full stack regardless.
    bypass: bpy.props.BoolProperty(name="Bypass", default=False, update=preview_only,
        description="Skip this layer (and its children) in the shader preview only; export, bakes and "
                    "2D snapshots still include it")
    cache_mode: bpy.props.EnumProperty(name="Cache", default='LIVE', update=cache_changed, items=[
        ('LIVE', 'Live', 'Compile the layer into the shader'),
        ('CACHED', 'Cached', 'Sample a cached image; refresh it once edits inside the layer settle'),
        ('FROZEN', 'Frozen', 'Sample the cached image until Refresh Cache')])
    cache: bpy.props.CollectionProperty(type=OP_LayerCache)
    # Decal objects (0.9.6): a layer whose channels project from an Empty; its matrix is the frame.
    decal_object: bpy.props.PointerProperty(name="Decal Object", type=bpy.types.Object, update=decal_object_changed,
        poll=lambda self, obj: obj.type == 'EMPTY',
        description="Empty whose local XY plane carries the decal images and whose -Z is the projection direction")
    decal_object_name: bpy.props.StringProperty()
    decal_depth: bpy.props.FloatProperty(name="Projection Depth", default=.25, min=0., soft_max=10., subtype='DISTANCE',
        unit='LENGTH', update=numeric, description="How far above and below the decal plane surfaces still receive it")
    decal_falloff: bpy.props.FloatProperty(name="Edge Softness", default=0., min=0., max=.5, update=numeric,
        description="Fade of the decal border, as a fraction of its size")
    decal_facing: bpy.props.BoolProperty(name="Front Faces Only", default=True, update=numeric,
        description="Skip surfaces facing away from the decal")
    # World axes of the decal frame, mirrored from the object by the scheduler so the shader
    # can rotate decal normals and test facing with uniforms (moving the object updates them).
    decal_axes: bpy.props.FloatVectorProperty(size=9, default=(1, 0, 0, 0, 1, 0, 0, 0, 1), update=numeric)
    decal_scale: bpy.props.FloatVectorProperty(size=3, default=(1, 1, 1), update=numeric)
    # Vector strokes (0.6.0): paint layers sharing a key across sets render the same strokes.
    stroke_key: bpy.props.StringProperty(update=changed,
        description="Key of the project's vector strokes this paint layer renders")
    # Material module identity on an instance root folder (0.4.1). Metadata only:
    # ids inside the instance are "<module_instance>.<module id>".
    module_id: bpy.props.StringProperty(description="Module definition this folder is an instance of")
    module_version: bpy.props.IntProperty(default=0, description="Module version this instance was inserted from or updated to")
    module_source: bpy.props.StringProperty(description="library:<file> or catalog:<recipe> the module came from")
    module_instance: bpy.props.StringProperty(description="Namespace token of this instance's ids")
    module_name: bpy.props.StringProperty()
    module_detached: bpy.props.BoolProperty(default=False, name="Detached",
        description="Keep the module provenance but exclude this instance from updates")
