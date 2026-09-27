import bpy

from ..constants import RESOLUTIONS
from .aging import OP_Aging
from .bake import OP_BakedMap, OP_BakeSettings
from .channel import OP_Output
from .export import OP_ExportSettings
from .layer import OP_Layer, changed


def surface_changed(self, context):
    # Surface uniforms do not change any baked channel pixels.
    from ..core.material_manager import update_surface
    update_surface(self)


def height_changed(self, context):
    surface_changed(self, context)
    # Metric surfaces divide encoded HEIGHT by this distance so a declared
    # micron amplitude stays fixed. Legacy channels remain entirely unchanged.
    if any(p.channel == 'HEIGHT' and getattr(p, 'material_mapping_version', 0) == 1
           for layer in self.layers for p in layer.channels):
        from .layer import numeric
        numeric(self, context)
    # Exported normals incorporate this distance; legacy HEIGHT/NORMAL do not.
    # Export computes their combination at request time, without caching it here.


def layer_selected(self, context):
    """A press on a Painter layer row lands here first; it may start a drag."""
    if context.region is not None:
        from ..ui.native import layer_row_pressed

        layer_row_pressed(self, context)


def resolution_changed(self, context):
    changed(self, context)
    # Texel size moved: physical filter radii resolve to new texel counts (0.9.5).
    from ..evaluation import physical
    physical.sync(self)


def backend_changed(self, context):
    # Work deferred by a stroke is applied (or dropped with the shader session) first.
    from ..evaluation.shader_preview import requested, resume, tick
    resume(self.id_data)
    changed(self, context)
    if requested(self):
        # The engine adapter may have been replaced by the CPU reference's image nodes:
        # switching back reattaches it, even if no recipe input changed.
        tick(self.id_data, self, force_repair=True)
        return
    # The CPU reference reads one image node per channel, which its reduced previews swap.
    from ..core.material_manager import repair
    try:
        repair(self)
    except ValueError as exc:
        from ..utils.logging import logger
        logger.error('CPU reference material: %s', exc)


class OP_Assignment(bpy.types.PropertyGroup):
    object: bpy.props.PointerProperty(type=bpy.types.Object)
    object_name: bpy.props.StringProperty()
    slot_index: bpy.props.IntProperty(min=0)
    original_material: bpy.props.PointerProperty(type=bpy.types.Material)
    created_slot: bpy.props.BoolProperty()
    udim_original_slot: bpy.props.IntProperty(default=-1)


class OP_LayerStore(bpy.types.PropertyGroup):
    """A texture set's layer stack, held by a hidden Palette (``core.layer_store``)."""
    set_id: bpy.props.StringProperty(options={'HIDDEN'})
    home: bpy.props.IntProperty(options={'HIDDEN'})   # session_uid of the scene whose set holds it
    layers: bpy.props.CollectionProperty(type=OP_Layer)


