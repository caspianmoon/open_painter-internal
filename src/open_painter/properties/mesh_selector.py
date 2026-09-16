"""Mesh-aware mask selectors (0.9.2): Blender's own scene structure as mask input."""
import bpy

KINDS = [
    ('OBJECT', 'Object', 'Faces of one assigned object'),
    ('COLLECTION', 'Collection', 'Faces of every assigned object in a collection (children included); add an object to the collection and the mask follows'),
    ('MATERIAL', 'Material', 'Faces whose material slot holds a material'),
    ('VERTEX_GROUP', 'Vertex Group', 'Vertex group weights, interpolated across faces'),
    ('FACE_SET', 'Face Set', 'Faces of one sculpt face set'),
    ('FACE_SELECTION', 'Face Selection', 'The current face selection of the assigned meshes'),
    ('UV_ISLAND', 'UV Island', 'UV islands containing the selected faces, or one island by index'),
    ('NORMAL_DIRECTION', 'Normal Direction', 'Faces whose normal points along a direction, with a soft angle'),
    ('ATTRIBUTE', 'Geometry Attribute', 'A named point, face or corner attribute (float, int, bool or vector length)'),
]


def changed(self, context):
    project = getattr(self.id_data, 'open_painter', None)
    if not project:
        return
    for ts in project.texture_sets:
        if any(m.mesh.as_pointer() == self.as_pointer() for layer in ts.layers for m in layer.masks):
            from ..core.layer_stack import invalidate
            invalidate(ts)
            from ..evaluation.shader_preview import notify
            notify(self, context)
            return


def _pointer_changed(name):
    """Mirror one pointer into its name field, then restructure.

    Only the edited pointer's name moves: a loaded material restores the pointers
    (None) before the names, and its other fields' callbacks must not erase them.
    """
    def update(self, context):
        value = getattr(self, name)
        setattr(self, name + '_name', value.name if value is not None else '')
        changed(self, context)
    return update


def numeric(self, context):
    project = getattr(self.id_data, 'open_painter', None)
    if not project:
        return
    for ts in project.texture_sets:
        if any(m.mesh.as_pointer() == self.as_pointer() for layer in ts.layers for m in layer.masks):
            from ..core.layer_stack import invalidate
            invalidate(ts)
            from ..evaluation.shader_preview import notify
            notify(self, context, structural=False)
            return


class OP_MeshSelector(bpy.types.PropertyGroup):
    kind: bpy.props.EnumProperty(name='Select By', items=KINDS, default='COLLECTION', update=changed)
    object: bpy.props.PointerProperty(name='Object', type=bpy.types.Object, update=_pointer_changed('object'))
    collection: bpy.props.PointerProperty(name='Collection', type=bpy.types.Collection, update=_pointer_changed('collection'))
    material: bpy.props.PointerProperty(name='Material', type=bpy.types.Material, update=_pointer_changed('material'))
    # Names mirror the pointers so portable materials can re-link by name.
    object_name: bpy.props.StringProperty()
    collection_name: bpy.props.StringProperty()
    material_name: bpy.props.StringProperty()
    attribute: bpy.props.StringProperty(name='Name', update=changed,
        description='Vertex group or attribute name')
    face_set: bpy.props.IntProperty(name='Face Set', default=1, min=0, update=changed)
    island: bpy.props.IntProperty(name='Island', default=-1, min=-1, update=changed,
        description='UV island index on the object (-1: the islands of the selected faces)')
    direction: bpy.props.FloatVectorProperty(name='Direction', default=(0., 0., 1.), subtype='DIRECTION', update=changed)
    angle: bpy.props.FloatProperty(name='Angle', default=45., min=0., max=180., subtype='ANGLE', unit='ROTATION',
        update=changed, description='Faces within this angle of the direction are fully covered')
    softness: bpy.props.FloatProperty(name='Softness', default=15., min=0., max=90., subtype='ANGLE', unit='ROTATION',
        update=changed, description='Angle over which coverage fades to zero past the angle')
    include_children: bpy.props.BoolProperty(name='Include Child Collections', default=True, update=changed)

    def resolve(self, name):
        """The pointed datablock, or the one named like it (after a portable import)."""
        from ..evaluation.mesh_masks import resolve
        return resolve(self, name)
