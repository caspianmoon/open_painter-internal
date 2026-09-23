"""Mesh-aware masks (0.9.2): exact UV-space coverage from Blender's own scene structure.

A MESH mask selects faces (or weights them) by object, collection, material,
vertex group, face set, face selection, UV island, normal direction or a
named attribute of the assigned meshes, rasterized through the render UVs at
the set resolution. ``signature`` names every input the coverage depends on
— including collection membership and material slots — so adding an object
to the collection changes the signature and the mask re-evaluates; the
scheduler's depsgraph tracking notices such changes (``dependencies``).
"""
import hashlib
import math
import zlib

import bpy
import numpy as np

CACHE = 'mesh_mask'
REGION_SAMPLING_VERSION = 'region-gutters-v1'
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


def _selection_flags(obj):
    """Face select flags of the object's base mesh, in Edit Mode straight from the
    edit BMesh: flushing it (``update_from_editmode``) tags the mesh as changed, and
    the scheduler would re-hash the whole mesh and flush again on every tick for as
    long as the artist stays in Edit Mode."""
    mesh = obj.data
    if obj.mode == 'EDIT' and mesh.is_editmode:
        import bmesh
        faces = bmesh.from_edit_mesh(mesh).faces
        return np.fromiter((face.select for face in faces), bool, len(faces))
    flags = np.empty(len(mesh.polygons), bool)
    mesh.polygons.foreach_get('select', flags)
    return flags


def _base_face_count(obj):
    """Face count of the base mesh, from the same source ``_selection_flags`` reads: in
    Edit Mode the edit BMesh (``mesh.polygons`` lags behind unflushed edits). Only the
    count: ``warnings`` runs on every panel redraw, and materialising the flags there
    walked every BMesh face through Python (~0.5 s on a 1M-face mesh)."""
    mesh = obj.data
    if obj.mode == 'EDIT' and mesh.is_editmode:
        import bmesh
        return len(bmesh.from_edit_mesh(mesh).faces)
    return len(mesh.polygons)


def _selection_token(obj):
    flags = _selection_flags(obj)
    # A persisted provenance compares this token across sessions: no Python hash().
    return int(flags.sum()), int(np.flatnonzero(flags)[:64].sum()) if flags.any() else 0, zlib.crc32(flags.tobytes())


def _selected_faces(target):
    """One 0/1 float per evaluated polygon (the bake's ``polygon_flags`` contract),
    or None when the modifier stack changes the face count (``warnings`` reports it)."""
    flags = _selection_flags(target['object'])
    if len(flags) != len(target['mesh'].polygons):
        return None
    return flags.astype(np.float32)


# Modifier kinds that only move vertices or edit non-topology data (weights, normals,
# UVs): a face selection maps through them by index. Any other kind may change the
# evaluated face count.
DEFORMING = frozenset({
    'ARMATURE', 'CAST', 'CLOTH', 'COLLISION', 'CORRECTIVE_SMOOTH', 'CURVE', 'DATA_TRANSFER',
    'DISPLACE', 'DYNAMIC_PAINT', 'HOOK', 'LAPLACIANDEFORM', 'LAPLACIANSMOOTH', 'LATTICE',
    'MESH_CACHE', 'MESH_DEFORM', 'NORMAL_EDIT', 'PARTICLE_SYSTEM', 'SHRINKWRAP',
    'SIMPLE_DEFORM', 'SMOOTH', 'SOFT_BODY', 'SURFACE', 'SURFACE_DEFORM', 'UV_PROJECT',
    'UV_WARP', 'VERTEX_WEIGHT_EDIT', 'VERTEX_WEIGHT_MIX', 'VERTEX_WEIGHT_PROXIMITY',
    'VOLUME_DISPLACE', 'WARP', 'WAVE', 'WEIGHTED_NORMAL',
})


def _evaluated_face_count(obj, depsgraph, base):
    """Face count of the evaluated mesh as the raster's ``to_mesh`` will see it. In Edit
    Mode a stack that only deforms evaluates to a BMesh wrapper whose RNA arrays stay
    empty until something materialises it: read as 0 faces, every such stack would be
    named. ``to_mesh`` materialises the wrapper in place (the copy itself is dropped),
    and RNA reports the real count until the next evaluation."""
    evaluated = obj.evaluated_get(depsgraph)
    count = len(evaluated.data.polygons)
    if count == 0 and base and obj.mode == 'EDIT':
        evaluated.to_mesh(depsgraph=depsgraph)
        try:
            count = len(evaluated.data.polygons)
        finally:
            evaluated.to_mesh_clear()
    return count


