"""Read-only, assignment-aware geometric region discovery.

Region names are opaque identities, never semantic labels. Frozen membership is
stored on an ordinary MESH mask, not on the artist's geometry. Topology changes
invalidate that membership instead of silently selecting different faces.
"""
import hashlib
import json

from ..utils.digest import sha256

MAX_REGIONS = 128
MAX_COMPONENTS = 48
MAX_QUERIES = 32


class RegionBindings(dict):
    """Object bindings plus a request-local, read-only query result registry."""

    def __init__(self, objects, registry):
        super().__init__(objects)
        self.region_registry = registry


def bindings(objects, registry):
    return RegionBindings(objects, registry)


def _public(record):
    return {key: value for key, value in record.items() if not key.startswith('_')}


def _fingerprint(target):
    """Query coordinates must not silently follow changed geometry before commit."""
    import numpy as np
    digest = hashlib.sha256(target['_region_topology'].encode())
    for values in (target['positions'], target['uv'], np.asarray(target['object'].matrix_world)):
        digest.update(values.tobytes())
    return digest.hexdigest()


def _query_spec(spec):
    import numpy as np
    from .schema import AuthoringError
    required = {'object_id', 'slot_index', 'space', 'bounds_min', 'bounds_max', 'selection'}
    optional = {'normal_direction', 'min_dot', 'max_dot'}
    if not isinstance(spec, dict) or not required <= spec.keys() or set(spec) - required - optional:
        raise AuthoringError('A region query requires object, slot, coordinate space, bounds and selection method')
    if not isinstance(spec['object_id'], str) or type(spec['slot_index']) is not int or not 0 <= spec['slot_index'] <= 255:
        raise AuthoringError('Region object and material-slot identifiers are invalid')
    if spec['space'] not in {'WORLD', 'OBJECT', 'ASSIGNMENT_NORMALIZED'} or spec['selection'] not in {'CENTROID', 'INTERSECTION'}:
        raise AuthoringError('Unsupported region coordinate space or selection method')
    def vector(value, name, limit):
        if not isinstance(value, (list, tuple)) or len(value) != 3 or any(type(v) not in (float, int) for v in value):
            raise AuthoringError(name + ' must contain three finite numbers')
        values = np.asarray(value, np.float64)
        if not np.isfinite(values).all() or np.abs(values).max() > limit:
            raise AuthoringError(name + ' is outside the supported finite range')
        return values
    lo, hi = (vector(spec[key], key, 1e6) for key in ('bounds_min', 'bounds_max'))
    if (lo > hi).any():
        raise AuthoringError('Region minimum bounds must not exceed maximum bounds')
    if spec['space'] == 'ASSIGNMENT_NORMALIZED' and ((lo < 0).any() or (hi > 1).any()):
        raise AuthoringError('Assignment-normalized bounds must stay within [0, 1]')
    result = dict(spec, bounds_min=lo.tolist(), bounds_max=hi.tolist())
    if 'normal_direction' in spec:
        direction = vector(spec['normal_direction'], 'normal_direction', 1.)
        magnitude = float(np.linalg.norm(direction))
        if magnitude < 1e-12:
            raise AuthoringError('Region normal direction must be nonzero')
        for key in ('min_dot', 'max_dot'):
            value = spec.get(key, -1. if key == 'min_dot' else 1.)
            if type(value) not in (float, int) or not np.isfinite(value) or not -1 <= value <= 1:
                raise AuthoringError('Region normal dot limits must be within [-1, 1]')
            result[key] = float(value)
        if result['min_dot'] > result['max_dot']:
            raise AuthoringError('Minimum normal dot must not exceed maximum normal dot')
        result['normal_direction'] = (direction / magnitude).tolist()
    elif {'min_dot', 'max_dot'} & spec.keys():
        raise AuthoringError('Normal dot limits require a normal direction')
    return result


