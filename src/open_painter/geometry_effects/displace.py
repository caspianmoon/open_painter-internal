"""Material → Geometry: a published height or mask becomes real displaced geometry.

The displacement lives on an owned copy of the evaluated source (never on the
source itself, so the live scene fields and the raster caches keyed on the
source's geometry are not fed their own output). A Geometry Nodes group
subdivides the copy, samples the image through the set's UV map per corner,
takes the first corner's value per vertex (no seam averaging, like the
Displace modifier) and offsets along the normal by ``(h - midlevel) * amplitude``.
"""
import bpy
import numpy as np

from ..utils.ids import new_id
from . import limits
from .manager import TAG

MAX_LEVELS = 4


def _uv_name(obj):
    uv = next((layer for layer in obj.data.uv_layers if layer.active_render), obj.data.uv_layers.active)
    if uv is None:
        raise ValueError(f'{obj.name} requires a UV map for displacement')
    return uv.name


def _node_group(image, uv_name, levels, amplitude, midlevel, smooth, material, created):
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
    set_position = nodes.new('GeometryNodeSetPosition')
    links.new(subdivide.outputs['Mesh'], set_position.inputs['Geometry'])
    links.new(along.outputs['Vector'], set_position.inputs['Offset'])
    shade = nodes.new('GeometryNodeSetShadeSmooth')
    links.new(set_position.outputs['Geometry'], shade.inputs['Geometry'])
    shade.inputs['Shade Smooth'].default_value = True
    last = shade
    if material is not None:
        set_material = nodes.new('GeometryNodeSetMaterial')
        set_material.inputs['Material'].default_value = material
        links.new(shade.outputs['Geometry'], set_material.inputs['Geometry'])
        last = set_material
    links.new(last.outputs['Geometry'], group_out.inputs['Geometry'])
    return group


def create(ts, obj, image, levels=2, amplitude=None, midlevel=.5, smooth=False, replacing=None):
    """An owned, tagged copy of the evaluated source displaced by ``image``; returns the helper."""
    if obj.type != 'MESH':
        raise ValueError('Displacement needs a mesh source')
    levels = int(max(0, min(MAX_LEVELS, levels)))
    amplitude = float(ts.height_distance if amplitude is None else amplitude)
    scene = ts.id_data
    with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    faces = len(evaluated.data.polygons)
    limits.check(scene, 0, faces * (4 ** levels) * 2, replacing=replacing)
    uv_name = _uv_name(obj)
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
        helper.matrix_parent_inverse = obj.matrix_world.inverted()
        material = ts.material if ts.material else None
        group = _node_group(image, uv_name, levels, amplitude, midlevel, smooth, material, created)
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
        helper['source_image'] = image.name
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
    image = bpy.data.images.get(helper.get('source_image', ''))
    source = helper.parent
    if image is None or source is None:
        raise ValueError('Displacement source or image is missing')
    replacement = create(ts, source, image, helper['levels'], helper['amplitude'], helper['midlevel'],
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
    source.data = helper.data.copy()
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
