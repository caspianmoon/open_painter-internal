"""Explicit offline commands; never execute expensive work on timeline changes."""
import json
import bpy
import numpy as np
from .operators import SafeOperator


SUPERSEDED='open_painter_superseded'


def retire_superseded(field,previous):
    """Let the raster a contact run just replaced go, keeping one superseded raster per mask.

    Each run rasters a fresh full-resolution fake-user image. The one it replaces
    only loses its fake user (not the datablock, so an undo of this run finds its
    pixels) and is purged on the next save; until then it stays resident. Without
    a bound, repeated runs before a save stacked every superseded raster in
    memory, so the field remembers the released predecessor's name and removes
    the one before it once a newer run releases another: that older raster is two
    undo steps away and nothing binds it any more. Names, not pointers, identify
    it (addresses are reused across loads); a datablock that gained a user or a
    fake user since (an artist wired it into a material, another mask adopted
    it) is no longer ours to remove.
    """
    from ..core.layer_stack import release_image
    from ..constants import OWNER
    if previous is None or not previous.get(OWNER):
        return
    release_image(previous)
    older=bpy.data.images.get(field.get(SUPERSEDED,''))
    if older is not None and older!=previous and older.get(OWNER) and not older.users and not older.use_fake_user:
        bpy.data.images.remove(older)
    # A predecessor something else still binds (another mask's field) is not ours to remove later.
    if not previous.users: field[SUPERSEDED]=previous.name
    elif SUPERSEDED in field: del field[SUPERSEDED]


# Each action names its input, its output and who reads it (UI plan phase 6).
ACTIONS = (
    ('CONTACT', 'Sample Contact History',
     'Input: the animation frames and the selected Contact scene field · Output: a frozen contact-duration '
     'image on that mask · Read by: the mask (contact-driven wear)'),
    ('STRAIN', 'Rest-State Strain',
     'Input: the active mesh and a rest-state copy · Output: the open_painter_strain point attribute · '
     'Read by: Attribute scene fields and mesh masks (deformation-driven wear)'),
    ('MARK', 'Mark Selected Cut Faces',
     'Input: the selected faces of the active mesh · Output: the open_painter_cut_surface face attribute · '
     'Read by: Attribute mesh masks (cut-surface selection)'),
    ('REPORT', 'Performance Inventory',
     'Input: the active texture set · Output: image memory, layer, filter and instance counts in the status · '
     'Read by: you, when the preview slows down'),
    ('LOD', 'Stage Mesh LOD',
     'Input: the active mesh · Output: a decimated evaluated copy for inspection · Read by: Export preparation'),
)


class OP_OT_offline_analysis(SafeOperator,bpy.types.Operator):
    bl_idname='open_painter.offline_analysis'
    bl_label='Offline Analysis'
    bl_options={'REGISTER','UNDO'}
    action: bpy.props.EnumProperty(items=[(k,label,description) for k,label,description in ACTIONS])

    @classmethod
    def description(cls, context, properties):
        return next((description for key, _label, description in ACTIONS if key == properties.action), '')
    first_frame: bpy.props.IntProperty(name='First Frame',default=1)
    last_frame: bpy.props.IntProperty(name='Last Frame',default=24)
    step: bpy.props.IntProperty(name='Frame Step',default=1,min=1)
    rest_object: bpy.props.StringProperty(name='Rest Mesh')
    ratio: bpy.props.FloatProperty(name='LOD Ratio',default=.5,min=.01,max=1)

    def invoke(self,context,event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self,context):
        description = next((d for key, _label, d in ACTIONS if key == self.action), '')
        for part in description.split(' · '):
            self.layout.label(text=part, icon='INFO' if part.startswith('Input') else 'BLANK1')
        if self.action=='CONTACT':
            for name in ('first_frame','last_frame','step'): self.layout.prop(self,name)
        elif self.action=='STRAIN': self.layout.prop_search(self,'rest_object',context.scene,'objects')
        elif self.action=='LOD':
            self.layout.prop(self,'ratio')

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
            previous=mask.influence.image
            mask.influence.image=image; mask.influence.mode='FROZEN'
            mask.influence.provenance=json.dumps(result); invalidate(ts)
            retire_superseded(mask.influence,previous)
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


def draw_action(layout, action, text=None, icon='NONE'):
    """One relocated analysis action with what it needs and what it makes."""
    label, description = next((l, d) for key, l, d in ACTIONS if key == action)
    op = layout.operator('open_painter.offline_analysis', text=text or label + '…', icon=icon)
    op.action = action
    return op


class OP_PT_offline_analysis(bpy.types.Panel):
    """No longer registered (UI plan phase 6): each action sits where its input is — Contact
    History on Contact scene fields, Rest-State Strain and Mark Selected Cut Faces on attribute
    masks, Performance Inventory in the Performance menu, Stage Mesh LOD under Export › Prepare.
    Every action stays searchable (F3) through ``open_painter.offline_analysis``."""
    bl_label='Offline Analysis'
    bl_space_type='VIEW_3D'; bl_region_type='UI'; bl_category='Painter'
    bl_options={'DEFAULT_CLOSED'}

    def draw(self,context):
        for action,label,_description in ACTIONS:
            self.layout.operator('open_painter.offline_analysis',text=label).action=action
