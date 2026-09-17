"""Bake Doctor: pre-bake checks whose findings select the offending geometry.

Painter has to diagnose imported geometry; Open Painter owns it. Every check
here is non-destructive and reports base-mesh elements (faces, vertices,
edges) or objects that ``select`` can highlight in Blender. The pre-bake tier
runs before every bake (errors stop it, warnings do not); the full tier adds
the sampled cage-ray coverage from ``diagnostics``.
"""
from dataclasses import dataclass, field
from time import perf_counter

import bpy
import numpy as np

from . import diagnostics
from .diagnostics import Issue

CHECKS = (
    ('uvs', 'UVs valid'),
    ('normals', 'Normals valid'),
    ('scale', 'Scale applied'),
    ('naming', 'Low/high naming matched'),
    ('doubles', 'No duplicate vertices'),
    ('modifiers', 'Render modifiers match'),
    ('cage', 'Cage clear of geometry'),
    ('seams', 'UV island seams'),
    ('coverage', 'Cage-ray coverage'),
)
LABELS = dict(CHECKS)
_last = {}   # (scene pointer, set id) -> Report; runtime only


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str            # 'ERROR' | 'WARNING' | 'INFO'
    message: str
    object_name: str = ''
    faces: tuple = ()        # base-mesh polygon indices
    vertices: tuple = ()
    edges: tuple = ()
    objects: tuple = ()      # object names to select as a whole

    @property
    def selectable(self):
        return bool(self.faces or self.vertices or self.edges or self.objects)

    @property
    def count(self):
        return len(self.faces) or len(self.vertices) or len(self.edges) or len(self.objects)


@dataclass
class Report:
    tier: str
    findings: tuple = ()
    ran: tuple = ()          # check keys that ran
    elapsed_ms: float = 0.0

    def status(self, key):
        """'PASS' | 'WARN' | 'FAIL' | 'SKIP' for one check."""
        if key not in self.ran:
            return 'SKIP'
        worst = 'PASS'
        for finding in self.findings:
            if finding.check != key:
                continue
            if finding.severity == 'ERROR':
                return 'FAIL'
            if finding.severity == 'WARNING':
                worst = 'WARN'
            elif worst == 'PASS':
                worst = 'INFO'
        return worst

    @property
    def errors(self):
        return [f for f in self.findings if f.severity == 'ERROR']

    @property
    def warnings(self):
        return [f for f in self.findings if f.severity == 'WARNING']

    def summary(self):
        return {key: self.status(key) for key, _ in CHECKS}


# ---- helpers -----------------------------------------------------------------

def _lows(ts):
    return tuple(dict.fromkeys(ref.object for ref in ts.assignments if ref.object and ref.object.type == 'MESH'))


def _highs(ts):
    return tuple(dict.fromkeys(ref.object for ref in ts.bake.high_objects if ref.object and ref.object.type == 'MESH'))


def _cages(ts):
    settings = ts.bake
    found = [settings.cage_object] if settings.cage_mode == 'CUSTOM' and settings.cage_object else []
    found.extend(ref.cage for ref in settings.high_objects if getattr(ref, 'cage', None) is not None)
    return tuple(dict.fromkeys(obj for obj in found if obj.type == 'MESH'))


def _projecting(ts):
    settings = ts.bake
    return bool(settings.selected_to_active and (settings.normal or settings.ao))


def _bmesh(mesh):
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    return bm


def _base_topology(ts):
    """Per target name, whether the viewport-evaluated face count matches its base mesh.

    ``diagnostics`` reports polygon indices of the evaluated mesh (the one
    ``geometry.snapshots.targets`` builds); they only address base faces
    when no modifier changes the face count. The same layer as ``targets``:
    a layer that excludes the target evaluates it to its base mesh, which
    would pass modifier-changed indices off as base faces.
    """
    from ..geometry.snapshots import depsgraph_for
    lows = _lows(ts)
    depsgraph = depsgraph_for(ts.id_data, lows)
    return {obj.name: len(obj.evaluated_get(depsgraph).data.polygons) == len(obj.data.polygons) for obj in lows}


