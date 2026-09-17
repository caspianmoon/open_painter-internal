"""Native controls for explicit managed geometry operations."""
import bpy
from .operators import SafeOperator


class OP_OT_geometry_effect(SafeOperator,bpy.types.Operator):
    bl_idname='open_painter.geometry_effect'
    bl_label='Geometry Effect'
    bl_options={'REGISTER','UNDO'}
    action: bpy.props.EnumProperty(items=[(k,k.title().replace('_',' '),'') for k in (
        'RUST','MOSS','WELD','REGENERATE','FREEZE','UNFREEZE','REMOVE','SNOW','SPLINTERS','PEEL',
        'BOLT','SCALE','ROCK','BARK','DISPLACE','EXTRACT_CURVES','STITCH','SEAM','BAKE_BACK','APPLY','REPLACE_SOURCE')])
    density: bpy.props.FloatProperty(name='Instances per Square Unit',default=30,min=0,max=5000)
    size: bpy.props.FloatProperty(name='Size',default=.02,min=.0001,max=10)
    seed: bpy.props.IntProperty(name='Seed',default=1,min=0)
    lift: bpy.props.FloatProperty(name='Surface Lift',default=0,min=0,max=1)
    levels: bpy.props.IntProperty(name='Subdivision Levels',default=2,min=0,max=4)
    amplitude: bpy.props.FloatProperty(name='Amplitude',default=.05,min=0,max=10,subtype='DISTANCE')
    midlevel: bpy.props.FloatProperty(name='Midlevel',default=.5,min=0,max=1)
    smooth: bpy.props.BoolProperty(name='Smooth Subdivision',default=False)
    source_channel: bpy.props.EnumProperty(name='Source',items=[('HEIGHT','Height Channel',''),('MASK','Active Layer Mask','')],default='HEIGHT')
    spacing: bpy.props.FloatProperty(name='Spacing',default=.02,min=.0005,max=5,subtype='DISTANCE')
    then: bpy.props.EnumProperty(name='Then',items=[('KEEP','Keep Geometry',''),('HIDE','Hide Geometry',''),('REMOVE','Remove Geometry','')],default='KEEP')

    # Starting points per kind: instances per square metre and prototype size in metres.
    DEFAULTS={'RUST':(1500,.02),'MOSS':(3000,.08),'SNOW':(800,.07),'SPLINTERS':(800,.04),'PEEL':(600,.03),'WELD':(0,.015),
              'BOLT':(200,.03),'SCALE':(2500,.03),'ROCK':(300,.05),'BARK':(1200,.06),'STITCH':(0,.01),'SEAM':(0,.008)}

    def invoke(self,context,event):
        if self.action in self.DEFAULTS or self.action in {'DISPLACE','BAKE_BACK'}:
            if self.action in self.DEFAULTS:
                self.density,self.size=self.DEFAULTS[self.action]
            if self.action=='DISPLACE':
                from ..core.project_manager import active_set
                ts=active_set(context)
                if ts: self.amplitude=ts.height_distance
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self,context):
        layout=self.layout
        if self.action=='DISPLACE':
            for name in ('source_channel','levels','amplitude','midlevel','smooth'): layout.prop(self,name)
        elif self.action in {'STITCH','SEAM'}:
            for name in ('spacing','size','seed'): layout.prop(self,name)
        elif self.action=='BAKE_BACK':
            layout.prop(self,'then')
        else:
            for name in ('density','size','seed','lift'): layout.prop(self,name)

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
        elif self.action=='SEAM':
            helper=weld.create(context.scene,obj,self.size,ripple=0.05,period=8.0)
        elif self.action=='STITCH':
            from ..geometry_effects import stitch
            source=obj.parent if obj.type=='CURVE' else None
            helper=stitch.create(context.scene,obj,'STITCH',self.spacing,self.size,self.seed,source=source)
        elif self.action=='APPLY':
            from ..geometry_effects import displace
            helper=displace.apply(obj)
        elif self.action=='REPLACE_SOURCE':
            from ..geometry_effects import displace
            helper=displace.replace_source(obj)
        elif self.action=='BAKE_BACK':
            from ..baking import bake_back
            ts=require_set(context)
            helpers=bake_back.helpers_of(ts,context.scene)
            bake_back.run(context,ts,helpers,self.then)
            context.scene.open_painter.status=f'Baked {len(helpers)} generated helpers back into Normal and AO'
            helper=None
        elif self.action=='DISPLACE':
            from ..geometry_effects import displace
            from ..core.published_outputs import publish_channel,publish_mask
            ts=require_set(context)
            if self.source_channel=='HEIGHT':
                image=publish_channel(ts,'HEIGHT')
            else:
                layer=active(ts)
                if layer is None: raise ValueError('Select the layer whose mask drives the displacement')
                image=publish_mask(ts,layer)
            previous=None
            if obj.get('kind')=='DISPLACE' and obj.parent is not None:
                # The last helper is active after a run: a second click retunes the source
                # and replaces the live helper instead of displacing the displaced copy.
                previous=None if obj.get('frozen') else obj
                obj=obj.parent
            helper=displace.create(ts,obj,image,self.levels,self.amplitude,self.midlevel,self.smooth,replacing=previous)
            if previous is not None: manager.remove(previous)
        elif self.action=='EXTRACT_CURVES':
            from ..geometry_effects import curves
            from ..core.published_outputs import publish_mask
            ts=require_set(context)
            layer=active(ts)
            if layer is None: raise ValueError('Select the layer whose mask outlines the curves')
            image=publish_mask(ts,layer)
            steps=curves.extract_steps(ts,image)
            while True:
                try: next(steps)
                except StopIteration as done:
                    polylines=done.value; break
            if not polylines: raise ValueError('The mask has no lines to trace; paint a thin line first')
            helper=curves.create_curve(context.scene,obj,polylines,'OP Curves · '+layer.name,manager.TAG)
        else:
            ts=require_set(context)
            if self.action=='REGENERATE':
                if obj.get('kind')=='DISPLACE':
                    from ..geometry_effects import displace
                    helper=displace.regenerate(ts,obj)
                else:
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
        layout.label(text='Separate helpers · active layer mask · owned materials')
        row=layout.row(align=True)
        for action,label in [('RUST','Rust Flakes'),('MOSS','Moss'),('WELD','Weld Curve')]:
            row.operator('open_painter.geometry_effect',text=label).action=action
        layout.label(text='Experimental surface prototypes')
        row=layout.row(align=True)
        for action,label in [('SNOW','Snow Clumps'),('SPLINTERS','Splinters'),('PEEL','Peel Fragments')]:
            row.operator('open_painter.geometry_effect',text=label).action=action
        layout.label(text='Material → Geometry',icon='MOD_DISPLACE')
        row=layout.row(align=True)
        row.operator('open_painter.geometry_effect',text='Displace from Height').action='DISPLACE'
        row.operator('open_painter.geometry_effect',text='Extract Curves').action='EXTRACT_CURVES'
        row=layout.row(align=True)
        for action,label in [('BOLT','Bolts'),('SCALE','Scales'),('ROCK','Rocks'),('BARK','Bark')]:
            row.operator('open_painter.geometry_effect',text=label).action=action
        row=layout.row(align=True)
        row.operator('open_painter.geometry_effect',text='Stitches on Curve').action='STITCH'
        row.operator('open_painter.geometry_effect',text='Seam Bead on Curve').action='SEAM'
        row=layout.row(align=True)
        row.operator('open_painter.geometry_effect',text='Bake Back to Normal / AO',icon='RENDER_STILL').action='BAKE_BACK'
        obj=context.active_object
        if obj and obj.get('kind')=='DISPLACE':
            row=layout.row(align=True)
            row.operator('open_painter.geometry_effect',text='Apply').action='APPLY'
            row.operator('open_painter.geometry_effect',text='Replace Source Mesh').action='REPLACE_SOURCE'
        if obj and obj.get('open_painter_geometry_effect')=='INSTANCE_HELPER':
            from ..geometry_effects import manager
            layout.label(text=f"{obj.get('instance_count',0):,} instances · bounded geometry")
            kind=obj.get('kind')
            # Weld, stitch and seam helpers have no regenerate path; a displaced copy is retuned
            # through its modifier inputs (Amplitude, Midlevel), so only its creation-time keys show.
            if kind in manager.KINDS or kind=='DISPLACE':
                for name in (('levels','smooth') if kind=='DISPLACE' else ('density','size','seed','lift')):
                    layout.prop(obj,f'["{name}"]',text=name.title())
                layout.operator('open_painter.geometry_effect',text='Regenerate').action='REGENERATE'
                layout.operator('open_painter.geometry_effect',text='Unfreeze' if obj.get('frozen') else 'Freeze').action='UNFREEZE' if obj.get('frozen') else 'FREEZE'
            layout.operator('open_painter.geometry_effect',text='Remove Helper').action='REMOVE'
