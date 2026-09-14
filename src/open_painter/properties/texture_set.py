import bpy

from ..constants import RESOLUTIONS
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
    # Exported normals incorporate this distance; authored HEIGHT/NORMAL do not.
    # Export computes their combination at request time, without caching it here.


def backend_changed(self, context):
    changed(self, context)
    # A still-current cached shader can have been disconnected by explicit CPU
    # preview. Switching back must reattach it, even if no recipe input changed.
    from ..evaluation.shader_preview import tick
    tick(self.id_data, self, force_repair=True)


class OP_Assignment(bpy.types.PropertyGroup):
    object: bpy.props.PointerProperty(type=bpy.types.Object)
    object_name: bpy.props.StringProperty()
    slot_index: bpy.props.IntProperty(min=0)
    original_material: bpy.props.PointerProperty(type=bpy.types.Material)
    created_slot: bpy.props.BoolProperty()


class OP_TextureSet(bpy.types.PropertyGroup):
    material_state_data: bpy.props.StringProperty()
    id: bpy.props.StringProperty()
    name: bpy.props.StringProperty(default="Texture Set")
    resolution: bpy.props.EnumProperty(items=RESOLUTIONS, default="2048", update=changed)
    preview_backend: bpy.props.EnumProperty(name="Evaluation", default="LIVE", update=backend_changed,
        items=[('LIVE', 'Shader Native', 'Edit the material shader directly; bake texture outputs explicitly'),
               ('CPU', 'CPU Reference', 'Evaluate and publish complete texture outputs after edits')])
    preview_quality: bpy.props.EnumProperty(name="Preview", default="FULL", update=changed,
        items=[('FULL', 'Full', 'Evaluate at authored resolution'),
               ('512', 'Interactive 512', 'Disposable 512 pixel preview, followed by full resolution'),
               ('1024', 'Interactive 1024', 'Disposable 1024 pixel preview, followed by full resolution')])
    assignments: bpy.props.CollectionProperty(type=OP_Assignment)
    active_assignment: bpy.props.IntProperty()
    material: bpy.props.PointerProperty(type=bpy.types.Material)
    layers: bpy.props.CollectionProperty(type=OP_Layer)
    active_layer: bpy.props.IntProperty()
    dirty: bpy.props.BoolProperty(default=True)
    outputs: bpy.props.CollectionProperty(type=OP_Output)
    preview_ao: bpy.props.BoolProperty(name="AO in Preview", default=True, update=surface_changed)
    anisotropy: bpy.props.FloatProperty(name='Anisotropy',default=0,min=0,max=1,update=surface_changed)
    coat_weight: bpy.props.FloatProperty(name='Clear Coat',default=0,min=0,max=1,update=surface_changed)
    coat_roughness: bpy.props.FloatProperty(name='Coat Roughness',default=.03,min=0,max=1,update=surface_changed)
    height_distance: bpy.props.FloatProperty(
        name="Height Distance", default=0.05, min=0, update=height_changed,
        description="World-space relief for a full height range; also included in exported normals"
    )
    bake: bpy.props.PointerProperty(type=OP_BakeSettings)
    baked_maps: bpy.props.CollectionProperty(type=OP_BakedMap)
    export: bpy.props.PointerProperty(type=OP_ExportSettings)
    edit_serial: bpy.props.IntProperty()