def _from_issue(check, issue, base_ok=None):
    faces, message = tuple(issue.faces), issue.message
    if faces and base_ok is not None and not base_ok.get(issue.object_name, True):
        faces = ()
        message += ' (modifiers change the topology, so they are not selectable)'
    return Finding(check, issue.severity, message, issue.object_name, faces=faces)


def _issue_findings(check, issues, ts):
    findings = []
    base_ok = _base_topology(ts) if any(issue.faces for issue in issues) else None
    for issue in issues:
        key = check
        if check == 'uvs':
            text = issue.message.lower()
            if 'scale' in text:
                key = 'scale'
            elif 'cage' in text:
                key = 'cage'
            elif any(word in text for word in ('source', 'target', 'match', 'ambiguous')) and 'slot' not in text:
                key = 'naming'
        findings.append(_from_issue(key, issue, base_ok))
    return findings


def _sync_edit_meshes(ts):
    """Edit Mode keeps its geometry in the edit BMesh; the datablock the checks read lags behind.

    The same flush every bake stage applies to its originals (``render_geometry``), so the
    checks and the bakes they gate read one geometry.
    """
    from .render_geometry import flush_edit_meshes
    flush_edit_meshes(_lows(ts) + _highs(ts) + _cages(ts))


def _end_paint_session(context):
    """A live paint or correction session masks strokes to the faces it selected;
    a highlight would silently replace that mask, so the session ends first."""
    project = context.scene.open_painter
    if project.paint_image is None:
        return
    if project.paint_correction:
        from .corrections import end_brush
        end_brush(context)
    else:
        from ..painting import paint_session
        paint_session.finish(context)


# ---- checks ------------------------------------------------------------------

def check_uvs(ts, tier='PRE_BAKE'):
    """Assignment, UV map, degenerate/outside faces and exact overlaps (diagnostics.inspect).

    The exact overlap analysis is bounded but not free; before a bake it runs
    only for targets up to ``LARGE_MESH`` faces, the Full tier always.
    """
    findings = []
    evaluated = tier == 'FULL' or all(len(obj.data.polygons) <= LARGE_MESH for obj in _lows(ts))
    if not evaluated:
        findings.append(Finding('uvs', 'INFO', 'large targets: exact UV overlap is checked by Full Check'))
    findings.extend(_issue_findings('uvs', diagnostics.inspect(ts, evaluated=evaluated), ts))
    return findings


LARGE_MESH = 250_000   # faces; per-face checks on bigger meshes wait for the Full tier


def _face_normals(mesh):
    normals = np.empty((len(mesh.polygons), 3), np.float32)
    mesh.polygons.foreach_get('normal', normals.ravel())
    return normals


def check_normals(ts, tier='PRE_BAKE'):
    """Faces whose winding disagrees with a consistent recalculation, on targets and sources.

    Inside-out sources give dark AO and inverted normal detail; inconsistent
    target faces flip the tangent frame of the normal map. Meshes above
    ``LARGE_MESH`` faces are checked on the Full tier only, so a bake never
    waits on a multi-million-face source.
    """
    import bmesh
    findings = []
    roles = [(obj, 'target') for obj in _lows(ts)]
    if _projecting(ts):
        roles.extend((obj, 'source') for obj in _highs(ts))
    for obj, role in roles:
        mesh = obj.data
        count = len(mesh.polygons)
        if not count:
            continue
        if count > LARGE_MESH and tier != 'FULL':
            findings.append(Finding('normals', 'INFO', f'{count} faces: normals are checked by Full Check', obj.name))
            continue
        before = _face_normals(mesh)
        bm = _bmesh(mesh)
        temp = bpy.data.meshes.new('OP Normals Check')
        try:
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
            bm.to_mesh(temp)
            after = _face_normals(temp)
        finally:
            bm.free()
            bpy.data.meshes.remove(temp)
        flipped = np.flatnonzero(np.einsum('ij,ij->i', before, after) < -1e-6)
        if not len(flipped):
            continue
        faces = tuple(int(f) for f in flipped)
        if len(flipped) > count / 2:
            message = (f'{len(flipped)} of {count} faces point inward: the mesh normals are flipped'
                       + ('; the AO bake sees its back faces' if role == 'source' else ''))
        else:
            message = f'{len(flipped)} faces have inconsistent normals'
            if role == 'source':
                message += ' (dark AO and inverted detail where the bake hits them)'
        findings.append(Finding('normals', 'WARNING', message, obj.name, faces=faces))
    return findings