def _triangle_box(triangles, lower, upper):
    """Closed triangle/AABB intersection using all 13 separating axes.

    Selection includes the whole polygon if any of its triangles intersects.
    A triangle's bounding box alone is insufficient at corners and diagonals.
    """
    import numpy as np
    center, half = (lower + upper) * .5, (upper - lower) * .5
    vertices = triangles - center
    # Mesh coordinates originate as float32; decimal query bounds need the
    # corresponding numerical tolerance to include a face lying on the box.
    epsilon = 1e-7 * max(float(np.abs(triangles).max()), float(np.abs(center).max()), 1.)
    hits = (vertices.min(axis=1) <= half + epsilon).all(axis=1) & (vertices.max(axis=1) >= -half - epsilon).all(axis=1)
    candidates = np.flatnonzero(hits)
    if not len(candidates):
        return hits
    vertices = vertices[candidates]
    edges = np.roll(vertices, -1, axis=1) - vertices
    axes = [np.cross(edges[:, 0], edges[:, 1])]
    for edge in range(3):
        axes.extend(np.cross(edges[:, edge], axis) for axis in np.eye(3))
    separated = np.zeros(len(candidates), dtype=bool)
    for axis in axes:
        projections = np.einsum('nij,nj->ni', vertices, axis)
        radius = np.abs(axis) @ half
        tolerance = epsilon * np.linalg.norm(axis, axis=1)
        separated |= (projections.min(axis=1) > radius + tolerance) | (projections.max(axis=1) < -radius - tolerance)
    hits[candidates[separated]] = False
    return hits


def query(ts, objects, spec, registry):
    """Read-only geometric query; register only opaque, assignment-scoped results.

    This is available to Explain as well as authoring. It changes no Blender ID,
    selection, material, mesh, image or UV. Applying its ID later uses the normal
    reviewable authoring transaction and an ordinary editable REGION mask.
    """
    import numpy as np
    from ..geometry.snapshots import targets
    from .schema import AuthoringError
    spec = _query_spec(spec)
    obj = objects.get(spec['object_id'])
    slot = spec['slot_index']
    if obj is None or not any(ref.object == obj and ref.slot_index == slot for ref in ts.assignments):
        raise AuthoringError('Region object/material slot is outside this texture set assignment')
    with targets(ts) as meshes:
        target = next((target for target in meshes if target['object'] == obj), None)
        if target is None:
            raise AuthoringError('Region object is not available in the evaluated texture set')
        target['_region_topology'] = topology(target['mesh'])
        chosen = target['tri_material'] == slot
        assigned = np.unique(target['tri_polygons'][chosen])
        positions = target['positions']
        if spec['space'] == 'OBJECT':
            world = np.asarray(obj.matrix_world, dtype=np.float64)
            if abs(float(np.linalg.det(world[:3, :3]))) < 1e-12:
                raise AuthoringError('Object transform is singular; object-space region query is undefined')
            inverse = np.linalg.inv(world)
            positions = positions @ inverse[:3, :3].T + inverse[:3, 3]
        elif spec['space'] == 'ASSIGNMENT_NORMALIZED':
            used = np.unique(target['tri_vertices'][chosen])
            lower, upper = positions[used].min(axis=0), positions[used].max(axis=0)
            size = upper - lower
            positions = np.divide(positions - lower, size, out=np.zeros_like(positions), where=size > 1e-12)
            # Normal directions stay in WORLD axes, without bbox anisotropy.
        lower, upper = np.asarray(spec['bounds_min']), np.asarray(spec['bounds_max'])
        if spec['selection'] == 'CENTROID':
            centroids = np.asarray([positions[list(target['mesh'].polygons[int(face)].vertices)].mean(axis=0) for face in assigned])
            epsilon = 1e-7 * max(float(np.abs(centroids).max()), float(np.abs(lower).max()), float(np.abs(upper).max()), 1.)
            chosen_faces = assigned[((centroids >= lower - epsilon) & (centroids <= upper + epsilon)).all(axis=1)]
        else:
            hits = _triangle_box(positions[target['tri_vertices'][chosen]], lower, upper)
            chosen_faces = np.unique(target['tri_polygons'][chosen][hits])
        if 'normal_direction' in spec and len(chosen_faces):
            # Geometric polygon normals; smooth/custom corner normals do not
            # change a face's orientation classification.
            normals = np.asarray([target['mesh'].polygons[int(face)].normal for face in chosen_faces], np.float64)
            if spec['space'] != 'OBJECT':
                matrix = np.asarray(obj.matrix_world.to_3x3().inverted_safe().transposed())
                normals = normals @ matrix.T
            normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
            dot = normals @ np.asarray(spec['normal_direction'])
            chosen_faces = chosen_faces[(dot >= spec['min_dot'] - 1e-10) & (dot <= spec['max_dot'] + 1e-10)]
        if not len(chosen_faces):
            raise AuthoringError('Region query selected no assigned faces; inspect bounds and broaden the query')
        provenance = 'Frozen spatial query: ' + json.dumps(spec, sort_keys=True, separators=(',', ':'))
        uncertainty = ('Exact whole-polygon membership, not a semantic label or geometric cut. '
                       + ('Centroid selection can include polygons extending beyond the bounds.' if spec['selection'] == 'CENTROID'
                          else 'Intersection includes each whole polygon touching the closed box; coarse faces can extend beyond the bounds.')
                       + ' Shared boundaries are inclusive. Preview coverage before authoring.')
        record = _record(ts, target, spec['object_id'], slot, chosen_faces, 'SPATIAL_QUERY', provenance, uncertainty)
        fingerprint = _fingerprint(target)
        record['id'] = 'region:' + hashlib.sha256((record['id'] + fingerprint + provenance).encode()).hexdigest()[:32]
        record.update(query=spec, selection_precision='WHOLE_EVALUATED_POLYGONS',
                      normal_space='OBJECT' if spec['space'] == 'OBJECT' else 'WORLD',
                      _fingerprint=fingerprint, _texture_set=ts.id)
        if record['id'] not in registry and len(registry) >= MAX_QUERIES:
            raise AuthoringError(f'Limit an instruction to {MAX_QUERIES} spatial region queries')
        registry[record['id']] = record
    return _public(record)


