"""Explicit offline commands; never execute expensive work on timeline changes."""
import json
import bpy
import numpy as np
from .operators import SafeOperator


class OP_OT_offline_analysis(SafeOperator,bpy.types.Operator):
    bl_idname='open_painter.offline_analysis'
    bl_label='Offline Analysis'
    bl_options={'REGISTER','UNDO'}
    action: bpy.props.EnumProperty(items=[(k,label,'') for k,label in (
        ('CONTACT','Sample Contact History'),('STRAIN','Rest-State Strain'),
        ('MARK','Mark Selected Cut Faces'),('REPORT','Performance Inventory'),('LOD','Stage Mesh LOD'))])
    first_frame: bpy.props.IntProperty(name='First Frame',default=1)
    last_frame: bpy.props.IntProperty(name='Last Frame',default=24)
    step: bpy.props.IntProperty(name='Frame Step',default=1,min=1)
    rest_object: bpy.props.StringProperty(name='Rest Mesh')
    ratio: bpy.props.FloatProperty(name='LOD Ratio',default=.5,min=.01,max=1)

    def invoke(self,context,event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self,context):
        if self.action=='CONTACT':
            for name in ('first_frame','last_frame','step'): self.layout.prop(self,name)
            self.layout.label(text='Uses active Contact scene field; freezes result')
        elif self.action=='STRAIN': self.layout.prop_search(self,'rest_object',context.scene,'objects')
        elif self.action=='LOD':
            self.layout.prop(self,'ratio'); self.layout.label(text='New evaluated copy; inspect visual quality')
        elif self.action=='MARK': self.layout.label(text='Write selected faces to open_painter_cut_surface')

    def run(self,context):
        from ..core.project_manager import require_set
        from ..core.layer_stack import active,invalidate
        from ..analysis import offline
        ts=require_set(context)
        if self.action=='REPORT':
            from ..analysis.analyzer import report
            values=report(ts)
            self.report({'INFO'},f"{values['estimated_image_buffer_bytes']/1048576:.1f} MiB image estimate · {values['layer_count']} layers · {values['filter_count']} filters · {values['instances']} instances")
        elif self.action=='CONTACT':
            layer=active(ts)
            if not layer or not layer.masks: raise ValueError('Select a Contact scene-field mask')
            mask=layer.masks[layer.active_mask]
            if mask.source!='SCENE': raise ValueError('Select a Contact scene-field mask')
            result=offline.contact_history(ts,mask.influence,self.first_frame,self.last_frame,self.step)
            from ..core import image_manager as images
            values=result.pop('contact_fraction'); result.pop('mean_proximity')
            n=values.shape[0]; rgba=np.ones((n,n,4),np.float32); rgba[...,:3]=values[...,None]
            image=images.create('Offline Contact Duration',n); images.write(image,rgba)
            mask.influence.image=image; mask.influence.mode='FROZEN'
            mask.influence.provenance=json.dumps(result); invalidate(ts)
        elif self.action=='MARK':
            obj=context.active_object
            if not obj or obj.type!='MESH': raise ValueError('Select a mesh')
            obj.update_from_editmode()
            offline.mark_faces(obj,[face.index for face in obj.data.polygons if face.select])
        elif self.action=='LOD':
            from ..analysis.lod import create
            if not context.active_object: raise ValueError('Select a source mesh')
            create(context,context.active_object,self.ratio)
        else:
            obj=context.active_object; rest=context.scene.objects.get(self.rest_object)
            if not obj or not rest or obj.type!='MESH' or rest.type!='MESH': raise ValueError('Choose deformed and rest meshes')
            graph=context.evaluated_depsgraph_get(); evaluated=obj.evaluated_get(graph)
            mesh=evaluated.to_mesh()
            try:
                original=np.array([v.co[:] for v in rest.data.vertices])
                deformed=np.array([v.co[:] for v in mesh.vertices])
                if len(deformed)!=len(obj.data.vertices): raise ValueError('Strain attributes require unchanged vertex topology')
                values=offline.strain(original,deformed,[edge.vertices[:] for edge in rest.data.edges])
            finally: evaluated.to_mesh_clear()
            attribute=obj.data.attributes.get('open_painter_strain')
            if attribute and (attribute.domain!='POINT' or attribute.data_type!='FLOAT'): raise ValueError('Existing strain attribute has incompatible type')
            attribute=attribute or obj.data.attributes.new('open_painter_strain','FLOAT','POINT')
            attribute.data.foreach_set('value',values)


class OP_PT_offline_analysis(bpy.types.Panel):
    bl_label='Offline Analysis'
    bl_space_type='VIEW_3D'; bl_region_type='UI'; bl_category='Painter'
    bl_options={'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls,context):
        from .editor import is_area
        return context.area and is_area(context.area)

    def draw(self,context):
        for action,label in [('CONTACT','Sample Contact History'),('STRAIN','Rest-State Strain'),
                             ('MARK','Mark Selected Cut Faces'),('REPORT','Performance Inventory'),('LOD','Stage Mesh LOD')]:
            self.layout.operator('open_painter.offline_analysis',text=label).action=action
