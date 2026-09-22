"""Decal objects UI (0.9.6): add, select and duplicate decal layers driven by Empties."""
import math

import bpy

from ..core import layer_stack as stack
from ..core.layer_stack import active
from ..core.project_manager import require_set
from ..painting import decals
from .operators import SafeOperator


class OP_OT_decal(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.decal'
    bl_label = 'Decal'
    bl_options = {'REGISTER', 'UNDO'}
    action: bpy.props.EnumProperty(items=[
        ('ADD', 'Add Decal', 'Create a decal Empty at the 3D cursor and a fill layer projecting from it'),
        ('SELECT', 'Select Decal Object', "Select the active layer's decal Empty in the viewport"),
        ('DUPLICATE', 'Duplicate Decal', 'Copy the layer and its Empty; move the copy independently'),
        ('CONVERT', 'Project From Decal', 'Give the active fill layer a decal Empty and switch its channels to Decal projection')],
        default='ADD')
    name: bpy.props.StringProperty(name='Name', default='Caution_01')
    image: bpy.props.StringProperty(name='Base Color Image', description='Existing image datablock to show and project')
    size: bpy.props.FloatProperty(name='Size', default=.25, min=.001, subtype='DISTANCE', unit='LENGTH')

    def invoke(self, context, event):
        if self.action in {'ADD', 'CONVERT'}:
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        self.layout.prop(self, 'name')
        self.layout.prop_search(self, 'image', bpy.data, 'images')
        self.layout.prop(self, 'size')

    def _frame(self, context):
        """Location at the 3D cursor; rotation facing the viewport when there is one."""
        location = context.scene.cursor.location.copy()
        rotation = (0., 0., 0.)
        space = getattr(context, 'space_data', None)
        region = getattr(space, 'region_3d', None) if space is not None and space.type == 'VIEW_3D' else None
        if region is not None:
            rotation = region.view_rotation.to_euler()
        return location, rotation

    def run(self, context):
        ts = require_set(context)
        scene = context.scene
        image = bpy.data.images.get(self.image) if self.image else None
        if self.action in {'ADD', 'CONVERT'}:
            if self.action == 'CONVERT':
                layer = active(ts)
                if layer is None or layer.type != 'FILL':
                    raise ValueError('Select a fill layer to project from a decal')
            else:
                layer = stack.add(ts, 'FILL', self.name)
                for prop in layer.channels:
                    prop.enabled = False
            location, rotation = self._frame(context)
            name = decals.PREFIX + self.name
            if name not in bpy.data.objects:
                # The name is free: a layer still naming an Empty that is gone would re-link
                # to the one made here, and it is not what the artist is making.
                for other in scene.open_painter.texture_sets:
                    for stale in other.layers:
                        if stale.decal_object is None and stale.decal_object_name == name:
                            stale.decal_object_name = ''
            obj = decals.create_object(context, name, image, location, rotation, self.size)
            obj[decals.MARKER] = layer.id
            layer.decal_object, layer.decal_object_name = obj, obj.name
            for prop in layer.channels:
                prop.projection = 'DECAL'
                prop.tiling = 'NONE'
            base = stack.channel_for(layer, 'BASE_COLOR')
            if image is not None and base is not None:
                base.image, base.enabled = image, True
            ts.active_layer = stack.index_of(ts, layer.id)
            decals.sync(ts)
            for other in context.view_layer.objects:
                other.select_set(other == obj)
            context.view_layer.objects.active = obj
            scene.open_painter.status = f'Decal {obj.name}: move, rotate or scale it in the viewport'
        elif self.action == 'SELECT':
            layer = active(ts)
            obj = decals.resolve(layer) if layer is not None else None
            if obj is None:
                raise ValueError('The active layer has no decal object')
            for other in context.view_layer.objects:
                other.select_set(other == obj)
            context.view_layer.objects.active = obj
        elif self.action == 'DUPLICATE':
            layer = active(ts)
            source = decals.resolve(layer) if layer is not None else None
            if source is None:
                raise ValueError('The active layer has no decal object')
            copy = source.copy()
            copy.name = source.name + '.001'
            scene.collection.objects.link(copy)
            copy[decals.MARKER] = layer.id
            created = decals.adopt_duplicates(scene)
            assert created, 'the duplicate must become its own layer'
            ts.active_layer = stack.index_of(ts, created[0])
            for other in context.view_layer.objects:
                other.select_set(other == copy)
            context.view_layer.objects.active = copy
        from ..core.layer_stack import invalidate
        invalidate(ts)


def draw_decal_controls(layout, context, ts, layer):
    """Decal section of a fill layer's properties."""
    obj = decals.resolve(layer)
    if obj is None and not any(p.projection == 'DECAL' for p in layer.channels):
        return
    box = layout.box()
    row = box.row(align=True)
    row.label(text='Decal', icon='OUTLINER_OB_IMAGE')
    if getattr(layer, 'decal_inline', False):
        box.prop(layer, 'decal_origin')
        box.prop(layer, 'decal_extent')
        box.prop(layer, 'decal_axes', text='Frame Axes')
        box.prop(layer, 'decal_depth')
        box.prop(layer, 'decal_falloff', slider=True)
        box.prop(layer, 'decal_facing')
        return
    row.operator('open_painter.decal', text='', icon='RESTRICT_SELECT_OFF').action = 'SELECT'
    row.operator('open_painter.decal', text='', icon='DUPLICATE').action = 'DUPLICATE'
    box.prop(layer, 'decal_object')
    if obj is None:
        box.label(text=f"'{layer.decal_object_name}' is not in this file" if layer.decal_object_name
                  else 'Choose an Empty; its plane is the decal', icon='ERROR')
        return
    box.prop(layer, 'decal_depth')
    box.prop(layer, 'decal_falloff', slider=True)
    box.prop(layer, 'decal_facing')
    box.label(text='Move, rotate, scale or Shift+D the Empty in the viewport', icon='INFO')


CLASSES = (OP_OT_decal,)