def check_scale(ts):
    """Unapplied or negative object scale on targets, sources and cages."""
    findings = []
    roles = [(obj, 'target') for obj in _lows(ts)]
    if _projecting(ts):
        roles.extend((obj, 'source') for obj in _highs(ts))
        roles.extend((obj, 'cage') for obj in _cages(ts))
    for obj, role in roles:
        scale = np.asarray(obj.scale, np.float64)
        determinant = float(np.linalg.det(np.asarray(obj.matrix_world, np.float64)[:3, :3]))
        if determinant < 0:
            findings.append(Finding('scale', 'WARNING',
                                    f'negative scale mirrors the {role}: its normals and tangents flip in the bake',
                                    obj.name, objects=(obj.name,)))
        elif not np.allclose(scale, 1.0, atol=1e-6):
            text = ' × '.join(f'{v:g}' for v in scale)
            message = f'{role} has unapplied scale ({text}); cage distances are in world units'
            findings.append(Finding('scale', 'WARNING', message, obj.name, objects=(obj.name,)))
    return findings


NEAR_SUFFIXES = {
    'high': ('_hi', '_hp', '_hipoly', '_high_poly', '_highpoly', '-high', ' high', '_HIGH', '_High', '_Hi'),
    'low': ('_lo', '_lp', '_lowpoly', '_low_poly', '-low', ' low', '_LOW', '_Low', '_Lo'),
}


def _near_stem(name, role):
    """The stem when ``name`` ends with a suffix that resembles ``_role`` but is not it."""
    import re
    for suffix in NEAR_SUFFIXES[role]:
        match = re.fullmatch(r'(.+)' + re.escape(suffix) + r'(?:\.\d+)?', name)
        if match and suffix != '_' + role:
            return match.group(1), suffix
    return None, None


def check_naming(ts):
    """Low/high suffix matching, ambiguous stems and near-miss suffixes."""
    from .pairing import stem, reference_target
    findings = []
    if not _projecting(ts):
        return findings
    settings = ts.bake
    lows = _lows(ts)
    highs = _highs(ts)
    scene = ts.id_data
    low_stems = {}
    for low in lows:
        key = stem(low.name, 'low')
        if key is None:
            near, suffix = _near_stem(low.name, 'low')
            if near:
                findings.append(Finding('naming', 'WARNING',
                                        f"suffix '{suffix}' is not '_low': name matching will not find its sources",
                                        low.name, objects=(low.name,)))
            elif settings.source_mode == 'MATCHED':
                findings.append(Finding('naming', 'INFO', "has no '_low' suffix; sources need explicit targets",
                                        low.name, objects=(low.name,)))
            continue
        if key in low_stems:
            findings.append(Finding('naming', 'WARNING', f"stem '{key}' is shared with {low_stems[key].name}: ambiguous",
                                    low.name, objects=(low.name, low_stems[key].name)))
        low_stems[key] = low
    for high in highs:
        key = stem(high.name, 'high')
        try:
            target = reference_target(next(r for r in settings.high_objects if r.object == high), lows)
        except ValueError as exc:
            findings.append(Finding('naming', 'ERROR', str(exc), high.name, objects=(high.name,)))
            continue
        if target is not None:
            continue
        near, suffix = _near_stem(high.name, 'high')
        if near and near in low_stems:
            findings.append(Finding('naming', 'WARNING',
                                    f"looks like a source for {low_stems[near].name} but the suffix is '{suffix}', not '_high'",
                                    high.name, objects=(high.name,)))
        elif key is not None and key not in low_stems:
            findings.append(Finding('naming', 'WARNING' if settings.source_mode == 'MATCHED' else 'INFO',
                                    f"'{key}_high' matches no assigned '_low' target", high.name, objects=(high.name,)))
        elif settings.source_mode == 'MATCHED':
            findings.append(Finding('naming', 'ERROR', 'no target: name it <stem>_high or choose its target',
                                    high.name, objects=(high.name,)))
    listed = set(highs)
    for key, low in low_stems.items():
        partners = [obj for obj in scene.objects if obj.type == 'MESH' and obj not in lows and stem(obj.name, 'high') == key]
        unlisted = [obj for obj in partners if obj not in listed]
        if partners and unlisted and settings.source_mode == 'MATCHED':
            findings.append(Finding('naming', 'INFO', f"{len(unlisted)} scene mesh(es) named '{key}_high' are not listed as sources",
                                    low.name, objects=tuple(obj.name for obj in unlisted)))
        if not partners and not any(stem(h.name, 'high') == key for h in highs):
            near = [obj for obj in scene.objects if obj.type == 'MESH' and obj not in lows
                    and _near_stem(obj.name, 'high')[0] == key]
            if near:
                findings.append(Finding('naming', 'WARNING',
                                        f"no '{key}_high' in the scene; {near[0].name} has a similar suffix",
                                        low.name, objects=tuple(obj.name for obj in near)))
            elif settings.source_mode == 'MATCHED' and not any(
                    getattr(r, 'target', None) == low for r in settings.high_objects):
                findings.append(Finding('naming', 'WARNING', f"no '{key}_high' source in the scene", low.name, objects=(low.name,)))
    return findings