def topology(mesh):
    import numpy as np
    digest = hashlib.sha256()
    for collection, property_name in ((mesh.loops, 'vertex_index'),
                                       (mesh.polygons, 'loop_start'),
                                       (mesh.polygons, 'loop_total'),
                                       (mesh.polygons, 'material_index')):
        values = np.empty(len(collection), np.int32)
        collection.foreach_get(property_name, values)
        digest.update(values.tobytes())
    digest.update(str(len(mesh.vertices)).encode())
    return digest.hexdigest()


def _components(mesh, faces):
    """Face-connected components; touching at only one vertex is not an edge."""
    parent = {int(face): int(face) for face in faces}
    edges = {}
    def root(face):
        while parent[face] != face:
            parent[face] = parent[parent[face]]
            face = parent[face]
        return face
    for face in faces:
        face = int(face)
        for edge in mesh.polygons[face].edge_keys:
            if edge in edges:
                a, b = root(face), root(edges[edge])
                parent[max(a, b)] = min(a, b)
            else:
                edges[edge] = face
    groups = {}
    for face in faces:
        groups.setdefault(root(int(face)), []).append(int(face))
    return sorted(groups.values(), key=lambda group: (-len(group), group[0]))


def _record(ts, target, object_id, slot, faces, kind, provenance, uncertainty):
    import numpy as np
    obj = target['object']
    faces = np.asarray(sorted(set(int(face) for face in faces)), np.int32)
    selected = np.isin(target['tri_polygons'], faces)
    if not selected.any():
        return None
    triangles = target['positions'][target['tri_vertices'][selected]]
    xyz = triangles.reshape(-1, 3)
    areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                    triangles[:, 2] - triangles[:, 0]), axis=1) * .5
    uv = target['uv'][target['tri_loops'][selected]]
    uv_area = np.abs((uv[:, 1, 0] - uv[:, 0, 0]) * (uv[:, 2, 1] - uv[:, 0, 1]) -
                     (uv[:, 1, 1] - uv[:, 0, 1]) * (uv[:, 2, 0] - uv[:, 0, 0])) * .5
    valid = np.isfinite(uv).all(axis=(1, 2)) & (uv_area > 1e-12) & (uv >= 0).all(axis=(1, 2)) & (uv <= 1).all(axis=(1, 2))
    stamp = target['_region_topology']
    object_key = f'{obj.library.filepath if obj.library else ""}|{obj.name_full}'
    digest = sha256(f'{ts.id}|{object_key}|{stamp}|{slot}|{kind}'.encode() + faces.tobytes()).hexdigest()
    centered = xyz - xyz.mean(axis=0)
    eigenvalues, eigenvectors = np.linalg.eigh(centered.T @ centered)
    axis = eigenvectors[:, -1]
    if axis[np.argmax(np.abs(axis))] < 0:
        axis = -axis
    surface_area = float(areas.sum())
    assigned_vertices = np.unique(target['tri_vertices'][target['tri_material'] == slot])
    assignment_xyz = target['positions'][assigned_vertices]
    assignment_min, assignment_max = assignment_xyz.min(axis=0), assignment_xyz.max(axis=0)
    assignment_size = assignment_max - assignment_min
    normalized = np.divide(xyz - assignment_min, assignment_size, out=np.zeros_like(xyz), where=assignment_size > 1e-12)
    if '_region_local' not in target:
        target['_region_local'] = np.empty((len(target['mesh'].vertices), 3), np.float32)
        target['mesh'].vertices.foreach_get('co', target['_region_local'].ravel())
    local = target['_region_local'][target['tri_vertices'][selected].ravel()]
    return dict(id='region:' + digest[:32], kind=kind, object_id=object_id, slot_index=int(slot),
                bounds_world=dict(minimum=xyz.min(axis=0).tolist(), maximum=xyz.max(axis=0).tolist()),
                bounds_object=dict(minimum=local.min(axis=0).tolist(), maximum=local.max(axis=0).tolist()),
                bounds_assignment_normalized=dict(minimum=normalized.min(axis=0).tolist(), maximum=normalized.max(axis=0).tolist(),
                    axes='WORLD', degenerate_axes=np.flatnonzero(assignment_size <= 1e-12).tolist()),
                center_world=xyz.mean(axis=0).tolist(), principal_axis_world=axis.tolist(),
                principal_axis_ambiguity=float(eigenvalues[-2] / max(eigenvalues[-1], 1e-12)),
                face_count=len(faces), triangle_count=int(selected.sum()), surface_area_world=surface_area,
                material_space=dict(render_uv=next((u.name for u in target['mesh'].uv_layers if u.active_render), target['mesh'].uv_layers.active.name),
                    valid_triangles=int(valid.sum()), total_triangles=int(selected.sum()),
                    valid_surface_fraction=float(areas[valid].sum() / surface_area) if surface_area else 0.,
                    validity='FINITE_NONDEGENERATE_IN_0_1', overlap_checked=False),
                provenance=provenance, uncertainty=uncertainty, semantic_label=None,
                topology_digest=stamp, _object=obj, _faces=faces.tolist())


