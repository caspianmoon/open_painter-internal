"""Mesh-aware masks (0.9.2): exact UV-space coverage from Blender's own scene structure.

A MESH mask selects faces (or weights them) by object, collection, material,
vertex group, face set, face selection, UV island, normal direction or a
named attribute of the assigned meshes, rasterized through the render UVs at
the set resolution. ``signature`` names every input the coverage depends on
— including collection membership and material slots — so adding an object
to the collection changes the signature and the mask re-evaluates; the
scheduler's depsgraph tracking notices such changes (``dependencies``).
"""
import math

import bpy
import numpy as np

CACHE = 'mesh_mask'
POOLS = {'object': 'objects', 'collection': 'collections', 'material': 'materials'}


def resolve(selector, name):
    """The pointed datablock, or the one named like it; works on RNA and captured snapshots."""
    value = getattr(selector, name)
    if value is not None:
        return value
    stored = getattr(selector, name + '_name', '')
    return getattr(bpy.data, POOLS[name]).get(stored) if stored else None


def _collection_objects(collection, include_children):
    if collection is None:
        return ()
    return tuple(collection.all_objects if include_children else collection.objects)


def _selection_token(obj):
    mesh = obj.data
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
    flags = np.empty(len(mesh.polygons), bool)
    mesh.polygons.foreach_get('select', flags)
    return int(flags.sum()), int(np.flatnonzero(flags)[:64].sum()) if flags.any() else 0, int(abs(hash(flags.tobytes())) % (1 << 31))


def _attribute_token(obj, name):
    attribute = obj.data.attributes.get(name)
    if attribute is None:
        return None
    return (attribute.domain, attribute.data_type, len(attribute.data))


def signature(ts, mask):
    """Repr-stable identity of everything a mesh mask's coverage depends on."""
    from .dependencies import mesh_signature
    selector = mask.mesh
    kind = selector.kind
    targets = tuple(dict.fromkeys(ref.object for ref in ts.assignments if ref.object and ref.object.type == 'MESH'))
    parts = [kind, tuple(obj.name for obj in targets)]
    if kind == 'OBJECT':
        obj = resolve(selector, 'object')
        parts.append(obj.name if obj else None)
    elif kind == 'COLLECTION':
        collection = resolve(selector, 'collection')
        members = _collection_objects(collection, selector.include_children)
        parts.append((collection.name if collection else None, tuple(sorted(o.name for o in members))))
    elif kind == 'MATERIAL':
        material = resolve(selector, 'material')
        parts.append((material.name if material else None,
                      tuple((obj.name, tuple(slot.material.name if slot.material else None for slot in obj.material_slots))
                            for obj in targets),
                      tuple((ref.object_name, ref.slot_index, ref.original_material.name if ref.original_material else None)
                            for ref in ts.assignments)))
    elif kind in {'VERTEX_GROUP', 'ATTRIBUTE', 'FACE_SET'}:
        parts.append((selector.attribute, selector.face_set,
                      tuple((obj.name, mesh_signature(obj.data),
                             _attribute_token(obj, selector.attribute if kind != 'FACE_SET' else '.sculpt_face_set'),
                             tuple(g.name for g in obj.vertex_groups)) for obj in targets)))
    elif kind == 'FACE_SELECTION':
        parts.append(tuple((obj.name, _selection_token(obj)) for obj in targets))
    elif kind == 'UV_ISLAND':
        obj = resolve(selector, 'object')
        parts.append((obj.name if obj else None, selector.island,
                      tuple((o.name, mesh_signature(o.data), _selection_token(o) if selector.island < 0 else None)
                            for o in targets)))
    elif kind == 'NORMAL_DIRECTION':
        parts.append((tuple(round(float(v), 6) for v in selector.direction), round(float(selector.angle), 6),
                      round(float(selector.softness), 6),
                      tuple((obj.name, mesh_signature(obj.data), tuple(round(v, 6) for row in obj.matrix_world for v in row))
                            for obj in targets)))
    return tuple(parts)


def _interpolate(weights, corner_values):
    return np.einsum('nk,nk->n', weights, corner_values)


def _point_values(target, values):
    """Per-vertex values → per-triangle-corner values for interpolation."""
    return values[target['tri_vertices']]


def _face_values(target, values):
    return values[target['tri_polygons']]


def _attribute_values(obj, mesh, name):
    """(domain, per-element float values) of a named attribute on the evaluated mesh, or None."""
    attribute = mesh.attributes.get(name)
    if attribute is None:
        return None
    count = len(attribute.data)
    if attribute.data_type in {'FLOAT', 'INT', 'INT8', 'BOOLEAN'}:
        values = np.empty(count, np.float32 if attribute.data_type == 'FLOAT' else np.int64)
        attribute.data.foreach_get('value', values)
        values = values.astype(np.float32)
    elif attribute.data_type in {'FLOAT_VECTOR', 'FLOAT_COLOR', 'BYTE_COLOR', 'FLOAT2'}:
        width = {'FLOAT_VECTOR': 3, 'FLOAT_COLOR': 4, 'BYTE_COLOR': 4, 'FLOAT2': 2}[attribute.data_type]
        raw = np.empty(count * width, np.float32)
        attribute.data.foreach_get('vector' if width in (2, 3) else 'color', raw)
        raw = raw.reshape(count, width)
        values = np.linalg.norm(raw[:, :3], axis=1) if width != 4 else raw[:, :3].mean(axis=1)
    else:
        return None
    return attribute.domain, values.astype(np.float32)


