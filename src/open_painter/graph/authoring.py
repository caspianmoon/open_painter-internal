"""Validated additions to a graph's owning stack, independent of editor context."""

from ..core import layer_stack as stack, mask_stack
from ..texture_engine.filter_defaults import initial_amount
from ..utils.ids import new_id


def add(ts, layer, kind, choice='', channel='ROUGHNESS', mask_index=None):
    """Return the new node ID. Validate all choices before touching collections."""
    from ..texture_engine.filters import CATALOG as filters
    from ..texture_engine.procedurals import CATALOG as procedurals
    from ..properties.mesh_selector import KINDS
    from ..properties.mask import OP_Mask
    from ..properties.influencer import OP_Influencer
    import bpy

    choices = {
        'LAYER': {'FILL', 'PAINT', 'FOLDER'},
        'RECIPE': {'EDGE_WEAR_2'},
        'MASK': {'PAINT', 'FILL', 'BAKED', 'NOISE', 'GROUP', 'GRAPH', 'STROKES'},
        'PROCEDURAL': procedurals, 'PROCEDURAL_FILL': procedurals,
        'GENERATOR': {item.identifier for item in OP_Mask.bl_rna.properties['generator'].enum_items},
        'SCENE': {item.identifier for item in OP_Influencer.bl_rna.properties['kind'].enum_items},
        'MESH': {item[0] for item in KINDS},
        'FILTER': filters, 'MASK_FILTER': filters,
        'REFERENCE': {''}, 'MASK_ANCHOR': {''}, 'CHANNEL_ANCHOR': {''},
    }
    if kind not in choices or choice not in choices[kind]:
        raise ValueError('Choose a supported node from the Add menu.')
    title = choice.replace('_', ' ').title()
    if kind in {'LAYER', 'PROCEDURAL_FILL'}:
        parent = (layer.id if layer.type == 'FOLDER' else layer.parent_id) if layer is not None else ''
        with stack.replay(ts):
            if layer is not None:
                ts.active_layer = stack.index_of(ts, layer.id)
            added = stack.add(ts, choice if kind == 'LAYER' else 'FILL',
                              title if kind == 'PROCEDURAL_FILL' else None, parent)
            if kind == 'PROCEDURAL_FILL':
                for prop in added.channels:
                    if prop.channel in {'BASE_COLOR', 'ROUGHNESS', 'HEIGHT'}:
                        prop.enabled, prop.procedural = True, choice
                        prop.procedural_height = .002
            return added.id
    if layer is None:
        raise ValueError('Select a layer node first: masks and filters are added to that layer.')
    if kind == 'RECIPE':
        from .operators import _add_edge_wear_2
        with stack.replay(ts):
            return _add_edge_wear_2(ts, layer)
    if kind in {'FILTER', 'CHANNEL_ANCHOR'}:
        if layer.type == 'FOLDER':
            raise ValueError('Channel filters belong on fill and paint layers.')
        prop = stack.channel_for(layer, channel)
        if prop is None:
            raise ValueError('Choose a channel for the filter.')
        with stack.replay(ts):
            effect = prop.effects.add()
            effect.id = new_id()
            if kind == 'CHANNEL_ANCHOR':
                from ..ui.anchors import _next_name
                effect.name, effect.kind = _next_name(ts), 'ANCHOR'
            else:
                effect.kind, effect.name, effect.amount = choice, title, initial_amount(choice)
            prop.active_effect = len(prop.effects) - 1
            return effect.id
    with stack.replay(ts):
        mask = layer.masks.add()
        mask.id, mask.name = new_id(), title
        identifier = mask.id
        image = None
        group = None
        try:
            if kind == 'MASK':
                if choice == 'NOISE':
                    # The catalog's value noise, evaluated in the engine's programs: the legacy
                    # Noise source rasters its sine-hash noise on the CPU (kept for existing files).
                    mask.source, mask.procedural = 'PROCEDURAL', 'VALUE'
                else:
                    mask.source = choice
                mask.name = {'GROUP': 'Mask Group', 'GRAPH': 'Custom Graph', 'STROKES': 'Vector Strokes'}.get(
                    choice, title + ' Mask')
                if choice in {'PAINT', 'FILL'}:
                    from ..core.image_manager import create
                    image = create(f'{ts.name}_{layer.name}_Mask',
                                   int(ts.resolution) if choice == 'PAINT' else 32, (1, 1, 1, 1))
                    mask.image = image
                    image.pack()
                elif choice == 'STROKES':
                    mask.stroke_key = new_id()
                elif choice == 'GRAPH':
                    from .templates import shader_group
                    group = shader_group('NOISE_RAMP', f'{ts.name} Mask Graph')
                    mask.node_group = group
            elif kind == 'GENERATOR':
                mask.source, mask.generator = 'GENERATOR', choice
            elif kind == 'PROCEDURAL':
                mask.source, mask.procedural = 'PROCEDURAL', choice
            elif kind == 'SCENE':
                mask.source = 'SCENE'
                mask.influence.kind, mask.influence.mode = choice, 'LIVE'
            elif kind == 'MESH':
                mask.source, mask.mesh.kind = 'MESH', choice
            elif kind == 'MASK_FILTER':
                mask.source, mask.effect.kind = 'FILTER', choice
                mask.effect.amount = initial_amount(choice)
            elif kind == 'REFERENCE':
                mask.source, mask.name = 'REFERENCE', 'Published Input'
            elif kind == 'MASK_ANCHOR':
                from ..ui.anchors import _next_name
                mask.source, mask.name = 'ANCHOR', _next_name(ts)
            layer.active_mask = mask_stack.adopt(layer, len(layer.masks) - 1, mask_index)
        except Exception:
            index = next(i for i, item in enumerate(layer.masks) if item.id == identifier)
            layer.masks.remove(index)
            if image is not None:
                bpy.data.images.remove(image)
            if group is not None:
                bpy.data.node_groups.remove(group)
            raise
    return identifier
