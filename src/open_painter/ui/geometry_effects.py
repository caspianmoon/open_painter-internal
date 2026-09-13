"""Native controls for explicit managed geometry operations."""
import bpy
from .operators import SafeOperator


class OP_OT_geometry_effect(SafeOperator,bpy.types.Operator):
    bl_idname='open_painter.geometry_effect'
    bl_label='Geometry Effect'
    bl_options={'REGISTER','UNDO'}
    action: bpy.props.EnumProperty(items=[(k,k.title(),'') for k in ('RUST','MOSS','WELD','REGENERATE','FREEZE','UNFREEZE','REMOVE','SNOW','SPLINTERS','PEEL')])
    density: bpy.props.FloatProperty(name='Instances per Square Unit',default=30,min=0,max=5000)
    size: bpy.props.FloatProperty(name='Size',default=.02,min=.0001,max=10)
    seed: bpy.props.IntProperty(name='Seed',default=1,min=0)
    lift: bpy.props.FloatProperty(name='Surface Lift',default=0,min=0,max=1)

    def invoke(self,context,event):
        if self.action in {'RUST','MOSS','WELD','SNOW','SPLINTERS','PEEL'}:
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def run(self,context):
        from ..geometry_effects import manager,weld
        from ..core.project_manager import require_set
        from ..core.layer_stack import active
        obj=context.active_object
        if obj is None:
            raise ValueError('Select a source object or effect helper')
        if self.action=='REMOVE':
            manager.remove(obj); return
        if self.action in {'FREEZE','UNFREEZE'}:
            if obj.get(manager.TAG)!='INSTANCE_HELPER':
                raise ValueError('Select an effect helper')
            obj['frozen']=self.action=='FREEZE'; return
        if self.action=='WELD':
            helper=weld.create(context.scene,obj,self.size)
        else:
            ts=require_set(context)
            if self.action=='REGENERATE':
                helper=manager.regenerate(ts,obj)
            else:
                layer=active(ts)
                if layer is None:
                    raise ValueError('Select the layer whose mask drives geometry density')
                from ..core.published_outputs import publish_mask
                mask=publish_mask(ts,layer)
                helper=manager.create(ts,obj,mask,self.action,self.density,self.size,self.seed,self.lift)
        if helper:
            for selected in context.selected_objects: selected.select_set(False)
            helper.select_set(True); context.view_layer.objects.active=helper


class OP_PT_geometry_effects(bpy.types.Panel):
    bl_label='Geometry Effects'
    bl_space_type='VIEW_3D'
    bl_region_type='UI'
    bl_category='Painter'
    bl_options={'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls,context):
        from .editor import is_area
        return context.area and is_area(context.area)

    def draw(self,context):
        layout=self.layout
        layout.label(text='Separate helpers · active layer mask')
        row=layout.row(align=True)
        for action,label in [('RUST','Rust Flakes'),('MOSS','Moss'),('WELD','Weld Curve')]:
            row.operator('open_painter.geometry_effect',text=label).action=action
        layout.label(text='Experimental surface prototypes')
        row=layout.row(align=True)
        for action,label in [('SNOW','Snow Clumps'),('SPLINTERS','Splinters'),('PEEL','Peel Fragments')]:
            row.operator('open_painter.geometry_effect',text=label).action=action
        obj=context.active_object
        if obj and obj.get('open_painter_geometry_effect')=='INSTANCE_HELPER':
            layout.label(text=f"{obj.get('instance_count',0):,} instances · bounded geometry")
            if obj.get('kind') in {'RUST','MOSS','SNOW','SPLINTERS','PEEL'}:
                for name in ('density','size','seed','lift'):
                    layout.prop(obj,f'["{name}"]',text=name.title())
                layout.operator('open_painter.geometry_effect',text='Regenerate').action='REGENERATE'
                layout.operator('open_painter.geometry_effect',text='Unfreeze' if obj.get('frozen') else 'Freeze').action='UNFREEZE' if obj.get('frozen') else 'FREEZE'
            layout.operator('open_painter.geometry_effect',text='Remove Helper').action='REMOVE'
