# SPDX-License-Identifier: GPL-2.0-or-later
"""Built-in Open Painter controls for the native dock editors."""
import math
import json
from bpy.app.handlers import persistent
import uuid
from pathlib import Path
import bpy.utils.previews
_previews=None
import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty, StringProperty


def material(context):
    obj=context.object
    return obj.active_material if obj and obj.type=='MESH' else None


def changed(self, context):
    mat=self.id_data
    if isinstance(mat,bpy.types.Material):
        sync(mat)
        active_changed(mat,context)


def active_changed(self,context):
    mat=self if isinstance(self,bpy.types.Material) else self.id_data
    if not mat.painter_layers or not context.screen:return
    if mat.painter_active>=len(mat.painter_layers):return
    image=mat.painter_layers[mat.painter_active].image
    if image and context.workspace.name=='Open Painter':
        for area in context.screen.areas:
            if area.type=='IMAGE_EDITOR':area.spaces.active.image=image


class PainterLayer(bpy.types.PropertyGroup):
    uid: StringProperty()
    parent: StringProperty()
    name: StringProperty(default='Fill layer')
    kind: StringProperty(default='FILL')
    visible: BoolProperty(default=True,update=changed)
    expanded: BoolProperty(default=True)
    blend: EnumProperty(items=[('MIX','Normal',''),('MULTIPLY','Multiply',''),('SCREEN','Screen',''),('OVERLAY','Overlay',''),('ADD','Add',''),('SUBTRACT','Subtract','')],update=changed)
    opacity: FloatProperty(default=1,min=0,max=1,update=changed)
    opacity_percent: FloatProperty(name='Opacity',min=0,max=100,get=lambda self:self.opacity*100,set=lambda self,value:setattr(self,'opacity',value/100))
    color: FloatVectorProperty(name='Base Color',size=4,subtype='COLOR',default=(.5,.5,.5,1),min=0,max=1,update=changed)
    roughness: FloatProperty(name='Roughness',default=.5,min=0,max=1,update=changed)
    metallic: FloatProperty(name='Metallic',default=0,min=0,max=1,update=changed)
    mask: EnumProperty(name='Mask',items=[('NONE','No mask',''),('WHITE','White mask',''),('BLACK','Black mask',''),('BITMAP','Bitmap mask','')],update=changed)
    mask_image: PointerProperty(type=bpy.types.Image,update=changed)
    tiling: FloatVectorProperty(name='Tiling',size=2,default=(1,1),min=.001,update=changed)
    offset: FloatVectorProperty(name='Offset',size=2,default=(0,0),update=changed)
    rotation: FloatProperty(name='Rotation',subtype='ANGLE',update=changed)
    image: PointerProperty(type=bpy.types.Image,update=changed)
    filtering: EnumProperty(name='Filtering',items=[('Linear','Bilinear',''),('Closest','Nearest',''),('Cubic','Bicubic','')],update=changed)
    wrap: EnumProperty(name='UV Wrap',items=[('REPEAT','Repeat',''),('EXTEND','Extend',''),('CLIP','Clip','')],update=changed)