def _catalog(ts, objects):
    import numpy as np
    from ..geometry.snapshots import targets
    from ..evaluation.mesh_masks import _selection_flags, DEFORMING
    bound = {obj: identifier for identifier, obj in objects.items()}
    entries = []
    with targets(ts) as meshes:
        for target in meshes:
            obj, mesh = target['object'], target['mesh']
            if obj not in bound:
                continue
            target['_region_topology'] = topology(mesh)
            for slot in sorted(target['slots']):
                assigned = np.unique(target['tri_polygons'][target['tri_material'] == slot])
                if not len(assigned):
                    continue
                def add(faces, kind, provenance, uncertainty):
                    if len(entries) >= MAX_REGIONS:
                        if kind != 'ASSIGNMENT':
                            return
                        discard = next((i for i in range(len(entries) - 1, -1, -1)
                                        if entries[i]['kind'] != 'ASSIGNMENT'), None)
                        if discard is None:
                            from .schema import AuthoringError
                            raise AuthoringError('Limit one instruction to 128 material-slot assignments')
                        entries.pop(discard)
                    record = _record(ts, target, bound[obj], slot, faces, kind, provenance, uncertainty)
                    if record:
                        entries.append(record)
                add(assigned, 'ASSIGNMENT', 'Existing object/material-slot assignment.', 'NONE: exact assigned faces; no semantic claim.')
                # Saved masks remain discoverable after a new instruction or
                # reopening the blend; their membership remains normal authoring
                # state, not a request registry or an evaluator-only annotation.
                for layer in ts.layers:
                    for mask in layer.masks:
                        if mask.source != 'MESH' or mask.mesh.kind != 'REGION' or not mask.mesh.region_id or mask.mesh.resolve('object') != obj:
                            continue
                        try:
                            frozen = face_indices(mask.mesh, mesh)
                        except ValueError:
                            continue
                        if not np.isin(frozen, assigned).all():
                            continue
                        saved = _record(ts, target, bound[obj], slot, frozen, 'SAVED_REGION', mask.mesh.region_provenance,
                            'Frozen editable polygon membership from an existing mask. Inspect before reuse; geometry semantics are not inferred.')
                        if saved and not any(item['id'] == mask.mesh.region_id for item in entries) and len(entries) < MAX_REGIONS:
                            saved.update(id=mask.mesh.region_id, layer_id=layer.id, mask_id=mask.id, name=mask.name,
                                         selection_precision='WHOLE_EVALUATED_POLYGONS')
                            entries.append(saved)
                selected = _selection_flags(obj)
                selection_maps = all(modifier.type in DEFORMING for modifier in obj.modifiers
                                     if modifier.show_viewport)
                if selection_maps and len(selected) == len(mesh.polygons) and selected[assigned].any():
                    add(assigned[selected[assigned]], 'ARTIST_SELECTION', 'Frozen snapshot of artist-selected faces inside this assignment.', 'Selection maps by face index; unsupported topology-changing modifiers must be applied first.')
                components = _components(mesh, assigned)
                if len(components) > 1:
                    for group in components[:MAX_COMPONENTS]:
                        add(group, 'CONNECTED_COMPONENT', f'Edge-connected geometry component; {len(components)} total, largest {MAX_COMPONENTS} exposed.',
                            'Geometry connectivity is not material identity. Inspect coverage before assigning a finish.')
                # Geometry candidates are offered as uncertain regions, never soles,
                # handles or other benchmark-specific semantic annotations.
                centroid = np.array([obj.matrix_world @ mesh.polygons[int(i)].center for i in assigned])
                used = np.unique(target['tri_vertices'][target['tri_material'] == slot])
                z0, z1 = float(target['positions'][used, 2].min()), float(target['positions'][used, 2].max())
                if z1 > z0 + 1e-9:
                    relative = (centroid[:, 2] - z0) / (z1 - z0)
                    for lo, hi in ((0., .125), (0., .25), (.75, 1.)):
                        add(assigned[(relative >= lo) & (relative <= hi)], 'HEIGHT_CANDIDATE',
                            f'Face centroids in world-Z relative height [{lo:g}, {hi:g}] of this assignment.',
                            'Face-based geometric approximation, not a semantic boundary; coarse polygons limit precision.')
    return entries


