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
    """A cheap identity for scene-scope members: names, transform, mesh update epoch and
    radius. Never the mesh's content hash: the scope can hold every mesh of the scene and
    the signature is asked for on the depsgraph tick, so hashing the members (tens of
    millions of vertices on a large assembly, all of them again once every load, undo and
    redo has emptied the fingerprint cache) would stall the session. The epoch is unique for
    the session (``mesh_epoch``), so a redo never reads as the members an undo published; a
    member published untouched keeps its identity across a reopen, one edited in the
    publishing session re-analyses the field once after it. The field's own object keeps
    its full record (``object_record`` hashes that one mesh)."""
    from ..evaluation.dependencies import mesh_epoch
    mesh = obj.data if obj.type == 'MESH' else None
    return (obj.name_full, mesh.name_full if mesh is not None else None,
            tuple(round(v, 6) for row in obj.matrix_world for v in row),
            mesh_epoch(mesh) if mesh is not None else 0,
            float(obj.get(RADIUS_PROPERTY, 0.0)))


def _content(ts, kind, name):
    """What a VERTEX_GROUP / ATTRIBUTE field reads off the assigned meshes: the weights or
    values (which the mesh content hash leaves out, so painting them moved nothing here),
    the attribute's shape and the group names. In digest form (the assigned meshes are few,
    and ``mesh_masks`` caches each digest per reported update), not the session's epoch that
    stands in for a scope member's mesh in ``_token``: the signature persists as the field's
    provenance, and a painted weight must move it, not a reopen."""
    from ..evaluation.mesh_masks import _attribute_token, _content_token
    from ..evaluation.resource_keys import stable_keys
    targets = dict.fromkeys(ref.object for ref in ts.assignments if ref.object and ref.object.type == 'MESH')
    with stable_keys():
        return [(obj.name_full, _content_token(obj, kind, name), _attribute_token(obj, name),
                 tuple(g.name for g in obj.vertex_groups)) for obj in targets]


def signature(ts, field):
    from .provenance import object_record
    scope = getattr(field, 'scope', 'EXPLICIT')
    try:
        members = objects(ts, field)
    except ValueError as error:
        members, scope = [], 'ERROR:' + str(error)
    entry = dict(kind=field.kind, scope=scope, object=object_record(field.object),
        objects=[_token(obj) if scope == 'SCENE' else object_record(obj) for obj in members],
        radii=sorted(radii(members, field.distance).items()),
        ground=object_record(getattr(field, 'ground_object', None)),
        bounds=tuple(field.bounds), direction=tuple(field.direction), distance=field.distance,
        attribute=field.attribute, resolution=field.resolution, update=field.update_serial)
    if field.kind in {'VERTEX_GROUP', 'ATTRIBUTE'}:   # only these read painted values: other kinds keep their provenance
        entry['content'] = _content(ts, field.kind, field.attribute)
    return json.dumps(entry, sort_keys=True)
