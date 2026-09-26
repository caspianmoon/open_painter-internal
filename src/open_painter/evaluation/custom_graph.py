"""Custom Graph mask source (0.9.3): the legacy path for a Blender shader group as coverage.

A Custom Graph mask reads a Mask Graph (``procedural_graph``): Open Painter's own
nodes, compiled by the engine and evaluated on the CPU for export. Masks saved
with a Blender shader group keep working here: the CPU reference cannot run
shader nodes, so it bakes the group's first output with Cycles (emission, at the
set resolution, over the assigned meshes) and caches the raster under a content
signature of the tree; the engine samples that raster. Editing the group
re-bakes it.
"""
import bpy
import numpy as np

CACHE = 'custom_graph'
COORDINATE_INPUTS = {'uv', 'vector', 'coordinates'}
PLACEMENT_INPUTS = {'position', 'normal'}   # wired from Geometry: world space in the bake and the shader


def reads_placement(tree):
    """Whether the graph reads the world position or normal: its coverage then moves with the object."""
    from ..procedural_graph import is_native
    if is_native(tree):
        from ..procedural_graph.program import reads_placement as native_placement
        return native_placement(tree)
    return tree is not None and any(item.item_type == 'SOCKET' and item.in_out == 'INPUT'
                                    and item.name.lower() in PLACEMENT_INPUTS for item in tree.interface.items_tree)


def _value(value):
    if value is None or isinstance(value, str):   # String/Menu sockets: a str iterates as characters
        return value
    try:
        return tuple(round(float(v), 6) for v in value)
    except TypeError:
        return round(float(value), 6) if isinstance(value, (int, float)) else str(value)


def _socket_value(socket):
    if socket.is_linked or not hasattr(socket, 'default_value'):
        return None
    return _value(socket.default_value)


def _node_data(node):
    """Read-only sub-structs the property walk skips: ramp stops, curve points, image frames."""
    data = [('mute', node.mute)]
    ramp = getattr(node, 'color_ramp', None)
    if ramp is not None:
        data.append(('color_ramp', ramp.interpolation, ramp.color_mode, ramp.hue_interpolation,
                     tuple((round(float(e.position), 6), _value(e.color)) for e in ramp.elements)))
    mapping = getattr(node, 'mapping', None)
    if mapping is not None:
        data.append(('mapping', mapping.tone, mapping.extend, mapping.use_clip,
                     _value((mapping.clip_min_x, mapping.clip_min_y, mapping.clip_max_x, mapping.clip_max_y)),
                     _value(mapping.black_level), _value(mapping.white_level),
                     tuple(tuple((_value(p.location), p.handle_type) for p in curve.points) for curve in mapping.curves)))
    user = getattr(node, 'image_user', None)
    if user is not None:
        data.append(('image_user', user.frame_current, user.frame_start, user.frame_offset, user.frame_duration,
                     user.use_cyclic, user.use_auto_refresh))
    texture = getattr(node, 'texture_mapping', None)
    if texture is not None:
        data.append(('texture_mapping', texture.vector_type, texture.mapping, texture.mapping_x, texture.mapping_y,
                     texture.mapping_z, _value(texture.translation), _value(texture.rotation), _value(texture.scale),
                     texture.use_min, texture.use_max, _value(texture.min), _value(texture.max)))
    return tuple(data)


def signature(tree, seen=None):
    """Repr-stable content identity of a Mask Graph or a shader node group (nested groups included)."""
    if tree is None:
        return None
    from ..procedural_graph import is_native
    if is_native(tree):
        from ..procedural_graph.program import values
        return ('mask-graph', tree.name, values(tree))
    seen = seen or set()
    if tree.name in seen:
        return ('recursive', tree.name)
    seen = seen | {tree.name}
    nodes = []
    for node in tree.nodes:
        entry = [node.name, node.bl_idname, tuple(_socket_value(s) for s in node.inputs), _node_data(node)]
        for prop in node.bl_rna.properties:
            if prop.identifier in {'rna_type', 'name', 'label', 'location', 'width', 'height', 'select', 'inputs',
                                   'outputs', 'parent', 'color', 'use_custom_color', 'hide', 'mute', 'show_options',
                                   'show_preview', 'show_texture', 'dimensions', 'internal_links', 'location_absolute',
                                   'width_hidden', 'warning_propagation'} or prop.is_readonly:
                continue
            if prop.type in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'}:
                value = getattr(node, prop.identifier)
                if isinstance(value, (set, frozenset)):
                    value = tuple(sorted(value))   # enum flags: unordered otherwise
                entry.append((prop.identifier, tuple(value) if getattr(prop, 'is_array', False) else value))
            elif prop.type == 'POINTER':
                value = getattr(node, prop.identifier)
                if isinstance(value, bpy.types.NodeTree):
                    entry.append((prop.identifier, signature(value, seen)))
                elif isinstance(value, bpy.types.Image):
                    from .resource_keys import image_key
                    entry.append((prop.identifier, image_key(value)))
                elif value is not None and hasattr(value, 'name'):
                    entry.append((prop.identifier, value.name))
        nodes.append(tuple(entry))
    links = tuple(sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier, l.is_muted)
                         for l in tree.links))
    # A fresh group instance (the bake's and the shader's) takes unlinked input values from the
    # interface defaults, and the socket names decide the UV/Position/Normal wiring.
    interface = tuple((item.identifier, item.name, item.in_out, item.socket_type, _value(getattr(item, 'default_value', None)))
                      for item in tree.interface.items_tree if item.item_type == 'SOCKET')
    return (tree.name, tuple(nodes), links, interface)


