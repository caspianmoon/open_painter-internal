"""Engine Preview: show the texture set as the target engine computes it.

The adapters are role-tagged nodes (``Engine · <stage>``) inside the Open Painter
Texture Set node's group, between its samplers and its outputs, so the material stays
the node, a Principled BSDF and a Material Output: 8-bit quantisation of every exported
channel (through sRGB for colour), the DirectX green-flip round trip and BC5 Z
reconstruction for normals, smoothness round trip for roughness, AO removed from direct
light (engines apply it to indirect light only, ``texture_set_node.ao_factor``), the
target's specular F0 on the BSDF (``specular``), and the Standard view transform.
Turning the preview off drops the adapters.
"""
import bpy

from ..constants import OWNER

PREFIX = 'Engine · '
LIBRARY_VERSION = 1


def _library(name, build):
    """A shared, versioned node group of pure math; rebuilt when the version changes."""
    full = 'OP ' + PREFIX + name
    tree = bpy.data.node_groups.get(full)
    if tree is not None and tree.get('op_library_version') == LIBRARY_VERSION and tree.get(OWNER):
        return tree
    if tree is not None:
        bpy.data.node_groups.remove(tree)
    tree = bpy.data.node_groups.new(full, 'ShaderNodeTree')
    tree[OWNER], tree['op_library_version'] = True, LIBRARY_VERSION
    build(tree)
    return tree


def _math(tree, operation, a, b=None, clamp=False):
    node = tree.nodes.new('ShaderNodeMath')
    node.operation, node.use_clamp = operation, clamp
    for index, value in enumerate((a, b)):
        if value is None:
            continue
        if hasattr(value, 'node'):
            tree.links.new(value, node.inputs[index])
        else:
            node.inputs[index].default_value = value
    return node.outputs[0]


def _quantize_float(tree):
    tree.interface.new_socket('Value', in_out='INPUT', socket_type='NodeSocketFloat')
    tree.interface.new_socket('Value', in_out='OUTPUT', socket_type='NodeSocketFloat')
    group_in, group_out = tree.nodes.new('NodeGroupInput'), tree.nodes.new('NodeGroupOutput')
    scaled = _math(tree, 'MULTIPLY', group_in.outputs['Value'], 255.0)
    rounded = _math(tree, 'ROUND', scaled)
    tree.links.new(_math(tree, 'DIVIDE', rounded, 255.0), group_out.inputs['Value'])


def _srgb_channel(tree, value, encode):
    """Piecewise sRGB transfer on one float socket."""
    if encode:
        low = _math(tree, 'MULTIPLY', value, 12.92)
        high = _math(tree, 'SUBTRACT', _math(tree, 'MULTIPLY', _math(tree, 'POWER', value, 1 / 2.4), 1.055), .055)
        below = _math(tree, 'LESS_THAN', value, .0031308)
    else:
        low = _math(tree, 'DIVIDE', value, 12.92)
        high = _math(tree, 'POWER', _math(tree, 'DIVIDE', _math(tree, 'ADD', value, .055), 1.055), 2.4)
        below = _math(tree, 'LESS_THAN', value, .04045)
    mix = tree.nodes.new('ShaderNodeMix')
    mix.data_type = 'FLOAT'
    tree.links.new(below, mix.inputs['Factor'])
    tree.links.new(high, mix.inputs[2])   # A (factor 0)
    tree.links.new(low, mix.inputs[3])    # B (factor 1)
    return mix.outputs[0]


def _quantize_color(tree):
    """Colour → sRGB encode → 8-bit → decode, per channel: what a BC/8-bit colour texture keeps."""
    tree.interface.new_socket('Color', in_out='INPUT', socket_type='NodeSocketColor')
    tree.interface.new_socket('Color', in_out='OUTPUT', socket_type='NodeSocketColor')
    group_in, group_out = tree.nodes.new('NodeGroupInput'), tree.nodes.new('NodeGroupOutput')
    separate = tree.nodes.new('ShaderNodeSeparateColor')
    tree.links.new(group_in.outputs['Color'], separate.inputs['Color'])
    combine = tree.nodes.new('ShaderNodeCombineColor')
    quantize = _library('Quantize', _quantize_float)
    for index in range(3):
        encoded = _srgb_channel(tree, separate.outputs[index], True)
        group = tree.nodes.new('ShaderNodeGroup')
        group.node_tree = quantize
        tree.links.new(encoded, group.inputs['Value'])
        decoded = _srgb_channel(tree, group.outputs['Value'], False)
        tree.links.new(decoded, combine.inputs[index])
    tree.links.new(combine.outputs['Color'], group_out.inputs['Color'])


