"""Independent native-workspace prototype; never imports the main Open Painter add-on."""
import uuid
from pathlib import Path
import bpy

WORKSPACE = "Open Painter Prototype"
_suppressed = []


def project(context):
    return context.scene.opp_project


def active(context):
    data = project(context)
    return data.layers[data.active_layer] if 0 <= data.active_layer < len(data.layers) else None


def role(context):
    if not context.workspace or context.workspace.name != WORKSPACE or not context.area:
        return ""
    areas = sorted((a for a in context.screen.areas if a.type == 'PROPERTIES'), key=lambda a: -a.y)
    return ('SETS', 'LAYERS', 'PROPERTIES')[areas.index(context.area)] if context.area in areas and len(areas) == 3 else ""


def update_layer(self, context):
    data = project(context)
    if data.material and data.material.node_tree:
        nodes = data.material.node_tree.nodes
        opacity = nodes.get(self.id + '_opacity')
        if opacity:
            opacity.inputs[1].default_value = self.opacity if self.visible else 0
        fill = nodes.get(self.id + '_fill')
        if fill:
            fill.outputs[0].default_value = self.color
        mix = nodes.get(self.id + '_mix')
        if mix:
            mix.blend_type = self.blend


def select_layer(self, context):
    data = project(context)
    layer = active(context)
    if not layer or not data.material:
        return
    nodes = data.material.node_tree.nodes
    node = nodes.get(layer.id + '_image')
    nodes.active = node
    context.scene.tool_settings.image_paint.mode = 'IMAGE'
    context.scene.tool_settings.image_paint.canvas = layer.image
    for screen in data_screens(context):
        for area in screen.areas:
            if area.type == 'IMAGE_EDITOR':
                area.spaces.active.image = layer.image
                area.spaces.active.ui_mode = 'PAINT' if layer.image else 'VIEW'
    # Never leave a previous layer's canvas active when a fill is selected.
    if not node and context.object and context.object.mode == 'TEXTURE_PAINT':
        bpy.ops.object.mode_set(mode='OBJECT')


def data_screens(context):
    return context.workspace.screens if context.workspace and context.workspace.name == WORKSPACE else []


class OPP_Layer(bpy.types.PropertyGroup):
    id: bpy.props.StringProperty()
    name: bpy.props.StringProperty(default='Layer')
    kind: bpy.props.EnumProperty(items=[('PAINT','Paint',''),('FILL','Fill','')])
    image: bpy.props.PointerProperty(type=bpy.types.Image)
    visible: bpy.props.BoolProperty(default=True, update=update_layer)
    opacity: bpy.props.FloatProperty(default=1,min=0,max=1,update=update_layer)
    color: bpy.props.FloatVectorProperty(size=4,subtype='COLOR',default=(0.2,0.3,0.4,1),min=0,max=1,update=update_layer)
    blend: bpy.props.EnumProperty(items=[('MIX','Normal',''),('MULTIPLY','Multiply',''),('ADD','Add','')],update=update_layer)


class OPP_Project(bpy.types.PropertyGroup):
    layers: bpy.props.CollectionProperty(type=OPP_Layer)
    active_layer: bpy.props.IntProperty(update=select_layer)
    material: bpy.props.PointerProperty(type=bpy.types.Material)
    mesh: bpy.props.PointerProperty(type=bpy.types.Object)
    resolution: bpy.props.EnumProperty(items=[('512','512',''),('2048','2048',''),('4096','4096','')],default='2048')