def _unmappable_modifier(obj, depsgraph):
    """The modifier a face selection cannot be mapped through, or None when the evaluated
    mesh keeps the base face count. The stack is not evaluated one modifier at a time
    (a depsgraph per candidate): the first shown modifier that can change the face
    count is named, the last shown one when every kind only deforms."""
    base = _base_face_count(obj)
    if _evaluated_face_count(obj, depsgraph, base) == base:
        return None
    # The viewport depsgraph (``depsgraph_for``) runs the viewport stack; in Edit Mode
    # only the modifiers shown there.
    shown = [modifier for modifier in obj.modifiers
             if modifier.show_viewport and (obj.mode != 'EDIT' or modifier.show_in_editmode)]
    if not shown:
        return None
    return next((modifier for modifier in shown if modifier.type not in DEFORMING), shown[-1])


def warnings(ts):
    """One entry per selection-driven mask and target whose modifier stack changes the
    face count: such a mask reads as empty (``_selected_faces``), which an artist cannot
    tell apart from an empty selection without a report. Cheap when no mask reads the
    selection; otherwise one evaluated face count per target, through the same depsgraph
    the raster reads."""
    assigned = tuple(dict.fromkeys(ref.object for ref in getattr(ts, 'assignments', None) or ()
                                   if ref.object and ref.object.type == 'MESH'))
    if not assigned:
        return []
    wanted = []
    for layer in ts.layers:
        for mask in layer.masks:
            if not mask.enabled or mask.source != 'MESH':
                continue
            selector = mask.mesh
            if selector.kind == 'FACE_SELECTION':
                wanted.append((layer, assigned))
            elif selector.kind == 'UV_ISLAND' and selector.island < 0:
                # Islands by selection read the chosen object's selection alone.
                chosen = resolve(selector, 'object')
                wanted.append((layer, assigned if chosen is None else tuple(o for o in assigned if o == chosen)))
    if not wanted:
        return []
    from ..geometry.snapshots import depsgraph_for
    scene = getattr(ts, 'id_data', None)
    if not isinstance(scene, bpy.types.Scene):
        scene = bpy.context.scene
    try:
        depsgraph = depsgraph_for(scene, assigned)
    except ValueError:
        return []   # no view layer evaluates a target: the raster reports that itself
    unmappable = {obj: _unmappable_modifier(obj, depsgraph) for obj in assigned}
    result = []
    for layer, objects in wanted:
        for obj in objects:
            modifier = unmappable[obj]
            if modifier is None:
                continue
            # Several targets may carry a same-named modifier: qualify it then.
            name = modifier.name if len(assigned) == 1 else f'{obj.name}.{modifier.name}'
            message = f'{layer.name}: face selection cannot be mapped through {name} (face count changed)'
            if message not in result:
                result.append(message)
    return result


_digests = {}   # (mesh pointer, kind, name) -> (mesh epoch, digest)
_weights = {}   # (mesh pointer, group name) -> (mesh epoch, modifier signature, evaluated weights)


def clear():
    """With the mesh epochs (``dependencies.clear``): a reopened file or an undo step
    counts from zero again, so an epoch-tagged digest (or weight array) would answer
    for other values."""
    _digests.clear()
    _weights.clear()
    from ..geometry import raster_cache
    raster_cache.clear()


def prune(meshes):
    for store in (_digests, _weights):
        for key in tuple(store):
            if key[0] not in meshes:
                store.pop(key, None)


