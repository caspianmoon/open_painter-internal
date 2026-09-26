"""Native controls for explicit managed geometry operations."""
import bpy
from .operators import SafeOperator


class OP_OT_geometry_effect(SafeOperator,bpy.types.Operator):
    bl_idname='open_painter.geometry_effect'
    bl_label='Geometry Effect'
    bl_options={'REGISTER','UNDO'}
    action: bpy.props.EnumProperty(items=[(k,k.title().replace('_',' '),'') for k in (
        'RUST','MOSS','WELD','REGENERATE','FREEZE','UNFREEZE','REMOVE','SNOW','SPLINTERS','PEEL',
        'BOLT','SCALE','ROCK','BARK','DISPLACE','EXTRACT_CURVES','STITCH','SEAM','BAKE_BACK','APPLY','REPLACE_SOURCE',
        'REGENERATE_ALL')])
    density: bpy.props.FloatProperty(name='Instances per Square Unit',default=30,min=0,max=5000)
    size: bpy.props.FloatProperty(name='Size',default=.02,min=.0001,max=10,
                                  description='Instance size in metres; for painted spots, a multiplier of the spot size')
    seed: bpy.props.IntProperty(name='Seed',default=1,min=0)
    lift: bpy.props.FloatProperty(name='Surface Lift',default=0,min=0,max=1)
    source: bpy.props.EnumProperty(name='Where', items=[(k, l, d) for k, l, d in (
        ('MATERIAL', 'What the material shows', 'The aging outcome this effect materialises (rust, coating loss, growth, checking); mesh edges, cavities and faces without aging'),
        ('MASK', 'Active layer mask', "Density from the active layer's mask"),
        ('SPOTS', 'Painted spots', "One piece per blob of the active layer's mask, sized by the blob"),
        ('SURFACE', 'Whole surface', 'Everywhere the set is assigned'))], default='MATERIAL')
    prototypes: bpy.props.StringProperty(name='Own Pieces', default='',
                                         description="A collection whose meshes are instanced instead of the built-in shapes",
                                         search=lambda self, context, text: [c.name for c in bpy.data.collections if text.lower() in c.name.lower()])
    match: bpy.props.FloatProperty(name='Match Surface', default=.7, min=0, max=1, subtype='FACTOR',
                                   description='How much each piece takes the colour of the surface under it')
    follow: bpy.props.BoolProperty(name='Follow the Material', default=True,
                                   description='Regenerate when the aging or the driving mask changes')
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
                from ..geometry_effects import sources, materials
                self.density,self.size=self.DEFAULTS[self.action]
                self.source=sources.DEFAULT_SOURCE.get(self.action,'MASK')
                self.match=materials.MATCH.get(self.action,0.0)
                if self.action=='BOLT': self.size=1.0
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
            layout.prop(self,'source')
            if self.source in {'MASK','SPOTS'}:
                from ..core.project_manager import active_set
                from ..core.layer_stack import active
                ts=active_set(context); layer=active(ts) if ts else None
                layout.label(text=f"Layer: {layer.name}" if layer else 'Select a layer with a mask', icon='INFO' if layer else 'ERROR')
            elif self.source=='MATERIAL':
                layout.label(text=self._material_hint(context), icon='INFO')
            names=('size','seed','lift') if self.source=='SPOTS' else ('density','size','seed','lift')
            for name in names: layout.prop(self,name)
            layout.prop(self,'prototypes')
            layout.prop(self,'match',slider=True)
            layout.prop(self,'follow')

    def _material_hint(self,context):
        from ..core.project_manager import active_set
        from ..aging import engine
        ts=active_set(context) if getattr(context,'scene',None) is not None else None
        aged=ts is not None and engine.folder_of(ts) is not None
        return {'RUST':'Where the aging shows corrosion and rust','PEEL':'Along the edge of the coating loss',
                'MOSS':'Where the aging shows growth','SPLINTERS':'Where wood checks and dents',
                'SNOW':'Upward faces, deeper in hollows'}.get(self.action,'The whole surface') if aged else {
                'RUST':'Mesh edges and cavities (no aging on this set)','PEEL':'Mesh edges (no aging on this set)',
                'MOSS':'Cavities, shade and the ground (no aging on this set)','SPLINTERS':'Mesh edges (no aging on this set)',
                'SNOW':'Upward faces, deeper in hollows'}.get(self.action,'The whole surface')

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
        elif self.action=='REGENERATE_ALL':
            ts=require_set(context)
            helpers=manager.helpers_of(ts)
            if not helpers: raise ValueError('This set has no geometry helpers to regenerate')
            replaced=[manager.regenerate(ts,item) for item in helpers]
            context.scene.open_painter.status=f'Regenerated {len(replaced)} helpers on the material as it is now'
            helper=next((item for item in replaced if item is not None),None)
        else:
            ts=require_set(context)
            if self.action=='REGENERATE':
                if obj.get('kind')=='DISPLACE':
                    from ..geometry_effects import displace
                    helper=displace.regenerate(ts,obj)
                else:
                    helper=manager.regenerate(ts,obj)
            else:
                from ..geometry_effects import sources
                layer=active(ts) if self.source in {'MASK','SPOTS'} else None
                if self.source in {'MASK','SPOTS'} and layer is None:
                    raise ValueError('Select the layer whose mask places the geometry')
                placement=sources.placement(ts,self.action,self.source,layer)
                if not placement.spots and float(placement.density.max())<=0:
                    raise ValueError(f'Nothing to place: {placement.label} is empty on this set')
                prototypes=bpy.data.collections.get(self.prototypes) if self.prototypes else None
                if self.prototypes and prototypes is None:
                    raise ValueError(f'No collection named {self.prototypes!r}')
                helper=manager.create(ts,obj,None,self.action,self.density,self.size,self.seed,self.lift,placement=placement,
                                      prototypes=prototypes,match=self.match,follow=self.follow,layer_id=placement.layer_id)
                if helper is None:
                    raise ValueError(f'No instances survived the density and spacing on {placement.label}')
                context.scene.open_painter.status=(f"{helper['instance_count']:,} pieces on {placement.label}"
                                                   + (" · the project's piece budget is reached" if helper.get('capped') else ''))
        if helper:
            for selected in context.selected_objects: selected.select_set(False)
            helper.select_set(True); context.view_layer.objects.active=helper