def _triangle_coverage(ts, mask, target):
    """(per-corner values (T, 3) | per-triangle values (T,)), for one evaluated target."""
    selector = mask.mesh
    kind = selector.kind
    obj, mesh = target['object'], target['mesh']
    tris = len(target['tri_loops'])
    if kind == 'OBJECT':
        chosen = resolve(selector, 'object')
        return np.full(tris, 1.0 if chosen is not None and chosen == obj else 0.0, np.float32)
    if kind == 'COLLECTION':
        members = _collection_objects(resolve(selector, 'collection'), selector.include_children)
        return np.full(tris, 1.0 if obj in members else 0.0, np.float32)
    if kind == 'MATERIAL':
        # The slot's material now, or the one it had before this set took the
        # slot over (the imported material ID artists think in).
        material = resolve(selector, 'material')
        if material is None:
            return np.zeros(tris, np.float32)
        slots = {index for index, slot in enumerate(obj.material_slots) if slot.material == material}
        slots |= {ref.slot_index for ref in ts.assignments if ref.object == obj and ref.original_material == material}
        return np.isin(target['tri_material'], list(slots)).astype(np.float32)
    if kind == 'VERTEX_GROUP':
        group = obj.vertex_groups.get(selector.attribute)
        if group is None:
            return np.zeros(tris, np.float32)
        weights = np.zeros(len(mesh.vertices), np.float32)
        # Evaluated meshes keep the deform layer; read it per vertex.
        for vertex in mesh.vertices:
            for entry in vertex.groups:
                if entry.group == group.index:
                    weights[vertex.index] = entry.weight
                    break
        return _point_values(target, weights)
    if kind == 'FACE_SET':
        found = _attribute_values(obj, mesh, '.sculpt_face_set')
        if found is None or found[0] != 'FACE':
            return np.zeros(tris, np.float32)
        return _face_values(target, (found[1] == float(selector.face_set)).astype(np.float32))
    if kind == 'FACE_SELECTION':
        from ..baking.maps.selection import polygon_flags
        try:
            flags = polygon_flags(target)
        except ValueError:
            return np.zeros(tris, np.float32)
        return _face_values(target, flags)
    if kind == 'UV_ISLAND':
        from ..baking.maps.utility import _islands
        chosen = resolve(selector, 'object')
        if chosen is not None and chosen != obj:
            return np.zeros(tris, np.float32)
        labels, _ = _islands(target)
        if selector.island >= 0:
            roots = sorted({int(r) for r in labels if r >= 0})
            if selector.island >= len(roots):
                return np.zeros(tris, np.float32)
            wanted = {roots[selector.island]}
        else:
            from ..baking.maps.selection import polygon_flags
            try:
                flags = polygon_flags(target)
            except ValueError:
                return np.zeros(tris, np.float32)
            wanted = {int(r) for r in labels[flags > .5] if r >= 0}
        return _face_values(target, np.isin(labels, list(wanted)).astype(np.float32))
    if kind == 'NORMAL_DIRECTION':
        direction = np.array(tuple(selector.direction), np.float32)
        direction = direction / max(np.linalg.norm(direction), 1e-12)
        corners = target['world_normals'][target['tri_loops']]
        cosine = corners @ direction
        angle = np.degrees(np.arccos(np.clip(cosine, -1, 1)))
        inner = float(math.degrees(selector.angle))
        soft = max(float(math.degrees(selector.softness)), 1e-6)
        t = np.clip((angle - inner) / soft, 0, 1)
        return (1 - t * t * (3 - 2 * t)).astype(np.float32)
    if kind == 'ATTRIBUTE':
        found = _attribute_values(obj, mesh, selector.attribute)
        if found is None:
            return np.zeros(tris, np.float32)
        domain, values = found
        if domain == 'POINT':
            return _point_values(target, values)
        if domain == 'FACE':
            return _face_values(target, values)
        if domain == 'CORNER':
            return values[target['tri_loops']]
        return np.zeros(tris, np.float32)
    return np.zeros(tris, np.float32)


def raster(ts, mask, n):
    """Full (n, n) coverage of a mesh mask at the set's UV space."""
    from ..geometry.snapshots import targets
    from ..geometry.uv_rasterizer import rasterize
    result = np.zeros((n, n), np.float32)
    with targets(ts) as meshes:
        for target in meshes:
            values = _triangle_coverage(ts, mask, target)
            if not np.any(values):
                continue
            rows, cols, tri, weights = rasterize(target['uv'][target['tri_loops']], n)
            if not len(rows):
                continue
            if values.ndim == 2:
                result[rows, cols] = _interpolate(weights, values[tri])
            else:
                result[rows, cols] = values[tri]
    return result


def evaluate(ts, mask, n, y0, y1, geometry_cache):
    """Rows [y0, y1) of the mask, cached per signature in the set's raster cache."""
    key = (CACHE, ts.id, mask.id, signature(ts, mask), n)
    store = geometry_cache if geometry_cache is not None else {}
    if key in store:
        return store[key][y0:y1]
    values = raster(ts, mask, n)
    try:
        store[key] = values
    except TypeError:
        pass
    return values[y0:y1]