def rebuild(context):
    data = project(context)
    nodes, links = data.material.node_tree.nodes, data.material.node_tree.links
    nodes.clear()
    surface = nodes.new('ShaderNodeBsdfPrincipled')
    surface.name = 'Surface'
    surface.inputs['Roughness'].default_value = 0.4
    output = nodes.new('ShaderNodeOutputMaterial')
    links.new(surface.outputs['BSDF'], output.inputs['Surface'])
    base = nodes.new('ShaderNodeRGB')
    base.outputs[0].default_value = (0.15,0.15,0.15,1)
    previous = base.outputs[0]
    for layer in data.layers:
        source = nodes.new('ShaderNodeTexImage' if layer.kind == 'PAINT' else 'ShaderNodeRGB')
        source.name = layer.id + ('_image' if layer.kind == 'PAINT' else '_fill')
        amount = nodes.new('ShaderNodeMath')
        amount.name = layer.id + '_opacity'
        amount.operation = 'MULTIPLY'
        amount.inputs[0].default_value = 1
        amount.inputs[1].default_value = layer.opacity if layer.visible else 0
        if layer.kind == 'PAINT':
            source.image = layer.image
            links.new(source.outputs['Alpha'],amount.inputs[0])
        else:
            source.outputs[0].default_value = layer.color
        mix = nodes.new('ShaderNodeMixRGB')
        mix.name = layer.id + '_mix'
        mix.blend_type = layer.blend
        links.new(amount.outputs[0],mix.inputs[0])
        links.new(previous,mix.inputs[1])
        links.new(source.outputs[0],mix.inputs[2])
        previous = mix.outputs[0]
    links.new(previous,surface.inputs['Base Color'])
    select_layer(data,context)


class OPP_OT_layer(bpy.types.Operator):
    bl_idname = 'opp.layer'
    bl_label = 'Layer'
    bl_options = {'REGISTER','UNDO'}
    action: bpy.props.EnumProperty(items=[(a,a.title(),'') for a in ('PAINT','FILL','DELETE','UP','DOWN')])

    def execute(self, context):
        data = project(context)
        obj = context.object
        if not data.material:
            if not obj or obj.type != 'MESH' or not obj.data.uv_layers:
                self.report({'ERROR'},'Select a mesh with a UV map.')
                return {'CANCELLED'}
            data.mesh = obj
            mat = bpy.data.materials.new('Open Painter Prototype Material')
            mat.use_nodes = True
            data.material = mat
            if obj.data.materials:
                obj.data.materials[obj.active_material_index] = mat
            else:
                obj.data.materials.append(mat)
        if self.action in {'PAINT','FILL'}:
            layer = data.layers.add()
            layer.id = uuid.uuid4().hex
            layer.kind = self.action
            layer.name = f'{self.action.title()} Layer {len(data.layers)}'
            if self.action == 'PAINT':
                n = int(data.resolution)
                layer.image = bpy.data.images.new(layer.name,n,n,alpha=True)
                layer.image.generated_color = (0,0,0,0)
                layer.image.use_fake_user = True
            data.active_layer = len(data.layers)-1
        elif data.layers:
            i = data.active_layer
            if self.action == 'DELETE':
                data.layers.remove(i)
                data.active_layer = max(0,i-1)
            else:
                j = max(0,min(len(data.layers)-1,i+(1 if self.action == 'UP' else -1)))
                data.layers.move(i,j)
                data.active_layer = j
        rebuild(context)
        return {'FINISHED'}


class OPP_OT_paint(bpy.types.Operator):
    bl_idname = 'opp.paint'
    bl_label = 'Paint Layer'
    def execute(self,context):
        data = project(context)
        layer = active(context)
        if not layer or not layer.image or context.object != data.mesh:
            self.report({'ERROR'},'Select the assigned mesh and a paint layer.')
            return {'CANCELLED'}
        select_layer(data,context)
        if context.object.mode != 'TEXTURE_PAINT':
            bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
        if not context.scene.tool_settings.image_paint.brush:
            bpy.ops.brush.asset_activate(asset_library_type='ESSENTIALS',relative_asset_identifier='brushes/essentials_brushes-mesh_texture.blend/Brush/Paint Hard')
        return {'FINISHED'}