PRESETS = (('Weathering', 'MOD_OCEAN', (('RUST', 'Rust Flakes'), ('PEEL', 'Peel Fragments'), ('MOSS', 'Moss'),
                                        ('SPLINTERS', 'Splinters'), ('SNOW', 'Snow'))),
           ('Details', 'MOD_PARTICLES', (('BOLT', 'Bolts'), ('SCALE', 'Scales'), ('ROCK', 'Rocks'), ('BARK', 'Bark'))),
           ('Curves and Relief', 'MOD_DISPLACE', (('EXTRACT_CURVES', 'Extract Curves from Mask'), ('WELD', 'Weld Seam'),
                                                  ('SEAM', 'Rippled Seam'), ('STITCH', 'Stitches'),
                                                  ('DISPLACE', 'Displace from Height'))))


class OP_MT_geometry_effect_add(bpy.types.Menu):
    """Creation presets of geometry effects (also on the asset shelf)."""
    bl_idname = 'OP_MT_geometry_effect_add'
    bl_label = 'Add Geometry Effect'

    def draw(self, context):
        layout = self.layout
        for group, icon, items in PRESETS:
            layout.label(text=group, icon=icon)
            for action, label in items:
                layout.operator('open_painter.geometry_effect', text=label).action = action
            layout.separator()


EFFECT_RESULTS = {
    'DISPLACE': ('Changes displacement', 'A displaced evaluated copy of the mesh; the source stays'),
    'WELD': ('Adds geometry', 'A weld bead mesh along the seam'),
    'SEAM': ('Adds geometry', 'A rippled seam mesh'),
    'STITCH': ('Adds geometry', 'Stitch instances along the curve'),
    'CURVES': ('Adds geometry', 'Curves traced from the mask'),
}


def helpers_of_set(ts, scene):
    """Every geometry helper made for this set, frozen ones included, for the applied list."""
    from ..geometry_effects import manager
    return [obj for obj in scene.objects if obj.get(manager.TAG) == 'INSTANCE_HELPER'
            and obj.get('source_texture_set') == ts.id]


def effect_result(obj):
    """What the effect produces: geometry, displacement or texture detail once baked back."""
    from ..geometry_effects import manager
    kind = obj.get('kind', '')
    if kind in manager.KINDS:
        return 'Adds geometry', f"{obj.get('instance_count', 0):,} instanced pieces; bake back for texture detail"
    return EFFECT_RESULTS.get(kind, ('Adds geometry', 'Generated helper object'))


class OP_OT_geometry_select(bpy.types.Operator):
    bl_idname = 'open_painter.geometry_select'
    bl_label = 'Select Geometry Effect'
    bl_description = 'Make this helper the active object so its controls show below'
    bl_options = {'REGISTER'}
    name: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.scene.objects.get(self.name)
        if obj is None:
            self.report({'ERROR'}, 'That helper is gone.')
            return {'CANCELLED'}
        for other in context.view_layer.objects:
            other.select_set(other == obj)
        context.view_layer.objects.active = obj
        return {'FINISHED'}