def _wire(material, tree):
    """Group → Emission → Output inside a staged bake material."""
    nodes, links = material.node_tree.nodes, material.node_tree.links
    output = next((n for n in nodes if n.bl_idname == 'ShaderNodeOutputMaterial'), None) or nodes.new('ShaderNodeOutputMaterial')
    group = nodes.new('ShaderNodeGroup')
    group.node_tree = tree
    coordinates = nodes.new('ShaderNodeTexCoord')
    geometry = nodes.new('ShaderNodeNewGeometry')
    for socket in group.inputs:
        name = socket.name.lower()
        if name in COORDINATE_INPUTS:
            links.new(coordinates.outputs['UV'], socket)
        elif name == 'position':
            links.new(geometry.outputs['Position'], socket)
        elif name == 'normal':
            links.new(geometry.outputs['Normal'], socket)
    emission = nodes.new('ShaderNodeEmission')
    emission.inputs['Strength'].default_value = 1.0
    links.new(group.outputs[0], emission.inputs['Color'])
    links.new(emission.outputs[0], output.inputs['Surface'])


def _context():
    """A context that can run the bake operator: the current one, or a window override in timers."""
    context = bpy.context
    if context.window is not None or bpy.app.background:
        return context, None
    window = next(iter(context.window_manager.windows), None)
    if window is None:
        return context, None
    return context, context.temp_override(window=window, screen=window.screen)


def raster(ts, mask, n):
    """Bake the group's first output over the assigned meshes into an (n, n) float raster."""
    from ..baking.cycles_baker import _run_bake
    from ..baking.pairing import Group, _targets
    from ..baking.staging import prepared
    from ..core.image_manager import create, pixels
    tree = mask.node_group
    if tree is None:
        raise ValueError(f'Custom Graph mask "{mask.name}" has no node group')
    parts = [Group(low, (), None) for low in _targets(ts)]
    context, override = _context()
    image = create(f'{ts.name} Custom Graph', n, (0., 0., 0., 1.))
    scratch = create('OP Custom Graph Scratch', 32)
    try:
        if override is not None:
            override.__enter__()
        try:
            # The evaluation tick and layer-cache jobs raster from a timer: never flush the
            # edit meshes here (``render_geometry.flush_edit_meshes``), the datablock is staged.
            with prepared(context, ts, parts, image, scratch) as stage:
                settings = stage.scene.render.bake
                settings.target = 'IMAGE_TEXTURES'
                settings.use_selected_to_active = False
                settings.use_clear = False
                settings.margin = 2
                settings.use_cage = False
                for material in stage.materials:
                    _wire(material, tree)
                for part in parts:
                    with stage.target(part, 'EMIT') as target:
                        uv = next((uv for uv in target.data.uv_layers if uv.active_render), target.data.uv_layers.active)
                        _run_bake(type='EMIT', uv_layer=uv.name)
        finally:
            if override is not None:
                override.__exit__(None, None, None)
        return pixels(image)[..., 0].copy()
    finally:
        bpy.data.images.remove(image)
        bpy.data.images.remove(scratch)


def evaluate(ts, mask, n, y0, y1, geometry_cache, cache=None):
    # Baked over the assigned meshes: the raster follows the geometry stamp like the mask stack does.
    key = (CACHE, ts.id, mask.id, signature(mask.node_group), n, getattr(geometry_cache, 'geometry_stamp', None))
    store = geometry_cache if geometry_cache is not None else {}
    if cache is not None and key in cache:
        return cache[key][y0:y1]
    if key in store:
        values = store[key]
    else:
        values = raster(ts, mask, n)
        try:
            store[key] = values
        except TypeError:
            pass
    # A bake is far too expensive to repeat per strip when the shared allowance
    # evicts it between strips: the job keeps its own reference.
    if cache is not None:
        cache[key] = values
    return values[y0:y1]
