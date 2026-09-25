"""Material → Geometry: a published height or mask becomes real displaced geometry.

The displacement lives on an owned copy of the evaluated source (never on the
source itself, so the live scene fields and the raster caches keyed on the
source's geometry are not fed their own output). A Geometry Nodes group
subdivides the copy, samples the image through the set's UV map per corner,
takes the first corner's value per vertex (no seam averaging, like the
Displace modifier) and offsets along the normal by ``(h - midlevel) * amplitude``.
Only the faces in the set's material slots move (other sets may own the object's
other slots); the copy keeps the source's material list and indices.
"""
import bpy
import numpy as np

from ..utils.ids import new_id
from . import limits
from .manager import TAG, _image, texture_set_of

MAX_LEVELS = 4


def _uv_name(obj):
    uv = next((layer for layer in obj.data.uv_layers if layer.active_render), obj.data.uv_layers.active)
    if uv is None:
        raise ValueError(f'{obj.name} requires a UV map for displacement')
    return uv.name


def _slots(ts, obj):
    """The material slots of ``obj`` the set owns; displacement touches their faces only."""
    slots = {ref.slot_index for ref in ts.assignments if ref.object == obj}
    if not slots:
        raise ValueError('Displacement source must be assigned to the texture set')
    return slots


def _face_selection(nodes, links, slots):
    """A face-domain field: the face's material slot belongs to the set."""
    material_index = nodes.new('GeometryNodeInputMaterialIndex')
    selected = None
    for slot in sorted(slots):
        compare = nodes.new('FunctionNodeCompare')
        compare.data_type, compare.operation = 'INT', 'EQUAL'
        links.new(material_index.outputs['Material Index'], compare.inputs['A'])
        compare.inputs['B'].default_value = slot
        if selected is None:
            selected = compare
            continue
        either = nodes.new('FunctionNodeBooleanMath'); either.operation = 'OR'
        links.new(selected.outputs['Result'], either.inputs[0])
        links.new(compare.outputs['Result'], either.inputs[1])
        selected = either
    # Evaluated on faces, so a point takes the selection of the faces it belongs to
    # (a boundary vertex moves with the set's side) instead of an averaged slot index.
    on_faces = nodes.new('GeometryNodeFieldOnDomain')
    on_faces.data_type, on_faces.domain = 'BOOLEAN', 'FACE'
    links.new(selected.outputs['Result'], on_faces.inputs['Value'])
    return on_faces.outputs['Value']


