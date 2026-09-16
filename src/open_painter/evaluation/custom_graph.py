"""Custom Graph mask source (0.9.3): an artist's shader node group as coverage.

The shader preview instances the group natively (``Graph._custom_graph``).
The CPU reference — export, snapshots, the CPU backend — cannot run shader
nodes, so it bakes the group's first output with Cycles (emission, at the set
resolution, over the assigned meshes) and caches the raster under a content
signature of the tree. Editing the group re-bakes on the next CPU evaluation.
"""
import bpy
import numpy as np

CACHE = 'custom_graph'


def _socket_value(socket):
    if socket.is_linked or not hasattr(socket, 'default_value'):
        return None
    value = socket.default_value
    try:
        return tuple(round(float(v), 6) for v in value)
    except TypeError:
        return round(float(value), 6) if isinstance(value, (int, float)) else str(value)


def signature(tree, seen=None):
    """Repr-stable content identity of a shader node group (nested groups included)."""
    if tree is None:
        return None
    seen = seen or set()
    if tree.name in seen:
        return ('recursive', tree.name)
    seen = seen | {tree.name}
    nodes = []
    for node in tree.nodes:
        entry = [node.name, node.bl_idname, tuple(_socket_value(s) for s in node.inputs)]
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
    links = tuple(sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                         for l in tree.links))
    return (tree.name, tuple(nodes), links)


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
        if name in {'uv', 'vector', 'coordinates'}:
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


def evaluate(ts, mask, n, y0, y1, geometry_cache):
    key = (CACHE, ts.id, mask.id, signature(mask.node_group), n)
    store = geometry_cache if geometry_cache is not None else {}
    if key in store:
        return store[key][y0:y1]
    values = raster(ts, mask, n)
    try:
        store[key] = values
    except TypeError:
        pass
    return values[y0:y1]
