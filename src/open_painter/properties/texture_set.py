import bpy

from ..constants import RESOLUTIONS
from .bake import OP_BakedMap, OP_BakeSettings
from .channel import OP_Output
from .export import OP_ExportSettings
from .layer import OP_Layer, changed


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
    preview_ao: bpy.props.BoolProperty(name="AO in Preview", default=True, update=changed)
    anisotropy: bpy.props.FloatProperty(name='Anisotropy',default=0,min=0,max=1,update=changed)
    coat_weight: bpy.props.FloatProperty(name='Clear Coat',default=0,min=0,max=1,update=changed)
    coat_roughness: bpy.props.FloatProperty(name='Coat Roughness',default=.03,min=0,max=1,update=changed)
    height_distance: bpy.props.FloatProperty(
        name="Height Distance", default=0.05, min=0, update=changed
    )
    bake: bpy.props.PointerProperty(type=OP_BakeSettings)
    baked_maps: bpy.props.CollectionProperty(type=OP_BakedMap)
    export: bpy.props.PointerProperty(type=OP_ExportSettings)
    edit_serial: bpy.props.IntProperty()
