"""Stroke records: creation, linking across texture sets, invalidation."""
import numpy as np
from mathutils import Vector

from ..utils.ids import new_id
from . import stroke_raster

CHANNEL_FIELDS = {'BASE_COLOR': 'base_color', 'ROUGHNESS': 'roughness', 'METALLIC': 'metallic', 'HEIGHT': 'height',
                  'AO': 'ao', 'EMISSION': 'emission', 'OPACITY': 'opacity_value', 'NORMAL': 'normal'}


def permits(stroke, ts):
    """Whether the stroke's scope lets texture set ``ts`` render it (0.13 hardening, H2).

    A stroke records the sets it may reach when it is recorded (``scope``); one recorded
    before the field existed (``scope_version`` 0) renders wherever its key is shared, as
    it always did."""
    if ts is None or not getattr(stroke, 'scope_version', 0):
        return True
    return ts.id in scope_ids(stroke)


def scope_ids(stroke):
    return {item for item in getattr(stroke, 'scope', '').split(',') if item}


def strokes_for(project, key, ts=None):
    """Strokes rendered by layers with this key, in stack order; with ``ts``, only those
    whose scope permits that set."""
    if not key:
        return []
    return sorted((s for s in project.strokes if s.layer_key == key and permits(s, ts)), key=lambda s: (s.order, s.id))


def permitted_sets(project, stroke):
    """(texture set, layer) pairs that render the stroke: the linked layers its scope permits."""
    return [(ts, layer) for ts, layer in linked_layers(project, stroke.layer_key) if permits(stroke, ts)]


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


def stroke_identity(project, key, ts=None):
    """Repr-stable identity of a key's strokes (those ``ts`` may render, when given), for
    cache keys and boundary identities."""
    return tuple((s.id, s.revision) for s in strokes_for(project, key, ts))


def touch(project, stroke):
    """A stroke edit: bump its revision and wake every layer rendering it."""
    from ..core.layer_stack import replaying
    if replaying():
        # A snapshot replay (restore_records) writes every field of the record, each
        # firing this through the property callback: the replayer wakes the key once.
        return
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


SCOPE_VERSION = 1   # explicit per-stroke permission (0.13 hardening, H2)


def record(project, ts, layer, samples, settings, plane_normal=(0, 0, 1), universal=True, key='', sets=None,
           anchors=None, dabs=None):
    """Store a gesture as a stroke of the layer's key (or of ``key``: a Vector Strokes
    mask's own key); link paint layers in other sets it reaches. ``sets`` (ids) limits
    the sets a universal stroke may link to those the gesture explicitly crossed.

    The stroke's permission (``scope``) is decided here and never widens later: its own
    set, plus — for a universal layer stroke — every other set the gesture may reach
    (``sets``, or any set when the caller names none) that already renders the key or that
    the raster marks now (which links it). A set linked by an earlier stroke gains nothing
    from a stroke that never reached it. ``anchors`` are the (x, y, z, polygon) surface hits
    of the samples, the seeds of a SURFACE stroke's reach (``settings.reach``)."""
    from ..core import layer_stack as stack
    key = key or ensure_key(layer)
    samples = np.ascontiguousarray(samples, np.float32)
    stroke = project.strokes.add()
    stroke.id, stroke.name = new_id(), f'Stroke {len(project.strokes)}'
    stroke.layer_key = key
    stroke.order = max((s.order for s in project.strokes if s.layer_key == key and s != stroke), default=-1) + 1
    for field in ('radius', 'hardness', 'opacity', 'flow', 'accumulate', 'alpha_image', 'projection', 'blend',
                  'base_color', 'roughness', 'metallic', 'height', 'ao', 'emission', 'opacity_value', 'normal',
                  'depth_limit', 'reach'):
        if hasattr(settings, field):
            setattr(stroke, field, getattr(settings, field))
    stroke.channels = ','.join(sorted(settings.channels)) if not isinstance(settings.channels, str) else settings.channels
    stroke.plane_normal = plane_normal
    stroke.samples = stroke_raster.pack_samples(samples)
    stroke.sample_count = len(samples)
    if dabs is not None:
        from .payload import VERSION, encode
        stroke.payload_version, stroke.dab_payload = VERSION, encode(dabs)
    if anchors is not None and len(anchors) == len(samples):
        from .surface_reach import pack_anchors
        stroke.anchors = pack_anchors(anchors)
    lo, hi = stroke_raster.bounds(samples)
    stroke.bounds_min, stroke.bounds_max = lo, hi
    permitted = [ts.id]
    stroke.scope, stroke.scope_version = ts.id, SCOPE_VERSION
    created, staged = [], []
    # A mask-only key has no paint layer to mirror into other sets.
    from ..evaluation.raster_cache import for_texture_set
    scene = ts.id_data
    if universal and key == layer.stroke_key:
        for other in project.texture_sets:
            if other.id == ts.id:
                continue
            if sets is not None and other.id not in sets:
                continue   # the gesture never crossed this set: its neighbourhood is no permission
            if any(item.stroke_key == key for item in other.layers):
                permitted.append(other.id)   # already linked, and reached by this gesture
                continue
            # Link only sets the stroke marks: the disc may spill onto a neighbouring
            # object the pointer never crossed, so the raster, not the ray hit, decides,
            # and a set the bounds merely enclose must not gain a layer it re-rasters
            # for nothing on every later edit of the key.
            if not _intersects(other, lo, hi):
                continue
            n = int(other.resolution)
            raster = stroke_raster.rasters(other, [stroke], n)[0]
            if not stroke_raster.marks(raster):
                continue
            linked = stack.add(other, 'PAINT', layer.name)
            linked.stroke_key = key
            created.append(other)
            permitted.append(other.id)
            staged.append((other, n, raster))
    stroke.scope = ','.join(permitted)   # the last write: every field write bumps the revision the raster keys carry
    for other, n, raster in staged:
        # The set reads geometry only now that it renders strokes, so only now does
        # its shared cache carry the stamp the evaluation keys stroke rasters under:
        # hand it the raster that decided the link instead of building it again.
        stroke_raster.adopt(other, stroke, n, for_texture_set(scene, other), raster)
    project.stroke_serial += 1
    enable_channels(project, key)
    _wake(project, key, created)
    return stroke


