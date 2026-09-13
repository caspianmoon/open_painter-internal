"""Original, editable mesh-map mask presets; no Adobe asset dependencies."""
from ..core.layer_stack import active
from ..utils.ids import new_id

# name, generator, amount, contrast, noise strength, noise scale, seed
PRESETS = (
    ('Edge Clean', 'EDGE_WEAR', .48, 3.0, .05, 32, 1),
    ('Edge Broken', 'EDGE_WEAR', .46, 4.0, .65, 65, 17),
    ('Edge Broad', 'EDGE_WEAR', .50, 2.0, .20, 18, 7),
    ('Cavity Dirt', 'DIRT', .45, 3.0, .12, 24, 3),
    ('Dirt Heavy', 'DIRT', .65, 2.5, .45, 42, 11),
    ('Dirt Fine', 'DIRT', .48, 4.5, .50, 120, 23),
    ('Sharp Edge Wear', 'EDGE_WEAR', .35, 6, .1, 60, 4),
    ('Soft Edge Wear', 'EDGE_WEAR', .4, 1.2, .1, 24, 5),
    ('Heavy Edge Damage', 'CHIPS', .9, 4, .5, 28, 6),
    ('Polished Edges', 'EDGE_WEAR', .25, 2, .05, 110, 7),
    ('Edge Rust', 'EDGE_WEAR', .6, 3, .65, 48, 8),
    ('Edge Paint Chips', 'CHIPS', .5, 4, .4, 50, 9),
    ('Deep Cavity Dirt', 'CAVITY_DIRT', .8, 4, .3, 28, 10),
    ('Cavity Moisture', 'CAVITY_DIRT', .4, 2, .15, 18, 11),
    ('Top Surface Dust', 'DUST', .6, 1.4, .35, 48, 12),
    ('Rust Growth', 'RUST', .55, 3, .3, 36, 13),
    ('Random Scratches', 'SCRATCHES', .5, 2, .1, 55, 14),
    ('Dripping Dirt', 'DRIPS', .5, 2, .3, 28, 15),
)


def apply(ts, name, replace=False):
    preset = next(p for p in PRESETS if p[0] == name)
    layer = active(ts)
    if layer is None:
        raise ValueError('Select a layer or folder for the smart mask.')
    from ..generators.base import required_maps
    required = required_maps(preset[1])
    available = {m.kind for m in ts.baked_maps if m.image}
    missing = sorted(required - available)
    if missing:
        raise ValueError('Bake Mesh Maps first: ' + ', '.join(missing) + ' required for ' + name + '.')
    if replace:
        layer.masks.clear()
    mask = layer.masks.add()
    mask.id, mask.name, mask.source = new_id(), name, 'GENERATOR'
    mask.operation = 'NORMAL'
    mask.generator, mask.amount, mask.contrast, mask.noise_strength, mask.noise_scale, mask.seed = preset[1:]
    layer.active_mask = len(layer.masks) - 1
    return mask
