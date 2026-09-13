"""Non-destructive bake diagnostics with explicit cost and severity."""
from dataclasses import dataclass
import numpy as np


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
        if ref.slot_index >= len(obj.material_slots):
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
    if (settings.normal or settings.ao) and settings.cage_mode == 'CUSTOM':
        low = {r.object for r in ts.assignments if r.object}
        cage = settings.cage_object
        if len(low) != 1 or not cage or cage.type != 'MESH':
            issues.append(Issue('ERROR', 'Custom cage requires one target and a mesh cage'))
        else:
            mesh = next(iter(low)).data
            topology = lambda m: (len(m.vertices), tuple(tuple(p.vertices) for p in m.polygons))
            if topology(mesh) != topology(cage.data):
                issues.append(Issue('ERROR', 'Custom cage polygon topology differs from target', cage.name))
    if not evaluated or any(i.severity == 'ERROR' for i in issues):
        return issues
    from ..geometry.snapshots import targets
    with targets(ts) as meshes:
        for target in meshes:
            flat, outside = set(), set()
            for tri in target['triangles']:
                uv = target['uv'][list(tri.loops)]
                a, b = uv[1]-uv[0], uv[2]-uv[0]
                if abs(a[0]*b[1]-a[1]*b[0]) < 1e-12 or tri.area < 1e-12:
                    flat.add(tri.polygon_index)
                if np.any(uv < -1e-6) or np.any(uv > 1+1e-6):
                    outside.add(tri.polygon_index)
            if flat:
                issues.append(Issue('WARNING', 'Degenerate geometry or UV triangles', target['object'].name, tuple(sorted(flat))))
            if outside:
                issues.append(Issue('WARNING', 'UVs outside the single supported tile; wrapping may be intentional', target['object'].name, tuple(sorted(outside))))
    return issues