def check_doubles(ts, tier='PRE_BAKE'):
    """Coincident vertices on the targets (split seams and broken smoothing in normal bakes)."""
    import bmesh
    findings = []
    for obj in _lows(ts):
        mesh = obj.data
        if len(mesh.vertices) < 2:
            continue
        if len(mesh.polygons) > LARGE_MESH and tier != 'FULL':
            findings.append(Finding('doubles', 'INFO', f'{len(mesh.polygons)} faces: duplicates are checked by Full Check', obj.name))
            continue
        bm = _bmesh(mesh)
        try:
            coords = np.empty((len(mesh.vertices), 3), np.float32)
            mesh.vertices.foreach_get('co', coords.ravel())
            extent = float(np.ptp(coords, axis=0).max() or 1.0)
            result = bmesh.ops.find_doubles(bm, verts=bm.verts, dist=max(1e-6, extent * 1e-6))
            doubles = sorted({v.index for v in result['targetmap']} | {v.index for v in result['targetmap'].values()})
        finally:
            bm.free()
        if doubles:
            findings.append(Finding('doubles', 'WARNING', f'{len(doubles)} duplicate vertices (coincident); merge by distance',
                                    obj.name, vertices=tuple(doubles)))
    return findings


def check_modifiers(ts):
    """Modifiers whose render state differs from what the viewport shows."""
    findings = []
    roles = [(obj, 'target') for obj in _lows(ts)]
    if _projecting(ts):
        roles.extend((obj, 'source') for obj in _highs(ts))
    for obj, role in roles:
        for modifier in obj.modifiers:
            if modifier.show_viewport and not modifier.show_render:
                findings.append(Finding('modifiers', 'WARNING',
                                        f"modifier '{modifier.name}' is viewport-only: the bake evaluates the {role} without it",
                                        obj.name, objects=(obj.name,)))
            elif modifier.show_render and not modifier.show_viewport:
                findings.append(Finding('modifiers', 'WARNING',
                                        f"modifier '{modifier.name}' is render-only: the bake evaluates the {role} with it",
                                        obj.name, objects=(obj.name,)))
            elif modifier.type in {'SUBSURF', 'MULTIRES'} and modifier.levels != modifier.render_levels:
                findings.append(Finding('modifiers', 'INFO',
                                        f"'{modifier.name}' bakes at render level {modifier.render_levels}, viewport shows {modifier.levels}",
                                        obj.name, objects=(obj.name,)))
    return findings


def _render_faces_estimate(obj):
    """Face count after render subdivision, from the base mesh; other modifiers are not guessed."""
    count = len(obj.data.polygons)
    for modifier in obj.modifiers:
        if modifier.show_render and modifier.type in {'SUBSURF', 'MULTIRES'}:
            count *= 4 ** modifier.render_levels
    return count