def _content_token(obj, kind, name):
    """The painted values a selector reads, as the session's mesh epoch (cheap, per
    process) or, for a persisted identity, a digest of the values themselves: a
    cached layer's provenance must still compare equal after the file is reopened.
    Weights of a mesh above ``dependencies.WEIGHT_HASH_BOUND`` are never digested (a
    per-vertex loop, seconds on a million vertices after every weight stroke): their
    session-salted epoch token stands in, so such a provenance re-evaluates once after
    a reopen instead of stalling the session."""
    from .dependencies import mesh_epoch, epoch_token, group_weights, weights_hashed
    from .resource_keys import stable
    mesh = obj.data
    epoch = mesh_epoch(mesh)
    if not stable():
        return epoch
    if kind == 'VERTEX_GROUP' and not weights_hashed(mesh):
        return epoch_token(mesh)
    key = (mesh.as_pointer(), kind, name)
    cached = _digests.get(key)
    if cached is not None and cached[0] == epoch:
        return cached[1]
    digest = hashlib.sha256()
    if kind == 'VERTEX_GROUP':
        weights = group_weights(obj, mesh, name)
        if weights is not None:
            digest.update(weights.tobytes())
    else:
        found = _attribute_values(obj, mesh, '.sculpt_face_set' if kind == 'FACE_SET' else name)
        if found is not None:
            digest.update(found[0].encode())
            digest.update(np.ascontiguousarray(found[1]).tobytes())
    token = digest.hexdigest()[:16]
    _digests[key] = (epoch, token)
    return token


def _attribute_token(obj, name):
    attribute = obj.data.attributes.get(name)
    if attribute is None:
        return None
    return (attribute.domain, attribute.data_type, len(attribute.data))


def signature(ts, mask):
    """Repr-stable identity of everything a mesh mask's coverage depends on."""
    from .dependencies import mesh_signature, modifier_signature
    selector = mask.mesh
    kind = selector.kind
    targets = tuple(dict.fromkeys(ref.object for ref in ts.assignments if ref.object and ref.object.type == 'MESH'))
    # Every kind rasterizes through the evaluated render UVs and topology: a re-unwrap
    # or a modifier edit re-identifies the raster even when the selection did not move.
    parts = [kind, tuple((obj.name, mesh_signature(obj.data), modifier_signature(obj)) for obj in targets)]
    if kind == 'OBJECT':
        obj = resolve(selector, 'object')
        parts.append(obj.name if obj else None)
    elif kind == 'REGION':
        obj = resolve(selector, 'object')
        parts.append((obj.name if obj else None, selector.region_id, selector.region_faces,
                      selector.region_topology))
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
        # Weights, attribute values and face sets are not part of the mesh signature (positions,
        # topology, UVs); their content token (epoch or digest) stands in.
        parts.append((selector.attribute, selector.face_set,
                      tuple((_content_token(obj, kind, selector.attribute),
                             _attribute_token(obj, selector.attribute if kind != 'FACE_SET' else '.sculpt_face_set'),
                             tuple(g.name for g in obj.vertex_groups)) for obj in targets)))
    elif kind == 'FACE_SELECTION':
        parts.append(tuple(_selection_token(obj) for obj in targets))
    elif kind == 'UV_ISLAND':
        obj = resolve(selector, 'object')
        parts.append((obj.name if obj else None, selector.island,
                      tuple(_selection_token(o) for o in targets) if selector.island < 0 else None))
    elif kind == 'NORMAL_DIRECTION':
        parts.append((tuple(round(float(v), 6) for v in selector.direction), round(float(selector.angle), 6),
                      round(float(selector.softness), 6), tuple(_orientation(obj) for obj in targets)))
    return tuple(parts)


def _orientation(obj):
    """What of the object's placement its world normals read: the normal matrix (the inverse
    transpose of the 3x3 part, as ``snapshots`` applies it) scaled to unit length. Rotation,
    mirroring, a non-uniform scale and shear all redirect the normals; a translation or a
    uniform scale does not, and keying the full world matrix re-rastered the mask (and
    re-keyed the set) on every drag of the asset."""
    matrix = np.asarray(obj.matrix_world.to_3x3().inverted_safe().transposed(), np.float64)
    length = float(np.linalg.norm(matrix))
    if length > 0.:
        matrix = matrix / length
    return tuple(round(float(v), 6) + 0.0 for v in matrix.ravel())   # + 0.0: no -0.0 vs 0.0 split


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


