"""Small editable graph recipes built from the same layers, masks and filters as Painter.

Each insertion owns fresh IDs and (for shader recipes) a fresh ShaderNodeTree.
No recipe replaces the current material or requires a mesh-map bake.
"""

from ..core import layer_stack as stack
from ..utils.ids import new_id


def _surface(color, roughness, metallic=0, procedural='', scale=8, height=.002):
    channels = {
        'BASE_COLOR': {'color': (*color, 1)},
        'ROUGHNESS': {'value': roughness},
        'METALLIC': {'value': metallic},
    }
    if procedural:
        for name in ('BASE_COLOR', 'ROUGHNESS'):
            channels[name].update(procedural=procedural, procedural_scale=scale, procedural_detail=.12)
        channels['HEIGHT'] = {'procedural': procedural, 'procedural_scale': scale, 'procedural_height': height}
    return channels


TEMPLATES = {
    'ROUGH_PLASTIC': {
        'name': 'Rough Plastic', 'description': 'Molded plastic grain with editable color, roughness and fine relief',
        'layers': [{'name': 'Plastic', 'channels': _surface((.055, .12, .2), .62, procedural='PLASTIC_GRAIN', scale=18),
                    'filters': {'ROUGHNESS': [{'kind': 'LEVELS', 'black': .08, 'white': .95}]}}],
    },
    'BRUSHED_METAL': {
        'name': 'Brushed Metal', 'description': 'Metal substrate with directional brushing and roughness levels',
        'layers': [{'name': 'Brushed metal', 'channels': _surface((.48, .5, .53), .32, 1, 'BRUSHED_METAL_LINES', 12),
                    'filters': {'ROUGHNESS': [{'kind': 'LEVELS', 'black': .05, 'white': .95}]}}],
    },
    'CHIPPED_PAINT': {
        'name': 'Chipped Paint', 'description': 'Metal under a paint coat with procedural chips and an editable threshold',
        'layers': [
            {'name': 'Metal substrate', 'channels': _surface((.22, .24, .27), .48, 1)},
            {'name': 'Paint coat', 'channels': _surface((.25, .035, .018), .36, procedural='ORANGE_PEEL', scale=16),
             'masks': [{'source': 'PROCEDURAL', 'name': 'Paint coverage', 'procedural': 'FRACTURED_PAINT', 'noise_scale': 7},
                       {'source': 'FILTER', 'name': 'Chip threshold', 'effect': {'kind': 'THRESHOLD', 'amount': .35, 'softness': .08}}]},
        ],
    },
    'LAYERED_GRUNGE': {
        'name': 'Layered Grunge', 'description': 'A nested mask group combining broad stains, fine breakup and levels',
        'layers': [{'name': 'Grime', 'channels': _surface((.075, .05, .025), .85), 'masks': [
            {'source': 'GROUP', 'name': 'Grunge coverage', 'children': [
                {'source': 'PROCEDURAL', 'name': 'Broad stains', 'procedural': 'GRUNGE_MEDIUM', 'noise_scale': 5},
                {'source': 'PROCEDURAL', 'name': 'Fine breakup', 'procedural': 'GRUNGE_FINE', 'noise_scale': 24,
                 'operation': 'MULTIPLY'},
                {'source': 'FILTER', 'name': 'Coverage levels', 'effect': {'kind': 'LEVELS', 'black': .08, 'white': .55}},
            ]},
        ]}],
    },
    'SHARED_DETAIL': {
        'name': 'Shared Surface Detail', 'description': 'One published mask drives roughness and height through connected anchor links',
        'layers': [
            {'name': 'Detail source', 'channels': {}, 'masks': [
                {'source': 'PROCEDURAL', 'name': 'Surface grain', 'procedural': 'METAL_GRAIN', 'noise_scale': 18},
                {'source': 'ANCHOR', 'name': 'Shared grain', 'key': 'grain'},
            ]},
            {'name': 'Detailed surface', 'channels': {
                **_surface((.3, .32, .35), .45, 1),
                'ROUGHNESS': {'anchor_key': 'grain'}, 'HEIGHT': {'anchor_key': 'grain'},
            }, 'filters': {
                'ROUGHNESS': [{'kind': 'LEVELS', 'output_black': .25, 'output_white': .6}],
                'HEIGHT': [{'kind': 'LEVELS', 'output_black': 0, 'output_white': .003}],
            }},
        ],
    },
    'TOP_DUST': {
        'name': 'Top Surface Dust', 'description': 'Upward-facing mesh coverage multiplied by particulate breakup; no bake required',
        'layers': [{'name': 'Dust', 'channels': _surface((.32, .27, .19), .92), 'masks': [
            {'source': 'GROUP', 'name': 'Dust placement', 'children': [
                {'source': 'MESH', 'name': 'Upward surfaces', 'mesh': {'kind': 'NORMAL_DIRECTION', 'direction': (0, 0, 1)}},
                {'source': 'PROCEDURAL', 'name': 'Dust breakup', 'procedural': 'GRUNGE_DUST', 'noise_scale': 14,
                 'operation': 'MULTIPLY'},
            ]},
        ]}],
    },
}

for _key, _name, _description in (
    ('NOISE_RAMP', 'Noise → Ramp', 'A reusable shader group: UV coordinates into Noise and an editable Color Ramp'),
    ('CELLULAR_RAMP', 'Cells → Ramp', 'A reusable shader group: Voronoi distance remapped with a Color Ramp'),
    ('LAYERED_NOISE', 'Layered Noise → Ramp', 'A reusable shader group: two noise scales multiplied and remapped'),
):
    TEMPLATES[_key] = {
        'name': _name, 'description': _description, 'shader': _key,
        'layers': [{'name': 'Masked surface', 'channels': _surface((.18, .24, .32), .55),
                    'masks': [{'source': 'GRAPH', 'name': _name}]}],
    }