def check_cage(ts, tier='PRE_BAKE'):
    """Cage vertices whose offset crosses the low-poly surface (likely cage intersections).

    From an automatic cage the vertex travels along its normal by the
    extrusion; from a custom cage it sits where the artist put it. In either
    case the segment from the surface to the cage point is cast against the
    low-poly mesh itself, evaluated with render modifiers as the bake sees it:
    a hit means the cage pierces neighbouring geometry (ears, fingers, folds),
    which projects the wrong surface there. The cast is one Python ray per
    evaluated vertex, and render subdivision multiplies those, so targets
    above ``LARGE_MESH`` evaluated faces wait for the Full tier like the other
    per-face checks.
    """
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    from .pairing import groups
    from .render_geometry import scope
    findings = []
    if not _projecting(ts) or ts.bake.cage_mode == 'NONE':
        return findings
    settings = ts.bake
    try:
        parts = {part.low: part for part in groups(ts, validate_cages=False)}
    except ValueError:
        return findings
    if tier != 'FULL':
        # Subdivided targets are skipped before the render-level evaluation itself gets costly.
        for low in list(parts):
            if _render_faces_estimate(low) > LARGE_MESH:
                findings.append(Finding('cage', 'INFO', f'about {_render_faces_estimate(low)} faces at render level: '
                                        'cage intersections are checked by Full Check', low.name))
                del parts[low]
        if not parts:
            return findings
    extrusion = settings.cage_extrusion if settings.separate_cage_distance else settings.ray_distance
    scene = ts.id_data
    from ..geometry.snapshots import view_layer_for
    originals = [obj for part in parts.values() for obj in (part.low, part.cage) if obj is not None]
    lows = list(parts)
    # The bake's own layer choice (the artist's first), so the findings name the staged faces.
    with bpy.context.temp_override(scene=scene, view_layer=view_layer_for(scene, lows)), \
            scope(bpy.context, originals, required=lows) as frozen:
        for low, part in parts.items():
            copy = frozen[low]
            mesh = copy.data
            count = len(mesh.vertices)
            if not count or not len(mesh.polygons):
                continue
            if tier != 'FULL' and len(mesh.polygons) > LARGE_MESH:
                findings.append(Finding('cage', 'INFO', f'{len(mesh.polygons)} evaluated faces: '
                                        'cage intersections are checked by Full Check', low.name))
                continue
            local = np.empty((count, 3), np.float32)
            mesh.vertices.foreach_get('co', local.ravel())
            normals = np.empty((count, 3), np.float32)
            mesh.vertices.foreach_get('normal', normals.ravel())
            world = np.asarray(copy.matrix_world, np.float64)
            positions = local @ world[:3, :3].T + world[:3, 3]
            normal_matrix = np.asarray(copy.matrix_world.to_3x3().inverted_safe().transposed(), np.float64)
            directions = normals @ normal_matrix.T
            directions /= np.maximum(np.linalg.norm(directions, axis=1, keepdims=True), 1e-12)
            if settings.cage_mode == 'CUSTOM' and part.cage is not None:
                cage_mesh = frozen[part.cage].data
                if len(cage_mesh.vertices) != count:
                    findings.append(Finding('cage', 'ERROR', 'custom cage vertex count differs from the evaluated target',
                                            low.name, objects=(part.cage.name,)))
                    continue
                cage_local = np.empty((count, 3), np.float32)
                cage_mesh.vertices.foreach_get('co', cage_local.ravel())
                cage_world = np.asarray(frozen[part.cage].matrix_world, np.float64)
                targets_ = cage_local @ cage_world[:3, :3].T + cage_world[:3, 3]
                below = np.einsum('ij,ij->i', targets_ - positions, directions) < -1e-6
            else:
                targets_ = positions + directions * extrusion
                below = np.zeros(count, bool)
            segments = targets_ - positions
            lengths = np.linalg.norm(segments, axis=1)
            loop_vertex = np.empty(len(mesh.loops), np.int32)
            mesh.loops.foreach_get('vertex_index', loop_vertex)
            poly_total = np.empty(len(mesh.polygons), np.int32)
            mesh.polygons.foreach_get('loop_total', poly_total)
            loop_face = np.repeat(np.arange(len(mesh.polygons)), poly_total)
            mesh.calc_loop_triangles()
            triangles = np.empty(len(mesh.loop_triangles) * 3, np.int32)
            mesh.loop_triangles.foreach_get('vertices', triangles)
            tree = BVHTree.FromPolygons(positions.tolist(), triangles.reshape(-1, 3).tolist())
            pierced = np.zeros(count, bool)
            cast = np.flatnonzero(lengths > 1e-9)
            rays = segments[cast] / lengths[cast, None]
            # Start a hair above the surface so the vertex's own faces do not count.
            origins = positions[cast] + rays * np.maximum(lengths[cast] * 1e-3, 1e-6)[:, None]
            for index, origin, direction, length in zip(cast, origins.tolist(), rays.tolist(), lengths[cast].tolist()):
                if tree.ray_cast(Vector(origin), Vector(direction), length)[0] is not None:
                    pierced[index] = True
            base_ok = len(mesh.polygons) == len(low.data.polygons) and count == len(low.data.vertices)
            for flags, message in ((pierced, 'cage vertices pierce the low-poly surface: likely cage intersections'),
                                   (below, 'custom cage vertices sit below the surface: rays start inside the mesh')):
                hits = np.flatnonzero(flags)
                if not len(hits):
                    continue
                faces = np.unique(loop_face[np.isin(loop_vertex, hits)])
                text = f'{len(hits)} {message}'
                if not base_ok:
                    text += ' (render modifiers change the topology, so they are not selectable)'
                findings.append(Finding('cage', 'WARNING', text, low.name,
                                        faces=tuple(int(f) for f in faces) if base_ok else (),
                                        vertices=tuple(int(v) for v in hits) if base_ok else ()))
    return findings