def capture(ts, objects):
    """JSON-safe regions; objects is the existing resolved opaque object binding."""
    entries = _catalog(ts, objects)
    for identifier in getattr(objects, 'region_registry', {}):
        try:
            item = _registered(ts, identifier, objects)
        except ValueError:
            continue
        if item and not any(entry['id'] == identifier for entry in entries):
            entries.append(item)
    return [_public(item) for item in entries]


def _registered(ts, region_id, objects):
    """Validate a query snapshot again at preview/compile/commit boundaries."""
    from ..geometry.snapshots import targets
    registry = getattr(objects, 'region_registry', {})
    item = registry.get(region_id)
    if item is None:
        return None
    if item['_texture_set'] != ts.id or objects.get(item['object_id']) != item['_object']:
        raise ValueError('Region query is outside the current object/material scope')
    obj, slot = item['_object'], item['slot_index']
    if not any(ref.object == obj and ref.slot_index == slot for ref in ts.assignments):
        raise ValueError('Region query assignment changed; query and preview its coverage again')
    with targets(ts) as meshes:
        target = next(target for target in meshes if target['object'] == obj)
        target['_region_topology'] = topology(target['mesh'])
        if _fingerprint(target) != item['_fingerprint']:
            raise ValueError('Region query geometry, transform or UVs changed; query and preview its coverage again')
    return item


def selector(ts, region_id, objects):
    from .schema import AuthoringError
    try:
        found = _registered(ts, region_id, objects)
    except ValueError as exc:
        raise AuthoringError(str(exc)) from None
    if found is None:
        found = next((item for item in _catalog(ts, objects) if item['id'] == region_id), None)
    if found is None:
        raise AuthoringError('Unknown or stale region; inspect the current regions again')
    if found['material_space']['valid_surface_fraction'] < .999:
        raise AuthoringError('Region has invalid or out-of-range render UVs; prepare its UVs before texturing')
    return dict(kind='REGION', object=found['_object'], object_name=found['_object'].name_full,
                region_id=found['id'], region_faces=json.dumps(found['_faces'], separators=(',', ':')),
                region_topology=found['topology_digest'], region_provenance=found['provenance'])


def face_indices(selector, mesh):
    """Reject stale/corrupt membership instead of targeting unrelated new polygons."""
    if not selector.region_topology or topology(mesh) != selector.region_topology:
        raise ValueError('Region topology changed; inspect and replace its face selection')
    try:
        faces = json.loads(selector.region_faces)
    except (ValueError, TypeError):
        raise ValueError('Region face membership is invalid') from None
    if not isinstance(faces, list) or not faces or any(type(i) is not int or i < 0 or i >= len(mesh.polygons) for i in faces):
        raise ValueError('Region face membership is invalid')
    return faces
