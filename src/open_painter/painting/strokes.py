"""Stroke records: creation, linking across texture sets, invalidation."""
import numpy as np
from mathutils import Vector

from ..utils.ids import new_id
from . import stroke_raster

CHANNEL_FIELDS = {'BASE_COLOR': 'base_color', 'ROUGHNESS': 'roughness', 'METALLIC': 'metallic', 'HEIGHT': 'height',
                  'AO': 'ao', 'EMISSION': 'emission', 'OPACITY': 'opacity_value', 'NORMAL': 'normal'}


def strokes_for(project, key):
    """Strokes rendered by layers with this key, in stack order."""
    if not key:
        return []
    return sorted((s for s in project.strokes if s.layer_key == key), key=lambda s: (s.order, s.id))


def linked_layers(project, key):
    """(texture set, layer) pairs rendering the key, across every set of the project."""
    return [(ts, layer) for ts in project.texture_sets for layer in ts.layers
            if getattr(layer, 'stroke_key', '') == key]


def linked_masks(project, key):
    """(texture set, layer, mask) triples of Vector Strokes masks rendering the key."""
    if not key:
        return []
    return [(ts, layer, mask) for ts in project.texture_sets for layer in ts.layers for mask in layer.masks
            if mask.source == 'STROKES' and mask.stroke_key == key]


def rendered_keys(project):
    """Every key some layer or Vector Strokes mask renders."""
    return ({layer.stroke_key for ts in project.texture_sets for layer in ts.layers if layer.stroke_key}
            | {mask.stroke_key for ts in project.texture_sets for layer in ts.layers for mask in layer.masks
               if mask.source == 'STROKES' and mask.stroke_key})


def _wake(project, key, created=()):
    """A stroke change is numeric for the layers and masks rendering the key; a
    newly linked layer is structural for its own set. A mask keyed on its own
    (no layer owns the key) hears about the strokes only through itself."""
    from ..properties.layer import changed, numeric
    for owner_ts, item in linked_layers(project, key):
        (changed if owner_ts in created else numeric)(item, None)
    from ..properties.mask import numeric as mask_numeric
    for _, _, mask in linked_masks(project, key):
        mask_numeric(mask, None)


def stroke_identity(project, key):
    """Repr-stable identity of a key's strokes, for cache keys and boundary identities."""
    return tuple((s.id, s.revision) for s in strokes_for(project, key))


def touch(project, stroke):
    """A stroke edit: bump its revision and wake every layer rendering it."""
    stroke.revision += 1
    project.stroke_serial += 1
    enable_channels(project, stroke.layer_key)
    _wake(project, stroke.layer_key)


def channel_value(stroke, channel):
    value = getattr(stroke, CHANNEL_FIELDS[channel])
    try:
        return tuple(value)[:3] if len(value) >= 3 else (float(value),) * 3
    except TypeError:
        return (float(value),) * 3


def stroke_channels(stroke):
    return [c for c in stroke.channels.split(',') if c in CHANNEL_FIELDS]


def _intersects(ts, lo, hi):
    """Whether any assigned object's world bounds overlap the stroke bounds: a cheap
    pre-filter, not a hit test (a housing's bounds enclose every part inside it)."""
    for ref in ts.assignments:
        obj = ref.object
        if obj is None or obj.type != 'MESH':
            continue
        corners = np.array([obj.matrix_world @ Vector(c) for c in obj.bound_box], np.float32)
        if np.all(corners.max(axis=0) >= lo) and np.all(corners.min(axis=0) <= hi):
            return True
    return False


def ensure_key(layer):
    if not layer.stroke_key:
        layer.stroke_key = new_id()
    return layer.stroke_key


def record(project, ts, layer, samples, settings, plane_normal=(0, 0, 1), universal=True, key=''):
    """Store a gesture as a stroke of the layer's key (or of ``key``: a Vector Strokes
    mask's own key); link paint layers in other sets it reaches."""
    from ..core import layer_stack as stack
    key = key or ensure_key(layer)
    samples = np.ascontiguousarray(samples, np.float32)
    stroke = project.strokes.add()
    stroke.id, stroke.name = new_id(), f'Stroke {len(project.strokes)}'
    stroke.layer_key = key
    stroke.order = max((s.order for s in project.strokes if s.layer_key == key and s != stroke), default=-1) + 1
    for field in ('radius', 'hardness', 'opacity', 'flow', 'accumulate', 'alpha_image', 'projection', 'blend',
                  'base_color', 'roughness', 'metallic', 'height', 'ao', 'emission', 'opacity_value', 'normal'):
        setattr(stroke, field, getattr(settings, field))
    stroke.channels = ','.join(sorted(settings.channels)) if not isinstance(settings.channels, str) else settings.channels
    stroke.plane_normal = plane_normal
    stroke.samples = stroke_raster.pack_samples(samples)
    stroke.sample_count = len(samples)
    lo, hi = stroke_raster.bounds(samples)
    stroke.bounds_min, stroke.bounds_max = lo, hi
    created = []
    # A mask-only key has no paint layer to mirror into other sets.
    if universal and key == layer.stroke_key:
        for other in project.texture_sets:
            if other.id == ts.id or any(item.stroke_key == key for item in other.layers):
                continue
            # Link only sets the stroke marks: the disc may spill onto a neighbouring
            # object the pointer never crossed, so the raster, not the ray hit, decides,
            # and a set the bounds merely enclose must not gain a layer it re-rasters
            # for nothing on every later edit of the key.
            if _intersects(other, lo, hi) and stroke_raster.touches(other, stroke, int(other.resolution)):
                linked = stack.add(other, 'PAINT', layer.name)
                linked.stroke_key = key
                created.append(other)
    project.stroke_serial += 1
    enable_channels(project, key)
    _wake(project, key, created)
    return stroke


def enable_channels(project, key):
    """Paint layers keep channels off until painted: strokes turn on the ones they write."""
    from ..core.layer_stack import channel_for
    wanted = {c for stroke in strokes_for(project, key) for c in stroke_channels(stroke)}
    for _, layer in linked_layers(project, key):
        for channel in wanted:
            prop = channel_for(layer, channel)
            if prop is not None and not prop.enabled:
                prop.enabled = True


def remove(project, stroke_id):
    index = next((i for i, s in enumerate(project.strokes) if s.id == stroke_id), None)
    if index is None:
        return False
    key = project.strokes[index].layer_key
    project.strokes.remove(index)
    project.stroke_serial += 1
    _wake(project, key)
    return True


def purge_orphans(project):
    """Strokes whose key no layer or mask renders any more."""
    keys = rendered_keys(project)
    doomed = [i for i, s in enumerate(project.strokes) if s.layer_key not in keys]
    for index in reversed(doomed):
        project.strokes.remove(index)
    if doomed:
        project.stroke_serial += 1
    return len(doomed)