def _node_group(image, uv_name, levels, amplitude, midlevel, smooth, slots, created, origin=None):
    group = bpy.data.node_groups.new('OP Displacement', 'GeometryNodeTree')
    created.append(group)
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    amplitude_socket = group.interface.new_socket(name='Amplitude', in_out='INPUT', socket_type='NodeSocketFloat')
    amplitude_socket.default_value = amplitude
    midlevel_socket = group.interface.new_socket(name='Midlevel', in_out='INPUT', socket_type='NodeSocketFloat')
    midlevel_socket.default_value = midlevel
    nodes, links = group.nodes, group.links
    group_in, group_out = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    if smooth:
        subdivide = nodes.new('GeometryNodeSubdivisionSurface')
    else:
        subdivide = nodes.new('GeometryNodeSubdivideMesh')
    subdivide.inputs['Level'].default_value = levels
    links.new(group_in.outputs['Geometry'], subdivide.inputs['Mesh'])
    uv = nodes.new('GeometryNodeInputNamedAttribute')
    uv.data_type = 'FLOAT_VECTOR'
    uv.inputs['Name'].default_value = uv_name
    texture = nodes.new('GeometryNodeImageTexture')
    texture.interpolation = 'Linear'
    texture.extension = 'EXTEND'
    texture.inputs['Image'].default_value = image
    links.new(uv.outputs['Attribute'], texture.inputs['Vector'])
    if origin is not None:
        local = nodes.new('ShaderNodeVectorMath')
        local.operation = 'SUBTRACT'
        local.inputs[1].default_value = (*origin, 0)
        links.new(uv.outputs['Attribute'], local.inputs[0])
        links.new(local.outputs['Vector'], texture.inputs['Vector'])
    # Corner value → vertex value: the first corner of each vertex (no seam averaging).
    corners = nodes.new('GeometryNodeCornersOfVertex')
    index = nodes.new('GeometryNodeInputIndex')
    links.new(index.outputs['Index'], corners.inputs['Vertex Index'])
    at_corner = nodes.new('GeometryNodeFieldAtIndex')
    at_corner.data_type = 'FLOAT'
    at_corner.domain = 'CORNER'
    links.new(corners.outputs['Corner Index'], at_corner.inputs['Index'])
    links.new(texture.outputs['Color'], at_corner.inputs['Value'])
    offset_height = nodes.new('ShaderNodeMath'); offset_height.operation = 'SUBTRACT'
    links.new(at_corner.outputs['Value'], offset_height.inputs[0])
    links.new(group_in.outputs['Midlevel'], offset_height.inputs[1])
    scaled = nodes.new('ShaderNodeMath'); scaled.operation = 'MULTIPLY'
    links.new(offset_height.outputs[0], scaled.inputs[0])
    links.new(group_in.outputs['Amplitude'], scaled.inputs[1])
    normal = nodes.new('GeometryNodeInputNormal')
    along = nodes.new('ShaderNodeVectorMath'); along.operation = 'SCALE'
    links.new(normal.outputs['Normal'], along.inputs[0])
    links.new(scaled.outputs[0], along.inputs['Scale'])
    selection = _face_selection(nodes, links, slots)
    set_position = nodes.new('GeometryNodeSetPosition')
    links.new(subdivide.outputs['Mesh'], set_position.inputs['Geometry'])
    links.new(selection, set_position.inputs['Selection'])
    links.new(along.outputs['Vector'], set_position.inputs['Offset'])
    shade = nodes.new('GeometryNodeSetShadeSmooth')
    links.new(set_position.outputs['Geometry'], shade.inputs['Geometry'])
    links.new(selection, shade.inputs['Selection'])
    shade.inputs['Shade Smooth'].default_value = True
    links.new(shade.outputs['Geometry'], group_out.inputs['Geometry'])
    return group


def settings(helper):
    """Amplitude and midlevel as the helper displaces right now.

    The group exposes them as modifier inputs so the artist tunes them in the modifier
    panel; a frozen helper (no modifiers) or another kind answers with its stored values.
    """
    values = dict(amplitude=float(helper.get('amplitude', 0.0)), midlevel=float(helper.get('midlevel', .5)))
    modifier = next((m for m in helper.modifiers if m.type == 'NODES' and m.node_group is not None), None)
    if modifier is None:
        return values
    for item in modifier.node_group.interface.items_tree:
        if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name.lower() in values:
            socket = getattr(modifier.properties.inputs, item.identifier, None)
            if socket is not None:
                values[item.name.lower()] = float(socket.value)
    return values