class OP_TextureSet(bpy.types.PropertyGroup):
    shared_stack_id: bpy.props.StringProperty(options={'HIDDEN'})
    shared_stack_version: bpy.props.IntProperty(default=0, options={'HIDDEN'})
    shared_stack_owner: bpy.props.StringProperty(options={'HIDDEN'})
    udim_tile: bpy.props.IntProperty(name="UDIM Tile", default=0, min=0, max=2000, update=changed)
    udim_group: bpy.props.StringProperty()
    udim_name: bpy.props.StringProperty(name="UDIM Export Name")
    udim_source_uv: bpy.props.StringProperty()
    physical_space_status: bpy.props.EnumProperty(name="Physical Size", default="UNKNOWN", update=changed,
        items=[('UNKNOWN', 'Unknown', 'No verified size declaration'),
               ('KNOWN', 'Declared', 'Artist or fixture author supplies explicit measurement provenance')])
    meters_per_world_unit: bpy.props.FloatProperty(name="Meters per World Unit", default=1, min=1e-9, update=changed)
    physical_space_provenance: bpy.props.StringProperty(name="Size Provenance", description="Measurement, documented source units or explicit artist scale declaration", update=changed)
    director_locks: bpy.props.StringProperty(default="[]", description="Protected authoring paths")
    director_history: bpy.props.StringProperty(default="[]", description="Bounded Material Director change receipts")
    material_state_data: bpy.props.StringProperty()
    id: bpy.props.StringProperty()
    name: bpy.props.StringProperty(default="Texture Set")
    resolution: bpy.props.EnumProperty(items=RESOLUTIONS, default="2048", update=resolution_changed)
    preview_backend: bpy.props.EnumProperty(name="Evaluation", default="LIVE", update=backend_changed,
        items=[('LIVE', 'Open Painter Engine', 'Evaluate the stack on the GPU into the set\'s images; the material reads them through its Open Painter Texture Set node'),
               ('CPU', 'CPU Reference', 'Evaluate and publish complete texture outputs after edits')])
    preview_quality: bpy.props.EnumProperty(name="Preview", default="FULL", update=changed,
        items=[('FULL', 'Full', 'Evaluate at authored resolution'),
               ('512', 'Interactive 512', 'Disposable 512 pixel preview, followed by full resolution'),
               ('1024', 'Interactive 1024', 'Disposable 1024 pixel preview, followed by full resolution')])
    assignments: bpy.props.CollectionProperty(type=OP_Assignment)
    active_assignment: bpy.props.IntProperty()
    material: bpy.props.PointerProperty(type=bpy.types.Material)
    # The stack itself lives in a hidden Palette (core.layer_store); ``layers`` reads it.
    layer_store: bpy.props.PointerProperty(type=bpy.types.Palette, options={'HIDDEN'})
    active_layer: bpy.props.IntProperty(
        name="Layer",
        description="Press to select a layer and drag to move it; double-click to rename",
        update=layer_selected,
    )
    dirty: bpy.props.BoolProperty(default=True)
    outputs: bpy.props.CollectionProperty(type=OP_Output)
    preview_ao: bpy.props.BoolProperty(name="AO in Preview", default=True, update=surface_changed)
    anisotropy: bpy.props.FloatProperty(name='Anisotropy',default=0,min=0,max=1,update=surface_changed)
    coat_weight: bpy.props.FloatProperty(name='Clear Coat',default=0,min=0,max=1,update=surface_changed)
    coat_roughness: bpy.props.FloatProperty(name='Coat Roughness',default=.03,min=0,max=1,update=surface_changed)
    ior: bpy.props.FloatProperty(name='Index of Refraction', default=1.5, min=1, max=4, update=surface_changed)
    transmission_weight: bpy.props.FloatProperty(name='Transmission', default=0, min=0, max=1, update=surface_changed)
    subsurface_weight: bpy.props.FloatProperty(name='Subsurface', default=0, min=0, max=1, update=surface_changed)
    subsurface_scale: bpy.props.FloatProperty(name='Scattering Distance', default=.001, min=0, max=10, subtype='DISTANCE', unit='LENGTH', update=surface_changed)
    sheen_weight: bpy.props.FloatProperty(name='Sheen', default=0, min=0, max=1, update=surface_changed)
    sheen_roughness: bpy.props.FloatProperty(name='Sheen Roughness', default=.5, min=0, max=1, update=surface_changed)
    height_distance: bpy.props.FloatProperty(
        name="Height Distance", default=0.05, min=0, update=height_changed,
        description="World-space relief for a full height range; also included in exported normals"
    )
    bake: bpy.props.PointerProperty(type=OP_BakeSettings)
    baked_maps: bpy.props.CollectionProperty(type=OP_BakedMap)
    export: bpy.props.PointerProperty(type=OP_ExportSettings)
    aging: bpy.props.PointerProperty(type=OP_Aging)
    edit_serial: bpy.props.IntProperty()

    @property
    def layers(self):
        from ..core.layer_store import layers
        return layers(self)