def draw_effect_inspector(layout, context, ts, obj):
    """Controls of the selected helper: what it is, where it comes from, what it produces."""
    from ..geometry_effects import manager
    box = layout.box()
    kind = obj.get('kind', '')
    what, detail = effect_result(obj)
    row = box.row(align=True)
    row.label(text=f"{kind.replace('_', ' ').title()} · {obj.name}", icon='OUTLINER_OB_POINTCLOUD')
    box.label(text=f"{what} · {detail}", icon='INFO')
    if obj.get('source_label'):
        box.label(text=f"Distribution: {obj['source_label']}", icon='MOD_MASK')
    if obj.get('frozen'):
        box.label(text='Frozen: keeps its pieces while the material changes', icon='FREEZE')
    if kind in manager.KINDS or kind == 'DISPLACE':
        if kind in manager.KINDS and 'follow' in obj:
            box.prop(obj, '["follow"]', text='Follow the Material', toggle=True)
        names = ('levels', 'smooth') if kind == 'DISPLACE' else ('size', 'seed', 'lift', 'match') if obj.get('source') == 'SPOTS' else ('density', 'size', 'seed', 'lift', 'match')
        for name in names:
            if name in obj:
                box.prop(obj, f'["{name}"]', text=name.title() if name != 'match' else 'Match Surface')
        row = box.row(align=True)
        row.operator('open_painter.geometry_effect', text='Regenerate', icon='FILE_REFRESH').action = 'REGENERATE'
        row.operator('open_painter.geometry_effect', text='Unfreeze' if obj.get('frozen') else 'Freeze',
                     icon='FREEZE').action = 'UNFREEZE' if obj.get('frozen') else 'FREEZE'
    if kind == 'DISPLACE':
        row = box.row(align=True)
        row.operator('open_painter.geometry_effect', text='Apply').action = 'APPLY'
        row.operator('open_painter.geometry_effect', text='Replace Source Mesh').action = 'REPLACE_SOURCE'
    box.operator('open_painter.geometry_effect', text='Remove Effect', icon='TRASH').action = 'REMOVE'


class OP_PT_geometry_effects(bpy.types.Panel):
    bl_label='Geometry Effects'
    bl_space_type='VIEW_3D'
    bl_region_type='UI'
    bl_category='Painter'
    bl_options={'DEFAULT_CLOSED'}
    bl_order=6   # after the everyday layer workflow, before bake and export

    @classmethod
    def poll(cls,context):
        from .editor import is_area
        return context.area and is_area(context.area)

    def draw(self,context):
        layout=self.layout
        from ..core.project_manager import active_set
        from ..aging import engine
        ts=active_set(context) if getattr(context,'scene',None) is not None else None
        active=getattr(context,'active_object',None)
        row=layout.row(align=True)
        row.menu('OP_MT_geometry_effect_add', text='Add Geometry Effect', icon='ADD')
        row.operator('open_painter.geometry_effect', text='', icon='FILE_REFRESH').action='REGENERATE_ALL'
        if ts is None:
            layout.label(text='Create a texture set to place pieces by its material', icon='INFO')
            helpers=[]
        else:
            aged=engine.folder_of(ts) is not None
            layout.label(text='Pieces go where the material shows them' if aged else 'Age the material to place pieces by its wear; else by mesh features',
                         icon='TIME' if aged else 'INFO')
            helpers=helpers_of_set(ts, context.scene)
        column=layout.column(align=True)
        if ts is not None and not helpers:
            column.label(text='No geometry effect applied to this set yet', icon='BLANK1')
        for obj in helpers:
            row=column.row(align=True)
            row.prop(obj, 'hide_viewport', text='', icon='HIDE_ON' if obj.hide_viewport else 'HIDE_OFF', emboss=False)
            what=effect_result(obj)[0]
            label=f"{obj.get('kind','').replace('_',' ').title()} · {obj.get('source_label','') or what}"
            op=row.operator('open_painter.geometry_select', text=label, icon='OUTLINER_OB_POINTCLOUD',
                            depress=obj==active, emboss=obj==active)
            op.name=obj.name
            if obj.get('frozen'):
                row.label(text='', icon='FREEZE')
        if active is not None and active.get('open_painter_geometry_effect')=='INSTANCE_HELPER':
            draw_effect_inspector(layout, context, ts, active)
        head,body=layout.panel('painter_geometry_bake_back', default_closed=True)
        head.label(text='Bake Back to Normal / AO', icon='RENDER_STILL')
        if body is not None:
            body.label(text='Output: the set\'s Normal and AO maps gain the pieces\' relief', icon='INFO')
            body.label(text='Generated geometry is kept unless you choose Hide or Remove', icon='INFO')
            body.operator('open_painter.geometry_effect', text='Bake Back…', icon='RENDER_STILL').action='BAKE_BACK'
