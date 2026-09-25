"""Non-destructive bake diagnostics with explicit cost and severity."""
from dataclasses import dataclass
import bpy
import numpy as np

# Last explicit inspection per (scene, texture set); runtime only, never saved.
_last = {}


@dataclass(frozen=True)
class Issue:
    severity: str
    message: str
    object_name: str = ''
    faces: tuple = ()


def inspect(ts, evaluated=False):
    issues = []
    if not ts.assignments:
        issues.append(Issue('ERROR', 'No target material slots assigned'))
    for ref in ts.assignments:
        obj = ref.object
        if obj is None or obj.type != 'MESH':
            issues.append(Issue('ERROR', 'Missing mesh target', ref.object_name))
            continue
        if ref.slot_index < 0 or ref.slot_index >= len(obj.material_slots):
            issues.append(Issue('ERROR', 'Assigned material slot is missing', obj.name))
        if not obj.data.uv_layers:
            issues.append(Issue('ERROR', 'Render UV map is missing', obj.name))
        scale = np.linalg.norm(np.asarray(obj.matrix_world)[:3,:3], axis=0)
        if np.min(scale) < 1e-8:
            issues.append(Issue('ERROR', 'Singular object scale', obj.name))
        elif not np.allclose(scale, scale[0]):
            issues.append(Issue('WARNING', 'Non-uniform scale: cage distance uses world units', obj.name))
    settings = ts.bake
    if (settings.normal or settings.ao) and settings.selected_to_active and not any(r.object for r in settings.high_objects):
        issues.append(Issue('ERROR', 'High-poly source list is empty'))
    if settings.normal or settings.ao:
        from .pairing import groups
        from .cages import validate_topology
        try:
            parts = groups(ts)
            if evaluated:
                # ``validate_topology`` stages from the context's scene and layer: the set's
                # scene, in the layer the CPU reference evaluates the targets in.
                from ..geometry.snapshots import view_layer_for
                scene = ts.id_data
                lows = [part.low for part in parts]
                with bpy.context.temp_override(scene=scene, view_layer=view_layer_for(scene, lows)):
                    for part in parts:
                        if part.cage:
                            validate_topology(part.low, part.cage)
        except ValueError as exc:
            issues.append(Issue('ERROR', str(exc)))
    if not evaluated or any(i.severity == 'ERROR' for i in issues):
        return issues
    from ..geometry.snapshots import targets
    with targets(ts) as meshes:
        shared = []  # (uv triangle, object index, polygon index) for exact overlap analysis
        for index, target in enumerate(meshes):
            triangles = target['triangles']
            if not triangles:
                continue
            loops = np.asarray([tri.loops[:] for tri in triangles], np.int64)
            polygon = np.asarray([tri.polygon_index for tri in triangles], np.int64)
            area = np.asarray([tri.area for tri in triangles], np.float64)
            uv = target['uv'][loops].astype(np.float64)
            a, b = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
            degenerate = (np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) < 1e-12) | (area < 1e-12)
            outside = np.any((uv < -1e-6) | (uv > 1 + 1e-6), axis=(1, 2))
            if degenerate.any():
                issues.append(Issue('WARNING', 'Degenerate geometry or UV triangles', target['object'].name,
                                    tuple(int(f) for f in np.unique(polygon[degenerate]))))
            if outside.any():
                from ..geometry.udim import tile_numbers
                tiles, invalid = tile_numbers(uv[~degenerate] if degenerate.any() else uv)
                spread = ', '.join(str(t) for t in tiles if t != 1001)
                message = 'UVs outside the single supported tile; wrapping may be intentional'
                if spread:
                    message += f'. Faces also cover UDIM tile(s) {spread}; create UDIM texture sets to author those tiles'
                if invalid:
                    message += '; some faces lie outside the 1001-2000 UDIM range'
                issues.append(Issue('WARNING', message, target['object'].name,
                                    tuple(int(f) for f in np.unique(polygon[outside]))))
            shared.append((uv[~degenerate], np.full(int((~degenerate).sum()), index, np.int64), polygon[~degenerate]))
        issues.extend(_overlap_issues(meshes, shared, .25 / int(ts.resolution)))
    return issues