def sync(mat):
    """Rebuild on structure changes only. Property edits update existing GPU node inputs."""
    if mat.get('_painter_building'): return
    mat['_painter_building']=True
    try:
        mat.use_nodes=True
        tree=mat.node_tree
        layers=mat.painter_layers
        signature=repr([(l.uid,l.parent,l.kind,bool(l.image),l.mask=='BITMAP' and bool(l.mask_image)) for l in layers])
        if mat.get('_painter_signature')!=signature:
            tree.nodes.clear()
            out=tree.nodes.new('ShaderNodeOutputMaterial')
            shader=tree.nodes.new('ShaderNodeBsdfPrincipled')
            tree.links.new(shader.outputs['BSDF'],out.inputs['Surface'])
            shader.name='Painter Surface'
            def constant(name,kind,value):
                n=tree.nodes.new(kind);n.name=name;n.outputs[0].default_value=value;return n.outputs[0]
            defaults=[constant('Base','ShaderNodeRGB',(.18,.18,.18,1)),constant('Rough','ShaderNodeValue',.5),constant('Metal','ShaderNodeValue',0)]
            def stack(parent,base,visited):
                for l in layers:
                    if l.parent!=parent or l.uid in visited: continue
                    channels=[]
                    if l.kind=='GROUP':
                        channels=stack(l.uid,list(base),visited|{l.uid})
                    else:
                        color=constant(l.uid+'color','ShaderNodeRGB',l.color)
                        if l.image:
                            uv=tree.nodes.new('ShaderNodeTexCoord')
                            mapping=tree.nodes.new('ShaderNodeMapping');mapping.name=l.uid+'mapping'
                            tex=tree.nodes.new('ShaderNodeTexImage');tex.name=l.uid+'image'
                            tree.links.new(uv.outputs['UV'],mapping.inputs['Vector'])
                            tree.links.new(mapping.outputs['Vector'],tex.inputs['Vector'])
                            color=tex.outputs['Color']
                        channels=[color,constant(l.uid+'rough','ShaderNodeValue',l.roughness),constant(l.uid+'metal','ShaderNodeValue',l.metallic)]
                    factor=None
                    if l.kind=='PAINT' and l.image:
                        alpha=tree.nodes.new('ShaderNodeMath');alpha.operation='MULTIPLY';alpha.name=l.uid+'alpha'
                        tree.links.new(tree.nodes[l.uid+'image'].outputs['Alpha'],alpha.inputs[1])
                        factor=alpha.outputs[0]
                    if l.mask=='BITMAP' and l.mask_image:
                        tex=tree.nodes.new('ShaderNodeTexImage');tex.name=l.uid+'mask_image'
                        gray=tree.nodes.new('ShaderNodeRGBToBW')
                        multiply=tree.nodes.new('ShaderNodeMath');multiply.operation='MULTIPLY';multiply.name=l.uid+'mask_opacity'
                        tree.links.new(tex.outputs['Color'],gray.inputs[0]);tree.links.new(gray.outputs[0],multiply.inputs[1])
                        if factor:
                            combined=tree.nodes.new('ShaderNodeMath');combined.operation='MULTIPLY'
                            tree.links.new(factor,combined.inputs[0]);tree.links.new(multiply.outputs[0],combined.inputs[1]);factor=combined.outputs[0]
                        else:factor=multiply.outputs[0]
                    for i in range(3):
                        mix=tree.nodes.new('ShaderNodeMixRGB');mix.name=l.uid+'mix'+str(i)
                        if factor:tree.links.new(factor,mix.inputs[0])
                        tree.links.new(base[i],mix.inputs[1]);tree.links.new(channels[i],mix.inputs[2]);base[i]=mix.outputs[0]
                return base
            for socket,value in zip(('Base Color','Roughness','Metallic'),stack('',list(defaults),set())):
                tree.links.new(value,shader.inputs[socket])
            mat['_painter_signature']=signature
        for l in layers:
            for i in range(3):
                n=tree.nodes.get(l.uid+'mix'+str(i))
                if n:
                    n.blend_type='MIX' if l.kind=='GROUP' else l.blend
                    n.inputs[0].default_value=l.opacity if l.visible and l.mask!='BLACK' else 0
            alpha=tree.nodes.get(l.uid+'alpha')
            if alpha:alpha.inputs[0].default_value=(1 if l.mask=='BITMAP' and l.mask_image else l.opacity) if l.visible and l.mask!='BLACK' else 0
            mask_tex=tree.nodes.get(l.uid+'mask_image')
            if mask_tex:
                mask_tex.image=l.mask_image
                tree.nodes[l.uid+'mask_opacity'].inputs[0].default_value=l.opacity if l.visible else 0
            for suffix,value in [('color',l.color),('rough',l.roughness),('metal',l.metallic)]:
                n=tree.nodes.get(l.uid+suffix)
                if n: n.outputs[0].default_value=value
            tex=tree.nodes.get(l.uid+'image')
            if tex:
                tex.image=l.image;tex.interpolation=l.filtering;tex.extension=l.wrap
                mapping=tree.nodes[l.uid+'mapping']
                mapping.inputs['Scale'].default_value=(*l.tiling,1)
                mapping.inputs['Location'].default_value=(*l.offset,0)
                mapping.inputs['Rotation'].default_value=(0,0,l.rotation)
        preserve_layers(mat)
    finally: mat['_painter_building']=False