def check_seams(ts):
    """UV island borders that carry no seam flag (reimported meshes lose their seams)."""
    findings = []
    for obj in _lows(ts):
        if obj.mode == 'EDIT':
            # The datablock's UV layers are empty while the edit BMesh owns them.
            import bmesh
            mesh = bpy.data.meshes.new('OP Seams Check')
            try:
                bmesh.from_edit_mesh(obj.data).to_mesh(mesh)
                findings.extend(_seam_findings(obj, mesh, obj.data))
            finally:
                bpy.data.meshes.remove(mesh)
        else:
            findings.extend(_seam_findings(obj, obj.data, obj.data))
    return findings


def _seam_findings(obj, mesh, source):
    """``mesh`` carries the geometry; the render UV map is named by ``source``."""
    named = next((layer for layer in source.uv_layers if layer.active_render), source.uv_layers.active)
    uv = mesh.uv_layers.get(named.name) if named is not None else None
    if uv is None or not len(mesh.polygons) or not len(mesh.edges):
        return []
    loops_total = len(mesh.loops)
    coords = np.empty((loops_total, 2), np.float32)
    uv.data.foreach_get('uv', coords.ravel())
    loop_vertex = np.empty(loops_total, np.int32)
    mesh.loops.foreach_get('vertex_index', loop_vertex)
    loop_edge = np.empty(loops_total, np.int32)
    mesh.loops.foreach_get('edge_index', loop_edge)
    poly_start = np.empty(len(mesh.polygons), np.int32)
    poly_total = np.empty(len(mesh.polygons), np.int32)
    mesh.polygons.foreach_get('loop_start', poly_start)
    mesh.polygons.foreach_get('loop_total', poly_total)
    # The loop after each loop within its polygon (edge end).
    next_loop = np.arange(loops_total) + 1
    ends = poly_start + poly_total - 1
    next_loop[ends] = poly_start
    edge_vertices = np.empty((len(mesh.edges), 2), np.int32)
    mesh.edges.foreach_get('vertices', edge_vertices.ravel())
    # UV of the edge oriented from the lower vertex index: comparable across faces.
    v0, v1 = loop_vertex, loop_vertex[next_loop]
    lo_first = v0 <= v1
    a = np.where(lo_first[:, None], coords, coords[next_loop])
    b = np.where(lo_first[:, None], coords[next_loop], coords)
    order = np.argsort(loop_edge, kind='stable')
    sorted_edges = loop_edge[order]
    border = np.zeros(len(mesh.edges), bool)
    starts = np.flatnonzero(np.r_[True, sorted_edges[1:] != sorted_edges[:-1]])
    counts = np.diff(np.r_[starts, len(sorted_edges)])
    # Every loop compared with the first loop of its edge group, vectorized.
    first = np.repeat(order[starts], counts)
    differs = (np.abs(a[order] - a[first]).max(axis=1) > 1e-5) | (np.abs(b[order] - b[first]).max(axis=1) > 1e-5)
    border[sorted_edges[differs]] = True
    seams = np.empty(len(mesh.edges), bool)
    mesh.edges.foreach_get('use_seam', seams)
    missing = np.flatnonzero(border & ~seams)
    if not len(missing):
        return []
    return [Finding('seams', 'INFO', f'{len(missing)} UV island borders are not marked as seams (bakes are unaffected; '
                    're-unwrapping would lose the islands)', obj.name, edges=tuple(int(e) for e in missing))]