def _overlap_issues(meshes, shared, eps):
    """Exact triangle overlap across every object sharing this texture set.

    ``eps`` is the overlap depth, in UV units, below which touching faces are
    not reported; the caller derives it from texel size at the authored
    resolution. Partial overlaps bleed bakes between unrelated surfaces. Stacked faces
    (identical, possibly mirrored, polygon corner sets) share texels
    deliberately on symmetric assets, so they are reported separately and less
    severely. Stacking is decided per polygon: a mirrored quad triangulates
    along the other diagonal, so its triangles alone would look partial.
    """
    from ..geometry import uv_overlap
    if not shared:
        return []
    uv = np.concatenate([item[0] for item in shared])
    owner = np.concatenate([item[1] for item in shared])
    polygon = np.concatenate([item[2] for item in shared])
    if len(uv) < 2:
        return []
    label = owner * (polygon.max() + 1) + polygon
    try:
        partial, stacked, _ = uv_overlap.overlaps(uv, eps=eps, exclude_same=label)
    except ValueError as exc:
        return [Issue('INFO', str(exc))]
    pairs = np.concatenate((partial, stacked), axis=1)
    if not pairs.shape[1]:
        return []
    keys, winding = {}, {}
    for index in np.unique(pairs):
        item = (int(owner[index]), int(polygon[index]))
        if item in keys:
            continue
        target = meshes[item[0]]
        corners = target['uv'][list(target['mesh'].polygons[item[1]].loop_indices)]
        keys[item] = tuple(sorted(map(tuple, np.round(corners, 5))))
        x, y = corners[:, 0], corners[:, 1]
        winding[item] = np.sign(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    same = np.asarray([keys[(int(owner[i]), int(polygon[i]))] == keys[(int(owner[j]), int(polygon[j]))]
                       for i, j in pairs.T], bool)
    mirrored = np.asarray([winding[(int(owner[i]), int(polygon[i]))] != winding[(int(owner[j]), int(polygon[j]))]
                           for i, j in pairs.T], bool) & same
    issues = []
    for members, flags, message in (
        (pairs[:, ~same], None, 'Partially overlapping UVs bleed between bakes'),
        (pairs[:, same], mirrored[same], 'Stacked or mirrored UV islands share texels; deliberate on symmetric assets'),
    ):
        if not members.shape[1]:
            continue
        flat = np.concatenate(members)
        for index, target in enumerate(meshes):
            faces = np.unique(polygon[flat[owner[flat] == index]])
            if not len(faces):
                continue
            detail = message
            if flags is not None and flags.any():
                mirrored_faces = len({(int(owner[t]), int(polygon[t])) for t in np.concatenate(members[:, flags])
                                      if owner[t] == index})
                if mirrored_faces:
                    detail += f' ({mirrored_faces} mirrored face(s))'
            others = sorted({meshes[o]['object'].name for o in np.unique(owner[flat]) if o != index})
            if others:
                detail += '; also overlaps ' + ', '.join(others)
            issues.append(Issue('WARNING' if flags is None else 'INFO', detail,
                                target['object'].name, tuple(int(f) for f in faces)))
    return issues


RAY_SAMPLES = 20000


def ray_coverage(ts, samples=RAY_SAMPLES, seed=0):
    """Sampled cage-ray misses per assigned low-poly object; a bounded estimate.

    Rays follow the bake configuration: from an automatic cage they start at
    the surface point pushed out along its corner normal by the extrusion and
    travel inward; from a custom cage they start at the corresponding cage
    point and travel toward the low-poly point. A ray counts as a hit when any
    high-poly source is met within the configured maximum distance (or at any
    distance when that is zero). Misses are reported as a fraction of sampled
    rays with the faces that contained them. This is not exact coverage.
    """
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    from ..geometry.snapshots import targets
    from .pairing import groups
    settings = ts.bake
    if not settings.selected_to_active or not (settings.normal or settings.ao):
        return []
    parts = {part.low: part for part in groups(ts)}
    if not any(part.sources for part in parts.values()):
        return []
    extrusion = settings.cage_extrusion if settings.separate_cage_distance else settings.ray_distance
    limit = settings.ray_distance if settings.ray_distance > 0 else None
    scene = ts.id_data
    # Sources and cages are read from the layer the targets are evaluated in (``targets``
    # below); a source disabled in viewports is read from its original, as the bake stages it.
    from ..geometry.snapshots import depsgraph_for, evaluable
    others = list(dict.fromkeys(obj for part in parts.values() for obj in (*part.sources, part.cage) if obj))
    depsgraph = depsgraph_for(scene, [*parts, *evaluable(scene, others)])
    rng = np.random.default_rng(seed)
    issues = []
    with targets(ts) as meshes:
        weights = np.asarray([sum(tri.area for tri in target['triangles']) for target in meshes], np.float64)
        shares = np.maximum(np.round(weights / max(weights.sum(), 1e-12) * samples).astype(int), 1)
        for target, count in zip(meshes, shares):
            part = parts.get(target['object'])
            if part is None or not part.sources:
                continue
            triangles = target['triangles']
            vertices = np.asarray([tri.vertices[:] for tri in triangles], np.int64)
            loops = np.asarray([tri.loops[:] for tri in triangles], np.int64)
            polygon = np.asarray([tri.polygon_index for tri in triangles], np.int64)
            area = np.asarray([tri.area for tri in triangles], np.float64)
            if area.sum() <= 0:
                continue
            pick = rng.choice(len(triangles), size=int(count), p=area / area.sum())
            r1, r2 = rng.random((2, int(count)))
            root = np.sqrt(r1)
            bary = np.stack(((1 - root), root * (1 - r2), root * r2), 1)
            corners = target['positions'][vertices[pick]]
            points = np.einsum('nk,nkj->nj', bary, corners)
            normals = np.einsum('nk,nkj->nj', bary, target['world_normals'][loops[pick]])
            normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
            if settings.cage_mode == 'CUSTOM' and part.cage is not None:
                cage = part.cage.evaluated_get(depsgraph)
                cage_mesh = cage.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
                try:
                    if len(cage_mesh.vertices) != len(target['mesh'].vertices):
                        issues.append(Issue('ERROR', 'Custom cage vertex count differs from the evaluated low-poly mesh',
                                            target['object'].name))
                        continue
                    local = np.empty((len(cage_mesh.vertices), 3), np.float32)
                    cage_mesh.vertices.foreach_get('co', local.ravel())
                    world = np.asarray(cage.matrix_world, np.float64)
                    cage_positions = local @ world[:3, :3].T + world[:3, 3]
                finally:
                    cage.to_mesh_clear()
                origins = np.einsum('nk,nkj->nj', bary, cage_positions[vertices[pick]])
                directions = points - origins
                lengths = np.linalg.norm(directions, axis=1, keepdims=True)
                directions = np.where(lengths > 1e-12, directions / np.maximum(lengths, 1e-12), -normals)
                cage_label = f"custom cage '{part.cage.name}'"
            else:
                offset = extrusion if settings.cage_mode == 'AUTO' else 0.0
                origins = points + normals * offset
                directions = -normals
                cage_label = f'automatic cage extruded {offset:g}' if offset else 'the surface with no cage'
            trees = []
            for source in part.sources:
                tree = BVHTree.FromObject(source, depsgraph)
                inverse = np.asarray(source.matrix_world.inverted(), np.float64)
                trees.append((tree, inverse, np.asarray(source.matrix_world, np.float64)))
            missed = np.ones(len(points), bool)
            for tree, inverse, world in trees:
                for index in np.flatnonzero(missed):
                    origin = inverse[:3, :3] @ origins[index] + inverse[:3, 3]
                    direction = inverse[:3, :3] @ directions[index]
                    hit = tree.ray_cast(Vector(origin), Vector(direction))
                    if hit[0] is None:
                        continue
                    if limit is None:
                        missed[index] = False
                        continue
                    location = world[:3, :3] @ np.asarray(hit[0], np.float64) + world[:3, 3]
                    if np.linalg.norm(location - origins[index]) <= limit + 1e-9:
                        missed[index] = False
            misses = int(missed.sum())
            fraction = misses / len(points)
            faces = tuple(int(f) for f in np.unique(polygon[pick[missed]]))
            message = (f'{misses} of {len(points)} sampled rays from {cage_label} missed the high-poly sources '
                       f'({fraction:.1%}); a sampled estimate, not exact coverage')
            issues.append(Issue('WARNING' if fraction > .01 else 'INFO', message, target['object'].name, faces))
    return issues


def run(ts, rays=True):
    """Explicit evaluated inspection; the result is retained for the panel."""
    from ..evaluation.resource_keys import key_for
    issues = list(inspect(ts, evaluated=True))
    if rays and not any(issue.severity == 'ERROR' for issue in issues):
        issues.extend(ray_coverage(ts))
    issues = tuple(issues)
    _last[key_for(ts.id_data, ts)] = issues
    return issues


def last(ts):
    from ..evaluation.resource_keys import key_for
    return _last.get(key_for(ts.id_data, ts), ())


def forget(ts=None):
    if ts is None:
        _last.clear()
    else:
        from ..evaluation.resource_keys import key_for
        _last.pop(key_for(ts.id_data, ts), None)


def select_faces(ts, issues):
    """Select reported faces on the base meshes; selection is the only change.

    Evaluated polygon indices only address base faces when no modifier changed
    the face count, so such objects are reported instead of guessed.
    """
    selected, skipped = {}, []
    for issue in issues:
        if not issue.faces:
            continue
        selected.setdefault(issue.object_name, set()).update(issue.faces)
    objects = {ref.object.name: ref.object for ref in ts.assignments if ref.object}
    for name, faces in selected.items():
        obj = objects.get(name)
        if obj is None or obj.type != 'MESH':
            skipped.append(name)
            continue
        mesh = obj.data
        if max(faces) >= len(mesh.polygons):
            skipped.append(name)
            continue
        # The layer the issues were found in: another one may evaluate the target to its
        # base mesh and pass evaluated indices off as base faces.
        from ..geometry.snapshots import depsgraph_for
        try:
            evaluated = obj.evaluated_get(depsgraph_for(ts.id_data, [obj]))
        except ValueError:
            skipped.append(name)
            continue
        if len(evaluated.data.polygons) != len(mesh.polygons):
            skipped.append(name)
            continue
        if obj.mode == 'EDIT':
            import bmesh
            bm = bmesh.from_edit_mesh(mesh)
            bm.faces.ensure_lookup_table()
            for face in bm.faces:
                face.select_set(face.index in faces)
            bm.select_flush_mode()
            bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        else:
            flags = np.zeros(len(mesh.polygons), bool)
            flags[list(faces)] = True
            mesh.polygons.foreach_set('select', flags)
            mesh.vertices.foreach_set('select', np.zeros(len(mesh.vertices), bool))
            mesh.edges.foreach_set('select', np.zeros(len(mesh.edges), bool))
            mesh.update()
    return {name: len(faces) for name, faces in selected.items() if name not in skipped}, skipped