def restore_records(project, ts, records, keys, added=None):
    """Add stroke snapshots (a material document's ``strokes``) under new keys and ids.

    ``keys`` maps each saved ``layer_key`` to the key the imported layers of ``ts``
    got; a record whose key no imported layer or mask renders is skipped. The
    strokes stay independent of the originals (fresh ids, revision 0). The ids
    added go into ``added`` as they are, so a failed import can take them back out.
    """
    from ..core.layer_stack import replay, restore
    from .surface_reach import unpack_anchors
    added = [] if added is None else added
    if not records:
        return added
    with replay(ts):
        for values in records:
            key = keys.get(values.get('layer_key', ''))
            if not key:
                continue
            samples = stroke_raster.unpack_samples(values.get('samples', ''))
            stroke = project.strokes.add()
            known = stroke.bl_rna.properties.keys()
            restore({k: v for k, v in values.items() if k in known and k not in ('id', 'revision', 'selected')}, stroke)
            stroke.id, stroke.layer_key = new_id(), key
            stroke.revision, stroke.selected = 0, False
            # Derived from the samples, not trusted from the document.
            stroke.sample_count = len(samples)
            stroke.bounds_min, stroke.bounds_max = stroke_raster.bounds(samples)
            if len(unpack_anchors(values.get('anchors', ''))) != len(samples):
                stroke.anchors = ''   # anchors that do not describe these samples seed by proximity
            # The document's scope named sets of the project that saved it: the copies
            # belong to the importing set alone, never to a set of similar name.
            stroke.scope, stroke.scope_version = ts.id, SCOPE_VERSION
            added.append(stroke.id)
    if added:
        project.stroke_serial += 1
        for key in dict.fromkeys(keys.values()):
            enable_channels(project, key)
            _wake(project, key, [ts])
    return added


def enable_channels(project, key):
    """Paint layers keep channels off until painted: strokes turn on the ones they write."""
    from ..core.layer_stack import channel_for
    wanted = {c for stroke in strokes_for(project, key) for c in stroke_channels(stroke)}
    for _, layer in linked_layers(project, key):
        for channel in wanted:
            prop = channel_for(layer, channel)
            if prop is not None and not prop.enabled:
                prop.enabled = True


STROKE_FIELDS = ('name', 'radius', 'hardness', 'opacity', 'flow', 'accumulate', 'alpha_image', 'alpha_rotation',
                 'alpha_angle', 'projection', 'plane_normal', 'depth_limit', 'blend', 'channels', 'base_color',
                 'roughness', 'metallic', 'height', 'ao', 'emission', 'opacity_value', 'normal', 'samples',
                 'sample_count', 'bounds_min', 'bounds_max', 'order', 'anchors', 'reach', 'scope', 'scope_version',
                 'payload_version', 'dab_payload', 'brush_graph_data')


def copy_key(project, key, ts=None, into=None):
    """A fresh key carrying copies of ``key``'s strokes: a duplicated layer or mask renders
    the same marks but its later strokes stay its own.

    With ``ts`` (the set whose layer is copied) only the strokes that set may render are
    copied, and the copies' scope names ``into`` (the set the copy lives in; ``ts`` itself
    for a duplicated layer) alone: a copy never widens a stroke's reach to sets the
    original was not permitted, nor keeps naming the set it was copied from."""
    fresh = new_id()
    into = into if into is not None else ts
    # Read every source first: adding to the collection may move the structs it holds.
    sources = [{field: getattr(source, field) for field in (*STROKE_FIELDS, 'revision')}
               for source in strokes_for(project, key, ts)]
    for values in sources:
        stroke = project.strokes.add()
        stroke.id, stroke.layer_key = new_id(), fresh
        for field, value in values.items():
            setattr(stroke, field, value)
        if into is not None and values.get('scope_version', 0):
            stroke.scope = into.id
    project.stroke_serial += 1
    return fresh


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
