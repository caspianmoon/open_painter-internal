"""Persistent bake input descriptions; no automatic rebaking on scene edits."""
import json
from ..evaluation.dependencies import mesh_signature

ALGORITHM_VERSION = 1


def identity(item):
    return (item.library.filepath if item.library else '', item.name_full) if item else None


def parameters(group):
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
            result[field.identifier] = identity(value)
    return result


def object_record(obj):
    if obj is None:
        return None
    return dict(identity=identity(obj), matrix=[list(row) for row in obj.matrix_world],
                mesh=mesh_signature(obj.data) if obj.type == 'MESH' else None,
                modifiers=[parameters(modifier) for modifier in obj.modifiers])


def settings_of(ts, kind):
    """Bake settings that identify one map kind; cheap, no mesh access."""
    shared_bake = ('margin','ray_distance','cage_mode','cage_object','selected_to_active','samples',
                   'source_mode','device','separate_cage_distance','cage_extrusion')
    fields = {'NORMAL': shared_bake,
              'AO': (*shared_bake,'ao_scope'),
              'CURVATURE': ('curvature_angle','curvature_width','mean_curvature_distance'),
              'CURVATURE_THIN': ('curvature_angle','curvature_width','mean_curvature_distance'),
              'CURVATURE_WIDE': ('curvature_angle','curvature_width','mean_curvature_distance'),
              'CURVATURE_XWIDE': ('curvature_angle','curvature_width','mean_curvature_distance'),
              'UV_BORDER': ('uv_border_width',),
              'THICKNESS': ('thickness_resolution','thickness_samples','thickness_max_distance'),
              'MEAN_CURVATURE': ('mean_curvature_distance',),
              'BEVEL_NORMAL': ('bevel_radius','bevel_samples','margin','samples','device'),
              'BEVEL_MASK': ('bevel_radius','bevel_samples','margin','samples','device')}
    settings = {key: identity(getattr(ts.bake,key)) if key == 'cage_object' else getattr(ts.bake,key)
                for key in fields.get(kind, ())}
    if kind in {'NORMAL', 'AO', 'BEVEL_NORMAL', 'BEVEL_MASK'} and int(ts.bake.supersample) != 1:
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
    return result


def record(ts, kind):
    scene = ts.id_data
    settings = settings_of(ts, kind)
    extra = {}
    if kind in {'NORMAL','AO'}:
        extra = dict(bake_algorithm=2, cage=object_record(ts.bake.cage_object),
                     source_bindings=[(identity(ref.object), identity(ref.target), object_record(ref.cage))
                                      for ref in ts.bake.high_objects])
        corrections = corrections_signature(ts, kind)
        if corrections:
            extra['corrections'] = corrections
    return json.dumps(dict(version=ALGORITHM_VERSION, kind=kind,
        resolution=int(ts.resolution), frame=(scene.frame_current, scene.frame_subframe),
        targets=[(object_record(ref.object), ref.slot_index) for ref in ts.assignments],
        sources=[object_record(ref.object) for ref in ts.bake.high_objects] if kind in {'NORMAL','AO'} else [],
        settings=settings, **extra), sort_keys=True, separators=(',', ':'))


_status_cache = {}


def status(ts, prop):
    if not prop.image:
        return 'Missing'
    if not prop.provenance:
        return 'Unverified inputs'
    method = prop.image.get('open_painter_curvature_method')
    if prop.kind == 'CURVATURE' and (method not in (None, 'GEOMETRIC_CREASES_V2')
                                     or (method is None and 'BakedCurvature' in prop.image.name)):
        return 'Stale · crease-only map, rebake for smooth edges'
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
    result = 'Ready' if prop.provenance == record(ts, prop.kind) else 'Stale · update bake'
    if len(_status_cache) > 64:
        _status_cache.clear()
    _status_cache[slot] = (token, result)
    return result
