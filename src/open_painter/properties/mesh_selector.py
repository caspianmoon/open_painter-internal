"""Mesh-aware mask selectors (0.9.2): Blender's own scene structure as mask input."""
import math

import bpy

from ..core.layer_stack import replaying
from ..core.layer_store import project_of as _project_of

# Project file format (``OP_Project.format_version``): 2 stores selector angles in
# radians. Files before it (0.9.6 and the first 0.9.7 builds) stored degrees under
# the same properties; ``migrate_angles`` converts them once on load. 3 (0.13) adds
# export UV padding: a file from before it exports with Automatic padding, and the
# Export view says so until the artist picks a mode.
FORMAT_VERSION = 3
ANGLES = ('angle', 'softness')

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
    ('REGION', 'Saved Face Region', 'Editable frozen face membership; topology changes require a new selection'),
]


def changed(self, context):
    project = _project_of(self)
    if not project or replaying():
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
    project = _project_of(self)
    if not project or replaying():
        return
    for ts in project.texture_sets:
        if any(m.mesh.as_pointer() == self.as_pointer() for layer in ts.layers for m in layer.masks):
            from ..core.layer_stack import invalidate
            invalidate(ts)
            from ..evaluation.shader_preview import notify
            notify(self, context, structural=False)
            return


def in_degrees(value) -> bool:
    """Whether a stored angle is a degree magnitude from before the radians change.

    Radians never exceed pi (the property maximum) and the old degree ranges
    were 0..180 and 0..90, so anything past a full turn can only be degrees.
    Degrees up to 2*pi (about 6 deg) are indistinguishable and stay as they are.
    """
    return isinstance(value, (int, float)) and value > 2 * math.pi


def degrees_to_radians(values: dict) -> dict:
    """A selector snapshot (a ``mesh`` dict from a document, module base or copy)
    with degree-magnitude angles converted; other snapshots come back unchanged."""
    if not any(in_degrees(values.get(name)) for name in ANGLES):
        return values
    return dict(values, **{name: math.radians(values[name]) for name in ANGLES if in_degrees(values.get(name))})


def migrate_angles(selector) -> bool:
    """Convert a live selector's degree-magnitude angles in place; True if it changed.

    A value read straight from an older file is not clamped by the property's
    maximum: 45 shows as 2578 deg and covers every face. Setting it converts and
    clamps, and the update callback invalidates the owning set.
    """
    changed_any = False
    for name in ANGLES:
        value = getattr(selector, name)
        if in_degrees(value):
            setattr(selector, name, math.radians(value))
            changed_any = True
    return changed_any


class OP_MeshSelector(bpy.types.PropertyGroup):
    kind: bpy.props.EnumProperty(name='Select By', items=KINDS, default='COLLECTION', update=changed)
    object: bpy.props.PointerProperty(name='Object', type=bpy.types.Object, update=_pointer_changed('object'))
    collection: bpy.props.PointerProperty(name='Collection', type=bpy.types.Collection, update=_pointer_changed('collection'))
    material: bpy.props.PointerProperty(name='Material', type=bpy.types.Material, update=_pointer_changed('material'))
    # Names mirror the pointers so portable materials can re-link by name.
    object_name: bpy.props.StringProperty()
    collection_name: bpy.props.StringProperty()
    material_name: bpy.props.StringProperty()
    region_id: bpy.props.StringProperty(name='Region Identity')
    region_faces: bpy.props.StringProperty(name='Saved Faces', default='[]', update=changed)
    region_topology: bpy.props.StringProperty(name='Topology Signature', update=changed)
    region_provenance: bpy.props.StringProperty(name='Region Origin')
    attribute: bpy.props.StringProperty(name='Name', update=changed,
        description='Vertex group or attribute name')
    face_set: bpy.props.IntProperty(name='Face Set', default=1, min=0, update=changed)
    island: bpy.props.IntProperty(name='Island', default=-1, min=-1, update=changed,
        description='UV island index on the object (-1: the islands of the selected faces)')
    direction: bpy.props.FloatVectorProperty(name='Direction', default=(0., 0., 1.), subtype='DIRECTION', update=changed)
    # ANGLE properties store radians; the UI shows degrees.
    angle: bpy.props.FloatProperty(name='Angle', default=math.radians(45.), min=0., max=math.pi, subtype='ANGLE',
        unit='ROTATION', update=changed, description='Faces within this angle of the direction are fully covered')
    softness: bpy.props.FloatProperty(name='Softness', default=math.radians(15.), min=0., max=math.pi / 2, subtype='ANGLE',
        unit='ROTATION', update=changed, description='Angle over which coverage fades to zero past the angle')
    include_children: bpy.props.BoolProperty(name='Include Child Collections', default=True, update=changed)

    def resolve(self, name):
        """The pointed datablock, or the one named like it (after a portable import)."""
        from ..evaluation.mesh_masks import resolve
        return resolve(self, name)
