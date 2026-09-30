import bpy

from ..constants import BLEND_ITEMS, CHANNELS
from ..core.layer_stack import replaying
from .channel import OP_Channel
from .mask import OP_Mask
from ..core.layer_store import project_of as _project_of


def changed(self, context):
    _changed(self, context, True)


def numeric(self, context):
    """A uniform edit: the shader binds it without re-deriving graph structure."""
    _changed(self, context, False)


def _changed(self, context, structural):
    project = _project_of(self)
    if not project or replaying():
        return
    from ..core.owners import locate
    found = locate(self)
    if found is None:
        return
    ts, layer = found
    pointer = self.as_pointer()
    # Answered here: the set itself, a layer or one of its masks.
    owned = (layer if layer is not None and (layer.as_pointer() == pointer or
                                             any(mask.as_pointer() == pointer for mask in layer.masks))
             else None)
    if ts.as_pointer() != pointer and owned is None:
        return
    previous_dirty = {output.channel: output.dirty for output in ts.outputs}
    ts.dirty = True
    serial = ts.edit_serial
    ts.edit_serial += 1
    if not structural and owned is not None and not (
            owned.as_pointer() == pointer and (owned.decal_inline or owned.decal_object is not None
                                               or owned.decal_object_name)):
        # A decal layer's own numeric fields are its projection frame, which the geometry key reads.
        from ..evaluation.structure_memo import numeric_edit
        numeric_edit(ts, serial)
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


def _texture_set_of(self):
    project = _project_of(self)
    if not project:
        return None
    pointer = self.as_pointer()
    return next((ts for ts in project.texture_sets
                 if any(layer.as_pointer() == pointer for layer in ts.layers)), None)


def preview_only(self, context):
    """A preview-side edit (bypass, cache mode): the shader restructures, export output is unchanged.

    The authoring serial moves so sessions notice, but no output is marked dirty.
    """
    if replaying():
        return
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
    elif self.cache_mode == 'LIVE' and len(self.cache):
        # Back to Live: the rows stay (see above) but give up their images, or a
        # layer cached once would carry full-resolution images in the file for
        # good. Released, never removed: an undo of the switch must find the
        # datablocks with their pixels. Not skipped while replaying: a snapshot
        # lands in a fresh layer (no rows, nothing to give up) and its own rows
        # are restored after this.
        from ..evaluation.layer_cache import retire_layer
        retire_layer(self)
    preview_only(self, context)


class OP_LayerCache(bpy.types.PropertyGroup):
    """One channel of a cached layer: the published image and what it was built from."""
    channel: bpy.props.StringProperty()
    image: bpy.props.PointerProperty(type=bpy.types.Image)
    provenance: bpy.props.StringProperty()
    refresh_serial: bpy.props.IntProperty(default=0)


def decal_object_changed(self, context):
    """The name mirrors the pointer so a re-link after load (or a deleted Empty) never falls
    back to an object chosen earlier. The Empty the layer leaves gives up the layer's marker:
    still marked, the scheduler would take it for a Shift+D copy and duplicate the layer."""
    from ..painting.decals import MARKER
    previous = bpy.data.objects.get(self.decal_object_name) if self.decal_object_name else None
    if previous is not None and previous != self.decal_object and previous.get(MARKER) == self.id:
        del previous[MARKER]
    self.decal_object_name = self.decal_object.name if self.decal_object is not None else ''
    changed(self, context)


def _master(layer, family):
    from ..smart_materials import controls
    return controls.master_values(layer, family)


def _set_master(layer, family, value):
    from ..smart_materials import controls
    controls.set_master_values(layer, family, value)