class OPP_UL_layers(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        layout.prop(item,'visible',text='',icon='HIDE_OFF' if item.visible else 'HIDE_ON',emboss=False)
        layout.prop(item,'name',text='',emboss=False,icon='IMAGE_DATA' if item.image else 'MATERIAL')
        layout.prop(item,'opacity',text='',slider=True)


class OPP_PT_sets(bpy.types.Panel):
    bl_label = 'Texture Set List'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'material'
    bl_order = -1000
    @classmethod
    def poll(cls,context): return role(context) == 'SETS'
    def draw(self,context):
        data = project(context)
        self.layout.label(text=data.material.name if data.material else 'No texture set',icon='MATERIAL')
        self.layout.prop(data,'resolution',text='New image size')


class OPP_PT_layers(bpy.types.Panel):
    bl_label = 'Layers'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'material'
    bl_order = -1000
    @classmethod
    def poll(cls,context): return role(context) == 'LAYERS'
    def draw(self,context):
        data = project(context)
        row = self.layout.row(align=True)
        for kind in ('PAINT','FILL'):
            row.operator('opp.layer',text=kind.title(),icon='ADD').action = kind
        self.layout.template_list('OPP_UL_layers','',data,'layers',data,'active_layer',rows=5,sort_reverse=True)
        row = self.layout.row(align=True)
        for action,icon in [('UP','TRIA_UP'),('DOWN','TRIA_DOWN'),('DELETE','TRASH')]:
            row.operator('opp.layer',text='',icon=icon).action = action


class OPP_PT_properties(bpy.types.Panel):
    bl_label = 'Properties'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'material'
    bl_order = -1000
    @classmethod
    def poll(cls,context): return role(context) == 'PROPERTIES'
    def draw(self,context):
        data = project(context)
        layer = active(context)
        if not layer: return
        self.layout.prop(layer,'name')
        self.layout.prop(layer,'blend')
        self.layout.prop(layer,'opacity',slider=True)
        if layer.image:
            self.layout.operator('opp.paint',icon='BRUSH_DATA')
            self.layout.label(text=layer.image.name,icon='IMAGE_DATA')
            paint = context.scene.tool_settings.image_paint
            if paint.brush:
                unified = paint.unified_paint_settings
                for prop in ('color','size','strength'):
                    owner = unified if getattr(unified,'use_unified_'+prop) else paint.brush
                    self.layout.prop(owner,prop)
        else:
            self.layout.prop(layer,'color')
        if data.material:
            surface = data.material.node_tree.nodes.get('Surface')
            if surface:
                for key in ('Roughness','Metallic'):
                    self.layout.prop(surface.inputs[key],'default_value',text=key)


class OPP_OT_workspace(bpy.types.Operator):
    bl_idname = 'opp.workspace'
    bl_label = 'Open Painter Prototype Workspace'
    def execute(self,context):
        existing = bpy.data.workspaces.get(WORKSPACE)
        if existing:
            context.window.workspace = existing
        else:
            bpy.ops.workspace.append_activate(idname=WORKSPACE,filepath=str(Path(__file__).with_name('workspace.blend')))
        return {'FINISHED'}


def panel_types(base):
    for cls in base.__subclasses__():
        yield cls
        yield from panel_types(cls)


def scoped_poll(previous):
    # Blender RNA poll callbacks have exactly two Python arguments.
    def poll(cls, context):
        if role(context):
            return False
        return previous(context) if previous else True
    return classmethod(poll)


def register():
    for cls in CLASSES: bpy.utils.register_class(cls)
    bpy.types.Scene.opp_project = bpy.props.PointerProperty(type=OPP_Project)
    for cls in list(panel_types(bpy.types.Panel)):
        if cls in CLASSES or getattr(cls, 'bl_space_type', '') != 'PROPERTIES':
            continue
        if getattr(cls, 'bl_context', '') != 'material':
            continue
        old = cls.__dict__.get('poll')
        previous = getattr(cls, 'poll', None)
        _suppressed.append((cls, old, previous))
        cls.poll = scoped_poll(previous)



def unregister():
    for cls, old, previous in reversed(_suppressed):
        if old is None:
            delattr(cls, 'poll')
        else:
            cls.poll = old
    _suppressed.clear()
    del bpy.types.Scene.opp_project
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)


CLASSES = (OPP_Layer,OPP_Project,OPP_OT_layer,OPP_OT_paint,OPP_UL_layers,OPP_PT_sets,OPP_PT_layers,OPP_PT_properties,OPP_OT_workspace)