LAYER_FIELDS=('uid','parent','name','kind','visible','opacity','color','roughness','metallic','mask','tiling','offset','rotation','filtering','wrap','blend','expanded')
def preserve_layers(mat):
    records=[]
    for layer in mat.painter_layers:
        record={key:(list(getattr(layer,key)) if key in ('color','tiling','offset') else getattr(layer,key)) for key in LAYER_FIELDS}
        record['image']=layer.image.name if layer.image else None
        record['mask_image']=layer.mask_image.name if layer.mask_image else None
        records.append(record)
    mat['open_painter_layers']=json.dumps({'version':1,'layers':records})

@persistent
def save_layers(*args):
    for mat in bpy.data.materials:
        if '_painter_signature' in mat:preserve_layers(mat)

@persistent
def restore_layers(*args):
    for mat in bpy.data.materials:
        if mat.painter_layers or not mat.get('open_painter_layers'):continue
        data=json.loads(mat['open_painter_layers'])
        if data.get('version')!=1:continue
        mat['_painter_building']=True
        try:
            for record in data['layers']:
                layer=mat.painter_layers.add()
                for key in LAYER_FIELDS:
                    if key in record:setattr(layer,key,record[key])
                for key in ('image','mask_image'):setattr(layer,key,bpy.data.images.get(record.get(key) or ''))
        finally:mat['_painter_building']=False


class PAINTER_OT_new(bpy.types.Operator):
    @classmethod
    def poll(cls,context):return context.object is not None and context.object.type=='MESH'
    bl_idname='painter.new_set';bl_label='New Texture Set';bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        obj=context.object
        if not obj or obj.type!='MESH': return {'CANCELLED'}
        mat=bpy.data.materials.new('Painter Material');obj.data.materials.append(mat)
        obj.active_material_index=len(obj.data.materials)-1
        for polygon in obj.data.polygons:polygon.material_index=obj.active_material_index
        sync(mat)
        return {'FINISHED'}


class PAINTER_OT_layer(bpy.types.Operator):
    @classmethod
    def poll(cls,context):return context.object is not None and context.object.type=='MESH'
    bl_idname='painter.layer';bl_label='Add Layer';bl_options={'REGISTER','UNDO'}
    kind: StringProperty(default='FILL')
    def execute(self,context):
        mat=material(context)
        if not mat or '_painter_signature' not in mat and not mat.painter_layers:
            bpy.ops.painter.new_set();mat=material(context)
        if not mat:return {'CANCELLED'}
        selected=mat.painter_layers[mat.painter_active] if mat.painter_layers else None
        parent=selected.uid if selected and selected.kind=='GROUP' else (selected.parent if selected else '')
        mat['_painter_building']=True
        l=mat.painter_layers.add();l.uid=uuid.uuid4().hex;l.parent=parent;l.kind=self.kind
        l.name='Folder' if self.kind=='GROUP' else 'Fill layer'
        mat.painter_active=len(mat.painter_layers)-1;mat['_painter_building']=False;sync(mat)
        return {'FINISHED'}


class PAINTER_OT_delete(bpy.types.Operator):
    @classmethod
    def poll(cls,context):
        mat=material(context)
        return mat is not None and bool(mat.painter_layers)
    bl_idname='painter.delete';bl_label='Delete Layer';bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        mat=material(context)
        if not mat or not mat.painter_layers:return {'CANCELLED'}
        ids={mat.painter_layers[mat.painter_active].uid}
        for _ in mat.painter_layers:
            ids.update(l.uid for l in mat.painter_layers if l.parent in ids)
        for i in reversed(range(len(mat.painter_layers))):
            if mat.painter_layers[i].uid in ids:mat.painter_layers.remove(i)
        mat.painter_active=max(0,min(mat.painter_active,len(mat.painter_layers)-1));sync(mat)
        return {'FINISHED'}


