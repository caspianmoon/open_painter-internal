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
)


def apply(ts, name):
    preset = next(p for p in PRESETS if p[0] == name)
    layer = active(ts)
    if layer is None:
        raise ValueError('Select a layer or folder for the smart mask.')
    required = {'CURVATURE'} | ({'AO'} if preset[1] == 'DIRT' else set())
    available = {m.kind for m in ts.baked_maps if m.image}
    missing = sorted(required - available)
    if missing:
        raise ValueError('Bake Mesh Maps first: ' + ', '.join(missing) + ' required for ' + name + '.')
    mask = layer.masks.add()
    mask.id, mask.name, mask.source = new_id(), name, 'GENERATOR'
    mask.generator, mask.amount, mask.contrast, mask.noise_strength, mask.noise_scale, mask.seed = preset[1:]
    layer.active_mask = len(layer.masks) - 1
    return mask