def create(ts, obj, image, levels=2, amplitude=None, midlevel=.5, smooth=False, replacing=None):
    """An owned, tagged copy of the evaluated source displaced by ``image``; returns the helper."""
    if obj.type != 'MESH':
        raise ValueError('Displacement needs a mesh source')
    # The operator leaves the last helper active: a helper as source would stack relief
    # on relief and hand Replace Source Mesh the helper instead of the artist's mesh.
    if obj.get(TAG):
        raise ValueError('Select the source object, not an effect helper')
    slots = _slots(ts, obj)
    levels = int(max(0, min(MAX_LEVELS, levels)))
    amplitude = float(ts.height_distance if amplitude is None else amplitude)
    scene = ts.id_data
    with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    faces = len(evaluated.data.polygons)   # every face subdivides; only the set's move
    material_index = np.empty(faces, np.int32)
    evaluated.data.polygons.foreach_get('material_index', material_index)
    if not np.isin(material_index, np.fromiter(slots, np.int32, len(slots))).any():
        raise ValueError(f'{obj.name} has no evaluated faces in its assigned slots')
    limits.check(scene, 0, faces * (4 ** levels) * 2, replacing=replacing)
    uv_name = ts.udim_source_uv if ts.udim_tile else _uv_name(obj)
    origin = None
    if ts.udim_tile:
        from ..core.udim import validate
        from ..geometry.udim import tile_origin
        validate(ts)
        origin = tile_origin(ts.udim_tile)
    identifier = new_id()
    created = []
    try:
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=graph)
        created.append(mesh)
        helper = bpy.data.objects.new('OP Displaced · ' + obj.name, mesh)
        created.append(helper)
        scene.collection.objects.link(helper)
        helper.matrix_world = obj.matrix_world.copy()
        helper.parent = obj
        helper.matrix_parent_inverse = obj.matrix_world.inverted_safe()   # a flat source still gets its helper
        group = _node_group(image, uv_name, levels, amplitude, midlevel, smooth, slots, created, origin)
        modifier = helper.modifiers.new('Open Painter Displacement', 'NODES')
        modifier.node_group = group
        for item in created:
            item[TAG] = 'INSTANCE_HELPER' if item == helper else 'OWNED_RESOURCE'
            item['effect_owner'] = identifier
        helper['instance_count'] = 0
        helper['estimated_triangles'] = faces * (4 ** levels) * 2
        helper['source_texture_set'] = ts.id
        helper['kind'] = 'DISPLACE'
        helper['levels'] = levels
        helper['amplitude'] = amplitude
        helper['midlevel'] = midlevel
        helper['smooth'] = smooth
        helper['source_image'] = image   # an ID pointer follows renames; older files hold the name
        helper['frozen'] = False
        obj.hide_set(True) if False else None   # the source stays visible; the artist decides
        return helper
    except Exception:
        for item in reversed(created):
            if isinstance(item, bpy.types.Object):
                bpy.data.objects.remove(item, do_unlink=True)
            elif isinstance(item, bpy.types.Mesh):
                bpy.data.meshes.remove(item)
            elif isinstance(item, bpy.types.NodeTree):
                bpy.data.node_groups.remove(item)
        raise


def regenerate(ts, helper):
    """Re-copy the evaluated source (a moved panel, an edited mesh) with the same settings."""
    if helper.get('frozen'):
        raise ValueError('Unfreeze the helper before regenerating')
    ts = texture_set_of(ts, helper)
    image = _image(helper.get('source_image'))
    source = helper.parent
    if image is None or source is None:
        raise ValueError('Displacement source or image is missing')
    tuned = settings(helper)   # the modifier's inputs, not the creation-time values
    replacement = create(ts, source, image, helper['levels'], tuned['amplitude'], tuned['midlevel'],
                         bool(helper['smooth']), replacing=helper)
    from .manager import remove
    remove(helper)
    return replacement


def apply(helper):
    """Freeze the displaced result into real mesh data on the helper (keeps the tag)."""
    with bpy.context.temp_override(object=helper, active_object=helper, selected_objects=[helper]):
        for modifier in list(helper.modifiers):
            bpy.ops.object.modifier_apply(modifier=modifier.name)
    helper['frozen'] = True
    return helper


def replace_source(helper):
    """Destructive: the source object takes the displaced mesh; the helper goes away."""
    source = helper.parent
    if source is None:
        raise ValueError('The displaced copy has no source any more')
    apply(helper)
    old = source.data
    mesh = helper.data.copy()
    for key in (TAG, 'effect_owner'):   # the source's own mesh now, not an owned resource
        mesh.pop(key, None)
    source.data = mesh
    # The copy came from the evaluated source, so its viewport modifiers are already in
    # it; left live they would run again over their own result (a Mirror doubles the
    # mesh, a Subdivision re-subdivides and smooths the relief away).
    for modifier in list(source.modifiers):
        if modifier.show_viewport:
            source.modifiers.remove(modifier)
    from .manager import remove
    remove(helper)
    if old.users == 0:
        bpy.data.meshes.remove(old)
    return source


def vertices_world(obj):
    scene = bpy.context.scene
    with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    count = len(evaluated.data.vertices)
    local = np.empty(count * 3, np.float32)
    evaluated.data.vertices.foreach_get('co', local)
    world = np.asarray(evaluated.matrix_world, np.float64)
    return local.reshape(-1, 3) @ world[:3, :3].T + world[:3, 3]