class PAINTER_OT_mask(bpy.types.Operator):
    @classmethod
    def poll(cls,context):
        mat=material(context)
        return mat is not None and bool(mat.painter_layers)
    bl_idname='painter.mask';bl_label='Add Mask';bl_options={'REGISTER','UNDO'}
    kind: StringProperty(default='WHITE')
    def execute(self,context):
        mat=material(context)
        if not mat or not mat.painter_layers:return {'CANCELLED'}
        mat.painter_layers[mat.painter_active].mask=self.kind
        return {'FINISHED'}


class PAINTER_MT_masks(bpy.types.Menu):
    bl_label='Masks'
    def draw(self,context):
        for kind,label in [('WHITE','Add white mask'),('BLACK','Add black mask'),('BITMAP','Add bitmap mask'),('NONE','Remove mask')]:
            self.layout.operator('painter.mask',text=label).kind=kind


class PAINTER_UL_layers(bpy.types.UIList):
    def filter_items(self,context,data,propname):
        layers=getattr(data,propname);flags=[0]*len(layers);order=[]
        def walk(parent,visible,seen):
            for i in reversed(range(len(layers))):
                item=layers[i]
                if item.parent!=parent or item.uid in seen:continue
                order.append(i)
                if visible:flags[i]=self.bitflag_filter_item
                walk(item.uid,visible and item.expanded,seen|{item.uid})
        walk('',True,set())
        order.extend(i for i in range(len(layers)) if i not in order)
        positions=[0]*len(order)
        for position,index in enumerate(order):positions[index]=position
        return flags,positions
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        row=layout.row(align=True)
        row.prop(item,'visible',text='',icon='HIDE_OFF' if item.visible else 'HIDE_ON',emboss=False)
        parent=item.parent;seen=set();by_id={x.uid:x for x in data.painter_layers}
        while parent in by_id and parent not in seen:
            row.label(text='',icon='BLANK1');seen.add(parent);parent=by_id[parent].parent
        if item.kind=='GROUP':row.prop(item,'expanded',text='',icon='TRIA_DOWN' if item.expanded else 'TRIA_RIGHT',emboss=False)
        row.prop(item,'name',text='',icon='FILE_FOLDER' if item.kind=='GROUP' else 'MATERIAL',emboss=False)
        if item.mask!='NONE':row.label(text='',icon='SELECT_SET' if item.mask=='WHITE' else 'SELECT_SUBTRACT')
        if item.kind=='GROUP':row.label(text='Normal')
        else:row.prop(item,'blend',text='')
        row.prop(item,'opacity_percent',text='',slider=True)


class PAINTER_PT_sets(bpy.types.Panel):
    bl_space_type='PAINTER_LAYERS';bl_region_type='WINDOW';bl_label='TEXTURE SET LIST'
    def draw(self,context):
        l=self.layout;obj=context.object
        if obj and obj.type=='MESH':
            l.template_list('MATERIAL_UL_matslots','',obj,'material_slots',obj,'active_material_index',rows=3)
        l.operator('painter.new_set',icon='ADD')
        l.row(align=True).prop(context.scene,'painter_tab',expand=True)


class PAINTER_PT_layers(bpy.types.Panel):
    @classmethod
    def poll(cls,context):return context.scene.painter_tab=='LAYERS'
    bl_space_type='PAINTER_LAYERS';bl_region_type='WINDOW';bl_label='LAYERS'
    def draw(self,context):
        l=self.layout;mat=material(context)
        row=l.row(align=True)
        row.operator('painter.layer',text='Fill',icon='MATERIAL').kind='FILL'
        row.operator('painter.layer',text='',icon='FILE_FOLDER').kind='GROUP'
        row.menu('PAINTER_MT_masks',text='',icon='MOD_MASK')
        row.operator('painter.delete',text='',icon='TRASH')
        if mat:l.template_list('PAINTER_UL_layers','',mat,'painter_layers',mat,'painter_active',rows=8)



