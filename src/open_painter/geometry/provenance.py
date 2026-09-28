"""Persistent bake input descriptions; no automatic rebaking on scene edits."""
import json
from ..evaluation.dependencies import mesh_signature, mesh_inputs, node_inputs, dependency_key

ALGORITHM_VERSION = 1


def identity(item):
    return (item.library.filepath if item.library else '', item.name_full) if item else None


def parameters(group, owner=None, legacy=False):
    """A modifier's settings. With its ``owner`` the objects it reads (a cutter, a target) are
    recorded with their relative placement and mesh, and a Geometry Nodes modifier with its
    group inputs; ``legacy`` keeps the form of records written before 0.9.7."""
    result = {}
    for field in group.bl_rna.properties:
        if field.identifier == 'rna_type' or field.is_readonly:
            continue
        value = getattr(group, field.identifier)
        if field.type in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'}:
            if isinstance(value, (set, frozenset)):
                # Enum flags (Decimate delimit, Boolean debug options, Data Transfer types) are
                # sets: unordered and not JSON serialisable; a sorted list records them stably.
                value = sorted(value)
            result[field.identifier] = tuple(value) if getattr(field, 'is_array', False) else value
        elif field.type == 'POINTER':
            result[field.identifier] = identity(value) if owner is None or legacy else dependency_key(owner, value, identity)
    if getattr(group, 'type', None) == 'NODES' and owner is not None and not legacy:
        inputs = node_inputs(group, lambda value: dependency_key(owner, value, identity))
        if inputs:
            result['inputs'] = inputs
    return result


def object_record(obj, transform='FULL'):
    """What a bake reads from an object. ``transform`` names how much of the world matrix
    matters: 'FULL' (position and orientation feed the map), 'SCALE' (only world sizes do:
    the scale part plus the mirroring sign), so moving or rotating the object leaves such maps
    current."""
    if obj is None:
        return None
    matrix = obj.matrix_world
    legacy = transform == 'LEGACY'
    if transform == 'SCALE':
        placement = dict(scale=[round(float(v), 6) for v in matrix.to_scale()],
                         mirrored=matrix.to_3x3().determinant() < 0)
    elif legacy:
        placement = [list(row) for row in matrix]   # exact floats, as records were written before 0.9.7
    else:
        placement = [[round(float(v), 6) + 0.0 for v in row] for row in matrix]   # a move back lands on the record (no -0.0)
    result = dict(identity=identity(obj), matrix=placement,
                  mesh=mesh_signature(obj.data) if obj.type == 'MESH' else None,
                  modifiers=[parameters(modifier, obj, legacy) for modifier in obj.modifiers])
    inputs = () if legacy else mesh_inputs(obj)
    if inputs:
        # Weights, creases and shape keys the modifiers read: recorded only when present, so
        # a plain mesh's record keeps its form and its maps stay current.
        result['inputs'] = inputs
    return result


# Maps that read where the object stands (occluders of the assembly, world axes); every other
# kind sees the surface alone, so only its world size takes part in its record.
PLACED_KINDS = {'AO', 'WORLD_NORMAL', 'POSITION', 'SCENE_FIELD'}


def transform_of(ts, kind):
    if kind in PLACED_KINDS:
        return 'FULL'
    if kind == 'NORMAL' and any(ref.object for ref in ts.bake.high_objects):
        return 'FULL'   # projected from high-poly sources placed around the target
    return 'SCALE'


def settings_of(ts, kind):
    """Bake settings that identify one map kind; cheap, no mesh access."""
    shared_bake = ('margin','ray_distance','cage_mode','cage_object','selected_to_active','samples',
                   'source_mode','device','separate_cage_distance','cage_extrusion')
    fields = {'NORMAL': shared_bake,
              'AO': (*shared_bake,'ao_scope'),
              'CURVATURE': ('mean_curvature_distance','margin','samples','device'),
              'CURVATURE_THIN': ('mean_curvature_distance','margin','samples','device'),
              'CURVATURE_WIDE': ('mean_curvature_distance','margin','samples','device'),
              'CURVATURE_XWIDE': ('mean_curvature_distance','margin','samples','device'),
              'UV_BORDER': ('uv_border_width',),
              'THICKNESS': ('thickness_resolution','thickness_samples','thickness_max_distance'),
              'MEAN_CURVATURE': ('mean_curvature_distance',),
              'BEVEL_NORMAL': ('bevel_radius','bevel_samples','margin','samples','device'),
              'BEVEL_MASK': ('bevel_radius','bevel_samples','margin','samples','device')}
    settings = {key: identity(getattr(ts.bake,key)) if key == 'cage_object' else getattr(ts.bake,key)
                for key in fields.get(kind, ())}
    if kind in {'NORMAL', 'AO', 'BEVEL_NORMAL', 'BEVEL_MASK', 'CURVATURE', 'CURVATURE_THIN',
                'CURVATURE_WIDE', 'CURVATURE_XWIDE'} and int(ts.bake.supersample) != 1:
        settings['supersample'] = int(ts.bake.supersample)
    if kind == 'AO' and ts.bake.ao_distance != 10.0:
        # Recorded only when it differs from the implicit distance of earlier
        # bakes, so maps baked before this setting existed are not marked stale.
        settings['ao_distance'] = ts.bake.ao_distance
    return settings