def check_coverage(ts):
    return _issue_findings('coverage', diagnostics.ray_coverage(ts), ts)


PRE_BAKE = (check_uvs, check_normals, check_scale, check_naming, check_doubles, check_modifiers, check_cage, check_seams)
KEYS = {check_uvs: 'uvs', check_normals: 'normals', check_scale: 'scale', check_naming: 'naming',
        check_doubles: 'doubles', check_modifiers: 'modifiers', check_cage: 'cage', check_seams: 'seams',
        check_coverage: 'coverage'}


def examine(ts, tier='PRE_BAKE'):
    """Run the checks of a tier and retain the report for the panel.

    PRE_BAKE: every exact check (bounded; runs before each bake).
    FULL: PRE_BAKE plus the sampled cage-ray coverage.
    """
    from ..evaluation.resource_keys import key_for
    started = perf_counter()
    findings, ran = [], []
    _sync_edit_meshes(ts)
    checks = list(PRE_BAKE) + ([check_coverage] if tier == 'FULL' else [])
    for check in checks:
        key = KEYS[check]
        if check is check_coverage and any(f.severity == 'ERROR' for f in findings):
            continue
        try:
            findings.extend(check(ts, tier) if check in {check_uvs, check_normals, check_doubles, check_cage} else check(ts))
        except Exception as exc:   # one broken check must not hide the others
            findings.append(Finding(key, 'INFO', f'check skipped: {exc}'))
        ran.append(key)
        if key == 'uvs' and any(f.severity == 'ERROR' and f.check == 'uvs' for f in findings):
            # Missing meshes or UV maps make the geometry checks meaningless.
            break
    # The UV check also files scale/naming/cage findings under their own keys.
    for key in ('scale', 'naming', 'cage'):
        if key not in ran and any(f.check == key for f in findings):
            ran.append(key)
    report = Report(tier, tuple(findings), tuple(ran), (perf_counter() - started) * 1000)
    _last[key_for(ts.id_data, ts)] = report
    return report


def last(ts):
    from ..evaluation.resource_keys import key_for
    return _last.get(key_for(ts.id_data, ts))


def forget(ts=None):
    if ts is None:
        _last.clear()
    else:
        from ..evaluation.resource_keys import key_for
        _last.pop(key_for(ts.id_data, ts), None)


# ---- selection ---------------------------------------------------------------

def _resolve(ts, name):
    scene = ts.id_data
    return scene.objects.get(name) or bpy.data.objects.get(name)