class PAINTER_PT_settings(bpy.types.Panel):
    bl_space_type='PAINTER_LAYERS';bl_region_type='WINDOW';bl_label='TEXTURE SET SETTINGS'
    @classmethod
    def poll(cls,context):return context.scene.painter_tab=='SETTINGS'
    def draw(self,context):
        mat=material(context);l=self.layout
        if not mat:return
        l.prop(mat,'name',text='Name');l.label(text='Shader: Principled BSDF')
        for layer in mat.painter_layers:
            if layer.image:l.label(text=f'{layer.image.name}: {layer.image.size[0]} × {layer.image.size[1]}')

class PAINTER_PT_fill(bpy.types.Panel):
    bl_space_type='PAINTER_LAYERS';bl_region_type='WINDOW';bl_label='PROPERTIES — FILL'
    def draw(self,context):
        mat=material(context);l=self.layout
        if not mat or not mat.painter_layers:
            l.label(text='Select a layer');return
        item=mat.painter_layers[mat.painter_active]
        l.prop(item,'opacity_percent');l.prop(item,'mask')
        if item.mask=='BITMAP':l.template_ID(item,'mask_image',open='image.open')
        if item.kind=='GROUP':return
        l.label(text='FILL');l.label(text='Projection: UV')
        l.template_ID(item,'image',open='image.open')
        l.prop(item,'filtering');l.prop(item,'wrap')
        l.label(text='UV transformations');l.prop(item,'tiling');l.prop(item,'rotation');l.prop(item,'offset')
        l.separator();l.label(text='MATERIAL');l.prop(item,'color');l.prop(item,'roughness');l.prop(item,'metallic')


class PAINTER_OT_asset(bpy.types.Operator):
    @classmethod
    def poll(cls,context):return context.object is not None and context.object.type=='MESH'
    bl_idname='painter.asset';bl_label='Apply Material';bl_options={'REGISTER','UNDO'}
    preset: IntProperty()
    def execute(self,context):
        bpy.ops.painter.layer(kind='FILL')
        mat=material(context)
        if not mat:return {'CANCELLED'}
        item=mat.painter_layers[mat.painter_active]
        name,color,rough,metal=PRESETS[self.preset]
        item.name=name;item.color=(*color,1);item.roughness=rough;item.metallic=metal
        return {'FINISHED'}

PRESETS=[('Steel',(.45,.48,.5),.25,1),('Copper',(.65,.25,.08),.3,1),('Rubber',(.025,.025,.025),.85,0),('Porcelain',(.8,.8,.75),.15,0)]
class PAINTER_PT_assets(bpy.types.Panel):
    bl_space_type='PAINTER_ASSETS';bl_region_type='WINDOW';bl_label='ASSETS'
    def draw(self,context):
        l=self.layout;l.prop(context.scene,'painter_search',text='',icon='VIEWZOOM');l.label(text='Material presets')
        grid=l.grid_flow(columns=4,even_columns=True,even_rows=True)
        for i,(name,*_) in enumerate(PRESETS):
            if context.scene.painter_search.casefold() not in name.casefold():continue
            col=grid.column(align=True)
            if _previews and name in _previews:col.template_icon(icon_value=_previews[name].icon_id,scale=3.5)
            col.operator('painter.asset',text=name).preset=i
        l.separator();l.operator('wm.append',text='Import Assets',icon='IMPORT')