def _normal_adapter(tree):
    """Tangent normal colour → optional DirectX round trip → 8-bit → optional Z reconstruction."""
    tree.interface.new_socket('Color', in_out='INPUT', socket_type='NodeSocketColor')
    tree.interface.new_socket('Flip Green', in_out='INPUT', socket_type='NodeSocketFloat')
    tree.interface.new_socket('Reconstruct Z', in_out='INPUT', socket_type='NodeSocketFloat')
    tree.interface.new_socket('Color', in_out='OUTPUT', socket_type='NodeSocketColor')
    group_in, group_out = tree.nodes.new('NodeGroupInput'), tree.nodes.new('NodeGroupOutput')
    separate = tree.nodes.new('ShaderNodeSeparateColor')
    tree.links.new(group_in.outputs['Color'], separate.inputs['Color'])
    quantize = _library('Quantize', _quantize_float)

    def q(value):
        group = tree.nodes.new('ShaderNodeGroup')
        group.node_tree = quantize
        tree.links.new(value, group.inputs['Value'])
        return group.outputs['Value']

    r = q(separate.outputs[0])
    # Green: stored as 1-G when DirectX, quantised, flipped back on decode.
    flipped = _math(tree, 'SUBTRACT', 1.0, separate.outputs[1])
    stored = tree.nodes.new('ShaderNodeMix'); stored.data_type = 'FLOAT'
    tree.links.new(group_in.outputs['Flip Green'], stored.inputs['Factor'])
    tree.links.new(separate.outputs[1], stored.inputs[2]); tree.links.new(flipped, stored.inputs[3])
    stored_q = q(stored.outputs[0])
    unflipped = _math(tree, 'SUBTRACT', 1.0, stored_q)
    g_mix = tree.nodes.new('ShaderNodeMix'); g_mix.data_type = 'FLOAT'
    tree.links.new(group_in.outputs['Flip Green'], g_mix.inputs['Factor'])
    tree.links.new(stored_q, g_mix.inputs[2]); tree.links.new(unflipped, g_mix.inputs[3])
    g = g_mix.outputs[0]
    b_stored = q(separate.outputs[2])
    # Z reconstruction: z = sqrt(max(0, 1 - x² - y²)) from the decoded xy in -1..1.
    x = _math(tree, 'SUBTRACT', _math(tree, 'MULTIPLY', r, 2.0), 1.0)
    y = _math(tree, 'SUBTRACT', _math(tree, 'MULTIPLY', g, 2.0), 1.0)
    zz = _math(tree, 'SUBTRACT', _math(tree, 'SUBTRACT', 1.0, _math(tree, 'MULTIPLY', x, x)), _math(tree, 'MULTIPLY', y, y))
    z = _math(tree, 'SQRT', _math(tree, 'MAXIMUM', zz, 0.0))
    z_color = _math(tree, 'MULTIPLY', _math(tree, 'ADD', z, 1.0), .5)
    b_mix = tree.nodes.new('ShaderNodeMix'); b_mix.data_type = 'FLOAT'
    tree.links.new(group_in.outputs['Reconstruct Z'], b_mix.inputs['Factor'])
    tree.links.new(b_stored, b_mix.inputs[2]); tree.links.new(z_color, b_mix.inputs[3])
    combine = tree.nodes.new('ShaderNodeCombineColor')
    tree.links.new(r, combine.inputs[0]); tree.links.new(g, combine.inputs[1]); tree.links.new(b_mix.outputs[0], combine.inputs[2])
    tree.links.new(combine.outputs['Color'], group_out.inputs['Color'])


