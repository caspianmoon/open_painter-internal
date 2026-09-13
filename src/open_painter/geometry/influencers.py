"""Resolve only authored references; never scan the scene for candidate parts."""


def objects(ts, field):
    sources = {ref.object for ref in ts.assignments if ref.object}
    selected = set(field.collection.all_objects) if field.collection else set()
    if field.object:
        selected.add(field.object)
    return sorted((obj for obj in selected if obj not in sources and obj.type == 'MESH'
                   and not obj.get('open_painter_geometry_effect')), key=lambda obj: obj.name_full)


def signature(ts, field):
    from .provenance import object_record
    import json
    return json.dumps(dict(kind=field.kind, object=object_record(field.object),
        objects=[object_record(obj) for obj in objects(ts, field)],
        bounds=tuple(field.bounds), direction=tuple(field.direction), distance=field.distance,
        attribute=field.attribute, resolution=field.resolution, update=field.update_serial), sort_keys=True)