classes=[PainterLayer,PAINTER_OT_new,PAINTER_OT_layer,PAINTER_OT_delete,PAINTER_OT_mask,PAINTER_MT_masks,PAINTER_UL_layers,PAINTER_PT_sets,PAINTER_PT_layers,PAINTER_PT_settings,PAINTER_PT_fill,PAINTER_OT_asset,PAINTER_PT_assets]
def register():
    global _previews
    _previews=bpy.utils.previews.new()
    for name,*_ in PRESETS:
        path=Path(__file__).parent/'open_painter_previews'/(name+'.png')
        if path.exists():_previews.load(name,str(path),'IMAGE')
    for cls in classes:bpy.utils.register_class(cls)
    bpy.types.Scene.painter_search=StringProperty(name='Search assets')
    bpy.types.Scene.painter_tab=EnumProperty(items=[('LAYERS','Layers',''),('SETTINGS','Texture Set Settings','')])
    bpy.types.Material.painter_layers=CollectionProperty(type=PainterLayer)
    bpy.types.Material.painter_active=IntProperty(default=0,min=0,update=active_changed)
    bpy.app.handlers.save_pre.append(save_layers)
    bpy.app.handlers.load_post.append(restore_layers)
    if not bpy.app.background:
        bpy.context.preferences.view.show_splash=False
        bpy.app.timers.register(setup,first_interval=2)
def unregister():
    global _previews
    if bpy.app.timers.is_registered(setup):bpy.app.timers.unregister(setup)
    if _previews:bpy.utils.previews.remove(_previews)
    _previews=None
    bpy.app.handlers.save_pre.remove(save_layers)
    bpy.app.handlers.load_post.remove(restore_layers)
    del bpy.types.Scene.painter_search
    del bpy.types.Scene.painter_tab
    del bpy.types.Material.painter_active;del bpy.types.Material.painter_layers
    for cls in reversed(classes):bpy.utils.unregister_class(cls)


stage=0

def setup():
    global stage
    screen=bpy.context.screen
    if stage==0:
        # A single native window, with real editor regions and direct GPU drawing.
        if len(screen.areas)>1:
            area=min(screen.areas,key=lambda a:a.width*a.height)
            with bpy.context.temp_override(area=area):bpy.ops.screen.area_close()
            return .2
        stage=1
    if stage==1:
        area=screen.areas[0]
        with bpy.context.temp_override(area=area):bpy.ops.screen.area_split(direction='VERTICAL',factor=.23)
        stage=2;return .2
    if stage==2:
        areas=sorted(screen.areas,key=lambda a:a.x)
        areas[0].type='PAINTER_ASSETS'
        with bpy.context.temp_override(area=areas[1]):bpy.ops.screen.area_split(direction='VERTICAL',factor=.7)
        stage=3;return .2
    if stage==3:
        areas=sorted(screen.areas,key=lambda a:a.x)
        areas[-1].type='PAINTER_LAYERS'
        with bpy.context.temp_override(area=areas[1]):bpy.ops.screen.area_split(direction='VERTICAL',factor=.5)
        stage=4;return .2
    areas=sorted(screen.areas,key=lambda a:a.x)
    areas[1].type='VIEW_3D'
    space=areas[1].spaces.active
    space.shading.type='MATERIAL';space.overlay.show_overlays=False
    space.show_region_toolbar=False;space.show_region_ui=False
    space.show_region_tool_header=False
    space.show_gizmo_navigate=False
    region=next(r for r in areas[1].regions if r.type=='WINDOW')
    with bpy.context.temp_override(area=areas[1],region=region):
        bpy.ops.view3d.view_selected(use_all_regions=False)
    areas[2].type='IMAGE_EDITOR';areas[2].ui_type='UV'
    areas[2].spaces.active.show_region_toolbar=False;areas[2].spaces.active.show_region_ui=False
    bpy.context.workspace.name='Open Painter'
    print('PAINTER_NATIVE_LAYOUT_READY',flush=True)
    return None

