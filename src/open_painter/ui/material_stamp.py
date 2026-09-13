"""One click, one undo step; native navigation is passed through unchanged."""
import bpy
from .operators import SafeOperator


class OP_OT_material_stamp(SafeOperator,bpy.types.Operator):
    bl_idname='open_painter.material_stamp'
    bl_label='Material Stamp'
    bl_options={'REGISTER','UNDO'}
    radius: bpy.props.FloatProperty(name='World Radius',default=.1,min=.0001,max=10)
    hardness: bpy.props.FloatProperty(name='Hardness',default=.8,min=0,max=1)
    pressure: bpy.props.FloatProperty(name='Pressure',default=1,min=0,max=1)
    color: bpy.props.FloatVectorProperty(name='Base Color',size=3,subtype='COLOR',default=(.3,.08,.02),min=0,max=1)
    roughness: bpy.props.FloatProperty(name='Roughness',default=.8,min=0,max=1)
    metallic: bpy.props.FloatProperty(name='Metallic',default=0,min=0,max=1)
    height: bpy.props.FloatProperty(name='Height',default=.55,min=0,max=1)
    center: bpy.props.FloatVectorProperty(size=3,options={'HIDDEN'})
    normal: bpy.props.FloatVectorProperty(size=3,default=(0,0,1),options={'HIDDEN'})

    def invoke(self,context,event):
        if context.area.type!='VIEW_3D':
            self.report({'ERROR'},'Move the pointer over the 3D view to place a stamp')
            return {'CANCELLED'}
        self._region=next(region for region in context.area.regions if region.type=='WINDOW')
        self._view=context.space_data.region_3d
        context.window.cursor_modal_set('CROSSHAIR')
        context.window_manager.modal_handler_add(self)
        context.scene.open_painter.status='Click to stamp four channels · Esc cancels'
        return {'RUNNING_MODAL'}

    def cancel(self,context):
        context.window.cursor_modal_restore()
        context.scene.open_painter.status=''

    def modal(self,context,event):
        if event.type in {'ESC','RIGHTMOUSE'}:
            self.cancel(context); return {'CANCELLED'}
        if event.type=='LEFTMOUSE' and event.value=='PRESS':
            from bpy_extras.view3d_utils import region_2d_to_origin_3d,region_2d_to_vector_3d
            region=self._region
            xy=(event.mouse_x-region.x,event.mouse_y-region.y)
            if not (0<=xy[0]<region.width and 0<=xy[1]<region.height):
                return {'PASS_THROUGH'}
            origin=region_2d_to_origin_3d(region,self._view,xy)
            direction=region_2d_to_vector_3d(region,self._view,xy)
            hit,point,normal,_,obj,_=context.scene.ray_cast(context.evaluated_depsgraph_get(),origin,direction)
            self.cancel(context)
            if not hit:
                return {'CANCELLED'}
            context.view_layer.objects.active=obj.original
            self.center=point; self.normal=normal
            self.pressure=event.pressure if event.is_tablet else 1
            return self.execute(context)
        return {'PASS_THROUGH'}

    def run(self,context):
        from ..core.project_manager import require_set
        from ..core.layer_stack import active
        from ..painting.material_stamp import apply
        ts=require_set(context); layer=active(ts)
        if layer is None:
            raise ValueError('Add or select a paint layer before stamping')
        apply(ts,layer,context.active_object,self.center,self.normal,self.radius,
              dict(BASE_COLOR=self.color[:],ROUGHNESS=self.roughness,METALLIC=self.metallic,HEIGHT=self.height),
              self.pressure,self.hardness)
        if context.scene.open_painter.paint_image:
            from ..painting.paint_session import synchronize
            synchronize(context)
