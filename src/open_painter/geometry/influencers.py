"""Resolve a scene field's influencers: explicit references, or an opt-in bounded scene scope."""
import json

RADIUS_PROPERTY = 'open_painter_field_radius'   # per-object override of the field distance
MAX_SCOPE = 256


def _candidate(obj, sources):
    return (obj.type == 'MESH' and obj not in sources
            and not obj.get('open_painter_geometry_effect')
            and not obj.get('open_painter_analysis_scratch')
            and not obj.get('open_painter_owned'))


def objects(ts, field):
    sources = {ref.object for ref in ts.assignments if ref.object}
    if getattr(field, 'scope', 'EXPLICIT') == 'SCENE':
        scene = ts.id_data
        excluded = set(field.exclude_collection.all_objects) if getattr(field, 'exclude_collection', None) else set()
        found = [obj for obj in scene.objects
                 if _candidate(obj, sources) and obj not in excluded and obj.visible_get()]
        limit = int(getattr(field, 'max_objects', 64))
        if len(found) > limit:
            raise ValueError(f'Scene scope found {len(found)} meshes; the limit is {limit}. '
                             'Exclude a collection or raise Max Objects.')
        return sorted(found, key=lambda obj: obj.name_full)
    selected = set(field.collection.all_objects) if field.collection else set()
    if field.object:
        selected.add(field.object)
    return sorted((obj for obj in selected if _candidate(obj, sources)), key=lambda obj: obj.name_full)


def radii(objs, default):
    """{object name: radius} honouring the per-object custom property."""
    return {obj.name_full: float(obj.get(RADIUS_PROPERTY, default)) for obj in objs}


def _token(obj):
    """A cheap identity for scene-scope members: pointer, transform and mesh epoch."""
    from ..evaluation.dependencies import _mesh_epochs
    return (obj.name_full, tuple(round(v, 6) for row in obj.matrix_world for v in row),
            _mesh_epochs.get(obj.data.as_pointer(), 0) if obj.data else 0,
            float(obj.get(RADIUS_PROPERTY, 0.0)))


def signature(ts, field):
    from .provenance import object_record
    scope = getattr(field, 'scope', 'EXPLICIT')
    try:
        members = objects(ts, field)
    except ValueError as error:
        members, scope = [], 'ERROR:' + str(error)
    return json.dumps(dict(kind=field.kind, scope=scope, object=object_record(field.object),
        objects=[_token(obj) if scope == 'SCENE' else object_record(obj) for obj in members],
        radii=sorted(radii(members, field.distance).items()),
        ground=object_record(getattr(field, 'ground_object', None)),
        bounds=tuple(field.bounds), direction=tuple(field.direction), distance=field.distance,
        attribute=field.attribute, resolution=field.resolution, update=field.update_serial), sort_keys=True)