# In-process application API. Cocoa sends JSON data, never executable user text.
def shell_dispatch(encoded):
    import base64
    request=json.loads(base64.b64decode(encoded))
    action=request.get('action','state')
    mat=material(bpy.context)
    if action=='ray':
        from mathutils import Vector
        from mathutils.geometry import barycentric_transform, intersect_point_tri_2d
        graph=bpy.context.evaluated_depsgraph_get()
        origin,direction=Vector(request['origin']),Vector(request['direction'])
        hit,point,normal,face,obj,matrix=bpy.context.scene.ray_cast(graph,origin,direction)
        if not hit or obj.original!=bpy.context.object:return json.dumps({'hit':False})
        mesh=obj.evaluated_get(graph).data
        if not mesh.uv_layers.active:return json.dumps({'hit':False})
        mesh.calc_loop_triangles();local=matrix.inverted()@point
        for tri in mesh.loop_triangles:
            if tri.polygon_index!=face:continue
            a,b,c=(mesh.vertices[v].co for v in tri.vertices)
            weights=barycentric_transform(local,a,b,c,Vector((0,0,0)),Vector((1,0,0)),Vector((0,1,0)))
            if weights.x<-.0001 or weights.y<-.0001 or weights.x+weights.y>1.0001:continue
            uv=[mesh.uv_layers.active.data[i].uv for i in tri.loops]
            result=uv[0]*(1-weights.x-weights.y)+uv[1]*weights.x+uv[2]*weights.y
            return json.dumps({'hit':True,'uv':list(result),'face':face})
        return json.dumps({'hit':False})
    if action=='new': bpy.ops.painter.new_set()
    elif action=='layer':
        bpy.ops.painter.layer(kind=request['kind'])
        if request['kind']=='PAINT':
            mat=material(bpy.context);layer=mat.painter_layers[mat.painter_active]
            layer.name='Paint layer'
            image=bpy.data.images.new('Paint '+layer.uid[:8],width=1024,height=1024,alpha=True)
            image.generated_color=(0,0,0,0)
            image.pixels[0]=0  # Allocate paint storage once, outside the stroke path.
            layer.image=image
    elif action=='asset': bpy.ops.painter.asset(preset=int(request['index']))
    elif action=='delete': bpy.ops.painter.delete()
    elif action=='select':
        if mat and 0<=int(request['index'])<len(mat.painter_layers):mat.painter_active=int(request['index'])
    elif action=='set':
        if mat and mat.painter_layers:
            key=request['key']
            if key not in {'name','visible','expanded','opacity','color','roughness','metallic','mask','tiling','offset','rotation','filtering','wrap','blend'}:raise ValueError('Unknown property')
            setattr(mat.painter_layers[mat.painter_active],key,request['value'])
            preserve_layers(mat)
    elif action=='image':
        if mat and mat.painter_layers:
            image=bpy.data.images.load(request['path'],check_existing=True)
            layer=mat.painter_layers[mat.painter_active]
            if request.get('mask'):
                layer.mask_image=image;layer.mask='BITMAP'
            else:layer.image=image
    elif action=='duplicate':
        if mat and mat.painter_layers:
            source=mat.painter_layers[mat.painter_active]
            values={key:(list(getattr(source,key)) if key in ('color','tiling','offset') else getattr(source,key)) for key in LAYER_FIELDS}
            image,mask=source.image,source.mask_image
            mat['_painter_building']=True
            try:
                layer=mat.painter_layers.add()
                for key,value in values.items():setattr(layer,key,value)
                layer.uid=uuid.uuid4().hex;layer.name+=' copy';layer.image=image;layer.mask_image=mask
                mat.painter_active=len(mat.painter_layers)-1
            finally:mat['_painter_building']=False
            sync(mat)
    elif action=='open':
        bpy.ops.wm.open_mainfile(filepath=request['path'])
    elif action=='save':
        for image in bpy.data.images:
            if image.source=='GENERATED' or image.is_dirty:image.pack()
        bpy.ops.wm.save_as_mainfile(filepath=request['path'])
    mat=material(bpy.context)
    rows=[]
    if mat:
        def walk(parent,depth):
            for index in reversed(range(len(mat.painter_layers))):
                layer=mat.painter_layers[index]
                if layer.parent!=parent:continue
                row={key:(list(getattr(layer,key)) if key in ('color','tiling','offset') else getattr(layer,key)) for key in LAYER_FIELDS}
                row.update(index=index,depth=depth,image=layer.image.name if layer.image else '')
                rows.append(row)
                if layer.expanded:walk(layer.uid,depth+1)
        walk('',0)
    return json.dumps({'material':mat.name if mat else '', 'rows':rows,'active':mat.painter_active if mat else -1})