def select(ts, finding, context=None):
    """Select what a finding points at; selection (and the active object) is the only change.

    Returns a short description of what was selected.
    """
    context = context or bpy.context
    scene = ts.id_data
    view_layer = context.view_layer if context.view_layer and context.view_layer in scene.view_layers[:] else scene.view_layers[0]
    _end_paint_session(context)
    if finding.objects:
        targets = [obj for obj in (_resolve(ts, name) for name in finding.objects) if obj is not None and obj.name in view_layer.objects]
        if not targets:
            raise ValueError('The reported objects are not in the active view layer')
        if context.object and context.object.mode != 'OBJECT':
            with context.temp_override(scene=scene, view_layer=view_layer):
                bpy.ops.object.mode_set(mode='OBJECT')
        for obj in view_layer.objects:
            obj.select_set(False, view_layer=view_layer)
        for obj in targets:
            obj.select_set(True, view_layer=view_layer)
        view_layer.objects.active = targets[0]
        return f'selected {", ".join(obj.name for obj in targets)}'
    obj = _resolve(ts, finding.object_name)
    if obj is None or obj.type != 'MESH':
        raise ValueError(f'{finding.object_name} is not a mesh in this scene')
    mesh = obj.data
    faces, vertices, edges = set(finding.faces), set(finding.vertices), set(finding.edges)
    if obj.mode == 'EDIT':
        # The edit BMesh is the geometry the highlight lands on; the datablock may lag behind it.
        import bmesh
        bm = bmesh.from_edit_mesh(mesh)
        counts = len(bm.faces), len(bm.verts), len(bm.edges)
    else:
        counts = len(mesh.polygons), len(mesh.vertices), len(mesh.edges)
    if any(wanted and max(wanted) >= count for wanted, count in zip((faces, vertices, edges), counts)):
        raise ValueError(f'{obj.name} changed since the check ran; run Bake Check again')
    if obj.name in view_layer.objects:
        for other in view_layer.objects:
            other.select_set(other == obj, view_layer=view_layer)
        view_layer.objects.active = obj
    mode = 'FACE' if faces else 'VERT' if vertices else 'EDGE'
    scene.tool_settings.mesh_select_mode = (mode == 'VERT', mode == 'EDGE', mode == 'FACE')
    if obj.mode == 'EDIT':
        bm.verts.ensure_lookup_table(); bm.edges.ensure_lookup_table(); bm.faces.ensure_lookup_table()
        for element in bm.verts:
            element.select_set(False)
        for element in bm.edges:
            element.select_set(False)
        for element in bm.faces:
            element.select_set(False)
        for index in faces:
            bm.faces[index].select_set(True)
        for index in vertices:
            bm.verts[index].select_set(True)
        for index in edges:
            bm.edges[index].select_set(True)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    else:
        mesh.polygons.foreach_set('select', np.isin(np.arange(len(mesh.polygons)), list(faces)))
        mesh.edges.foreach_set('select', np.isin(np.arange(len(mesh.edges)), list(edges)))
        vertex_flags = np.isin(np.arange(len(mesh.vertices)), list(vertices))
        if faces:
            for index in faces:
                vertex_flags[list(mesh.polygons[index].vertices)] = True
        if edges:
            edge_vertices = np.empty((len(mesh.edges), 2), np.int32)
            mesh.edges.foreach_get('vertices', edge_vertices.ravel())
            vertex_flags[edge_vertices[list(edges)].ravel()] = True
        mesh.vertices.foreach_set('select', vertex_flags)
        mesh.update()
    what = f'{len(faces)} faces' if faces else f'{len(vertices)} vertices' if vertices else f'{len(edges)} edges'
    return f'selected {what} on {obj.name}'


def select_all(ts, report, context=None):
    """Select every selectable element of the report, grouped per object."""
    merged = {}
    objects = []
    for finding in report.findings:
        if finding.objects:
            objects.extend(finding.objects)
        elif finding.selectable:
            entry = merged.setdefault(finding.object_name, [set(), set(), set()])
            entry[0].update(finding.faces); entry[1].update(finding.vertices); entry[2].update(finding.edges)
    messages = []
    for name, (faces, vertices, edges) in merged.items():
        try:
            messages.append(select(ts, Finding('all', 'INFO', '', name, faces=tuple(faces), vertices=tuple(vertices),
                                               edges=tuple(edges)), context))
        except ValueError as exc:
            messages.append(str(exc))
    if objects and not merged:
        messages.append(select(ts, Finding('all', 'INFO', '', objects=tuple(dict.fromkeys(objects))), context))
    return '; '.join(messages) or 'nothing to select'
