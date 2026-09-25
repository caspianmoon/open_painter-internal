"""Request-owned evaluated geometry for candidate pixels and paired UV boundaries.

No Blender mesh or depsgraph handles survive capture. UV islands are topology-based,
not guessed from image proximity. Nonmanifold edges are reported, never arbitrarily paired.
"""
import math
import numpy as np

MAX_TRIANGLES = 2_000_000


def seam_pairs(target, object_id, texture_set_id):
    from .seams import _edge_sides
    from ..baking.maps.utility import _islands
    mesh, uv = target['mesh'], target['uv']
    labels, _ = _islands(target)
    faces = np.unique(target['tri_polygons'])
    va, vb, face, la, lb = _edge_sides(mesh, uv, faces)
    keys = np.minimum(va, vb).astype(np.int64) * len(mesh.vertices) + np.maximum(va, vb)
    order = np.argsort(keys, kind='stable')
    _, starts, counts = np.unique(keys[order], return_index=True, return_counts=True)
    a, b = order[starts[counts == 2]], order[starts[counts == 2] + 1]
    lo, hi = np.where(va < vb, la, lb), np.where(va < vb, lb, la)
    different = np.any(abs(uv[lo[a]] - uv[lo[b]]) > 1e-6, axis=1) | np.any(abs(uv[hi[a]] - uv[hi[b]]) > 1e-6, axis=1)
    result = []
    for a, b in zip(a[different], b[different]):
        vertex_ids = sorted((int(va[a]), int(vb[a])))
        if np.linalg.norm(np.diff(target['positions'][vertex_ids], axis=0)) < 1e-12:
            continue
        sides = []
        for side in (a, b):
            f = mesh.polygons[int(face[side])]
            ends = uv[[lo[side], hi[side]]].astype(float)
            sides.append(dict(face=int(face[side]), uv_edge=ends.tolist(),
                uv_island=int(labels[face[side]]), uv_center=uv[list(f.loop_indices)].mean(0).tolist(),
                winding_reversed=bool(va[side] > vb[side])))
        ea = np.diff(sides[0]['uv_edge'], axis=0)[0]
        eb = np.diff(sides[1]['uv_edge'], axis=0)[0]
        laa, lbb = np.linalg.norm(ea), np.linalg.norm(eb)
        rotation = math.atan2(ea[0]*eb[1]-ea[1]*eb[0], float(ea @ eb)) if laa*lbb > 1e-15 else None
        result.append(dict(seam_id=f'{object_id}:{texture_set_id}:edge:{vertex_ids[0]}:{vertex_ids[1]}',
            object_id=object_id, texture_set_id=texture_set_id, vertex_ids=vertex_ids,
            edge_world=target['positions'][vertex_ids].tolist(), adjacent_faces=[s['face'] for s in sides],
            side_a=sides[0], side_b=sides[1], orientation=dict(
                endpoint_rule='A0=B0 and A1=B1 in world space; arrows follow the same physical direction',
                rotation_radians=rotation, uv_length_ratio=float(lbb/laa) if laa > 1e-12 else None,
                winding_reversed=sides[0]['winding_reversed'] != sides[1]['winding_reversed'])))
    return result, labels, int((counts > 2).sum())