def _evaluated_weights(obj, mesh, name):
    """The group's weights on the evaluated mesh (it keeps the deform layer), read per
    vertex once per mesh update and modifier stack: every strip, resolution and mask
    that reads the group within one geometry state reuses the array rather than
    looping over the vertices again (seconds on a large asset)."""
    from .dependencies import mesh_epoch, modifier_signature, group_weights
    if name not in obj.vertex_groups:
        return None
    key = (obj.data.as_pointer(), name)
    # The evaluated weights also follow the stack (a Vertex Weight modifier, a Subdivision
    # level), which a mesh update does not report: its signature is part of the stamp.
    stamp = (mesh_epoch(obj.data), modifier_signature(obj))
    cached = _weights.get(key)
    if cached is not None and cached[:2] == stamp and len(cached[2]) == len(mesh.vertices):
        return cached[2]
    weights = group_weights(obj, mesh, name)
    _weights[key] = (*stamp, weights)
    return weights


def _triangle_coverage(ts, mask, target):
    """(per-corner values (T, 3) | per-triangle values (T,)), for one evaluated target."""
    selector = mask.mesh
    kind = selector.kind
    obj, mesh = target['object'], target['mesh']
    tris = len(target['tri_loops'])
    if kind == 'OBJECT':
        chosen = resolve(selector, 'object')
        return np.full(tris, 1.0 if chosen is not None and chosen == obj else 0.0, np.float32)
    if kind == 'REGION':
        chosen = resolve(selector, 'object')
        if chosen is None or chosen != obj:
            return np.zeros(tris, np.float32)
        from ..authoring.regions import face_indices
        faces = face_indices(selector, mesh)
        return np.isin(target['tri_polygons'], faces).astype(np.float32)
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
        weights = _evaluated_weights(obj, mesh, selector.attribute)
        if weights is None:
            return np.zeros(tris, np.float32)
        return _point_values(target, weights)
    if kind == 'FACE_SET':
        found = _attribute_values(obj, mesh, '.sculpt_face_set')
        if found is None or found[0] != 'FACE':
            return np.zeros(tris, np.float32)
        return _face_values(target, (found[1] == float(selector.face_set)).astype(np.float32))
    if kind == 'FACE_SELECTION':
        flags = _selected_faces(target)
        if flags is None:
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
            flags = _selected_faces(target)
            if flags is None:
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
    from ..geometry.raster_cache import sample
    result = np.zeros((n, n), np.float32)
    with targets(ts) as meshes:
        for target in meshes:
            values = _triangle_coverage(ts, mask, target)
            if not np.any(values):
                continue
            rows, cols, tri, weights = sample(target['uv'][target['tri_loops']], n)
            if not len(rows):
                continue
            if values.ndim == 2:
                result[rows, cols] = _interpolate(weights, values[tri])
            else:
                result[rows, cols] = values[tri]
    return result


def sampling_signature(ts, mask):
    """Version every consumer of derived REGION pixels, including cached prefixes.

    ``signature`` still identifies exact authored membership. Sampling changes
    belong in this identity, shared by CPU stacks and shader dependency trees.
    """
    membership = signature(ts, mask)
    return (REGION_SAMPLING_VERSION, membership) if mask.mesh.kind == 'REGION' else membership


def evaluate(ts, mask, n, y0, y1, geometry_cache, cache=None):
    """Rows [y0, y1) of the mask, cached per signature in the set's raster cache."""
    region = mask.mesh.kind == 'REGION'
    key = (CACHE, ts.id, mask.id, sampling_signature(ts, mask), n)
    store = geometry_cache if geometry_cache is not None else {}
    if cache is not None and key in cache:
        return cache[key][y0:y1]
    if key in store:
        values = store[key]
    else:
        values = raster(ts, mask, n)
        if region:
            from .uv_gutters import padded
            # Linear filtering at a UV chart edge must not blend a selected
            # surface with the atlas's zero background. Only unowned texels in
            # this disposable buffer change; selected and excluded face texels
            # retain their exact membership, as do persisted authoring inputs.
            values = padded(ts, values)
        try:
            store[key] = values
        except TypeError:
            pass
    # Every strip of a job reads the whole raster; the shared allowance may
    # evict it between strips, so the job keeps its own reference.
    if cache is not None:
        cache[key] = values
    return values[y0:y1]