ITEMS = [(key, value['name'], value['description']) for key, value in TEMPLATES.items()]


def shader_group(kind, name):
    """Create a standalone shader group whose first output is mask coverage."""
    import bpy
    from ..constants import OWNER
    tree = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    try:
        tree[OWNER] = True
        tree.interface.new_socket(name='UV', in_out='INPUT', socket_type='NodeSocketVector')
        tree.interface.new_socket(name='Coverage', in_out='OUTPUT', socket_type='NodeSocketFloat')
        inputs = tree.nodes.new('NodeGroupInput')
        inputs.location = (-650, 40)
        output = tree.nodes.new('NodeGroupOutput')
        output.location = (280, 40)
        source = tree.nodes.new('ShaderNodeTexVoronoi' if kind == 'CELLULAR_RAMP' else 'ShaderNodeTexNoise')
        source.location = (-420, 40)
        source.inputs['Scale'].default_value = 8
        tree.links.new(inputs.outputs['UV'], source.inputs['Vector'])
        signal = source.outputs['Distance' if kind == 'CELLULAR_RAMP' else 'Fac']
        if kind == 'LAYERED_NOISE':
            detail = tree.nodes.new('ShaderNodeTexNoise')
            detail.location = (-420, -300)
            detail.inputs['Scale'].default_value = 36
            tree.links.new(inputs.outputs['UV'], detail.inputs['Vector'])
            multiply = tree.nodes.new('ShaderNodeMath')
            multiply.operation = 'MULTIPLY'
            multiply.location = (-180, 40)
            tree.links.new(signal, multiply.inputs[0])
            tree.links.new(detail.outputs['Fac'], multiply.inputs[1])
            signal = multiply.outputs[0]
        ramp = tree.nodes.new('ShaderNodeValToRGB')
        ramp.location = (0, 40)
        ramp.color_ramp.elements[0].position = .1 if kind == 'LAYERED_NOISE' else .25
        ramp.color_ramp.elements[1].position = .45 if kind == 'LAYERED_NOISE' else .75
        tree.links.new(signal, ramp.inputs['Fac'])
        tree.links.new(ramp.outputs['Color'], output.inputs['Coverage'])
        return tree
    except Exception:
        bpy.data.node_groups.remove(tree)
        raise


def _properties(target, values):
    for field, value in values.items():
        if isinstance(value, dict):
            _properties(getattr(target, field), value)
        else:
            setattr(target, field, value)


def _masks(layer, specs, anchors, parent=''):
    for spec in specs:
        mask = layer.masks.add()
        mask.id, mask.parent_id, mask.operation = new_id(), parent, 'NORMAL'
        _properties(mask, {k: v for k, v in spec.items() if k not in {'children', 'key'}})
        identifier = mask.id
        if 'key' in spec:
            anchors[spec['key']] = identifier
        _masks(layer, spec.get('children', ()), anchors, identifier)


def apply(ts, key, parent_id=''):
    """Insert a fresh editable folder, rolling back only this insertion on failure."""
    if key not in TEMPLATES:
        raise ValueError('Choose a graph template from the Templates menu.')
    if parent_id and not any(layer.id == parent_id and layer.type == 'FOLDER' for layer in ts.layers):
        raise ValueError('The destination folder no longer exists.')
    recipe = TEMPLATES[key]
    old_ids = {layer.id for layer in ts.layers}
    old_active = stack.active(ts)
    old_active_id = old_active.id if old_active is not None else ''
    group = None
    with stack.replay(ts):
        try:
            names = {layer.name for layer in ts.layers}
            name, count = recipe['name'], 2
            while name in names:
                name = f"{recipe['name']} {count}"
                count += 1
            folder = stack.add(ts, 'FOLDER', name, parent_id)
            folder_id = folder.id
            folder.group_mode = 'ISOLATED'
            if recipe.get('shader'):
                group = shader_group(recipe['shader'], name)
            anchors = {}
            for spec in recipe['layers']:
                layer = stack.add(ts, 'FILL', spec['name'], folder_id)
                for prop in layer.channels:
                    prop.enabled = prop.channel in spec['channels']
                    settings = spec['channels'].get(prop.channel, {})
                    _properties(prop, {k: v for k, v in settings.items() if k != 'anchor_key'})
                    if 'anchor_key' in settings:
                        prop.anchor = anchors[settings['anchor_key']]
                    for values in spec.get('filters', {}).get(prop.channel, ()):
                        effect = prop.effects.add()
                        effect.id = new_id()
                        _properties(effect, values)
                _masks(layer, spec.get('masks', ()), anchors)
                for mask in layer.masks:
                    if mask.source == 'GRAPH':
                        mask.node_group = group
            ts.active_layer = stack.index_of(ts, folder_id)
        except Exception:
            for index in reversed(range(len(ts.layers))):
                if ts.layers[index].id not in old_ids:
                    ts.layers.remove(index)
            ts.active_layer = next((i for i, layer in enumerate(ts.layers) if layer.id == old_active_id), -1)
            if group is not None:
                import bpy
                bpy.data.node_groups.remove(group)
            raise
    return next(layer for layer in ts.layers if layer.id == folder_id)