def capture(context, scene, bindings):
    from mathutils.bvhtree import BVHTree
    from .snapshots import _triangle_arrays, _unit
    graph = context.evaluated_depsgraph_get()
    names = {name: key for key, (name, _) in bindings.items()}
    assignments = {}
    for ts in scene.open_painter.texture_sets:
        for ref in ts.assignments:
            if ref.object:
                assignments[(ref.object, ref.slot_index)] = ts.id
    records, count = [], 0
    for obj in sorted(scene.objects, key=lambda obj: obj.name_full):
        if not obj.visible_get(view_layer=context.view_layer) or obj.type not in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}:
            continue
        evaluated = obj.evaluated_get(graph)
        try:
            mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=graph)
            if not mesh:
                continue
            mesh.calc_loop_triangles()
            vertices, loops, materials, polygons = _triangle_arrays(mesh)
            count += len(vertices)
            if count > MAX_TRIANGLES:
                raise ValueError('Candidate correspondence supports at most two million visible triangles')
            if not len(vertices):
                continue
            local = np.empty((len(mesh.vertices), 3), np.float32)
            mesh.vertices.foreach_get('co', local.ravel())
            matrix = np.asarray(evaluated.matrix_world, np.float64)
            positions = local @ matrix[:3, :3].T + matrix[:3, 3]
            normals = np.empty((len(mesh.loops), 3), np.float32)
            mesh.corner_normals.foreach_get('vector', normals.ravel())
            normals = _unit(normals @ np.linalg.pinv(matrix[:3, :3]))
            layer = next((uv for uv in mesh.uv_layers if uv.active_render), mesh.uv_layers.active)
            uv = np.zeros((len(mesh.loops), 2), np.float32)
            if layer:
                layer.data.foreach_get('uv', uv.ravel())
            original = obj.get('open_painter_candidate_source', obj.name_full)
            object_id = names.get(original, 'occluder:' + original)
            sets = {slot: identifier for (owner, slot), identifier in assignments.items() if owner == obj}
            seams, islands = {}, {}
            nonmanifold = 0
            for identifier in set(sets.values()):
                slots = {slot for slot, value in sets.items() if value == identifier}
                ts = next(item for item in scene.open_painter.texture_sets if item.id == identifier)
                if ts.udim_tile:
                    from ..core.udim import local_uv
                    faces = np.unique(polygons[np.isin(materials, list(slots))])
                    coords = local_uv(mesh, ts, faces)
                    for face in faces:
                        indices = list(mesh.polygons[int(face)].loop_indices)
                        uv[indices] = coords[indices]
                target = dict(mesh=mesh, uv=uv, positions=positions, slots=slots,
                              tri_polygons=polygons[np.isin(materials, list(slots))])
                if not layer:
                    raise ValueError('Candidate correspondence needs render UVs on ' + original)
                seams[identifier], islands[identifier], excluded = seam_pairs(target, object_id, identifier)
                nonmanifold += excluded
            records.append(dict(object_id=object_id, name=original, positions=positions,
                vertices=vertices.copy(), uv=uv[loops].copy(), normals=normals[loops].copy(),
                polygons=polygons.copy(), materials=materials.copy(), sets=sets, seams=seams, islands=islands,
                nonmanifold_edges=nonmanifold,
                bvh=BVHTree.FromPolygons(positions.tolist(), vertices.tolist(), all_triangles=True)))
        finally:
            evaluated.to_mesh_clear()
    return records


def ray(view, x, y):
    """Normalized image coordinates have top-left origin, including orthographic views."""
    if not (math.isfinite(x) and math.isfinite(y) and 0 <= x <= 1 and 0 <= y <= 1):
        raise ValueError('Choose finite normalized candidate image coordinates in [0, 1]')
    inverse = np.linalg.inv(np.asarray(view['projection']) @ np.asarray(view['matrix']))
    ends = []
    for z in (-1., 1.):
        point = inverse @ [2*x-1, 1-2*y, z, 1]
        ends.append(point[:3] / point[3])
    direction = ends[1]-ends[0]
    length = float(np.linalg.norm(direction))
    return ends[0], direction / length, length