class OP_Layer(bpy.types.PropertyGroup):
    master_controls: bpy.props.StringProperty(description="Portable authored master-control definitions")
    surface_id: bpy.props.StringProperty(description="Stable surface identity; independent of layer name")
    surface_family: bpy.props.StringProperty(description="Typed surface recipe family")
    surface_role: bpy.props.StringProperty(description="Native surface component role")
    director_recipe: bpy.props.StringProperty(description="Material Director recipe provenance")
    aging_role: bpy.props.StringProperty(description="The aging outcome this layer materialises; the Material Aging engine drives it",
                                         options={'HIDDEN'})
    # Kept for files and material documents written before 0.10.0; Material Aging
    # (``aging``) replaced the Age Response roles and drives its own layers.
    effect_semantic: bpy.props.EnumProperty(name='Age Response',items=[(k,k.replace('_',' ').title(),'Legacy artistic effect role')
        for k in ('NONE','DIRT','DUST','FADE','SCRATCHES','OXIDATION','COATING_LOSS')],options={'HIDDEN'})
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
    # A percent view of ``opacity`` for the panels (UI plan phase 8); derived, never stored
    # (layer_stack.DERIVED keeps snapshots and replays from writing it back).
    opacity_percent: bpy.props.IntProperty(
        name="Layer Opacity", description="Opacity of the whole layer, in percent", min=0, max=100,
        subtype="PERCENTAGE",
        get=lambda self: int(round(self.opacity * 100)),
        set=lambda self, value: setattr(self, "opacity", value / 100))
    # A smart material folder's computed master controls (smart_materials.controls): each
    # component reads and writes the recipe layers it binds, nothing is stored here, and
    # layer_stack.DERIVED keeps snapshots, documents and signatures from carrying them.
    master_factor: bpy.props.FloatVectorProperty(
        name="Master Control", description="Master control of the smart material (0–1)",
        size=8, min=0, max=1, get=lambda self: _master(self, "factor"),
        set=lambda self, value: _set_master(self, "factor", value))
    master_scale: bpy.props.FloatVectorProperty(
        name="Master Control", description="Master control of the smart material, times the authored value",
        size=8, min=0, max=32, soft_min=0, soft_max=4, precision=2,
        get=lambda self: _master(self, "multiplier"),
        set=lambda self, value: _set_master(self, "multiplier", value))
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
        description="Skip this layer (and its children) in the live view only; export, bakes and "
                    "2D snapshots still include it")
    cache_mode: bpy.props.EnumProperty(name="Cache", default='LIVE', update=cache_changed, items=[
        ('LIVE', 'Live', 'Evaluate the layer live with the rest of the stack'),
        ('CACHED', 'Cached', 'Sample a cached image; refresh it once edits inside the layer settle'),
        ('FROZEN', 'Frozen', 'Sample the cached image until Refresh Cache')])
    cache: bpy.props.CollectionProperty(type=OP_LayerCache)
    # Decal objects (0.9.6): a layer whose channels project from an Empty; its matrix is the frame.
    decal_object: bpy.props.PointerProperty(name="Decal Object", type=bpy.types.Object, update=decal_object_changed,
        poll=lambda self, obj: obj.type == 'EMPTY',
        description="Empty whose local XY plane carries the decal images and whose -Z is the projection direction")
    decal_object_name: bpy.props.StringProperty()
    decal_inline: bpy.props.BoolProperty(name="Stored Projection Frame", default=False, update=changed)
    decal_origin: bpy.props.FloatVectorProperty(name="Projection Center", size=3, update=numeric)
    decal_extent: bpy.props.FloatVectorProperty(name="Projection Size", size=2, default=(1, 1), min=.000001, update=numeric)
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
    # Native painting across sets (0.9.7): the layers one brush session targets share a link.
    # Its own id, not the stroke key: a key alone routes a layer through the vector-stroke
    # boundary even when it renders no strokes.
    paint_link: bpy.props.StringProperty(
        description="Id shared by the paint layers of other sets this layer paints together with")
    # Material module identity on an instance root folder (0.4.1). Metadata only:
    # ids inside the instance are "<module_instance>.<module id>".
    module_id: bpy.props.StringProperty(description="Module definition this folder is an instance of")
    module_version: bpy.props.IntProperty(default=0, description="Module version this instance was inserted from or updated to")
    module_source: bpy.props.StringProperty(description="library:<file> or catalog:<recipe> the module came from")
    module_instance: bpy.props.StringProperty(description="Namespace token of this instance's ids")
    module_name: bpy.props.StringProperty()
    module_detached: bpy.props.BoolProperty(default=False, name="Detached",
        description="Keep the module provenance but exclude this instance from updates")
