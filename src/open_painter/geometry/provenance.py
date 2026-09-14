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


def record(ts, kind):
    scene = ts.id_data
    shared_bake = ('margin','ray_distance','cage_mode','cage_object','selected_to_active','samples',
                   'source_mode','device','separate_cage_distance','cage_extrusion')
    fields = {'NORMAL': shared_bake,
              'AO': (*shared_bake,'ao_scope'),
              'CURVATURE': ('curvature_angle','curvature_width','mean_curvature_distance'),
              'UV_BORDER': ('uv_border_width',),
              'THICKNESS': ('thickness_resolution','thickness_samples','thickness_max_distance'),
              'MEAN_CURVATURE': ('mean_curvature_distance',)}
    settings = {key: identity(getattr(ts.bake,key)) if key == 'cage_object' else getattr(ts.bake,key)
                for key in fields.get(kind, ())}
    if kind in {'NORMAL', 'AO'} and int(ts.bake.supersample) != 1:
        settings['supersample'] = int(ts.bake.supersample)
    if kind == 'AO' and ts.bake.ao_distance != 10.0:
        # Recorded only when it differs from the implicit distance of earlier
        # bakes, so maps baked before this setting existed are not marked stale.
        settings['ao_distance'] = ts.bake.ao_distance
    extra = {}
    if kind in {'NORMAL','AO'}:
        extra = dict(bake_algorithm=2, cage=object_record(ts.bake.cage_object),
                     source_bindings=[(identity(ref.object), identity(ref.target), object_record(ref.cage))
                                      for ref in ts.bake.high_objects])
    return json.dumps(dict(version=ALGORITHM_VERSION, kind=kind,
        resolution=int(ts.resolution), frame=(scene.frame_current, scene.frame_subframe),
        targets=[(object_record(ref.object), ref.slot_index) for ref in ts.assignments],
        sources=[object_record(ref.object) for ref in ts.bake.high_objects] if kind in {'NORMAL','AO'} else [],
        settings=settings, **extra), sort_keys=True, separators=(',', ':'))


def status(ts, prop):
    if not prop.image:
        return 'Missing'
    if not prop.provenance:
        return 'Unverified inputs'
    method = prop.image.get('open_painter_curvature_method')
    if prop.kind == 'CURVATURE' and (method not in (None, 'GEOMETRIC_CREASES_V2')
                                     or (method is None and 'BakedCurvature' in prop.image.name)):
        return 'Stale · crease-only map, rebake for smooth edges'
    return 'Ready' if prop.provenance == record(ts, prop.kind) else 'Stale · update bake'