def corrections_signature(ts, kind):
    """Painted bake corrections (0.9.1) that shape this map; empty when none exist."""
    corrections = getattr(ts.bake, 'corrections', None)
    if corrections is None:
        return {}
    keys = ['offset', 'skew', 'direction']
    if kind == 'NORMAL':
        keys.append('ignore')
    if kind == 'AO':
        keys.extend(['ignore', 'ao_exclude'])
    result = {}
    for key in keys:
        image = getattr(corrections, key + '_image', None)
        if image is not None and image.get('open_painter_bake_correction_used'):
            result[key] = (image.name, image.get('open_painter_pixel_revision', 0))
    if 'ignore' in result:
        result['ignored_sources'] = sorted(identity(ref.object) for ref in ts.bake.high_objects
                                           if ref.object and getattr(ref, 'ignorable', False))
    # The settings that scale a painted raster at bake time, recorded only with that raster
    # so maps without the correction keep their records.
    if 'offset' in result:
        result['offset_range'] = round(float(corrections.offset_range), 6)
    if 'direction' in result:
        from ..baking.corrections import bake_settings
        result['direction_vector'] = list(bake_settings(ts)['direction_vector'])
    return result


def record(ts, kind, legacy=False):
    """The bake inputs of ``kind`` as a JSON string. ``legacy`` writes every transform in full,
    the form maps baked before placement-aware records carry; ``status`` accepts both."""
    scene = ts.id_data
    settings = settings_of(ts, kind)
    transform = 'LEGACY' if legacy else transform_of(ts, kind)
    extra = {}
    if kind in {'NORMAL','AO'}:
        extra = dict(bake_algorithm=2, cage=object_record(ts.bake.cage_object, transform),
                     source_bindings=[(identity(ref.object), identity(ref.target), object_record(ref.cage, transform))
                                      for ref in ts.bake.high_objects])
        corrections = corrections_signature(ts, kind)
        if corrections:
            extra['corrections'] = corrections
    if getattr(ts, 'udim_tile', 0):
        extra['udim'] = (ts.udim_tile, ts.udim_source_uv)
    return json.dumps(dict(version=ALGORITHM_VERSION, kind=kind,
        resolution=int(ts.resolution), frame=(scene.frame_current, scene.frame_subframe),
        targets=[(object_record(ref.object, transform), ref.slot_index) for ref in ts.assignments],
        sources=[object_record(ref.object, transform) for ref in ts.bake.high_objects] if kind in {'NORMAL','AO'} else [],
        settings=settings, **extra), sort_keys=True, separators=(',', ':'))


_status_cache = {}


def status(ts, prop):
    if not prop.image:
        return 'Missing'
    if not prop.provenance:
        return 'Unverified inputs'
    method = prop.image.get('open_painter_curvature_method')
    if prop.kind == 'CURVATURE' and (method not in (None, 'CYCLES_BEVEL')
                                     or (method is None and 'BakedCurvature' in prop.image.name)):
        return 'Stale · pre-Cycles map, rebake for the Bevel edge set'
    # The panel asks on every redraw; the record serialises mesh signatures and
    # modifier settings, so reuse it until geometry, settings or targets change.
    from ..evaluation.dependencies import epoch
    scene = ts.id_data
    slot = (ts.as_pointer(), prop.kind)
    token = (prop.provenance, ts.edit_serial, epoch(), int(ts.resolution), scene.frame_current,
             scene.frame_subframe, repr(settings_of(ts, prop.kind)), repr(corrections_signature(ts, prop.kind)),
             tuple((identity(ref.object), ref.slot_index) for ref in ts.assignments),
             tuple((identity(ref.object), identity(ref.target), identity(ref.cage)) for ref in ts.bake.high_objects))
    cached = _status_cache.get(slot)
    if cached is not None and cached[0] == token:
        return cached[1]
    current = prop.provenance == record(ts, prop.kind) or prop.provenance == record(ts, prop.kind, legacy=True)
    result = 'Ready' if current else 'Stale · update bake'
    if len(_status_cache) > 64:
        _status_cache.clear()
    _status_cache[slot] = (token, result)
    return result