def lookup(records, view, x, y, texture_set_id):
    from mathutils import Vector
    origin, direction, distance = ray(view, x, y)
    depth_image = view.get('depth')
    visible_depth = None
    if depth_image is not None:
        h, w = depth_image.shape
        col, row = min(int(x*w), w-1), min(int(y*h), h-1)
        depth = float(depth_image[row, col])
        if 0 < depth < 1:
            # Reconstruct depth on this ray; use pixel-centre picking to match
            # the rasterized surface rather than a subpixel edge neighbour.
            x, y = (col+.5)/w, (row+.5)/h
            origin, direction, distance = ray(view, x, y)
            inverse = np.linalg.inv(np.asarray(view['projection']) @ np.asarray(view['matrix']))
            point = inverse @ [2*x-1, 1-2*y, depth*2-1, 1]
            visible_depth = float((point[:3]/point[3]-origin) @ direction)
    best = None
    for record in records:
        travelled = 0.
        for _ in range(32):
            location, _, triangle, depth = record['bvh'].ray_cast(Vector(origin+direction*travelled), Vector(direction), distance-travelled)
            if location is None:
                break
            absolute_depth = travelled+depth
            if visible_depth is None or abs(absolute_depth-visible_depth) < max(1e-5, visible_depth*.002):
                if best is None or absolute_depth < best[0]:
                    best = absolute_depth, record, triangle, np.asarray(location)
                break
            if absolute_depth > visible_depth:
                break
            # Skip geometry in front of the depth-tested visible surface (e.g.
            # alpha-cutout texels). Never return its hidden triangle as evidence.
            travelled = absolute_depth + max(1e-6, distance*1e-7)
    if best is None:
        raise ValueError('The selected candidate pixel is background or has no matching evaluated surface; choose a visible opaque surface')
    _, record, triangle, position = best
    material = int(record['materials'][triangle])
    if record['sets'].get(material) != texture_set_id:
        raise ValueError('The visible pixel belongs to another material or an occluder; choose the requested material')
    p = record['positions'][record['vertices'][triangle]]
    edges = (p[1:]-p[0]).T
    uvw = np.linalg.lstsq(edges, position-p[0], rcond=None)[0]
    bary = np.array([1-uvw.sum(), *uvw])
    uv = bary @ record['uv'][triangle]
    normal = bary @ record['normals'][triangle]
    normal /= max(np.linalg.norm(normal), 1e-12)
    face = int(record['polygons'][triangle])
    return record, dict(object_id=record['object_id'], texture_set_id=texture_set_id,
        triangle=int(triangle), face=face, barycentric=bary.tolist(), position=position.tolist(),
        normal=normal.tolist(), uv=uv.tolist(), uv_island=int(record['islands'][texture_set_id][face]),
        material_slot=material)


def component_triangles(record, triangle):
    from ..evaluation.material_space import component_frames
    if '_frames' not in record:
        record['_frames'] = component_frames(record['positions'], record['vertices'])
    frames = record['_frames']
    return np.array([frame is frames[triangle] for frame in frames]), frames[triangle]


def neighborhood(record, hit):
    """Stay on the hit's connected surface, so a nearby parallel component isn't paired."""
    vertices = record['vertices']
    same, (origin, axes) = component_triangles(record, hit['triangle'])
    chosen = vertices[same]
    extent = np.ptp(record['positions'][np.unique(chosen)], axis=0)
    radius = max(float(np.linalg.norm(extent)) * .045, 1e-5)
    position = np.asarray(hit['position'])
    nearby = []
    faces = set(record['polygons'][same].tolist())
    for seam in record['seams'][hit['texture_set_id']]:
        if not any(face in faces for face in seam['adjacent_faces']):
            continue
        a, b = np.asarray(seam['edge_world'])
        edge = b-a
        t = float(np.clip((position-a) @ edge / max(float(edge @ edge), 1e-20), 0, 1))
        point = a+t*edge
        distance = float(np.linalg.norm(point-position))
        if distance <= radius*2:
            nearby.append(dict(seam, distance_world=distance, edge_parameter=t, closest_world=point.tolist()))
    nearby.sort(key=lambda item: (item['distance_world'], item['seam_id']))
    normal = np.asarray(hit['normal'])
    along = axes[1] - normal * (axes[1] @ normal)
    if np.linalg.norm(along) < 1e-5:
        along = axes[0] - normal * (axes[0] @ normal)
    along /= max(np.linalg.norm(along), 1e-12)
    return nearby[:4], radius, dict(along_component=along.tolist(), around_component=np.cross(normal, along).tolist(),
        meaning='Geometric principal-axis tangent and its perpendicular, not a semantic scale/grain direction; symmetric components are ambiguous')