def _gloss_adapter(tree):
    """Roughness → optional smoothness round trip → 8-bit."""
    tree.interface.new_socket('Value', in_out='INPUT', socket_type='NodeSocketFloat')
    tree.interface.new_socket('Smoothness', in_out='INPUT', socket_type='NodeSocketFloat')
    tree.interface.new_socket('Value', in_out='OUTPUT', socket_type='NodeSocketFloat')
    group_in, group_out = tree.nodes.new('NodeGroupInput'), tree.nodes.new('NodeGroupOutput')
    quantize = _library('Quantize', _quantize_float)
    value = group_in.outputs['Value']
    smooth = _math(tree, 'SUBTRACT', 1.0, value)
    stored = tree.nodes.new('ShaderNodeMix'); stored.data_type = 'FLOAT'
    tree.links.new(group_in.outputs['Smoothness'], stored.inputs['Factor'])
    tree.links.new(value, stored.inputs[2]); tree.links.new(smooth, stored.inputs[3])
    group = tree.nodes.new('ShaderNodeGroup'); group.node_tree = quantize
    tree.links.new(stored.outputs[0], group.inputs['Value'])
    back = _math(tree, 'SUBTRACT', 1.0, group.outputs['Value'])
    out = tree.nodes.new('ShaderNodeMix'); out.data_type = 'FLOAT'
    tree.links.new(group_in.outputs['Smoothness'], out.inputs['Factor'])
    tree.links.new(group.outputs['Value'], out.inputs[2]); tree.links.new(back, out.inputs[3])
    tree.links.new(out.outputs[0], group_out.inputs['Value'])


def specular(project):
    """The Principled BSDF's Specular IOR Level: the target's under Engine Preview, else Blender's."""
    if project is not None and project.engine_preview:
        from . import get
        return get(project.target).specular
    return .5


def adapt(ts, project, colors, node, connect):
    """Insert the engine adapters for the project's target (``node``/``connect`` build them in
    the texture set node's group); returns the adapted sockets."""
    from . import get
    target = get(project.target)
    adapted = dict(colors)
    quantize = project.engine_preview_quantize
    if quantize:
        color_group = node(PREFIX + 'Base Color', 'ShaderNodeGroup', (-250, 320))
        color_group.node_tree = _library('Quantize Color', _quantize_color)
        connect(colors['BASE_COLOR'], color_group.inputs['Color'])
        adapted['BASE_COLOR'] = color_group.outputs['Color']
        metal = node(PREFIX + 'Metallic', 'ShaderNodeGroup', (-250, 80))
        metal.node_tree = _library('Quantize', _quantize_float)
        connect(colors['METALLIC'], metal.inputs['Value'])
        adapted['METALLIC'] = metal.outputs['Value']
    gloss = node(PREFIX + 'Roughness', 'ShaderNodeGroup', (-250, 180))
    gloss.node_tree = _library('Gloss', _gloss_adapter)
    gloss.inputs['Smoothness'].default_value = 1.0 if target.gloss == 'SMOOTHNESS' else 0.0
    connect(colors['ROUGHNESS'], gloss.inputs['Value'])
    if quantize or target.gloss == 'SMOOTHNESS':
        adapted['ROUGHNESS'] = gloss.outputs['Value']
    normal = node(PREFIX + 'Normal', 'ShaderNodeGroup', (-250, -200))
    normal.node_tree = _library('Normal', _normal_adapter)
    normal.inputs['Flip Green'].default_value = 1.0 if target.normal == 'DIRECTX' else 0.0
    normal.inputs['Reconstruct Z'].default_value = 1.0 if project.engine_preview_normal_reconstruct else 0.0
    connect(colors['NORMAL'], normal.inputs['Color'])
    adapted['NORMAL'] = normal.outputs['Color']
    return adapted


def remove(nodes):
    """Drop the engine adapter nodes earlier versions placed in a material (they live in the
    texture set node's group now)."""
    for item in list(nodes):
        if item.get(OWNER) and str(item.get('role', '')).startswith(PREFIX):
            nodes.remove(item)


def enter(scene, project):
    """Save the colour management and switch to the target's view transform."""
    if not project.saved_view_transform:
        project.saved_view_transform = scene.view_settings.view_transform
        project.saved_look = scene.view_settings.look
    from . import get
    wanted = get(project.target).view_transform
    try:
        if scene.view_settings.view_transform != wanted:
            scene.view_settings.view_transform = wanted
        if scene.view_settings.look != 'None':
            scene.view_settings.look = 'None'
    except TypeError:
        pass


def leave(scene, project):
    if project.saved_view_transform:
        try:
            scene.view_settings.view_transform = project.saved_view_transform
            scene.view_settings.look = project.saved_look or 'None'
        except TypeError:
            pass
        project.saved_view_transform = ''
        project.saved_look = ''
