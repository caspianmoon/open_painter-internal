"""Native state controls; affect explicitly tagged fill layers only."""
import bpy
from .operators import SafeOperator
from ..core.environment_profiles import PROFILES


class OP_OT_material_state(SafeOperator,bpy.types.Operator):
    bl_idname='open_painter.material_state'
    bl_label='Material State'
    bl_options={'REGISTER','UNDO'}
    state: bpy.props.EnumProperty(items=[(k,k.title(),'') for k in ('CLEAN','USED','DAMAGED','CUSTOM')],default='USED')
    age: bpy.props.FloatProperty(name='Age',default=.5,min=0,max=1)
    usage: bpy.props.FloatProperty(name='Usage',default=.5,min=0,max=1)
    maintenance: bpy.props.FloatProperty(name='Maintenance',default=.5,min=0,max=1)
    profile: bpy.props.EnumProperty(items=[(key,key.title(),'Artistic environment response') for key in PROFILES])
    seed: bpy.props.IntProperty(name='Asset Seed',default=1,min=0)

    def invoke(self,context,event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self,context):
        layout=self.layout
        layout.prop(self,'state')
        if self.state=='CUSTOM':
            for name in ('age','usage','maintenance'): layout.prop(self,name)
        layout.prop(self,'profile'); layout.prop(self,'seed')
        layout.label(text='Artistic controls · tagged fill layers only')
        layout.label(text='Shared texture sets have identical wear')

    def run(self,context):
        from ..core.project_manager import require_set
        from ..core.material_states import apply
        apply(require_set(context),None if self.state=='CUSTOM' else self.state,
              age=self.age,usage=self.usage,maintenance=self.maintenance,profile=self.profile,seed=self.seed)
