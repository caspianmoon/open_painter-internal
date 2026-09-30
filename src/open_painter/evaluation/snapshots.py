"""Immutable authoring parameters for one cooperative evaluation revision.

Blender ID handles stay on the main thread. Their managed revisions and the
authoring serial are checked by the evaluator before resuming/publication.
"""
from types import MappingProxyType


class Parameters:
    __slots__ = ('_values', '_pointer')

    def __init__(self, values, pointer):
        object.__setattr__(self, '_values', MappingProxyType(values))
        object.__setattr__(self, '_pointer', pointer)

    def __getattr__(self, name):
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        raise TypeError('Evaluation parameters are immutable')

    def as_pointer(self):
        return self._pointer

    def replaced(self, **values):
        return Parameters(dict(self._values, **values), self._pointer)


def capture(group, pixel_scale=1.0):
    import bpy
    values = {'id_data': group.id_data}
    for field in group.bl_rna.properties:
        name = field.identifier
        if name in {'rna_type', 'id_data', 'opacity_percent', 'master_factor', 'master_scale'}:
            continue   # derived views of other fields (a computed master control reads its members)
        value = getattr(group, name)
        if field.type == 'COLLECTION':
            values[name] = tuple(capture(item, pixel_scale) for item in value)
        elif field.type == 'POINTER' and isinstance(value, bpy.types.PropertyGroup):
            values[name] = capture(value, pixel_scale)
        elif getattr(field, 'is_array', False):
            values[name] = tuple(value)
        else:
            values[name] = value
    if group.bl_rna.identifier == 'OP_TextureSet':
        # The stack lives in the set's layer store (core.layer_store), not among its RNA properties.
        values.pop('layer_store', None)
        values['layers'] = tuple(capture(item, pixel_scale) for item in group.layers)
    if group.bl_rna.identifier == 'OP_TextureEffect' and pixel_scale != 1:
        # Pixel radii refer to authored pixels; angle-based radial blur does not.
        if values.get('kind') != 'RADIAL_BLUR':
            values['radius'] *= pixel_scale
        if values.get('kind') in {'HEIGHT_TO_NORMAL', 'EDGE_DETECT'}:
            values['amount'] *= pixel_scale
    if group.bl_rna.identifier == 'OP_Mask' and pixel_scale != 1 and 'detail_radius' in values:
        values['detail_radius'] *= pixel_scale
    return Parameters(values, group.as_pointer())
